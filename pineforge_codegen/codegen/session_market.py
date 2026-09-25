"""C++ helper emitted for a chart bar's session.ismarket.

TradingView flags a bar by its own open time: on an extended-hours
NASDAQ:AAPL 60 chart the 09:00 bar, which holds the 09:30 open, is
pre-market and the 16:00 bar post-market. It reads a session's day mask per
session day, so an overnight session's Sunday-evening open belongs to
Monday's, and a ``0000-2400`` day is in market throughout; a D/W/M bar holds
whole session days (``tests/test_e2e_session_ismarket.py`` replays the tapes).

The engine's session calendar answers that instant: ``native_calendar``'s
session day at the bar's open, on the calendar the kernel builds for the run
from the same session and timezone (the adapter reads an empty session as
``24x7`` and an empty timezone as ``UTC``), resolved once per session day as
the kernel resolves it. The time-of-day predicate ``pine_session_*`` tests the
instant's own weekday and does not parse ``2400``; the kernel's per-bar fact
``session_ismarket_`` asks the bar's grid interval, which a 16:00 bar after a
09:30-16:00 session shares with 15:30. The answer depends on the session, the
timezone and the instant only, so the parsed calendar and the last session day
are kept per thread. A session the calendar cannot parse, which the kernel
would not run, keeps the predicate.
"""

SESSION_MARKET_CPP = r"""
static inline bool _pf_session_ismarket(const std::string& session,
                                        const std::string& timezone,
                                        const std::string& chart_tf,
                                        int64_t bar_ms) {
    if (tf_is_daily_or_higher(chart_tf)) return true;
    struct Memo {
        std::string session;
        std::string timezone;
        std::optional<native_calendar::SessionCalendar> calendar;
        std::optional<native_calendar::NativeSessionDay> day;
        bool parsed = false;
    };
    thread_local Memo memo;
    if (!memo.parsed || memo.session != session || memo.timezone != timezone) {
        memo.session = session;
        memo.timezone = timezone;
        memo.calendar = native_calendar::parse_session(
            session.empty() ? std::string_view("24x7") : std::string_view(session),
            timezone.empty() ? std::string_view("UTC") : std::string_view(timezone));
        memo.day.reset();
        memo.parsed = true;
    }
    if (!memo.calendar) return pine_session_ismarket(session, timezone, bar_ms);
    if (memo.day && memo.day->holds(bar_ms)) return memo.day->in_session_at(bar_ms);
    try {
        auto day = native_calendar::session_day_at(*memo.calendar, bar_ms);
        if (!day) return false;
        const bool in_market = day->in_session_at(bar_ms);
        if (day->holds(bar_ms)) memo.day = std::move(day);
        return in_market;
    } catch (...) {
        return pine_session_ismarket(session, timezone, bar_ms);
    }
}
"""
