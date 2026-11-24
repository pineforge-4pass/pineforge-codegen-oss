"""A selection writes the same Pine binding in statement and value position."""

import math

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
from tests import _series_sma_oracle as oracle
from tests._compile import run_emitted_tu
from tests.test_callable_ta_forwarded_length import _ctor_periods, _reset_lines


HEADER = '//@version=6\nstrategy("Selection writes")\n'
IF = "x = if close > open\n    p := p + 1\n"
SWITCH = "x = switch\n    close > open => p := p + 1\n    => p\n"
FILENAME = "selection-length.pine"

# The one-minute setup initializer is 60. There are enough bars to see its
# moving window finish warming up, even though it grows on every fourth bar.
DRIVER = r"""
#include <iomanip>
#include <iostream>
#include <vector>
int main() {
    std::vector<Bar> bars;
    for (int i = 0; i < 84; ++i) {
        double close = 10.0 + i;
        double open = close + (i % 4 == 0 ? -1.0 : 1.0);
        bars.push_back(Bar{open, close + 1, close - 1, close, 1.0,
                           1704067200000LL + i * 60000LL});
        GeneratedStrategy strategy;
        strategy.run(bars.data(), bars.size(), "1", "1");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 1;
        }
        std::cout << std::setprecision(17) << strategy.out << " " << strategy.z << "\n";
    }
}
"""


@pytest.mark.parametrize("decl,body,length,initial,persistent,offset", [
    pytest.param("p = 5", IF, "p", 5, False, 0, id="plain-if"),
    pytest.param("var int p = 5", IF, "p", 5, True, 0, id="persistent-literal"),
    pytest.param("var int p = timeframe.in_seconds()", SWITCH, "p",
                 60, True, 0, id="persistent-setup"),
    pytest.param("p = 5", "if timeframe.isminutes\n    p := 6\n" + SWITCH,
                 "p", 6, False, 0, id="mixed-writes"),
    pytest.param("p = 5", "int x = 0\nx := if close > open\n"
                 "    if close > open\n        p := p + 1\n",
                 "p", 5, False, 0, id="nested-reassignment"),
    pytest.param("p = 5", "[u, v] = if close > open\n    p := p + 1\n"
                 "    [p, close]\nelse\n    [p, open]\n",
                 'p + input.int(1, "Extra")', 5, False, 1, id="tuple-derived"),
])
def test_reads_and_sma_follow_each_write(decl, body, length, initial, persistent, offset):
    source = HEADER + decl + "\n" + body + f"out = p\nz = ta.sma(close, {length})\n"
    cpp = transpile(source, filename=FILENAME)
    output = run_emitted_tu(cpp, DRIVER, opt="-O0", label="selection-length")
    rows = [tuple(map(float, line.split())) for line in output.splitlines()]
    assert len(rows) == 84
    p = initial
    for bar, (read, average) in enumerate(rows):
        p = (p if persistent else initial) + (bar % 4 == 0)
        assert read == p, (bar, read, p)
        n = p + offset
        if bar + 1 < n:
            assert math.isnan(average), (bar, n, average)
        else:
            # Mean of the last n consecutive closes, ending at 10 + bar.
            assert average == pytest.approx(10 + bar - (n - 1) / 2, abs=1e-12), (bar, n)


def test_sma_sites_read_on_their_own_side_of_the_write():
    cpp = transpile(HEADER + "p = 5\nout = ta.sma(close, p)\n" + IF + "z = ta.sma(close, p)\n")
    driver = oracle.per_bar_driver(("out", "z"))
    rows = oracle.per_bar_rows(
        run_emitted_tu(cpp, driver, opt="-O0", label="selection-between-sites"), 2)
    for bar, (before, after) in enumerate(rows):
        window = 5 + (oracle.CLOSES[bar] > oracle.OPENS[bar])
        for result, n in ((before, 5), (after, window)):
            if bar + 1 < n:
                assert math.isnan(result), (bar, n)
            else:
                assert result == pytest.approx(sum(oracle.CLOSES[bar + 1 - n:bar + 1]) / n)


@pytest.mark.parametrize("body", [
    pytest.param("p = 5\n" + IF + "z = ta.ema(close, p)\n",
                 id="selection-write"),
    pytest.param("p = input.int(5)\ncalc(int seed) =>\n    p = seed\n"
                 "    x = if close > open\n        p := p + 1\n"
                 "    ta.ema(close, p)\nz = calc(5)\n", id="local-hides-input"),
    pytest.param("p = int(close)\nz = ta.ema(close, p)\n", id="unrelated-ema"),
])
def test_unsupported_length_reports_its_registered_call(body):
    source = HEADER + body
    line, call = next((i, text) for i, text in enumerate(source.splitlines(), 1)
                      if "ta.ema(" in text)
    col = call.index("(", call.index("ta.ema")) + 1
    with pytest.raises(CompileError) as exc:
        transpile(source, filename=FILENAME)
    diagnostic, = exc.value.diagnostics
    assert (diagnostic.level, diagnostic.phase, diagnostic.code) == (
        Level.ERROR, Phase.CODEGEN, "PF-E5009")
    assert diagnostic.message == (
        "Unsupported TA constructor length 'p' for ta::EMA: it is neither a "
        "compile-time constant nor derived from an input, so PineForge cannot "
        "size the indicator buffer.")
    assert diagnostic.hint == (
        "Use a literal, an input.*() value, or arithmetic over those for TA lengths.")
    loc = diagnostic.location
    assert (loc.file, loc.line, loc.col, loc.end_col) == (FILENAME, line, col, col + 1)


def test_length_uses_the_declaration_visible_at_registration():
    source = HEADER + """window = input.int(4, "Global")
pair(float sample, simple int width) =>
    early = ta.rma(sample, window)
    window = width + 1
    late = ta.rma(sample, window)
    early - late
sizeHint = input.int(8, "Width")
out = pair(close, sizeHint)
"""
    cpp = transpile(source)
    assert _ctor_periods(cpp, "rma") == ["4", "9"]
    early, late = _reset_lines(cpp, "rma")
    assert 'get_input_int("Global", 4)' in early
    assert 'get_input_int("Width", 8)' in late


def test_a_formal_cannot_capture_another_formals_actual():
    source = HEADER + """n = input.int(9, "First")
other = input.int(2, "Second")
smooth(float sample, simple int length, simple int n) => ta.rma(sample, length + n)
out = smooth(close, n, other)
"""
    cpp = transpile(source)
    assert _ctor_periods(cpp, "rma") == ["11"]
    reset, = _reset_lines(cpp, "rma")
    assert 'get_input_int("First", 9)' in reset and 'get_input_int("Second", 2)' in reset


def test_local_write_does_not_resize_the_global_ema():
    source = HEADER + """p = input.int(5, "EMA period")
counter(int start) =>
    p = start
    changed = if close > open
        p := p + 1
    p
out = counter(12)
z = ta.ema(close, p)
"""
    cpp = transpile(source)
    assert _ctor_periods(cpp, "ema") == ["5"]
    resets = _reset_lines(cpp, "ema", factory="pineforge::source::PineEma(")
    # First-bar setup and both sides of standalone precalculation size this site.
    assert len(resets) == 3
    assert all('get_input_int("EMA period", 5)' in reset for reset in resets)


def test_imported_v5_rma_keeps_both_callers_lengths():
    library = '''//@version=5
library("Ribbon")
smooth(float sample, simple int count) => ta.rma(sample, count)
export band(float sample, simple int width) =>
    base = width + 1
    span = base * 2
    smooth(sample, span)
'''
    source = """//@version=6
strategy("Two ribbon widths")
import pftest/Ribbon/1 as ribbon
traceBand(float sample, simple int sizeHint) => ribbon.band(sample, sizeHint)
narrow = input.int(3, "Narrow")
wide = input.int(8, "Wide")
a = traceBand(low, narrow)
b = traceBand(high, wide)
plot(a - b)
"""
    cpp = transpile(source, libraries={"pftest/Ribbon/1": library})
    assert _ctor_periods(cpp, "rma") == ["8", "18"]
    first, second = _reset_lines(cpp, "rma")
    assert '(get_input_int("Narrow", 3) + 1) * 2' in first
    assert '(get_input_int("Wide", 8) + 1) * 2' in second
    # Independent fixed-window calls on exactly the same bars: overrides 2
    # and 4 give periods 6 and 10. Neither period occurs in the witness.
    reference = transpile(HEADER + "a = ta.rma(low, 6)\nb = ta.rma(high, 10)\n")
    driver = oracle.per_bar_driver(("a", "b"), (("Narrow", 2), ("Wide", 4)))
    expected = oracle.per_bar_rows(run_emitted_tu(
        reference, oracle.per_bar_driver(("a", "b")), opt="-O0", label="fixed-rma"), 2)
    rows = oracle.per_bar_rows(run_emitted_tu(cpp, driver, opt="-O0", label="borrowed-rma"), 2)
    assert all(math.isfinite(v) for v in expected[-1])
    for actual, fixed in zip(rows, expected, strict=True):
        assert actual == pytest.approx(fixed, nan_ok=True)
