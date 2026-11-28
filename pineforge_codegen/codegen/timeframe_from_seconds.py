"""Pine timeframe conversion, shared by emission and timeframe inventories."""

# (Last input in seconds, output spelling). C1-9 reads "1" at 45.
# There is no reading between 30 and 45, so retain the old "45S" there.
_SECOND_BANDS = ((1, "1S"), (5, "5S"), (10, "10S"), (15, "15S"),
                 (30, "30S"), (44, "45S"))
_MINUTE = 60
_DAY = 24 * 60 * _MINUTE
_WEEK = 7 * _DAY
_MONTH = 2_628_003
# C1-39/40: the cap is 365 days, not twelve of the measured months.
_YEAR_CAP = 365 * _DAY


def timeframe_from_seconds(seconds: int) -> str:
    """Spell a present count; choose its unit before rounding it up."""
    for limit, period in _SECOND_BANDS:
        if seconds <= limit:
            return period
    if seconds < _DAY:
        return str((seconds + _MINUTE - 1) // _MINUTE)
    if seconds >= _YEAR_CAP:
        return "12M"
    if seconds % _MONTH == 0:
        return f"{seconds // _MONTH}M"
    if seconds % _WEEK == 0:
        return f"{seconds // _WEEK}W"
    return f"{(seconds + _DAY - 1) // _DAY}D"


TIMEFRAME_FROM_SECONDS_NAME = "_pf_timeframe_from_seconds"
TIMEFRAME_FROM_SECONDS_MARKER = "/* __PINEFORGE_TIMEFRAME_FROM_SECONDS__ */"


def timeframe_from_seconds_cpp(name: str) -> str:
    """The same rounding rule for an argument computed in the strategy."""
    seconds = "\n".join(
        f'    if (_pf_seconds <= {limit}) return "{period}";'
        for limit, period in _SECOND_BANDS
    )
    return f"""static inline std::string {name}(int64_t _pf_seconds) {{
    if (is_na(_pf_seconds)) return "0D";
{seconds}
    if (_pf_seconds < {_DAY})
        return std::to_string((_pf_seconds + {_MINUTE - 1}) / {_MINUTE});
    if (_pf_seconds >= {_YEAR_CAP}) return "12M";
    if (_pf_seconds % {_MONTH} == 0) return std::to_string(_pf_seconds / {_MONTH}) + "M";
    if (_pf_seconds % {_WEEK} == 0) return std::to_string(_pf_seconds / {_WEEK}) + "W";
    return std::to_string((_pf_seconds + {_DAY - 1}) / {_DAY}) + "D";
}}"""
