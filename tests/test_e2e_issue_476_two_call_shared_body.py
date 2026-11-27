"""Issue #476, the two-call case: a fractional median below a body two calls share.

``fixtures/open_items_tv/temp_names_array.pine`` passes ten values through one
untyped function, ``fmt(v) => str.tostring(v, "#.####")``. This probe keeps two
of those written calls and nothing else that reaches ``fmt``:

* ``fmt(c1)`` over ``c1 = array.median(c)``, a float computed at run time that
  changes with the bar (1.5 up to 2.5, fractional unless ``close % 7`` is 2);
* ``fmt(c10)`` over ``c10 = array.get(array.sort_indices(idx), 0)``, a genuine
  integer: 3 where ``close % 7`` is below 1, otherwise 1.

The fixture's ``fmt(w)`` over the plain float ``close % 7`` is left out, so no
unrelated float argument reaches the function. An untyped parameter takes the
type of each written call's argument, so the median must come back with its
fraction whether the two calls end in one body or in two.

Every expectation is independent of the transpiler: the corpus feed's close
and plain arithmetic for every exit, and TradingView's own ``fmt(c1)`` and
``fmt(c10)`` (fields 0 and 9 of the fixture tape's exit Signals, frozen below)
for the seven exits the tape holds. The actual values are the exit comments of
the transpiled, compiled and executed strategy. ``@pf-trace`` records the
median, the index and ``close % 7`` before the calls on every bar, so a wrong
field after the call is told apart from a wrong input.

Each native test publishes one observation (the JUnit property
``pf476_observation`` and a ``PF476-OBS`` line): the stage reached, the
generated C++ definition each call
reaches with its parameter signature and actual, and per exit the value before
the call, the comment after it and the expectation. The definitions are
recorded, never asserted: one sound shared body and two sound specializations
are both valid.
"""

from __future__ import annotations

import csv
import json
import math
import re
import statistics
from pathlib import Path

import pytest

from tests._e2e import Build, closed_trades, execute_all, skip_unless_e2e_env
from tests._tv_tapes import tape, window_feed


PROBE = "pf476-two-call"
FIXTURE = "temp_names_array"

# TradingView's fmt(c1), fmt(c10) and fmt(w) on the fixture tape's seven exits:
# fields 0, 9 and 11 of each exit Signal, in trade order.
TRADINGVIEW = (
    ("2.5", "1", "6.08"),
    ("2.5", "1", "6.41"),
    ("2.5", "1", "4.61"),
    ("2.5", "1", "6.99"),
    ("1.5", "3", "0.8"),
    ("2.435", "1", "2.87"),
    ("2.5", "1", "5.42"),
)

# The comment each written order spells, and the median's field in it. The
# first written call types a shared body, so both orders are probed.
ORDERS = {
    "median_first": ('fmt(c1) + "|" + fmt(c10)', 0),
    "index_first": ('fmt(c10) + "|" + fmt(c1)', 1),
}

TRACED = ("pre_w", "pre_median", "pre_index")
EXACT = 1e-9
# "#.####" keeps four decimals.
ROUNDED = 0.00005 + 1e-9


def _script(order: str) -> str:
    comment = ORDERS[order][0]
    return (
        "//@version=6\n"
        'strategy("pf-476-two-call", overlay = true)\n'
        "\n"
        "w = close % 7\n"
        "c = array.from(3.0, 1.0, 2.0, w)\n"
        "idx = array.from(9.0, 1.0, 5.0, w)\n"
        "c1 = array.median(c)\n"
        "c10 = array.get(array.sort_indices(idx), 0)\n"
        "\n"
        'fmt(v) => str.tostring(v, "#.####")\n'
        'if hour(time, "UTC") % 6 == 0 and minute(time, "UTC") == 0\n'
        '    strategy.entry("L", strategy.long, comment = "E")\n'
        'if hour(time, "UTC") % 6 == 0 and minute(time, "UTC") == 15\n'
        f'    strategy.close("L", comment = {comment})\n'
        "// @pf-trace pre_w=w\n"
        "// @pf-trace pre_median=c1\n"
        "// @pf-trace pre_index=c10\n"
    )


def _oracle(w: float) -> tuple[float, set[int]]:
    """The median of the script's ``c`` and the first sort index of its
    ``idx`` (every tied index when two keys are equal) for one ``close % 7``."""
    median = statistics.median([3.0, 1.0, 2.0, w])
    keys = [9.0, 1.0, 5.0, w]
    return median, {index for index, key in enumerate(keys) if key == min(keys)}


def _tape_rows() -> list[dict]:
    rows = []
    for trade in tape(FIXTURE):
        fields = trade["exit"][1].split("|")
        assert len(fields) == 12, fields
        rows.append({
            "entry_ms": trade["entry"][0], "exit_ms": trade["exit"][0],
            "median": fields[0], "index": fields[9], "w": fields[11],
        })
    return rows


def _feed_bars(feed: Path) -> list[tuple[int, float]]:
    """``(open time in ms, close)`` of every bar of the window feed."""
    with feed.open(newline="") as handle:
        rows = [row for row in csv.reader(handle) if row]
    header = [cell.strip().lower() for cell in rows[0]]
    column = header.index("close") if "close" in header else 4
    return [(int(row[0]), float(row[column])) for row in rows[1:]]


def _near(got, want: float, tolerance: float = EXACT) -> bool:
    return (isinstance(got, (int, float)) and not isinstance(got, bool)
            and abs(got - want) <= tolerance)


def _finite(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _fractional(value) -> bool:
    return _finite(value) and value != math.floor(value)


def _whole(value) -> bool:
    return _finite(value) and value == math.floor(value)


# ---------------------------------------------------------------------------
# Diagnostics: the definition each written call reaches. Recorded only.
# ---------------------------------------------------------------------------

_NAME = r"(?:pf_safe_)?fmt(?:_[A-Za-z0-9_]+)?"
_CALL = re.compile(r"(?<!\w)(?P<name>" + _NAME + r")\s*\(")
_DEFINITION = re.compile(
    r"^(?P<ret>[A-Za-z_][\w:<>,&*\s]*?)\s+(?P<name>" + _NAME + r")\s*"
    r"\((?P<params>[^()]*)\)\s*(?:const\s*)?(?P<tail>[{;]?)\s*$")
_STATEMENT_WORDS = frozenset({
    "return", "else", "case", "throw", "new", "delete", "goto", "co_return"})


def _actual(line: str, start: int) -> str:
    depth = 1
    for position in range(start, len(line)):
        if line[position] == "(":
            depth += 1
        elif line[position] == ")":
            depth -= 1
            if depth == 0:
                return line[start:position]
    return line[start:]


def _reached(cpp: str) -> dict:
    """Every definition of ``fmt`` in the emitted C++ and, per written call,
    the callee, its actual and that callee's signature."""
    lines = cpp.splitlines()
    definitions: dict[str, str] = {}
    calls: list[dict] = []
    named: list[str] = []
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not _CALL.search(line):
            continue
        named.append(f"{number}: {line[:240]}")
        match = _DEFINITION.match(line)
        if match is not None and match["ret"].split()[0] not in _STATEMENT_WORDS:
            if match["tail"] != ";" or match["name"] not in definitions:
                definitions[match["name"]] = (
                    f'{match["ret"].strip()} {match["name"]}({match["params"].strip()})')
            continue
        for call in _CALL.finditer(line):
            calls.append({"callee": call["name"], "line": number,
                          "actual": _actual(line, call.end())})
    for call in calls:
        call["definition"] = definitions.get(call["callee"], "UNRESOLVED")
    # The helper's body spells the format literal: its lines and the three
    # above each, so the signature is on record under any naming.
    body = []
    for number, raw in enumerate(lines, start=1):
        if "#.####" in raw:
            first = max(1, number - 3)
            body.append([f"{n}: {lines[n - 1].strip()[:240]}"
                         for n in range(first, number + 1)])
    return {
        "definitions": sorted(definitions.values()),
        "calls": calls,
        "one_definition_for_both_calls":
            len(calls) == 2 and len({call["callee"] for call in calls}) == 1,
        "cpp_lines_naming_fmt": named[:40],
        "cpp_lines_of_the_format_body": body[:6],
    }


# ---------------------------------------------------------------------------
# Observation: one arm's run of one written order, judged by no assertion.
# ---------------------------------------------------------------------------

def _stage(error: str) -> str:
    for marker, stage in (("transpile_json refused it", "TRANSPILE-REFUSED"),
                          ("transpile_json crashed", "TRANSPILE-CRASH"),
                          ("compile failed in", "COMPILE"),
                          ("run_strategy failed in", "RUN")):
        if marker in error:
            return stage
    return "PIPELINE"


def _observe(engine: Path, base: Path, feed: Path, bars: list[tuple[int, float]],
             order: str, outcome) -> dict:
    cpp = (outcome.transpiled or {}).get("cpp") or ""
    seen: dict = {
        "probe": PROBE, "order": order, "stage": "ok", "detail": "",
        "missing": [], "pre_call_misses": [], "pre_call_miss_count": 0,
        "post_call_misses": [], "rows": [], "control": {},
    }
    seen.update(_reached(cpp))
    if outcome.error is not None:
        seen["stage"] = _stage(outcome.error)
        seen["detail"] = outcome.error[:3000]
        return seen

    missing: list[str] = []
    pre_misses: list[str] = []
    post_misses: list[str] = []

    # Before the calls: every traced bar against the feed and arithmetic.
    records = outcome.traces.get("default") or []
    series = {name: [record["value"] for record in records if record["name"] == name]
              for name in TRACED}
    seen["bars"] = {"feed": len(bars)}
    for name, values in series.items():
        seen["bars"][name] = len(values)
        if not values:
            missing.append(f"no @pf-trace record of {name}")
    covered = min(len(bars), *(len(values) for values in series.values()))
    for position in range(covered):
        w = math.fmod(bars[position][1], 7.0)
        median, allowed = _oracle(w)
        got = {name: series[name][position] for name in TRACED}
        if not (_near(got["pre_w"], w) and _near(got["pre_median"], median)
                and got["pre_index"] in allowed):
            pre_misses.append(
                f"bar {position} close {bars[position][1]!r}: traced {got}; arithmetic "
                f"w {w!r}, median {median!r}, index {sorted(allowed)}")

    # After the calls: every closed trade's exit comment.
    try:
        trades = closed_trades(engine, base / order, feed)
    except Exception as exc:  # the report run failed: no verdict on a field
        seen["stage"] = "RUN-REPORT"
        seen["detail"] = str(exc)[:3000]
        return seen
    position_of = {stamp: position for position, (stamp, _close) in enumerate(bars)}
    by_entry = {trade["entry_time"]: trade for trade in trades}
    tradingview: dict[int, dict] = {}
    for tape_row in _tape_rows():
        twin = by_entry.get(tape_row["entry_ms"])
        if twin is None or twin["exit_time"] != tape_row["exit_ms"]:
            missing.append(
                f"absent fill: the tape trade entered at {tape_row['entry_ms']} exits at "
                f"{tape_row['exit_ms']}, its engine twin at "
                f"{None if twin is None else twin['exit_time']}")
        else:
            tradingview[tape_row["exit_ms"]] = tape_row

    median_at = ORDERS[order][1]
    for trade in sorted(trades, key=lambda item: item["exit_time"]):
        exit_ms, comment = trade["exit_time"], trade["exit_comment"]
        row: dict = {"exit_ms": exit_ms, "comment": comment}
        seen["rows"].append(row)
        exit_position = position_of.get(exit_ms)
        if not exit_position or exit_position - 1 >= covered:
            missing.append(f"exit at {exit_ms}: no traced signal bar before it")
            continue
        position = exit_position - 1
        fields = comment.split("|") if isinstance(comment, str) else []
        if len(fields) != 2 or not all(fields):
            missing.append(
                f"exit at {exit_ms}: the comment {comment!r} does not spell both calls")
            continue
        median_field, index_field = fields[median_at], fields[1 - median_at]
        close = bars[position][1]
        w = math.fmod(close, 7.0)
        median, allowed = _oracle(w)
        pre_median = series["pre_median"][position]
        pre_index = series["pre_index"][position]
        try:
            after = float(median_field)
        except ValueError:
            after = math.nan
        wrong = []
        if not abs(after - median) <= ROUNDED:
            wrong.append("median")
        if index_field not in {str(index) for index in allowed}:
            wrong.append("index")
        tape_row = tradingview.get(exit_ms)
        if tape_row is not None:
            if not abs(w - float(tape_row["w"])) <= ROUNDED:
                pre_misses.append(
                    f"bar {position}: the feed's close % 7 is {w!r}, the tape's "
                    f"{tape_row['w']}")
            if median_field != tape_row["median"]:
                wrong.append("median-vs-tradingview")
            if index_field != tape_row["index"]:
                wrong.append("index-vs-tradingview")
        row.update({
            "signal_bar": position, "close": close, "w": w,
            "pre_call": {"median": pre_median, "index": pre_index},
            "after_call": {"median": median_field, "index": index_field},
            "expected": {"median": median, "index": sorted(allowed)},
            "tradingview": (None if tape_row is None
                            else [tape_row["median"], tape_row["index"]]),
            "wrong": wrong,
        })
        if wrong:
            lost = (math.isfinite(after) and after == math.floor(after)
                    and abs(median - round(median)) > 0.001)
            post_misses.append(
                f"exit {exit_ms} (bar {position}, close {close!r}): before the call "
                f"median {pre_median!r} and index {pre_index!r}; after it {comment!r}; "
                f"expected median {median!r} and index {sorted(allowed)}"
                + ("" if tape_row is None else
                   f", TradingView {tape_row['median']!r} and {tape_row['index']!r}")
                + f"; wrong: {wrong}"
                + ("; the fraction is lost" if lost else ""))

    judged = [row for row in seen["rows"] if "pre_call" in row]
    seen["control"] = {
        "exits": len(seen["rows"]),
        "exits_judged": len(judged),
        "tape_exits_paired": len(tradingview),
        "fractional_medians_before_the_call": sorted(
            {row["pre_call"]["median"] for row in judged
             if _fractional(row["pre_call"]["median"])}),
        "indexes_before_the_call": sorted(
            {int(row["pre_call"]["index"]) for row in judged
             if _whole(row["pre_call"]["index"])}),
        "medians_after_the_call": sorted({row["after_call"]["median"] for row in judged}),
    }
    seen["missing"] = missing
    seen["pre_call_misses"] = pre_misses[:5]
    seen["pre_call_miss_count"] = len(pre_misses)
    seen["post_call_misses"] = post_misses
    return seen


def _publish(seen: dict, request) -> dict:
    """Attach the observation to the test's JUnit properties and print it."""
    text = json.dumps(seen, sort_keys=True, default=str)
    request.node.user_properties.append(("pf476_observation", text))
    print("PF476-OBS " + text)
    return seen


def _not_evidence(seen: dict) -> str:
    return (f"NOT EVIDENCE of the field after the call [{seen['stage']}] {seen['detail']}\n"
            f"missing output: {seen['missing'][:5]}\n"
            f"pre-call controls ({seen['pre_call_miss_count']} misses): "
            f"{seen['pre_call_misses']}\n"
            f"definitions: {seen['definitions']}; calls: {seen['calls']}")


@pytest.fixture(scope="module")
def observations(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("pf476_two_call")
    feed = window_feed(engine, base)
    runs = execute_all(engine, feed, base, {
        order: Build(_script(order), trace=True) for order in ORDERS})
    bars = _feed_bars(feed)
    return {order: _observe(engine, base, feed, bars, order, runs[order])
            for order in ORDERS}


def test_the_frozen_tape_values_back_the_arithmetic_oracle():
    # TradingView's own fmt(c1), fmt(c10) and fmt(w) on the fixture tape are
    # the frozen rows, and arithmetic on each row's close % 7 gives that
    # median and that sort index: the expectation needs no transpiler.
    rows = _tape_rows()
    assert [(row["median"], row["index"], row["w"]) for row in rows] == list(TRADINGVIEW)
    for median, index, w in TRADINGVIEW:
        expected, allowed = _oracle(float(w))
        assert abs(float(median) - expected) <= ROUNDED, (median, expected, w)
        assert allowed == {int(index)}, (index, allowed, w)
    # The medians change and none is integral; the integer control varies.
    assert {median for median, _index, _w in TRADINGVIEW} == {"2.5", "1.5", "2.435"}
    assert {index for _median, index, _w in TRADINGVIEW} == {"1", "3"}


@pytest.mark.parametrize("order", list(ORDERS))
def test_both_calls_run_on_pre_call_controls_that_change(observations, order, request):
    seen = _publish(observations[order], request)
    # Transpiled, compiled and run; every tape trade filled; each exit comment
    # spells one field per call.
    assert seen["stage"] == "ok", _not_evidence(seen)
    assert not seen["missing"], _not_evidence(seen)
    assert seen["control"]["tape_exits_paired"] == len(TRADINGVIEW), _not_evidence(seen)
    assert seen["control"]["exits_judged"] == seen["control"]["exits"], _not_evidence(seen)
    # Before the calls the median, the index and close % 7 equal arithmetic on
    # the feed on every traced bar ...
    assert seen["pre_call_miss_count"] == 0, _not_evidence(seen)
    # ... and on the exits' bars the median takes several fractional values
    # and the index both of its values.
    assert len(seen["control"]["fractional_medians_before_the_call"]) >= 3, (
        _not_evidence(seen))
    assert seen["control"]["indexes_before_the_call"] == [1, 3], _not_evidence(seen)


@pytest.mark.parametrize("order", list(ORDERS))
def test_the_median_keeps_its_fraction_after_the_call(observations, order, request):
    seen = _publish(observations[order], request)
    assert (seen["stage"] == "ok" and not seen["missing"] and seen["rows"]
            and seen["pre_call_miss_count"] == 0), _not_evidence(seen)
    assert not seen["post_call_misses"], (
        f"POST-CALL: {len(seen['post_call_misses'])} of {len(seen['rows'])} exits spell a "
        f"wrong field after the call.\ndefinitions: {seen['definitions']}\n"
        f"calls: {seen['calls']}\n" + "\n".join(seen["post_call_misses"][:5]))
