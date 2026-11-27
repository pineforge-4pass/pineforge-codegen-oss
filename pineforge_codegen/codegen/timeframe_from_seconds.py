"""Pine timeframe conversion, shared by emission and timeframe inventories."""

# Seconds have discrete multipliers. For the remaining units, the multiplier
# is a positive integer in the documented range. Order matters on ties:
# prefer 1D over 1440, and 1W over 7D.
_SECOND_MULTIPLIERS = (1, 5, 10, 15, 30, 45)
_UNITS = (
    (60, 1440, ""),
    (86_400, 365, "D"),
    (604_800, 52, "W"),
    (31_536_000 // 12, 12, "M"),
)
_MONTH, _MAX_MONTHS, _MONTH_SUFFIX = _UNITS[-1]


def timeframe_from_seconds(seconds: int) -> str:
    """Return the shortest supported period at least as long as ``seconds``."""
    for multiplier in _SECOND_MULTIPLIERS:
        if seconds <= multiplier:
            return f"{multiplier}S"
    duration = _MONTH * _MAX_MONTHS
    result = f"{_MAX_MONTHS}{_MONTH_SUFFIX}"
    for unit, limit, suffix in _UNITS:
        multiplier = 1 + (seconds - 1) // unit
        if multiplier <= limit and multiplier * unit <= duration:
            duration = multiplier * unit
            result = f"{multiplier}{suffix}"
    return result


TIMEFRAME_FROM_SECONDS_NAME = "_pf_timeframe_from_seconds"
TIMEFRAME_FROM_SECONDS_MARKER = "/* __PINEFORGE_TIMEFRAME_FROM_SECONDS__ */"


def timeframe_from_seconds_cpp(name: str) -> str:
    """The same rounding rule for an argument computed in the strategy."""
    seconds = ", ".join(str(multiplier) for multiplier in _SECOND_MULTIPLIERS)
    units = "\n".join(
        f'        {{{unit}, {limit}, "{suffix}"}},'
        for unit, limit, suffix in _UNITS
    )
    return f"""static inline std::string {name}(int64_t _pf_seconds) {{
    if (is_na(_pf_seconds)) return na<std::string>();
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
        if (_pf_candidate <= _pf_out) {{
            _pf_out = _pf_candidate;
            _pf_v = _pf_multiple;
            _pf_symbol = _pf_unit._pf_symbol;
        }}
    }}
    return std::to_string(_pf_v) + _pf_symbol;
}}"""
