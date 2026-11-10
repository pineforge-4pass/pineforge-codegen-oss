"""Input-derived TA settings retain their declared numeric type."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.errors import CompileError
from pineforge_codegen.symbols import PineType
from tests._e2e import reference_codegen, transpile_json
from tests.test_runtime_var_initialization import _compile_and_run


CPP_REFERENCE = "a156a7d2785c8729e54da7eb01c5d41ddd03df14"
_BAR_COUNT = 512
_WARMUP = 32
_HEADER = '''//@version=6
strategy("float indicator setting", overlay = true, initial_capital = 10000)
'''


@dataclass(frozen=True)
class SettingCase:
    name: str
    source: str
    override: tuple[str, str] | None = None
    control_change: tuple[str, str] | None = None


def _observe(source, members, override=None, *, precalculate=False):
    cpp = transpile(source)
    prints = " << ' ' << ".join(f"static_cast<double>({name})" for name in members)
    setter = (
        f"strategy.set_input({json.dumps(override[0])}, {json.dumps(override[1])});"
        if override is not None else ""
    )
    run = (
        "strategy.run(bars.data(), static_cast<int>(bars.size()));"
        if precalculate else
        'strategy.run(bars.data(), static_cast<int>(bars.size()), "15", "15");'
    )
    driver = r'''
#include <cmath>
#include <iomanip>
#include <iostream>
#include <vector>
struct ObservedStrategy : GeneratedStrategy {
    int scenario = 0;
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        std::cout << scenario << ' ' << PRINTS << '\n';
    }
};
int main() {
    std::cout << std::setprecision(17);
    std::vector<Bar> bars;
    for (int i = 0; i < BAR_COUNT; ++i) {
        const double center = 100.0 + i * 0.07
            + 12.0 * std::sin(i * 0.23) + 4.0 * std::cos(i * 0.071);
        const double open = center + std::sin(i * 0.47);
        const double close = center + std::cos(i * 0.31);
        bars.push_back(Bar{open,
            std::max(open, close) + 1.0 + (i % 5) * 0.17,
            std::min(open, close) - 1.0 - (i % 7) * 0.13,
            close, 100.0 + (i % 13),
            1700000100000LL + i * 900000LL});
    }
    for (int scenario = 0; scenario < SCENARIOS; ++scenario) {
        ObservedStrategy strategy;
        strategy.scenario = scenario;
        if (scenario == 1) { SETTER }
        RUN
    }
}
'''
    driver = (
        driver.replace("PRINTS", prints)
        .replace("BAR_COUNT", str(_BAR_COUNT))
        .replace("SCENARIOS", "2" if override is not None else "1")
        .replace("SETTER", setter)
        .replace("RUN", run)
    )
    rows = [[] for _ in range(2 if override is not None else 1)]
    for line in _compile_and_run(cpp + driver).splitlines():
        scenario, *values = line.split()
        assert len(values) == len(members), line
        rows[int(scenario)].append(tuple(map(float, values)))
    assert all(len(run_rows) == _BAR_COUNT for run_rows in rows)
    return rows


def _column(rows, position):
    return [row[position] for row in rows[_WARMUP:]]


def _assert_equal_values(left, right):
    assert len(left) == len(right) == _BAR_COUNT - _WARMUP
    assert all(math.isfinite(value) for value in left)
    assert all(math.isfinite(value) for value in right)
    assert len(set(left)) > 1
    assert len(set(right)) > 1
    for bar, (actual, expected) in enumerate(zip(left, right), _WARMUP):
        assert actual == pytest.approx(expected, rel=1e-12, abs=1e-10), bar


def _assert_override_moves_values(runs, position=0):
    before, after = (_column(rows, position) for rows in runs)
    assert any(
        not math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-10)
        for left, right in zip(before, after)
    )


def _traced_members(source):
    traces = dict(re.findall(r"^// @pf-trace (\w+)=(\w+)$", source, re.M))
    return traces["a"], traces["b"]


_REPORTED_CASES = (
    pytest.param(
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, level, lower] = ta.bb(close, 20, m / 2)
// @pf-trace level=level
if ta.crossover(close, level)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(close, level)
    strategy.close("Long")
''',
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, level, lower] = ta.bb(close, 20, m * 0.5)
// @pf-trace level=level
if ta.crossover(close, level)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(close, level)
    strategy.close("Long")
''',
        ("Mult", "5.0"), id="bb-input-division",
    ),
    pytest.param(
        _HEADER + '''m = input.float(3, "Mult")
[mid, level, lower] = ta.bb(close, 20, m * 0.5)
// @pf-trace level=level
if ta.crossover(close, level)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(close, level)
    strategy.close("Long")
''',
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, level, lower] = ta.bb(close, 20, m * 0.5)
// @pf-trace level=level
if ta.crossover(close, level)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(close, level)
    strategy.close("Long")
''',
        ("Mult", "5.0"), id="bb-input-product",
    ),
    pytest.param(
        _HEADER + '''m = input.float(2, "Factor")
[level, dir] = ta.supertrend(m * 1.25, 10)
// @pf-trace level=level
if ta.crossover(close, level)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(close, level)
    strategy.close("Long")
''',
        _HEADER + '''m = input.float(2.0, "Factor")
[level, dir] = ta.supertrend(m * 1.25, 10)
// @pf-trace level=level
if ta.crossover(close, level)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(close, level)
    strategy.close("Long")
''',
        ("Factor", "3.0"), id="supertrend-input-product",
    ),
    pytest.param(
        _HEADER + '''m = input.float(1, "Maximum")
level = ta.sar(0.02, 0.02, m * 0.2)
// @pf-trace level=level
if ta.crossover(close, level)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(close, level)
    strategy.close("Long")
''',
        _HEADER + '''m = input.float(1.0, "Maximum")
level = ta.sar(0.02, 0.02, m * 0.2)
// @pf-trace level=level
if ta.crossover(close, level)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(close, level)
    strategy.close("Long")
''',
        ("Maximum", "0.4"), id="sar-input-product",
    ),
    pytest.param(
        _HEADER + '''m = input.float(3, "Mult")
[mid, level, lower] = ta.kc(close, 20, m * 0.5)
// @pf-trace level=level
if ta.crossover(close, level)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(close, level)
    strategy.close("Long")
''',
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, level, lower] = ta.kc(close, 20, m * 0.5)
// @pf-trace level=level
if ta.crossover(close, level)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(close, level)
    strategy.close("Long")
''',
        ("Mult", "5.0"), id="kc-input-product",
    ),
    pytest.param(
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, level, lower] = ta.bb(close, 20, m / 2.0)
// @pf-trace level=level
if ta.crossover(close, level)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(close, level)
    strategy.close("Long")
''',
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, level, lower] = ta.bb(close, 20, m * 0.5)
// @pf-trace level=level
if ta.crossover(close, level)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(close, level)
    strategy.close("Long")
''',
        ("Mult", "5.0"), id="bb-float-divisor",
    ),
    pytest.param(
        _HEADER + '''m = input.float(3.0, "Mult")
half = m / 2
[mid, level, lower] = ta.bb(close, 20, half)
// @pf-trace level=level
if ta.crossover(close, level)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(close, level)
    strategy.close("Long")
''',
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, level, lower] = ta.bb(close, 20, m * 0.5)
// @pf-trace level=level
if ta.crossover(close, level)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(close, level)
    strategy.close("Long")
''',
        ("Mult", "5.0"), id="bb-named-quotient",
    ),
    pytest.param(
        _HEADER + '''m = input.float(3, "Mult")
half = m * 0.5
[mid, level, lower] = ta.bb(close, 20, half)
// @pf-trace level=level
if ta.crossover(close, level)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(close, level)
    strategy.close("Long")
''',
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, level, lower] = ta.bb(close, 20, m * 0.5)
// @pf-trace level=level
if ta.crossover(close, level)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(close, level)
    strategy.close("Long")
''',
        ("Mult", "5.0"), id="bb-named-product",
    ),
)
_SETTING_CASES = (
    SettingCase(
        "bb-division",
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, up, lo] = ta.bb(close, 20, m / 2)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-product-whole-default",
        _HEADER + '''m = input.float(3, "Mult")
[mid, up, lo] = ta.bb(close, 20, m * 0.5)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-product-decimal-default",
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, up, lo] = ta.bb(close, 20, m * 0.5)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-decimal-divisor",
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, up, lo] = ta.bb(close, 20, m / 2.0)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-named-division",
        _HEADER + '''m = input.float(3.0, "Mult")
half = m / 2
[mid, up, lo] = ta.bb(close, 20, half)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-named-product-whole-default",
        _HEADER + '''m = input.float(3, "Mult")
half = m * 0.5
[mid, up, lo] = ta.bb(close, 20, half)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "kc-product",
        _HEADER + '''m = input.float(3, "Mult")
[mid, up, lo] = ta.kc(close, 20, m * 0.5)
[mid2, up2, lo2] = ta.kc(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.kc(close, 20, 1.5)", "[mid2, up2, lo2] = ta.kc(close, 20, 2.5)"),
    ),
    SettingCase(
        "supertrend-product",
        _HEADER + '''m = input.float(2, "Factor")
[lv, dr] = ta.supertrend(m * 1.25, 10)
[lv2, dr2] = ta.supertrend(2.5, 10)
// @pf-trace a=lv
// @pf-trace b=lv2
''',
        ("Factor", "3.0"),
        ("[lv2, dr2] = ta.supertrend(2.5, 10)", "[lv2, dr2] = ta.supertrend(3.75, 10)"),
    ),
    SettingCase(
        "sar-maximum-product",
        _HEADER + '''m = input.float(1, "Maximum")
lv = ta.sar(0.02, 0.02, m * 0.2)
lv2 = ta.sar(0.02, 0.02, 0.2)
// @pf-trace a=lv
// @pf-trace b=lv2
''',
        ("Maximum", "0.4"),
        ("lv2 = ta.sar(0.02, 0.02, 0.2)", "lv2 = ta.sar(0.02, 0.02, 0.08)"),
    ),
    SettingCase(
        "sar-all-settings-product",
        _HEADER + '''m = input.float(1, "Maximum")
lv = ta.sar(m * 0.02, m * 0.02, m * 0.2)
lv2 = ta.sar(0.02, 0.02, 0.2)
// @pf-trace a=lv
// @pf-trace b=lv2
''',
        ("Maximum", "0.5"),
        ("lv2 = ta.sar(0.02, 0.02, 0.2)", "lv2 = ta.sar(0.01, 0.01, 0.1)"),
    ),
    SettingCase(
        "sar-direct-input",
        _HEADER + '''m = input.float(1, "Maximum")
lv = ta.sar(0.02, 0.02, m)
lv2 = ta.sar(0.02, 0.02, 1.0)
// @pf-trace a=lv
// @pf-trace b=lv2
''',
        ("Maximum", "0.06"),
        ("lv2 = ta.sar(0.02, 0.02, 1.0)", "lv2 = ta.sar(0.02, 0.02, 0.06)"),
    ),
    SettingCase(
        "bb-constant-variable",
        _HEADER + '''m = 3.0
[mid, up, lo] = ta.bb(close, 20, m / 2)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
    ),
    SettingCase(
        "bb-integer-input-product",
        _HEADER + '''k = input.int(2, "K")
[mid, up, lo] = ta.bb(close, 20, k * 0.75)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("K", "4"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 3.0)"),
    ),
    SettingCase(
        "bbw-division",
        _HEADER + '''m = input.float(3.0, "Mult")
w = ta.bbw(close, 20, m / 2)
w2 = ta.bbw(close, 20, 1.5)
// @pf-trace a=w
// @pf-trace b=w2
''',
        ("Mult", "5.0"),
        ("w2 = ta.bbw(close, 20, 1.5)", "w2 = ta.bbw(close, 20, 2.5)"),
    ),
    SettingCase(
        "kcw-product",
        _HEADER + '''m = input.float(3, "Mult")
w = ta.kcw(close, 20, m * 0.5)
w2 = ta.kcw(close, 20, 1.5)
// @pf-trace a=w
// @pf-trace b=w2
''',
        ("Mult", "5.0"),
        ("w2 = ta.kcw(close, 20, 1.5)", "w2 = ta.kcw(close, 20, 2.5)"),
    ),
    SettingCase(
        "alma-offset-division",
        _HEADER + '''m = input.float(3.0, "M")
a = ta.alma(close, 20, m / 4, 6)
a2 = ta.alma(close, 20, 0.75, 6)
// @pf-trace a=a
// @pf-trace b=a2
''',
        ("M", "2.0"),
        ("a2 = ta.alma(close, 20, 0.75, 6)", "a2 = ta.alma(close, 20, 0.5, 6)"),
    ),
    SettingCase(
        "alma-integral-sigma-product",
        _HEADER + '''m = input.float(3, "M")
a = ta.alma(close, 20, 0.85, m * 2)
a2 = ta.alma(close, 20, 0.85, 6)
// @pf-trace a=a
// @pf-trace b=a2
''',
        ("M", "4.0"),
        ("a2 = ta.alma(close, 20, 0.85, 6)", "a2 = ta.alma(close, 20, 0.85, 8)"),
    ),
    SettingCase(
        "interpolated-percentile-product",
        _HEADER + '''m = input.float(5, "P")
p = ta.percentile_linear_interpolation(close, 20, m * 10)
p2 = ta.percentile_linear_interpolation(close, 20, 50)
// @pf-trace a=p
// @pf-trace b=p2
''',
        ("P", "7.0"),
        ("p2 = ta.percentile_linear_interpolation(close, 20, 50)", "p2 = ta.percentile_linear_interpolation(close, 20, 70)"),
    ),
    SettingCase(
        "nearest-percentile-product",
        _HEADER + '''m = input.float(5, "P")
p = ta.percentile_nearest_rank(close, 20, m * 10)
p2 = ta.percentile_nearest_rank(close, 20, 50)
// @pf-trace a=p
// @pf-trace b=p2
''',
        ("P", "7.0"),
        ("p2 = ta.percentile_nearest_rank(close, 20, 50)", "p2 = ta.percentile_nearest_rank(close, 20, 70)"),
    ),
    SettingCase(
        "bb-addition",
        _HEADER + '''m = input.float(1, "Mult")
[mid, up, lo] = ta.bb(close, 20, m + 0.5)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "2.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-subtraction",
        _HEADER + '''m = input.float(2, "Mult")
[mid, up, lo] = ta.bb(close, 20, m - 0.5)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "3.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-absolute-division",
        _HEADER + '''m = input.float(3, "Mult")
[mid, up, lo] = ta.bb(close, 20, math.abs(m) / 2)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "-5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-maximum-division",
        _HEADER + '''m = input.float(3, "Mult")
[mid, up, lo] = ta.bb(close, 20, math.max(m, 1) / 2)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-conditional-division",
        _HEADER + '''m = input.float(3, "Mult")
[mid, up, lo] = ta.bb(close, 20, m > 0 ? m / 2 : 1.5)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-literal-division",
        _HEADER + '''[mid, up, lo] = ta.bb(close, 20, 3.0 / 2)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
    ),
    SettingCase(
        "bb-product-integer-operand",
        _HEADER + '''m = input.float(3, "Mult")
[mid, up, lo] = ta.bb(close, 20, m * 0.5 * 1)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-left-constant-product",
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, up, lo] = ta.bb(close, 20, 0.5 * m)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-absolute-product",
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, up, lo] = ta.bb(close, 20, math.abs(m) * 0.5)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-product-plus-zero",
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, up, lo] = ta.bb(close, 20, m * 0.5 + 0.0)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-decimal-subtraction",
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, up, lo] = ta.bb(close, 20, m - 1.5)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "4.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-named-product-decimal-default",
        _HEADER + '''m = input.float(3.0, "Mult")
half = m * 0.5
[mid, up, lo] = ta.bb(close, 20, half)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-direct-fractional-input",
        _HEADER + '''m = input.float(1.5, "Mult")
[mid, up, lo] = ta.bb(close, 20, m)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "2.5"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-product-decimal-operand",
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, up, lo] = ta.bb(close, 20, m * 0.5 * 1.0)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-maximum-product",
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, up, lo] = ta.bb(close, 20, math.max(m * 0.5, 1.0))
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "bb-conditional-product",
        _HEADER + '''m = input.float(3.0, "Mult")
[mid, up, lo] = ta.bb(close, 20, m > 0 ? m * 0.5 : 1.5)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        ("Mult", "5.0"),
        ("[mid2, up2, lo2] = ta.bb(close, 20, 1.5)", "[mid2, up2, lo2] = ta.bb(close, 20, 2.5)"),
    ),
    SettingCase(
        "alma-fractional-sigma-product",
        _HEADER + '''m = input.float(5.0, "M")
a = ta.alma(close, 20, 0.85, m * 0.5)
a2 = ta.alma(close, 20, 0.85, 2.5)
// @pf-trace a=a
// @pf-trace b=a2
''',
        ("M", "7.0"),
        ("a2 = ta.alma(close, 20, 0.85, 2.5)", "a2 = ta.alma(close, 20, 0.85, 3.5)"),
    ),
    SettingCase(
        "alma-fractional-sigma-whole-default",
        _HEADER + '''m = input.float(3, "M")
a = ta.alma(close, 20, 0.85, m * 0.5)
a2 = ta.alma(close, 20, 0.85, 1.5)
// @pf-trace a=a
// @pf-trace b=a2
''',
        ("M", "5.0"),
        ("a2 = ta.alma(close, 20, 0.85, 1.5)", "a2 = ta.alma(close, 20, 0.85, 2.5)"),
    ),
    SettingCase(
        "sar-decimal-default-product",
        _HEADER + '''m = input.float(1.0, "Maximum")
lv = ta.sar(0.02, 0.02, m * 0.2)
lv2 = ta.sar(0.02, 0.02, 0.2)
// @pf-trace a=lv
// @pf-trace b=lv2
''',
        ("Maximum", "0.4"),
        ("lv2 = ta.sar(0.02, 0.02, 0.2)", "lv2 = ta.sar(0.02, 0.02, 0.08)"),
    ),
)
_REFUSED_CASES = (
    pytest.param(
        _HEADER + '''m = input.float(3, "Mult")
[mid, up, lo] = ta.bb(close, 20, nz(m) / 2)
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        "nz(m) / 2", id="replacement-division",
    ),
    pytest.param(
        _HEADER + '''m = input.float(3, "Mult")
half(x) => x / 2
[mid, up, lo] = ta.bb(close, 20, half(m))
[mid2, up2, lo2] = ta.bb(close, 20, 1.5)
// @pf-trace a=up
// @pf-trace b=up2
''',
        "half(m)", id="function-result",
    ),
)
_IDENTITY_CASES = (
    pytest.param('''//@version=6
strategy("derived-class-scope")
rsiLen = input.int(14, "RSI Length")
wilderLen = rsiLen * 2 - 1
src = ta.ema(close, wilderLen)
plot(src)
''', id="integer-arithmetic"),
    pytest.param('''//@version=6
strategy("derived-func-local")
rsiSmooth = input.int(5, "Smooth EMA Length")
qqeCalc(int sf) =>
    wp = sf * 2 - 1
    ta.ema(ta.ema(close, wp), wp)
out = qqeCalc(rsiSmooth)
plot(out)
''', id="function-derived-integer"),
    pytest.param('''//@version=6
strategy("derived-nested-param")
i_bbwLen = input.int(7, "BBW Basis Length")
f_basisMa(float _src, int _len) =>
    ta.sma(_src, _len)
f_bbwp(float _price, int _bbwLen) =>
    f_basisMa(_price, _bbwLen) + ta.stdev(_price, _bbwLen)
out = f_bbwp(close, i_bbwLen)
plot(out)
''', id="nested-integer-parameter"),
    pytest.param('''//@version=6
strategy("derived-nested-inline-input")
dirmov(len) =>
    truerange = ta.rma(ta.tr, len)
    plus = ta.rma(close, len) / truerange
adx(dilen, adxlen) =>
    x = dirmov(dilen)
    ta.rma(x, adxlen)
sig = adx(input(15), input(15))
plot(sig)
''', id="inline-nested-integer-input"),
    pytest.param('''//@version=6
strategy("legit-default-one")
atrLen = input.int(1, "UT Bot ATR Period")
a = ta.atr(atrLen)
plot(a)
''', id="unit-integer-input"),
    pytest.param('''//@version=6
strategy("stable-timeframe-ternary")
maPeriodFastInput = input.int(60, "Fast SMA Period")
isM5 = timeframe.isminutes and timeframe.multiplier == 5
fastPeriod = isM5 ? math.max(1, int(maPeriodFastInput / 5)) : maPeriodFastInput
fastMA = ta.sma(close, fastPeriod)
plot(fastMA)
''', id="timeframe-integer-choice"),
    pytest.param('''//@version=6
strategy("direct method length")
type Holder
    float seed
method smooth(Holder self, float src, int len) => self, ta.sma(src, len)
var Holder holder = Holder.new(0.0)
n = input.int(5, "Len")
out = holder.smooth(close, n)
plot(out)
''', id="method-integer-parameter"),
    pytest.param('''//@version=6
strategy("security dynamic TA offset")
k = input.int(2, "Offset")
x = request.security(syminfo.tickerid, "60", ta.ema(close, 5)[k], lookahead=barmerge.lookahead_off)
if not na(x) and close > x
    strategy.entry("L", strategy.long)
''', id="requested-integer-history"),
    pytest.param('''//@version=6
strategy("global TA through a helper")
s = ta.sma(close, 5)
g() =>
    y = close * 0 + s
    y
v = request.security(syminfo.tickerid, "60", g() + s)
plot(v)
''', id="requested-global-ta"),
    pytest.param('''//@version=6
strategy("t")
x = input.int(5, "n")
plot(close)
''', id="integer-input-getter"),
    pytest.param('''//@version=6
strategy("t")
x = input.timeframe("D", "tf")
plot(close)
''', id="timeframe-input-getter"),
    pytest.param(
        _HEADER + '''m = input.int(3, "Setting")
a = ta.bbw(close, 20, m)
b = ta.bbw(close, 20, na)
c = ta.bbw(close, 20, 3.0)
''', id="plain-integer-missing-setting",
    ),
    pytest.param(
        _HEADER + '''length = input.int(2, "Len")
offset = input.float(0.85, "Offset")
sigma = input.int(5, "Sigma")
value = ta.alma(close, length, offset, sigma)
plot(value)
''', id="alma-plain-inputs-chart",
    ),
    pytest.param(
        _HEADER + '''length = input.int(2, "Len")
offset = input.float(0.85, "Offset")
sigma = input.int(5, "Sigma")
value = request.security(syminfo.tickerid, "60", ta.alma(close, length, offset, sigma))
plot(value)
''', id="alma-plain-inputs-requested",
    ),
    pytest.param(
        _HEADER + '''length = input.int(2, "Len")
offset = input.float(0.85, "Offset")
sigma = input.int(5, "Sigma")
smooth(float src, int period, float center, int width) => ta.alma(src, period, center, width)
chart = smooth(close, length, offset, sigma)
requested = request.security(syminfo.tickerid, "60", smooth(close, length, offset, sigma))
plot(chart + requested)
''', id="alma-plain-inputs-helper-requested",
    ),
    pytest.param(
        _HEADER + '''multiplier = input.float(1.5, "Multiplier")
[basis, upper, lower] = ta.bb(close, 20, multiplier)
plot(upper)
''', id="bb-plain-multiplier-input",
    ),
    pytest.param(
        _HEADER + '''multiplier = input.float(1.5, "Multiplier")
[basis, upper, lower] = ta.kc(close, 20, multiplier)
plot(upper)
''', id="kc-plain-multiplier-input",
    ),
    pytest.param(
        _HEADER + '''factor = input.float(2.5, "Factor")
[value, direction] = ta.supertrend(factor, 10)
plot(value)
''', id="supertrend-plain-factor-input",
    ),
    pytest.param(
        _HEADER + '''start = input.float(0.02, "Start")
increment = input.float(0.02, "Increment")
maximum = input.float(0.2, "Maximum")
value = ta.sar(start, increment, maximum)
plot(value)
''', id="sar-plain-inputs",
    ),
    pytest.param(
        _HEADER + '''[bbBasis, bbUpper, bbLower] = ta.bb(close, 20, 1.5)
[kcBasis, kcUpper, kcLower] = ta.kc(close, 20, 1.5)
[trend, direction] = ta.supertrend(2.5, 10)
sarValue = ta.sar(0.02, 0.02, 0.2)
almaValue = ta.alma(close, 2, 0.85, 5)
plot(bbUpper + kcUpper + trend + sarValue + almaValue)
''', id="literal-float-settings",
    ),
)
_INTEGER_CASES = (
    pytest.param(
        '''//@version=6
strategy("float indicator setting", overlay = true, initial_capital = 10000)
n = input.int(10, "N")
s = ta.sma(close, n * 2)
s2 = ta.sma(close, 20)
// @pf-trace a=s
// @pf-trace b=s2
''',
        '''//@version=6
strategy("float indicator setting", overlay = true, initial_capital = 10000)
n = input.int(10, "N")
s = ta.sma(close, 20)
s2 = ta.sma(close, 20)
// @pf-trace a=s
// @pf-trace b=s2
''',
        '''//@version=6
strategy("float indicator setting", overlay = true, initial_capital = 10000)
n = input.int(10, "N")
s = ta.sma(close, 30)
s2 = ta.sma(close, 20)
// @pf-trace a=s
// @pf-trace b=s2
''',
        "s", ("N", "15"), id="input-integer-product",
    ),
    pytest.param(
        '''//@version=6
strategy("fdiv")
poles = input.int(4, "Poles")
lenv = int(math.round(10 * math.pow(2.0, 2 / poles)))
observed = ta.ema(close, lenv)
plot(observed)
''',
        '''//@version=6
strategy("fdiv")
poles = input.int(4, "Poles")
lenv = int(math.round(10 * math.pow(2.0, 2 / poles)))
observed = ta.ema(close, 14)
plot(observed)
''',
        '''//@version=6
strategy("fdiv")
poles = input.int(4, "Poles")
lenv = int(math.round(10 * math.pow(2.0, 2 / poles)))
observed = ta.ema(close, 20)
plot(observed)
''',
        "observed", ("Poles", "2"), id="rounded-integer-length",
    ),
    pytest.param(
        '''//@version=6
strategy("var-scalar-len")
barsPerHour = input.int(60, "Bars Per Hour")
var int _tfSec = timeframe.in_seconds()
lenv = math.max(1, math.round(barsPerHour * 60.0 / _tfSec))
observed = ta.sma(close, lenv)
plot(observed)
''',
        '''//@version=6
strategy("var-scalar-len")
barsPerHour = input.int(60, "Bars Per Hour")
var int _tfSec = timeframe.in_seconds()
lenv = math.max(1, math.round(barsPerHour * 60.0 / _tfSec))
observed = ta.sma(close, 4)
plot(observed)
''',
        '''//@version=6
strategy("var-scalar-len")
barsPerHour = input.int(60, "Bars Per Hour")
var int _tfSec = timeframe.in_seconds()
lenv = math.max(1, math.round(barsPerHour * 60.0 / _tfSec))
observed = ta.sma(close, 8)
plot(observed)
''',
        "observed", ("Bars Per Hour", "120"), id="timeframe-derived-integer-length",
    ),
)


@pytest.mark.parametrize("source,control,override", _REPORTED_CASES)
def test_float_setting_matches_equivalent_input_expression(source, control, override):
    actual = _observe(source, ("level",), override)
    expected = _observe(control, ("level",), override)
    for actual_run, expected_run in zip(actual, expected):
        _assert_equal_values(_column(actual_run, 0), _column(expected_run, 0))
    _assert_override_moves_values(actual)
    _assert_override_moves_values(expected)


def _setting_runs():
    for case in _SETTING_CASES:
        yield pytest.param(case.source, None, id=case.name + "-default")
        if case.override is not None:
            before, after = case.control_change
            assert case.source.count(before) == 1
            yield pytest.param(
                case.source.replace(before, after), case.override,
                id=case.name + "-override",
            )


@pytest.mark.parametrize("source,override", tuple(_setting_runs()))
def test_float_setting_matches_literal_indicator(source, override):
    runs = _observe(source, _traced_members(source), override)
    observed = runs[-1]
    _assert_equal_values(_column(observed, 0), _column(observed, 1))
    if override is not None:
        _assert_override_moves_values(runs)


@pytest.mark.parametrize("source,expression", _REFUSED_CASES)
def test_unrenderable_float_setting_keeps_its_diagnostic(source, expression):
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E5009"
    assert diagnostic.message == (
        f"Unsupported TA constructor length '{expression}' for ta::BB: "
        "it is neither a compile-time constant nor derived from an input, "
        "so PineForge cannot size the indicator buffer."
    )
    assert diagnostic.hint == (
        "Use a literal, an input.*() value, or arithmetic over those for TA lengths."
    )


@pytest.fixture(scope="module")
def reference():
    tree = reference_codegen(CPP_REFERENCE)
    assert tree is not None, f"The C++ reference commit is unavailable: {CPP_REFERENCE}"
    return tree


def _assert_reference_cpp(source, reference, tmp_path):
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before
    after = transpile_json(pine)
    assert after["ok"], after
    assert after["cpp"] == before["cpp"]


@pytest.mark.parametrize("source", _IDENTITY_CASES)
def test_other_ta_and_input_scripts_keep_complete_cpp(source, reference, tmp_path):
    _assert_reference_cpp(source, reference, tmp_path)


@pytest.mark.parametrize(
    "source,control,override_control,member,override", _INTEGER_CASES,
)
def test_integer_lengths_keep_complete_cpp_and_values(
    source, control, override_control, member, override, reference, tmp_path,
):
    _assert_reference_cpp(source, reference, tmp_path)
    actual = _observe(source, (member,), override)
    (expected,) = _observe(control, (member,))
    (expected_override,) = _observe(override_control, (member,))
    _assert_equal_values(_column(actual[0], 0), _column(expected, 0))
    _assert_equal_values(_column(actual[1], 0), _column(expected_override, 0))
    _assert_override_moves_values(actual)


@pytest.mark.parametrize("expression", ["int(m)"], ids=["explicit-int"])
def test_integer_missing_setting_becomes_float_na(expression):
    source = _HEADER + f'''m = input.int(3, "Setting")
a = ta.bbw(close, 20, {expression})
b = ta.bbw(close, 20, na)
c = ta.bbw(close, 20, 3.0)
'''
    runs = _observe(source, ("a", "b", "c"), ("Setting", "-2147483648"))
    _assert_equal_values(_column(runs[0], 0), _column(runs[0], 2))
    missing = _column(runs[1], 0)
    control = _column(runs[1], 1)
    assert all(math.isnan(value) for value in control)
    assert all(math.isnan(value) for value in missing)
    assert all(math.isfinite(value) for value in _column(runs[1], 2))


def test_legacy_renderer_keeps_fractional_setting(monkeypatch):
    monkeypatch.setattr(
        CodeGen, "_lower_reset_expr_via_visitor",
        lambda self, expanded, pine_type=PineType.INT: None,
    )
    source = next(
        case.source for case in _SETTING_CASES
        if case.name == "bb-absolute-division"
    )
    (rows,) = _observe(source, _traced_members(source))
    _assert_equal_values(_column(rows, 0), _column(rows, 1))


@pytest.mark.parametrize("precalculate", [False, True], ids=["live", "precalculated"])
def test_nested_float_setting_uses_the_run_value(precalculate):
    source = _HEADER + '''m = input.float(3.0, "Setting")
a = ta.bbw(close, 20, m / 2) + 0.0
b = ta.bbw(close, 20, 1.5) + 0.0
'''
    runs = _observe(
        source, ("a", "b", "_use_precalc"), ("Setting", "5.0"),
        precalculate=precalculate,
    )
    _assert_equal_values(_column(runs[0], 0), _column(runs[0], 1))
    control = source.replace("ta.bbw(close, 20, 1.5)", "ta.bbw(close, 20, 2.5)")
    overridden = _observe(
        control, ("a", "b", "_use_precalc"), ("Setting", "5.0"),
        precalculate=precalculate,
    )
    _assert_equal_values(_column(overridden[1], 0), _column(overridden[1], 1))
    for run_rows in runs + overridden:
        assert all(row[2] == int(precalculate) for row in run_rows)
    _assert_override_moves_values(runs)


@pytest.mark.parametrize("helper", [False, True], ids=["direct", "helper-bound"])
def test_requested_float_setting_matches_literal_indicator(helper):
    body = (
        '''width(float value) => ta.bbw(close, 20, value / 2)
a = request.security(syminfo.tickerid, "15", width(m))
'''
        if helper else
        '''a = request.security(syminfo.tickerid, "15", ta.bbw(close, 20, m / 2))
'''
    )
    source = _HEADER + 'm = input.float(3.0, "Setting")\n' + body + (
        'b = request.security(syminfo.tickerid, "15", ta.bbw(close, 20, 1.5))\n'
    )
    runs = _observe(source, ("a", "b"), ("Setting", "5.0"))
    _assert_equal_values(_column(runs[0], 0), _column(runs[0], 1))
    control = source.replace("ta.bbw(close, 20, 1.5)", "ta.bbw(close, 20, 2.5)")
    overridden = _observe(control, ("a", "b"), ("Setting", "5.0"))
    _assert_equal_values(_column(overridden[1], 0), _column(overridden[1], 1))
    _assert_override_moves_values(runs)


def test_dynamic_plan_preserves_input_derived_float_factor():
    source = _HEADER + '''m = input.float(3.0, "Setting")
period = syminfo.type == "crypto" ? 10 : 14
[a, direction] = ta.supertrend(m / 2, period)
[b, controlDirection] = ta.supertrend(1.5, period)
'''
    assert "pineforge::source::PineSupertrend" in transpile(source)
    runs = _observe(source, ("a", "b"), ("Setting", "5.0"))
    _assert_equal_values(_column(runs[0], 0), _column(runs[0], 1))
    control = source.replace("ta.supertrend(1.5, period)", "ta.supertrend(2.5, period)")
    overridden = _observe(control, ("a", "b"), ("Setting", "5.0"))
    _assert_equal_values(_column(overridden[1], 0), _column(overridden[1], 1))
    _assert_override_moves_values(runs)


_VWAP_BAND_FORMS = (
    pytest.param(
        "ta.vwap(close, bar_index % 16 == 0, {multiplier})",
        id="explicit-anchor",
    ),
    pytest.param(
        "ta.vwap(close, stdev_mult={multiplier})",
        id="omitted-anchor",
    ),
)


def _vwap_setting_source(form, requested, default, expression, control):
    source = _HEADER + (
        f'm = input.float({default}, "Setting")\n'
        f"setting = {expression}\n"
        f"[basis, a, lower] = {form.format(multiplier='setting')}\n"
        f"[controlBasis, b, controlLower] = {form.format(multiplier=control)}\n"
    )
    if requested:
        source += (
            'requestedA = request.security(syminfo.tickerid, "60", a)\n'
            'requestedB = request.security(syminfo.tickerid, "60", b)\n'
        )
    return source


def _assert_vwap_setting_values(
    form, requested, default, expression, override, control, override_control,
):
    source = _vwap_setting_source(form, requested, default, expression, control)
    members = ("requestedA", "requestedB", "a") if requested else ("a", "b")
    runs = _observe(source, members, ("Setting", override))
    _assert_equal_values(_column(runs[0], 0), _column(runs[0], 1))
    overridden_source = _vwap_setting_source(
        form, requested, default, expression, override_control,
    )
    overridden = _observe(overridden_source, members, ("Setting", override))
    _assert_equal_values(_column(overridden[1], 0), _column(overridden[1], 1))
    _assert_override_moves_values(runs)
    if requested:
        for rows in (runs[0], overridden[1]):
            assert any(
                not math.isclose(remote, chart, rel_tol=1e-12, abs_tol=1e-10)
                for remote, chart in zip(_column(rows, 0), _column(rows, 2))
            )


@pytest.mark.parametrize("form", _VWAP_BAND_FORMS)
@pytest.mark.parametrize("requested", [False, True], ids=["chart", "requested"])
@pytest.mark.parametrize(
    "default,expression,override",
    [
        pytest.param("3.0", "m / 2", "5.0", id="division"),
        pytest.param("1.5", "math.max(m, 0.5)", "2.5", id="math-maximum"),
    ],
)
def test_vwap_float_multiplier_matches_literal_bands(
    form, requested, default, expression, override,
):
    _assert_vwap_setting_values(
        form, requested, default, expression, override, "1.5", "2.5",
    )


@pytest.mark.parametrize("form", _VWAP_BAND_FORMS)
@pytest.mark.parametrize("requested", [False, True], ids=["chart", "requested"])
def test_vwap_explicit_integer_multiplier_keeps_truncation(form, requested):
    _assert_vwap_setting_values(
        form, requested, "3.5", "int(m)", "5.5", "3.0", "5.0",
    )


@pytest.mark.parametrize("form", _VWAP_BAND_FORMS)
@pytest.mark.parametrize("requested", [False, True], ids=["chart", "requested"])
@pytest.mark.parametrize(
    "default,expression",
    [
        pytest.param("3", "m", id="plain-input-whole-default"),
        pytest.param("1.5", "m", id="plain-input-decimal-default"),
        pytest.param("1.5", "1.5", id="literal"),
    ],
)
def test_vwap_plain_multiplier_keeps_complete_cpp(
    form, requested, default, expression, reference, tmp_path,
):
    source = _vwap_setting_source(form, requested, default, expression, "1.5")
    _assert_reference_cpp(source, reference, tmp_path)
