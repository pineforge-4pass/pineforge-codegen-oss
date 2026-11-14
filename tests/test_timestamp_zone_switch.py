"""The timestamp rejection switch changes only its branch header."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.run_stops import RUN_STOP_SHIMS_CPP
from pineforge_codegen.codegen.tables import (
    TIMESTAMP_CALENDAR_BODY,
    TIMESTAMP_REJECTED_ZONE_STOPS_RUN,
    build_timestamp_calendar_body,
)


OLD_CALENDAR_BODY = (
    "static thread_local std::string _last_tz; "
    "static thread_local int _last_yr = -1, _last_mo = -1, _last_dy = -1, _last_hr = -1, _last_min = -1, _last_sc = -1; "
    "static thread_local int64_t _last_res = -1; "
    "if (_last_res != -1 && _last_tz == _tz && _last_yr == _yr && _last_mo == _mo && _last_dy == _dy && _last_hr == _hr && _last_min == _min && _last_sc == _sc) { "
    "return _last_res; } "
    "struct tm t = {}; "
    "t.tm_year = _yr - 1900; t.tm_mon = _mo - 1; "
    "t.tm_mday = _dy; t.tm_hour = _hr; t.tm_min = _min; t.tm_sec = _sc; "
    "t.tm_isdst = -1; int64_t _res; "
    'if (_tz.empty() || _tz == "UTC" || _tz == "Etc/UTC" || '
    '!pineforge::native_calendar::timezone_accepted(_tz)) { '
    "_res = (int64_t)timegm(&t) * 1000; } else { "
    "static std::mutex _pf_ts_mu; "
    "std::lock_guard<std::mutex> _pf_ts_mu_lock(_pf_ts_mu); "
    'const char* _old = std::getenv("TZ"); '
    'std::string _old_tz = _old ? _old : ""; bool _had_old = (_old != nullptr); '
    '::setenv("TZ", _tz.c_str(), 1); ::tzset(); '
    "_res = (int64_t)mktime(&t) * 1000; "
    'if (_had_old) { ::setenv("TZ", _old_tz.c_str(), 1); } '
    'else { ::unsetenv("TZ"); } ::tzset(); } '
    "_last_tz = _tz; _last_yr = _yr; _last_mo = _mo; _last_dy = _dy; "
    "_last_hr = _hr; _last_min = _min; _last_sc = _sc; "
    "_last_res = _res; return _res; }()"
)

OLD_BRANCH_HEADER = (
    'if (_tz.empty() || _tz == "UTC" || _tz == "Etc/UTC" || '
    '!pineforge::native_calendar::timezone_accepted(_tz)) { '
    '_res = (int64_t)timegm(&t) * 1000; } else { '
)
STOP_BRANCH = (
    '} else if (!pineforge::native_calendar::timezone_accepted(_tz)) { '
    '_PF_TIMEZONE_STOP("timestamp", _tz.c_str(), '
    r'(std::string("Incorrect `timezone` value for the timestamp() function: \"") '
    r'+ _tz + "\". The value must be a string in either UTC/GMT notation '
    r'(e.g. \"UTC-5\", \"GMT+0530\") or an IANA time zone database name '
    r'(e.g. \"America/New_York\").").c_str()); } else { '
)
STOP_BRANCH_HEADER = (
    'if (_tz.empty() || _tz == "UTC" || _tz == "Etc/UTC") { '
    '_res = (int64_t)timegm(&t) * 1000; '
    + STOP_BRANCH
)
TIMEZONE_STOP_MACROS = """#ifdef PINEFORGE_HAS_TIMEZONE_STOP_V1
#define _PF_TIMEZONE_STOP(function, zone, english) ::pineforge::pine_timezone_stop(function, zone, english)
#else
#define _PF_TIMEZONE_STOP(function, zone, english) pine_runtime_error(std::string(english))
#endif
"""

HEADER = '//@version=6\nstrategy("zone switch")\n'


def test_false_restores_exact_old_calendar_body():
    """Pin the complete old timestamp suffix when stopping is disabled."""
    assert build_timestamp_calendar_body(False) == OLD_CALENDAR_BODY


def test_true_changes_only_the_rejected_zone_branch_header():
    """Pin the exact stop text and preserve every byte outside the branch header."""
    old = build_timestamp_calendar_body(False)
    stopping = build_timestamp_calendar_body(True)
    assert old.count(OLD_BRANCH_HEADER) == 1
    assert stopping.count(STOP_BRANCH_HEADER) == 1
    assert STOP_BRANCH in stopping
    assert stopping.replace(STOP_BRANCH_HEADER, "", 1) == old.replace(
        OLD_BRANCH_HEADER, "", 1,
    )


def test_calendar_body_follows_the_single_switch():
    """Pin the exported suffix to the module switch's selected body."""
    assert TIMESTAMP_CALENDAR_BODY == build_timestamp_calendar_body(
        TIMESTAMP_REJECTED_ZONE_STOPS_RUN,
    )


def test_rejected_zone_stops_are_enabled_by_default():
    """Pin stopping as the default policy for rejected timestamp zones."""
    assert TIMESTAMP_REJECTED_ZONE_STOPS_RUN is True


def test_timestamp_emits_one_stop_call_and_one_macro_block():
    """Pin one timestamp stop call and one complete guarded compatibility block."""
    cpp = transpile(
        HEADER + 'x = timestamp("America/New_York", 2024, 1, 15, 8, 0, 0)\n'
    )
    assert cpp.count('_PF_TIMEZONE_STOP("timestamp", _tz.c_str(),') == 1
    assert "PINEFORGE_HAS_TIMEZONE_STOP_V1" not in RUN_STOP_SHIMS_CPP
    assert cpp.count(RUN_STOP_SHIMS_CPP) == 1
    assert cpp.count(TIMEZONE_STOP_MACROS) == 1
    assert cpp.index(RUN_STOP_SHIMS_CPP) < cpp.index(TIMEZONE_STOP_MACROS)


def test_script_without_timestamp_emits_no_timezone_block():
    """Pin the absence of timezone macros in scripts without timestamp."""
    cpp = transpile(HEADER + "x = close\n")
    assert "PINEFORGE_HAS_TIMEZONE_STOP_V1" not in cpp
    assert "_PF_TIMEZONE_STOP" not in cpp


def test_date_string_timestamp_emits_no_timezone_block():
    """A folded date-string timestamp needs no calendar stop macro."""
    cpp = transpile(HEADER + 'x = timestamp("01 Jan 2024 00:00 +0000")\n')
    assert "PINEFORGE_HAS_TIMEZONE_STOP_V1" not in cpp
    assert "_PF_TIMEZONE_STOP" not in cpp


@pytest.mark.parametrize(
    "source",
    [
        'stamp(h) => timestamp("America/New_York", 2024, 1, 15, h, 0, 0)\n'
        "x = stamp(8)\n",
        'x = request.security(syminfo.tickerid, "60", '
        'timestamp("America/New_York", 2024, 1, 15, 8, 0, 0))\n',
        "x = timestamp(year = 2024, month = 1, day = 15)\n",
        "x = timestamp(2024, 1, 15, 8, 0, 0)\n",
    ],
    ids=["user-function", "security-payload", "keywords", "exchange-zone"],
)
def test_timestamp_in_other_positions_emits_the_block_once(source):
    """Find calendar timestamp calls throughout syntax and both argument forms."""
    cpp = transpile(HEADER + source)
    assert cpp.count(TIMEZONE_STOP_MACROS) == 1
    assert cpp.count('_PF_TIMEZONE_STOP("timestamp", _tz.c_str(),') >= 1
