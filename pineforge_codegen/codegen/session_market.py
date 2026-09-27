"""C++ emitted for a chart bar's session.ismarket when the run has no
timeframe.

TradingView flags a bar by its own open time: on an extended-hours
NASDAQ:AAPL 60 chart the 09:00 bar, which holds the 09:30 open, is
pre-market and the 16:00 bar post-market. It reads a session's day mask per
session day, so an overnight session's Sunday-evening open belongs to
Monday's, and a ``0000-2400`` day is in market throughout; a D/W/M bar holds
whole session days, and a bar that opens in a break and holds the reopen is
in market (``tests/test_e2e_session_ismarket.py`` and
``tests/test_e2e_session_windows.py`` replay the tapes).

The kernel's per-bar fact ``session_ismarket_`` gives all of that: it reads a
bar at its interval's first eligible instant, which is the bar's open unless
the bar opens in a break, and it is what a chart's session.ismarket reads. A
run whose timeframe the engine cannot detect (one bar, none given; its
``script_tf_`` is empty) gets no session-day facts, and there the lowering
asks the engine's session calendar instead: ``native_calendar``'s session day
at the bar's open, on the calendar the kernel builds for the run from the same
session and timezone (the adapter reads an empty session as ``24x7`` and an
empty timezone as ``UTC``), resolved once per session day and read as the
kernel reads it (a day the calendar cannot resolve is out of session). The
time-of-day predicate ``pine_session_*`` tests the instant's own weekday and
does not parse ``2400``.

``SESSION_MARKET_CPP`` precedes the strategy class; each strategy holds one
``SESSION_MARKET_MEMBER`` after its checkpointed script state (a cache of an
answer that depends on the session, the timezone and the instant only). A
session the calendar cannot parse, which the kernel would not run, keeps the
time-of-day predicate.
"""

SESSION_MARKET_CPP = r"""
struct _PFSessionMarket {
    std::string session_key;
    std::string tz_key;
    bool parsed = false;
    std::optional<native_calendar::SessionCalendar> calendar;
    std::optional<native_calendar::NativeSessionDay> day;

    bool operator()(const std::string& session, const std::string& tz,
                    const std::string& chart_tf, int64_t bar_ms) {
        if (tf_is_daily_or_higher(chart_tf)) return true;
        if (!parsed || session_key != session || tz_key != tz) {
            parsed = false;
            day.reset();
            calendar = native_calendar::parse_session(
                session.empty() ? std::string_view("24x7") : std::string_view(session),
                tz.empty() ? std::string_view("UTC") : std::string_view(tz));
            session_key = session;
            tz_key = tz;
            parsed = true;
        }
        if (!calendar) return pine_session_ismarket(session, tz, bar_ms);
        if (day && day->holds(bar_ms)) return day->in_session_at(bar_ms);
        try {
            auto found = native_calendar::session_day_at(*calendar, bar_ms);
            if (!found) return false;
            const bool in_market = found->in_session_at(bar_ms);
            if (found->holds(bar_ms)) day = std::move(found);
            return in_market;
        } catch (...) {
            return false;
        }
    }
};
"""

SESSION_MARKET_MEMBER = "    mutable _PFSessionMarket _pf_session_market_;"
