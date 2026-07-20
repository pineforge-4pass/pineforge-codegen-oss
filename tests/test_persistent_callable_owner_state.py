"""Regression coverage for callable-owned persistent member identity.

Two ordinary Pine functions may each declare a persistent local with the same
lexical name.  The generated C++ storage must remain independent by callable
owner (and then by call site) while the Pine body continues to use the raw
lexical spelling.
"""

from __future__ import annotations

import math
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests.test_runtime_var_initialization import _compile_and_run


def _leaf(name: str, *, array: bool) -> list[str]:
    left = name == "left"
    seed = "1.0" if left else "100.0"
    step = "1.0" if left else "10.0"
    if array:
        return [
            f"{name}() =>",
            f"    var array<float> state = array.from({seed})",
            f"    state.push({step})",
            "    state.sum()",
        ]
    return [
        f"{name}() =>",
        f"    var float state = {seed}",
        f"    state += {step}",
        "    state",
    ]


def _owner_source(
    *, array: bool, reverse: bool, nested: bool, multi: bool
) -> tuple[str, list[str]]:
    order = ("right", "left") if reverse else ("left", "right")
    lines = ["//@version=6", 'strategy("persistent owner regression")']
    for name in order:
        lines.extend(_leaf(name, array=array))

    if nested:
        lines.extend(("left_path() => left()", "right_path() => right()"))
        left_call, right_call = "left_path()", "right_path()"
    else:
        left_call, right_call = "left()", "right()"

    outputs = ["observed_left_one", "observed_right_one"]
    lines.extend(
        (
            f"{outputs[0]} = {left_call}",
            f"{outputs[1]} = {right_call}",
        )
    )
    if multi:
        outputs.extend(("observed_left_two", "observed_right_two"))
        lines.extend(
            (
                f"{outputs[2]} = {left_call}",
                f"{outputs[3]} = {right_call}",
            )
        )
    return "\n".join(lines) + "\n", outputs


def _driver(outputs: list[str], *, bars: int = 2) -> str:
    streamed = ' << " " << '.join(f"strategy.{name}" for name in outputs)
    all_bars = (
        "Bar{1.0, 1.0, 1.0, 1.0, 1.0, 0},\n        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 60000}"
    )
    return f"""\n#include <iomanip>
#include <iostream>
int main() {{
    Bar input[] = {{
        {all_bars}
    }};
    GeneratedStrategy strategy;
    strategy.run(input, {bars});
    std::cout << std::fixed << std::setprecision(1)
              << {streamed} << "\\n";
    return 0;
}}
"""


def _checkpoint_members(cpp: str) -> list[str]:
    """Return the generated state identities captured by the rollback hook."""
    marker = "_pf_script_state_checkpoint_.emplace(_PFScriptState{"
    start = cpp.index(marker) + len(marker)
    end = cpp.index("\n        });", start)
    return re.findall(r"^\s+([A-Za-z_][A-Za-z0-9_]*),$", cpp[start:end], re.M)


def _qualified_member(cpp: str, cpp_type: str, raw: str, owner: str) -> str:
    """Resolve one opaque reserved member without pinning its sequence token."""
    match = re.search(
        rf"^    {re.escape(cpp_type)} "
        rf"(_pfv_[0-9]+_{re.escape(raw)}__{re.escape(owner)})"
        rf"(?: = [^;]+)?;$",
        cpp,
        re.M,
    )
    assert match is not None
    return match.group(1)


@pytest.mark.parametrize("reverse", [False, True], ids=["left-first", "right-first"])
@pytest.mark.parametrize("array", [False, True], ids=["scalar", "array"])
def test_same_raw_name_in_separate_udfs_gets_owner_storage(
    array: bool, reverse: bool
) -> None:
    source, _outputs = _owner_source(
        array=array, reverse=reverse, nested=False, multi=False
    )
    cpp = transpile(source)
    cpp_type = "std::vector<double>" if array else "double"

    left = _qualified_member(cpp, cpp_type, "state", "left")
    right = _qualified_member(cpp, cpp_type, "state", "right")
    assert not re.search(r"^    (?:double|std::vector<double>) state;$", cpp, re.M)
    assert f"{left} =" in cpp
    assert f"{right} =" in cpp
    compile_cpp(cpp, label=f"persistent-owner-{int(array)}-{int(reverse)}")


@pytest.mark.parametrize("array", [False, True], ids=["scalar", "array"])
def test_reverse_nested_multicallsite_storage_is_independent_at_runtime(
    array: bool,
) -> None:
    source, outputs = _owner_source(array=array, reverse=True, nested=True, multi=True)
    cpp = transpile(source)
    cpp_type = "std::vector<double>" if array else "double"

    left = _qualified_member(cpp, cpp_type, "state", "left")
    right = _qualified_member(cpp, cpp_type, "state", "right")
    for name in (left, right, f"{left}_cs1", f"{right}_cs1"):
        declaration = (
            f"    {cpp_type} {name} = na<double>();"
            if not array
            else f"    {cpp_type} {name};"
        )
        assert declaration in cpp

    assert _compile_and_run(cpp + _driver(outputs)) == ("3.0 120.0 3.0 120.0\n")


def test_owner_name_that_looks_like_callsite_suffix_cannot_alias_clone() -> None:
    source = """//@version=6
strategy("persistent owner suffix namespace")
left() =>
    var float state = 1.0
    state += 1.0
    state
left_cs1() =>
    var float state = 100.0
    state += 10.0
    state
a = left()
b = left()
c = left_cs1()
"""
    cpp = transpile(source)
    checkpoint_members = _checkpoint_members(cpp)

    left = _qualified_member(cpp, "double", "state", "left")
    suffix_owner = _qualified_member(cpp, "double", "state", "left_cs1")
    qualified = [name for name in checkpoint_members if name.startswith("_pfv_")]
    assert len(qualified) == len(set(qualified))
    assert {left, f"{left}_cs1", suffix_owner}.issubset(qualified)
    compile_cpp(cpp, label="persistent-owner-callsite-looking-owner")
    assert _compile_and_run(cpp + _driver(["a", "b", "c"])) == ("3.0 3.0 120.0\n")


def test_qualified_storage_cannot_alias_generated_function_init_flag() -> None:
    source = """//@version=6
strategy("owner function flag collision")
something_cs0() =>
    var float _fvinit_left = 1.0
    _fvinit_left += 1.0
    _fvinit_left
right() =>
    var float _fvinit_left = 100.0
    _fvinit_left += 10.0
    _fvinit_left
left__pf_something() =>
    var float unique = 7.0
    unique += 1.0
    unique
a = something_cs0()
b = right()
c = left__pf_something()
"""
    cpp = transpile(source)
    checkpoint_members = _checkpoint_members(cpp)

    assert len(checkpoint_members) == len(set(checkpoint_members))
    first = _qualified_member(cpp, "double", "_fvinit_left", "something_cs0")
    second = _qualified_member(cpp, "double", "_fvinit_left", "right")
    assert checkpoint_members.count(first) == 1
    assert checkpoint_members.count(second) == 1
    assert checkpoint_members.count("_fvinit_left__pf_something_cs0") == 1
    compile_cpp(cpp, label="persistent-owner-function-init-flag")
    assert _compile_and_run(cpp + _driver(["a", "b", "c"], bars=1)) == (
        "2.0 110.0 8.0\n"
    )


def test_qualified_storage_cannot_alias_legacy_authored_base_clone() -> None:
    source = """//@version=6
strategy("qualified versus legacy clone collision")
left_cs1() =>
    var float state = 1.0
    state += 1.0
    state
right() =>
    var float state = 100.0
    state += 10.0
    state
existing() =>
    var float _pfv_1_state__left = 7.0
    _pfv_1_state__left += 1.0
    _pfv_1_state__left
a = left_cs1()
b = right()
c = existing()
d = existing()
"""
    cpp = transpile(source)
    checkpoint_members = _checkpoint_members(cpp)

    qualified = _qualified_member(cpp, "double", "state", "left_cs1")
    _qualified_member(cpp, "double", "state", "right")
    assert len(checkpoint_members) == len(set(checkpoint_members))
    assert checkpoint_members.count("_pfv_1_state__left") == 1
    assert checkpoint_members.count("_pfv_1_state__left_cs1") == 1
    assert qualified not in {"_pfv_1_state__left", "_pfv_1_state__left_cs1"}
    compile_cpp(cpp, label="persistent-owner-legacy-authored-base-clone")
    assert _compile_and_run(cpp + _driver(["a", "b", "c", "d"], bars=1)) == (
        "2.0 110.0 8.0 8.0\n"
    )


def test_qualified_storage_cannot_alias_generated_sibling_block_clone() -> None:
    source = """//@version=6
strategy("qualified versus generated block clone")
blk1_cs1() =>
    var float state = 1.0
    state += 1.0
    state
right() =>
    var float state = 100.0
    state += 10.0
    state
existing(bool a, bool b) =>
    if a
        var float _pfv_1_state = 7.0
        _pfv_1_state += 1.0
    if b
        var float _pfv_1_state = 70.0
        _pfv_1_state += 10.0
    0.0
x = blk1_cs1()
y = right()
a = existing(true, false)
b = existing(false, true)
"""
    cpp = transpile(source)
    checkpoint_members = _checkpoint_members(cpp)

    qualified = _qualified_member(cpp, "double", "state", "blk1_cs1")
    block_clone = "_pfv_1_state__blk1_cs1"
    assert qualified != block_clone
    assert len(checkpoint_members) == len(set(checkpoint_members))
    assert checkpoint_members.count(qualified) == 1
    assert checkpoint_members.count(block_clone) == 1
    compile_cpp(cpp, label="persistent-owner-generated-block-clone")
    observed = tuple(
        float(value)
        for value in _compile_and_run(
            cpp
            + _driver(
                [
                    "x",
                    "y",
                    "_pfv_1_state",
                    "_pfv_1_state__blk1",
                    "_pfv_1_state_cs1",
                    block_clone,
                ],
                bars=1,
            )
        ).split()
    )
    assert observed[:3] == (2.0, 110.0, 8.0)
    assert math.isnan(observed[3])
    assert math.isnan(observed[4])
    assert observed[5] == 80.0


def test_direct_and_block_persistent_name_ambiguity_fails_closed() -> None:
    source = """//@version=6
strategy("direct and block persistent ambiguity")
left(bool arm) =>
    var float state = 1.0
    state += 1.0
    if arm
        var float state = 100.0
        state += 10.0
    state
right() =>
    var float state = 1000.0
    state += 100.0
    state
a = left(true)
b = right()
"""
    with pytest.raises(
        CompileError,
        match=(
            "Persistent bindings named 'state' still share generated storage "
            "across distinct lexical declarations; this declaration shape is "
            "not supported yet"
        ),
    ):
        transpile(source)


def test_unsupported_udt_and_plain_global_collision_fails_closed() -> None:
    source = """//@version=6
strategy("unsupported UDT ordinary global collision")
type Holder
    int value
make() =>
    var Holder state = Holder.new(1)
    state
result = make()
state = 7.0
"""
    with pytest.raises(
        CompileError,
        match=(
            "Persistent bindings named 'state' still share generated storage "
            "across distinct lexical declarations; this declaration shape is "
            "not supported yet"
        ),
    ):
        transpile(source)


@pytest.mark.parametrize(
    ("source", "expected_members"),
    (
        pytest.param(
            """//@version=6
strategy("mixed primitive owners")
left() =>
    var int state = 1
    state += 1
    state
right() =>
    var string state = "a"
    state += "b"
    state
left_value = left()
right_value = right()
""",
            (("int", "state", "left"), ("std::string", "state", "right")),
            id="primitive-types",
        ),
        pytest.param(
            """//@version=6
strategy("mixed array element owners")
left() =>
    var array<int> state = array.from(1)
    state.push(2)
    state.size()
right() =>
    var array<string> state = array.from("a")
    state.push("b")
    state.size()
left_value = left()
right_value = right()
""",
            (
                ("std::vector<int>", "state", "left"),
                ("std::vector<std::string>", "state", "right"),
            ),
            id="array-element-types",
        ),
    ),
)
def test_owner_qualification_preserves_exact_types(
    source: str, expected_members: tuple[tuple[str, str, str], ...]
) -> None:
    cpp = transpile(source)
    for cpp_type, raw, owner in expected_members:
        _qualified_member(cpp, cpp_type, raw, owner)
    compile_cpp(cpp, label="persistent-owner-mixed-types")


def test_persistent_map_owners_keep_independent_sizes_at_runtime() -> None:
    source = """//@version=6
strategy("persistent map owners")
left() =>
    var map<string, int> state = map.new<string, int>()
    if bar_index == 0
        state.put("left-zero", 1)
    else
        state.put("left-later", 2)
    state.size()
right() =>
    var map<string, int> state = map.new<string, int>()
    state.put("right", 10)
    state.size()
left_value = left()
right_value = right()
"""
    cpp = transpile(source)
    cpp_type = "PineMap<std::string, int>"
    left = _qualified_member(cpp, cpp_type, "state", "left")
    right = _qualified_member(cpp, cpp_type, "state", "right")

    assert left != right
    compile_cpp(cpp, label="persistent-owner-map-runtime")
    observed = _compile_and_run(cpp + _driver(["left_value", "right_value"])).split()
    assert [float(value) for value in observed] == [2.0, 1.0]


def test_history_scalar_and_map_owners_compile_and_run_independently() -> None:
    source = """//@version=6
strategy("persistent mixed scalar map owners")
left() =>
    var float state = 1.0
    prior = state[1]
    state += 1.0
    state
right() =>
    var map<string, int> state = map.new<string, int>()
    state.put("right", 10)
    state.size()
left_value = left()
right_value = right()
"""
    cpp = transpile(source)
    left = _qualified_member(cpp, "Series<double>", "state", "left")
    right = _qualified_member(cpp, "PineMap<std::string, int>", "state", "right")

    assert left != right
    compile_cpp(cpp, label="persistent-owner-history-scalar-map")
    observed = _compile_and_run(cpp + _driver(["left_value", "right_value"])).split()
    assert [float(value) for value in observed] == [3.0, 1.0]


def test_persistent_matrix_owners_keep_independent_values_at_runtime() -> None:
    source = """//@version=6
strategy("persistent matrix owners")
left() =>
    var matrix<int> state = matrix.new<int>(1, 1, 1)
    state.set(0, 0, state.get(0, 0) + 1)
    state.get(0, 0)
right() =>
    var matrix<int> state = matrix.new<int>(1, 1, 100)
    state.set(0, 0, state.get(0, 0) + 10)
    state.get(0, 0)
left_value = left()
right_value = right()
"""
    cpp = transpile(source)
    cpp_type = "PineGenericMatrix<int>"
    left = _qualified_member(cpp, cpp_type, "state", "left")
    right = _qualified_member(cpp, cpp_type, "state", "right")

    assert left != right
    compile_cpp(cpp, label="persistent-owner-matrix-runtime")
    observed = _compile_and_run(cpp + _driver(["left_value", "right_value"])).split()
    assert [float(value) for value in observed] == [3.0, 120.0]


def test_three_callable_owners_get_three_independent_members() -> None:
    cpp = transpile("""//@version=6
strategy("three persistent owners")
alpha() =>
    var int dominantPeriod = 1
    dominantPeriod += 1
    dominantPeriod
beta() =>
    var int dominantPeriod = 10
    dominantPeriod += 2
    dominantPeriod
gamma() =>
    var int dominantPeriod = 100
    dominantPeriod += 3
    dominantPeriod
a = alpha()
b = beta()
c = gamma()
""")

    for owner in ("alpha", "beta", "gamma"):
        member = _qualified_member(cpp, "int", "dominantPeriod", owner)
        assert f"{member} +=" in cpp
    assert "    int dominantPeriod;" not in cpp
    compile_cpp(cpp, label="persistent-owner-three-callables")


def test_history_persistent_members_and_callsite_clones_keep_series_types() -> None:
    source = """//@version=6
strategy("persistent owner history")
left() =>
    var float state = 1.0
    state += 1.0
    nz(state[1])
right() =>
    var float state = 100.0
    state += 10.0
    nz(state[1])
l1 = left()
r1 = right()
l2 = left()
r2 = right()
"""
    cpp = transpile(source)

    left = _qualified_member(cpp, "Series<double>", "state", "left")
    right = _qualified_member(cpp, "Series<double>", "state", "right")
    for name in (left, right, f"{left}_cs1", f"{right}_cs1"):
        assert f"    Series<double> {name};" in cpp
        assert f"{name}.update(" in cpp
        assert f"{name}[1]" in cpp

    compile_cpp(cpp, label="persistent-owner-history-clones")
    assert _compile_and_run(cpp + _driver(["l1", "r1", "l2", "r2"])) == (
        "2.0 110.0 2.0 110.0\n"
    )


def test_noncolliding_persistent_output_keeps_legacy_member_names() -> None:
    cpp = transpile("""//@version=6
strategy("noncolliding persistent owner")
only() =>
    var float state = 1.0
    state += 1.0
    state
a = only()
b = only()
""")

    assert "_pfv_" not in cpp
    assert "    double state = na<double>();" in cpp
    assert "    double state_cs1 = na<double>();" in cpp
    assert "state = 1.0;" in cpp
    assert "state_cs1 = 1.0;" in cpp
    compile_cpp(cpp, label="persistent-owner-noncolliding")


def test_initializer_rhs_reads_same_named_global_before_local_activation() -> None:
    source = """//@version=6
strategy("persistent owner global rhs")
var float state = 5.0
left() =>
    var float state = state + 1.0
    state += 1.0
    state
right() =>
    var float state = state + 100.0
    state += 10.0
    state
a = left()
b = right()
"""
    cpp = transpile(source)

    assert "    double state;" in cpp
    left = _qualified_member(cpp, "double", "state", "left")
    right = _qualified_member(cpp, "double", "state", "right")
    assert f"{left} = (state + 1.0);" in cpp
    assert f"{right} = (state + 100.0);" in cpp
    assert _compile_and_run(cpp + _driver(["a", "b"], bars=1)) == ("7.0 115.0\n")


def test_unrelated_history_parameter_does_not_poison_qualified_clone_init() -> None:
    source = """//@version=6
strategy("qualified init plus unrelated series param")
state = 5.0
hist(float state) => nz(state[1])
left() =>
    var float state = state + 1.0
    state += 1.0
    state
right() =>
    var float state = state + 100.0
    state += 10.0
    state
a = left()
b = left()
c = right()
d = hist(close)
"""
    cpp = transpile(source)
    left = _qualified_member(cpp, "double", "state", "left")
    right = _qualified_member(cpp, "double", "state", "right")

    assert "    Series<double> state_cs1;" not in cpp
    assert f"{left} = (state + 1.0);" in cpp
    assert f"{left}_cs1 = (state + 1.0);" in cpp
    assert f"{right} = (state + 100.0);" in cpp
    compile_cpp(cpp, label="persistent-owner-unrelated-series-param")
    assert _compile_and_run(cpp + _driver(["a", "b", "c"], bars=1)) == (
        "7.0 7.0 115.0\n"
    )


def test_callable_history_local_shadowing_direct_global_fails_closed() -> None:
    source = """//@version=6
strategy("global plus callable local history")
state = 5.0
hist() =>
    state = close
    nz(state[1])
observed = hist()
"""

    with pytest.raises(
        CompileError,
        match="declaration-exact callable storage",
    ):
        transpile(source)


def test_security_only_history_helper_global_collision_fails_closed() -> None:
    source = """//@version=6
strategy("security helper global owner collision")
state = 5.0
hist() =>
    state = close
    nz(state[1])
owner() =>
    var float state = state + 1.0
    state += 1.0
    state
owned = owner()
observed = request.security(syminfo.tickerid, "60", hist())
"""

    with pytest.raises(
        CompileError,
        match="declaration-exact callable storage",
    ):
        transpile(source)


def test_same_named_nonpersistent_history_locals_in_udfs_fail_closed() -> None:
    source = """//@version=6
strategy("same-named callable history locals")
left() =>
    state = close
    nz(state[1])
right() =>
    state = open
    nz(state[1])
a = left()
b = right()
"""

    with pytest.raises(
        CompileError,
        match="declaration-exact callable storage",
    ):
        transpile(source)


def test_embedded_callable_history_local_collision_fails_closed() -> None:
    source = """//@version=6
strategy("embedded callable history local")
left() =>
    result = if true
        state = close
        nz(state[1])
    else
        0.0
    result
right() =>
    state = open
    nz(state[1])
a = left()
b = right()
"""

    with pytest.raises(
        CompileError,
        match="declaration-exact callable storage",
    ):
        transpile(source)


def test_plain_global_and_udf_persistent_up_trend_have_distinct_state() -> None:
    source = """//@version=6
strategy("ordinary global persistent owner")
vol_stop_up_trend() =>
    var bool up_trend = true
    up_trend := not up_trend
    up_trend
local_up_trend = vol_stop_up_trend()
up_trend = not local_up_trend
"""
    cpp = transpile(source)
    persistent = _qualified_member(cpp, "bool", "up_trend", "vol_stop_up_trend")
    checkpoint_members = _checkpoint_members(cpp)

    compile_cpp(cpp, label="persistent-owner-ordinary-global-up-trend")
    assert "    bool up_trend = false;" in cpp
    assert checkpoint_members.count("up_trend") == 1
    assert checkpoint_members.count(persistent) == 1
    assert len(checkpoint_members) == len(set(checkpoint_members))
    driver = f"""\n#include <iostream>
int main() {{
    Bar input[] = {{
        Bar{{1.0, 1.0, 1.0, 1.0, 1.0, 0}},
        Bar{{2.0, 2.0, 2.0, 2.0, 1.0, 60000}}
    }};
    GeneratedStrategy strategy;
    strategy.run(input, 2);
    std::cout << std::boolalpha
              << strategy.local_up_trend << " "
              << strategy.up_trend << " "
              << strategy.{persistent} << "\\n";
    return 0;
}}
"""
    assert _compile_and_run(cpp + driver) == "true false true\n"


def test_history_global_and_udf_persistent_state_are_independent_at_runtime() -> None:
    source = """//@version=6
strategy("ordinary global history persistent owner")
left() =>
    var float state = 1.0
    prior = state[1]
    state += 1.0
    state
a = left()
state = 100.0
global_prior = state[1]
"""
    cpp = transpile(source)
    persistent = _qualified_member(cpp, "Series<double>", "state", "left")
    checkpoint_members = _checkpoint_members(cpp)

    assert "    Series<double> state;" in cpp
    assert checkpoint_members.count("state") == 1
    assert checkpoint_members.count(persistent) == 1
    assert len(checkpoint_members) == len(set(checkpoint_members))
    compile_cpp(cpp, label="persistent-owner-ordinary-global-history")
    assert _compile_and_run(cpp + _driver(["a", "global_prior"])) == ("3.0 100.0\n")


@pytest.mark.parametrize(
    "global_first", [False, True], ids=["udf-first", "global-first"]
)
def test_scalar_global_stays_scalar_when_udf_persistent_has_history(
    global_first: bool,
) -> None:
    udf = """left() =>
    var float state = 1.0
    prior = state[1]
    state += 1.0
    state
"""
    prefix = '//@version=6\nstrategy("scalar global versus persistent history")\n'
    if global_first:
        source = prefix + "state = 100.0\n" + udf + "a = left()\n"
    else:
        source = prefix + udf + "a = left()\nstate = 100.0\n"
    source += "read() => state\nb = read()\n"
    cpp = transpile(source)
    persistent = _qualified_member(cpp, "Series<double>", "state", "left")
    checkpoint_members = _checkpoint_members(cpp)

    assert "    double state = 0.0;" in cpp
    assert "    Series<double> state;" not in cpp
    assert checkpoint_members.count("state") == 1
    assert checkpoint_members.count(persistent) == 1
    compile_cpp(cpp, label="persistent-owner-scalar-global-history-udf")
    assert _compile_and_run(cpp + _driver(["a", "b"])) == "3.0 100.0\n"


@pytest.mark.parametrize(
    "global_first", [False, True], ids=["udf-first", "global-first"]
)
def test_persistent_scalar_global_and_history_udf_keep_exact_storage(
    global_first: bool,
) -> None:
    """A top-level ``var`` and a history UDF may share a Pine spelling."""
    prefix = '//@version=6\nstrategy("persistent global versus history UDF")\n'
    global_decl = "var float state = 100.0\n"
    udf = """left() =>
    var float state = 1.0
    prior = state[1]
    state += 1.0
    state
"""
    source = prefix + (global_decl + udf if global_first else udf + global_decl)
    source += "a = left()\nb = state\n"

    cpp = transpile(source)
    persistent = _qualified_member(cpp, "Series<double>", "state", "left")
    checkpoint_members = _checkpoint_members(cpp)

    assert "    double state;" in cpp
    assert "    Series<double> state;" not in cpp
    assert checkpoint_members.count("state") == 1
    assert checkpoint_members.count(persistent) == 1
    compile_cpp(
        cpp,
        label=f"persistent-owner-var-global-history-{global_first}",
    )
    assert _compile_and_run(cpp + _driver(["a", "b"])) == "3.0 100.0\n"


@pytest.mark.parametrize(
    "global_first", [False, True], ids=["udf-first", "global-first"]
)
def test_input_source_global_remains_scalar_with_same_named_history_udf(
    global_first: bool,
) -> None:
    """History metadata from the UDF must not turn source replay into push."""
    prefix = '//@version=6\nstrategy("source global versus history UDF")\n'
    global_decl = 'state = input.source(close, "State")\n'
    udf = """left() =>
    var float state = 1.0
    prior = state[1]
    state += 1.0
    state
"""
    source = prefix + (global_decl + udf if global_first else udf + global_decl)
    source += "a = left()\nbasis = ta.sma(state, 2)\nb = state\n"

    cpp = transpile(source)
    _qualified_member(cpp, "Series<double>", "state", "left")
    replay = 'state = get_input_source("State", _src_close_)[0];'

    assert "    double state = 0.0;" in cpp
    assert "    Series<double> state;" not in cpp
    # One replay is in normal dispatch and one feeds the TA precalc loop.
    assert cpp.count(replay) == 2
    assert 'state.push(get_input_source("State", _src_close_)[0]);' not in cpp
    compile_cpp(
        cpp,
        label=f"persistent-owner-input-source-history-{global_first}",
    )
    assert _compile_and_run(cpp + _driver(["a", "b"])) == "3.0 2.0\n"


def test_post_declaration_read_resolves_qualified_persistent_series() -> None:
    source = """//@version=6
strategy("post declaration persistent read")
left() =>
    var float state = 1.0
    prior = state[1]
    var float copy = state
    state += 1.0
    copy
a = left()
state = 100.0
read() => state
b = read()
"""
    cpp = transpile(source)
    persistent = _qualified_member(cpp, "Series<double>", "state", "left")

    assert "    double state = 0.0;" in cpp
    assert f"copy = {persistent}[0];" in cpp
    assert "copy = state;" not in cpp
    compile_cpp(cpp, label="persistent-owner-post-declaration-series-read")
    assert _compile_and_run(cpp + _driver(["a", "b"])) == "1.0 100.0\n"


def test_map_global_keeps_exact_type_when_udf_persistent_has_history() -> None:
    source = """//@version=6
strategy("map global versus persistent history")
left() =>
    var float state = 1.0
    prior = state[1]
    state += 1.0
    state
a = left()
state = map.new<string, int>()
state.put("k", 7)
read() => state.size()
b = read()
"""
    cpp = transpile(source)
    persistent = _qualified_member(cpp, "Series<double>", "state", "left")

    assert "    PineMap<std::string, int> state;" in cpp
    assert "    Series<double> state;" not in cpp
    assert persistent != "state"
    compile_cpp(cpp, label="persistent-owner-map-global-history-udf")
    observed = [
        float(value) for value in _compile_and_run(cpp + _driver(["a", "b"])).split()
    ]
    assert observed == [3.0, 1.0]


@pytest.mark.parametrize(
    "global_first", [False, True], ids=["udf-first", "global-first"]
)
@pytest.mark.parametrize(
    ("family", "global_body", "reader", "member_decl", "expected_global"),
    (
        (
            "array",
            "state = array.from(7.0)",
            "read() => state.size()",
            "std::vector<double> state;",
            1.0,
        ),
        (
            "map",
            'state = map.new<string, float>()\nstate.put("k", 7.0)',
            "read() => state.size()",
            "PineMap<std::string, double> state;",
            1.0,
        ),
        (
            "matrix",
            "state = matrix.new<float>(1, 1, 7.0)",
            "read() => state.get(0, 0)",
            "PineMatrix state;",
            7.0,
        ),
    ),
)
def test_collection_global_before_or_after_history_udf_keeps_exact_types(
    family: str,
    global_body: str,
    reader: str,
    member_decl: str,
    expected_global: float,
    global_first: bool,
) -> None:
    prefix = f'//@version=6\nstrategy("{family} global source order")\n'
    udf = """left() =>
    var float state = 1.0
    prior = state[1]
    state += 1.0
    state
"""
    if global_first:
        source = prefix + global_body + "\n" + udf + "a = left()\n"
    else:
        source = prefix + udf + "a = left()\n" + global_body + "\n"
    source += reader + "\nb = read()\n"

    cpp = transpile(source)
    _qualified_member(cpp, "Series<double>", "state", "left")
    assert f"    {member_decl}" in cpp
    assert "double left_cs0()" in cpp
    compile_cpp(cpp, label=f"persistent-owner-{family}-global-{global_first}")
    observed = [
        float(value) for value in _compile_and_run(cpp + _driver(["a", "b"])).split()
    ]
    assert observed == [3.0, expected_global]


def test_tuple_global_and_udf_persistent_state_are_independent_at_runtime() -> None:
    source = """//@version=6
strategy("ordinary tuple global persistent owner")
left() =>
    var float state = 1.0
    state += 1.0
    state
pair() => [100.0, 7.0]
a = left()
[state, other] = pair()
read() => state
b = read()
"""
    cpp = transpile(source)
    persistent = _qualified_member(cpp, "double", "state", "left")
    checkpoint_members = _checkpoint_members(cpp)

    assert "    double state = 0.0;" in cpp
    assert checkpoint_members.count("state") == 1
    assert checkpoint_members.count(persistent) == 1
    assert len(checkpoint_members) == len(set(checkpoint_members))
    compile_cpp(cpp, label="persistent-owner-ordinary-global-tuple")
    assert _compile_and_run(cpp + _driver(["a", "b"])) == "3.0 100.0\n"


def test_tuple_global_stays_scalar_when_udf_persistent_has_history() -> None:
    source = """//@version=6
strategy("tuple global versus persistent history")
left() =>
    var float state = 1.0
    prior = state[1]
    state += 1.0
    state
pair() => [100.0, 7.0]
a = left()
[state, other] = pair()
read() => state
b = read()
"""
    cpp = transpile(source)
    persistent = _qualified_member(cpp, "Series<double>", "state", "left")
    checkpoint_members = _checkpoint_members(cpp)

    assert "    double state = 0.0;" in cpp
    assert "    Series<double> state;" not in cpp
    assert checkpoint_members.count("state") == 1
    assert checkpoint_members.count(persistent) == 1
    compile_cpp(cpp, label="persistent-owner-tuple-global-history-udf")
    assert _compile_and_run(cpp + _driver(["a", "b"])) == "3.0 100.0\n"


def test_block_local_map_and_history_udf_raw_collision_fails_closed() -> None:
    source = """//@version=6
strategy("block map versus persistent history")
if true
    state = map.new<string, int>()
    state.put("k", 7)
left() =>
    var float state = 1.0
    prior = state[1]
    state += 1.0
    state
a = left()
"""
    with pytest.raises(
        CompileError,
        match=(
            "History references on scalar local or loop bindings that shadow a map ID"
        ),
    ):
        transpile(source)


@pytest.mark.parametrize(
    "udf_before_block",
    [False, True],
    ids=["block-before-udf", "udf-before-block"],
)
def test_nested_block_shadow_does_not_assign_same_named_direct_global(
    udf_before_block: bool,
) -> None:
    prefix = """//@version=6
strategy("ordinary global nested shadow")
state = 100.0
"""
    udf = """left() =>
    var float state = 1.0
    prior = state[1]
    state += 1.0
    state
"""
    block = """if true
    state = 7.0
    local_seen = state
"""
    if udf_before_block:
        source = prefix + udf + "a = left()\n" + block
    else:
        source = prefix + block + udf + "a = left()\n"
    source += "read() => state\nb = read()\n"

    cpp = transpile(source)
    persistent = _qualified_member(cpp, "Series<double>", "state", "left")
    checkpoint_members = _checkpoint_members(cpp)

    assert "    double state = 0.0;" in cpp
    assert re.search(r"^\s{12}double state = 7.0;$", cpp, re.M)
    assert checkpoint_members.count("state") == 1
    assert checkpoint_members.count(persistent) == 1
    compile_cpp(cpp, label=f"persistent-owner-nested-shadow-{udf_before_block}")
    assert _compile_and_run(cpp + _driver(["a", "b"])) == "3.0 100.0\n"


@pytest.mark.parametrize(
    "global_first", [False, True], ids=["block-first", "global-first"]
)
@pytest.mark.parametrize(
    ("global_decl", "nested_decl"),
    (
        pytest.param(
            "state = true",
            'state = "local"',
            id="bool-global-string-local",
        ),
        pytest.param(
            'state = "global"',
            "state = false",
            id="string-global-bool-local",
        ),
    ),
)
def test_cross_type_primitive_block_shadow_fails_closed(
    global_decl: str,
    nested_decl: str,
    global_first: bool,
) -> None:
    prefix = """//@version=6
strategy("cross-type primitive block shadow")
"""
    block = f"if true\n    {nested_decl}\n"
    if global_first:
        source = prefix + global_decl + "\n" + block
    else:
        source = prefix + block + global_decl + "\n"
    source += "read() => state\nvalue = read()\n"

    with pytest.raises(
        CompileError,
        match="declaration-exact block storage",
    ):
        transpile(source)


@pytest.mark.parametrize(
    "global_first", [False, True], ids=["block-first", "global-first"]
)
@pytest.mark.parametrize(
    "block",
    (
        pytest.param(
            """if true
    state = array.from(7.0)
    local_seen := state.size()
""",
            id="aggregate",
        ),
        pytest.param(
            """if true
    state = 7.0
    prior = state[1]
    local_seen := state
""",
            id="exact-history",
        ),
    ),
)
def test_stateful_block_shadow_of_direct_global_fails_closed(
    block: str,
    global_first: bool,
) -> None:
    prefix = """//@version=6
strategy("stateful block shadow")
local_seen = 0.0
"""
    global_decl = "state = 100.0\n"
    source = prefix + (global_decl + block if global_first else block + global_decl)
    source += "read() => state\nvalue = read()\n"

    with pytest.raises(
        CompileError,
        match="declaration-exact block storage",
    ):
        transpile(source)


def test_block_history_local_and_qualified_owners_fail_closed() -> None:
    source = """//@version=6
strategy("block history versus qualified owners")
observed = 0.0
if true
    state = 7.0
    prior = state[1]
    observed := nz(prior)
left() =>
    var float state = 1.0
    prior = state[1]
    state += 1.0
    state
right() =>
    var float state = 100.0
    state += 10.0
    state
a = left()
b = right()
"""

    with pytest.raises(
        CompileError,
        match="declaration-exact block storage",
    ):
        transpile(source)


def test_embedded_block_history_and_qualified_owners_fail_closed() -> None:
    source = """//@version=6
strategy("embedded block history versus qualified owners")
observed = if true
    state = 7.0
    prior = state[1]
    nz(prior)
else
    0.0
left() =>
    var float state = 1.0
    state += 1.0
    state
right() =>
    var float state = 100.0
    state += 10.0
    state
a = left()
b = right()
"""

    with pytest.raises(
        CompileError,
        match="declaration-exact block storage",
    ):
        transpile(source)


@pytest.mark.parametrize(
    "true_branch",
    (
        pytest.param(
            """    state = "local"
    1.0
""",
            id="cross-type",
        ),
        pytest.param(
            """    state = 7.0
    prior = state[1]
    nz(prior)
""",
            id="exact-history",
        ),
    ),
)
def test_stateful_embedded_if_shadow_of_direct_global_fails_closed(
    true_branch: str,
) -> None:
    source = (
        '//@version=6\nstrategy("embedded if block shadow")\n'
        "state = 100.0\n"
        "result = if true\n"
        f"{true_branch}"
        "else\n"
        "    0.0\n"
    )

    with pytest.raises(
        CompileError,
        match="declaration-exact block storage",
    ):
        transpile(source)


def test_cross_type_for_shadow_of_direct_global_fails_closed() -> None:
    source = """//@version=6
strategy("cross-type for block shadow")
state = 100.0
for k = 0 to 0
    state = "local"
read() => state
observed = read()
"""

    with pytest.raises(
        CompileError,
        match="declaration-exact block storage",
    ):
        transpile(source)


def test_numeric_for_shadow_keeps_lexical_local_and_direct_global() -> None:
    source = """//@version=6
strategy("numeric for block shadow")
state = 100.0
local_seen = 0.0
for k = 0 to 0
    state = 7
    local_seen := state
global_seen = state
"""
    cpp = transpile(source)

    assert "    double state = 0.0;" in cpp
    assert re.search(r"^\s{12}double state = 7;$", cpp, re.M)
    compile_cpp(cpp, label="persistent-owner-numeric-for-shadow")
    assert (
        _compile_and_run(cpp + _driver(["local_seen", "global_seen"], bars=1))
        == "7.0 100.0\n"
    )


def test_callable_numeric_for_history_binder_global_collision_fails_closed() -> None:
    source = """//@version=6
strategy("callable numeric for history binder")
state = 5.0
hist() =>
    result = 0.0
    for state = 0 to 1
        result += nz(state[1])
    result
observed = hist()
"""

    with pytest.raises(
        CompileError,
        match="declaration-exact callable storage",
    ):
        transpile(source)


def test_callable_for_in_history_binder_global_collision_fails_closed() -> None:
    source = """//@version=6
strategy("callable for-in history binder")
state = 5.0
hist() =>
    values = array.from(1.0, 2.0)
    result = 0.0
    for state in values
        result += nz(state[1])
    result
observed = hist()
"""

    with pytest.raises(
        CompileError,
        match="declaration-exact callable storage",
    ):
        transpile(source)


def test_block_local_scalar_before_history_udf_remains_lexical() -> None:
    source = """//@version=6
strategy("block scalar versus persistent history")
observed = 0.0
if true
    state = 7.0
    observed := state
left() =>
    var float state = 1.0
    prior = state[1]
    state += 1.0
    state
a = left()
"""
    cpp = transpile(source)

    assert "_pfv_" not in cpp
    assert "    Series<double> state;" in cpp
    assert re.search(r"^\s{12}double state = 7.0;$", cpp, re.M)
    compile_cpp(cpp, label="persistent-owner-block-local-before-history-udf")
    assert _compile_and_run(cpp + _driver(["a", "observed"])) == "3.0 7.0\n"


def test_authored_helper_and_clone_tokens_are_never_reused() -> None:
    cpp = transpile("""//@version=6
strategy("persistent owner helper token safety")
left(float _pfv_1_state__left__ni1) =>
    var float state = 1.0
    state += _pfv_1_state__left__ni1
    state
right(float _pfv_3_state__right_cs1) =>
    var float state = 100.0
    state += _pfv_3_state__right_cs1
    state
a = left(1.0)
b = right(10.0)
c = left(2.0)
d = right(20.0)
""")

    left = _qualified_member(cpp, "double", "state", "left")
    right = _qualified_member(cpp, "double", "state", "right")
    assert left == "_pfv_2_state__left"
    assert right == "_pfv_4_state__right"
    for name in (left, right, f"{left}_cs1", f"{right}_cs1"):
        assert re.search(rf"^    double {name} = na<double>\(\);$", cpp, re.M)
    assert not re.search(r"^    double _pfv_1_state__left;$", cpp, re.M)
    assert not re.search(r"^    double _pfv_3_state__right;$", cpp, re.M)
    compile_cpp(cpp, label="persistent-owner-authored-helper-tokens")


def test_global_and_udt_method_storage_stay_on_existing_paths() -> None:
    cpp = transpile("""//@version=6
strategy("persistent owner scope boundary")
type Holder
    int seed
method read_float(Holder self) =>
    var int methodState = 7
    methodState += self.seed
    methodState
left() =>
    var int state = 1
    state += 1
    state
right() =>
    var int state = 10
    state += 2
    state
var int globalState = 42
var Holder holder = Holder.new(3)
a = left()
b = right()
c = holder.read_float()
    """)

    assert "    int globalState;" in cpp
    assert "    int methodState = na<int>();" in cpp
    assert not re.search(r"_pfv_[0-9]+_globalState__", cpp)
    assert not re.search(r"_pfv_[0-9]+_methodState__", cpp)
    _qualified_member(cpp, "int", "state", "left")
    _qualified_member(cpp, "int", "state", "right")
    compile_cpp(cpp, label="persistent-owner-scope-boundary")


def test_cross_callable_persistent_drawing_collision_remains_fail_closed() -> None:
    source = """//@version=6
strategy("persistent drawing boundary")
make_line() =>
    var line state = line.new(bar_index, close, bar_index + 1, close)
    state
make_label() =>
    var label state = label.new(bar_index, close, "label")
    state
a = make_line()
b = make_label()
"""
    with pytest.raises(
        CompileError,
        match="Persistent drawing bindings named 'state'",
    ):
        transpile(source)
