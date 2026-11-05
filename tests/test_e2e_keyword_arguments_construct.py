"""A keyword argument of `timestamp`, `time`, `matrix.new` or `math.random` reaches its own parameter, or the call is refused.

The same fault in `time` and `time_close` (`time("60", timezone = "UTC+5")`
skips `session`: the timezone moved into the session slot) and in
`math.random(seed = 3)` (the seed became the minimum) has its rows below too.

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

# Three scripts, so that a matrix the keywords build wrongly stops only the rows
# that read it: the dimensions are read without reading an element (a 0 by 0
# matrix has none, and the read of one stops the run).
MATRIX_DIMENSIONS = (
    "matrix<float> kw = matrix.new<float>(rows = 2, columns = 3, initial_value = 1.5)\n"
    "matrix<float> pos = matrix.new<float>(2, 3, 1.5)\n"
    "matrix<float> mixed = matrix.new<float>(2, columns = 3, initial_value = 1.5)\n"
    "float kw_rows = kw.rows()\nfloat kw_columns = kw.columns()\n"
    "float pos_rows = pos.rows()\nfloat pos_columns = pos.columns()\n"
    "float mixed_rows = mixed.rows()\nfloat mixed_columns = mixed.columns()\n"
    + "".join(f"// @pf-trace {name}={name}\n" for name in (
        "kw_rows", "kw_columns", "pos_rows", "pos_columns", "mixed_rows", "mixed_columns"))
)
MATRIX_FILL = (
    "matrix<float> kw = matrix.new<float>(rows = 2, columns = 3, initial_value = 1.5)\n"
    "matrix<float> pos = matrix.new<float>(2, 3, 1.5)\n"
    "float kw_last = kw.get(1, 2)\nfloat pos_last = pos.get(1, 2)\n"
    "// @pf-trace kw_last=kw_last\n// @pf-trace pos_last=pos_last\n"
)
MATRIX_NO_ROWS = (
    "matrix<float> no_rows_kw = matrix.new<float>(columns = 4)\n"
    "matrix<float> no_rows_pos = matrix.new<float>(0, 4)\n"
    "float no_rows_kw_rows = no_rows_kw.rows()\nfloat no_rows_kw_columns = no_rows_kw.columns()\n"
    "float no_rows_pos_rows = no_rows_pos.rows()\nfloat no_rows_pos_columns = no_rows_pos.columns()\n"
    + "".join(f"// @pf-trace {name}={name}\n" for name in (
        "no_rows_kw_rows", "no_rows_kw_columns", "no_rows_pos_rows", "no_rows_pos_columns"))
)

# `math.random(seed = 3)` skips `min` and `max`, which default to 0 and 1. Each
# spelling is its own script: the value depends on the call's position in the
# script, so two calls in one script would differ whatever their seeds.
RANDOM_KEYWORD = "float random_value = math.random(seed = 3)\n// @pf-trace random_value=random_value\n"
RANDOM_POSITIONAL = "float random_value = math.random(0.0, 1.0, 3)\n// @pf-trace random_value=random_value\n"

# `time("60", timezone = "UTC+5")` skips `session`. With no session the call is the
# open time of the hour bar the chart bar is in, and `time_close` the close time:
# the timezone changes neither.
TIME_CALLS = (
    'float time_kw = time("60", timezone = "UTC+5")\n'
    'float time_pos = time("60", "", "UTC+5")\n'
    'float close_kw = time_close("60", timezone = "UTC+5")\n'
    'float close_pos = time_close("60", "", "UTC+5")\n'
    "// @pf-trace t=time\n"
    "// @pf-trace time_kw=time_kw\n// @pf-trace time_pos=time_pos\n"
    "// @pf-trace close_kw=close_kw\n// @pf-trace close_pos=close_pos\n"
)

SOURCES = {
    "calendar": HEADER + CALENDAR,
    "time": HEADER + TIME_CALLS,
    "matrix_dimensions": HEADER + MATRIX_DIMENSIONS,
    "matrix_fill": HEADER + MATRIX_FILL,
    "matrix_no_rows": HEADER + MATRIX_NO_ROWS,
    "random_keyword": HEADER + RANDOM_KEYWORD,
    "random_positional": HEADER + RANDOM_POSITIONAL,
}


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
    return execute_all(engine, feed, base / "runs",
                       {name: Build(source, trace=True) for name, source in SOURCES.items()})


def _ran(runs, name):
    """One script's run; a script that did not run fails only the rows that read it."""
    outcome = runs[name]
    assert outcome.error is None, f"[{name}] {outcome.error}"
    return outcome


def _milliseconds(moment: datetime) -> float:
    return moment.timestamp() * 1000.0


def test_timestamp_keyword_zone_is_the_named_zone(runs):
    # 2024-03-01 00:00 in UTC+5 is 2024-02-29 19:00 UTC.
    expected = _milliseconds(datetime(2024, 3, 1, tzinfo=timezone(timedelta(hours=5))))
    assert expected == 1709233200000.0
    for name in ("zone_pos", "zone_kw"):
        _all_bars_equal(_ran(runs, "calendar"), name, expected)


def test_timestamp_keyword_after_a_skipped_hour_keeps_its_own_slot(runs):
    # The chart's symbol is on UTC: 2024-03-01 00:30 UTC.
    expected = _milliseconds(datetime(2024, 3, 1, 0, 30, tzinfo=timezone.utc))
    assert expected == 1709253000000.0
    for name in ("skip_pos", "skip_kw"):
        _all_bars_equal(_ran(runs, "calendar"), name, expected)


def test_time_keyword_after_a_skipped_session_keeps_its_own_slot(runs):
    outcome = _ran(runs, "time")
    hour = 3_600_000
    stamps = _column(outcome, "t")
    assert len(stamps) == BARS
    for name, offset in (("time_pos", 0), ("time_kw", 0), ("close_pos", hour), ("close_kw", hour)):
        column = _column(outcome, name)
        assert len(column) == BARS, name
        wrong = [(bar, column[bar], (stamps[bar] // hour) * hour + offset) for bar in sorted(column)
                 if not column[bar] == (stamps[bar] // hour) * hour + offset]
        assert not wrong, (f"{name}: {len(wrong)} of {BARS} bars differ from the hour bar's "
                           f"{'open' if offset == 0 else 'close'} time; first {wrong[:3]}")


def test_matrix_keywords_name_the_dimensions(runs):
    outcome = _ran(runs, "matrix_dimensions")
    for name, expected in (("kw_rows", 2.0), ("kw_columns", 3.0),
                           ("pos_rows", 2.0), ("pos_columns", 3.0),
                           ("mixed_rows", 2.0), ("mixed_columns", 3.0)):
        _all_bars_equal(outcome, name, expected)


def test_matrix_keywords_name_the_fill(runs):
    outcome = _ran(runs, "matrix_fill")
    for name in ("kw_last", "pos_last"):
        _all_bars_equal(outcome, name, 1.5)


def test_a_matrix_that_skips_its_rows_has_the_rows_default(runs):
    outcome = _ran(runs, "matrix_no_rows")
    # No row count named is the same matrix as zero rows written out.
    for kw_name, pos_name in (("no_rows_kw_rows", "no_rows_pos_rows"),
                              ("no_rows_kw_columns", "no_rows_pos_columns")):
        keyword, positional = _column(outcome, kw_name), _column(outcome, pos_name)
        assert len(keyword) == len(positional) == BARS
        wrong = [(bar, keyword[bar], positional[bar]) for bar in sorted(keyword)
                 if not keyword[bar] == positional[bar]]
        assert not wrong, f"{kw_name} differs from {pos_name} on {len(wrong)} bars; first {wrong[:3]}"
    assert set(_column(outcome, "no_rows_kw_rows").values()) == {0.0}


def test_random_keyword_seed_keeps_the_default_range(runs):
    keyword = _column(_ran(runs, "random_keyword"), "random_value")
    positional = _column(_ran(runs, "random_positional"), "random_value")
    assert len(keyword) == len(positional) == BARS
    # Without min and max the range is [0, 1): the seed must not become the minimum.
    outside = [(bar, value) for bar, value in sorted(keyword.items()) if not 0.0 <= value < 1.0]
    assert not outside, f"{len(outside)} of {BARS} values outside [0, 1); first {outside[:3]}"
    # The same seed gives the same sequence whichever way the seed is written.
    different = [(bar, keyword[bar], positional[bar]) for bar in sorted(keyword)
                 if not keyword[bar] == positional[bar]]
    assert not different, f"{len(different)} bars differ from the positional seed; first {different[:3]}"
    # Non-vacuity: the values move.
    assert len(set(keyword.values())) > 100


# (call, source line, the code the record's namespace refuses an unknown keyword with:
# math records have their own, every other record PF-E2027)
UNKNOWN = [
    ("math.random", "float x = math.random(sead = 3)\n", "PF-E5090"),
    ("time", 'x = time("60", zone = "UTC")\n', "PF-E2027"),
    ("time_close", 'x = time_close("60", zone = "UTC")\n', "PF-E2027"),
    ("timestamp", 'x = timestamp(zone = "UTC", year = 2024, month = 3, day = 1)\n', "PF-E2027"),
    ("matrix.new", "matrix<float> x = matrix.new<float>(row = 2, columns = 3)\n", "PF-E2027"),
    ("str.format_time", 'x = str.format_time(time, fmt = "yyyy")\n', "PF-E2027"),
]


@pytest.mark.parametrize("name,line,code", UNKNOWN, ids=[row[0] for row in UNKNOWN])
def test_a_keyword_no_parameter_has_is_refused_with_the_typed_code(name, line, code):
    source = HEADER + line
    with pytest.raises(CompileError) as caught:
        transpile(source)
    codes = [d.code for d in caught.value.diagnostics]
    assert codes == [code], codes
    assert name in caught.value.diagnostics[0].message


# Calls whose every keyword is a parameter of SOME overload of `timestamp`, so the
# keyword-name check passes, but that bind to no overload: the lowering refuses
# them with the code the typed methods use for the same fault. (call, code, the
# name the message quotes)
NO_OVERLOAD = [
    ('x = timestamp(dateString = "2024-03-01", year = 2024)\n', "PF-E2027", "year"),
    ('x = timestamp(2024, dateString = "2024-03-01")\n', "PF-E2027", "dateString"),
    ('x = timestamp("UTC+5", 2024, 3, 1, timezone = "UTC+5")\n', "PF-E2024", "timezone"),
]


@pytest.mark.parametrize("line,code,quoted", NO_OVERLOAD, ids=[row[2] for row in NO_OVERLOAD])
def test_a_timestamp_that_binds_to_no_overload_is_refused_with_the_typed_code(line, code, quoted):
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + line)
    codes = [d.code for d in caught.value.diagnostics]
    assert codes == [code], codes
    assert f"'{quoted}'" in caught.value.diagnostics[0].message


# The two offsets Pine v6 gives time() and time_close() are parameters of their
# records: naming them is no unknown keyword (the lowering reads them by name).
OFFSET_KEYWORDS = [
    'x = time("60", bars_back = -1)\n',
    'x = time_close("60", timeframe_bars_back = 1)\n',
]


@pytest.mark.parametrize("line", OFFSET_KEYWORDS, ids=["time", "time_close"])
def test_the_offset_keywords_of_time_are_no_unknown_keywords(line):
    assert "pine_time_offset" in transpile(HEADER + line)
