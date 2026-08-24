"""Emission tests for the 2026-06 audit fixes (items A4/A5, B8-B17, C18)."""
import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError

PRELUDE = '//@version=6\nstrategy("T")\n'


def _gen(body: str) -> str:
    return transpile(PRELUDE + body)


# ---------------------------------------------------------------------------
# A4 — display.pine_screener gets a distinct constant
# ---------------------------------------------------------------------------

def test_display_pine_screener_distinct_value():
    cpp = _gen("d = display.pine_screener\nplot(close)\n")
    assert "= 6;" in cpp  # distinct from display.all (0)


# ---------------------------------------------------------------------------
# A5 — bare ta.<name> property reads without a call site reject loudly
# ---------------------------------------------------------------------------

def test_bare_ta_property_read_rejected():
    with pytest.raises(CompileError, match=r"ta\.rsi"):
        _gen("x = ta.rsi\n")


def test_ta_property_indicators_still_work():
    cpp = _gen("x = ta.obv\nplot(x)\n")
    assert "_ta_obv_" in cpp


# ---------------------------------------------------------------------------
# B8 — /= and %= compound assignment semantics
# ---------------------------------------------------------------------------

def test_compound_divide_is_always_float():
    cpp = _gen("var a = 10.0\na /= 3\nplot(a)\n")
    assert "a = ((double)(a) / (double)(3));" in cpp
    assert "a /= 3" not in cpp


def test_compound_modulo_is_fmod():
    cpp = _gen("var a = 10.0\na %= 3\nplot(a)\n")
    assert "a = std::fmod((double)(a), (double)(3));" in cpp
    assert "a %= 3" not in cpp


def test_compound_add_unchanged():
    cpp = _gen("var a = 10.0\na += 3\nplot(a)\n")
    assert "a += 3;" in cpp


# ---------------------------------------------------------------------------
# B9 — str.replace 4-arg occurrence form
# ---------------------------------------------------------------------------

def test_str_replace_occurrence_form():
    cpp = _gen('s = str.replace("aXbXc", "X", "-", 1)\nplot(close)\n')
    assert "_occ" in cpp
    assert "while((p=s.find(t,p))!=std::string::npos)" in cpp


def test_str_replace_three_arg_unchanged():
    cpp = _gen('s = str.replace("aXbXc", "X", "-")\nplot(close)\n')
    assert "_occ" not in cpp
    assert "s.find(" in cpp


# ---------------------------------------------------------------------------
# B10 — timestamp() arity handling
# ---------------------------------------------------------------------------

def test_timestamp_numeric_form_works():
    cpp = _gen("t = timestamp(2020, 1, 2)\nplot(close)\n")
    assert "timegm" in cpp


def test_timestamp_numeric_form_defaults_to_syminfo_timezone():
    # Pine v6: "If the timezone argument is not specified, the function uses
    # the exchange time zone (syminfo.timezone)". The numeric overload must
    # resolve its calendar fields through syminfo_.timezone (UTC fast path
    # kept via timegm, DST-aware mktime otherwise) — not a bare timegm().
    cpp = _gen("t = timestamp(2020, 1, 2, 8, 30, 0)\nplot(close)\n")
    lam = cpp[cpp.index("normalize_timezone_for_posix((syminfo_.timezone))"):]
    lam = lam[: lam.index("}()")]
    assert "_hr = (8)" in lam and "_min = (30)" in lam
    assert "timegm" in lam and "mktime" in lam
    assert "t.tm_isdst = -1" in lam
    assert 'if (_tz.empty() || _tz == "UTC" || _tz == "Etc/UTC")' in lam


def test_timestamp_numeric_kwargs_default_to_syminfo_timezone():
    cpp = _gen(
        "t = timestamp(year=2021, month=3, day=14, hour=9, minute=30)\n"
        "plot(close)\n"
    )
    assert "normalize_timezone_for_posix((syminfo_.timezone))" in cpp
    assert "_hr = (9)" in cpp and "_min = (30)" in cpp and "_sc = (0)" in cpp


def test_timestamp_numeric_series_args_default_to_syminfo_timezone():
    # The opening-range idiom: fields taken from the current bar.
    cpp = _gen(
        "t = timestamp(year, month, dayofmonth, 8, 0, 0)\nplot(close)\n"
    )
    assert "normalize_timezone_for_posix((syminfo_.timezone))" in cpp


def test_timestamp_tz_form_works():
    cpp = _gen('t = timestamp("GMT+2", 2020, 1, 2)\nplot(close)\n')
    assert "normalize_timezone_for_posix" in cpp
    assert "mktime" in cpp


def test_timestamp_tz_form_does_not_fall_back_to_syminfo():
    # An explicit timezone argument wins; syminfo must not appear in the
    # tz-first overload's lambda.
    cpp = _gen('t = timestamp("Asia/Tokyo", 2020, 1, 2, 9, 0)\nplot(close)\n')
    assert 'normalize_timezone_for_posix(("Asia/Tokyo"))' in cpp or \
        'normalize_timezone_for_posix((std::string("Asia/Tokyo")))' in cpp
    assert "syminfo_.timezone" not in cpp[cpp.index("normalize_timezone_for_posix"):cpp.index("}()", cpp.index("normalize_timezone_for_posix"))]


def test_timestamp_datestring_without_tz_stays_gmt0():
    # Pine: a dateString with no time zone is GMT+0 (NOT syminfo.timezone);
    # it is folded at transpile time and must not pick up the syminfo default.
    cpp = _gen('t = timestamp("2020-01-02 08:30")\nplot(close)\n')
    assert "1577953800000LL" in cpp
    assert "syminfo_.timezone" not in cpp


@pytest.mark.parametrize("call", [
    "timestamp()",
    "timestamp(2020)",
    "timestamp(2020, 1)",
])
def test_timestamp_short_numeric_arity_rejected(call):
    with pytest.raises(CompileError, match="timestamp"):
        _gen(f"t = {call}\nplot(close)\n")


def test_timestamp_datestring_literal_parsed_at_transpile_time():
    # 2020-02-20T15:30:00 UTC = 1582212600000 ms (was silently 1970 epoch).
    cpp = _gen('t = timestamp("2020-02-20T15:30:00")\nplot(close)\n')
    assert "1582212600000LL" in cpp


def test_timestamp_datestring_dd_mmm_yyyy_parsed():
    # Pine reference example form, no tz = GMT+0.
    cpp = _gen('t = timestamp("20 Jul 2021 00:00 +0300")\nplot(close)\n')
    assert "1626728400000LL" in cpp


def test_timestamp_datestring_ymd_utc_word_parsed():
    # "YYYY-MM-DD HH:MM UTC" — space-separated with a trailing tz WORD (not a
    # numeric offset). 2024-01-01 00:00 UTC = 1704067200000 ms.
    cpp = _gen('t = timestamp("2024-01-01 00:00 UTC")\nplot(close)\n')
    assert "1704067200000LL" in cpp


def test_timestamp_datestring_gmt_offset_word_parsed():
    # "GMT+2" trailing word = +2h offset. 1 Jan 2020 09:30 GMT+2 = 07:30 UTC
    # = 1577863800000 ms.
    cpp = _gen('t = timestamp("1 Jan 2020 09:30 GMT+2")\nplot(close)\n')
    assert "1577863800000LL" in cpp


def test_timestamp_unparseable_datestring_rejected():
    with pytest.raises(CompileError, match="could not parse"):
        _gen('t = timestamp("not a date")\nplot(close)\n')


def test_timestamp_non_literal_datestring_rejected():
    # Loud reject (either the literal-required or the arity message,
    # depending on how the analyzer types the argument).
    with pytest.raises(CompileError, match="timestamp"):
        _gen('s = syminfo.ticker\nt = timestamp(s)\nplot(close)\n')


def test_timestamp_tz_missing_day_rejected():
    with pytest.raises(CompileError, match="timestamp"):
        _gen('t = timestamp("GMT+2", 2020, 1)\nplot(close)\n')


# ---------------------------------------------------------------------------
# B11 — string(x) / int(x) casts
# ---------------------------------------------------------------------------

def test_string_cast_numeric():
    cpp = _gen("s = string(close)\nplot(close)\n")
    assert "std::to_string(current_bar_.close)" in cpp


def test_string_cast_string_passthrough():
    cpp = _gen('a = syminfo.ticker\ns = string(a)\nplot(close)\n')
    assert "std::to_string(a)" not in cpp


def test_string_cast_bool():
    cpp = _gen("b = close > open\ns = string(b)\nplot(close)\n")
    assert 'std::string("true")' in cpp
    assert 'std::string("false")' in cpp


def test_int_cast_propagates_na():
    cpp = _gen("i = int(close)\nplot(close)\n")
    assert "is_na(_pf_v) ? na<int>() : (int)_pf_v" in cpp


# ---------------------------------------------------------------------------
# B12 — strategy.openprofit_percent uses realized equity
# ---------------------------------------------------------------------------

def test_openprofit_percent_uses_current_equity():
    cpp = _gen("x = strategy.openprofit_percent\nplot(x)\n")
    assert "open_profit(current_bar_.close) / current_equity()" in cpp
    assert "open_profit(current_bar_.close) / initial_capital_" not in cpp


# ---------------------------------------------------------------------------
# B13 — syminfo.country ISO codes
# ---------------------------------------------------------------------------

def test_country_lookup_is_iso():
    cpp = _gen("c = syminfo.country\nplot(close)\n")
    assert '{"LSE", "GB"}' in cpp
    assert '{"AQUIS", "GB"}' in cpp
    assert '"UK"' not in cpp
    # Non-ISO pseudo-codes removed: pan-EU / global crypto venues return na.
    assert '"GLOBAL"' not in cpp
    assert '"EURONEXT"' not in cpp
    assert '"BINANCE"' not in cpp


# ---------------------------------------------------------------------------
# B14 — math.round_to_mintick uses the engine method
# ---------------------------------------------------------------------------

def test_round_to_mintick_uses_engine_method():
    cpp = _gen("y = math.round_to_mintick(close)\nplot(y)\n")
    assert "round_to_mintick(current_bar_.close)" in cpp
    assert "std::round(current_bar_.close / syminfo_mintick_)" not in cpp


# ---------------------------------------------------------------------------
# B15 — array.stdev / array.variance biased argument
# ---------------------------------------------------------------------------

def test_array_stdev_biased_arg():
    cpp = _gen(
        "arr = array.new<float>(0)\narray.push(arr, close)\n"
        "v = array.stdev(arr, false)\nplot(v)\n"
    )
    assignment = next(line for line in cpp.splitlines() if line.startswith("        v ="))
    token = assignment.split("auto ", 1)[1].split("=", 1)[0]
    assert token.startswith("__pf_array_arg_")
    assert f"auto {token}=(false);" in assignment
    assert f"_d=({token})?" in assignment
    assert assignment.index("(false)") < assignment.index("arr.empty()")


def test_array_stdev_no_arg_unchanged():
    cpp = _gen(
        "arr = array.new<float>(0)\narray.push(arr, close)\n"
        "v = array.stdev(arr)\nplot(v)\n"
    )
    assert "_d=" not in cpp
    assert "std::sqrt(s/arr.size())" in cpp


def test_array_variance_biased_arg():
    cpp = _gen(
        "arr = array.new<float>(0)\narray.push(arr, close)\n"
        "v = array.variance(arr, false)\nplot(v)\n"
    )
    assert "_d=" in cpp


# ---------------------------------------------------------------------------
# B16/B17 — array.join / copy / slice honor the element type
# ---------------------------------------------------------------------------

def test_array_join_string_elements():
    cpp = _gen(
        'sa = array.new<string>(0)\narray.push(sa, "a")\n'
        's = array.join(sa, ",")\nplot(close)\n'
    )
    assert "r+=sa[i];" in cpp
    assert "std::to_string(sa[i])" not in cpp


def test_array_join_numeric_elements():
    cpp = _gen(
        "fa = array.new<float>(0)\narray.push(fa, close)\n"
        's = array.join(fa, ",")\nplot(close)\n'
    )
    assert "std::to_string(fa[i])" in cpp


def test_array_copy_int_elements():
    cpp = _gen(
        "ia = array.new<int>(3, 0)\nib = array.copy(ia)\nplot(close)\n"
    )
    assert "std::vector<int>(ia)" in cpp


def test_array_slice_string_elements():
    cpp = _gen(
        'sa = array.new<string>(0)\narray.push(sa, "a")\narray.push(sa, "b")\n'
        "sb = array.slice(sa, 0, 1)\nplot(close)\n"
    )
    # The slice keeps the receiver's ELEMENT type (std::string, not double).
    # Iterators come from the bounds-checked lambda's ``__pf_array`` binding.
    assert "std::vector<std::string>(__pf_array.begin()" in cpp
    assert "}((sa))" in cpp


# ---------------------------------------------------------------------------
# C18 — bare time variables use the exchange timezone path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("var", [
    "hour", "minute", "second", "dayofmonth", "dayofweek", "month", "year",
])
def test_bare_time_vars_use_exchange_timezone(var):
    # Bare time vars route through the engine helper pine_<var>() threading the
    # exchange TZ (syminfo_.timezone); the tm-field extraction and Pine offsets
    # live inside the helper (session_time.cpp, KI-35), not the generated code.
    cpp = _gen(f"x = {var}\nplot(close)\n")
    assert f"_bar_{var}()" not in cpp
    assert f"pine_{var}(current_bar_.timestamp, syminfo_.timezone)" in cpp


def test_bare_and_function_form_share_emission():
    cpp = _gen("a = hour\nb = hour(time)\nplot(close)\n")
    # Both the bare and function forms route through the same engine helper
    # pine_hour() (the bare form passes the timestamp directly, the function
    # form casts its time arg to int64_t).
    assert cpp.count("pine_hour(") == 2
    assert "_bar_hour()" not in cpp


# ---------------------------------------------------------------------------
# format.* members now type as STRING for bare reads
# ---------------------------------------------------------------------------

def test_format_member_bare_read_types_string():
    cpp = _gen("f = format.mintick\nplot(close)\n")
    # Global member declared std::string (was double — C++ type mismatch).
    assert 'std::string f' in cpp
    assert 'f = std::string("mintick");' in cpp
    assert "double f" not in cpp
