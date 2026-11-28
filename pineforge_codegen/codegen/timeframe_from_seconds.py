"""Pine timeframe conversion, shared by emission and timeframe inventories."""

_MINUTE = 60
_DAY = 24 * 60 * _MINUTE
_MEASURED_MONTH = 2_628_003

# TradingView, measured 2026-11-28: the inclusive (first, last) spans of
# inputs where its answer differs from the earlier rule below. A span of more
# than one input was measured at both of its ends, with the same answer; a
# span of one input is a single reading. Inside a span the count is rounded
# up to whole minutes below one day and to whole days from one day on, except
# that an exact multiple of the measured month, 2,628,003 seconds, is that
# many months.
_MEASURED_SPANS = (
    (45, 45),                  # "1", not "45S"
    (86_341, 86_399),          # "1440", not "1D"
    (518_401, 604_799),        # "7D", not "1W"
    (2_592_001, 2_628_000),    # "31D", not "1M"
    (2_628_003, 2_628_003),    # "1M", not "31D"
    (5_256_000, 5_256_000),    # "61D", not "2M"
    (5_256_006, 5_256_006),    # "2M", not "61D"
    (31_449_601, 31_535_999),  # "365D", not "12M", which starts at 365 days
)

# The earlier rule answers every input outside those spans; every other
# reading agrees with it. Seconds have discrete multipliers. For the remaining
# units, the multiplier is a positive integer in the documented range, and the
# answer is the shortest period at least as long as the input. Order matters
# on ties: the larger unit wins, so 86,400 is 1D and 604,800 is 1W.
# TradingView has not been measured just below 2 to 52 weeks, in the rest of
# the two-month window below 5,256,000, or at 3 to 11 months of 2,628,000 or
# of 2,628,003 seconds. The rule of the spans would answer differently there,
# so each of those inputs keeps this rule until it has a reading. The second
# bands from 7 to 29 and from 31 to 44 have no reading either.
_SECOND_MULTIPLIERS = (1, 5, 10, 15, 30, 45)
_UNITS = (
    (60, 1440, ""),
    (86_400, 365, "D"),
    (604_800, 52, "W"),
    (31_536_000 // 12, 12, "M"),
)
_MONTH, _MAX_MONTHS, _MONTH_SUFFIX = _UNITS[-1]


def _measured_period(seconds: int) -> str:
    """Spell a count inside a measured span the way TradingView returned it."""
    if seconds < _DAY:
        return str((seconds + _MINUTE - 1) // _MINUTE)
    if seconds % _MEASURED_MONTH == 0:
        return f"{seconds // _MEASURED_MONTH}M"
    return f"{(seconds + _DAY - 1) // _DAY}D"


def timeframe_from_seconds(seconds: int) -> str:
    """Return the timeframe string for a present count of seconds."""
    for first, last in _MEASURED_SPANS:
        if first <= seconds <= last:
            return _measured_period(seconds)
    for multiplier in _SECOND_MULTIPLIERS:
        if seconds <= multiplier:
            return f"{multiplier}S"
    duration = _MONTH * _MAX_MONTHS
    result = f"{_MAX_MONTHS}{_MONTH_SUFFIX}"
    for unit, limit, suffix in _UNITS:
        multiplier = 1 + (seconds - 1) // unit
        # A tie keeps the larger unit; see the note on the earlier rule.
        if multiplier <= limit and multiplier * unit <= duration:
            duration = multiplier * unit
            result = f"{multiplier}{suffix}"
    return result


TIMEFRAME_FROM_SECONDS_NAME = "_pf_timeframe_from_seconds"
TIMEFRAME_FROM_SECONDS_MARKER = "/* __PINEFORGE_TIMEFRAME_FROM_SECONDS__ */"


def timeframe_from_seconds_cpp(name: str) -> str:
    """The same rounding rule for an argument computed in the strategy."""
    measured = "\n            || ".join(
        f"(_pf_seconds >= {first} && _pf_seconds <= {last})"
        for first, last in _MEASURED_SPANS
    )
    seconds = ", ".join(str(multiplier) for multiplier in _SECOND_MULTIPLIERS)
    units = "\n".join(
        f'        {{{unit}, {limit}, "{suffix}"}},'
        for unit, limit, suffix in _UNITS
    )
    return f"""static inline std::string {name}(int64_t _pf_seconds) {{
    if (is_na(_pf_seconds)) return "0D";
    if ({measured}) {{
        if (_pf_seconds < {_DAY})
            return std::to_string((_pf_seconds + {_MINUTE - 1}) / {_MINUTE});
        if (_pf_seconds % {_MEASURED_MONTH} == 0)
            return std::to_string(_pf_seconds / {_MEASURED_MONTH}) + "M";
        return std::to_string((_pf_seconds + {_DAY - 1}) / {_DAY}) + "D";
    }}
    constexpr int _pf_seconds_options[] = {{{seconds}}};
    for (int _pf_option : _pf_seconds_options) {{
        if (_pf_seconds <= _pf_option) return std::to_string(_pf_option) + "S";
    }}
    constexpr struct {{
        int64_t _pf_step;
        int _pf_limit;
        const char* _pf_symbol;
    }} _pf_units[] = {{
{units}
    }};
    int64_t _pf_out = {_MONTH * _MAX_MONTHS};
    int64_t _pf_v = {_MAX_MONTHS};
    const char* _pf_symbol = "{_MONTH_SUFFIX}";
    for (const auto& _pf_unit : _pf_units) {{
        const int64_t _pf_multiple = 1 + (_pf_seconds - 1) / _pf_unit._pf_step;
        if (_pf_multiple > _pf_unit._pf_limit) continue;
        const int64_t _pf_candidate = _pf_multiple * _pf_unit._pf_step;
        // A tie keeps the larger unit.
        if (_pf_candidate <= _pf_out) {{
            _pf_out = _pf_candidate;
            _pf_v = _pf_multiple;
            _pf_symbol = _pf_unit._pf_symbol;
        }}
    }}
    return std::to_string(_pf_v) + _pf_symbol;
}}"""
