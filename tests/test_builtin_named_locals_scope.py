"""Compare scoped builtin-named locals with reference or renamed-control C++.

Preservation rows compare complete C++; sibling readers also retain their
reference definitions. Local variants compare complete C++ after renaming,
or compare tuple-history refusal text with the control. These checks do not
compile or run the emitted code and supply no hosted-engine evidence.
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
            f"    var {type_name} {name} = {seed}\n    {name} := {value}\n"
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


def _normalize_local(cpp, name):
    spelling = re.escape(name)
    cpp = re.sub(rf"{spelling}__pfhist[0-9]+", "aa", cpp)
    # Keep host fields (bar.close) and the map reference's .first->second.
    # Only the local, its escaped spelling, and generated local names change.
    return re.sub(
        rf"(?<![\w.])(?<!first->)"
        rf"(?P<prefix>(?:_sec[0-9]+_[A-Za-z_][A-Za-z_0-9]*?_[0-9]+_"
        rf"|_pf_var_init_)?)"
        rf"(?:pf_safe_)?{spelling}(?P<suffix>(?:_cs[0-9]+)?)\b",
        r"\g<prefix>aa\g<suffix>",
        cpp,
    )


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
    pytest.param(
        _variant_source("second", "tuple", string=False),
        id="requested-numeric-tuple",
    ),
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
    cpp = transpile(_chart_local_source(name, string=string))
    control = transpile(_chart_local_source("aa", string=string))
    assert _normalize_local(cpp, name) == control
    assert control.strip() and _HISTORY_READ.findall(control)


def test_sibling_declaration_preserves_builtin_reader(reference, tmp_path):
    source = _sibling_source("second")
    before = _reference_cpp(source, reference, tmp_path)
    cpp = transpile(source)
    control = transpile(_sibling_source("aa"))
    assert _normalize_local(cpp, "second") == control
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
        control = transpile(control_source)
        assert _normalize_local(transpile(source), name) == control
