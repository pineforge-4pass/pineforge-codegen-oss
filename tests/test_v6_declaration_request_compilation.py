"""Complete strategy compilation for qualified declarations and request payloads.

These are compile regressions. TradingView value comparisons belong to the
parity population and are separate from this translation-unit check.
"""

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp, skip_if_no_compile_env


COLOR_DECLARATION = """//@version=6
strategy("var const declaration", overlay = true, initial_capital = 10000)
var const color LINE_COLOR = color.blue
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
plot(fast, color = LINE_COLOR)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""

FLOAT_DECLARATION = """//@version=6
strategy("var const float in a calculation", overlay = true, initial_capital = 10000)
var const float BAND = 0.002
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
if fast > slow * (1 + BAND)
    strategy.entry("Long", strategy.long, qty = 0.01)
if fast < slow
    strategy.close("Long")
"""

LOWER_TUPLE = """//@version=6
strategy("security_lower_tf with a tuple", overlay = true, initial_capital = 10000)
[closes, opens] = request.security_lower_tf(syminfo.tickerid, "15", [close, open])
float body = array.size(closes) > 0 ? array.last(closes) - array.first(opens) : close - open
fast = ta.ema(body, 5)
slow = ta.ema(body, 20)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""

MIXED_BLOCKS = """//@version=6
strategy("Repro", overlay = true, initial_capital = 10000, default_qty_value = 0.01)
fast = ta.ema(close, 9)
slow = ta.ema(close, 21)
if ta.crossover(fast, slow)
	if close > open
	    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.entry("Short", strategy.short)
"""

TIME_HELPER = """//@version=6
strategy("Repro", overlay = true, initial_capital = 10000, default_qty_value = 0.01)
valid(x) => na(x) ? 0 : 1
weekStart = request.security(syminfo.tickerid, "D", valid(close) * time("W"))
fast = ta.ema(close, 9)
slow = ta.ema(close, 21)
if ta.crossover(fast, slow) and time >= weekStart
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.entry("Short", strategy.short)
"""

TIMEFRAME_HELPER = """//@version=6
strategy("Repro", overlay = true, initial_capital = 10000, default_qty_value = 0.01)
valid(x) => na(x) ? 0 : 1
dailyScore = request.security(syminfo.tickerid, "D", valid(close) + (timeframe.change("W") ? 1 : 0))
fast = ta.ema(close, 9)
slow = ta.ema(close, 21)
if ta.crossover(fast, slow) and dailyScore == 1
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.entry("Short", strategy.short)
"""

GLOBAL_TUPLE_HELPER = """//@version=6
strategy("Repro", overlay = true, initial_capital = 10000, default_qty_value = 0.01)
channel() =>
    upper = ta.highest(high, 20)
    lower = ta.lowest(low, 20)
    [upper, lower]
[upper, lower] = channel()
valid(x) => na(x) ? 0 : 1
score() => valid(close) + valid(upper)
dailyScore = request.security(syminfo.tickerid, "D", score())
fast = ta.ema(close, 9)
slow = ta.ema(close, 21)
if ta.crossover(fast, slow) and dailyScore == 2
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.entry("Short", strategy.short)
"""

GLOBAL_TUPLE_HISTORY = """//@version=6
strategy("Repro", overlay = true, initial_capital = 10000, default_qty_value = 0.01)
channel() =>
    upper = ta.highest(high, 20)
    lower = ta.lowest(low, 20)
    [upper, lower]
[upper, lower] = channel()
dailyAbove = request.security(syminfo.tickerid, "D", close > upper[1])
fast = ta.ema(close, 9)
slow = ta.ema(close, 21)
if ta.crossover(fast, slow) and dailyAbove
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.entry("Short", strategy.short)
"""

UNUSED_FORWARDER = """//@version=6
strategy("Repro", overlay = true, initial_capital = 10000, default_qty_value = 0.01)
smooth(src, len) => ta.rma(src, len)
smoothTwice(src, len) => smooth(smooth(src, len), len)
fast = smooth(close, 9)
slow = ta.ema(close, 21)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.entry("Short", strategy.short)
"""


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(COLOR_DECLARATION, id="color"),
        pytest.param(FLOAT_DECLARATION, id="float"),
        pytest.param(
            COLOR_DECLARATION.replace("\nvar const ", "\nconst ", 1),
            id="color-control",
        ),
        pytest.param(
            FLOAT_DECLARATION.replace("\nvar const ", "\nconst ", 1),
            id="float-control",
        ),
        pytest.param(
            COLOR_DECLARATION.replace(
                "var const color LINE_COLOR = color.blue",
                "varip const int C = 3",
            ).replace("color = LINE_COLOR", "color = color.blue"),
            id="varip-int",
        ),
    ],
)
def test_persistent_qualified_declaration_compiles(source):
    skip_if_no_compile_env()
    compile_cpp(transpile(source, check_support=True), label="persistent-qualified-declaration")


@pytest.mark.parametrize(
    "declaration",
    [
        pytest.param(
            '[closes, opens] = request.security_lower_tf(syminfo.tickerid, "15", [close, open])',
            id="inline",
        ),
        pytest.param(
            'closeOpen() =>\n    [close, open]\n'
            '[closes, opens] = request.security_lower_tf(syminfo.tickerid, "15", closeOpen())',
            id="helper",
        ),
        pytest.param(
            'closes = request.security_lower_tf(syminfo.tickerid, "15", close)\n'
            'opens = request.security_lower_tf(syminfo.tickerid, "15", open)',
            id="separate-control",
        ),
    ],
)
def test_lower_timeframe_tuple_arrays_compile(declaration):
    skip_if_no_compile_env()
    source = LOWER_TUPLE.replace(LOWER_TUPLE.splitlines()[2], declaration, 1)
    compile_cpp(transpile(source, check_support=True), label="lower-timeframe-tuple-arrays")


@pytest.mark.parametrize(
    "outer,inner",
    [
        pytest.param("\t", "\t    ", id="tab-then-spaces"),
        pytest.param("    ", "    \t", id="spaces-then-tab"),
        pytest.param("\t", "\t\t", id="tabs-control"),
        pytest.param("    ", "        ", id="spaces-control"),
    ],
)
def test_mixed_nested_block_compiles(outer, inner):
    skip_if_no_compile_env()
    source = MIXED_BLOCKS.replace(
        "\tif close > open\n\t    strategy.entry",
        outer + "if close > open\n" + inner + "strategy.entry",
        1,
    )
    compile_cpp(transpile(source, check_support=True), label="mixed-nested-block")


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(TIME_HELPER, id="time-na"),
        pytest.param(
            TIME_HELPER.replace("valid(x) => na(x) ? 0 : 1", "valid(x) => nz(x)"),
            id="time-nz",
        ),
        pytest.param(
            TIME_HELPER.replace("valid(x)", "valid(float x)", 1),
            id="time-typed",
        ),
        pytest.param(
            TIME_HELPER.replace('valid(close) * time("W")', '(na(close) ? 0 : 1) * time("W")'),
            id="time-inline-control",
        ),
        pytest.param(TIMEFRAME_HELPER, id="timeframe-change"),
        pytest.param(GLOBAL_TUPLE_HELPER, id="global-tuple-helper"),
        pytest.param(
            GLOBAL_TUPLE_HELPER.replace(
                "[upper, lower] = channel()\nvalid(x)", "valid(x)", 1,
            ).replace(
                "score() => valid(close) + valid(upper)",
                "score() =>\n    [upper, lower] = channel()\n    valid(close) + valid(upper)",
                1,
            ),
            id="local-tuple-control",
        ),
        pytest.param(GLOBAL_TUPLE_HISTORY, id="global-tuple-history"),
        pytest.param(
            GLOBAL_TUPLE_HISTORY.replace(
                "[upper, lower] = channel()", "upper = ta.highest(high, 20)", 1,
            ),
            id="direct-history-control",
        ),
    ],
)
def test_requested_helpers_keep_parameter_and_tuple_bindings(source):
    skip_if_no_compile_env()
    compile_cpp(transpile(source, check_support=True), label="requested-helper-bindings")


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(UNUSED_FORWARDER, id="uncalled"),
        pytest.param(
            UNUSED_FORWARDER.replace(
                "smoothTwice(src, len) => smooth(smooth(src, len), len)\n", "", 1,
            ),
            id="removed-control",
        ),
        pytest.param(
            UNUSED_FORWARDER + "plot(smoothTwice(close, 9))\n", id="called-control",
        ),
    ],
)
def test_unreachable_length_forwarding_does_not_allocate_ta(source):
    skip_if_no_compile_env()
    compile_cpp(transpile(source, check_support=True), label="unused-length-forwarder")
