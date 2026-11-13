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
from tests._numeric_expected import integer_arithmetic, integer_to_double


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
    'if (_tz.empty() || _tz == "UTC" || _tz == "Etc/UTC") { '
    "_res = (int64_t)timegm(&t) * 1000; "
    '} else if (!pineforge::native_calendar::timezone_accepted(_tz)) { '
    '_PF_TIMEZONE_STOP("timestamp", _tz.c_str(), '
    r'(std::string("Incorrect `timezone` value for the timestamp() function: \"") '
    r'+ _tz + "\". The value must be a string in either UTC/GMT notation '
    r'(e.g. \"UTC-5\", \"GMT+0530\") or an IANA time zone database name '
    r'(e.g. \"America/New_York\").").c_str()); '
    "} else { "
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
    ("tm", "tm", "(_sec0_pf_safe_tm + [&]()"),
    # Offset one indexes the requested tm series, leaving the struct type intact.
    ("tm", "tm[1]", "(_sec0_pf_safe_tm[1] + [&]()"),
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
    ("int64_t", "int64_t", "(_sec0_pf_safe_int64_t + [&]()"),
    # Offset one reads the requested int64_t series; the fixed body's types stay.
    ("int64_t", "int64_t[1]", "(_sec0_pf_safe_int64_t[1] + [&]()"),
])
def test_calendar_locals_survive_mutable_request_names(calendar, name, read, sum_prefix):
    body = _evaluator(transpile(_source(name, read, calendar)))
    _assert_calendar_locals(body)
    # The potentially missing integer is staged before the unchanged calendar lambda.
    left, calendar_prefix = sum_prefix[1:].split(" + ", 1)
    operands = f"auto _pna_l = ({left}); auto _pna_r = ({calendar_prefix}"
    assert operands in body
    timezone = (
        'std::string("Asia/Taipei")' if '"Asia/Taipei"' in calendar
        else "syminfo_.timezone"
    )
    calendar_value = (
        "[&]() -> int64_t { "
        f"std::string _tz = pineforge::normalize_timezone_for_posix(({timezone})); "
        "int _yr = (pine_year(bar.timestamp, syminfo_.timezone)); "
        "int _mo = (pine_month(bar.timestamp, syminfo_.timezone)); "
        "int _dy = (pine_dayofmonth(bar.timestamp, syminfo_.timezone)); "
        "int _hr = (8); int _min = (0); int _sc = (0); "
        + CALENDAR_BODY
    )
    # Pin the entire store: ordered captures, unchanged calendar bytes, both
    # original-type guards, conditional int64 result, pre-operation casts and
    # the final na-preserving conversion to the request's double result.
    expected = integer_to_double(integer_arithmetic(left, "+", calendar_value))
    assert body.count(f"_req_sec_0 = {expected};") == 1
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
    "int64_t",  # A renamed helper local must leave the lambda's return type usable.
    "size_t",  # Native size and time aliases follow the same binding rule.
    "time_t",
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
    # The helper still has its requested-context local; int(close) is a 64-bit int.
    assert "int64_t _sec0_calendar_" in body


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
    # The three series hold int(float) values, which are int64_t, so each int argument reads its
    # series through the na-preserving narrowing into the calendar's 32-bit int.
    def read(series: str) -> str:
        series = re.escape(series)
        return (
            rf"\((?:{series}"
            rf"|\[&\]\(\)\{{ auto _pf_v = \({series}\); .*?\}}\(\))\)"
        )

    assert re.search(
        "int _hr = " + read("_sec0_t[1]") + "; "
        "int _min = " + read("_sec0__pf_ts_mu[1]") + "; "
        "int _sc = " + read("_sec0__pf_ts_mu_lock[1]") + "; ",
        body,
    ), body


class _RewriteHost(SecurityEmitter, NamingHelper):
    def __init__(self, *names: str):
        self._global_mutable_infos = {
            name: SimpleNamespace(is_series=True) for name in names
        }
        self._security_string_series = set()
        self._security_color_series = set()


@pytest.mark.parametrize("return_type", [
    "ResultType",
    "const std::pair<ResultType, OtherType*>&",
    "decltype(auto)",
])
@pytest.mark.parametrize("binding", [
    "mutable-scalar", "mutable-series", "helper-scalar", "helper-series",
])
def test_nested_lambda_return_types_keep_value_rebinding(return_type, binding):
    host = _RewriteHost("ResultType") if binding.startswith("mutable") else _RewriteHost()
    mutable = {"ResultType"} if binding.startswith("mutable") else set()
    stack = None
    if binding == "mutable-scalar":
        host._global_mutable_infos["ResultType"].is_series = False
        value = "_sec0_ResultType"
    elif binding == "mutable-series":
        value = "_sec0_ResultType[0] + _sec0_ResultType[1]"
    elif binding == "helper-scalar":
        stack = ({"ResultType": "_sec0_helper_ResultType"},)
        value = "_sec0_helper_ResultType"
    else:
        stack = ({"ResultType": "@series:helper:ResultType"},)
        value = (
            '_security_helper_series_["helper:ResultType"][0]'
            ' + _security_helper_series_["helper:ResultType"][1]'
        )
    read = "ResultType + ResultType[1]" if binding.endswith("series") else "ResultType"
    # Signature-like strings and comments cannot start a protected C++ scope.
    suffix = (
        ' + std::string(") -> ResultType { ResultType[1] }").size()'
        " + 'R' /* ResultType[1] ) -> OtherType { */"
        " // ResultType[1] -> OtherType {\n"
    )
    original = (
        f"([&]() -> {return_type} {{ return consume({read}, "
        f"[&]()->{return_type}{{ return {read}; }}()); }}())"
        + suffix + read
    )
    expected = (
        f"([&]() -> {return_type} {{ return consume({value}, "
        f"[&]()->{return_type}{{ return {value}; }}()); }}())"
        + suffix + value
    )
    rendered = host._rewrite_security_cpp(original, 0, mutable, stack)
    assert rendered == expected
    # A surrounding fallback may pass the same nested expression through again.
    assert host._rewrite_security_cpp(rendered, 0, mutable, stack) == expected


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


@pytest.mark.parametrize("timezone,stamp", [
    ("UTC", 1704096000000),
    ("Asia/Taipei", 1704067200000),
])
@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_native_calendar_sum_preserves_missing_requested_history(timezone, stamp, opt):
    """Exercise the guarded sum's consumer; this source has no TradingView tape."""
    from tests._compile import run_emitted_tu

    source = (
        HEADER + "var int t = 1\nt := int(close)\n"
        "if close > open\n    t := int(na)\n"
        'float out = request.security(syminfo.tickerid, "1", '
        f't[1] + timestamp("{timezone}", 2024, 1, 1, 8, 0, 0))\n'
    )
    cpp = transpile(source)
    _assert_calendar_locals(_evaluator(cpp))
    driver = r"""
#include <cmath>
#include <cstdio>
#include <type_traits>

int main() {
    const double closes[] = {7.0, 101.0, -7.0, 0.0, 11.0, 19.0};
    constexpr unsigned size = sizeof(closes) / sizeof(closes[0]);
    pineforge::Bar bars[size];
    for (unsigned index = 0; index < size; ++index) {
        bars[index] = pineforge::Bar{
            100.0, 102.0, closes[index] - 1.0, closes[index], 10.0,
            1704067200000LL + index * 60000LL,
        };
    }
    for (unsigned count = 1; count <= size; ++count) {
        GeneratedStrategy strategy;
        static_assert(std::is_same_v<decltype(strategy.out), double>);
        strategy.run(bars, count, "1", "1");
        if (!strategy.last_error().empty()) return 1;
        const bool missing = count == 1 || closes[count - 2] > 100.0;
        if (missing) {
            if (!std::isnan(strategy.out)) return 2;
        } else {
            const double expected = CALENDAR_STAMP + closes[count - 2];
            if (!std::isfinite(strategy.out) || strategy.out != expected) {
                std::printf("bar=%u expected=%.17g actual=%.17g\n",
                            count - 1, expected, strategy.out);
                return 3;
            }
        }
    }
    std::puts("ok");
}
"""
    assert run_emitted_tu(
        cpp, driver.replace("CALENDAR_STAMP", str(stamp)), opt=opt,
        label="calendar-sum-missing-history",
    ) == "ok\n"


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
@pytest.mark.parametrize("name", ["counter", "int64_t"])
@pytest.mark.parametrize("history", [False, True], ids=["current", "history"])
@pytest.mark.parametrize("timezone,offset", [
    pytest.param(None, 8 * 3600000, id="exchange-UTC"),
    pytest.param("Asia/Taipei", 0, id="explicit-Taipei"),
])
def test_native_calendar_return_type_and_requested_values(name, history, timezone, offset, opt):
    """Compile the actual emitted host; the safe-name twin checks the same setup."""
    from tests._compile import run_emitted_tu

    read = f"{name}[1]" if history else name
    fields = "year, month, dayofmonth, 8, 0, 0"
    calendar = f'timestamp("{timezone}", {fields})' if timezone else f"timestamp({fields})"
    source = (
        HEADER + f"var int {name} = 1\n{name} := int(close)\n"
        + f"if close > open\n    {name} := int(na)\n"
        + 'float out = request.security(syminfo.tickerid, "1", '
        + f"{read} + {calendar})\n"
    )
    cpp = transpile(source)
    _assert_calendar_locals(_evaluator(cpp))
    driver = r"""
#include <cmath>
#include <cstdio>
#include <type_traits>

int main() {
    const double closes[] = {7.0, 101.0, -7.0, 0.0, 11.0, 19.0};
    constexpr unsigned size = sizeof(closes) / sizeof(closes[0]);
    pineforge::Bar bars[size];
    for (unsigned index = 0; index < size; ++index) {
        // Jan 1 at 23:58 UTC through Jan 2 at 00:03 UTC. The calendar
        // must follow each requested bar's date, including the midnight.
        bars[index] = pineforge::Bar{
            100.0, 102.0, closes[index] - 1.0, closes[index], 10.0,
            1704153480000LL + index * 60000LL,
        };
    }
    for (unsigned count = 1; count <= size; ++count) {
        GeneratedStrategy strategy;
        static_assert(std::is_same_v<decltype(strategy.out), double>);
        strategy.run(bars, count, "1", "1");
        if (!strategy.last_error().empty()) return 1;
        const int input = static_cast<int>(count) - 1 - HISTORY_OFFSET;
        const bool missing = input < 0 || closes[input] > 100.0;
        if (missing) {
            if (!std::isnan(strategy.out)) return 2;
        } else {
            // Both overloads take the date from the UTC exchange calendar.
            // 08:00 UTC is day + 8h; 08:00 Taipei is day + 0h.
            const auto day = bars[count - 1].timestamp / 86400000LL * 86400000LL;
            const double expected = day + CALENDAR_OFFSET + closes[input];
            if (!std::isfinite(strategy.out) || strategy.out != expected) {
                std::printf("bar=%u expected=%.17g actual=%.17g\n",
                            count - 1, expected, strategy.out);
                return 3;
            }
        }
    }
    std::puts("ok");
}
"""
    driver = driver.replace("HISTORY_OFFSET", str(int(history))).replace(
        "CALENDAR_OFFSET", str(offset),
    )
    assert run_emitted_tu(
        cpp, driver, opt=opt, label=f"calendar-return-type-{name}",
    ) == "ok\n"


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_native_type_names_preserve_distinct_authored_bindings(opt):
    """Native type aliases and two occupied escape spellings keep their values."""
    from tests._compile import run_emitted_tu

    names = ("int32_t", "int64_t", "uint32_t", "uint64_t", "size_t", "time_t", "tm")
    declarations, requests = [], []
    for index, name in enumerate(names):
        factor = index + 1
        declarations.append(
            f"var int {name} = 1\n"
            f"var int pf_safe_{name} = 2\n"
            f"var int pf_safe_{name}_2 = 3\n"
            f"{name} := int(close * {factor}.0)\n"
            f"pf_safe_{name} := int(open * {factor}.0)\n"
            f"pf_safe_{name}_2 := int(high * {factor}.0)\n"
            f"if close > open\n    {name} := int(na)\n"
        )
        requests.append(
            f'float observed{index} = request.security(syminfo.tickerid, "1", '
            f'{name}[1] + timestamp("Asia/Taipei", year, month, dayofmonth, 8, 0, 0))\n'
            f'float escaped{index} = request.security(syminfo.tickerid, "1", '
            f"pf_safe_{name}[1] + pf_safe_{name}_2[1])\n"
        )
    cpp = transpile(HEADER + "".join(declarations + requests))
    _assert_calendar_locals(cpp)
    calendar = (
        "[&]() -> int64_t { "
        'std::string _tz = pineforge::normalize_timezone_for_posix((std::string("Asia/Taipei"))); '
        "int _yr = (pine_year(bar.timestamp, syminfo_.timezone)); "
        "int _mo = (pine_month(bar.timestamp, syminfo_.timezone)); "
        "int _dy = (pine_dayofmonth(bar.timestamp, syminfo_.timezone)); "
        "int _hr = (8); int _min = (0); int _sc = (0); "
        + CALENDAR_BODY
    )
    for index, name in enumerate(names):
        # Both authored escapes are occupied before allocation. All stores,
        # history reads and requested copies must use the deterministic third.
        safe = f"pf_safe_{name}_3"
        state = f"_sec{2 * index}_{safe}"
        for member in (safe, f"pf_safe_{name}", f"pf_safe_{name}_2", state):
            # Every one of them holds int(<float series>), which is a 64-bit int.
            assert f"Series<int64_t> {member};" in cpp
        expected = integer_to_double(integer_arithmetic(f"{state}[1]", "+", calendar))
        assert cpp.count(f"_req_sec_{2 * index} = {expected};") == 1
        other = 2 * index + 1
        expected_escapes = integer_to_double(integer_arithmetic(
            f"_sec{other}_pf_safe_{name}[1]", "+",
            f"_sec{other}_pf_safe_{name}_2[1]",
        ))
        assert cpp.count(f"_req_sec_{other} = {expected_escapes};") == 1

    driver = r"""
#include <cmath>
#include <cstdio>

int main() {
    const double closes[] = {7.0, 101.0, -7.0, 0.0, 11.0, 19.0};
    const double opens[] = {10.0, 100.0, 20.0, 5.0, 30.0, 50.0};
    constexpr unsigned size = sizeof(closes) / sizeof(closes[0]);
    pineforge::Bar bars[size];
    for (unsigned index = 0; index < size; ++index) {
        bars[index] = pineforge::Bar{
            opens[index], opens[index] + 3.0, closes[index] - 1.0, closes[index], 10.0,
            1704153480000LL + index * 60000LL,
        };
    }
    GeneratedStrategy strategy;
    // Reuse the host over different run lengths to check renamed state resets.
    for (unsigned count = 1; count <= size; ++count) {
        strategy.run(bars, count, "1", "1");
        if (!strategy.last_error().empty()) return 1;
        const double observed[] = {OBSERVED_VALUES};
        const double escaped[] = {ESCAPED_VALUES};
        const int input = static_cast<int>(count) - 2;
        for (unsigned index = 0; index < sizeof(observed) / sizeof(observed[0]); ++index) {
            if (input < 0 || closes[input] > opens[input]) {
                if (!std::isnan(observed[index])) return 2;
            } else {
                const auto day = bars[count - 1].timestamp / 86400000LL * 86400000LL;
                const double expected = day + closes[input] * (index + 1);
                if (!std::isfinite(observed[index]) || observed[index] != expected) return 3;
            }
            if (input < 0) {
                if (!std::isnan(escaped[index])) return 4;
            } else {
                const double expected = (opens[input] + bars[input].high) * (index + 1);
                if (!std::isfinite(escaped[index]) || escaped[index] != expected) return 5;
            }
        }
    }
    std::puts("ok");
}
"""
    driver = driver.replace(
        "OBSERVED_VALUES", ", ".join(f"strategy.observed{i}" for i in range(len(names))),
    ).replace(
        "ESCAPED_VALUES", ", ".join(f"strategy.escaped{i}" for i in range(len(names))),
    )
    assert run_emitted_tu(
        cpp, driver, opt=opt, label="native-type-name-collisions",
    ) == "ok\n"
