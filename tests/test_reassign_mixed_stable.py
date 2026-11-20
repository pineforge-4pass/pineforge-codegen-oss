"""A stable reassignment fold must account for every write to its scalar."""

import math
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("Mixed stable reassignment")\n'
SMA = "z = ta.sma(close, p)"

CLOSES = (10, 11, 14, 16, 17, 19, 22, 23, 25, 28, 30, 31)
UP = (True, False, False, True, False, True, True, False, True, False, False, True)

DRIVER = r"""
#include <iomanip>
#include <iostream>
#include <vector>
int main() {
    const double closes[] = {CLOSE_VALUES};
    const double opens[] = {OPEN_VALUES};
    std::vector<pineforge::Bar> bars(sizeof(closes) / sizeof(closes[0]));
    for (std::size_t i = 0; i < bars.size(); ++i) {
        bars[i].open = opens[i];
        bars[i].high = closes[i] + 1;
        bars[i].low = closes[i] - 1;
        bars[i].close = closes[i];
        bars[i].volume = 1;
        bars[i].timestamp = 1704067200000LL + static_cast<long long>(i) * 3600000LL;
    }
    for (std::size_t count = 1; count <= bars.size(); ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars.data(), count, "60", "60");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 1;
        }
        std::cout << std::setprecision(17) << strategy.out << ' ' << strategy.z << "\n";
    }
}
""".replace("CLOSE_VALUES", ", ".join(map(str, CLOSES))).replace(
    "OPEN_VALUES",
    ", ".join(
        str(close - 1 if up else close + 1)
        for close, up in zip(CLOSES, UP, strict=True)
    ),
)


def _source(body: str, declaration: str = "p = 5") -> str:
    return HEADER + f"{declaration}\n{body}\n{SMA}\nout = p\n"


def _check_bars(cpp: str, lengths: list[int], label: str) -> None:
    rows = run_emitted_tu(cpp, DRIVER, opt="-O0", label=label).splitlines()
    for count, (row, length) in enumerate(zip(rows, lengths, strict=True), start=1):
        out, z = map(float, row.split())
        assert out == length, (count, row)
        if count < length:
            assert math.isnan(z), (count, row)
        else:
            expected = sum(CLOSES[count - length:count]) / length
            assert z == pytest.approx(expected), (count, row)


def _up_lengths() -> list[int]:
    return [5 + int(up) for up in UP]


def test_expression_write_is_not_hidden_by_stable_if_fold():
    body = """if big
    p := 6
x = if close > open
    p := p + 1
    0
else
    0"""
    cpp = transpile(_source(body, "big = input.bool(false, \"Big\")\np = 5"))
    _check_bars(cpp, _up_lengths(), "mixed-stable-expression")


def test_switch_expression_write_is_not_hidden_by_stable_if_fold():
    body = """if big
    p := 6
x = switch
    close > open =>
        p := p + 1
        0
    => 0"""
    cpp = transpile(_source(body, "big = input.bool(false, \"Big\")\np = 5"))
    _check_bars(cpp, _up_lengths(), "mixed-stable-switch-expression")


def test_switch_statement_write_is_not_ignored_by_stable_if_fold():
    body = """if big
    p := 6
switch bar_index % 2
    0 =>
        p := p + 1
    => na"""
    expected = [5 + int(index % 2 == 0) for index in range(len(CLOSES))]
    cpp = transpile(_source(body, "big = input.bool(false, \"Big\")\np = 5"))
    _check_bars(cpp, expected, "mixed-stable-switch-statement")


def test_for_statement_write_is_not_ignored_by_stable_if_fold():
    body = """if big
    p := 6
for i = 1 to 2
    p := p + i"""
    cpp = transpile(_source(body, "big = input.bool(false, \"Big\")\np = 5"))
    _check_bars(cpp, [8] * len(CLOSES), "mixed-stable-for")


def test_nested_expression_write_is_not_hidden_by_stable_if_fold():
    body = """if big
    p := 6
else
    x = if close > open
        p := p + 1
        0
    else
        0"""
    cpp = transpile(_source(body, "big = input.bool(false, \"Big\")\np = 5"))
    _check_bars(cpp, _up_lengths(), "mixed-stable-nested-expression")


def test_while_statement_write_is_not_ignored_by_stable_if_fold():
    body = """if big
    p := 6
while p < 8
    p := p + 1"""
    cpp = transpile(_source(body, "big = input.bool(false, \"Big\")\np = 5"))
    _check_bars(cpp, [8] * len(CLOSES), "mixed-stable-while")


def test_stable_fold_alone_keeps_constructor_and_reset():
    source = _source(
        "if big\n    p := 6",
        'big = input.bool(false, "Big")\np = 5',
    )
    cpp = transpile(source)
    assert re.search(
        r'explicit GeneratedStrategy\(\) : [^\n]*(_ta_sma_\d+)\(1\)'
        r'[\s\S]*\1 = .*get_input_bool\("Big", false\)',
        cpp,
    )
    _check_bars(cpp, [5] * len(CLOSES), "mixed-stable-control")


def test_var_mixed_script_keeps_pf_e5092_refusal():
    source = _source(
        "if big\n    p := 6",
        'big = input.bool(false, "Big")\nvar int p = 5',
    )
    with pytest.raises(CompileError) as caught:
        transpile(source)
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E5092"
    assert diagnostic.message == (
        "Unsupported TA constructor length 'p' for ta.sma: it is neither a "
        "compile-time constant nor derived from an input, so PineForge cannot "
        "size the indicator buffer. TradingView accepts a series length for "
        "ta.sma(); PineForge does not support it yet."
    )


def test_series_condition_keeps_dynamic_length_plan():
    body = """if close > open
    p := p + 1"""
    cpp = transpile(_source(body))
    _check_bars(cpp, _up_lengths(), "mixed-stable-series-condition")
