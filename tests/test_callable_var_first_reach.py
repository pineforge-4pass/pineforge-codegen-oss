"""Runtime coverage for callable-local ``var`` declaration lifecycles.

Pine v6 initializes a ``var`` declaration when execution first reaches that
declaration. Entering its containing UDF is insufficient when the declaration
lives under conditional control flow. Each written UDF call site owns an
independent persistent instance.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests.test_runtime_var_initialization import _compile_and_run


def _matrix_source(storage: str, delayed: bool, callsites: int) -> str:
    if storage == "scalar":
        declaration = "var float state = close"
        read = "state"
    else:
        declaration = "var array<float> state = array.from(close)"
        read = "state.get(0)"
    if delayed:
        conditions = ["bar_index >= 1", "bar_index >= 2"]
    else:
        conditions = ["true", "true"]
    calls = [f"value = probe({conditions[0]})"]
    if callsites == 2:
        calls = [
            f"left_value = probe({conditions[0]})",
            f"right_value = probe({conditions[1]})",
        ]
    return (
        "//@version=6\n"
        'strategy("callable var first reach matrix")\n'
        "probe(bool active) =>\n"
        "    float observed = 0.0\n"
        "    if active\n"
        f"        {declaration}\n"
        f"        observed := {read}\n"
        "    observed\n"
        + "\n".join(calls)
        + "\n"
    )


def _matrix_driver(callsites: int) -> str:
    output = (
        "strategy.value"
        if callsites == 1
        else 'strategy.left_value << " " << strategy.right_value'
    )
    return f'''\n#include <iostream>\nint main() {{\n    Bar bars[] = {{\n        Bar{{10.0, 11.0, 9.0, 10.0, 1.0, 1000}},\n        Bar{{20.0, 21.0, 19.0, 20.0, 1.0, 61000}},\n        Bar{{30.0, 31.0, 29.0, 30.0, 1.0, 121000}},\n    }};\n    GeneratedStrategy strategy;\n    strategy.run(bars, 3);\n    if (!strategy.last_error().empty()) return 7;\n    std::cout << {output} << "\\n";\n}}\n'''


@pytest.mark.parametrize("storage", ["scalar", "array"])
@pytest.mark.parametrize("delayed", [False, True], ids=["eager", "delayed"])
@pytest.mark.parametrize("callsites", [1, 2], ids=["one-site", "two-sites"])
def test_callable_var_first_reach_exhaustive_matrix(
    storage: str, delayed: bool, callsites: int
) -> None:
    """All 2^3 cells run; no first passing combination short-circuits this gate."""
    cpp = transpile(_matrix_source(storage, delayed, callsites))
    observed = tuple(
        float(value)
        for value in _compile_and_run(cpp + _matrix_driver(callsites)).split()
    )
    if not delayed:
        expected = (10.0,) * callsites
    elif callsites == 1:
        expected = (20.0,)
    else:
        expected = (20.0, 30.0)
    assert observed == expected


def test_conditional_initializer_side_effect_occurs_at_first_reach() -> None:
    source = '''//@version=6
strategy("callable var initializer side effect")
var array<int> initializer_bars = array.new<int>()
seed() =>
    initializer_bars.push(bar_index)
    close
probe(bool active) =>
    float observed = -1.0
    if active
        var float state = seed()
        observed := state
    observed
value = probe(bar_index >= 2)
side_count = initializer_bars.size()
side_first = side_count > 0 ? initializer_bars.get(0) : -1
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{10.0, 11.0, 9.0, 10.0, 1.0, 1000},
        Bar{20.0, 21.0, 19.0, 20.0, 1.0, 61000},
        Bar{30.0, 31.0, 29.0, 30.0, 1.0, 121000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    std::cout << strategy.value << " " << strategy.side_count << " "
              << strategy.side_first << "\n";
}
'''
    assert _compile_and_run(transpile(source) + driver) == "30 1 2\n"


def test_conditional_persistent_map_uses_exact_callsite_storage() -> None:
    source = '''//@version=6
strategy("callable map first reach")
make(int seed) =>
    map<string, int> value = map.new<string, int>()
    value.put("seed", seed)
    value
probe(bool active) =>
    int observed = -1
    if active
        var map<string, int> state = make(bar_index)
        observed := state.get("seed")
    observed
left_value = probe(bar_index >= 1)
right_value = probe(bar_index >= 2)
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{10.0, 11.0, 9.0, 10.0, 1.0, 1000},
        Bar{20.0, 21.0, 19.0, 20.0, 1.0, 61000},
        Bar{30.0, 31.0, 29.0, 30.0, 1.0, 121000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    std::cout << strategy.left_value << " " << strategy.right_value << "\n";
}
'''
    cpp = transpile(source)
    assert "if (!this->_pf_var_init_state)" in cpp
    assert "if (!this->_pf_var_init_state_cs1)" in cpp
    assert "}((state_cs1))" in cpp
    assert _compile_and_run(cpp + driver) == "1 2\n"


@pytest.mark.parametrize("storage", ["udt", "matrix"])
@pytest.mark.parametrize("callsites", [1, 2], ids=["one-site", "two-sites"])
def test_conditional_udt_and_matrix_initialize_at_first_reach(
    storage: str, callsites: int
) -> None:
    if storage == "udt":
        preamble = "type Point\n    float value\n"
        declaration = "var Point state = Point.new(close)"
        read = "state.value"
    else:
        preamble = ""
        declaration = "var matrix<float> state = matrix.new<float>(1, 1, close)"
        read = "state.get(0, 0)"
    calls = ["value = probe(bar_index >= 1)"]
    if callsites == 2:
        calls = [
            "left_value = probe(bar_index >= 1)",
            "right_value = probe(bar_index >= 2)",
        ]
    source = (
        "//@version=6\n"
        'strategy("callable aggregate first reach")\n'
        + preamble
        + "probe(bool active) =>\n"
        "    float observed = 0.0\n"
        "    if active\n"
        f"        {declaration}\n"
        f"        observed := {read}\n"
        "    observed\n"
        + "\n".join(calls)
        + "\n"
    )
    observed = tuple(
        float(value)
        for value in _compile_and_run(
            transpile(source) + _matrix_driver(callsites)
        ).split()
    )
    assert observed == ((20.0,) if callsites == 1 else (20.0, 30.0))


def test_direct_callable_var_runs_after_preceding_side_effect() -> None:
    source = '''//@version=6
strategy("direct callable var source order")
var array<float> seen = array.new<float>()
probe() =>
    seen.push(close)
    var float first_size = seen.size()
    first_size
value = probe()
side_count = seen.size()
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{10.0, 11.0, 9.0, 10.0, 1.0, 1000},
        Bar{20.0, 21.0, 19.0, 20.0, 1.0, 61000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 2);
    std::cout << strategy.value << " " << strategy.side_count << "\n";
}
'''
    assert _compile_and_run(transpile(source) + driver) == "1 2\n"


@pytest.mark.parametrize("declaration_kind", ["var", "varip"])
def test_direct_callable_var_and_internal_varip_lowering_read_preceding_local(
    declaration_kind: str,
) -> None:
    source = f'''//@version=6
strategy("direct callable var reads preceding local")
probe() =>
    float seed = close * 2.0
    {declaration_kind} float first = seed
    first
value = probe()
'''
    # Public batch backtests intentionally reject varip because they have no
    # realtime ticks. Bypass only that support gate to pin the shared historical
    # declaration-lowering predicate; this does not claim realtime varip parity.
    cpp = transpile(source, check_support=declaration_kind == "var")
    body = cpp[cpp.index("double probe_cs0("):]
    assert body.index("double seed =") < body.index("if (!this->_pf_var_init_first)")
    assert _compile_and_run(cpp + _matrix_driver(1)) == "20\n"


@pytest.mark.parametrize("declaration_kind", ["var", "varip"])
def test_nested_callable_var_and_varip_initialize_on_delayed_first_reach(
    declaration_kind: str,
) -> None:
    source = f'''//@version=6
strategy("nested callable varip first reach")
probe(bool active) =>
    float observed = -1.0
    if active
        {declaration_kind} float state = close
        observed := state
    observed
left_value = probe(bar_index >= 1)
right_value = probe(bar_index >= 2)
'''
    cpp = transpile(source, check_support=declaration_kind == "var")
    assert _compile_and_run(cpp + _matrix_driver(2)) == "20 30\n"


def test_loop_scoped_callable_var_initializes_on_first_iteration_per_callsite() -> None:
    source = '''//@version=6
strategy("loop scoped callable var first reach")
probe(float bias) =>
    float observed = -1.0
    for i = 0 to 2
        var float first = close + i + bias
        observed := first
    observed
left_value = probe(0.0)
right_value = probe(100.0)
'''
    cpp = transpile(source)
    assert "for (int i = _for_start_" in cpp
    assert cpp.index("for (int i = _for_start_") < cpp.index(
        "if (!this->_pf_var_init_first)"
    )
    assert _compile_and_run(cpp + _matrix_driver(2)) == "10 110\n"


@pytest.mark.parametrize("storage", ["scalar", "array", "map", "matrix", "udt"])
def test_direct_callable_var_source_order_isolated_per_written_callsite(
    storage: str,
) -> None:
    preamble = ""
    if storage == "scalar":
        declaration = "var float state = seed"
        read = "state"
    elif storage == "array":
        declaration = "var array<float> state = array.from(seed)"
        read = "state.get(0)"
    elif storage == "map":
        preamble = '''build_state(float seed) =>
    map<string, float> result = map.new<string, float>()
    result.put("seed", seed)
    result
'''
        declaration = "var map<string, float> state = build_state(seed)"
        read = 'state.get("seed")'
    elif storage == "matrix":
        declaration = "var matrix<float> state = matrix.new<float>(1, 1, seed)"
        read = "state.get(0, 0)"
    else:
        preamble = "type Point\n    float value\n"
        declaration = "var Point state = Point.new(seed)"
        read = "state.value"
    source = (
        "//@version=6\n"
        'strategy("direct callable var callsite matrix")\n'
        + preamble
        + "probe(float bias) =>\n"
        "    float seed = close + bias\n"
        f"    {declaration}\n"
        f"    {read}\n"
        "left_value = probe(1.0)\n"
        "right_value = probe(100.0)\n"
    )
    assert _compile_and_run(transpile(source) + _matrix_driver(2)) == "11 110\n"


def test_callable_once_flags_reserve_authored_callsite_clone_names() -> None:
    source = '''//@version=6
strategy("callable once flag collision")
probe(bool active) =>
    var float _pf_var_init_state = 100.0
    float observed = -1.0
    if active
        var float state = close
        observed := state
    observed + _pf_var_init_state
left_value = probe(bar_index >= 1)
right_value = probe(bar_index >= 2)
'''
    cpp = transpile(source)
    assert "double _pf_var_init_state_cs1 = na<double>();" in cpp
    assert "bool _pf_var_init_state_2 = false;" in cpp
    assert "bool _pf_var_init_state_cs1_2 = false;" in cpp
    assert cpp.count(" _pf_var_init_state_cs1 =") == 1
    assert _compile_and_run(cpp + _matrix_driver(2)) == "120 130\n"


def test_unreached_callable_primitive_base_and_clone_remain_na() -> None:
    source = '''//@version=6
strategy("unreached callable primitive state")
probe(bool active) =>
    if active
        var float state = close
    0.0
left_value = probe(false)
right_value = probe(false)
'''
    cpp = transpile(source)
    assert "double state = na<double>();" in cpp
    assert "double state_cs1 = na<double>();" in cpp
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{10.0, 11.0, 9.0, 10.0, 1.0, 1000},
        Bar{20.0, 21.0, 19.0, 20.0, 1.0, 61000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 2);
    std::cout << is_na(strategy.state) << " "
              << is_na(strategy.state_cs1) << "\n";
}
'''
    assert _compile_and_run(cpp + driver) == "1 1\n"


def test_callable_conditional_ta_keeps_lifecycle_and_side_effects_in_branch() -> None:
    source = '''//@version=6
strategy("conditional TA callable lifecycle")
var array<int> reached = array.new<int>()
probe(float src, bool active) =>
    float observed = na
    if active
        reached.push(bar_index)
        var float state = src
        float avg = ta.sma(src, 2)
        float fixed = fixnan(src)
        float prev = src[1]
        observed := state + avg + fixed + prev
    observed
value = probe(close, bar_index >= 1)
reach_count = reached.size()
first_reach = reach_count > 0 ? reached.get(0) : -1
'''
    cpp = transpile(source)
    body = cpp[cpp.index("double probe_cs0("):cpp.index("    void on_bar(")]
    assert body.index("if (active) {") < body.index(
        "if (!this->_pf_var_init_state)"
    )
    assert body.index("if (active) {") < body.index("reached.push")
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{10.0, 11.0, 9.0, 10.0, 1.0, 1000},
        Bar{20.0, 21.0, 19.0, 20.0, 1.0, 61000},
        Bar{30.0, 31.0, 29.0, 30.0, 1.0, 121000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    std::cout << strategy.value << " " << strategy.reach_count << " "
              << strategy.first_reach << "\n";
}
'''
    assert _compile_and_run(cpp + driver) == "95 2 1\n"


def test_callable_conditional_ta_history_advances_only_on_executed_branch() -> None:
    source = '''//@version=6
strategy("conditional TA local history")
probe(float src, bool active) =>
    float observed = na
    if active
        var float state = src
        float avg = ta.sma(src, 2)
        float fixed = fixnan(src)
        float prev = src[1]
        observed := state + avg + fixed + prev
    observed
value = probe(close, bar_index != 1)
'''
    cpp = transpile(source)
    body = cpp[cpp.index("double probe_cs0("):cpp.index("    void on_bar(")]
    assert "if (active) {" in body
    assert "_hoist_" not in body
    # The conditional SMA sees bars 0 and 2, so its final value is 20. Pine
    # warns about this lazy local history; it does not rewrite the call to run
    # on the skipped bar 1. Eager TA advancement would incorrectly yield 85.
    assert _compile_and_run(cpp + _matrix_driver(1)) == "80\n"


@pytest.mark.parametrize("storage", ["scalar", "array"])
@pytest.mark.parametrize("callsites", [1, 2], ids=["one-site", "two-sites"])
def test_udt_method_persistent_state_isolated_per_written_callsite(
    storage: str, callsites: int
) -> None:
    if storage == "scalar":
        declaration = "var float state = close"
        read = "state"
    else:
        declaration = "var array<float> state = array.from(close)"
        read = "state.get(0)"
    calls = ["value = holder.probe(bar_index >= 1)"]
    if callsites == 2:
        calls = [
            "left_value = holder.probe(bar_index >= 1)",
            "right_value = holder.probe(bar_index >= 2)",
        ]
    source = (
        "//@version=6\n"
        'strategy("method written callsite state")\n'
        "type Holder\n"
        "    float pad\n"
        "method probe(Holder self, bool active) =>\n"
        "    float observed = -1.0\n"
        "    if active\n"
        f"        {declaration}\n"
        f"        observed := {read}\n"
        "    observed\n"
        "var Holder holder = Holder.new(0.0)\n"
        + "\n".join(calls)
        + "\n"
    )
    cpp = transpile(source)
    assert "_udt_Holder_probe_cs0" in cpp
    assert ("_udt_Holder_probe_cs1" in cpp) == (callsites == 2)
    expected = "20\n" if callsites == 1 else "20 30\n"
    assert _compile_and_run(cpp + _matrix_driver(callsites)) == expected


def test_stateless_method_and_udf_stay_on_uncloned_compile_paths() -> None:
    source = '''//@version=6
strategy("stateless callable controls")
type Holder
    float pad
method add(Holder self, float value) => value + self.pad
plus_one(float value) => value + 1.0
var Holder holder = Holder.new(2.0)
method_value = holder.add(close)
udf_value = plus_one(close)
'''
    cpp = transpile(source)
    assert "double _udt_Holder_add(" in cpp
    assert "_udt_Holder_add_cs" not in cpp
    assert "double plus_one(" in cpp
    assert "plus_one_cs" not in cpp
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {Bar{10.0, 11.0, 9.0, 10.0, 1.0, 1000}};
    GeneratedStrategy strategy;
    strategy.run(bars, 1);
    std::cout << strategy.method_value << " " << strategy.udf_value << "\n";
}
'''
    assert _compile_and_run(cpp + driver) == "12 11\n"
