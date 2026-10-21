"""TradingView-aligned choice sets for `input.*` (compile-time metadata).

PineForge does not render the TradingView Settings UI; these frozensets document
the same **identifier** choices TV offers in common dropdowns so the analyzer
can validate **const** `defval` / `options` expressions with warnings (not hard
errors), matching TV’s intent without a runtime UI.
"""

from __future__ import annotations

import re

# Built-in OHLCV and common derived series (typical "Source" dropdown entries).
INPUT_SOURCE_SERIES_IDS: frozenset[str] = frozenset({
    "open",
    "high",
    "low",
    "close",
    "volume",
    "hl2",
    "hlc3",
    "ohlc4",
    "hlcc4",
})

# Common Pine timeframe strings (chart + typical dropdown values).
# See: https://www.tradingview.com/pine-script-reference/v6/#fun_timeframe
_INPUT_TF_NUMERIC = frozenset(
    str(m) for m in (
        1, 2, 3, 4, 5, 10, 15, 30, 45, 60, 90, 120, 180, 240, 360, 720,
    )
)
_INPUT_TF_SEC = frozenset(f"{s}S" for s in (1, 5, 15, 30, 45))
_INPUT_TF_DAY = frozenset(("1D", "2D", "3D", "D"))
_INPUT_TF_WEEK = frozenset(("1W", "2W", "3W", "W"))
_INPUT_TF_MONTH = frozenset(("1M", "2M", "3M", "6M", "12M", "M"))

INPUT_TIMEFRAME_CHOICES: frozenset[str] = (
    frozenset({""})
    | _INPUT_TF_NUMERIC
    | _INPUT_TF_SEC
    | _INPUT_TF_DAY
    | _INPUT_TF_WEEK
    | _INPUT_TF_MONTH
)

# Loose pattern for valid Pine timeframe literals beyond the canonical set above
# (e.g. custom minute counts).
_TIMEFRAME_RE = re.compile(
    r"^(?:"
    r"\s*|"  # chart
    r"[0-9]+S?|"  # minutes or N-second bars
    r"[0-9]+[DWM]|"  # e.g. 2D, 3W
    r"[DWM]"  # bare D / W / M
    r")$"
)


def is_valid_timeframe_string(s: str) -> bool:
    """Return True if *s* is a plausible Pine timeframe literal."""
    if s in INPUT_TIMEFRAME_CHOICES:
        return True
    return bool(_TIMEFRAME_RE.match(s))


# Typical session presets (informational; many custom strings are valid).
INPUT_SESSION_PRESETS: frozenset[str] = frozenset({
    "",
    "24x7",
    "0000-2359",
})

# HHMM-HHMM Mon-Fri style (same idea as TV session strings).
_SESSION_RANGE_RE = re.compile(r"^\d{4}-\d{4}$")


def is_plausible_session_string(s: str) -> bool:
    """Return True if *s* looks like a valid `input.session` defval."""
    if s in INPUT_SESSION_PRESETS:
        return True
    if _SESSION_RANGE_RE.match(s):
        return True
    # e.g. "0930-1600:23456" with weekday flags
    if re.match(r"^\d{4}-\d{4}(:[0-9]{7})?$", s):
        return True
    return False
