"""Pine scalar families are preserved across callable history parameters.

The history operator does not erase the underlying Pine type.  In
particular, ``src[1]`` on an ``int`` or ``bool`` UDF parameter must not force
that parameter to ``Series<double>``.  The runtime panel deliberately crosses
plain UDFs, nested UDFs, UDT methods, arithmetic/ternary actuals, a timestamp
actual, and float controls so a compensating bridge bug cannot make a narrow
compile-only probe look green.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests.test_runtime_var_initialization import _compile_and_run


_SOURCE = '''//@version=6
strategy("typed series param runtime panel")
intHistory(int src) => src[1]
intOuter(int src) => intHistory(src)
boolHistory(bool src) => src[1]
floatHistory(float src) => src[1]
type Holder
    int seed
method intHistory(Holder self, int src) => src[1]
method intOuter(Holder self, int src) => self.intHistory(src)
var Holder holder = Holder.new(0)
flag = close > open
udf_direct = intHistory(bar_index)
udf_arithmetic = intHistory(bar_index + 1)
udf_ternary = intHistory(close > open ? bar_index : bar_index + 1)
udf_time = intHistory(time)
udf_nested = intOuter(bar_index)
method_direct = holder.intHistory(bar_index)
method_nested = holder.intOuter(bar_index)
bool_direct = boolHistory(flag)
bool_compound = boolHistory(close > open ? true : false)
float_control = floatHistory(close)
var bool int_first_missing = false
var bool time_first_missing = false
var bool bool_first_is_false = false
if bar_index == 0
    int_first_missing := na(udf_direct)
    time_first_missing := na(udf_time)
    bool_first_is_false := bool_direct == false
'''


_DRIVER = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{10.0, 12.0, 9.0, 11.0, 1.0, 1700000000000LL},
        Bar{20.0, 21.0, 18.0, 19.0, 1.0, 1700000060000LL},
        Bar{30.0, 32.0, 29.0, 31.0, 1.0, 1700000120000LL},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    std::cout << strategy.udf_direct << " "
              << strategy.udf_arithmetic << " "
              << strategy.udf_ternary << " "
              << strategy.udf_time << " "
              << strategy.udf_nested << " "
              << strategy.method_direct << " "
              << strategy.method_nested << " "
              << strategy.bool_direct << " "
              << strategy.bool_compound << " "
              << strategy.float_control << " "
              << strategy.int_first_missing << " "
              << strategy.time_first_missing << " "
              << strategy.bool_first_is_false << "\n";
}
'''


def test_callable_history_parameters_keep_their_pine_scalar_family() -> None:
    cpp = transpile(_SOURCE)

    assert "int64_t intHistory_cs0(const Series<int64_t>& src)" in cpp
    assert "int64_t intOuter_cs0(const Series<int64_t>& src)" in cpp
    assert (
        "int64_t _udt_Holder_intHistory_cs0(Holder self, "
        "const Series<int64_t>& src)"
    ) in cpp
    assert (
        "int64_t _udt_Holder_intOuter_cs0(Holder self, "
        "const Series<int64_t>& src)"
    ) in cpp
    assert "bool boolHistory_cs0(const Series<bool>& src)" in cpp
    assert "double floatHistory_cs0(const Series<double>& src)" in cpp

    # bar_index uses Series<int> at chart scope, so the int callable boundary
    # widens its call-site history. Epoch time already uses Series<int64_t> and
    # can bind directly; neither path is allowed to detour through double.
    assert "auto _pf_series_raw = (pine_bar_index())" in cpp
    assert "is_na(_pf_series_raw) ? na<int64_t>()" in cpp
    assert "udf_time = intHistory_cs4(time);" in cpp
    assert "intHistory_cs0(const Series<double>& src)" not in cpp
    assert "boolHistory_cs0(const Series<double>& src)" not in cpp


def test_callable_history_scalar_family_panel_runs_natively() -> None:
    cpp = transpile(_SOURCE)
    assert _compile_and_run(cpp + _DRIVER) == (
        "1 2 2 1700000060000 1 1 1 0 0 19 1 1 1\n"
    )


def test_nested_int_series_widens_into_float_history_natively() -> None:
    source = '''//@version=6
strategy("nested int to float history")
floatHistory(float src) => src[1]
intOuter(int src) => floatHistory(src)
type Holder
    int seed
method floatHistory(Holder self, float src) => src[1]
method intOuter(Holder self, int src) => self.floatHistory(src)
var Holder holder = Holder.new(0)
widened = intOuter(time)
methodWidened = holder.intOuter(time)
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 1.0, 1.0, 1.0, 1.0, 1700000000000LL},
        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 1700000060000LL},
        Bar{3.0, 3.0, 3.0, 3.0, 1.0, 1700000120000LL},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    std::cout << static_cast<long long>(strategy.widened) << " "
              << static_cast<long long>(strategy.methodWidened) << "\n";
}
'''
    cpp = transpile(source)
    assert "double floatHistory_cs0(const Series<double>& src)" in cpp
    assert "double intOuter_cs0(const Series<int64_t>& src)" in cpp
    assert (
        "double _udt_Holder_floatHistory_cs0(Holder self, "
        "const Series<double>& src)"
    ) in cpp
    assert (
        "double _udt_Holder_intOuter_cs0(Holder self, "
        "const Series<int64_t>& src)"
    ) in cpp
    assert "([&]() -> const Series<double>&" in cpp
    assert _compile_and_run(cpp + driver) == (
        "1700000060000 1700000060000\n"
    )


def test_parameterless_wrapper_preserves_captured_time_history_natively() -> None:
    source = '''//@version=6
strategy("captured time history")
captureTime() => time[1]
wrapper() => captureTime()
captured = wrapper()
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 1.0, 1.0, 1.0, 1.0, 1700000000000LL},
        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 1700000060000LL},
        Bar{3.0, 3.0, 3.0, 3.0, 1.0, 1700000120000LL},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    std::cout << strategy.captured << "\n";
}
'''
    cpp = transpile(source)
    assert "int64_t captureTime()" in cpp
    assert "int64_t wrapper()" in cpp
    assert "int64_t captured = 0;" in cpp
    assert _compile_and_run(cpp + driver) == "1700000060000\n"


def test_untyped_udf_history_specializes_both_source_orders_natively() -> None:
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{10.0, 12.0, 9.0, 11.25, 1.0, 1700000000000LL},
        Bar{20.0, 21.0, 18.0, 19.75, 1.0, 1700000060000LL},
        Bar{30.0, 32.0, 29.0, 31.50, 1.0, 1700000120000LL},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    std::cout << strategy.intOut << " " << strategy.floatOut << " "
              << strategy.intFirstMissing << "\n";
}
'''
    orders = (
        (
            "int-first",
            "int intOut = history(src=bar_index)\n"
            "float floatOut = history(src=close)",
            "int64_t history_cs0(const Series<int64_t>& src)",
            "double history_cs1(const Series<double>& src)",
        ),
        (
            "float-first",
            "float floatOut = history(src=close)\n"
            "int intOut = history(src=bar_index)",
            "double history_cs0(const Series<double>& src)",
            "int64_t history_cs1(const Series<int64_t>& src)",
        ),
    )
    for title, calls, first_signature, second_signature in orders:
        source = f'''//@version=6
strategy("untyped udf {title}")
history(src) => src[1]
{calls}
var bool intFirstMissing = false
if bar_index == 0
    intFirstMissing := na(intOut)
'''
        cpp = transpile(source)
        assert first_signature in cpp
        assert second_signature in cpp
        assert _compile_and_run(cpp + driver) == "1 19.75 1\n"


def test_untyped_method_history_specializes_both_source_orders_natively() -> None:
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{10.0, 12.0, 9.0, 11.25, 1.0, 1700000000000LL},
        Bar{20.0, 21.0, 18.0, 19.75, 1.0, 1700000060000LL},
        Bar{30.0, 32.0, 29.0, 31.50, 1.0, 1700000120000LL},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    std::cout << strategy.intOut << " " << strategy.floatOut << " "
              << strategy.boolOut << " " << strategy.intFirstMissing << " "
              << strategy.boolFirstValue << "\n";
}
'''
    orders = (
        (
            "int-first",
            "int intOut = holder.history(src=bar_index)\n"
            "float floatOut = holder.history(src=close)",
            "int64_t _udt_Holder_history_cs0",
            "double _udt_Holder_history_cs1",
        ),
        (
            "float-first",
            "float floatOut = holder.history(src=close)\n"
            "int intOut = holder.history(src=bar_index)",
            "double _udt_Holder_history_cs0",
            "int64_t _udt_Holder_history_cs1",
        ),
    )
    for title, calls, first_signature, second_signature in orders:
        source = f'''//@version=6
strategy("untyped method {title}")
type Holder
    int seed
method history(Holder self, src) => src[1]
var Holder holder = Holder.new(0)
{calls}
bool boolOut = holder.history(src=close > open)
var bool intFirstMissing = false
var bool boolFirstValue = true
if bar_index == 0
    intFirstMissing := na(intOut)
    boolFirstValue := boolOut
'''
        cpp = transpile(source)
        assert first_signature in cpp
        assert second_signature in cpp
        assert "const Series<bool>& src" in cpp
        assert _compile_and_run(cpp + driver) == "1 19.75 0 1 0\n"


def test_transformed_untyped_wrapper_profiles_own_each_history_bridge() -> None:
    source = '''//@version=6
strategy("transformed untyped wrapper")
inner(innerSrc) => innerSrc[1]
outer(outerSrc) => inner(outerSrc + 0)
int intOut = outer(bar_index)
float floatOut = outer(close)
var bool intFirstMissing = false
if bar_index == 0
    intFirstMissing := na(intOut)
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{10.0, 12.0, 9.0, 11.25, 1.0, 1700000000000LL},
        Bar{20.0, 21.0, 18.0, 19.75, 1.0, 1700000060000LL},
        Bar{30.0, 32.0, 29.0, 31.50, 1.0, 1700000120000LL},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    std::cout << strategy.intOut << " " << strategy.floatOut << " "
              << strategy.intFirstMissing << "\n";
}
'''
    cpp = transpile(source)
    assert "Series<int64_t> _series_arg_" in cpp
    assert "Series<double> _series_arg_" in cpp
    assert "int64_t outer_cs0(int64_t outerSrc)" in cpp
    assert "double outer_cs1(double outerSrc)" in cpp
    assert _compile_and_run(cpp + driver) == "1 19.75 1\n"


def test_mutable_local_wide_history_survives_udf_and_method_wrappers() -> None:
    source = '''//@version=6
strategy("mutable local wide history")
captureReassigned() =>
    int value = 0
    value := time[1]
    value
wrapper() => captureReassigned()
type Holder
    int seed
method captureReassigned(Holder self) =>
    int value = 0
    value := time[1]
    value
method wrapper(Holder self) => self.captureReassigned()
var Holder holder = Holder.new(0)
udfObserved = wrapper()
methodObserved = holder.wrapper()
var bool udfFirstMissing = false
var bool methodFirstMissing = false
if bar_index == 0
    udfFirstMissing := na(udfObserved)
    methodFirstMissing := na(methodObserved)
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 1.0, 1.0, 1.0, 1.0, 1700000000000LL},
        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 1700000060000LL},
        Bar{3.0, 3.0, 3.0, 3.0, 1.0, 1700000120000LL},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    std::cout << strategy.udfObserved << " " << strategy.methodObserved << " "
              << strategy.udfFirstMissing << " "
              << strategy.methodFirstMissing << "\n";
}
'''
    cpp = transpile(source)
    assert cpp.count("int64_t value = 0;") == 2
    assert "int64_t captureReassigned()" in cpp
    assert "int64_t wrapper()" in cpp
    assert "int64_t _udt_Holder_captureReassigned(Holder self)" in cpp
    assert "int64_t _udt_Holder_wrapper(Holder self)" in cpp
    assert _compile_and_run(cpp + driver) == (
        "1700000060000 1700000060000 1 1\n"
    )


def test_callable_result_history_preserves_int64_and_first_bar_na() -> None:
    source = '''//@version=6
strategy("call result wide history")
captureTime() => time
prior = captureTime()[1]
var bool firstMissing = false
if bar_index == 0
    firstMissing := na(prior)
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 1.0, 1.0, 1.0, 1.0, 1700000000000LL},
        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 1700000060000LL},
        Bar{3.0, 3.0, 3.0, 3.0, 1.0, 1700000120000LL},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    std::cout << strategy.prior << " " << strategy.firstMissing << "\n";
}
'''
    cpp = transpile(source)
    assert "Series<int64_t> _hist_call_" in cpp
    assert "int64_t prior = 0;" in cpp
    assert _compile_and_run(cpp + driver) == "1700000060000 1\n"


def test_int64_to_float_series_bridge_translates_na_sentinel() -> None:
    source = '''//@version=6
strategy("sentinel preserving widening")
intHistory(int src) => src[1]
floatHistory(float src) => src[1]
out = floatHistory(intHistory(time))
var bool firstMissing = false
var bool secondMissing = false
if bar_index == 0
    firstMissing := na(out)
if bar_index == 1
    secondMissing := na(out)
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 1.0, 1.0, 1.0, 1.0, 1700000000000LL},
        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 1700000060000LL},
        Bar{3.0, 3.0, 3.0, 3.0, 1.0, 1700000120000LL},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    std::cout << static_cast<long long>(strategy.out) << " "
              << strategy.firstMissing << " " << strategy.secondMissing
              << "\n";
}
'''
    cpp = transpile(source)
    assert "is_na(_pf_series_raw) ? na<double>()" in cpp
    assert _compile_and_run(cpp + driver) == "1700000000000 1 1\n"


def test_unresolved_transformed_untyped_history_profile_fails_closed() -> None:
    source = '''//@version=6
strategy("unresolved transformed profile")
inner(x) => x[1]
outer(y) => inner(math.abs(y))
out = outer(close)
'''
    with pytest.raises(
        CompileError,
        match=(
            "Cannot infer the per-callsite primitive type of history "
            "parameter 'x'"
        ),
    ):
        transpile(source)


def test_multi_wrapper_untyped_history_profile_collision_fails_closed() -> None:
    source = '''//@version=6
strategy("multi-wrapper profile collision")
history(src) => src[1]
first(value) => history(value)
second(value) => history(value)
int intOut = first(bar_index)
float floatOut = first(close)
int timeOut = second(time)
'''
    with pytest.raises(
        CompileError,
        match=(
            "distinct primitive types collapse onto the same written-call "
            "variant"
        ),
    ):
        transpile(source)


def test_history_return_alias_and_terminal_if_keep_variant_family_natively() -> None:
    source = '''//@version=6
strategy("history return flow")
aliasHistory(src) =>
    prior = src[1]
    prior
branchHistory(src) =>
    if bar_index >= 0
        src[1]
    else
        src
int aliasInt = aliasHistory(bar_index)
float aliasFloat = aliasHistory(close)
int branchInt = branchHistory(bar_index)
float branchFloat = branchHistory(close)
var bool aliasFirstMissing = false
var bool branchFirstMissing = false
if bar_index == 0
    aliasFirstMissing := na(aliasInt)
    branchFirstMissing := na(branchInt)
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{10.0, 12.0, 9.0, 11.25, 1.0, 1700000000000LL},
        Bar{20.0, 21.0, 18.0, 19.75, 1.0, 1700000060000LL},
        Bar{30.0, 32.0, 29.0, 31.50, 1.0, 1700000120000LL},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    std::cout << strategy.aliasInt << " " << strategy.aliasFloat << " "
              << strategy.branchInt << " " << strategy.branchFloat << " "
              << strategy.aliasFirstMissing << " "
              << strategy.branchFirstMissing << "\n";
}
'''
    cpp = transpile(source)
    assert "int64_t aliasHistory_cs0(const Series<int64_t>& src)" in cpp
    assert "double aliasHistory_cs1(const Series<double>& src)" in cpp
    assert "int64_t branchHistory_cs0(const Series<int64_t>& src)" in cpp
    assert "double branchHistory_cs1(const Series<double>& src)" in cpp
    assert _compile_and_run(cpp + driver) == "1 19.75 1 19.75 1 1\n"


def test_wide_local_reassignment_through_na_wrappers_runs_natively() -> None:
    source = '''//@version=6
strategy("wide local na wrappers")
captureNz() =>
    int value = 0
    value := nz(time[1])
    value
captureFixnan() =>
    int value = 0
    value := fixnan(time[1])
    value
nzObserved = captureNz()
fixnanObserved = captureFixnan()
var bool fixnanFirstMissing = false
if bar_index == 0
    fixnanFirstMissing := na(fixnanObserved)
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 1.0, 1.0, 1.0, 1.0, 1700000000000LL},
        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 1700000060000LL},
        Bar{3.0, 3.0, 3.0, 3.0, 1.0, 1700000120000LL},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    std::cout << strategy.nzObserved << " " << strategy.fixnanObserved << " "
              << strategy.fixnanFirstMissing << "\n";
}
'''
    cpp = transpile(source)
    assert cpp.count("int64_t value = 0;") == 2
    assert "int64_t captureNz()" in cpp
    assert "int64_t captureFixnan_cs0()" in cpp
    assert "int64_t _prev_fixnan_1 = na<int64_t>();" in cpp
    assert _compile_and_run(cpp + driver) == (
        "1700000060000 1700000060000 1\n"
    )


def test_nested_untyped_pure_transform_does_not_use_shared_return_cache() -> None:
    source = '''//@version=6
strategy("nested pure transform")
history(src) => src[1]
castInt(value) => int(value)
outer(value) => history(castInt(value))
out = outer(time)
'''
    with pytest.raises(
        CompileError,
        match=(
            "Cannot infer the per-callsite primitive type of history "
            "parameter 'src'"
        ),
    ):
        transpile(source)
