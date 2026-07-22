"""UDT-method state follows Pine's written-callsite lifecycle."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests.test_runtime_var_initialization import _compile_and_run


def _driver(outputs: list[str], *, split_ohlc: bool = False) -> str:
    streamed = ' << " " << '.join(
        f"strategy.{output}" for output in outputs
    )
    if split_ohlc:
        bars = """
        Bar{1.0, 15.0, 1.0, 15.0, 1.0, 0},
        Bar{2.0, 25.0, 2.0, 25.0, 1.0, 60000},
        Bar{3.0, 35.0, 3.0, 35.0, 1.0, 120000},
"""
    else:
        bars = """
        Bar{15.0, 15.0, 15.0, 15.0, 1.0, 0},
        Bar{25.0, 25.0, 25.0, 25.0, 1.0, 60000},
        Bar{35.0, 35.0, 35.0, 35.0, 1.0, 120000},
"""
    return f"""
#include <iomanip>
#include <iostream>
int main() {{
    Bar bars[] = {{{bars}    }};
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    std::cout << std::fixed << std::setprecision(1)
              << {streamed} << "\\n";
    return 0;
}}
"""


def _persistent_method_source(*, nested: bool, written_sites: int) -> str:
    body = (
        """method sample(Holder self) =>
    float observed = na
    if true
        var float state = close
        observed := state
    observed
"""
        if nested
        else """method sample(Holder self) =>
    var float state = close
    state
"""
    )
    calls = "first = time >= 60000 ? holder.sample() : na\n"
    if written_sites == 2:
        calls += "second = time >= 120000 ? holder.sample() : na\n"
    return (
        """//@version=6
strategy("method written callsite lifecycle")
type Holder
    float seed
"""
        + body
        + "var Holder holder = Holder.new(0.0)\n"
        + calls
    )


@pytest.mark.parametrize("nested", [False, True], ids=["direct", "nested"])
@pytest.mark.parametrize("written_sites", [1, 2], ids=["one-site", "two-sites"])
def test_method_var_written_callsite_matrix(
    nested: bool, written_sites: int
) -> None:
    cpp = transpile(
        _persistent_method_source(nested=nested, written_sites=written_sites)
    )

    assert "double _udt_Holder_sample_cs0(" in cpp
    assert "_fvinit__udt_Holder_sample_cs0" in cpp
    assert "_udt_Holder_sample_cs0(holder)" in cpp
    if written_sites == 2:
        assert "double _udt_Holder_sample_cs1(" in cpp
        assert "_fvinit__udt_Holder_sample_cs1" in cpp
        assert "_udt_Holder_sample_cs1(holder)" in cpp
        outputs = ["first", "second"]
        expected = "25.0 35.0\n"
    else:
        assert "_udt_Holder_sample_cs1" not in cpp
        outputs = ["first"]
        expected = "25.0\n"
    assert _compile_and_run(cpp + _driver(outputs)) == expected


def test_method_state_propagates_through_udf_wrapper_paths() -> None:
    source = """//@version=6
strategy("method wrapper written callsite lifecycle")
type Holder
    float seed
var Holder holder = Holder.new(0.0)
method sample(Holder self) =>
    var float state = close
    state
wrapped() => holder.sample()
first = time >= 60000 ? wrapped() : na
second = time >= 120000 ? wrapped() : na
"""
    cpp = transpile(source)

    assert "double wrapped_cs0()" in cpp
    assert "double wrapped_cs1()" in cpp
    assert "return _udt_Holder_sample_cs0(holder);" in cpp
    assert "return _udt_Holder_sample_cs1(holder);" in cpp
    assert _compile_and_run(cpp + _driver(["first", "second"])) == (
        "25.0 35.0\n"
    )


def test_wrapper_udt_parameter_shadows_same_named_global_for_callsite_state() -> None:
    source = """//@version=6
strategy("lexical method receiver")
type A
    float pad
type B
    float pad
method sample(B self) =>
    var float state = close
    state
var A obj = A.new(0.0)
wrapped(B obj) => obj.sample()
var B b = B.new(0.0)
first = time >= 60000 ? wrapped(b) : na
second = time >= 120000 ? wrapped(b) : na
"""
    cpp = transpile(source)

    assert "double wrapped_cs0(B obj)" in cpp
    assert "double wrapped_cs1(B obj)" in cpp
    assert "return _udt_B_sample_cs0(obj);" in cpp
    assert "return _udt_B_sample_cs1(obj);" in cpp
    assert _compile_and_run(cpp + _driver(["first", "second"])) == (
        "25.0 35.0\n"
    )


def test_equal_udt_ternary_receiver_keeps_written_callsite_state() -> None:
    source = """//@version=6
strategy("ternary method receiver")
type Holder
    float seed
method sample(Holder self) =>
    var float state = close
    state
var Holder left = Holder.new(0.0)
var Holder right = Holder.new(1.0)
first = time >= 60000 ? (close > open ? left : right).sample() : na
second = time >= 120000 ? (close < open ? left : right).sample() : na
"""
    cpp = transpile(source)

    assert "double _udt_Holder_sample_cs0(" in cpp
    assert "double _udt_Holder_sample_cs1(" in cpp
    assert _compile_and_run(cpp + _driver(["first", "second"])) == (
        "25.0 35.0\n"
    )


def test_method_var_ta_history_and_fixnan_clone_together() -> None:
    source = """//@version=6
strategy("method state clone panel")
type Holder
    float seed
method sample(Holder self, float src) =>
    var float state = src
    avg = ta.sma(src, 2)
    fixed = fixnan(src)
    prev = src[1]
    state + avg + fixed + prev
var Holder holder = Holder.new(0.0)
first = holder.sample(close)
second = holder.sample(open)
"""
    cpp = transpile(source)

    assert "ta::SMA _ta_sma_1;" in cpp
    assert "ta::SMA _ta_sma_1_cs1;" in cpp
    assert "double _prev_fixnan_1 = na<double>();" in cpp
    assert "double _prev_fixnan_1_cs1 = na<double>();" in cpp
    assert (
        "double _udt_Holder_sample_cs0(Holder self, "
        "const Series<double>& src)" in cpp
    )
    assert (
        "double _udt_Holder_sample_cs1(Holder self, "
        "const Series<double>& src)" in cpp
    )
    assert "first = _udt_Holder_sample_cs0(holder, _s_close);" in cpp
    assert "second = _udt_Holder_sample_cs1(holder, _s_open);" in cpp
    assert _compile_and_run(
        cpp + _driver(["first", "second"], split_ohlc=True)
    ) == "105.0 8.5\n"


def test_method_series_requirement_propagates_through_udf_wrapper_chain() -> None:
    source = """//@version=6
strategy("method series wrapper propagation")
type Holder
    float seed
method sample(Holder self, float src, bool active) =>
    float observed = na
    if active
        var float state = src
        float avg = ta.sma(src, 2)
        float fixed = fixnan(src)
        float prev = src[1]
        observed := state + avg + fixed + prev
    observed
var Holder holder = Holder.new(0.0)
inner(float src, bool active) => holder.sample(src, active)
outer(float src, bool active) => inner(src, active)
first = outer(close, true)
second = outer(open, true)
"""
    cpp = transpile(source)

    for name in ("inner_cs0", "inner_cs1", "outer_cs0", "outer_cs1"):
        assert f"double {name}(const Series<double>& src, bool active)" in cpp
    assert (
        "double _udt_Holder_sample_cs0(Holder self, "
        "const Series<double>& src, bool active)" in cpp
    )
    assert (
        "double _udt_Holder_sample_cs1(Holder self, "
        "const Series<double>& src, bool active)" in cpp
    )
    assert _compile_and_run(
        cpp + _driver(["first", "second"], split_ohlc=True)
    ) == "105.0 8.5\n"


def test_method_series_requirement_promotes_wrapper_parameter() -> None:
    source = """//@version=6
strategy("method series wrapper")
type Holder
    float seed
method sample(Holder self, float src, bool active) =>
    var float state = src
    avg = ta.sma(src, 2)
    fixed = fixnan(src)
    prev = src[1]
    active ? state + avg + fixed + prev : na
var Holder holder = Holder.new(0.0)
wrapped(float src, bool active) => holder.sample(src, active)
first = wrapped(close, true)
"""
    cpp = transpile(source)

    assert "double wrapped_cs0(const Series<double>& src, bool active)" in cpp
    assert "return _udt_Holder_sample_cs0(holder, src, active);" in cpp
    assert "first = wrapped_cs0(_s_close, true);" in cpp
    assert _compile_and_run(
        cpp + _driver(["first"], split_ohlc=True)
    ) == "105.0\n"


def test_method_series_keyword_compound_actual_uses_local_bridge() -> None:
    source = """//@version=6
strategy("method keyword compound wrapper")
type Holder
    float seed
method sample(Holder self, float src, bool active) =>
    var float state = src
    avg = ta.sma(src, 2)
    fixed = fixnan(src)
    prev = src[1]
    active ? state + avg + fixed + prev : na
var Holder holder = Holder.new(0.0)
wrapped(float src) => holder.sample(active = true, src = src + 1.0)
first = wrapped(close)
"""
    cpp = transpile(source)

    # A compound actual needs its own history bridge inside the wrapper; the
    # scalar wrapper parameter itself must not be over-promoted to Series.
    assert "double wrapped_cs0(double src)" in cpp
    assert "Series<double> _series_arg_1;" in cpp
    assert "_udt_Holder_sample_cs0(holder, ([&]() -> const Series<double>&" in cpp
    assert _compile_and_run(
        cpp + _driver(["first"], split_ohlc=True)
    ) == "109.0\n"


def test_method_conditional_ta_preserves_delayed_var_first_reach() -> None:
    source = '''//@version=6
strategy("method conditional TA lifecycle")
type Holder
    float seed
method sample(Holder self, float src, bool active) =>
    float observed = na
    if active
        var float state = src
        float avg = ta.sma(src, 2)
        float fixed = fixnan(src)
        float prev = src[1]
        observed := state + avg + fixed + prev
    observed
var Holder holder = Holder.new(0.0)
first = holder.sample(close, bar_index >= 1)
'''
    cpp = transpile(source)
    body = cpp[
        cpp.index("double _udt_Holder_sample_cs0("):cpp.index("    void on_bar(")
    ]
    assert body.index("if (active) {") < body.index(
        "if (!this->_pf_var_init_state)"
    )
    assert _compile_and_run(
        cpp + _driver(["first"], split_ohlc=True)
    ) == "115.0\n"


def test_history_series_requirement_flows_from_udf_into_calling_method() -> None:
    source = '''//@version=6
strategy("method calls history UDF")
history(float src) => src[1]
type Holder
    float seed
method sample(Holder self, float src) => history(src)
var Holder holder = Holder.new(0.0)
first = holder.sample(close)
second = holder.sample(open)
'''
    cpp = transpile(source)
    assert (
        "double _udt_Holder_sample_cs0(Holder self, "
        "const Series<double>& src)" in cpp
    )
    assert "return history_cs0(src);" in cpp
    assert "return history_cs1(src);" in cpp
    assert _compile_and_run(
        cpp + _driver(["first", "second"], split_ohlc=True)
    ) == "25.0 2.0\n"


def test_history_series_requirement_flows_between_sibling_methods() -> None:
    source = '''//@version=6
strategy("sibling method history flow")
type Holder
    float seed
method inner(Holder self, float src) => src[1]
method outer(Holder self, float src) => self.inner(src)
var Holder holder = Holder.new(0.0)
first = holder.outer(close)
second = holder.outer(open)
'''
    cpp = transpile(source)
    for index in (0, 1):
        assert (
            f"double _udt_Holder_outer_cs{index}(Holder self, "
            "const Series<double>& src)" in cpp
        )
        assert f"_udt_Holder_inner_cs{index}(self, src)" in cpp
    assert _compile_and_run(
        cpp + _driver(["first", "second"], split_ohlc=True)
    ) == "25.0 2.0\n"


def test_compound_history_argument_flows_between_sibling_methods() -> None:
    source = '''//@version=6
strategy("sibling method compound history bridge")
type Holder
    float seed
method inner(Holder self, float src) => src[1]
method outer(Holder self, float src) => self.inner(src + 1.0)
var Holder holder = Holder.new(0.0)
first = holder.outer(close)
second = holder.outer(open)
'''
    cpp = transpile(source)
    assert "Series<double> _series_arg_" in cpp
    assert _compile_and_run(
        cpp + _driver(["first", "second"], split_ohlc=True)
    ) == "26.0 3.0\n"


def test_compound_history_argument_uses_wrapper_udt_parameter_type() -> None:
    source = '''//@version=6
strategy("wrapper method compound history bridge")
type Holder
    float seed
method inner(Holder self, float src) => src[1]
wrapped(Holder obj, float src) => obj.inner(src + 1.0)
var Holder holder = Holder.new(0.0)
first = wrapped(holder, close)
second = wrapped(holder, open)
'''
    cpp = transpile(source)
    assert "Series<double> _series_arg_" in cpp
    assert _compile_and_run(
        cpp + _driver(["first", "second"], split_ohlc=True)
    ) == "26.0 3.0\n"


def test_compound_method_history_bridge_wins_over_same_named_udf() -> None:
    source = '''//@version=6
strategy("method history bridge name collision")
inner(float src) => src + 100.0
type Holder
    float seed
method inner(Holder self, float src) => src[1]
method outer(Holder self, float src) => self.inner(src + 1.0)
var Holder holder = Holder.new(0.0)
first = holder.outer(close)
second = holder.outer(open)
'''
    cpp = transpile(source)
    assert "Series<double> _series_arg_" in cpp
    assert "_udt_Holder_inner_cs0(self," in cpp
    assert "return inner_cs0(" not in cpp
    assert _compile_and_run(
        cpp + _driver(["first", "second"], split_ohlc=True)
    ) == "26.0 3.0\n"


def test_method_to_ta_udf_keeps_scalar_parameter_control() -> None:
    source = '''//@version=6
strategy("method calls TA UDF scalar control")
smooth(float src) => ta.sma(src, 2)
type Holder
    float seed
method sample(Holder self, float src) => smooth(src)
var Holder holder = Holder.new(0.0)
first = holder.sample(close)
second = holder.sample(open)
'''
    cpp = transpile(source)
    assert "_udt_Holder_sample_cs0(Holder self, double src)" in cpp
    assert _compile_and_run(
        cpp + _driver(["first", "second"], split_ohlc=True)
    ) == "30.0 2.5\n"


def test_stateless_method_does_not_gain_clone_state() -> None:
    source = """//@version=6
strategy("pure method remains single")
type Holder
    float seed
method pure(Holder self, float value) => value + self.seed
var Holder holder = Holder.new(2.0)
first = holder.pure(close)
second = holder.pure(open)
"""
    cpp = transpile(source)

    assert cpp.count("double _udt_Holder_pure(") == 1
    assert "_udt_Holder_pure_cs" not in cpp
    assert "_fvinit__udt_Holder_pure" not in cpp
