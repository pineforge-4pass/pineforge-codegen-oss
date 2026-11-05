"""A history read inside a loop of a requested expression commits one slot per requested bar.

TradingView commits ONE history slot per bar to an expression read as
`(expr)[k]`, holding the LAST evaluation of that bar, and every read of the bar
sees the history before this bar. Inside a request.security payload the bar is
the REQUESTED bar: `sumRequested()` below reads `(r + 0.0)[i]` for i = 1 to 3
with r = 100 + 10 * the requested bar's bar_index, so on requested bar k its sum
is the sum of r on requested bars k - 1, k - 2 and k - 3, and is na before bar 3.
A payload that pushed one slot per evaluation would read r(k - 1) three times.

The value rows hold the chart series of the payload to numbers written here from
that arithmetic and from the alignment of the request on the 15-minute chart:
with the chart's own timeframe requested bar k is chart bar k; with "60" it is
the four chart bars 4k .. 4k + 3, completes at chart bar 4k + 3, and the chart
reads the last COMPLETED requested bar (no lookahead). Nothing is read from the
code under test.
"""

from __future__ import annotations

import math

import pytest

from tests._e2e import Build, chart_feed_head, execute_all, skip_unless_e2e_env

HEADER = '//@version=6\nstrategy("Requested loop history")\n'
LOOP_HELPER = (
    "sumRequested() =>\n"
    "    float r = 100.0 + 10.0 * bar_index\n"
    "    float s = 0.0\n"
    "    for i = 1 to 3\n"
    "        s += (r + 0.0)[i]\n"
    "    s\n"
)
TWICE_HELPER = "twice(float v) => v + v\n"
NAN = float("nan")


def _source(helper: str, payload: str, tf: str) -> str:
    return (
        HEADER + helper
        + f'float s = request.security(syminfo.tickerid, "{tf}", {payload}, '
        "gaps=barmerge.gaps_off, lookahead=barmerge.lookahead_off)\n"
        + "plot(s)\n// @pf-trace s=s\n"
    )


def _r(k: int) -> float:
    return 100.0 + 10.0 * k


def _loop_sum(k: int) -> float:
    return sum(_r(k - j) for j in (1, 2, 3)) if k >= 3 else NAN


def _twice_previous(k: int) -> float:
    return 2.0 * _r(k - 1) if k >= 1 else NAN


def _second_previous(k: int) -> float:
    return _r(k - 2) if k >= 2 else NAN


# (id, helper, payload, requested timeframe, chart bars per requested bar,
#  the payload's value on requested bar k)
ROWS = [
    ("loop-chart-timeframe", LOOP_HELPER, "sumRequested()", "15", 1, _loop_sum),
    ("loop-hourly", LOOP_HELPER, "sumRequested()", "60", 4, _loop_sum),
    ("one-history-read-twice", TWICE_HELPER,
     "twice((100.0 + 10.0 * bar_index)[1])", "15", 1, _twice_previous),
    ("plain-read-neighbour", "", "(100.0 + 10.0 * bar_index)[2]", "60", 4, _second_previous),
]


def _expected_at_chart_bar(bar: int, ratio: int, value_on_requested_bar) -> float:
    completed = (bar + 1) // ratio - 1  # the last completed requested bar, -1 before any
    return value_on_requested_bar(completed) if completed >= 0 else NAN


def _same(got: float, want: float) -> bool:
    return (math.isnan(got) and math.isnan(want)) or abs(got - want) < 1e-9


def test_a_loop_history_read_commits_one_slot_per_requested_bar(tmp_path):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 1500)
    builds = {row[0]: Build(_source(row[1], row[2], row[3]), trace=True) for row in ROWS}
    runs = execute_all(engine, feed, tmp_path / "runs", builds)
    problems = []
    for name, _helper, _payload, _tf, ratio, value in ROWS:
        outcome = runs[name]
        if outcome.error:
            problems.append(f"[{name}] {outcome.error}")
            continue
        observed = {r["bar_index"]: r["value"] for r in outcome.traces["default"] if r["name"] == "s"}
        assert len(observed) == 1500, (name, len(observed))
        wrong = [
            (bar, observed[bar], _expected_at_chart_bar(bar, ratio, value))
            for bar in sorted(observed)
            if not _same(observed[bar], _expected_at_chart_bar(bar, ratio, value))
        ]
        defined = [v for v in observed.values() if not math.isnan(v)]
        # Non-vacuity: the series has many defined values and they move.
        assert len(set(defined)) > 50, (name, len(set(defined)))
        if wrong:
            problems.append(
                f"[{name}] {len(wrong)} of 1500 chart bars differ from the arithmetic of the source; "
                f"first (chart bar, engine, expected): {wrong[:3]}")
    assert not problems, "\n".join(problems)
