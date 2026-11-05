"""A keyword argument of `timestamp` or `matrix.new` reaches its own parameter, or the call is refused.

`timestamp(timezone = "UTC+5", year = 2024, month = 3, day = 1)` ignored the
timezone (the numeric overload does not name one, and a keyword it does not know
was dropped) and used the symbol's, and `matrix.new<float>(rows = 2, columns = 3,
initial_value = 1.5)` built a matrix with no rows and no columns (the lowering
read the positional list, where the keywords are not). `timestamp(year = 2024,
month = 3, day = 1, minute = 30)`, which skips `hour`, read the 30 as the hour.
No diagnostic was raised.

Each row runs the keyword spelling on the engine next to its positional control
and holds it to a value that comes from the language, not from the code under
test: the instant the calendar fields name in the zone Python's own timezone
rules give, and the dimensions and the element the source writes. A keyword the
call does not have is refused with the typed unknown-keyword code, as
TradingView refuses it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._e2e import Build, chart_feed_head, execute_all, skip_unless_e2e_env

BARS = 1500
HEADER = '//@version=6\nstrategy("keyword arguments, constructors")\n'

CALENDAR = (
    'float zone_kw = timestamp(timezone = "UTC+5", year = 2024, month = 3, day = 1)\n'
    'float zone_pos = timestamp("UTC+5", 2024, 3, 1)\n'
    'float skip_kw = timestamp(year = 2024, month = 3, day = 1, minute = 30)\n'
    'float skip_pos = timestamp(2024, 3, 1, 0, 30)\n'
    "// @pf-trace zone_kw=zone_kw\n// @pf-trace zone_pos=zone_pos\n"
    "// @pf-trace skip_kw=skip_kw\n// @pf-trace skip_pos=skip_pos\n"
)

MATRIX = (
    "matrix<float> kw = matrix.new<float>(rows = 2, columns = 3, initial_value = 1.5)\n"
    "matrix<float> pos = matrix.new<float>(2, 3, 1.5)\n"
    "matrix<float> mixed = matrix.new<float>(2, columns = 3, initial_value = 1.5)\n"
    "matrix<float> no_rows_kw = matrix.new<float>(columns = 4)\n"
    "matrix<float> no_rows_pos = matrix.new<float>(0, 4)\n"
    "float kw_rows = kw.rows()\nfloat kw_columns = kw.columns()\nfloat kw_last = kw.get(1, 2)\n"
    "float pos_rows = pos.rows()\nfloat pos_columns = pos.columns()\n"
    "float mixed_rows = mixed.rows()\nfloat mixed_columns = mixed.columns()\n"
    "float no_rows_kw_rows = no_rows_kw.rows()\nfloat no_rows_kw_columns = no_rows_kw.columns()\n"
    "float no_rows_pos_rows = no_rows_pos.rows()\nfloat no_rows_pos_columns = no_rows_pos.columns()\n"
    + "".join(f"// @pf-trace {name}={name}\n" for name in (
        "kw_rows", "kw_columns", "kw_last", "pos_rows", "pos_columns",
        "mixed_rows", "mixed_columns", "no_rows_kw_rows", "no_rows_kw_columns",
        "no_rows_pos_rows", "no_rows_pos_columns"))
)

SOURCES = {"calendar": HEADER + CALENDAR, "matrix": HEADER + MATRIX}


def _column(outcome, name):
    return {r["bar_index"]: r["value"] for r in outcome.traces["default"] if r["name"] == name}


def _all_bars_equal(outcome, name, expected):
    column = _column(outcome, name)
    assert len(column) == BARS, (name, len(column))
    wrong = [(bar, column[bar]) for bar in sorted(column) if not column[bar] == expected]
    assert not wrong, f"{name}: {len(wrong)} of {BARS} bars differ from {expected}; first {wrong[:3]}"


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("keyword_arguments_construct")
    feed = chart_feed_head(engine, base, BARS)
    outcomes = execute_all(engine, feed, base / "runs",
                           {name: Build(source, trace=True) for name, source in SOURCES.items()})
    for name, outcome in outcomes.items():
        assert outcome.error is None, f"[{name}] {outcome.error}"
    return outcomes


def _milliseconds(moment: datetime) -> float:
    return moment.timestamp() * 1000.0


def test_timestamp_keyword_zone_is_the_named_zone(runs):
    # 2024-03-01 00:00 in UTC+5 is 2024-02-29 19:00 UTC.
    expected = _milliseconds(datetime(2024, 3, 1, tzinfo=timezone(timedelta(hours=5))))
    assert expected == 1709233200000.0
    for name in ("zone_pos", "zone_kw"):
        _all_bars_equal(runs["calendar"], name, expected)


def test_timestamp_keyword_after_a_skipped_hour_keeps_its_own_slot(runs):
    # The chart's symbol is on UTC: 2024-03-01 00:30 UTC.
    expected = _milliseconds(datetime(2024, 3, 1, 0, 30, tzinfo=timezone.utc))
    assert expected == 1709253000000.0
    for name in ("skip_pos", "skip_kw"):
        _all_bars_equal(runs["calendar"], name, expected)


def test_matrix_keywords_name_the_dimensions_and_the_fill(runs):
    outcome = runs["matrix"]
    for name, expected in (("kw_rows", 2.0), ("kw_columns", 3.0), ("kw_last", 1.5),
                           ("pos_rows", 2.0), ("pos_columns", 3.0),
                           ("mixed_rows", 2.0), ("mixed_columns", 3.0)):
        _all_bars_equal(outcome, name, expected)


def test_a_matrix_that_skips_its_rows_has_the_rows_default(runs):
    outcome = runs["matrix"]
    # No row count named is the same matrix as zero rows written out.
    for kw_name, pos_name in (("no_rows_kw_rows", "no_rows_pos_rows"),
                              ("no_rows_kw_columns", "no_rows_pos_columns")):
        keyword, positional = _column(outcome, kw_name), _column(outcome, pos_name)
        assert len(keyword) == len(positional) == BARS
        wrong = [(bar, keyword[bar], positional[bar]) for bar in sorted(keyword)
                 if not keyword[bar] == positional[bar]]
        assert not wrong, f"{kw_name} differs from {pos_name} on {len(wrong)} bars; first {wrong[:3]}"
    assert set(_column(outcome, "no_rows_kw_rows").values()) == {0.0}


UNKNOWN = [
    ("timestamp", 'x = timestamp(zone = "UTC", year = 2024, month = 3, day = 1)\n'),
    ("matrix.new", "matrix<float> x = matrix.new<float>(row = 2, columns = 3)\n"),
    ("str.format_time", 'x = str.format_time(time, fmt = "yyyy")\n'),
]


@pytest.mark.parametrize("name,line", UNKNOWN, ids=[row[0] for row in UNKNOWN])
def test_a_keyword_no_parameter_has_is_refused_with_the_typed_code(name, line):
    source = HEADER + line
    with pytest.raises(CompileError) as caught:
        transpile(source)
    codes = [d.code for d in caught.value.diagnostics]
    assert codes == ["PF-E2027"], codes
    assert name in caught.value.diagnostics[0].message
