"""A write in a block used as a value counts as a reassignment, like one in an if statement."""

import math
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("Reassignment in a value")\n'
STATEMENT = "if close > open\n    p := p + 1"
EXPRESSION = "x = " + STATEMENT + "\n    0\nelse\n    0"
SMA = "z = ta.sma(close, p)"
PERSISTENT = "var int p = 5"


def _source(declaration, block, result):
    return HEADER + f"{declaration}\n{block}\n{result}\n"


# Each close rises from the preceding close, but its own open determines
# whether the Pine block executes. The 5- and 6-bar means always differ.
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
    "OPEN_VALUES", ", ".join(str(c - 1 if up else c + 1) for c, up in zip(CLOSES, UP, strict=True))
)


def _check_bars(cpp, lengths):
    rows = run_emitted_tu(cpp, DRIVER, opt="-O0", label="reassigned-length").splitlines()
    for count, (row, length) in enumerate(zip(rows, lengths, strict=True), start=1):
        out, z = map(float, row.split())
        assert out == length, (count, row)
        if count < length:
            assert math.isnan(z), (count, row)
        else:
            assert z == pytest.approx(sum(CLOSES[count - length:count]) / length), (count, row)


def test_plain_length_is_reinitialized_then_updated_each_bar():
    cpp = transpile(_source("p = 5", EXPRESSION, SMA + "\nout = p"))
    _check_bars(cpp, [5 + int(up) for up in UP])


@pytest.mark.parametrize("block", [STATEMENT, EXPRESSION], ids=["statement", "expression"])
def test_plain_read_after_write_uses_the_variable(block):
    cpp = transpile(_source("p = 5", block, SMA + "\nout = p"))
    assert "out = p;" in cpp
    assert "out = 5;" not in cpp


def _refusal(source):
    with pytest.raises(CompileError) as caught:
        transpile(source)
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code.startswith("PF-E50")
    assert "Unsupported TA constructor length" in diagnostic.message
    return diagnostic


@pytest.mark.parametrize(
    "declaration,name,result",
    [
        pytest.param(PERSISTENT, "p", "z = ta.sma(close, p + input.int(0))", id="input-offset"),
        pytest.param(PERSISTENT, "p", "z = ta.sma(close, p * timeframe.multiplier)", id="tf-factor"),
        pytest.param("var int n = timeframe.in_seconds()", "n", "z = ta.sma(close, n)", id="tf-init"),
        pytest.param(PERSISTENT, "p", "z = ta.ema(close, p)", id="ema"),
        pytest.param(PERSISTENT, "p", "[mid, hi, lo] = ta.bb(close, p, 2)", id="bb"),
        pytest.param(PERSISTENT, "p", "z = ta.pivothigh(high, p, p)", id="pivothigh"),
    ],
)
def test_persistent_length_refused_like_statement(declaration, name, result):
    statement = f"if close > open\n    {name} := {name} + 1"
    expression = f"x = {statement}\n    0\nelse\n    0"
    reference = _refusal(_source(declaration, statement, result))
    actual = _refusal(_source(declaration, expression, result))
    assert actual.code == reference.code


@pytest.mark.parametrize(
    "result,member_type,fixed_ctor",
    [
        pytest.param("z = ta.highest(close, p)", "SeriesHighest", r"_ta_highest_\d+\(5\)", id="highest"),
        pytest.param("[st, dir] = ta.supertrend(3, p)", "PineSupertrend",
                     r"_ta_supertrend_\d+\(3,\s*5\)", id="supertrend"),
    ],
)
def test_dynamic_member_like_statement(result, member_type, fixed_ctor):
    for block in (STATEMENT, EXPRESSION):
        cpp = transpile(_source(PERSISTENT, block, result))
        assert f"pineforge::source::{member_type} " in cpp
        assert re.search(fixed_ctor, cpp) is None


@pytest.mark.parametrize(
    "block",
    [
        pytest.param("x = switch\n    close > open =>\n        p := p + 1\n        0\n    => 0",
                     id="switch"),
        pytest.param("x = if close > open\n    y = if close > low\n        p += 1\n"
                     "        0\n    else\n        0\n    y\nelse\n    0", id="nested-if-compound"),
        pytest.param("var int x = 0\nx := " + STATEMENT + "\n    0\nelse\n    0",
                     id="reassignment-value"),
        pytest.param("[u, v] = " + STATEMENT + "\n    [1, 2]\nelse\n    [3, 4]",
                     id="tuple-value"),
    ],
)
def test_persistent_sma_refused_across_value_positions(block):
    diag = _refusal(_source(PERSISTENT, block, SMA))
    assert diag.code == "PF-E5092"
    assert "Unsupported TA constructor length 'p' for ta.sma" in diag.message
    # The literal-only guard on the base already handles the bare name. An
    # arithmetic use distinguishes the shared proof for each block position.
    result = "z = ta.sma(close, p + input.int(0))"
    assert _refusal(_source(PERSISTENT, block, result)).code == _refusal(
        _source(PERSISTENT, STATEMENT, result)
    ).code


@pytest.mark.parametrize(
    "block",
    [
        pytest.param("var int q = 0\nx = if close > open\n    q := q + 1\n    0\nelse\n    0",
                     id="different-target"),
        pytest.param("x = if close > open\n    p\nelse\n    0", id="read-only"),
    ],
)
def test_unmodified_length_keeps_fixed_sma(block):
    cpp = transpile(_source(PERSISTENT, block, SMA + "\nout = p"))
    assert re.search(r"_ta_sma_\d+\(5\)", cpp)
    _check_bars(cpp, [5] * len(CLOSES))
