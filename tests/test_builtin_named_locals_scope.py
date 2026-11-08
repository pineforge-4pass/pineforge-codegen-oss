"""Compare scoped builtin-named locals with reference or renamed-control C++.

Preservation rows compare complete C++; sibling readers also retain their
reference definitions. Local variants compare complete C++ after renaming,
or compare tuple-history refusal text with the control. These checks do not
compile or run the emitted code and supply no hosted-engine evidence.
Control comparisons ignore notes in the embedded manifest because they are
a separate known difference.
"""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer.tables import BUILTIN_VARS
from pineforge_codegen.codegen.tables import BAR_BUILTINS, BAR_FIELDS, TIME_FIELD_EXPRS
from pineforge_codegen.codegen.visit_expr import _REALTIME_ONLY_VARS
from pineforge_codegen.errors import CompileError
from pineforge_codegen.lexer import KEYWORDS
from pineforge_codegen.signatures import BUILTIN_VARIABLES
from tests._e2e import reference_codegen, transpile_json
from tests.test_helper_builtin_named_locals import CPP_REFERENCE
from tests.test_helper_series_words import _REFERENCE


_BARE_BUILTINS = (
    set(BUILTIN_VARS) | set(BAR_BUILTINS) | set(BAR_FIELDS)
    | set(TIME_FIELD_EXPRS) | set(_REALTIME_ONLY_VARS)
    | {name for name in BUILTIN_VARIABLES if "." not in name}
)
# Literal tokens cannot declare identifiers; dotted members are not bare names.
_LOCAL_NAMES = sorted(_BARE_BUILTINS - KEYWORDS.keys())
_HISTORY_READ = re.compile(
    rf'(?:{_REFERENCE.pattern}'
    r'|_security_helper_series_(?:str_)?\["(?:\\.|[^"\\])*"\]'
    r'|\b[A-Za-z_][A-Za-z_0-9]*)\[[1-9][0-9]*\]'
)
_BUILTIN_HISTORY = "close[1] + high[2] + open[1] + bar_index[1] + time[1]"


def _program(functions, tail, *, prelude=""):
    return '//@version=6\nstrategy("p")\n' + prelude + functions + tail


def _outer_binding_source(*, parameter):
    return _program(
        ("h(second) =>\n" if parameter else "h() =>\n")
        + "    x = close\n    y = x[1]\n    x + y + second\n",
        'v = request.security(syminfo.tickerid, "60", '
        + ("h(3))\n" if parameter else "h())\n")
        + "plot(v)\n",
        prelude="" if parameter else "second = 5\n",
    )


def _builtin_history_source(*, requested=False, global_high=False):
    return _program(
        ("h() =>\n" if requested else "f() =>\n")
        + f"    {_BUILTIN_HISTORY}\n",
        'v = request.security(syminfo.tickerid, "60", h())\nplot(v)\n'
        if requested else "plot(f())\n",
        prelude="high = 3.0\n" if global_high else "",
    )


def _chart_local_source(name, *, string):
    body = (
        f"    {name} = str.tostring(close)\n"
        f"    bb = {name}[1]\n    {name} + bb\n"
        if string else
        f"    {name} = close * 2\n"
        f"    p = {name}[1]\n    {name} + p\n"
    )
    return _program(
        "f() =>\n" + body,
        "v = f()\nplot(str.length(v))\n" if string else "plot(f())\n",
    )


def _sibling_source(name):
    return _program(
        f"f() =>\n    {name} = str.tostring(close)\n"
        f"    bb = {name}[1]\n    {name} + bb\n"
        "g() =>\n    x = close\n    y = x[1]\n    x + y + second\n",
        'v = request.security(syminfo.tickerid, "60", f())\n'
        'w = request.security(syminfo.tickerid, "60", g())\n'
        "plot(str.length(v) + w)\n",
    )


def _requested_source(body, *, string, two_sites=False, prelude=""):
    tail = 'v = request.security(syminfo.tickerid, "60", h())\n'
    if two_sites:
        tail += 'w = request.security(syminfo.tickerid, "240", h())\n'
    value = "str.length(v)" if string else "v"
    if two_sites:
        value += " + str.length(w)" if string else " + w"
    return _program(
        "h() =>\n" + body, tail + f"plot({value})\n",
        prelude="n = input.int(3)\nz = close[n]\n" + prelude,
    )


def _variant_source(name, variant, *, string):
    value = "str.tostring(close)" if string else "close"
    previous = f"{name}[1]" if string else f"nz({name}[1])"
    type_name = "string" if string else "float"
    seed = '"a"' if string else "0.0"
    empty = '""' if string else "0.0"
    constant = '"x"' if string else "1.0"
    reassigned = '"b"' if string else "1.5"
    history_body = (
        f"    {name} = {value}\n    bb = {previous}\n    {name} + bb\n"
    )
    bodies = {
        "history-free": (
            f"    {name} = {value}\n    bb = {constant}\n    {name} + bb\n"
        ),
        "typed": (
            f"    {type_name} {name} = {value}\n"
            f"    bb = {previous}\n    {name} + bb\n"
        ),
        "reassigned": (
            f"    var {type_name} {name} = {seed}\n    {name} := {reassigned}\n"
            f"    bb = {previous}\n    {name} + bb\n"
        ),
        "nested": (
            f"    r = {empty}\n    if close > open\n"
            f"        {name} = {value}\n        bb = {previous}\n"
            f"        r := {name} + bb\n    r\n"
        ),
        "tuple": (
            f"    [{name}, bb] = pair()\n    cc = {name}[1]\n"
            f"    {name} + bb + cc\n"
        ),
        "loop": f"    r = 0.0\n    for {name} = 0 to 2\n        r += {name}\n    r\n",
        "two-sites": history_body,
    }
    prelude = ""
    if variant == "tuple":
        pair = (
            "[str.tostring(close), str.tostring(open)]"
            if string else "[close, open]"
        )
        prelude = f"pair() =>\n    {pair}\n"
    return _requested_source(
        bodies[variant], string=string, two_sites=variant == "two-sites",
        prelude=prelude,
    )


def _mask_manifest_notes(cpp):
    string = r'\\"(?:\\\\(?:\\.|[^\\])|[^"\\])*\\"'
    return re.sub(
        rf'(\\"unresolved\\":\[)(?:\s*{string}(?:\s*,\s*{string})*)?\s*(\])',
        r"\1\2",
        cpp,
    )


def _normalize_local(cpp, name, control):
    spelling = re.escape(name)
    local = re.compile(
        rf"(?P<prefix>(?:_sec[0-9]+_[A-Za-z_][A-Za-z_0-9]*?_[0-9]+_"
        rf"|_pf_var_init_)?)"
        rf"(?:pf_safe_{spelling}|_{spelling}_|{spelling})"
        rf"(?:__pfhist[0-9]+)?(?P<suffix>(?:_cs[0-9]+)?)"
    )
    identifiers = re.compile(
        r"(?P<qualified>(?:->|\.|::)\s*(?P<member>[A-Za-z_][A-Za-z_0-9]*))"
        r"|[A-Za-z_][A-Za-z_0-9]*"
    )
    storage_owner = re.compile(r"\b(?:this|GeneratedStrategy)\s*$")

    def rename(match):
        member = match["member"]
        if member is not None and not storage_owner.search(
            match.string, 0, match.start(),
        ):
            return match[0]
        binding = local.fullmatch(member if member is not None else match[0])
        if binding is None:
            return match[0]
        neutral = f"{binding['prefix']}aa{binding['suffix']}"
        return match[0][:-len(member)] + neutral if member is not None else neutral

    # Separate code from comments and literals before replacing identifiers.
    text = re.compile(
        r"""("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|//[^\n]*|/\*.*?\*/)""",
        re.DOTALL,
    )
    parts = text.split(cpp)
    neutral = text.split(control)
    for index in range(0, len(parts), 2):
        parts[index] = identifiers.sub(rename, parts[index])

    # Corresponding text slots require identical surrounding code. A text
    # replacement must equal that slot's control; shared literals stay intact.
    if parts[::2] == neutral[::2]:
        for index in range(1, len(parts), 2):
            candidate = identifiers.sub(rename, parts[index])
            if candidate == neutral[index]:
                parts[index] = candidate
    return "".join(parts)


@pytest.mark.parametrize("string", [False, True], ids=["numeric", "string"])
@pytest.mark.parametrize("requested", [False, True], ids=["chart", "requested"])
def test_normalizer_is_identity_for_neutral_control(string, requested):
    source = (
        _variant_source("aa", "typed", string=string)
        if requested else _chart_local_source("aa", string=string)
    )
    control = _mask_manifest_notes(transpile(source))
    assert _normalize_local(control, "aa", control) == control


@pytest.mark.parametrize("name", ["second", "time_close"])
@pytest.mark.parametrize("form", ["plain", "prefixed", "wrapped", "history"])
def test_normalizer_renames_only_qualified_local_storage(name, form):
    spelling = {
        "plain": name,
        "prefixed": f"pf_safe_{name}",
        "wrapped": f"_{name}_",
        "history": f"{name}__pfhist1",
    }[form]
    shared = (
        "auto iterator = this->it->second;\n"
        f"auto other = object.{name} + Scope::{name} + current_bar_.{name};\n"
        f"auto unrelated = not_this->{spelling} + OtherGeneratedStrategy::{spelling};\n"
    )
    cpp = (
        f"auto &value = this->{spelling};\n"
        f"using Local = decltype(this -> {spelling});\n"
        f"using Storage = decltype(GeneratedStrategy :: {spelling});\n"
        f"auto &member = GeneratedStrategy::{spelling};\n"
        + shared
    )
    control = (
        "auto &value = this->aa;\n"
        "using Local = decltype(this -> aa);\n"
        "using Storage = decltype(GeneratedStrategy :: aa);\n"
        "auto &member = GeneratedStrategy::aa;\n"
        + shared
    )
    assert _normalize_local(cpp, name, control) == control


@pytest.mark.parametrize("name", ["second", "volume"])
def test_normalizer_preserves_qualified_members_and_shared_text(name):
    shared = (
        "auto item = it->second + result.first->second;\n"
        f"auto member = object.{name} + Scope::{name};\n"
        f"auto spaced = it -> {name} + object . {name} + Scope :: {name};\n"
        'if (_mode == "volume") use("second");\n'
    )
    cpp = (
        f"double {name} = 1.5;\n" + shared
        + f'auto &cell = store.try_emplace("{name}", 128).first->second;\n'
    )
    control = (
        "double aa = 1.5;\n" + shared
        + 'auto &cell = store.try_emplace("aa", 128).first->second;\n'
    )
    assert _normalize_local(cpp, name, control) == control


@pytest.mark.parametrize(
    "spelling", ["pf_safe_time_close", "_time_close_"],
    ids=["prefixed", "wrapped"],
)
def test_normalizer_accepts_escaped_local_spellings(spelling):
    cpp = f"double {spelling};\nauto value = _sec0_h_1_{spelling}_cs1;\n"
    control = "double aa;\nauto value = _sec0_h_1_aa_cs1;\n"
    assert _normalize_local(cpp, "time_close", control) == control


@pytest.mark.parametrize("cpp,control", [
    pytest.param(
        "Series<int> second__pfhist1;",
        "Series<std::string> aa;",
        id="series-type",
    ),
    pytest.param(
        "return second;",
        "return std::to_string(aa);",
        id="missing-call",
    ),
    pytest.param(
        "return pine_second(current_bar_.timestamp, syminfo_.timezone);",
        "return aa;",
        id="builtin-read",
    ),
    pytest.param(
        'note("built-in second");',
        'note("");',
        id="builtin-note",
    ),
])
def test_normalizer_retains_differences_beyond_local_spelling(cpp, control):
    assert _normalize_local(cpp, "second", control) != control


@pytest.mark.parametrize("payload", [
    pytest.param(r'\"note\"', id="single-note"),
    pytest.param(r'\"one\",\"two\"', id="multiple-notes"),
    pytest.param(r'\"note ] with \\\"quotes\\\" and \\\\ path\"', id="escaped-note"),
])
def test_manifest_note_mask_changes_only_unresolved_payload(payload):
    prefix = r'const char* manifest = "{\"before\":\"keep\",\"unresolved\":['
    suffix = r'],\"after\":[\"keep\"]}";'
    cpp = prefix + payload + suffix
    assert _mask_manifest_notes(cpp) == prefix + suffix


def test_manifest_note_mask_preserves_text_without_unresolved_list():
    cpp = 'Series<double> aa;\n' + r'const char* manifest = "{\"other\":[\"keep\"]}";'
    assert _mask_manifest_notes(cpp) == cpp


def test_manifest_note_mask_is_idempotent():
    cpp = r'const char* manifest = "{\"unresolved\":[\"note\"],\"other\":\"keep\"}";'
    once = _mask_manifest_notes(cpp)
    assert _mask_manifest_notes(once) == once


def test_manifest_note_mask_retains_series_type_difference():
    subject = 'Series<int> aa;\n' + r'const char* manifest = "{\"unresolved\":[\"note\"]}";'
    control = 'Series<std::string> aa;\n' + r'const char* manifest = "{\"unresolved\":[]}";'
    empty = r'const char* manifest = "{\"unresolved\":[]}";'
    subject = _mask_manifest_notes(subject)
    control = _mask_manifest_notes(control)
    assert subject == 'Series<int> aa;\n' + empty
    assert control == 'Series<std::string> aa;\n' + empty
    assert subject != control


@pytest.fixture(scope="module")
def reference():
    tree = reference_codegen(CPP_REFERENCE)
    if tree is None:
        pytest.skip("The C++ reference commit is unavailable")
    return tree


def _reference_cpp(source, reference, tmp_path):
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before
    return before["cpp"]


@pytest.mark.parametrize("source", [
    pytest.param(_outer_binding_source(parameter=False), id="global-second-requested"),
    pytest.param(_outer_binding_source(parameter=True), id="parameter-second-requested"),
    pytest.param(_builtin_history_source(requested=True), id="requested-builtin-history"),
    pytest.param(_builtin_history_source(), id="chart-builtin-history"),
    pytest.param(
        _builtin_history_source(global_high=True),
        id="chart-global-high-builtin-history",
    ),
    pytest.param(
        _program("f() =>\n    close[1] + high[2] + bar_index[1]\n", "plot(f())\n"),
        id="chart-builtin-reader",
    ),
    # A numeric tuple element spelled like a built-in is not a preservation row:
    # on main its history is stored in the built-in's series type. It is pinned
    # against the renamed control by test_requested_numeric_tuple_matches_renamed_control.
])
def test_outer_bindings_and_builtin_reads_keep_complete_cpp(source, reference, tmp_path):
    before = _reference_cpp(source, reference, tmp_path)
    cpp = transpile(source)
    assert cpp == before
    expected_reads = _HISTORY_READ.findall(before)
    assert cpp.strip() and expected_reads and _HISTORY_READ.findall(cpp) == expected_reads


@pytest.mark.parametrize("name,string", [
    pytest.param("second", True, id="chart-string-second"),
    pytest.param("high", False, id="chart-numeric-high"),
])
def test_chart_local_history_matches_renamed_control(name, string):
    cpp = _mask_manifest_notes(transpile(_chart_local_source(name, string=string)))
    control = _mask_manifest_notes(transpile(_chart_local_source("aa", string=string)))
    assert _normalize_local(cpp, name, control) == control
    assert control.strip() and _HISTORY_READ.findall(control)


def test_sibling_declaration_preserves_builtin_reader(reference, tmp_path):
    source = _sibling_source("second")
    before = _reference_cpp(source, reference, tmp_path)
    cpp = transpile(source)
    control = _mask_manifest_notes(transpile(_sibling_source("aa")))
    assert _normalize_local(_mask_manifest_notes(cpp), "second", control) == control
    # The declaring function changes; compare the reader and its evaluator.
    for function in ("g", "_eval_security_1"):
        definition = re.compile(
            rf"^    [^\n]+ {function}(?:_cs[0-9]+)?\([^\n]*\) \{{\n"
            rf".*?^    \}}",
            re.MULTILINE | re.DOTALL,
        )
        expected = definition.findall(before)
        assert expected, before
        assert definition.findall(cpp) == expected


_VARIANTS = [
    pytest.param(variant, string, id=f"{variant}-{'string' if string else 'numeric'}")
    for variant in ("history-free", "typed", "reassigned", "nested", "two-sites")
    for string in (False, True)
] + [
    pytest.param("loop", False, id="loop-numeric"),
    pytest.param("tuple", True, id="tuple-string"),
]


@pytest.mark.parametrize("name", _LOCAL_NAMES)
@pytest.mark.parametrize("variant,string", _VARIANTS)
def test_requested_local_variants_match_renamed_control(name, variant, string):
    source = _variant_source(name, variant, string=string)
    control_source = _variant_source("aa", variant, string=string)
    if variant == "tuple":
        with pytest.raises(CompileError) as control:
            transpile(control_source)
        with pytest.raises(CompileError) as subject:
            transpile(source)
        assert str(subject.value) == str(control.value)
    else:
        control = _mask_manifest_notes(transpile(control_source))
        cpp = _mask_manifest_notes(transpile(source))
        assert _normalize_local(cpp, name, control) == control


@pytest.mark.parametrize("name", _LOCAL_NAMES)
def test_requested_numeric_tuple_matches_renamed_control(name):
    # A numeric tuple element read with history, for every name of the census.
    control = _mask_manifest_notes(
        transpile(_variant_source("aa", "tuple", string=False))
    )
    cpp = _mask_manifest_notes(
        transpile(_variant_source(name, "tuple", string=False))
    )
    assert _normalize_local(cpp, name, control) == control


def _extended_callable_local_source(name, shape):
    if shape == "method":
        return _program(
            f"method read_local(float receiver) =>\n"
            f"    {name} = receiver * 2.0\n"
            f"    nz({name}[1]) + {name}\n",
            "v = close\nplot(v.read_local())\n",
        )
    if shape == "varip":
        body = (
            f"    varip float {name} = 0.0\n"
            f"    {name} := 1.5\n    nz({name}[1]) + {name}\n"
        )
    else:
        binder, value = {
            "for-in": (name, name),
            "for-in-index": (f"[{name}, item]", f"{name} + item"),
            "for-in-value": (f"[position, {name}]", f"position + {name}"),
        }[shape]
        body = (
            "    r = 0.0\n"
            f"    for {binder} in array.from(1.5, 2.5)\n"
            f"        r += {value}\n    r\n"
        )
    return _program("h() =>\n" + body, "plot(h())\n")


@pytest.mark.parametrize("name", _LOCAL_NAMES)
@pytest.mark.parametrize(
    "shape", ["method", "varip", "for-in", "for-in-index", "for-in-value"],
)
def test_method_persistent_and_iteration_locals_match_renamed_control(name, shape):
    control = _mask_manifest_notes(
        transpile(_extended_callable_local_source("aa", shape))
    )
    cpp = _mask_manifest_notes(
        transpile(_extended_callable_local_source(name, shape))
    )
    assert _normalize_local(cpp, name, control) == control


@pytest.mark.parametrize("name", _LOCAL_NAMES)
def test_typed_local_refusal_retains_authored_name(name):
    def source(local):
        return _program(
            f"h() =>\n    int {local} = 1.5\n    {local}\n",
            "plot(h())\n",
        )

    with pytest.raises(CompileError) as control:
        transpile(source("aa"))
    with pytest.raises(CompileError) as subject:
        transpile(source(name))
    message = str(subject.value)
    assert f"'{name}'" in message
    assert "__pfhist" not in message
    assert message.replace(f"'{name}'", "'aa'") == str(control.value)


@pytest.mark.parametrize("name", _LOCAL_NAMES)
def test_untitled_persistent_local_input_keeps_authored_key(name):
    cpp = transpile(_program(
        f"h() =>\n    var float {name} = input.float(1.5)\n    {name}\n",
        "plot(h())\n",
    ))
    assert re.search(rf'get_input_double\("{re.escape(name)}",', cpp), cpp
