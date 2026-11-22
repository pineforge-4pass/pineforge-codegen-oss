"""Conditional history samples local state before choosing what to read."""

from __future__ import annotations

from functools import partial
import math
import re
import subprocess
from types import SimpleNamespace

import pytest

from pineforge_codegen import transpile
from tests import _e2e
from tests._compile import skip_if_no_engine_lib
from tests._e2e import Build, execute_all, ok, per_bar_mismatches, same


HEADER = '//@version=6\nstrategy("Conditional call history")\n'
SMOOTH = """smooth(float x, float bias) =>
    float state = na
    state := na(state[1]) ? x + bias : (state[1] + x + bias) / 2.0
    state
"""
COUNTER = """cnt(float step) =>
    o = float(na)
    o := na(o[1]) ? 0 : o[1] + step
    o
"""
BANNER = ("// Pine v6 lazy operand: a call read at an offset runs on every "
          "execution, only the read is gated.")


def _ternary(offset):
    return (
        f"x = take ? smooth(close, 0.0)[{offset}] : smooth(high, 100.0)[{offset}]\n",
        "a = smooth(close, 0.0)\nb = smooth(high, 100.0)\n"
        f"x = take ? a[{offset}] : b[{offset}]\n",
    )


@pytest.fixture
def engine(monkeypatch):
    skip_if_no_engine_lib()
    root = _e2e.engine_root()
    assert root is not None
    if not (root / "scripts" / "run_strategy.py").is_file():
        pytest.skip(f"engine checkout {root} has no scripts/run_strategy.py")
    # Bound transpile, compilation and each native execution independently.
    monkeypatch.setattr(
        _e2e, "subprocess", SimpleNamespace(run=partial(subprocess.run, timeout=120))
    )
    return root


def _feed(path, closes, choices):
    rows = ["timestamp,open,high,low,close,volume\n"]
    for bar, (close, take) in enumerate(zip(closes, choices, strict=True)):
        stamp = 1_704_067_200_000 + bar * 900_000
        open_ = close - 0.5 if take else close + 0.5
        rows.append(f"{stamp},{open_},{close + 1},{close - 1},{close},1000\n")
    path.write_text("".join(rows), encoding="utf-8")
    return path


def _build(definitions, body):
    return Build(
        HEADER + definitions + "take = close > open\n" + body + "// @pf-trace x=x\n",
        trace=True, overrides={"input_tf": "15", "script_tf": "15"},
    )


def _trace(runs, name, bars):
    rows = ok(runs, name).traces["override"]
    assert all(row["name"] == "x" for row in rows)
    assert [row["bar_index"] for row in rows] == list(range(bars)), name
    return rows


@pytest.mark.parametrize(
    ("branch", "call_first"),
    [
        pytest.param(*_ternary(0), id="ternary-zero"),
        pytest.param(*_ternary(1), id="ternary-one"),
        pytest.param(
            """pick(int offset) =>
    bool choose = close > open
    choose ? smooth(close, 0.0)[offset] : smooth(high, 100.0)[offset]
x = pick(1 + bar_index % 3)
""",
            """pick(int offset) =>
    bool choose = close > open
    a = smooth(close, 0.0)
    b = smooth(high, 100.0)
    choose ? a[offset] : b[offset]
x = pick(1 + bar_index % 3)
""",
            id="function-dynamic-offset",
        ),
        pytest.param(
            "x = take ? (bar_index % 3 == 1 ? smooth(close, 0.0)[1] : -1.0) "
            ": smooth(high, 100.0)[1]\n",
            "a = smooth(close, 0.0)\nb = smooth(high, 100.0)\n"
            "x = take ? (bar_index % 3 == 1 ? a[1] : -1.0) : b[1]\n",
            id="nested-ternary",
        ),
        pytest.param(
            "x = (not take and nz(smooth(close, 0.0)[1], -1.0) > close - 40.0) "
            "? 1.0 : 0.0\n",
            "a = smooth(close, 0.0)\n"
            "x = (not take and nz(a[1], -1.0) > close - 40.0) ? 1.0 : 0.0\n",
            id="and-right",
        ),
        pytest.param(
            "x = (take or nz(smooth(close, 0.0)[1], -1.0) > close - 40.0) ? 1.0 : 0.0\n",
            "a = smooth(close, 0.0)\n"
            "x = (take or nz(a[1], -1.0) > close - 40.0) ? 1.0 : 0.0\n",
            id="or-right",
        ),
        pytest.param(
            "x = take ? ta.sma(close, 5)[1] : ta.highest(high, 3)[1]\n",
            "a = ta.sma(close, 5)\nb = ta.highest(high, 3)\nx = take ? a[1] : b[1]\n",
            id="control-builtin-ta-already-sampled",
        ),
    ],
)
def test_conditional_history_matches_call_first(engine, tmp_path, branch, call_first):
    bars = 64
    choices = [i % 11 in {0, 3, 4, 7, 8, 9} for i in range(bars)]
    closes = [100.0 + (i * 37 % 97) for i in range(bars)]
    feed = _feed(tmp_path / "chart.csv", closes, choices)
    runs = execute_all(engine, feed, tmp_path, {
        "conditional": _build(SMOOTH, branch),
        "call-first": _build(SMOOTH, call_first),
    })
    subject = _trace(runs, "conditional", bars)
    control = _trace(runs, "call-first", bars)
    assert len({row["value"] for row in control if math.isfinite(row["value"])}) > 1
    compared, missed, first = per_bar_mismatches(subject, control)
    assert compared == bars
    assert missed == 0, first


def test_control_plain_calls_run_only_on_selected_bars(engine, tmp_path):
    # The left call takes 10, 30, 50; the right takes 1100, 1200, 1300.
    feed = _feed(tmp_path / "chart.csv", [10, 100, 200, 30, 50, 300],
                 [True, False, False, True, True, False])
    runs = execute_all(engine, feed, tmp_path, {
        "plain": _build(SMOOTH, "x = take ? smooth(close, 0.0) : smooth(close, 1000.0)\n")
    })
    assert [row["value"] for row in _trace(runs, "plain", 6)] == [
        10.0, 1100.0, 1150.0, 20.0, 35.0, 1225.0,
    ]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            """x = take ? placing(close)[1] : -1.0
if bar_index == 4
    strategy.close("L")
""",
            id="control-order-stays-in-its-branch",
        ),
    ],
)
def test_control_order_stays_in_its_branch(engine, tmp_path, body):
    # CONTROL: a callee that places an order keeps its in-branch lowering, so
    # the bar-1 entry is never reached. Sampling it on every bar would submit
    # the entry. TradingView behavior for such a callee is not measured.
    placing = """placing(float x) =>
    float seen = na
    seen := nz(seen[1]) + 1.0
    if bar_index == 1
        strategy.entry("L", strategy.long, qty = 1)
    x
"""
    bars = 8
    feed = _feed(tmp_path / "chart.csv", [100.0 + 2 * i for i in range(bars)],
                 [i % 2 == 0 for i in range(bars)])
    runs = execute_all(engine, feed, tmp_path, {"ordering": _build(placing, body)})
    assert _e2e.trade_count(ok(runs, "ordering").trades["override"]) == 0


@pytest.mark.parametrize(
    ("body", "call_first"),
    [
        pytest.param(
            """var array<float> buf = array.new<float>(1, -2.0)
float picked = -1.0
if take
    array.set(buf, 0, close)
    picked := pass(array.get(buf, 0))[0]
x = picked
""",
            """var array<float> buf = array.new<float>(1, -2.0)
float picked = -1.0
if take
    array.set(buf, 0, close)
    v = array.get(buf, 0)
    p = pass(v)
    picked := p[0]
x = picked
""",
            id="control-read-after-mutation-stays",
        ),
    ],
)
def test_control_read_after_mutation_stays(engine, tmp_path, body, call_first):
    # CONTROL: inside the if block the read follows the write. Sampling the
    # call before the block would read the buffer before the write.
    passthrough = "pass(float x) => x\n"
    bars = 8
    feed = _feed(tmp_path / "chart.csv", [100.0 + 2 * i for i in range(bars)],
                 [i % 2 == 0 for i in range(bars)])
    runs = execute_all(engine, feed, tmp_path, {
        "mutation": _build(passthrough, body),
        "call-first": _build(passthrough, call_first),
    })
    subject = _trace(runs, "mutation", bars)
    control = _trace(runs, "call-first", bars)
    assert len({row["value"] for row in control if math.isfinite(row["value"])}) > 1
    compared, missed, first = per_bar_mismatches(subject, control)
    assert compared == bars
    assert missed == 0, first


def test_control_shared_collection_reader_stays_in_its_arm(engine, tmp_path):
    # CONTROL: the condition writes the buffer that the callee iterates, so
    # the read keeps its in-arm lowering and gives the plain call's values.
    # A plain call runs only on the selected bars, after the write, so each
    # of them adds its own close: 100, 100 + 104, 204 + 108 and 312 + 112;
    # the other bars read -1. Sampling the call before the statement would
    # add what the buffer held before the write. TradingView's order for
    # this shape is not measured.
    definitions = """var array<float> buf = array.new<float>(1, 0.0)
stamp() =>
    array.set(buf, 0, close)
    close > open
total() =>
    var float acc = 0.0
    for v in buf
        acc += v
    acc
"""
    bars = 8
    feed = _feed(tmp_path / "chart.csv", [100.0 + 2 * i for i in range(bars)],
                 [i % 2 == 0 for i in range(bars)])
    runs = execute_all(engine, feed, tmp_path, {
        "boundary": _build(definitions, "x = stamp() ? total()[0] : -1.0\n"),
        "plain": _build(definitions, "x = stamp() ? total() : -1.0\n"),
    })
    subject = _trace(runs, "boundary", bars)
    control = _trace(runs, "plain", bars)
    assert [row["value"] for row in control] == [
        100.0, -1.0, 204.0, -1.0, 312.0, -1.0, 424.0, -1.0,
    ]
    compared, missed, first = per_bar_mismatches(subject, control)
    assert compared == bars
    assert missed == 0, first


def test_control_strategy_state_reader_stays_in_its_arm(engine, tmp_path):
    # CONTROL: the condition closes the position at once and the callee reads
    # the closed trade count, so the read keeps its in-arm lowering and gives
    # the plain call's values. The entries of bars 0 and 4 fill at the next
    # bar's open. On bars 2 and 6 the close is booked before the plain call
    # runs (tests/test_e2e_closed_trade_fold.py pins that booking), so the
    # call adds a count of 1, then of 2: 1 and 1 + 2; the other bars read -1.
    # Sampling the call before the statement would add the count from before
    # the close. TradingView's order for this shape is not measured.
    definitions = """flatten() =>
    strategy.close("L", immediately = true)
    true
count() =>
    var float seen = 0.0
    seen += strategy.closedtrades
    seen
"""
    entry = 'if bar_index % 4 == 0\n    strategy.entry("L", strategy.long, qty = 1)\n'
    condition = "bar_index % 4 == 2 and flatten()"
    bars = 8
    feed = _feed(tmp_path / "chart.csv", [100.0 + 2 * i for i in range(bars)],
                 [i % 2 == 0 for i in range(bars)])
    runs = execute_all(engine, feed, tmp_path, {
        "boundary": _build(definitions, entry + f"x = {condition} ? count()[0] : -1.0\n"),
        "plain": _build(definitions, entry + f"x = {condition} ? count() : -1.0\n"),
    })
    subject = _trace(runs, "boundary", bars)
    control = _trace(runs, "plain", bars)
    assert [row["value"] for row in control] == [
        -1.0, -1.0, 1.0, -1.0, -1.0, -1.0, 3.0, -1.0,
    ]
    compared, missed, first = per_bar_mismatches(subject, control)
    assert compared == bars
    assert missed == 0, first


def _every_execution(bar, offset=0):
    if bar < offset:
        return math.nan
    return (bar - offset) * (1 if bar % 3 == 0 else 1000)


def _selected_execution(bar):
    return bar // 3 if bar % 3 == 0 else 1000 * (bar - bar // 3 - 1)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(
            "pick() => bar_index % 3 == 0 ? cnt(1)[0] : cnt(1000)[0]\nx = pick()\n",
            _every_execution, id="counter-zero",
        ),
        pytest.param(
            "pick(int n) => bar_index % 3 == 0 ? cnt(1)[n] : cnt(1000)[n]\nx = pick(0)\n",
            _every_execution, id="counter-parameter-offset",
        ),
        pytest.param(
            "pick() => bar_index % 3 == 0 ? cnt(1)[1] : cnt(1000)[1]\nx = pick()\n",
            partial(_every_execution, offset=1), id="counter-one",
        ),
        pytest.param(
            "pick() => bar_index % 3 == 0 ? cnt(1) : cnt(1000)\nx = pick()\n",
            _selected_execution, id="control-counter-plain",
        ),
        pytest.param(
            "pick() =>\n    a = cnt(1)\n    z = cnt(1000)\n"
            "    bar_index % 3 == 0 ? a : z\nx = pick()\n",
            _every_execution, id="control-counter-call-first",
        ),
    ],
)
def test_conditional_history_matches_execution_count(engine, tmp_path, body, expected):
    bars = 64
    feed = _feed(tmp_path / "chart.csv", [100.0] * bars, [False] * bars)
    runs = execute_all(engine, feed, tmp_path, {"counter": _build(COUNTER, body)})
    trace = _trace(runs, "counter", bars)
    for row in trace:
        bar = row["bar_index"]
        assert same(row["value"], expected(bar)), (bar, row["value"], expected(bar))


def _pick_body(cpp):
    match = re.search(r"^    \w+ pick(?:_cs\d+)?\([^;\n]*\) \{\n(.*?)^    \}",
                      cpp, re.MULTILINE | re.DOTALL)
    assert match is not None
    return match[1]


def test_local_call_history_push_precedes_return():
    cpp = transpile(HEADER + COUNTER
                    + "pick() => close > open ? cnt(1)[1] : -1.0\nx = pick()\n")
    body = _pick_body(cpp)
    assert BANNER in body
    before, result = body.split("return ", 1)
    write = re.search(r"\b(_hist_call_\d+)\.push\(cnt(?:_cs\d+)?\(", before)
    assert write is not None
    assert f"{write[1]}[" in result
    assert not re.search(r"\bcnt(?:_cs\d+)?\(", result)


@pytest.mark.parametrize(
    ("prefix", "effect"),
    [
        pytest.param("", 'strategy.close("L")', id="order"),
        pytest.param('write_log() =>\n    log.info("sample")\n    close\n',
                     "observed = write_log()", id="transitive-log"),
        pytest.param("", "varip int count = 0\n    count += 1", id="varip"),
        pytest.param("var array<float> shared = array.new<float>()\n",
                     "array.push(shared, close)", id="collection-write"),
        pytest.param("method sample(float receiver) => receiver\n",
                     "observed = close.sample()", id="unresolved-for-locality"),
        pytest.param("var array<float> shared = array.new<float>(1, 0.0)\n",
                     "for v in shared\n        state += v", id="collection-read"),
        pytest.param("var array<float> shared = array.new<float>(1, 0.0)\n",
                     "own = array.new<float>()\n    array.concat(own, shared)",
                     id="collection-operand-read"),
        pytest.param("var array<float> shared = array.new<float>(1, 0.0)\n",
                     "observed = na(shared[1])", id="collection-history-read"),
        pytest.param("var array<float> shared = array.new<float>(1, 0.0)\n"
                     "var array<float> other = array.new<float>(1, 0.0)\n",
                     "observed = na((close > open ? shared : other)[1])",
                     id="collection-selection-history-read"),
        pytest.param("", "observed = strategy.closedtrades", id="strategy-state-read"),
        pytest.param("inner(int v = strategy.closedtrades) => v\n",
                     "observed = inner()", id="strategy-state-default-read"),
    ],
)
def test_control_excluded_calls_stay_in_their_arm(prefix, effect):
    cpp = transpile(
        HEADER + prefix + "producer() =>\n"
        + "    float state = na\n    state := nz(state[1]) + 1.0\n"
        + f"    {effect}\n    state\n"
        + "pick() => close > open ? producer()[1] : -1.0\nx = pick()\n"
    )
    body = _pick_body(cpp)
    assert BANNER not in cpp
    assert re.search(r"\?.*\[&\].*\bproducer(?:_cs\d+)?\(", body)
