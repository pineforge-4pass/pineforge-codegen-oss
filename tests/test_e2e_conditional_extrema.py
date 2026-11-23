"""Native comparisons of conditional TA results with explicit sample histories."""

from __future__ import annotations

import json
import math
import subprocess
import sys

import pytest

from tests._compile import skip_if_no_engine_lib
from tests._e2e import build_strategy_library, engine_root, same, transpile_json


BARS = 112
LENGTH = 5
HEADER = """//@version=6
strategy("Window samples", overlay = true)
bool take = bar_index % 11 < 4 or bar_index % 17 == 5
"""
TAIL = """// @pf-trace observed=observed
plot(observed)
"""


def _taken(bar):
    return bar % 11 < 4 or bar % 17 == 5


def _run(root, feed, work, source, inputs, bars):
    work.mkdir()
    pine = work / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    result = transpile_json(pine)
    assert result["ok"], result.get("diagnostics")
    build_strategy_library(result["cpp"], work)
    settings = work / "inputs.json"
    settings.write_text(json.dumps(inputs), encoding="utf-8")
    trace = work / "trace.json"
    completed = subprocess.run(
        [sys.executable, str(root / "scripts" / "run_strategy.py"), str(work),
         "--ohlcv", str(feed), "--no-trim-output", "--inputs-json", str(settings),
         "--trace-json", str(trace), "-o", str(work / "trades.csv")],
        capture_output=True, text=True, timeout=45,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    records = [r for r in json.loads(trace.read_text(encoding="utf-8"))["trace"]
               if r["name"] == "observed"]
    assert [r["bar_index"] for r in records] == list(range(bars))
    return [r["value"] for r in records]


@pytest.fixture
def replay(tmp_path):
    skip_if_no_engine_lib()
    root = engine_root()
    assert root is not None
    assert (root / "scripts" / "run_strategy.py").is_file()
    feed = tmp_path / "chart.csv"
    reached_feed = tmp_path / "reached.csv"
    columns = "timestamp,open,high,low,close,volume"
    rows, reached = [columns], [columns]
    for bar in range(BARS):
        # The first low and high stay extreme across a gap; subsequent
        # lows and highs move independently and are not monotonic.
        low = 50 if bar == 0 else 70 + bar * 13 % 19
        high = 150 if bar == 0 else 110 + bar * 17 % 23
        close = 100 + bar * 7 % 11
        prices = f"100,{high},{low},{close},1000"
        rows.append(f"{1704067200000 + bar * 900000},{prices}")
        if _taken(bar):
            reached.append(f"{1704067200000 + (len(reached) - 1) * 900000},{prices}")
    feed.write_text("\n".join(rows) + "\n", encoding="utf-8")
    reached_feed.write_text("\n".join(reached) + "\n", encoding="utf-8")
    settings = {"input_tf": "15", "script_tf": "15"}

    def run(body, label, overrides=None, *, reached_only=False):
        return _run(root, reached_feed if reached_only else feed, tmp_path / label,
                    HEADER + body + TAIL, settings | (overrides or {}),
                    len(reached) - 1 if reached_only else BARS)

    return run


def _assert_equal(actual, expected, bars=range(BARS), *, tolerance=0.0):
    for bar in bars:
        got, want = actual[bar], expected[bar]
        assert same(got, want) or math.isclose(got, want, rel_tol=tolerance, abs_tol=tolerance), (
            bar, got, want)


def _array_window(src, reducer, *, gate="take", length="n", warmup=False):
    result = f"array.{reducer}(samples)"
    if warmup:
        result = f"array.size(samples) == {length} ? {result} : na"
    return f"""var array<float> samples = array.new<float>()
float observed = na
if {gate}
    array.push(samples, {src})
    if array.size(samples) > {length}
        array.shift(samples)
    observed := {result}
"""


@pytest.mark.parametrize(("ta", "src", "reducer"), [
    pytest.param("lowest", "low", "min", id="lowest"),
    pytest.param("highest", "high", "max", id="highest"),
])
@pytest.mark.parametrize("shape", ["if", "ternary", "switch", "and", "or", "helper"])
def test_conditional_window_matches_array(replay, ta, src, reducer, shape):
    decl = f'int n = input.int({LENGTH}, "Length", minval=1)\n'
    call = f"ta.{ta}({src}, n)"
    actual = {
        "if": f"float observed = na\nif take\n    observed := {call}\n",
        "ternary": f"float observed = take ? {call} : na\n",
        "switch": f"float observed = switch\n    take => {call}\n    => na\n",
        "and": f"float observed = take and {call} [CMP] ? 1.0 : 0.0\n",
        "or": f"float observed = not take or {call} [CMP] ? 1.0 : 0.0\n",
        "helper": (f"window(x, simple int length) => ta.{ta}(x, length)\n"
                   f"float observed = na\nif take\n    observed := window({src}, n)\n"),
    }[shape]
    reference = _array_window(src, reducer)
    if shape in ("and", "or"):
        # Test for the feed's first extreme, not merely for a low/high
        # threshold: an unwritten kernel slot can rescan as zero.
        cmp = "== 50" if reducer == "min" else "== 150"
        actual = actual.replace("[CMP]", cmp)
        reference = reference.replace("observed", "extreme") + (
            f"float observed = {'not take or' if shape == 'or' else 'take and'} "
            f"extreme {cmp} ? 1.0 : 0.0\n"
        )
    got = replay(decl + actual, "call")
    want = replay(decl + reference, "array")
    reaches = [bar for bar in range(BARS) if _taken(bar)]
    _assert_equal(got, want, reaches[LENGTH - 1:])
    _assert_equal(got, want, reaches[:LENGTH - 1])


@pytest.mark.parametrize(("ta", "src", "reducer"), [
    pytest.param("lowest", "low", "min", id="lowest"),
    pytest.param("highest", "high", "max", id="highest"),
])
def test_control_every_bar_window_matches_chart_array(replay, ta, src, reducer):
    decl = f"int n = {LENGTH}\n"
    actual = f"""float every = ta.{ta}({src}, n)
float observed = na
if take
    observed := every
"""
    reference = _array_window(src, reducer, gate="true", warmup=True)
    ref = replay(decl + reference, "array")
    got = replay(decl + actual, "call")
    _assert_equal(got, ref, (bar for bar in range(BARS) if _taken(bar)))


def test_conditional_window_uses_overridden_input_length(replay):
    decl = f'int n = input.int({LENGTH}, "Length", minval=1)\n'
    body = "float observed = na\nif take\n    observed := ta.lowest(low, n)\n"
    reference = _array_window("low", "min", length="6")
    _assert_equal(replay(decl + body, "call", {"Length": 6}),
                  replay(reference, "array"))


def test_conditional_calls_own_separate_windows(replay):
    actual = """window(x) => ta.lowest(x, 5)
float a = take ? window(low) : na
float b = not take ? window(high) : na
float observed = take ? a : b
"""
    reference = (_array_window("low", "min", length="5")
                 .replace("samples", "first").replace("observed", "a")
                 + _array_window("high", "min", gate="not take", length="5")
                 .replace("samples", "second").replace("observed", "b")
                 + "float observed = take ? a : b\n")
    _assert_equal(replay(actual, "call"), replay(reference, "array"))


def test_conditional_calls_keep_their_own_window_lengths(replay):
    """Unequal windows make crossed receivers observable, even without shared state."""
    actual = """float a = take ? ta.lowest(low, 2) : na
float b = not take ? ta.lowest(high, 5) : na
float observed = take ? a : b
"""
    reference = (_array_window("low", "min", length="2")
                 .replace("samples", "first").replace("observed", "a")
                 + _array_window("high", "min", gate="not take", length="5")
                 .replace("samples", "second").replace("observed", "b")
                 + "float observed = take ? a : b\n")
    _assert_equal(replay(actual, "call"), replay(reference, "array"))


@pytest.mark.parametrize(("ta", "src", "reducer"), [
    pytest.param("lowest", "low", "min", id="lowest"),
    pytest.param("highest", "high", "max", id="highest"),
])
def test_requested_window_matches_array_on_requested_bars(replay, ta, src, reducer):
    actual = (f'float observed = request.security(syminfo.tickerid, "60", '
              f'(bar_index % 11 < 4 or bar_index % 17 == 5) ? ta.{ta}({src}, 5) : na, '
              "gaps = barmerge.gaps_on)\n")
    # Capture requested OHLC and run the independent array on the chart once
    # per requested close. No requested TA is used to construct the oracle.
    reference = f"""[sample, ordinal] = request.security(syminfo.tickerid, "60", [{src}, bar_index],
    gaps = barmerge.gaps_on)
""" + _array_window("sample", reducer, gate=(
        "not na(sample) and (ordinal % 11 < 4 or ordinal % 17 == 5)"
    ), length="5")
    _assert_equal(replay(actual, "call"), replay(reference, "array"))


@pytest.mark.parametrize(("ta", "src", "reducer"), [
    pytest.param("lowestbars", "low", "min", id="lowestbars"),
    pytest.param("highestbars", "high", "max", id="highestbars"),
])
def test_conditional_offset_counts_reached_samples(replay, ta, src, reducer):
    actual = f"float observed = na\nif take\n    observed := ta.{ta}({src}, 5)\n"
    reference = _array_window(src, reducer, length="5").replace(
        f"observed := array.{reducer}(samples)",
        f"float best = array.{reducer}(samples)\n"
        "    observed := array.indexof(samples, best) - (array.size(samples) - 1)",
    )
    _assert_equal(replay(actual, "call"), replay(reference, "array"))


@pytest.mark.parametrize("call", [
    pytest.param("ta.sma(close, 4)", id="sma"),
    pytest.param("ta.ema(close, 4)", id="ema"),
    pytest.param("ta.rma(close, 4)", id="rma"),
    pytest.param("ta.wma(close, 4)", id="wma"),
    pytest.param("ta.stdev(close, 4)", id="stdev"),
    pytest.param("ta.cum(close)", id="cum"),
    pytest.param("ta.valuewhen(close > 105, close, 1)", id="valuewhen"),
    pytest.param("ta.barssince(close > 105)", id="barssince"),
    pytest.param("ta.pivothigh(close, 1, 1)", id="pivothigh"),
    pytest.param("ta.crossover(close, 105) ? 1.0 : 0.0", id="crossover"),
    pytest.param("ta.rsi(close, 4)", id="rsi"),
])
def test_control_block_kernels_count_reached_samples(replay, call):
    actual = f"float observed = na\nif take\n    observed := {call}\n"
    reference = f"float observed = {call}\n"
    got = replay(actual, "call")
    want = replay(reference, "dense", reached_only=True)
    _assert_equal([got[bar] for bar in range(BARS) if _taken(bar)], want,
                  range(len(want)), tolerance=1e-11)


@pytest.mark.parametrize("ta", ["change", "mom"])
def test_control_block_source_offsets_use_held_chart_history(replay, ta):
    actual = f"float observed = na\nif take\n    observed := ta.{ta}(close, 3)\n"
    reference = """var float held = na
if take
    held := close
float observed = take ? close - held[3] : na
"""
    _assert_equal(replay(actual, "call"), replay(reference, "held"))
