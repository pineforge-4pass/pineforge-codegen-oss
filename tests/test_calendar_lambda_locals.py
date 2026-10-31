"""Requested-context substitutions preserve fixed timezone lambda bodies."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.helpers import NamingHelper
from pineforge_codegen.codegen.security import SecurityEmitter
from pineforge_codegen.codegen.tables import tz_time_field_lambda
from pineforge_codegen.codegen.visit_call import _timestamp_calendar_lambda


HEADER = '//@version=6\nstrategy("calendar locals")\n'

# The fixed suffix has no Pine expressions: its bindings retain their own spelling.
CALENDAR_BODY = (
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

# The hour suffix uses the decomposed tm's hour, with the original TZ restore.
FIELD_BODY = (
    "struct tm tm_buf; "
    'if (_tz.empty() || _tz == "UTC" || _tz == "Etc/UTC" || '
    '!pineforge::native_calendar::timezone_accepted(_tz)) { '
    "gmtime_r(&_secs, &tm_buf); } else { "
    "static std::mutex _pf_tz_mu; "
    "std::lock_guard<std::mutex> _pf_tz_lock(_pf_tz_mu); "
    'const char* _old = std::getenv("TZ"); '
    'std::string _old_tz = _old ? _old : ""; bool _had_old = (_old != nullptr); '
    '::setenv("TZ", _tz.c_str(), 1); ::tzset(); '
    "localtime_r(&_secs, &tm_buf); "
    'if (_had_old) { ::setenv("TZ", _old_tz.c_str(), 1); } '
    'else { ::unsetenv("TZ"); } ::tzset(); } '
    "return tm_buf.tm_hour; }()"
)


def _source(name: str, read: str, calendar: str) -> str:
    return (
        HEADER
        + f"var int {name} = 1\n{name} := int(close)\n"
        + f'out = request.security(syminfo.tickerid, "60", {read} + {calendar})\n'
        + 'if out > 0\n    strategy.entry("L", strategy.long)\n'
    )


def _evaluator(cpp: str) -> str:
    start = cpp.index("void _eval_security_0(")
    return cpp[start:cpp.index("void evaluate_security(", start)]


def _assert_intact_lambda_bodies(cpp: str) -> None:
    # Match the suffix boundaries so a substituted member or type is still inspected.
    bodies = re.findall(
        r"(?:static thread_local std::string _last_tz; |struct tm tm_buf; )"
        r".*?\}\(\)", cpp, re.DOTALL,
    )
    assert bodies  # Every caller supplies at least one timezone lambda.
    for body in bodies:
        assert not re.search(r"_sec\d+_", body)


def _assert_calendar_locals(cpp: str) -> None:
    _assert_intact_lambda_bodies(cpp)
    assert CALENDAR_BODY in cpp  # The calendar's tm, mutex and lock retain their bindings.
    assert not re.search(
        r"(?:struct tm|std::mutex|std::lock_guard<std::mutex>)\s+"
        r"_sec\d+_\w+\s*\[\s*0\s*\]", cpp,
    )  # A current series read is valid in an expression, never as an array declarator.


@pytest.mark.parametrize("calendar", [
    # The numeric overload reads its year, month and day from the requested bar.
    "timestamp(year, month, dayofmonth, 8, 0, 0)",
    # An explicit timezone keeps the same requested calendar fields.
    'timestamp("Asia/Taipei", year, month, dayofmonth, 8, 0, 0)',
])
@pytest.mark.parametrize("name, read, sum_prefix", [
    # The sum reads the scalar requested t outside the calendar's tm scope.
    ("t", "t", "(_sec0_t + [&]() -> int64_t { "),
    # Offset one indexes the requested series in that same sum.
    ("t", "t[1]", "(_sec0_t[1] + [&]() -> int64_t { "),
    # The user's mutex-shaped scalar is the left operand, not the static mutex.
    ("_pf_ts_mu", "_pf_ts_mu", "(_sec0__pf_ts_mu + [&]() -> int64_t { "),
    # History indexes the requested value outside the static mutex's scope.
    ("_pf_ts_mu", "_pf_ts_mu[1]", "(_sec0__pf_ts_mu[1] + [&]() -> int64_t { "),
    # The user's lock-shaped scalar is distinct from the lock_guard binding.
    ("_pf_ts_mu_lock", "_pf_ts_mu_lock", "(_sec0__pf_ts_mu_lock + [&]() -> int64_t { "),
    # Offset one reads the prior requested value beside the ordinary lock_guard.
    ("_pf_ts_mu_lock", "_pf_ts_mu_lock[1]", "(_sec0__pf_ts_mu_lock[1] + [&]() -> int64_t { "),
    # The scalar tm is the requested sum operand, separate from the struct type.
    ("tm", "tm", "(_sec0_tm + [&]()"),
    # Offset one indexes the requested tm series, leaving the struct type intact.
    ("tm", "tm[1]", "(_sec0_tm[1] + [&]()"),
    # The scalar empty is rebound in the sum, never in the timezone's member call.
    ("empty", "empty", "(_sec0_empty + [&]()"),
    # Offset one reads the requested empty series, leaving the member call intact.
    ("empty", "empty[1]", "(_sec0_empty[1] + [&]()"),
    # The scalar mutex is rebound in the sum, separate from the standard type.
    ("mutex", "mutex", "(_sec0_mutex + [&]()"),
    # Offset one indexes the requested mutex series, leaving the standard type intact.
    ("mutex", "mutex[1]", "(_sec0_mutex[1] + [&]()"),
    # The scalar lock_guard is rebound in the sum, separate from the lock type.
    ("lock_guard", "lock_guard", "(_sec0_lock_guard + [&]()"),
    # Offset one reads the requested lock_guard series, leaving the lock type intact.
    ("lock_guard", "lock_guard[1]", "(_sec0_lock_guard[1] + [&]()"),
    # The scalar tm_year is rebound in the sum, separate from the tm member.
    ("tm_year", "tm_year", "(_sec0_tm_year + [&]()"),
    # Offset one indexes the requested tm_year series, leaving the year member intact.
    ("tm_year", "tm_year[1]", "(_sec0_tm_year[1] + [&]()"),
    # The scalar tm_mon is rebound in the sum, separate from the tm member.
    ("tm_mon", "tm_mon", "(_sec0_tm_mon + [&]()"),
    # Offset one indexes the requested tm_mon series, leaving the month member intact.
    ("tm_mon", "tm_mon[1]", "(_sec0_tm_mon[1] + [&]()"),
    # The scalar tm_mday is rebound in the sum, separate from the tm member.
    ("tm_mday", "tm_mday", "(_sec0_tm_mday + [&]()"),
    # Offset one indexes the requested tm_mday series, leaving the day member intact.
    ("tm_mday", "tm_mday[1]", "(_sec0_tm_mday[1] + [&]()"),
    # The scalar tm_hour is rebound in the sum, separate from the tm member.
    ("tm_hour", "tm_hour", "(_sec0_tm_hour + [&]()"),
    # Offset one indexes the requested tm_hour series, leaving the hour member intact.
    ("tm_hour", "tm_hour[1]", "(_sec0_tm_hour[1] + [&]()"),
    # The scalar tm_min is rebound in the sum, separate from the tm member.
    ("tm_min", "tm_min", "(_sec0_tm_min + [&]()"),
    # Offset one indexes the requested tm_min series, leaving the minute member intact.
    ("tm_min", "tm_min[1]", "(_sec0_tm_min[1] + [&]()"),
    # The scalar tm_sec is rebound in the sum, separate from the tm member.
    ("tm_sec", "tm_sec", "(_sec0_tm_sec + [&]()"),
    # Offset one indexes the requested tm_sec series, leaving the second member intact.
    ("tm_sec", "tm_sec[1]", "(_sec0_tm_sec[1] + [&]()"),
    # The scalar tm_isdst is rebound in the sum, separate from the tm member.
    ("tm_isdst", "tm_isdst", "(_sec0_tm_isdst + [&]()"),
    # Offset one indexes the requested tm_isdst series, leaving the DST member intact.
    ("tm_isdst", "tm_isdst[1]", "(_sec0_tm_isdst[1] + [&]()"),
    # The scalar timegm is rebound in the sum, separate from the UTC conversion call.
    ("timegm", "timegm", "(_sec0_timegm + [&]()"),
    # Offset one reads the requested timegm series, leaving the conversion call intact.
    ("timegm", "timegm[1]", "(_sec0_timegm[1] + [&]()"),
    # The scalar mktime is rebound in the sum, separate from the local conversion call.
    ("mktime", "mktime", "(_sec0_mktime + [&]()"),
    # Offset one reads the requested mktime series, leaving the conversion call intact.
    ("mktime", "mktime[1]", "(_sec0_mktime[1] + [&]()"),
    # The scalar c_str is rebound in the sum, separate from the timezone's member call.
    ("c_str", "c_str", "(_sec0_c_str + [&]()"),
    # Offset one indexes the requested c_str series, leaving the member calls intact.
    ("c_str", "c_str[1]", "(_sec0_c_str[1] + [&]()"),
    # The scalar int64_t is rebound in the sum; the fixed body's integer types stay.
    ("int64_t", "int64_t", "(_sec0_int64_t + [&]()"),
    # Offset one reads the requested int64_t series; the fixed body's types stay.
    ("int64_t", "int64_t[1]", "(_sec0_int64_t[1] + [&]()"),
])
def test_calendar_locals_survive_mutable_request_names(calendar, name, read, sum_prefix):
    body = _evaluator(transpile(_source(name, read, calendar)))
    _assert_calendar_locals(body)
    assert sum_prefix in body  # A replayed assignment cannot satisfy the payload's sum.
    assert "pine_year(bar.timestamp, syminfo_.timezone)" in body  # Fields use the requested bar.


@pytest.mark.parametrize("calendar, prefix", [
    # The numeric overload takes the exchange zone and the six literal fields.
    ("timestamp(1970, 1, 2, 8, 0, 0)",
     "[&]() -> int64_t { "
     "std::string _tz = pineforge::normalize_timezone_for_posix((syminfo_.timezone)); "
     "int _yr = (1970); int _mo = (1); int _dy = (2); "
     "int _hr = (8); int _min = (0); int _sc = (0); "),
    # The timezone overload differs only in its explicit string expression.
    ('timestamp("Asia/Taipei", 1970, 1, 2, 8, 0, 0)',
     "[&]() -> int64_t { "
     'std::string _tz = pineforge::normalize_timezone_for_posix((std::string("Asia/Taipei"))); '
     "int _yr = (1970); int _mo = (1); int _dy = (2); "
     "int _hr = (8); int _min = (0); int _sc = (0); "),
])
def test_colliding_and_noncolliding_scripts_keep_the_original_calendar_bytes(calendar, prefix):
    plain = _evaluator(transpile(_source("counter", "counter[1]", calendar)))
    _assert_intact_lambda_bodies(plain)
    assert prefix + CALENDAR_BODY in plain
    # The collision twin fails until the same calendar suffix is preserved there.
    colliding = _evaluator(transpile(_source("t", "t[1]", calendar)))
    _assert_intact_lambda_bodies(colliding)
    assert prefix + CALENDAR_BODY in colliding


@pytest.mark.parametrize("name", [
    "t",  # The helper's integer local cannot replace the calendar's tm.
    "_pf_ts_mu",  # The helper's integer local cannot replace the calendar's mutex.
    "_pf_ts_mu_lock",  # The helper's integer local cannot replace the calendar's lock.
])
def test_calendar_locals_survive_helper_local_bindings(name):
    source = (
        HEADER + "calendar() =>\n"
        + f"    {name} = int(close)\n"
        + f"    timestamp(1970, 1, 2, 8, 0, {name})\n"
        + 'out = request.security(syminfo.tickerid, "60", calendar())\n'
    )
    body = _evaluator(transpile(source))
    _assert_calendar_locals(body)  # Helper substitutions leave the calendar's own bindings intact.
    assert "int _sec0_calendar_" in body  # The helper still has its requested-context local.


def test_calendar_locals_survive_helper_loop_counter_binding():
    source = HEADER + """calendar() =>
    stamp = 0
    for t = 0 to 1
        stamp := timestamp(1970, 1, 2, 8, 0, int(t))
    stamp
out = request.security(syminfo.tickerid, "60", calendar())
"""
    body = _evaluator(transpile(source))
    _assert_calendar_locals(body)  # The loop's t leaves the calendar's tm binding intact.
    # A counted loop declares its requested-context t from its saved start value.
    assert re.search(
        r"for \(int _sec0_calendar_\d+_t = _sec0_calendar_\d+_for_start;", body,
    )
    # The explicit seconds cast reads the requested loop counter once.
    assert re.search(
        r"int _sc = \(\[&\]\(\)\{ auto _pf_v = \(_sec0_calendar_\d+_t\); ", body,
    )


def test_calendar_locals_survive_helper_tuple_element_binding():
    source = HEADER + """parts() =>
    [int(close), int(open)]
calendar() =>
    [t, _] = parts()
    timestamp(1970, 1, 2, 8, 0, int(t))
out = request.security(syminfo.tickerid, "60", calendar())
"""
    body = _evaluator(transpile(source))
    _assert_calendar_locals(body)  # The tuple's t leaves the calendar's tm binding intact.
    # Element zero binds t to the helper's requested-context tuple result.
    assert re.search(
        r"auto _sec0_calendar_\d+_t = std::get<0>\(_sec0_calendar_\d+_tuple\);", body,
    )
    # The explicit seconds cast reads the requested tuple element once.
    assert re.search(
        r"int _sc = \(\[&\]\(\)\{ auto _pf_v = \(_sec0_calendar_\d+_t\); ", body,
    )


def test_all_colliding_calendar_arguments_still_read_requested_history():
    source = HEADER + """var int t = 1
var int _pf_ts_mu = 2
var int _pf_ts_mu_lock = 3
t := int(close)
_pf_ts_mu := int(open)
_pf_ts_mu_lock := int(high)
out = request.security(syminfo.tickerid, "60", timestamp(1970, 1, 2, t[1], _pf_ts_mu[1], _pf_ts_mu_lock[1]))
"""
    body = _evaluator(transpile(source))
    _assert_calendar_locals(body)
    # Hour, minute and second evaluate their distinct requested series before the local tm exists.
    assert (
        "int _hr = (_sec0_t[1]); int _min = (_sec0__pf_ts_mu[1]); "
        "int _sc = (_sec0__pf_ts_mu_lock[1]); "
    ) in body


class _RewriteHost(SecurityEmitter, NamingHelper):
    def __init__(self, *names: str):
        self._global_mutable_infos = {
            name: SimpleNamespace(is_series=True) for name in names
        }
        self._security_string_series = set()
        self._security_color_series = set()


def test_calendar_body_preserves_mutable_and_helper_bindings():
    host = _RewriteHost("mutex", "tm_year", "t")
    host._global_mutable_infos["mutex"].is_series = False
    calendar = _timestamp_calendar_lambda(
        'std::string("UTC")', "1970", "1", "2",
        "mutex + tm_year + tm_year[1]", "empty + c_str + c_str[1]", "t[1]",
    )
    rendered = host._rewrite_security_cpp(
        calendar + " + mutex + tm_year + tm_year[1] + empty + c_str + c_str[1]"
        + ' + std::string("mutex tm_year empty c_str t").size() + \'t\'',
        0, {"mutex", "tm_year", "t"},
        ({"empty": "_sec0_calendar_empty", "c_str": "@series:calendar:c_str"},),
    )
    _assert_calendar_locals(rendered)
    # The hour binds the scalar mutex, current tm_year and prior tm_year in order.
    assert "int _hr = (_sec0_mutex + _sec0_tm_year[0] + _sec0_tm_year[1]); " in rendered
    # The minute binds a helper scalar and both the current and prior helper series.
    assert (
        'int _min = (_sec0_calendar_empty + _security_helper_series_["calendar:c_str"][0]'
        ' + _security_helper_series_["calendar:c_str"][1]); '
    ) in rendered
    assert "int _sc = (_sec0_t[1]); " in rendered  # The seconds read the prior requested t.
    # The same bindings reach the trailing reads; quoted names and the character stay literal.
    assert rendered.endswith(
        " + _sec0_mutex + _sec0_tm_year[0] + _sec0_tm_year[1] + _sec0_calendar_empty"
        ' + _security_helper_series_["calendar:c_str"][0]'
        ' + _security_helper_series_["calendar:c_str"][1]'
        ' + std::string("mutex tm_year empty c_str t").size() + \'t\''
    )


def test_calendar_body_preserves_string_helper_series_binding():
    host = _RewriteHost()
    host._security_string_series = {"calendar:empty"}
    calendar = _timestamp_calendar_lambda(
        '(empty == std::string("") ? empty[1] : empty)', "1970", "1", "2", "8", "0", "0",
    )
    rendered = host._rewrite_security_cpp(
        calendar, 0, set(), ({"empty": "@series:calendar:empty"},),
    )
    _assert_calendar_locals(rendered)
    # The timezone reads current, prior and current string history in its original ternary.
    assert (
        "std::string _tz = pineforge::normalize_timezone_for_posix("
        '((_security_helper_series_str_["calendar:empty"][0] == std::string("")'
        ' ? _security_helper_series_str_["calendar:empty"][1]'
        ' : _security_helper_series_str_["calendar:empty"][0]))); '
    ) in rendered


def test_nested_calendar_arguments_rewrite_without_capturing_either_lambda():
    host = _RewriteHost("t", "_pf_ts_mu", "_pf_ts_mu_lock")
    inner = _timestamp_calendar_lambda(
        'std::string("UTC")', "1970", "1", "2", "8", "0", "t[1]",
    )
    outer = _timestamp_calendar_lambda(
        'std::string("Asia/Taipei")', "1970", "1", "2", "8", "0",
        f"({inner}) / 1000 + _pf_ts_mu[1] + _pf_ts_mu_lock[1]",
    )
    rendered = host._rewrite_security_cpp(
        outer + ' + t[1] + std::string("t _pf_ts_mu _pf_ts_mu_lock").size()',
        0, {"t", "_pf_ts_mu", "_pf_ts_mu_lock"},
    )
    _assert_intact_lambda_bodies(rendered)
    assert rendered.count(CALENDAR_BODY) == 2  # Each of the two calls owns its own tm and mutex.
    assert "int _sc = (_sec0_t[1]); " in rendered  # The inner second is the user's prior t.
    assert "/ 1000 + _sec0__pf_ts_mu[1] + _sec0__pf_ts_mu_lock[1]" in rendered  # Outer seconds use both histories.
    assert ' + _sec0_t[1] + std::string("t _pf_ts_mu _pf_ts_mu_lock").size()' in rendered  # Only the outside read changes.
    # A surrounding fallback may rewrite the already-rendered expression again.
    repeated = host._rewrite_security_cpp(rendered, 0, {"t", "_pf_ts_mu", "_pf_ts_mu_lock"})
    _assert_intact_lambda_bodies(repeated)
    assert repeated.count(CALENDAR_BODY) == 2  # Neither pass changes either local scope.
    assert "int _sc = (_sec0_t[1]); " in repeated  # Rebinding does not add another request prefix.


@pytest.mark.parametrize("name, seconds", [
    # The decomposed tm and the requested history named tm_buf occupy different scopes.
    ("tm_buf", "time_t _secs = (time_t)((_sec0_tm_buf[1] + bar.timestamp) / 1000); "),
    # Only the timestamp argument reads the requested mutex-shaped user name.
    ("_pf_tz_mu", "time_t _secs = (time_t)((_sec0__pf_tz_mu[1] + bar.timestamp) / 1000); "),
    # The lock name likewise remains local while its user namesake is rebound.
    ("_pf_tz_lock", "time_t _secs = (time_t)((_sec0__pf_tz_lock[1] + bar.timestamp) / 1000); "),
])
def test_time_field_template_locals_survive_request_rewriting(name, seconds):
    # This retained template has no live time-field call sites.
    host = _RewriteHost(name)
    rendered = host._rewrite_security_cpp(
        tz_time_field_lambda("tm_buf.tm_hour", f"{name}[1] + current_bar_.timestamp",
                             'std::string("Asia/Taipei")'),
        0, {name},
    )
    _assert_intact_lambda_bodies(rendered)
    assert FIELD_BODY in rendered
    assert seconds in rendered


@pytest.mark.parametrize("calendar, expected", [
    # UTC: (24 + 8) * 3600000 + prior seconds 7000/11000; prior closes are 7/11.
    ("timestamp(1970, 1, 2, 8, 0, t[1])", "115207000 7\n115211000 11\n"),
    # Taipei is UTC+8: 24 * 3600000 + 7000/11000; history still reads 7/11.
    ('timestamp("Asia/Taipei", 1970, 1, 2, 8, 0, t[1])', "86407000 7\n86411000 11\n"),
])
@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_calendar_and_requested_history_values_in_engine(calendar, expected, opt):
    """Needs the compiled engine environment: compile and run the complete emitted translation unit."""
    from tests._compile import run_emitted_tu

    source = (
        HEADER + "var int t = 0\nt := int(close)\n"
        + '[stamp, previous] = request.security(syminfo.tickerid, "1", '
        + f"[bar_index > 0 ? {calendar} : 0, t[1]])\n"
    )
    cpp = transpile(source)
    _assert_calendar_locals(_evaluator(cpp))
    driver = r'''
#include <iomanip>
#include <iostream>
int main() {
    Bar bars[] = {
        {7.0, 7.0, 7.0, 7.0, 1.0, 0},
        {11.0, 11.0, 11.0, 11.0, 1.0, 60000},
        {19.0, 19.0, 19.0, 19.0, 1.0, 120000},
    };
    std::cout << std::fixed << std::setprecision(0);
    for (int count = 2; count <= 3; ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars, count, "1", "1");
        std::cout << strategy.stamp << " " << strategy.previous << "\n";
    }
}
'''
    assert run_emitted_tu(cpp, driver, opt=opt, label="calendar history") == expected
