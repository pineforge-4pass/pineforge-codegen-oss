"""A request whose timeframe is a series string runs where dynamic requests are on, and is refused where they are off.

Pine v6, the only version PineForge translates, has dynamic requests on, so
`request.security` takes a timeframe that is computed on every bar from a
history read or from `bar_index`; `dynamic_requests = false` in the `strategy()`
declaration turns them off (the refusal rows are in
`test_security_selection_blocks.py`).

Each row runs one script on the engine: the request whose timeframe is chosen
on every bar next to the same choice written out over two requests with literal
timeframes ("60" and "240"). The dynamic value must equal the written-out
selection on every bar, and the rows hold that the choice really moves between
the two timeframes and that the two timeframes really differ, so a request that
stayed on one stream or on the chart's timeframe cannot pass.
"""

from __future__ import annotations

import math

import pytest

from tests._e2e import Build, chart_feed_head, execute_all, skip_unless_e2e_env

BARS = 1500

# The choice is written once and used twice: in the computed timeframe and in
# the written-out selection of two literal-timeframe requests.
CHOICES = {
    "history": "close[1] > close[2]",
    "bar_index": "bar_index % 2 == 0",
}

VERSION_6 = '//@version=6\nstrategy("Series timeframe")\n'
DYNAMIC_REQUESTS_DECLARED = '//@version=6\nstrategy("Series timeframe", dynamic_requests = true)\n'


def script(header: str, choice: str) -> str:
    return (
        header
        + "pick = input.bool(true)\n"
        + f'tf = if pick\n    candidate = str.tostring({choice} ? 60 : 240)\n    candidate\nelse\n    "1D"\n'
        + "dynamic_value = request.security(syminfo.tickerid, tf, close)\n"
        + 'hour_value = request.security(syminfo.tickerid, "60", close)\n'
        + 'four_hour_value = request.security(syminfo.tickerid, "240", close)\n'
        + f"chose_hour = {choice} ? 1 : 0\n"
        + "written_out = chose_hour == 1 ? hour_value : four_hour_value\n"
        + "".join(f"// @pf-trace {name}={name}\n" for name in (
            "dynamic_value", "written_out", "hour_value", "four_hour_value", "chose_hour"))
    )


ROWS = {
    "v6-history": (VERSION_6, "history"),
    "v6-bar_index": (VERSION_6, "bar_index"),
    "v6-declared-on-history": (DYNAMIC_REQUESTS_DECLARED, "history"),
}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("series_timeframe_dynamic_requests")
    feed = chart_feed_head(engine, base, BARS)
    return execute_all(
        engine, feed, base / "runs",
        {name: Build(script(header, CHOICES[choice]), trace=True)
         for name, (header, choice) in ROWS.items()})


def _column(outcome, name):
    return {r["bar_index"]: r["value"] for r in outcome.traces["default"] if r["name"] == name}


def _same(left, right):
    return (math.isnan(left) and math.isnan(right)) or left == right


@pytest.mark.parametrize("row", ROWS)
def test_the_computed_timeframe_reads_the_stream_the_written_out_choice_reads(runs, row):
    outcome = runs[row]
    assert outcome.error is None, f"[{row}] {outcome.error}"
    dynamic = _column(outcome, "dynamic_value")
    written_out = _column(outcome, "written_out")
    assert len(dynamic) == len(written_out) == BARS, (len(dynamic), len(written_out))
    wrong = [(bar, dynamic[bar], written_out[bar]) for bar in sorted(dynamic)
             if not _same(dynamic[bar], written_out[bar])]
    assert not wrong, f"{row}: {len(wrong)} of {BARS} bars differ from the written-out choice; first {wrong[:3]}"


@pytest.mark.parametrize("row", ROWS)
def test_the_rows_cannot_pass_on_one_stream(runs, row):
    outcome = runs[row]
    assert outcome.error is None, f"[{row}] {outcome.error}"
    hour = _column(outcome, "hour_value")
    four_hour = _column(outcome, "four_hour_value")
    chose_hour = _column(outcome, "chose_hour")
    # The two literal timeframes differ on many bars once both have a value.
    apart = [bar for bar in hour if not (math.isnan(hour[bar]) or math.isnan(four_hour[bar]))
             and hour[bar] != four_hour[bar]]
    assert len(apart) >= 100, len(apart)
    # The choice takes both values often, so both streams are read.
    picks = [chose_hour[bar] for bar in chose_hour]
    assert picks.count(1.0) >= 100 and picks.count(0.0) >= 100, (picks.count(1.0), picks.count(0.0))
