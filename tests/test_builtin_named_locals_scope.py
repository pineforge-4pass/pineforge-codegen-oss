"""Compare scoped builtin-named locals with reference or renamed-control C++.

Preservation rows compare complete C++, including embedded manifests;
sibling readers also retain their reference definitions. Local variants
compare complete C++ after renaming, or compare tuple-history refusal text
with the control. Compile rows using these same source builders live in
test_builtin_named_locals_compile.py. These checks do not run the emitted
code or supply hosted-engine evidence.
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
    control = transpile(source)
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


@pytest.fixture(scope="module")
def reference():
    tree = reference_codegen(CPP_REFERENCE)
    if tree is None:
        pytest.skip(f"The C++ reference commit {CPP_REFERENCE} is unavailable")
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
    cpp = transpile(_chart_local_source(name, string=string))
    control = transpile(_chart_local_source("aa", string=string))
    assert _normalize_local(cpp, name, control) == control
    assert control.strip() and _HISTORY_READ.findall(control)


def test_sibling_declaration_preserves_builtin_reader(reference, tmp_path):
    source = _sibling_source("second")
    before = _reference_cpp(source, reference, tmp_path)
    cpp = transpile(source)
    control = transpile(_sibling_source("aa"))
    assert _normalize_local(cpp, "second", control) == control
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
        cpp = transpile(source)
        assert _normalize_local(cpp, name, control) == control


@pytest.mark.parametrize("name", _LOCAL_NAMES)
def test_requested_numeric_tuple_matches_renamed_control(name):
    # A numeric tuple element read with history, for every name of the census.
    control = transpile(_variant_source("aa", "tuple", string=False))
    cpp = transpile(_variant_source(name, "tuple", string=False))
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
    control = transpile(_extended_callable_local_source("aa", shape))
    cpp = transpile(_extended_callable_local_source(name, shape))
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


_OBJECT_NAMESPACE_LOCAL_NAMES = ("dayofweek", "time", "timenow")


def _object_local_source(name):
    return _program(
        f"h() =>\n    Holder {name} = Holder.new(1.5)\n    {name}.value\n",
        "plot(h())\n",
        prelude="type Holder\n    float value\n",
    )


def test_object_local_namespace_rows_cover_the_builtin_intersection():
    from pineforge_codegen.analyzer.base import _BUILTIN_NAMESPACE_NAMES

    assert set(_OBJECT_NAMESPACE_LOCAL_NAMES) == (
        set(_LOCAL_NAMES) & _BUILTIN_NAMESPACE_NAMES
    )
    assert "second" in _LOCAL_NAMES
    assert "second" not in _BUILTIN_NAMESPACE_NAMES


@pytest.mark.parametrize("name", _OBJECT_NAMESPACE_LOCAL_NAMES)
def test_builtin_named_object_local_preserves_namespace_refusal(name):
    with pytest.raises(CompileError) as caught:
        transpile(_object_local_source(name))
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.message == (
        f"Invalid object name: {name}. Namespaces of built-ins cannot be used."
    )
    assert "__pfhist" not in diagnostic.message
    assert "__pfhist" not in str(caught.value)


def test_non_namespace_builtin_named_object_local_matches_renamed_control():
    control = transpile(_object_local_source("aa"))
    cpp = transpile(_object_local_source("second"))
    assert control.strip()
    assert _normalize_local(cpp, "second", control) == control


# Private storage allocation and authored diagnostic spellings.
_PRIVATE_STORAGE_TOKEN = re.compile(
    r"\b(?:second|minute)__pfhist[0-9]+(?:_cs[0-9]+|__ni[0-9]+)?\b"
)


def _allocation_source(authored):
    return _program(
        "h() =>\n    second = close\n    bb = nz(second[1])\n    second + bb\n",
        'v = request.security(syminfo.tickerid, "60", h())\n'
        'w = request.security(syminfo.tickerid, "240", h())\n'
        f"plot(v + w + {authored})\n",
        prelude=f"{authored} = 7.0\n",
    )


def _multiple_declaration_source(extra=()):
    declarations = "".join(f"{name} = {value}\n" for name, value in extra)
    more = "".join(f" + {name}" for name, _ in extra)
    return _program(
        "f() =>\n"
        "    second = close\n    bb = nz(second[1])\n    second + bb\n"
        "g() =>\n"
        "    minute = open\n    second = high\n"
        "    cc = nz(minute[1]) + nz(second[1])\n    minute + second + cc\n"
        "k() =>\n"
        "    r = 0.0\n    if close > open\n"
        "        second = low\n        r := nz(second[1])\n    r\n",
        declarations + f"plot(f() + g() + k(){more})\n",
    )


_SOURCE_ORDER_WRITES = {
    "second__pfhist1": "close",
    "minute__pfhist2": "open",
    "second__pfhist3": "high",
    "second__pfhist4": "low",
}
_PRIVATE_STORAGE_CASES = [
    (
        "authored-base", _allocation_source("second__pfhist1"),
        {"second__pfhist1"},
        {"second__pfhist1", "second__pfhist2", "second__pfhist2_cs1"},
        {"second__pfhist2": "close"},
    ),
    (
        "authored-call-clone", _allocation_source("second__pfhist1_cs1"),
        {"second__pfhist1_cs1"},
        {"second__pfhist1_cs1", "second__pfhist2", "second__pfhist2_cs1"},
        {"second__pfhist2": "close"},
    ),
    (
        "authored-nested-clone", _allocation_source("second__pfhist1__ni1"),
        {"second__pfhist1__ni1"},
        {"second__pfhist1__ni1", "second__pfhist2", "second__pfhist2_cs1"},
        {"second__pfhist2": "close"},
    ),
    (
        "later-authored-base", _allocation_source("second__pfhist2"),
        {"second__pfhist2"},
        {"second__pfhist2", "second__pfhist1", "second__pfhist1_cs1"},
        {"second__pfhist1": "close"},
    ),
    (
        "plain-global", _allocation_source("zz_plain"), {"zz_plain"},
        {"second__pfhist1", "second__pfhist1_cs1"},
        {"second__pfhist1": "close"},
    ),
    (
        "source-order", _multiple_declaration_source(), set(),
        {"second__pfhist1", "minute__pfhist2", "second__pfhist3", "second__pfhist4"},
        _SOURCE_ORDER_WRITES,
    ),
    (
        "unblocked-extra-names",
        _multiple_declaration_source((
            ("second__pfhist2", 1.0), ("minute__pfhist3", 2.0),
        )),
        {"second__pfhist2", "minute__pfhist3"},
        {"second__pfhist1", "second__pfhist2", "second__pfhist3",
         "second__pfhist4", "minute__pfhist2", "minute__pfhist3"},
        _SOURCE_ORDER_WRITES,
    ),
    (
        "reserved-source-order",
        _multiple_declaration_source((
            ("minute__pfhist2", 1.0), ("second__pfhist4", 2.0),
        )),
        {"minute__pfhist2", "second__pfhist4"},
        {"second__pfhist1", "minute__pfhist2", "minute__pfhist3",
         "second__pfhist4", "second__pfhist5", "second__pfhist6"},
        {"second__pfhist1": "close", "minute__pfhist3": "open",
         "second__pfhist5": "high", "second__pfhist6": "low"},
    ),
]


@pytest.mark.parametrize(
    "case", _PRIVATE_STORAGE_CASES, ids=[case[0] for case in _PRIVATE_STORAGE_CASES],
)
def test_private_storage_reserves_authored_and_clone_names_in_source_order(case):
    case_id, source, authored, expected, writes = case
    cpp = transpile(source)
    tokens = set(_PRIVATE_STORAGE_TOKEN.findall(cpp))
    assert tokens == expected, case_id
    members = set(re.findall(r"Series<double>\s+([A-Za-z_][A-Za-z_0-9]*)", cpp))
    private_members = {name for name in members if _PRIVATE_STORAGE_TOKEN.fullmatch(name)}
    assert private_members == tokens - authored
    assert not members & authored
    for name in authored:
        assert re.search(rf"\b{re.escape(name)}\b", cpp), (case_id, name)
    for storage, field in writes.items():
        assert re.search(
            rf"\b{re.escape(storage)}\.(?:push|update)\(current_bar_\.{field}\);", cpp,
        ), (case_id, storage, field)


def _typed_private_name_refusal_source(name):
    return _program(
        "h() =>\n    minute = 1.0\n"
        f"    int {name} = 1.5\n    {name}\n",
        "plot(h())\n",
        prelude="second__pfhist1 = 7.0\n",
    )


def test_refusal_restoration_distinguishes_allocated_and_authored_private_names():
    with pytest.raises(CompileError) as renamed:
        transpile(_typed_private_name_refusal_source("second"))
    assert len(renamed.value.diagnostics) == 1
    assert renamed.value.diagnostics[0].message == (
        "An int declaration cannot initialize 'second' with a float literal."
    )
    assert "second__pfhist" not in str(renamed.value)

    # The other colliding local keeps restoration active in this script too.
    with pytest.raises(CompileError) as authored:
        transpile(_typed_private_name_refusal_source("second__pfhist1"))
    assert len(authored.value.diagnostics) == 1
    assert authored.value.diagnostics[0].message == (
        "An int declaration cannot initialize 'second__pfhist1' with a float literal."
    )
    assert "'second__pfhist1'" in str(authored.value)


# Generator refusals, collection history and requests capturing locals.
def _matrix_receiver_source(name):
    return _program(
        f"h() =>\n    {name} = 1.0\n    matrix.det({name})\n",
        "plot(h())\n",
    )


@pytest.mark.parametrize("name", _LOCAL_NAMES)
def test_generator_refusal_restores_builtin_named_local(name):
    with pytest.raises(CompileError) as control:
        transpile(_matrix_receiver_source("aa"))
    with pytest.raises(CompileError) as subject:
        transpile(_matrix_receiver_source(name))
    assert len(control.value.diagnostics) == len(subject.value.diagnostics) == 1
    expected = control.value.diagnostics[0]
    actual = subject.value.diagnostics[0]
    assert expected.phase.value == actual.phase.value == "CODEGEN"
    assert "'aa'" in expected.message
    assert actual.message == expected.message.replace("'aa'", f"'{name}'")
    assert "__pfhist" not in actual.message
    assert "__pfhist" not in str(subject.value)


def _matrix_local_history_source(name):
    return _program(
        f"h() =>\n    matrix<float> {name} = matrix.new<float>(2, 2, 1.0)\n"
        f"    x = {name}[1]\n    matrix.det(x)\n",
        "plot(h())\n",
    )


def test_matrix_local_history_refusal_retains_authored_receiver():
    with pytest.raises(CompileError) as control:
        transpile(_matrix_local_history_source("aa"))
    with pytest.raises(CompileError) as subject:
        transpile(_matrix_local_history_source("second"))
    assert len(control.value.diagnostics) == len(subject.value.diagnostics) == 1
    expected = control.value.diagnostics[0]
    actual = subject.value.diagnostics[0]
    assert expected.phase.value == actual.phase.value == "ANALYZER"
    assert "aa[1]" in expected.message
    assert actual.message == expected.message.replace("aa[1]", "second[1]")
    assert "__pfhist" not in actual.message
    assert "__pfhist" not in str(subject.value)


def _captured_local_request_source(name):
    return _program(
        f"f() =>\n    {name} = close\n    p = nz({name}[1])\n"
        f'    request.security(syminfo.tickerid, "60", {name} + p)\n',
        "plot(f())\n",
    )


def _replace_neutral_name(value, name):
    if isinstance(value, str):
        return re.sub(r"\baa\b", name, value)
    if isinstance(value, list):
        return [_replace_neutral_name(item, name) for item in value]
    if isinstance(value, dict):
        return {key: _replace_neutral_name(item, name) for key, item in value.items()}
    return value


def _capabilities_manifest_literal(cpp):
    literals = [
        literal for literal in re.findall(r'"(?:\\.|[^"\\\n])*"', cpp)
        if r'\"unresolved\":[' in literal
    ]
    assert len(literals) == 1, literals
    return literals[0]


@pytest.mark.parametrize("name", _LOCAL_NAMES)
def test_captured_local_request_preserves_cpp_diagnostics_and_manifest(name):
    import json

    from pineforge_codegen import transpile_full

    control = transpile_full(_captured_local_request_source("aa"))
    subject = transpile_full(_captured_local_request_source(name))
    assert _normalize_local(subject["cpp"], name, control["cpp"]) == control["cpp"]
    expected_messages = [diagnostic.message for diagnostic in control["diagnostics"]]
    messages = [diagnostic.message for diagnostic in subject["diagnostics"]]
    assert expected_messages
    assert messages == _replace_neutral_name(expected_messages, name)
    assert subject["requests"] == _replace_neutral_name(control["requests"], name)
    assert all("__pfhist" not in message for message in messages)
    for result in (control, subject):
        literal = _capabilities_manifest_literal(result["cpp"])
        assert "__pfhist" not in literal
        manifest = json.loads(json.loads(literal))
        assert manifest["requests"]


# Primitive families, collections and additional callable blocks.
_ADDITIONAL_LOCAL_SHAPES = (
    "int", "bool", "color", "array", "switch", "while",
    "method-nested", "method-typed", "method-reassigned",
)


def _additional_callable_local_source(name, shape):
    bodies = {
        "int": f"    int {name} = 3\n    {name} + 1\n",
        "bool": f"    bool {name} = close > open\n    {name} ? 1.0 : 0.0\n",
        "color": f"    color {name} = color.red\n    color.r({name})\n",
        "array": (
            f"    array<float> {name} = array.from(1.0, 2.0)\n"
            f"    array.get({name}, 0)\n"
        ),
        "switch": (
            "    r = 0.0\n    switch\n        close > open =>\n"
            f"            {name} = close\n            r := nz({name}[1])\n"
            "        => r := 1.0\n    r\n"
        ),
        "while": (
            "    r = 0.0\n    i = 0\n    while i < 2\n"
            f"        {name} = close\n        r += nz({name}[1])\n"
            "        i += 1\n    r\n"
        ),
        "method-nested": (
            "    r = 0.0\n    if receiver > 0\n"
            f"        {name} = receiver * 2.0\n"
            f"        r := nz({name}[1]) + {name}\n    r\n"
        ),
        "method-typed": (
            f"    float {name} = receiver * 2.0\n    nz({name}[1]) + {name}\n"
        ),
        "method-reassigned": (
            f"    var float {name} = 0.0\n    {name} := receiver\n"
            f"    nz({name}[1]) + {name}\n"
        ),
    }
    if shape.startswith("method-"):
        return _program(
            "method m(float receiver) =>\n" + bodies[shape],
            "v = close\nplot(v.m())\n",
        )
    return _program("h() =>\n" + bodies[shape], "plot(h())\n")


@pytest.mark.parametrize("name", _LOCAL_NAMES)
@pytest.mark.parametrize("shape", _ADDITIONAL_LOCAL_SHAPES)
def test_additional_local_types_and_blocks_match_renamed_control(name, shape):
    control = transpile(_additional_callable_local_source("aa", shape))
    cpp = transpile(_additional_callable_local_source(name, shape))
    assert _normalize_local(cpp, name, control) == control


@pytest.mark.parametrize("source", [
    pytest.param(
        _program(
            "f() =>\n    hl2[1] + hlc3[2] + hlcc4[1] + ohlc4[1]\n",
            "plot(f())\n",
        ),
        id="chart-derived-price-history",
    ),
])
def test_builtin_derived_price_history_keeps_complete_cpp(source, reference, tmp_path):
    before = _reference_cpp(source, reference, tmp_path)
    cpp = transpile(source)
    assert cpp == before
    expected_reads = _HISTORY_READ.findall(before)
    assert cpp.strip() and expected_reads and _HISTORY_READ.findall(cpp) == expected_reads


def test_callable_local_census_matches_independent_builtin_inventories():
    from pineforge_codegen.analyzer.callable_history_names import _builtin_callable_local_names

    assert _builtin_callable_local_names() == set(_LOCAL_NAMES)
