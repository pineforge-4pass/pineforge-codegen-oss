"""TA constructor lengths forwarded through callable boundaries.

The probes in this module are authored as compact Pine v6 reductions.  They
exercise the call-path argument propagation used to size stateful TA helpers;
the generated C++ must never retain a callable-local ``len`` constructor arg.
"""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests.test_runtime_var_initialization import _compile_and_run


def _ctor_periods(cpp: str, ta_name: str = "sma") -> list[str]:
    members = re.findall(rf"(_ta_{ta_name}_\d+(?:_[A-Za-z0-9]+)*)\(", cpp)
    periods: list[str] = []
    for member in members:
        match = re.search(rf"\b{re.escape(member)}\(([^)]*)\)", cpp)
        assert match is not None
        periods.append(match.group(1).strip())
    return periods


def _reset_lines(cpp: str, ta_name: str = "sma") -> list[str]:
    return [
        line.strip()
        for line in cpp.splitlines()
        if re.match(
            rf"_ta_{ta_name}_\d+(?:_[A-Za-z0-9]+)*\s*=\s*ta::",
            line.strip(),
        )
    ]


def test_direct_udf_parameter_length_control() -> None:
    source = '''//@version=6
strategy("direct UDF length")
smooth(float src, int len) => ta.sma(src, len)
n = input.int(3, "Len")
out = smooth(close, n)
plot(out)
'''
    cpp = transpile(source)
    assert _ctor_periods(cpp) == ["3"]
    assert any('get_input_int("Len", 3)' in line for line in _reset_lines(cpp))


def test_nested_udf_parameter_length_control() -> None:
    source = '''//@version=6
strategy("nested UDF length")
inner(float src, int len) => ta.sma(src, len)
outer(float src, int len) => inner(src, len)
n = input.int(4, "Len")
out = outer(close, n)
plot(out)
'''
    cpp = transpile(source)
    assert _ctor_periods(cpp) == ["4"]
    assert any('get_input_int("Len", 4)' in line for line in _reset_lines(cpp))


def test_direct_method_parameter_length() -> None:
    source = '''//@version=6
strategy("direct method length")
type Holder
    float seed
method smooth(Holder self, float src, int len) => ta.sma(src, len)
var Holder holder = Holder.new(0.0)
n = input.int(5, "Len")
out = holder.smooth(close, n)
plot(out)
'''
    cpp = transpile(source)
    assert _ctor_periods(cpp) == ["5"]
    assert any('get_input_int("Len", 5)' in line for line in _reset_lines(cpp))


def test_sibling_method_forwards_parameter_length() -> None:
    source = '''//@version=6
strategy("sibling method length")
type Holder
    float seed
method inner(Holder self, int len) => ta.sma(close, len)
method outer(Holder self, int len) => self.inner(len)
var Holder holder = Holder.new(0.0)
n = input.int(6, "Len")
out = holder.outer(n)
plot(out)
'''
    cpp = transpile(source)
    assert _ctor_periods(cpp) == ["6"]
    assert any('get_input_int("Len", 6)' in line for line in _reset_lines(cpp))


def test_udf_wrapper_receiver_forwards_parameter_length() -> None:
    source = '''//@version=6
strategy("wrapper receiver length")
type Holder
    float seed
method inner(Holder self, int len) => ta.sma(close, len)
wrapped(Holder obj, int len) => obj.inner(len)
var Holder holder = Holder.new(0.0)
n = input.int(7, "Len")
out = wrapped(holder, n)
plot(out)
'''
    cpp = transpile(source)
    assert _ctor_periods(cpp) == ["7"]
    assert any('get_input_int("Len", 7)' in line for line in _reset_lines(cpp))


def test_forward_defined_sibling_method_forwards_parameter_length() -> None:
    source = '''//@version=6
strategy("forward sibling method length")
type Holder
    float seed
method outer(Holder self, int len) => self.inner(len)
method inner(Holder self, int len) => ta.sma(close, len)
var Holder holder = Holder.new(0.0)
n = input.int(10, "Len")
out = holder.outer(n)
plot(out)
'''
    cpp = transpile(source)
    assert _ctor_periods(cpp) == ["10"]
    assert any('get_input_int("Len", 10)' in line for line in _reset_lines(cpp))


def test_forward_defined_sibling_method_composes_renamed_parameters() -> None:
    source = '''//@version=6
strategy("forward renamed sibling method length")
type Holder
    float seed
method outer(Holder self, int outerLen) => self.inner(outerLen)
method inner(Holder self, int innerLen) => ta.sma(close, innerLen)
var Holder holder = Holder.new(0.0)
n = input.int(2, "Len")
out = holder.outer(n)
plot(out)
'''
    cpp = transpile(source)
    assert _ctor_periods(cpp) == ["2"]
    assert "innerLen" not in "\n".join(_reset_lines(cpp))
    assert any('get_input_int("Len", 2)' in line for line in _reset_lines(cpp))
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 11.0, 0.0, 10.0, 1.0, 1000},
        Bar{2.0, 21.0, 1.0, 20.0, 1.0, 61000},
        Bar{3.0, 31.0, 2.0, 30.0, 1.0, 121000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) return 7;
    std::cout << strategy.out << "\n";
}
'''
    assert _compile_and_run(cpp + driver) == "25\n"


def test_widened_caller_indices_exclude_interleaved_unrelated_ta() -> None:
    source = '''//@version=6
strategy("interleaved unrelated TA length")
type Holder
    float seed
method inner(Holder self, int len) => ta.sma(close, len)
method unrelated(Holder self, int len) => ta.ema(close, len)
method outer(Holder self, int len) =>
    own = ta.rma(close, len)
    own + self.inner(len)
var Holder holder = Holder.new(0.0)
n = input.int(2, "Len")
unrelatedOut = holder.unrelated(20)
out = holder.outer(n)
plot(out + unrelatedOut)
'''
    cpp = transpile(source)
    assert _ctor_periods(cpp, "sma") == ["2"]
    assert _ctor_periods(cpp, "rma") == ["2"]
    assert _ctor_periods(cpp, "ema") == ["20"]
    assert _reset_lines(cpp, "ema") == []
    driver = r'''
#include <iostream>
#include <vector>
int main() {
    std::vector<Bar> bars;
    for (int i = 1; i <= 21; ++i) {
        bars.push_back(Bar{
            double(i), double(i), double(i), double(i), 1.0,
            1000LL + 60000LL * i,
        });
    }
    GeneratedStrategy strategy;
    strategy.run(bars.data(), bars.size());
    if (!strategy.last_error().empty()) return 7;
    std::cout << strategy.unrelatedOut << "\n";
}
'''
    assert _compile_and_run(cpp + driver) == "12.7835\n"


def test_reverse_defined_three_method_chain_forwards_parameter_length() -> None:
    source = '''//@version=6
strategy("reverse method chain length")
type Holder
    float seed
method outer(Holder self, int len) => self.middle(len)
method middle(Holder self, int len) => self.inner(len)
method inner(Holder self, int len) => ta.sma(close, len)
var Holder holder = Holder.new(0.0)
n = input.int(11, "Len")
out = holder.outer(n)
plot(out)
'''
    cpp = transpile(source)
    assert _ctor_periods(cpp) == ["11"]
    assert any('get_input_int("Len", 11)' in line for line in _reset_lines(cpp))


@pytest.mark.parametrize("forward_defined", [False, True])
def test_branch_wrapper_and_two_callsites_keep_distinct_lengths(
    forward_defined: bool,
) -> None:
    inner = (
        "method inner(Holder self, float src, int len) => ta.ema(src, len)"
    )
    outer = '''method outer(Holder self, float src, int len, bool choose) =>
    choose ? self.inner(src, len) : self.inner(src, len + 1)'''
    method_defs = f"{outer}\n{inner}" if forward_defined else f"{inner}\n{outer}"
    source = f'''//@version=6
strategy("branch and callsite lengths")
type Holder
    float seed
{method_defs}
var Holder holder = Holder.new(0.0)
fast = input.int(8, "Fast")
slow = input.int(13, "Slow")
a = holder.outer(close, fast, true)
b = holder.outer(open, slow, false)
plot(a + b)
'''
    cpp = transpile(source)
    periods = _ctor_periods(cpp, "ema")
    assert periods == ["8", "9", "13", "14"]
    resets = _reset_lines(cpp, "ema")
    assert sum('get_input_int("Fast", 8)' in line for line in resets) == 2
    assert sum('get_input_int("Slow", 13)' in line for line in resets) == 2


def test_series_dynamic_length_remains_rejected_through_method_wrapper() -> None:
    source = '''//@version=6
strategy("dynamic method length guard")
type Holder
    float seed
method inner(Holder self, int len) => ta.sma(close, len)
method outer(Holder self, int len) => self.inner(len)
var Holder holder = Holder.new(0.0)
out = holder.outer(int(ta.atr(14)))
plot(out)
'''
    with pytest.raises(CompileError) as exc:
        transpile(source)
    assert "Unsupported TA constructor length" in str(exc.value)


def test_two_method_callsites_run_with_distinct_forwarded_lengths() -> None:
    source = '''//@version=6
strategy("runtime forwarded lengths")
type Holder
    float seed
method inner(Holder self, float src, int len) => ta.sma(src, len)
method outer(Holder self, float src, int len) => self.inner(src, len)
var Holder holder = Holder.new(0.0)
fast = input.int(2, "Fast")
slow = input.int(3, "Slow")
a = holder.outer(close, fast)
b = holder.outer(open, slow)
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 11.0, 0.0, 10.0, 1.0, 1000},
        Bar{2.0, 21.0, 1.0, 20.0, 1.0, 61000},
        Bar{3.0, 31.0, 2.0, 30.0, 1.0, 121000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) return 7;
    std::cout << strategy.a << " " << strategy.b << "\n";
}
'''
    assert _compile_and_run(transpile(source) + driver) == "25 2\n"
