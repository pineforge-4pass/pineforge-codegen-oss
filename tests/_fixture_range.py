"""One range check for the recorded TradingView runs under ``fixtures/``.

A fixture's ``metrics.json`` states the range its run asked TradingView for
(``range.requested``), the range TradingView returned (``range.returned``) and
a label (``range.covered``). Every test that relies on a trade list holding
its whole window asks ``assert_range_covered`` here, so one rule decides.

Time convention, as the records hold it: every ``from`` and ``to`` is in
milliseconds since the epoch, UTC. A requested ``from`` / ``to`` is 00:00 UTC
of the record's ``from`` / ``to`` date. A returned ``from`` / ``to`` is the
OPEN of the first / last chart bar TradingView returned, and the requested
end is inclusive: ``hm-g236-eth-60-24x7`` holds 97 bars of 60 minutes for a
96-hour window, the last one opening at the requested ``to``.

The rule:

* the label ``covered`` is true; and
* a round-the-clock symbol returned exactly the requested range; and
* a session symbol returned every session bar of the requested window: its
  returned ``from`` is the open of the first session bar at or after the
  requested ``from``, its returned ``to`` the open of the last session bar at
  or before the requested ``to``. A session list's first bar opens at the
  session open, not at the requested start, so "returned contains requested"
  by the calendar is not the rule.

A symbol without a rule in ``SESSION_RULES`` fails: it never passes unchecked.

Session bars. A session day is a trading date, Monday to Friday (the lane
facts' ``:23456``); a window whose close is not after its open opened on the
evening before (the Sunday-evening open is Monday's). A day's bars are the
cells of the chart's interval counted from the day's first open, or from the
rule's ``grid`` time; a cell that holds session time is a bar, stamped at the
cell's start (TSE:7203's 60-minute 12:00 bar opens in the lunch break), and no
earlier than the day's first open (HKEX:700's 60-minute cells count from 09:00
and its opening bar is stamped 09:30). There is no holiday calendar here: a
window whose first or last session day is a market holiday needs one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

DAY_MINUTES = 24 * 60
SESSION_WEEKDAYS = (0, 1, 2, 3, 4)   # date.weekday() of Monday .. Friday
DAYS_SEARCHED = 9                    # past any weekend around a requested start or end
ROUND_THE_CLOCK = "24x7"
RULES_AT = "SESSION_RULES (tests/_fixture_range.py)"


@dataclass(frozen=True)
class SessionRule:
    """A symbol's session, as the suite's own tables state it."""
    hours: str                    # "24x7", or "HHMM-HHMM[,HHMM-HHMM]" in ``timezone``
    timezone: str
    grid: str | None = None       # "HHMM" the bar cells count from, when not the day's first open
    extended: str | None = None   # the hours of the symbol's extended-hours chart
    daily: str | None = None      # "HHMM" a daily bar is stamped at


SESSION_RULES = {
    # The lane facts: CASES in tests/test_e2e_session_ismarket.py.
    "BINANCE:ETHUSDT.P": SessionRule(ROUND_THE_CLOCK, "UTC"),
    "CME_MINI:ES1!": SessionRule("1700-1600", "America/Chicago"),
    "OANDA:EURUSD": SessionRule("1700-1700", "America/New_York"),
    # "OANDA:XAUUSD's daily bars are stamped at the 17:00 ET break."
    "OANDA:XAUUSD": SessionRule("1800-1700", "America/New_York", daily="1700"),
    # "NASDAQ:AAPL's regular session, whose extended hours TradingView reads
    # as the 04:00-09:30 pre-market and 16:00-20:00 post-market windows."
    "NASDAQ:AAPL": SessionRule("0930-1600", "America/New_York", extended="0400-2000"),
    # The session tests' tables: TSE, HKEX, ZC, TAPES and FIRST_LAST in
    # tests/test_e2e_session_windows.py. TSE[1]: TradingView keeps the 15:30
    # bar in session. HKEX:700 at 60 and 240 minutes opens at 09:30 and then
    # at 10:00 / 13:00: its cells count from 09:00.
    "TSE:7203": SessionRule("0900-1130,1230-1545", "Asia/Tokyo"),
    "HKEX:700": SessionRule("0930-1200,1300-1600", "Asia/Hong_Kong", grid="0900"),
    "CBOT:ZC1!": SessionRule("1900-0745,0830-1320", "America/Chicago"),
}


def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[2:])


def _windows(hours: str) -> list[tuple[int, int]]:
    """(open, close) of each window, in minutes after the session day's
    midnight. A window whose close is not after its open opened on the evening
    before: a negative open."""
    windows = []
    for part in hours.split(","):
        opened, closed = (_minutes(side) for side in part.split("-"))
        if closed <= opened:
            opened -= DAY_MINUTES
        windows.append((opened, closed))
    return windows


def _bar_minutes(hours: str, grid: str | None, step: int) -> list[int]:
    """The opens of one session day's bars of ``step`` minutes, in minutes
    after the day's midnight, in time order."""
    windows = _windows(hours)
    first_open = min(opened for opened, _ in windows)
    last_close = max(closed for _, closed in windows)
    cell = _minutes(grid) if grid else first_open
    opens = []
    while cell < last_close:
        if any(opened < cell + step and closed > cell for opened, closed in windows):
            opens.append(max(cell, first_open))
        cell += step
    return opens


def _stamp(day: dt.date, minutes: int, zone: ZoneInfo) -> int:
    """UTC milliseconds of the wall-clock time ``minutes`` after ``day``'s
    midnight in ``zone``."""
    wall = dt.datetime(day.year, day.month, day.day) + dt.timedelta(minutes=minutes)
    local = dt.datetime(wall.year, wall.month, wall.day, wall.hour, wall.minute, tzinfo=zone)
    return int(local.timestamp()) * 1000


def _day_bars(rule: SessionRule, hours: str, interval: str, day: dt.date) -> list[int]:
    """The bar opens of session day ``day`` in UTC milliseconds, in time
    order; none on a Saturday or a Sunday."""
    if day.weekday() not in SESSION_WEEKDAYS:
        return []
    if interval.isdigit():
        minutes = _bar_minutes(hours, rule.grid, int(interval))
    else:
        # A daily bar: stamped on the evening before for an overnight session.
        overnight = min(opened for opened, _ in _windows(hours)) < 0
        minutes = [_minutes(rule.daily) - (DAY_MINUTES if overnight else 0)]
    zone = ZoneInfo(rule.timezone)
    return [_stamp(day, minute, zone) for minute in minutes]


def _first_bar(instant: int, bars_of, zone: ZoneInfo) -> int | None:
    """The open of the first session bar at or after ``instant``."""
    day = dt.datetime.fromtimestamp(instant / 1000, zone).date()
    for shift in range(-1, DAYS_SEARCHED):
        for stamp in bars_of(day + dt.timedelta(days=shift)):
            if stamp >= instant:
                return stamp
    return None


def _last_bar(instant: int, bars_of, zone: ZoneInfo) -> int | None:
    """The open of the last session bar at or before ``instant``."""
    day = dt.datetime.fromtimestamp(instant / 1000, zone).date()
    for shift in range(1, -DAYS_SEARCHED, -1):
        for stamp in reversed(bars_of(day + dt.timedelta(days=shift))):
            if stamp <= instant:
                return stamp
    return None


def _local(stamp: int, zone: ZoneInfo) -> str:
    try:
        return dt.datetime.fromtimestamp(stamp / 1000, zone).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "no such time"


def range_failure(metrics: dict, extended_hours: bool = False) -> str | None:
    """The condition the record's range fails, or None when it passes the rule
    in the module text. ``extended_hours``: the record is of the symbol's
    extended-hours chart, which the symbol alone does not tell."""
    record = metrics.get("range")
    try:
        covered = record["covered"]
        requested = (record["requested"]["from"], record["requested"]["to"])
        returned = (record["returned"]["from"], record["returned"]["to"])
    except (KeyError, TypeError):
        return "the record holds no `range` of `requested`, `returned` and `covered`"
    if not all(type(stamp) is int for stamp in requested + returned):
        return f"a `from` or `to` of the range is not whole milliseconds: {record}"
    if covered is not True:
        return f"the label `covered` is not true: {covered!r}"
    symbol = metrics.get("symbol")
    rule = SESSION_RULES.get(symbol)
    if rule is None:
        return (f"no session rule for symbol {symbol!r}: state its session hours and time zone "
                f"in {RULES_AT}")
    if rule.hours == ROUND_THE_CLOCK:
        if returned != requested:
            return (f"round-the-clock symbol {symbol}: the returned range {returned[0]} .. {returned[1]} "
                    f"is not the requested range {requested[0]} .. {requested[1]}")
        return None
    hours = rule.hours
    if extended_hours:
        if rule.extended is None:
            return (f"no extended-hours rule for symbol {symbol!r}: state its extended hours "
                    f"in {RULES_AT}")
        hours = rule.extended
    interval = str(metrics.get("interval"))
    if interval in ("1D", "D"):
        if rule.daily is None:
            return (f"no daily-bar rule for symbol {symbol!r}: state the time its daily bar is "
                    f"stamped at in {RULES_AT}")
        bars = "daily bars"
    elif interval.isdigit() and 0 < int(interval) < DAY_MINUTES:
        bars = f"{interval} minutes"
    else:
        return f"no bar rule for interval {interval!r} of session symbol {symbol}"
    zone = ZoneInfo(rule.timezone)

    def bars_of(day: dt.date) -> list[int]:
        return _day_bars(rule, hours, interval, day)

    first = _first_bar(requested[0], bars_of, zone)
    last = _last_bar(requested[1], bars_of, zone)
    chart = f"session symbol {symbol} at {bars} ({hours} {rule.timezone})"
    if first is None or last is None or first > last:
        return f"{chart}: the requested window {requested[0]} .. {requested[1]} holds no session bar"
    if returned[0] != first:
        return (f"{chart}: the returned range starts at {returned[0]} ({_local(returned[0], zone)}), "
                f"but the first session bar at or after the requested start {requested[0]} "
                f"opens at {first} ({_local(first, zone)})")
    if returned[1] != last:
        return (f"{chart}: the returned range ends at {returned[1]} ({_local(returned[1], zone)}), "
                f"but the last session bar at or before the requested end {requested[1]} "
                f"opens at {last} ({_local(last, zone)})")
    return None


def assert_range_covered(metrics: dict, fixture: str, extended_hours: bool = False) -> None:
    """Fail, naming ``fixture`` and the failed condition, unless the record's
    range passes the rule in the module text."""
    __tracebackhide__ = True
    failure = range_failure(metrics, extended_hours)
    if failure is not None:
        raise AssertionError(f"{fixture}: {failure}")
