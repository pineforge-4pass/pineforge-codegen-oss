"""Numeric witnesses for selection writes and TA constructor bindings."""

import math

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("TA length binding")\n'
# Prefix runs expose the member after each bar. Closes are 1..96; a rising
# candle at indices 0,4,... increments a persistent length exactly 24 times.
DRIVER = r"""
#include <cmath>
#include <iomanip>
#include <iostream>
#include <vector>
int main() {
    std::vector<pineforge::Bar> bars;
    for (int i = 0; i < 96; ++i) {
        double c = i + 1, o = c + (i % 4 == 0 ? -1 : 1);
        bars.push_back({o, std::max(o, c), std::min(o, c), c, 1.0,
                        1704067200000LL + 60000LL * i});
        GeneratedStrategy s;
        s.run(bars.data(), i + 1, "1", "1");
        if (!s.last_error().empty()) {
            std::cerr << s.last_error();
            return 1;
        }
        std::cout << std::setprecision(17) << static_cast<double>(s.out) << '\n';
    }
}
"""


def values(body):
    output = run_emitted_tu(transpile(HEADER + body), DRIVER, opt="-O0",
                            label="rev460-426")
    result = list(map(float, output.split()))
    assert len(result) == 96
    return result


def assert_windows(actual, periods):
    for count, (value, length) in enumerate(zip(actual, periods, strict=True), 1):
        if count < length:
            assert math.isnan(value), (count, length, value)
        else:
            assert value == pytest.approx(count - (length - 1) / 2), (count, length)


@pytest.mark.parametrize("init,first", [("5", 5), ("timeframe.in_seconds()", 60)],
                         ids=["literal", "setup"])
def test_persistent_selection_window(init, first):
    actual = values(f"var int p = {init}\nx = if close > open\n    p := p + 1\n"
                    "out = ta.sma(close, p)\n")
    assert_windows(actual, [first + i // 4 + 1 for i in range(96)])


def test_shadow_preserves_global_ema_period():
    # Compare the observed EMA to its independently sized, literal-period peer.
    actual = values("p = 5\nlocalValue() =>\n    int p = 20\n"
                    "    x = if close > open\n        p := p + 1\n    p\n"
                    "localResult = localValue()\nz = ta.ema(close, p)\n"
                    "reference = ta.ema(close, 5)\nout = z - reference\n")
    assert actual[-1] == pytest.approx(0)


@pytest.mark.parametrize("selection", [
    pytest.param("    x = if close > open\n        p := 6\n", id="declaration-value"),
    pytest.param("    switch\n        close > open =>\n            p := 6\n",
                 id="switch-arm"),
])
def test_helper_write_changes_local_window(selection):
    actual = values("f() =>\n    int p = 5\n" + selection
                    + "    ta.sma(close, p)\nout = f()\n")
    assert_windows(actual, [6 if i % 4 == 0 else 5 for i in range(96)])


def test_inner_write_leaves_helper_period():
    # A spelling-only write set discards the outer local as a constructor
    # binding. EMA needs that stable binding; the inner p is unrelated.
    actual = values("f() =>\n    int p = 5\n    if close > open\n"
                    "        int p = 20\n        p := 21\n    ta.ema(close, p)\n"
                    "z = f()\nreference = ta.ema(close, 5)\nout = z - reference\n")
    assert actual[-1] == pytest.approx(0)


def test_global_fold_reads_block_declaration():
    actual = values("n = 5\nbig = input.bool(true)\np = 3\nif big\n"
                    "    n = 7\n    p := n\nout = ta.sma(close, p)\n")
    assert_windows(actual, [7] * 96)


def test_constructor_actual_escapes_other_formal():
    actual = values("b = 7\nf(int a, int b) => ta.sma(close, a)\nout = f(b, 2)\n")
    assert_windows(actual, [7] * 96)


@pytest.mark.parametrize("initializer", ["true", "timeframe.isintraday"],
                         ids=["literal", "setup"])
def test_registration_blames_outer_write(initializer):
    source = (HEADER + f"pick = {initializer}\ntf = if pick\n"
              '    pick := false\n    "60"\nelse\n    "1D"\n'
              "value = request.security(syminfo.tickerid, tf, close)\nplot(value)\n")
    with pytest.raises(CompileError) as caught:
        transpile(source)
    diagnostic, = caught.value.diagnostics
    assert (diagnostic.location.line, diagnostic.location.col) == (5, 5)
    assert diagnostic.code == "PF-E3083"
    assert "outer assignment" in diagnostic.message
