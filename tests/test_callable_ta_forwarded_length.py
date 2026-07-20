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


def test_reverse_defined_three_method_chain_revisits_growing_ta_owner() -> None:
    source = '''//@version=6
strategy("reverse growing method chain length")
type Holder
    float seed
method outer(Holder self, int outerLen) => self.middle(outerLen)
method middle(Holder self, int middleLen) =>
    ta.ema(close, middleLen) + self.inner(middleLen)
method inner(Holder self, int innerLen) => ta.sma(close, innerLen)
var Holder holder = Holder.new(0.0)
n = input.int(11, "Len")
out = holder.outer(n)
plot(out)
'''
    cpp = transpile(source)
    assert _ctor_periods(cpp, "ema") == ["11"]
    assert _ctor_periods(cpp, "sma") == ["11"]
    assert any(
        'get_input_int("Len", 11)' in line for line in _reset_lines(cpp, "ema")
    )
    assert any(
        'get_input_int("Len", 11)' in line for line in _reset_lines(cpp, "sma")
    )


def test_existing_method_and_top_level_edges_revisit_growing_ta_owner() -> None:
    source = '''//@version=6
strategy("existing growing method edges")
type Holder
    float seed
method middle(Holder self, int middleLen) =>
    ta.ema(close, middleLen) + self.inner(middleLen)
method outer(Holder self, int outerLen) => self.middle(outerLen)
method inner(Holder self, int innerLen) => ta.sma(close, innerLen)
var Holder holder = Holder.new(0.0)
n = input.int(11, "Len")
out = holder.outer(n)
plot(out)
'''
    cpp = transpile(source)
    assert _ctor_periods(cpp, "ema") == ["11"]
    assert _ctor_periods(cpp, "sma") == ["11"]


def test_growing_chain_keeps_unrelated_ta_out_of_two_constant_callsites() -> None:
    source = '''//@version=6
strategy("growing method owner slice")
type Holder
    float seed
method outer(Holder self, int outerLen) => self.middle(outerLen)
method unrelated(Holder self) => ta.rma(close, 19)
method middle(Holder self, int middleLen) =>
    ta.ema(close, middleLen) + self.inner(middleLen)
method inner(Holder self, int innerLen) => ta.sma(close, innerLen)
var Holder holder = Holder.new(0.0)
noise = holder.unrelated()
first = holder.outer(4)
second = holder.outer(9)
plot(noise + first + second)
'''
    cpp = transpile(source)
    assert _ctor_periods(cpp, "rma") == ["19"]
    assert _ctor_periods(cpp, "ema") == ["4", "9"]
    assert _ctor_periods(cpp, "sma") == ["4", "9"]


def test_security_nested_dynamic_length_keeps_requested_context_diagnostic() -> None:
    source = '''//@version=6
strategy("requested nested mixed length")
length = input.int(2, "L")
inner(float src, int innerLen) => ta.ema(src, innerLen)
outer(float src, int outerLen) =>
    [inner(src, outerLen), inner(src, outerLen + int(close))]
[a, b] = request.security(syminfo.tickerid, "1", outer(close, length))
plot(a + b)
'''
    with pytest.raises(CompileError) as exc_info:
        transpile(source)
    message = str(exc_info.value)
    assert "requested-context TA constructor length" in message
    assert "not a stable per-run scalar" in message


def test_earlier_unrelated_ordinary_ta_length_error_keeps_precedence() -> None:
    source = '''//@version=6
strategy("two length errors")
bad(int n) => ta.sma(close, n)
ordinary = bad(int(close))
e(float src, int len) => ta.ema(src, len)
four(float src, int p) => [e(src, p), e(src, p + int(close))]
[a, b] = request.security(syminfo.tickerid, "1", four(close, input.int(2, "L")))
plot(ordinary + a + b)
'''
    with pytest.raises(CompileError) as exc_info:
        transpile(source)
    message = str(exc_info.value)
    assert "Unsupported TA constructor length 'int(close)'" in message
    assert "requested-context" not in message


def test_same_ta_source_with_safe_requested_variant_keeps_ordinary_error() -> None:
    source = '''//@version=6
strategy("same TA node mixed contexts")
e(float src, int len) => ta.ema(src, len)
ordinary = e(close, int(close))
requested = request.security(
    syminfo.tickerid, "1", e(close, input.int(2, "L")))
plot(ordinary + requested)
'''
    with pytest.raises(CompileError) as exc_info:
        transpile(source)
    message = str(exc_info.value)
    assert "Unsupported TA constructor length 'int(close)'" in message
    assert "requested-context" not in message


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


def test_nested_parameterless_ta_state_stays_isolated_when_callee_has_more_callsites() -> None:
    source = '''//@version=6
strategy("nested state crowded namespace")
inner(float src) => ta.change(src)
outer(float src) => inner(src)
noise1 = inner(high)
noise2 = inner(low)
noise3 = inner(volume)
a = outer(close)
b = outer(open)
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 11.0, 0.0, 10.0, 1.0, 1000},
        Bar{2.0, 21.0, 1.0, 20.0, 2.0, 61000},
        Bar{3.0, 31.0, 2.0, 30.0, 3.0, 121000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) return 7;
    std::cout << strategy.a << " " << strategy.b << "\n";
}
'''
    assert _compile_and_run(transpile(source) + driver) == "10 1\n"


def test_three_level_dynamic_length_reuses_outer_target_when_leaf_namespace_is_crowded() -> None:
    source = '''//@version=6
strategy("three level crowded dynamic length")
leaf(float src, int len) => ta.sma(src, len)
middle(float src, int len) => leaf(src, len)
entry(float src, int len) => middle(src, len)
noise1 = leaf(high, 2)
noise2 = leaf(low, 3)
noise3 = leaf(volume, 4)
a = entry(close, 2)
b = entry(open, 3)
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 11.0, 0.0, 10.0, 1.0, 1000},
        Bar{2.0, 21.0, 1.0, 20.0, 2.0, 61000},
        Bar{3.0, 31.0, 2.0, 30.0, 3.0, 121000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) return 7;
    std::cout << strategy.a << " " << strategy.b << "\n";
}
'''
    assert _compile_and_run(transpile(source) + driver) == "25 2\n"


def test_three_level_dynamic_length_composes_shifted_parent_source_identity() -> None:
    source = '''//@version=6
strategy("three level shifted parent source identity")
leaf(float src, int len) => ta.sma(src, len)
sub(float src, int len) => leaf(src, len)
pre(float src, int len) => sub(src, len)
outer(float src, int len) => sub(src, len)
p = pre(volume, 4)
a = outer(close, 1)
b = outer(open, 2)
c = outer(high, 3)
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 11.0, 0.0, 10.0, 1.0, 1000},
        Bar{2.0, 21.0, 1.0, 20.0, 2.0, 61000},
        Bar{3.0, 31.0, 2.0, 30.0, 3.0, 121000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) return 7;
    std::cout << strategy.a << " " << strategy.b << " " << strategy.c << "\n";
}
'''
    assert _compile_and_run(transpile(source) + driver) == "30 2.5 21\n"


def test_crowded_nested_var_paths_get_fresh_persistent_state() -> None:
    source = '''//@version=6
strategy("crowded callable var only")
inner(float src) =>
    var float total = 0.0
    total += src
    total
outer(float src) => inner(src)
noise = inner(high)
a = outer(close)
b = outer(open)
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 11.0, 0.0, 10.0, 1.0, 1000},
        Bar{2.0, 21.0, 1.0, 20.0, 2.0, 61000},
        Bar{3.0, 31.0, 2.0, 30.0, 3.0, 121000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) return 7;
    std::cout << strategy.a << " " << strategy.b << "\n";
}
'''
    assert _compile_and_run(transpile(source) + driver) == "60 6\n"


def test_crowded_nested_fixnan_paths_get_fresh_previous_value_state() -> None:
    source = '''//@version=6
strategy("crowded callable fixnan only")
inner(float src) => fixnan(src)
outer(float src) => inner(src)
noise = inner(high)
a = outer(close)
b = outer(open)
'''
    driver = r'''
#include <iostream>
#include <limits>
int main() {
    double nan = std::numeric_limits<double>::quiet_NaN();
    Bar bars[] = {
        Bar{1.0, 11.0, 0.0, 10.0, 1.0, 1000},
        Bar{2.0, 21.0, 1.0, nan, 2.0, 61000},
        Bar{3.0, 31.0, 2.0, nan, 3.0, 121000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) return 7;
    std::cout << strategy.a << " " << strategy.b << "\n";
}
'''
    assert _compile_and_run(transpile(source) + driver) == "10 3\n"


def test_transitive_crowded_var_and_fixnan_paths_cross_pure_wrappers() -> None:
    source = '''//@version=6
strategy("transitive crowded var and fixnan")
inner(float src) =>
    var float total = 0.0
    held = fixnan(src)
    total += held
    total
middle(float src) => inner(src)
outer(float src) => middle(src)
noise = inner(high)
a = outer(close)
b = outer(open)
'''
    driver = r'''
#include <iostream>
#include <limits>
int main() {
    double nan = std::numeric_limits<double>::quiet_NaN();
    Bar bars[] = {
        Bar{1.0, 11.0, 0.0, 10.0, 1.0, 1000},
        Bar{2.0, 21.0, 1.0, nan, 2.0, 61000},
        Bar{3.0, 31.0, 2.0, nan, 3.0, 121000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) return 7;
    std::cout << strategy.a << " " << strategy.b << "\n";
}
'''
    assert _compile_and_run(transpile(source) + driver) == "30 6\n"


def test_shifted_dynamic_ta_paths_keep_nested_var_state_independent() -> None:
    source = '''//@version=6
strategy("shifted TA lineage with nested var state")
state(float src) =>
    var float total = 0.0
    total += src
    total
sub(float src, int len) =>
    ignored = ta.sma(src, len)
    state(src)
pre(float src, int len) => sub(src, len)
outer(float src, int len) => sub(src, len)
p = pre(volume, 4)
a = outer(close, 1)
b = outer(open, 2)
c = outer(high, 3)
'''
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{1.0, 11.0, 0.0, 10.0, 1.0, 1000},
        Bar{2.0, 21.0, 1.0, 20.0, 2.0, 61000},
        Bar{3.0, 31.0, 2.0, 30.0, 3.0, 121000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) return 7;
    std::cout << strategy.p << " " << strategy.a << " "
              << strategy.b << " " << strategy.c << "\n";
}
'''
    assert _compile_and_run(transpile(source) + driver) == "6 60 6 63\n"


def test_recursive_stateful_callable_is_rejected_before_instance_expansion() -> None:
    source = '''//@version=6
strategy("recursive stateful callable")
rec(float src, int n) =>
    var float total = 0.0
    total += src
    n <= 0 ? total : rec(src, n - 1)
out = rec(close, 1)
'''
    with pytest.raises(
        CompileError, match="Recursive stateful callable cycle"
    ):
        transpile(source)
