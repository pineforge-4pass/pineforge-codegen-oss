"""Compile-only smoke tests for transpiled C++ output.

These tests run ``g++ -fsyntax-only`` on the C++ that
``pineforge_codegen.transpile()`` emits, against the public
``pineforge-engine`` headers. They don't link, don't run a backtest, and
don't compare trade output to TradingView; the contract here is narrower
and more useful for the transpiler-only repo:

    "Every code path the codegen can take produces structurally valid
     C++ against the runtime ABI we ship for."

Each test is one Pine fixture sized so the whole file runs in <2s on a
modern laptop. The fixtures collectively exercise every dispatch lane
that the codegen has:

* TA classes — moving averages, oscillators, tuple-returning, volume
  variables, pivot levels.
* math.* (covered through transpile path: cmath dispatch + math::Sum).
* str.* (format / format_time / split / match / tostring with mintick).
* Inputs (every input.* + plain input()).
* Strategy commands (entry / exit / close / cancel / order with limits
  and stops).
* Trade accessors (closed + open).
* Strategy risk + override + magnifier toggles via run_backtest_full.
* request.security / request.security_lower_tf with same-symbol shape.
* Arrays / maps / matrices.
* User-defined types + methods.
* Enums + input.enum.
* na / nz / fixnan.
* ``// @pf-trace`` pragmas.

If the engine include dir or Eigen tree is missing, every test in this
file is skipped via ``_compile.skip_if_no_compile_env()``. See
``tests/_compile.py`` for the activation contract.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp, have_compile_env, skip_if_no_compile_env


pytestmark = pytest.mark.skipif(
    not have_compile_env(),
    reason="set PINEFORGE_ENGINE_INCLUDE (and ensure Eigen3 + a C++ compiler are reachable)",
)


def _pine(body: str) -> str:
    return f'//@version=6\nstrategy("T")\n{body}\n'


def _check(label: str, body: str) -> None:
    cpp = transpile(_pine(body))
    compile_cpp(cpp, label=label)


# ---------------------------------------------------------------------------
# Hello-world: the smallest possible compileable strategy.
# ---------------------------------------------------------------------------

def test_minimal_strategy_compiles():
    skip_if_no_compile_env()
    cpp = transpile('//@version=6\nstrategy("T")\n')
    compile_cpp(cpp, label="minimal_strategy")


# ---------------------------------------------------------------------------
# TA — covers single-value, tuple-returning, implicit-OHLC, and ta.* vars.
# ---------------------------------------------------------------------------

def test_ta_moving_averages_compile():
    _check("ta_moving_averages", """
length = input.int(14)
a = ta.sma(close, length)
b = ta.ema(close, length)
c = ta.rma(close, length)
d = ta.wma(close, length)
e = ta.hma(close, length)
f = ta.alma(close, length, 0.85, 6.0)
g = ta.vwma(close, length)
h = ta.swma(close)
""")


def test_ta_oscillators_compile():
    _check("ta_oscillators", """
r = ta.rsi(close, 14)
s = ta.stoch(close, high, low, 14)
c = ta.cci(close, 20)
m = ta.mfi(close, 14)
mo = ta.mom(close, 10)
roc = ta.roc(close, 10)
cmo = ta.cmo(close, 9)
tsi = ta.tsi(close, 13, 25)
wpr = ta.wpr(14)
cog = ta.cog(close, 10)
""")


def test_ta_tuple_returning_compile():
    _check("ta_tuple_returning", """
[m, s, h] = ta.macd(close, 12, 26, 9)
[bbm, bbu, bbl] = ta.bb(close, 20, 2.0)
[kcm, kcu, kcl] = ta.kc(close, 20, 1.5)
[stval, stdir] = ta.supertrend(3.0, 10)
[dip, dim, adx] = ta.dmi(14, 14)
""")


def test_ta_implicit_ohlc_compile():
    _check("ta_implicit_ohlc", """
a = ta.atr(14)
b = ta.tr(true)
c = ta.tr
d = ta.sar(0.02, 0.02, 0.2)
e = ta.pivothigh(2, 2)
f = ta.pivotlow(2, 2)
""")


def test_ta_volume_property_variables_compile():
    _check("ta_volume_vars", """
o = ta.obv
a = ta.accdist
n = ta.nvi
p = ta.pvi
v = ta.pvt
w = ta.wad
wv = ta.wvad
i = ta.iii
""")


def test_ta_pivot_point_levels_compile():
    _check("ta_pivot_point_levels", """
levels = ta.pivot_point_levels("Traditional", true)
p = levels[0]
""")


# ---------------------------------------------------------------------------
# math + str — exercise both runtime helpers and inline cmath dispatch.
# ---------------------------------------------------------------------------

def test_math_helpers_compile():
    _check("math_helpers", """
a = math.abs(close - open)
b = math.sqrt(close)
c = math.pow(close, 2.0)
d = math.log(close)
e = math.sin(close)
f = math.atan2(high, low)
g = math.sum(close, 14)
h = math.random(0.0, 1.0, 42)
i = math.sign(close - open)
j = math.min(high, low, close)
k = math.max(open, close)
""")


def test_str_helpers_compile():
    _check("str_helpers", """
a = str.tostring(close)
b = str.tostring(close, "#.##")
sc = str.tostring(close)
c = str.format("{0} bar at {1} (close={2})", sc, high, str.tostring(close))
d = str.format_time(time, "yyyy-MM-dd", "UTC")
e = str.split("a,b,c", ",")
f = str.match(syminfo.tickerid, "^[A-Z]+")
g = str.length("hello")
h = str.lower("HI")
i = str.upper("hi")
j = str.contains(syminfo.ticker, "USD")
k = str.replace("hello", "l", "L", 0)
n = str.replace_all("hello", "l", "L")
o = str.startswith("hello", "he")
p = str.endswith("hello", "lo")
q = str.pos("hello", "ll")
r = str.repeat("ab", 3)
s = str.trim("  hi  ")
t = str.tonumber("3.14")
u = str.substring("hello", 1, 4)
""")


# ---------------------------------------------------------------------------
# Inputs — every input.* plus plain input() overloads.
# ---------------------------------------------------------------------------

def test_inputs_compile():
    _check("inputs", """
i  = input.int(14, "Length", minval=1, maxval=100)
f  = input.float(1.5, "Mult", minval=0.1, maxval=10.0)
b  = input.bool(true, "Long")
s  = input.string("foo", "Mode", options=["foo", "bar"])
src = input.source(close, "Source")
col = input.color(color.red, "Up")
tf  = input.timeframe("D", "TF")
sess = input.session("0930-1600", "Session")
sym  = input.symbol("AAPL", "Sym")
pr   = input.price(100.0, "Price")
tt   = input.time(timestamp("2024-01-01"), "Start")
ta_  = input.text_area("note", "Note")
""")


# ---------------------------------------------------------------------------
# strategy.* — entry / exit / close / cancel + accessors.
# ---------------------------------------------------------------------------

def test_strategy_orders_compile():
    _check("strategy_orders", """
if ta.crossover(ta.sma(close, 10), ta.sma(close, 30))
    strategy.entry("L", strategy.long, qty=1, comment="enter")
if ta.crossunder(ta.sma(close, 10), ta.sma(close, 30))
    strategy.close("L", comment="exit")

strategy.exit("X", from_entry="L", profit=1.0, loss=0.5)
strategy.exit("T", from_entry="L", trail_points=10, trail_offset=5)
strategy.cancel("L")
strategy.cancel_all()
strategy.order("R", strategy.long, qty=1, limit=close*0.99)
""")


def test_strategy_accessors_compile():
    _check("strategy_accessors", """
n = strategy.position_size
e = strategy.equity
np_ = strategy.netprofit
nt = strategy.closedtrades
ot = strategy.opentrades

p1 = strategy.closedtrades.profit(0)
p2 = strategy.closedtrades.entry_price(0)
p3 = strategy.closedtrades.exit_price(0)
p4 = strategy.closedtrades.size(0)
p5 = strategy.closedtrades.max_drawdown(0)

q1 = strategy.opentrades.profit(0)
q2 = strategy.opentrades.entry_price(0)
q3 = strategy.opentrades.size(0)
q4 = strategy.opentrades.max_runup(0)
""")


def test_strategy_risk_compile():
    _check("strategy_risk", """
strategy.risk.max_drawdown(20.0, strategy.percent_of_equity)
strategy.risk.max_intraday_loss(10.0, strategy.percent_of_equity)
strategy.risk.max_position_size(100.0)
strategy.risk.max_cons_loss_days(3)
strategy.risk.max_intraday_filled_orders(50)
strategy.risk.allow_entry_in(strategy.direction.long)
""")


# ---------------------------------------------------------------------------
# request.security — same-symbol higher-TF + lower-TF.
# ---------------------------------------------------------------------------

def test_request_security_same_symbol_compile():
    _check("request_security", """
htf = request.security(syminfo.tickerid, "D", close)
htf_g = request.security(syminfo.tickerid, "D", close,
                          gaps=barmerge.gaps_off,
                          lookahead=barmerge.lookahead_off)
""")


def test_request_security_lower_tf_compile():
    _check("request_security_lower_tf", """
ltf = request.security_lower_tf(syminfo.tickerid, "1", close)
""")


# ---------------------------------------------------------------------------
# Arrays / maps / matrices.
# ---------------------------------------------------------------------------

def test_arrays_compile():
    _check("arrays", """
a = array.new<float>(5, 0.0)
array.push(a, close)
array.set(a, 0, high)
v = array.get(a, 0)
sz = array.size(a)
mn = array.min(a)
mx = array.max(a)
av = array.avg(a)
""")


def test_descending_for_by_array_remove_compiles():
    _check("descending_for_by_array_remove", """
var levels = array.new<float>()
if bar_index == 0
    array.push(levels, close)
    array.push(levels, high)
if array.size(levels) > 0
    for i = array.size(levels) - 1 to 0 by 1
        v = array.get(levels, i)
        if v <= close
            array.remove(levels, i)
""")


def test_maps_compile():
    _check("maps", """
m = map.new<string, float>()
map.put(m, "x", close)
v = map.get(m, "x")
ok = map.contains(m, "x")
sz = map.size(m)
""")


def test_matrices_compile():
    _check("matrices", """
m = matrix.new<float>(3, 3, 0.0)
n = matrix.new<float>(3, 3, 1.0)
matrix.set(m, 0, 0, close)
v = matrix.get(m, 0, 0)
r = matrix.rows(m)
c = matrix.columns(m)
det = matrix.det(m)
sm = matrix.sum(m)
mn = matrix.min(m)
mx = matrix.max(m)
av = matrix.avg(m)
tr = matrix.trace(m)
rk = matrix.rank(m)
ec = matrix.elements_count(m)
sq = matrix.is_square(m)
sy = matrix.is_symmetric(m)
inv = matrix.inv(m)
pin = matrix.pinv(m)
trp = matrix.transpose(m)
cp  = matrix.copy(m)
sub = matrix.submatrix(m, 0, 1, 0, 1)
con = matrix.concat(m, n, false)
df  = matrix.diff(m, n)
ml  = matrix.mult(m, n)
pw  = matrix.pow(m, 2)
ev  = matrix.eigenvectors(m)
kr  = matrix.kron(m, n)
""")


# ---------------------------------------------------------------------------
# User-defined types + methods + enums.
# ---------------------------------------------------------------------------

def test_udts_and_methods_compile():
    _check("udts_methods", """
type Position
    float entry
    float stop
    int   bar

method risk(Position self, float mult = 1.0) =>
    math.abs(self.entry - self.stop) * mult

p = Position.new(entry=close, stop=low, bar=bar_index)
r = p.risk(2.0)
""")


def test_enums_and_input_enum_compile():
    _check("enums_input_enum", """
enum Mode
    Trend = "trend"
    Mean  = "mean"

m = input.enum(Mode.Trend, "Mode")
go_long = m == Mode.Trend
""")


# ---------------------------------------------------------------------------
# na / nz / fixnan / barstate / time.
# ---------------------------------------------------------------------------

def test_na_nz_fixnan_compile():
    _check("na_nz_fixnan", """
a = na
b = nz(close)
c = nz(close, open)
d = fixnan(close)
e = na(close)
""")


def test_time_helpers_compile():
    # Both the variable form (``year`` / ``month`` / ...) and the function
    # form (``year(time)``, ``hour(time, "UTC")``, ...) are exercised. The
    # variable form maps to the engine's ``_bar_year()`` accessors; the
    # function form inlines a ``gmtime_r``-based extraction lambda matching
    # ``BacktestEngine::_decompose_bar_time()``.
    _check("time_helpers", """
t  = time("D", "0930-1600", "America/New_York")
tc = time_close("D")
ts = timestamp(2024, 1, 1, 9, 30, 0)
yv = year
mov = month
dmv = dayofmonth
dwv = dayofweek
hrv = hour
mnv = minute
scv = second
wkv = weekofyear
yf  = year(time)
mof = month(time)
dmf = dayofmonth(time)
dwf = dayofweek(time)
hrf = hour(time, "UTC")
mnf = minute(time)
scf = second(time)
wkf = weekofyear(time)
""")


# ---------------------------------------------------------------------------
# // @pf-trace pragma round-trip — exercises the trace emission tail.
# ---------------------------------------------------------------------------

def test_pf_trace_pragma_compiles():
    skip_if_no_compile_env()
    src = """//@version=6
//@pf-trace ema_fast=ta.ema(close, 10)
//@pf-trace ema_slow=ta.ema(close, 30)
strategy("T")
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
if ta.crossover(fast, slow)
    strategy.entry("L", strategy.long)
"""
    cpp = transpile(src)
    compile_cpp(cpp, label="pf_trace")


# ---------------------------------------------------------------------------
# Composite — a representative, realistic strategy with most features.
# ---------------------------------------------------------------------------

def test_composite_strategy_compiles():
    skip_if_no_compile_env()
    src = """//@version=6
strategy("Composite", overlay=true, initial_capital=10000,
         commission_type=strategy.commission.percent, commission_value=0.05)

length = input.int(14, "Length", minval=1)
mult   = input.float(2.0, "Mult", minval=0.1)
useTrail = input.bool(true, "Use trail")
sess   = input.session("0930-1600", "Session")
htf    = input.timeframe("D", "HTF")

basis = ta.sma(close, length)
[mid, upper, lower] = ta.bb(close, length, mult)
atr = ta.atr(14)

htfClose = request.security(syminfo.tickerid, htf, close)

inSess = not na(time("D", sess))

longCond  = inSess and ta.crossover(close, upper) and close > htfClose
shortCond = inSess and ta.crossunder(close, lower) and close < htfClose

if longCond
    strategy.entry("L", strategy.long, qty=1)
    strategy.exit("LX", from_entry="L", stop=close - atr*mult,
                  trail_points=useTrail ? atr*mult : na,
                  trail_offset=useTrail ? atr : na)
if shortCond
    strategy.entry("S", strategy.short, qty=1)
    strategy.exit("SX", from_entry="S", stop=close + atr*mult)

if strategy.position_size > 0 and ta.crossunder(close, basis)
    strategy.close("L")
"""
    cpp = transpile(src)
    compile_cpp(cpp, label="composite_strategy")


# ---------------------------------------------------------------------------
# Regression tests for previously-broken codegen paths.
#
# Each of the three tests below pins a specific Pine v6 idiom that used
# to transpile happily but emit C++ that did NOT compile against the
# pineforge-engine headers. The transpiler accepted them, the support
# checker did not reject them, but the resulting C++ was malformed.
# These regressions never showed up in the 162-strategy corpus, which is
# why they survived for so long.
#
# Asserting them as plain compile checks (instead of xfail markers) keeps
# the codegen honest: any future change that re-breaks one of these
# paths fails the suite at PR time.
# ---------------------------------------------------------------------------

def test_regression_year_function_call_form_compiles():
    """Used to emit raw ``year(timestamp)``; now inlines gmtime_r."""
    skip_if_no_compile_env()
    src = """//@version=6
strategy("T")
y  = year(time)
mo = month(time)
dm = dayofmonth(time)
dw = dayofweek(time)
hr = hour(time, "UTC")
mn = minute(time)
sc = second(time)
wk = weekofyear(time)
"""
    cpp = transpile(src)
    # Spot-check the lowering shape so anyone deleting the special case
    # in visit_call.py learns about it from the assertion, not from a
    # mysterious clang error.
    assert "gmtime_r" in cpp, (
        "year(time) etc. should lower to a gmtime_r-based extraction lambda; "
        "if you intentionally moved the implementation to a runtime helper, "
        "update this assertion to match the new emission shape."
    )
    compile_cpp(cpp, label="regression_year_function_form")


def test_regression_matrix_returning_methods_get_pine_matrix_type():
    """LHS of inv / pinv / transpose / copy / submatrix / concat / diff /
    mult / pow / eigenvectors / kron is now declared as ``PineMatrix``,
    not the analyzer-default ``double``."""
    skip_if_no_compile_env()
    src = """//@version=6
strategy("T")
m = matrix.new<float>(3, 3, 0.0)
n = matrix.new<float>(3, 3, 1.0)
inv  = matrix.inv(m)
pin  = matrix.pinv(m)
trp  = matrix.transpose(m)
cp   = matrix.copy(m)
sub  = matrix.submatrix(m, 0, 1, 0, 1)
con  = matrix.concat(m, n, false)
df   = matrix.diff(m, n)
ml   = matrix.mult(m, n)
pw   = matrix.pow(m, 2)
ev   = matrix.eigenvectors(m)
kr   = matrix.kron(m, n)
"""
    cpp = transpile(src)
    # Each LHS variable must be declared as PineMatrix, never as double.
    for name in ("inv", "pin", "trp", "cp", "sub", "con", "df", "ml", "pw", "ev", "kr"):
        assert f"PineMatrix {name};" in cpp, (
            f"matrix-returning method's LHS '{name}' lost its PineMatrix type; "
            "regression in MATRIX_RETURNING_METHODS dispatch?"
        )
    compile_cpp(cpp, label="regression_matrix_returning_methods")


def test_regression_str_format_with_string_typed_args_compiles():
    """``str.format(fmt, sval)`` with any string-typed (not just literal)
    arg used to double-wrap the value in ``std::to_string``. Now the
    wrap is gated on the analyzer's inferred PineType, so STRING-typed
    args pass through unchanged and bool args render TV-style as
    ``"true"`` / ``"false"`` rather than ``"1"`` / ``"0"``."""
    skip_if_no_compile_env()
    src = """//@version=6
strategy("T")
sval = str.tostring(close)
a = str.format("{0} bar", sval)
b = str.format("{0} {1} {2}", close, true, "literal")
"""
    cpp = transpile(src)
    # The string-typed argument must NOT be wrapped in std::to_string;
    # the boolean must render TV-style as "true"/"false".
    assert "std::to_string(sval)" not in cpp, (
        "str.format double-wrapped a STRING-typed argument; "
        "regression in the _infer_type-based gate?"
    )
    assert 'std::string("true")' in cpp, (
        "bool args to str.format should render TV-style as \"true\"/\"false\"."
    )
    compile_cpp(cpp, label="regression_str_format_string_args")


# ---------------------------------------------------------------------------
# Typed (UDT) matrix support — Phase 2 generic_matrix.hpp dispatch.
# Closes the C++ review test gap: actually compiles emitted code for
# matrix<int>, matrix<bool>, matrix<UDT>.
# ---------------------------------------------------------------------------

def test_matrix_int_compiles():
    skip_if_no_compile_env()
    _check("matrix_int", """
var m = matrix.new<int>(3, 3, 0)
m.set(0, 0, 7)
v = m.get(0, 0)
var n = m.copy()
""")


def test_matrix_bool_compiles():
    skip_if_no_compile_env()
    _check("matrix_bool", """
var m = matrix.new<bool>(2, 2, false)
m.set(0, 0, true)
v = m.get(0, 0)
""")


def test_matrix_udt_compiles():
    skip_if_no_compile_env()
    _check("matrix_udt", """
type Pt
    float x
    float y
var m = matrix.new<Pt>(2, 2)
""")


def test_matrix_int_concat_compiles():
    skip_if_no_compile_env()
    _check("matrix_int_concat", """
var m = matrix.new<int>(2, 2, 0)
var other = matrix.new<int>(2, 2, 1)
m.concat(other, false)
""")
