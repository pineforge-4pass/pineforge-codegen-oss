"""A copied accumulator's history belongs to the requested clock.

The reporter strategy is complete and unchanged. Variants change only the
binding or call form under test. Native observations use the repository's
compile-and-run harness and real engine aggregation with changing chart-candle
directions. The arithmetic oracle uses the input candles, not emitted C++.
"""

from __future__ import annotations

import math

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu, skip_if_no_engine_lib


REPORTER = """//@version=6
strategy("security history of a var alias", overlay = true, initial_capital = 10000)
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
var float acc = 0.0
acc += close > open ? volume : -volume
accAlias = acc
impulse = accAlias - accAlias[1]
htf = request.security(syminfo.tickerid, "240", impulse)
plot(htf)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""

DIRECT = REPORTER.replace(
    "accAlias = acc\nimpulse = accAlias - accAlias[1]\n",
    "impulse = acc - acc[1]\n",
)
OUTSIDE = REPORTER.replace(
    'htf = request.security(syminfo.tickerid, "240", impulse)\nplot(htf)',
    "plot(accAlias - accAlias[1])",
)
TWO_ALIASES = REPORTER.replace(
    "impulse = accAlias - accAlias[1]",
    "secondAlias = accAlias\nimpulse = secondAlias - secondAlias[1]",
)
THREE_ALIASES = TWO_ALIASES.replace(
    "impulse = secondAlias - secondAlias[1]",
    "thirdAlias = secondAlias\nimpulse = thirdAlias - thirdAlias[1]",
)
LOCAL_SHADOW = REPORTER.replace(
    "fast = ta.ema",
    "shadow() =>\n"
    "    accAlias = close * 2.0\n"
    "    accAlias - accAlias[1]\n"
    "fast = ta.ema",
).replace('"240", impulse)', '"240", impulse + shadow())')
PARAMETER_SHADOW = REPORTER.replace(
    "fast = ta.ema",
    "shadow(float accAlias) => accAlias - accAlias[1]\nfast = ta.ema",
).replace('"240", impulse)', '"240", impulse + shadow(close))')
NO_HISTORY = REPORTER.replace(
    "impulse = accAlias - accAlias[1]", "impulse = accAlias"
)
INPUT_UPDATE = REPORTER.replace(
    "var float acc", 'stepScale = input.float(1.75, "Step scale")\nvar float acc'
).replace(
    "acc += close > open ? volume : -volume",
    "acc += (close > open ? volume : -volume) * stepScale",
)
NAMED = REPORTER.replace(
    'request.security(syminfo.tickerid, "240", impulse)',
    'request.security(expression = impulse, timeframe = "240", symbol = syminfo.tickerid)',
)
DIRECT_PAYLOAD = REPORTER.replace(
    'request.security(syminfo.tickerid, "240", impulse)',
    'request.security(syminfo.tickerid, "240", accAlias - accAlias[1])',
)
COPY_BEFORE_UPDATE = REPORTER.replace(
    "accAlias = acc\n", "accAlias = acc\nacc += close - open\n",
)
COPY_VALUE_BEFORE_UPDATE = COPY_BEFORE_UPDATE.replace(
    "impulse = accAlias - accAlias[1]", "impulse = accAlias",
)
COPY_CHAIN_BEFORE_UPDATE = TWO_ALIASES.replace(
    "accAlias = acc\n", "accAlias = acc\nacc += close - open\n",
)

# Each group is one requested 4h candle, made from four 1h chart candles.
# The completed signs alternate, and developing candles can change sign.
CHILD_CLOSES = (
    (105.0, 110.0, 109.0, 108.0),
    (110.0, 100.0, 95.0, 90.0),
    (95.0, 100.0, 109.0, 110.0),
    (115.0, 104.0, 103.0, 103.0),
    (107.0, 120.0, 122.0, 123.0),
    (125.0, 110.0, 100.0, 95.0),
    (96.0, 98.0, 102.0, 102.0),
    (104.0, 100.0, 98.0, 100.0),
)
HOUR_MS = 60 * 60 * 1000


def chart_bars():
    open_ = 100.0
    for i, close in enumerate(v for group in CHILD_CLOSES for v in group):
        yield (open_, max(open_, close) + 1.0, min(open_, close) - 1.0,
               close, float(i + 1), i * HOUR_MS)
        open_ = close


def native_driver(*, outside: bool = False, override: float | None = None) -> str:
    bars = list(chart_bars())
    literals = ",\n".join(
        "        Bar{" + ", ".join(repr(v) for v in row) + "}" for row in bars
    )
    if outside:
        observer = """
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        std::cout << "chart " << bar.timestamp << " 1 "
                  << bar.open << ' ' << bar.close << ' ' << bar.volume
                  << ' ' << static_cast<double>(impulse) << '\\n';
    }
"""
    else:
        observer = """
    void evaluate_security(int sec_id, const Bar& bar, bool complete) override {
        GeneratedStrategy::evaluate_security(sec_id, bar, complete);
        if (sec_id == 0) {
            std::cout << "requested " << bar.timestamp << ' ' << complete
                      << ' ' << bar.open << ' ' << bar.close << ' ' << bar.volume
                      << ' ' << _req_sec_0 << '\\n';
        }
    }

    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        std::cout << "chart " << bar.timestamp << " 1 "
                  << bar.open << ' ' << bar.close << ' ' << bar.volume
                  << ' ' << static_cast<double>(impulse)
                  << ' ' << static_cast<double>(htf) << '\\n';
    }
"""
    setting = "" if override is None else f'    strategy.set_input("Step scale", "{override}");\n'
    return (
        "#include <iomanip>\n#include <iostream>\n"
        "struct AliasHistoryObserver : GeneratedStrategy {\n" + observer + "};\n"
        "int main() {\n    std::cout << std::setprecision(17);\n"
        "    AliasHistoryObserver strategy;\n" + setting
        + "    Bar bars[] = {\n" + literals + "\n    };\n"
        + f'    strategy.run(bars, {len(bars)}, "60", "60");\n'
        + "    return 0;\n}\n"
    )


CASES = (
    pytest.param(REPORTER, "difference", 1.0, None, id="R01-reporter"),
    pytest.param(DIRECT, "difference", 1.0, None, id="C01-direct"),
    pytest.param(OUTSIDE, "outside", 1.0, None, id="C02-outside"),
    pytest.param(TWO_ALIASES, "difference", 1.0, None, id="A01-two-aliases"),
    pytest.param(THREE_ALIASES, "difference", 1.0, None, id="A02-three-aliases"),
    pytest.param(LOCAL_SHADOW, "local-shadow", 1.0, None, id="S01-local-shadow"),
    pytest.param(PARAMETER_SHADOW, "parameter-shadow", 1.0, None, id="S02-parameter-shadow"),
    pytest.param(NO_HISTORY, "cumulative", 1.0, None, id="N01-no-history"),
    pytest.param(INPUT_UPDATE, "difference", 1.75, None, id="I01-input-default"),
    pytest.param(INPUT_UPDATE, "difference", 2.5, 2.5, id="I02-input-override"),
    pytest.param(NAMED, "difference", 1.0, None, id="K01-named"),
    pytest.param(DIRECT_PAYLOAD, "difference", 1.0, None, id="H02-direct-payload"),
    pytest.param(COPY_BEFORE_UPDATE, "snapshot-history", 1.0, None, id="M01-copy-before-update"),
    pytest.param(COPY_VALUE_BEFORE_UPDATE, "snapshot-value", 1.0, None, id="M02-copy-value"),
    pytest.param(COPY_CHAIN_BEFORE_UPDATE, "snapshot-history", 1.0, None, id="M03-copy-chain"),
)


def expected_values(groups, mode, scale):
    """Pine value copies from the input candles, including subsequent stores."""
    result = []
    accumulator = 0.0
    previous_copy = previous_close = None
    for group in groups:
        open_, close = group[0][0], group[-1][3]
        volume = sum(bar[4] for bar in group)
        accumulator += (volume if close > open_ else -volume) * scale
        copied = accumulator
        if mode.startswith("snapshot-"):
            accumulator += close - open_
        if mode in {"cumulative", "snapshot-value"}:
            value = copied
        else:
            value = math.nan if previous_copy is None else copied - previous_copy
        if mode == "local-shadow" and previous_close is not None:
            value += 2.0 * (close - previous_close)
        if mode == "parameter-shadow" and previous_close is not None:
            value += close - previous_close
        result.append(value)
        previous_copy, previous_close = copied, close
    return result


@pytest.mark.parametrize("source, mode, scale, override", CASES)
def test_alias_history_native_values(source, mode, scale, override):
    skip_if_no_engine_lib()
    cpp = transpile(source)
    output = run_emitted_tu(
        cpp, native_driver(outside=mode == "outside", override=override),
        opt="-O0", label="requested accumulator alias history",
    )
    print(output, end="")
    rows = []
    chart_rows = []
    for line in output.splitlines():
        parts = line.split()
        if mode != "outside" and parts and parts[0] == "chart":
            assert len(parts) == 8 and parts[2] == "1", line
            chart_rows.append((int(parts[1]), *(float(v) for v in parts[3:])))
            continue
        assert len(parts) == 7, line
        context, timestamp, complete, open_, close, volume, value = parts
        assert context == ("chart" if mode == "outside" else "requested"), line
        assert complete in {"0", "1"}, line
        rows.append((int(timestamp), complete == "1", float(open_), float(close),
                     float(volume), float(value)))

    bars = list(chart_bars())
    groups = [bars[i:i + 4] for i in range(0, len(bars), 4)]
    if mode == "outside":
        groups = [[bar] for bar in bars]
    completed = [row for row in rows if row[1]]
    assert [row[0] for row in completed] == [group[0][-1] for group in groups]
    assert len(rows) >= len(groups) > 1

    expected_requested = expected_values(groups, mode, scale)
    for i, (row, group, wanted) in enumerate(zip(completed, groups, expected_requested)):
        open_, close = group[0][0], group[-1][3]
        volume = sum(bar[4] for bar in group)
        assert row[2:5] == pytest.approx((open_, close, volume))
        if math.isnan(wanted):
            assert math.isnan(row[5]), row
        else:
            assert row[5] == pytest.approx(wanted), (i, row, wanted)

    if mode != "outside":
        assert [row[0] for row in chart_rows] == [bar[-1] for bar in bars]
        chart_mode = "difference" if mode.endswith("-shadow") else mode
        impulses = expected_values([[bar] for bar in bars], chart_mode, scale)
        for i, (row, bar, chart_impulse) in enumerate(zip(chart_rows, bars, impulses)):
            open_, _, _, close, volume, _ = bar
            assert row[1:4] == pytest.approx((open_, close, volume))
            completed_slot = (i + 1) // 4 - 1
            held = (expected_requested[completed_slot]
                    if completed_slot >= 0 else math.nan)
            for actual, wanted in (
                (row[4], chart_impulse),
                (row[5], held),
            ):
                if math.isnan(wanted):
                    assert math.isnan(actual), (i, row, wanted)
                else:
                    assert actual == pytest.approx(wanted), (i, row, wanted)
