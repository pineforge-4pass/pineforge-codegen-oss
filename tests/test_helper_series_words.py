"""Requested helper bindings cannot rewrite the members of their storage."""

import re
from types import SimpleNamespace

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.codegen.helpers import NamingHelper
from pineforge_codegen.codegen.security import SecurityEmitter
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser


_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_REFERENCE = re.compile(
    r'\(_security_helper_series_(?:str_)?\.[A-Za-z_][A-Za-z_0-9]*\('
    r'"(?:\\.|[^"\\])*", [0-9]+\)[^()\n;]*\)'
)
_STORE_CALL = re.compile(r"_security_helper_series_(?:str_)?\.try_emplace\(")


def helper_source(first="aa", second="bb", *, string=False, timeframe="60"):
    value = "str.tostring(close)" if string else "close"
    previous = f"{first}[1]" if string else f"nz({first}[1])"
    return (
        '//@version=6\nstrategy("helper words")\n'
        "n = input.int(3)\nz = close[n]\n"
        f"h() =>\n    {first} = {value}\n    {second} = {previous}\n"
        f"    {first} + {second}\n"
        f'v = request.security(syminfo.tickerid, "{timeframe}", h())\n'
        + ("plot(str.length(v))\n" if string else "plot(v)\n")
    )


def _references(cpp):
    references = _REFERENCE.findall(cpp)
    assert references, cpp
    # Every call on a store is one complete reference with an intact tail. Other
    # text may hold the word `try_emplace` before a bracket, for example the
    # constructor initializer of a script variable of that name, so only calls
    # on a store are counted.
    assert len(_STORE_CALL.findall(cpp)) == len(references)
    assert cpp.count(").first->second)") == len(references)
    assert ".(" not in cpp
    assert "->(" not in cpp
    # Local names affect key values, but neither store selection nor capacity.
    return sorted(_STRING.sub('""', reference) for reference in references)


@pytest.mark.parametrize("names", [
    ("first", "second"), ("try_emplace", "aa"), ("aa", "bb"),
    ("_security_helper_series_", "bb"), ("_security_helper_series_str_", "bb"),
])
@pytest.mark.parametrize("capacity", [None, 100])
@pytest.mark.parametrize("string", [False, True], ids=["numeric", "string"])
def test_requested_helper_local_words_preserve_reference_text(names, capacity, string):
    control = transpile(helper_source(string=string), history_capacity=capacity)
    cpp = transpile(helper_source(*names, string=string), history_capacity=capacity)
    assert _references(cpp) == _references(control)


def _control_tokens():
    references = []
    old_references = []
    for string in (False, True):
        source = helper_source(string=string)
        references.extend(_REFERENCE.findall(transpile(source)))
        # This source has only literal history after removing the chart read.
        old_cpp = transpile(source.replace("n = input.int(3)\nz = close[n]\n", ""))
        old_references.extend(re.findall(
            r'_security_helper_series_(?:str_)?\["(?:\\.|[^"\\])*"\]',
            old_cpp,
        ))
    assert references and old_references
    tokens = set(_IDENTIFIER.findall(_STRING.sub("", " ".join(references))))
    old_tokens = set(_IDENTIFIER.findall(_STRING.sub("", " ".join(old_references))))
    return tokens, old_tokens


_REFERENCE_TOKENS, _OLD_REFERENCE_TOKENS = _control_tokens()


def test_reference_identifier_inventory_covers_both_scalar_stores():
    assert _REFERENCE_TOKENS - _OLD_REFERENCE_TOKENS == {"try_emplace", "first", "second"}
    assert _OLD_REFERENCE_TOKENS == {
        "_security_helper_series_", "_security_helper_series_str_",
    }


# Every reference word, including the two store names, is a valid Pine local:
# the allocator escapes authored store spellings and preserves the stores.
# Every word can name either a string or a numeric helper local; declaration
# bindings determine the history store even for builtin spellings.
_REFERENCE_WORDS = sorted(_REFERENCE_TOKENS)
_WORD_ROWS = [
    (name, string)
    for name in _REFERENCE_WORDS
    for string in (False, True)
]


@pytest.mark.parametrize("name, string", _WORD_ROWS)
@pytest.mark.parametrize("capacity", [None, 100])
def test_every_reference_word_can_be_a_requested_helper_local(name, string, capacity):
    source = helper_source(name, "bb", string=string)
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    generator = CodeGen(Analyzer(program).analyze())
    spelling = generator._safe_name(name)
    cpp = transpile(source, history_capacity=capacity)
    control = transpile(helper_source(string=string), history_capacity=capacity)
    if spelling != name:
        # Escaped Pine names retain their authored keys and use a distinct C++ ID.
        assert re.search(rf"\b{re.escape(spelling)}\b", cpp), spelling
        pattern = rf"\b{re.escape(name)}\s*="
        assert len(re.findall(pattern, cpp)) == len(re.findall(pattern, control))
    assert _references(cpp) == _references(control)


def mutable_global_source(name, *, var=True):
    """A global that a requested helper reads next to a local with history."""
    declaration = f"var float {name} = 0.0\n" if var else f"float {name} = 0.0\n"
    return (
        '//@version=6\nstrategy("mutable global words")\n'
        "n = input.int(3)\nz = close[n]\n"
        f"{declaration}{name} := {name} + 1.0\n"
        "h() =>\n    a = close\n    b = nz(a[1])\n"
        f"    a + b + {name}\n"
        'v = request.security(syminfo.tickerid, "60", h())\nplot(v)\n'
    )


@pytest.mark.parametrize("name", _REFERENCE_WORDS)
@pytest.mark.parametrize("var", [True, False], ids=["var", "reassigned"])
@pytest.mark.parametrize("capacity", [None, 100])
def test_every_introduced_word_can_be_a_security_payload_mutable_global(name, var, capacity):
    # The payload's mutable globals are rebound to script-state members by the
    # same pass; the reference's own words must stay out of that rebinding.
    cpp = transpile(mutable_global_source(name, var=var), history_capacity=capacity)
    control = transpile(mutable_global_source("aa", var=var), history_capacity=capacity)
    assert _references(cpp) == _references(control)


class _RewriteHost(SecurityEmitter, NamingHelper):
    def __init__(self, names=(), *, series=False):
        self._global_mutable_infos = {
            name: SimpleNamespace(is_series=series) for name in names
        }
        self._security_string_series = set()
        self._security_color_series = set()
        self._history_capacity = 128

    def _resolved_history_capacity(self):
        return 128


@pytest.mark.parametrize("binding", ["mutable-scalar", "mutable-series", "helper"])
@pytest.mark.parametrize("store", ["_security_helper_series_", "_security_helper_series_str_"])
def test_rendered_reference_is_opaque_but_its_index_is_rebound(binding, store):
    names = ("first", "second", "try_emplace")
    mutable = set(names) if binding.startswith("mutable") else set()
    host = _RewriteHost(mutable, series=binding == "mutable-series")
    stack = ({name: f"_bound_{name}" for name in names},) if binding == "helper" else None
    # Escaped quote and backslash are part of one C++ key literal.
    reference = f'({store}.try_emplace("key\\\"first\\\\second", 128).first->second)'
    text = reference + "[first + second + try_emplace]"
    prefix = "_bound_" if binding == "helper" else "_sec0_"
    suffix = "[0]" if binding == "mutable-series" else ""
    index = " + ".join(f"{prefix}{name}{suffix}" for name in names)
    expected = reference + f"[{index}]"
    assert host._rewrite_security_cpp(text, 0, mutable, stack) == expected


def test_inserted_references_are_opaque_to_later_bindings():
    host = _RewriteHost()
    stack = ({
        "first": "@series:one",
        "second": "@series:two",
        "try_emplace": "_bound_call",
    },)
    one = '(_security_helper_series_.try_emplace("one", 128).first->second)'
    two = '(_security_helper_series_.try_emplace("two", 128).first->second)'
    expected = f"{one}[{two}[0]] + {two}[0] + _bound_call"
    assert host._rewrite_security_cpp(
        "first[second] + second + try_emplace", 0, set(), stack,
    ) == expected


def test_reference_like_literal_text_cannot_hide_following_value_reads():
    host = _RewriteHost()
    reference = '(_security_helper_series_.try_emplace("key", 128).first->second)'
    literal = '"(_security_helper_series_.try_emplace(\\"first\\", 128).first->second)"'
    comment = "/* " + reference + " first */"
    text = literal + " + " + comment + " + " + reference + "[first] + 'f' + first"
    expected = literal + " + " + comment + " + " + reference + "[_bound] + 'f' + _bound"
    assert host._rewrite_security_cpp(text, 0, set(), ({"first": "_bound"},)) == expected
