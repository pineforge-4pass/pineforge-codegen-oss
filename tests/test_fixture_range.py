"""Unit rows for the one range check of the fixture metadata
(``tests/_fixture_range.py``), on invented records.

The invented window is the shape the real records have, 00:00 UTC of a
``from`` and of a ``to`` date: Monday 2025-05-12 to Saturday 2025-05-17. A
returned ``from`` / ``to`` is the open of the first / last bar returned. The
passing session rows state, per chart, the first and the last bar open that
``FIRST_LAST`` of ``tests/test_e2e_session_windows.py`` records, and the lane
facts of ``tests/test_e2e_session_ismarket.py``.

Needs no engine and no fixture file.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from tests import test_e2e_session_ismarket as lane_facts
from tests import test_e2e_session_windows as session_tables
from tests._fixture_range import SESSION_RULES, SessionRule, assert_range_covered, range_failure

TOKYO, HONG_KONG = "Asia/Tokyo", "Asia/Hong_Kong"
CHICAGO, NEW_YORK = "America/Chicago", "America/New_York"
HOUR = 3_600_000
QUARTER = 900_000


def _ms(day: int, clock: str = "00:00", zone: str = "UTC") -> int:
    """UTC milliseconds of ``clock`` on 2025-05-``day`` in ``zone``."""
    moment = dt.datetime(2025, 5, day, int(clock[:2]), int(clock[3:]), tzinfo=ZoneInfo(zone))
    return int(moment.timestamp()) * 1000


REQUESTED = (_ms(12), _ms(17))   # Monday 00:00 UTC .. Saturday 00:00 UTC


def _record(symbol: str, interval: str, returned: tuple[int, int], covered: object = True) -> dict:
    return {"symbol": symbol, "interval": interval,
            "range": {"requested": {"from": REQUESTED[0], "to": REQUESTED[1]},
                      "returned": {"from": returned[0], "to": returned[1]},
                      "covered": covered}}


TSE_15 = (_ms(12, "09:00", TOKYO), _ms(16, "15:30", TOKYO))
SUNDAY_EVENING = _ms(11, "19:00", CHICAGO)   # Monday's open in Chicago: the requested start itself

# id -> (record, the record is of the extended-hours chart)
PASSING = {
    "round-the-clock": (_record("BINANCE:ETHUSDT.P", "60", REQUESTED), False),
    "tse7203-15": (_record("TSE:7203", "15", TSE_15), False),
    "tse7203-60": (_record("TSE:7203", "60", (TSE_15[0], _ms(16, "15:00", TOKYO))), False),
    "tse7203-240": (_record("TSE:7203", "240", (TSE_15[0], _ms(16, "13:00", TOKYO))), False),
    "hkex700-15": (_record("HKEX:700", "15", (_ms(12, "09:30", HONG_KONG),
                                              _ms(16, "15:45", HONG_KONG))), False),
    "hkex700-60": (_record("HKEX:700", "60", (_ms(12, "09:30", HONG_KONG),
                                              _ms(16, "15:00", HONG_KONG))), False),
    "hkex700-240": (_record("HKEX:700", "240", (_ms(12, "09:30", HONG_KONG),
                                                _ms(16, "13:00", HONG_KONG))), False),
    "zc1-15": (_record("CBOT:ZC1!", "15", (SUNDAY_EVENING, _ms(16, "13:15", CHICAGO))), False),
    "zc1-60": (_record("CBOT:ZC1!", "60", (SUNDAY_EVENING, _ms(16, "13:00", CHICAGO))), False),
    "zc1-240": (_record("CBOT:ZC1!", "240", (SUNDAY_EVENING, _ms(16, "11:00", CHICAGO))), False),
    # An overnight session: the Sunday-evening open is Monday's, and Friday
    # 17:00 opens no session day.
    "es1-60": (_record("CME_MINI:ES1!", "60", (SUNDAY_EVENING, _ms(16, "15:00", CHICAGO))), False),
    "aapl-15-regular": (_record("NASDAQ:AAPL", "15", (_ms(12, "09:30", NEW_YORK),
                                                      _ms(16, "15:45", NEW_YORK))), False),
    "aapl-15-extended": (_record("NASDAQ:AAPL", "15", (_ms(12, "04:00", NEW_YORK),
                                                       _ms(16, "19:45", NEW_YORK))), True),
    # Daily bars stamped at the 17:00 break: Tuesday's is the first whose
    # stamp is not before the requested start, Friday's the last.
    "xauusd-1d": (_record("OANDA:XAUUSD", "1D", (_ms(12, "17:00", NEW_YORK),
                                                 _ms(15, "17:00", NEW_YORK))), False),
}

# id -> (record, the record is of the extended-hours chart, the failed condition)
FAILING = {
    "session-one-bar-missing-at-the-end":
        (_record("TSE:7203", "15", (TSE_15[0], TSE_15[1] - QUARTER)), False, "last session bar"),
    "session-one-bar-missing-at-the-start":
        (_record("TSE:7203", "15", (TSE_15[0] + QUARTER, TSE_15[1])), False, "first session bar"),
    "round-the-clock-one-bar-short-at-the-end":
        (_record("BINANCE:ETHUSDT.P", "60", (REQUESTED[0], REQUESTED[1] - HOUR)), False,
         "round-the-clock"),
    "round-the-clock-one-bar-short-at-the-start":
        (_record("BINANCE:ETHUSDT.P", "60", (REQUESTED[0] + HOUR, REQUESTED[1])), False,
         "round-the-clock"),
    "symbol-without-a-session-rule":
        (_record("NOWHERE:XYZ", "15", REQUESTED), False, "no session rule for symbol 'NOWHERE:XYZ'"),
    "label-false":
        (_record("BINANCE:ETHUSDT.P", "60", REQUESTED, covered=False), False, "`covered` is not true"),
    "label-a-word":
        (_record("BINANCE:ETHUSDT.P", "60", REQUESTED, covered="covered"), False,
         "`covered` is not true"),
    # The requested range itself is not a session list's range: 08:00 in Hong
    # Kong opens no bar, and Saturday holds none.
    "session-range-by-the-calendar":
        (_record("HKEX:700", "15", REQUESTED), False, "first session bar"),
    "session-range-past-its-last-bar":
        (_record("TSE:7203", "15", (TSE_15[0], REQUESTED[1])), False, "last session bar"),
    "no-daily-bar-rule": (_record("TSE:7203", "1D", TSE_15), False, "no daily-bar rule"),
    "no-extended-hours-rule": (_record("TSE:7203", "15", TSE_15), True, "no extended-hours rule"),
    "no-bar-rule-for-the-interval":
        (_record("TSE:7203", "1W", TSE_15), False, "no bar rule for interval"),
    "record-without-a-range":
        ({"symbol": "BINANCE:ETHUSDT.P", "interval": "60"}, False, "holds no `range`"),
}


@pytest.mark.parametrize("record, extended", list(PASSING.values()), ids=list(PASSING))
def test_a_range_holding_every_bar_passes(record: dict, extended: bool) -> None:
    assert range_failure(record, extended) is None


@pytest.mark.parametrize("record, extended, condition", list(FAILING.values()), ids=list(FAILING))
def test_a_range_missing_a_bar_or_a_rule_fails(record: dict, extended: bool, condition: str) -> None:
    failure = range_failure(record, extended)
    assert failure is not None and condition in failure, failure


def test_the_check_fails_only_a_failed_range_and_names_fixture_and_condition() -> None:
    passing, extended = PASSING["tse7203-15"]
    assert assert_range_covered(passing, "invented-tape", extended) is None
    failing, extended, condition = FAILING["session-one-bar-missing-at-the-end"]
    try:
        assert_range_covered(failing, "invented-tape", extended)
    except AssertionError as failed:
        message = str(failed)
    else:
        message = "the check passed it"
    assert message.startswith("invented-tape: ") and condition in message, message


def test_the_rules_are_the_suites_own_session_tables() -> None:
    """SESSION_RULES restates what the session tests hold: the hours and time
    zone of a lane fact in ``CASES``, and the two-window sessions with their
    zones. A rule changed here or there alone fails this row."""
    facts = {(case.session, case.timezone) for case in lane_facts.CASES}
    for symbol in ("BINANCE:ETHUSDT.P", "CME_MINI:ES1!", "OANDA:EURUSD", "OANDA:XAUUSD",
                   "NASDAQ:AAPL"):
        rule = SESSION_RULES[symbol]
        assert (rule.hours, rule.timezone) in facts, symbol
    zones = {slug: zone for slug, (zone, _) in session_tables.TAPES.items()}
    assert SESSION_RULES["TSE:7203"] == SessionRule(session_tables.TSE[1],
                                                    zones["cgs2-flags-tse7203-15"])
    assert SESSION_RULES["HKEX:700"] == SessionRule(session_tables.HKEX,
                                                    zones["cgs2-flags-hkex700-15"], grid="0900")
    assert SESSION_RULES["CBOT:ZC1!"] == SessionRule(session_tables.ZC, zones["cgs2-flags-zc1-15"])
