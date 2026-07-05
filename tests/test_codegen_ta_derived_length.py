"""Regression tests for TA constructor lengths derived from arithmetic over
inputs and from function-local / parameter expressions.

Bug history: when a ``ta.*`` length argument was not a literal or a direct
input alias — i.e. arithmetic over an input/const (``wilderLen = rsiLen*2-1``)
or a function-local / parameter-derived length (``wp = sf*2-1``; a helper
``f(src,_len) => ta.sma(src,_len)``) — the transpiler silently emitted a TA
constructor period of 1 with no overwriting runtime reset. The smoother then
degenerated to a no-op, producing a wrong indicator and wrong signals.

These tests pin the three faithful behaviors:
  1. class-scope arithmetic over an input folds the ctor-init to the literal
     AND emits an override-aware runtime reset;
  2. function-local / parameter-derived lengths (including a length threaded
     through a nested user-function call) resolve to the real input;
  3. a legitimate input that genuinely defaults to 1 stays period 1;
and the guardrail: a genuinely-unresolvable computed length raises instead of
silently emitting period 1.
"""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError


def _ctor_period(cpp: str, member: str) -> str:
    """Return the ctor-init period for a TA member, e.g. ``_ta_ema_9`` -> '27'."""
    m = re.search(rf"\b{re.escape(member)}\(([^)]*)\)", cpp)
    assert m, f"{member} not found in initializer list"
    return m.group(1).strip()


def _reset_line(cpp: str, member: str) -> str:
    """Return the runtime-reset assignment for a TA member (the line that
    overwrites the ctor placeholder under ``!_ta_initialized_``)."""
    for ln in cpp.splitlines():
        s = ln.strip()
        if s.startswith(f"{member} = ") and s.endswith(";"):
            return s
    return ""


# ---------------------------------------------------------------------------
# 1. Class-scope arithmetic over an input  (wilderLen = rsiLen * 2 - 1)
# ---------------------------------------------------------------------------

def test_class_scope_arithmetic_over_input_length():
    src = """//@version=6
strategy("derived-class-scope")
rsiLen = input.int(14, "RSI Length")
wilderLen = rsiLen * 2 - 1
src = ta.ema(close, wilderLen)
plot(src)
"""
    cpp = transpile(src)
    # Find the EMA member sized by wilderLen.
    members = re.findall(r"(_ta_ema_\d+)\(", cpp)
    assert members, "no EMA member emitted"
    # The wilderLen-sized EMA folds to 14*2-1 = 27 in the init list.
    sized = [m for m in members if _ctor_period(cpp, m) == "27"]
    assert sized, f"expected an EMA with ctor period 27, got {[(_m, _ctor_period(cpp, _m)) for _m in members]}"
    member = sized[0]
    # And the runtime reset re-derives it from the (possibly overridden) input.
    reset = _reset_line(cpp, member)
    assert 'get_input_int("RSI Length", 14)' in reset
    # Reset lowers through the expression visitor, which parenthesizes operands
    # (grouping preserved: ``(rsiLen * 2) - 1``, not a flattened infix string).
    assert '(get_input_int("RSI Length", 14) * 2) - 1' in reset
    assert "ta::EMA(1)" not in reset  # must NOT be the silent no-op


# ---------------------------------------------------------------------------
# 2a. Function-local derived length  (qqeCalc: wp = sf * 2 - 1)
# ---------------------------------------------------------------------------

def test_function_local_derived_length():
    src = """//@version=6
strategy("derived-func-local")
rsiSmooth = input.int(5, "Smooth EMA Length")
qqeCalc(int sf) =>
    wp = sf * 2 - 1
    ta.ema(ta.ema(close, wp), wp)
out = qqeCalc(rsiSmooth)
plot(out)
"""
    cpp = transpile(src)
    members = re.findall(r"(_ta_ema_\d+)\(", cpp)
    # sf = 5 -> wp = 9. The wp-sized EMAs must be period 9, not 1.
    sized = [m for m in members if _ctor_period(cpp, m) == "9"]
    assert len(sized) >= 2, (
        f"expected >=2 EMAs with ctor period 9, got "
        f"{[(_m, _ctor_period(cpp, _m)) for _m in members]}"
    )
    for m in sized:
        reset = _reset_line(cpp, m)
        assert 'get_input_int("Smooth EMA Length", 5)' in reset
        assert '(get_input_int("Smooth EMA Length", 5) * 2) - 1' in reset
    assert "ta::EMA(1)" not in cpp


# ---------------------------------------------------------------------------
# 2b. Length threaded through a NESTED user-function call
#     f_basisMa(src, _len) => ta.sma(src, _len)   called from f_bbwp(_bbwLen)
# ---------------------------------------------------------------------------

def test_nested_user_func_param_length():
    src = """//@version=6
strategy("derived-nested-param")
i_bbwLen = input.int(7, "BBW Basis Length")
f_basisMa(float _src, int _len) =>
    ta.sma(_src, _len)
f_bbwp(float _price, int _bbwLen) =>
    f_basisMa(_price, _bbwLen) + ta.stdev(_price, _bbwLen)
out = f_bbwp(close, i_bbwLen)
plot(out)
"""
    cpp = transpile(src)
    # The SMA inside f_basisMa must be sized by the real input (7), not 1.
    sma_members = re.findall(r"(_ta_sma_\d+)\(", cpp)
    assert sma_members
    sized = [m for m in sma_members if _ctor_period(cpp, m) == "7"]
    assert sized, (
        f"nested SMA length not resolved; got "
        f"{[(_m, _ctor_period(cpp, _m)) for _m in sma_members]}"
    )
    reset = _reset_line(cpp, sized[0])
    assert 'get_input_int("BBW Basis Length", 7)' in reset
    assert "ta::SMA(1)" not in cpp


# ---------------------------------------------------------------------------
# 2c. Length threaded through TWO levels of nested user functions whose
#     call site passes an INLINE input() call (no intermediate var name).
#     Mirrors the quantbyboji DMI/ADX pattern:
#       dirmov(len) => ta.rma(ta.tr, len)
#       adx(dilen, adxlen) => dirmov(dilen); ta.rma(x, adxlen)
#       sig = adx(input(15), input(15))
#     The ctor args must resolve to input-backed reset expressions
#     (get_input_int(..., 15)), never to the bare parameter name ``len``
#     or the unresolved call spelling ``input(15)``.
# ---------------------------------------------------------------------------

def test_nested_user_func_inline_input_length():
    src = """//@version=6
strategy("derived-nested-inline-input")
dirmov(len) =>
    truerange = ta.rma(ta.tr, len)
    plus = ta.rma(close, len) / truerange
adx(dilen, adxlen) =>
    x = dirmov(dilen)
    ta.rma(x, adxlen)
sig = adx(input(15), input(15))
plot(sig)
"""
    cpp = transpile(src)
    rma_members = re.findall(r"(_ta_rma_\d+)\(", cpp)
    assert rma_members, "no RMA member emitted"
    # Every RMA ctor that sizes a buffer must be backed by a runtime reset
    # that reads the input value (override-aware); no bare ``len`` and no
    # unresolved ``input(15)`` spelling may survive in the reset.
    saw_input_backed = False
    for m in rma_members:
        reset = _reset_line(cpp, m)
        if not reset:
            continue
        if "get_input_int(" in reset and "15" in reset:
            saw_input_backed = True
        assert "input(15)" not in reset, (
            f"reset for {m} still carries the unresolved input() spelling: {reset}"
        )
        assert " ta::RMA(len)" not in reset and "= len;" not in reset, (
            f"reset for {m} references the bare param name: {reset}"
        )
    assert saw_input_backed, (
        "no RMA runtime reset references the input value; ctor args did not "
        f"resolve to input-backed expressions. members: {rma_members}"
    )
    assert "ta::RMA(len)" not in cpp
    assert "ta::RMA(input(15))" not in cpp


# ---------------------------------------------------------------------------
# 3. Legitimate input that genuinely defaults to 1 stays period 1
# ---------------------------------------------------------------------------

def test_legit_input_default_one_preserved():
    src = """//@version=6
strategy("legit-default-one")
atrLen = input.int(1, "UT Bot ATR Period")
a = ta.atr(atrLen)
plot(a)
"""
    cpp = transpile(src)
    members = re.findall(r"(_ta_atr_\d+)\(", cpp)
    assert members
    m = members[0]
    assert _ctor_period(cpp, m) == "1"
    # The reset is still emitted so an override (e.g. set ATR Period = 10)
    # re-sizes the buffer — the period 1 here is the genuine Pine default.
    reset = _reset_line(cpp, m)
    assert 'get_input_int("UT Bot ATR Period", 1)' in reset


# ---------------------------------------------------------------------------
# Guardrail: a genuinely-unresolvable computed length raises (no silent 1).
# ---------------------------------------------------------------------------

def test_unresolvable_length_raises_loudly():
    # ``barsSince(...)`` is a runtime series, not a const or input — there is
    # no faithful compile-time buffer size and no input to re-derive from.
    src = """//@version=6
strategy("unresolvable-length")
n = ta.barssince(close > open)
v = ta.ema(close, n)
plot(v)
"""
    with pytest.raises(CompileError):
        transpile(src)


# ---------------------------------------------------------------------------
# Determinism: transpiling the same source twice is byte-identical.
# ---------------------------------------------------------------------------

def test_derived_length_transpile_is_deterministic():
    src = """//@version=6
strategy("determinism")
rsiLen = input.int(14, "RSI Length")
wilderLen = rsiLen * 2 - 1
x = ta.ema(close, wilderLen)
plot(x)
"""
    assert transpile(src) == transpile(src)


# ---------------------------------------------------------------------------
# 4. Stable timeframe-ternary length
#    fastPeriod = isM5 ? math.max(1, int(maPeriodFastInput / 5))
#                     : maPeriodFastInput
#    where isM5 = timeframe.isminutes and timeframe.multiplier == 5
#    All operands are stable per-run scalars (an input + timeframe.*) so the
#    ctor guard must accept it and the reset must render to valid C++ that
#    references no undeclared locals.
# ---------------------------------------------------------------------------

def test_stable_timeframe_ternary_length():
    src = """//@version=6
strategy("stable-timeframe-ternary")
maPeriodFastInput = input.int(60, "Fast SMA Period")
isM5 = timeframe.isminutes and timeframe.multiplier == 5
fastPeriod = isM5 ? math.max(1, int(maPeriodFastInput / 5)) : maPeriodFastInput
fastMA = ta.sma(close, fastPeriod)
plot(fastMA)
"""
    cpp = transpile(src)
    members = re.findall(r"(_ta_sma_\d+)\(", cpp)
    assert members, "no SMA member emitted"
    member = members[0]
    # ctor-init list uses a safe placeholder (1); the runtime reset
    # overwrites it on the first bar.
    assert _ctor_period(cpp, member) == "1"
    reset = _reset_line(cpp, member)
    assert reset, "no runtime reset emitted for stable-timeframe length"
    # Override-aware: the input read appears in the reset.
    assert 'get_input_int("Fast SMA Period", 60)' in reset
    # The timeframe condition renders to the engine's tf_* helpers, NOT to
    # the bare Pine ``timeframe.isminutes`` / a dangling ``isM5`` local.
    assert "tf_is_intraday(script_tf_)" in reset
    assert "tf_multiplier(script_tf_)" in reset
    assert "timeframe.isminutes" not in reset
    assert " isM5 " not in reset
    # Pine ``and`` must be lowered to C++ ``&&``.
    assert " && " in reset
    # Ternary preserved as a C++ ternary.
    assert " ? " in reset and " : " in reset
    # math.max / int(...) cast lowered to valid C++.
    assert "std::max" in reset


# ---------------------------------------------------------------------------
# 5. Function-local math-derived length over input-derived parameters
#    filterLen = math.max(1, int(math.round(2 / a)))   inside AIFilter(data,a,p)
#    where alpha/beta/pi are class-scope stable scalars derived from inputs.
# ---------------------------------------------------------------------------

def test_function_local_math_derived_length():
    src = """//@version=6
strategy("func-local-math-length")
period = input.int(144, "Period")
poles = input.int(4, "Poles")
pi    = math.asin(1) * 2
beta  = (1 - math.cos(2 * pi / period)) / (math.pow(1.414, 2 / poles) - 1)
alpha = -beta + math.sqrt(beta * beta + 2 * beta)
AIFilter(data, a, p) =>
    filterLen = math.max(1, int(math.round(2 / a)))
    ta.ema(data, filterLen)
out = AIFilter(close, alpha, poles)
plot(out)
"""
    cpp = transpile(src)
    members = re.findall(r"(_ta_ema_\d+)\(", cpp)
    assert members, "no EMA member emitted"
    member = members[0]
    # ctor placeholder; reset overwrites it.
    assert _ctor_period(cpp, member) == "1"
    reset = _reset_line(cpp, member)
    assert reset, "no runtime reset emitted for function-local math length"
    # The reset references BOTH inputs (period, poles) override-aware.
    assert 'get_input_int("Period", 144)' in reset
    assert 'get_input_int("Poles", 4)' in reset
    # math.* and the int() cast lower to valid C++; no Pine-namespace leak.
    assert "std::asin" in reset
    assert "std::cos" in reset
    assert "std::sqrt" in reset
    assert "std::pow" in reset
    assert "std::round" in reset
    assert "std::max" in reset
    assert "math.asin" not in reset
    assert "math.cos" not in reset
    # No dangling function-local identifier (``alpha`` / ``filterLen``)
    # should survive — they must be fully expanded to input reads + math.
    assert "alpha" not in reset
    assert "filterLen" not in reset


# ---------------------------------------------------------------------------
# Guardrail (negative): a ternary length whose condition depends on a
# series (ta.*) result stays rejected — it is NOT a stable scalar.
# ---------------------------------------------------------------------------

def test_series_dependent_ternary_length_rejected():
    src = """//@version=6
strategy("series-dep-ternary")
normalSwingLookback = input.int(10, "Normal")
highVolSwingLookback = input.int(20, "High Vol")
highVolThreshold = input.float(2.0, "Threshold")
volatilityRatio = ta.rma(close, 14) / ta.atr(14)
activeSwingLookback = volatilityRatio >= highVolThreshold ? highVolSwingLookback : normalSwingLookback
x = ta.lowest(low, activeSwingLookback)
plot(x)
"""
    with pytest.raises(CompileError) as ei:
        transpile(src)
    assert "Unsupported TA constructor length" in str(ei.value)


# ---------------------------------------------------------------------------
# Guardrail (negative): a reassigned series-dependent length stays rejected.
# ---------------------------------------------------------------------------

def test_series_reassigned_length_rejected():
    src = """//@version=6
strategy("series-reassigned")
n = ta.barssince(close > open)
v = ta.ema(close, n)
plot(v)
"""
    with pytest.raises(CompileError):
        transpile(src)


# ---------------------------------------------------------------------------
# 6. Stable REASSIGNED class-scope scalar length.
#    effectiveSwingSize = swingSize                       (initial: input)
#    if autoSwingSize                                    (cond: input bool)
#        if timeframe.isdaily                            (cond: stable)
#            effectiveSwingSize := math.max(swingSize, 3)
#        else if timeframe.in_seconds() >= 3600          (cond: stable)
#            effectiveSwingSize := math.max(swingSize, 2)
#    pivHi = ta.pivothigh(high, effectiveSwingSize, effectiveSwingSize)
#
#    Stable per run because every condition and assignment uses only
#    inputs / timeframe.* / math.*. The ctor guard must accept it and the
#    reset must render to valid C++ that references the input reads.
# ---------------------------------------------------------------------------

def test_stable_reassigned_class_scope_length():
    src = """//@version=6
strategy("stable-reassigned-scalar")
swingSize = input.int(1, "Pivot Lookback")
autoSwingSize = input.bool(true, "Auto")
effectiveSwingSize = swingSize
if autoSwingSize
    if timeframe.isdaily
        effectiveSwingSize := math.max(swingSize, 3)
    else if timeframe.in_seconds() >= 3600
        effectiveSwingSize := math.max(swingSize, 2)
pivHi = ta.pivothigh(high, effectiveSwingSize, effectiveSwingSize)
plot(pivHi)
"""
    cpp = transpile(src)
    members = re.findall(r"(_ta_pivothigh_\d+)\(", cpp)
    assert members, "no PivotHigh member emitted"
    member = members[0]
    # ctor placeholder; reset overwrites it on the first bar.
    assert _ctor_period(cpp, member) in ("1", "1, 1"), (
        f"expected ctor placeholder, got {_ctor_period(cpp, member)}"
    )
    reset = _reset_line(cpp, member)
    assert reset, "no runtime reset emitted for reassigned scalar length"
    # Override-aware: both inputs appear in the reset.
    assert 'get_input_int("Pivot Lookback", 1)' in reset
    assert 'get_input_bool("Auto", true)' in reset
    # timeframe.* renders to the engine's tf_* helpers, not bare Pine.
    assert "tf_is_daily(script_tf_)" in reset
    assert "tf_to_seconds(script_tf_)" in reset
    assert "timeframe.isdaily" not in reset
    assert "timeframe.in_seconds" not in reset
    # math.max lowered to std::max.
    assert "std::max" in reset
    # No dangling local name survives — it must be fully expanded.
    assert "effectiveSwingSize" not in reset
    assert "autoSwingSize" not in reset


# ---------------------------------------------------------------------------
# Guardrail (negative): the wellmanapex shape — a reassigned scalar whose
# value depends on a SERIES (ta.rma result) — stays rejected even though
# the structure (initial VarDecl + reassigned in a ternary) superficially
# resembles the stable case above.
# ---------------------------------------------------------------------------

def test_series_reassigned_ternary_length_rejected():
    src = """//@version=6
strategy("series-reassigned-ternary")
normalSwingLookback = input.int(10, "Normal")
highVolSwingLookback = input.int(20, "High Vol")
highVolThreshold = input.float(2.0, "Threshold")
volatilityRatio = ta.rma(close, 14) / ta.atr(14)
activeSwingLookback = volatilityRatio >= highVolThreshold ? highVolSwingLookback : normalSwingLookback
x = ta.lowest(low, activeSwingLookback)
plot(x)
"""
    with pytest.raises(CompileError) as ei:
        transpile(src)
    assert "Unsupported TA constructor length" in str(ei.value)


# ---------------------------------------------------------------------------
# 7. Cloned no-ctor TA sites (ta.change) referenced by a cloned function
#    variant MUST be declared + initialized even when the clone is minted
#    while visiting a DEAD user function's body.
#
#    Shape (mirrors quantbyboji-nq-hma-midday DMI):
#      dirmov(len) => uses ta.change (no-ctor) + ta.rma(len) (ctor)
#      adx(dilen, adxlen) => calls dirmov(dilen); ta.rma(adxlen)
#      adx_dead(dilen, adxlen) => calls dirmov(dilen); ta.rma(adxlen)
#      sig = adx(input(15), input(15))   # adx_dead is NEVER called
#
#    The analyzer registers dirmov's cs0 clones while visiting adx's body,
#    and dirmov's cs1 clones while visiting adx_dead's body. adx_dead's
#    TA range therefore INCLUDES dirmov's cs1 clones; when adx_dead is
#    classified dead (zero call sites), the dead-code pass must NOT mark
#    those clones dead -- they belong to dirmov (a LIVE callee), and
#    dirmov_cs1's emitted body references them by name
#    (``_ta_change_<n>_cs1``, ``_ta_rma_<n>_cs1``).  Previously the pass
#    keyed dead-ness off the dead function's TA-range slice and dropped
#    the clone declarations, producing
#    ``error: use of undeclared identifier '_ta_change_<n>_cs1'``.
# ---------------------------------------------------------------------------

def test_cloned_no_ctor_ta_sites_declared_through_dead_caller():
    src = """//@version=6
strategy("cs1-clone-through-dead-fn")
diLen = input.int(15, "DI Length")
adxLen = input.int(15, "ADX Length")
dirmov(len) =>
    up = ta.change(high)
    down = -ta.change(low)
    plusDM = na(up) ? na : up > down and up > 0 ? up : 0
    minusDM = na(down) ? na : down > up and down > 0 ? down : 0
    truerange = ta.rma(ta.tr, len)
    plus = fixnan(100 * ta.rma(plusDM, len) / truerange)
    minus = fixnan(100 * ta.rma(minusDM, len) / truerange)
    [plus, minus]
adx() =>
    [plus, minus] = dirmov(diLen)
    sum = plus + minus
    adx_val = 100 * ta.rma(math.abs(plus - minus) / (sum == 0 ? 1 : sum), adxLen)
    adx_val
adx_dead() =>
    [plus, minus] = dirmov(diLen)
    sum = plus + minus
    adx_val = 100 * ta.rma(math.abs(plus - minus) / (sum == 0 ? 1 : sum), adxLen)
    adx_val
sig = adx()
plot(sig)
"""
    cpp = transpile(src)

    # Every _ta_* identifier referenced in the emitted C++ MUST have a
    # corresponding ``ta::<Class> <name>;`` declaration. This catches the
    # exact regression: ``_ta_change_<n>_cs1`` / ``_ta_rma_<n>_cs1``
    # referenced by the cloned ``dirmov_cs1`` body but dropped from the
    # member list by the dead-code pass.
    declared = set(re.findall(r"ta::\w+\s+(\w+)\s*;", cpp))
    referenced = set()
    for m in re.finditer(r"(_ta_\w+)\s*(?:\.|=)", cpp):
        referenced.add(m.group(1))
    referenced |= set(re.findall(r"(_ta_\w+)\s*\.", cpp))
    # Exclude internal bookkeeping flags (``_ta_initialized_``) -- those are
    # declared as ``bool _ta_initialized_ = false;`` separately, not as TA
    # members; they are not part of this regression.
    referenced.discard("_ta_initialized_")

    missing = referenced - declared
    assert not missing, (
        f"TA members referenced in emitted C++ but never declared: "
        f"{sorted(missing)}"
    )

    # The no-ctor ``ta.change`` clones specifically must be present
    # (this is the exact class that regressed -- it has no ctor arg so the
    # old codegen never even tried to recover it via the reset path).
    change_clones = re.findall(r"ta::Change\s+(_ta_change_\w+)\s*;", cpp)
    assert any("_cs1" in n for n in change_clones), (
        f"expected at least one _ta_change_*_cs1 declaration; got {change_clones}"
    )

    # And at least one ``dirmov_cs1`` body must be emitted (i.e. we did not
    # silently drop the clone -- it compiles against the now-declared members).
    assert re.search(r"\bdirmov_cs1\s*\(", cpp), (
        "dirmov_cs1 clone body not emitted"
    )

    # Sanity: the genuinely-dead adx_dead function is still skipped.
    assert not re.search(r"\badx_dead\b\s*\(", cpp), (
        "dead adx_dead body should not be emitted"
    )


# ---------------------------------------------------------------------------
# Regression: the stable-runtime-length reset re-materializes the length by
# re-parsing the expanded expression and lowering it through the SAME
# expression visitor the statement path uses. Two Pine semantics MUST survive:
#   (1) operator grouping (a parenthesized +/- subexpression under a division),
#   (2) float division of two int operands (Pine `/` never truncates).
# Both used to be destroyed by the old lossy AST->infix serializer +
# token-substitution renderer (grouping flattened, int/int stayed integer
# division), collapsing a derived TA length to 1 and trading zero times.
# ---------------------------------------------------------------------------

def test_reset_preserves_grouping_across_division():
    # length = int(round((a + b) / (a - b))). Pine groups the sum over the
    # difference: (10+3)/(10-3) = 13/7 ≈ 1.857 → 2. If the serializer drops the
    # parentheses the string reassociates under C++ precedence to
    # `a + b / a - b` = 10 + 0.3 - 3 ≈ 7.3 → 7 — a different length. The reset
    # must emit the difference as one grouped divisor, coerced to double.
    src = """//@version=6
strategy("grp")
a = input.int(10, "A")
b = input.int(3, "B")
lenv = int(math.round((a + b) / (a - b)))
plot(ta.ema(close, lenv))
"""
    cpp = transpile(src)
    members = re.findall(r"(_ta_ema_\d+)\(", cpp)
    assert members, "no EMA member emitted"
    reset = _reset_line(cpp, members[0])
    assert reset, "no runtime reset emitted for grouped-division length"
    # Pine `/` is float division → both operands coerced to double.
    assert "(double)(" in reset
    # The difference (a - b) survives as one grouped divisor unit — NOT
    # reassociated so that `+ b` and `- b` become sibling additive terms.
    assert '(get_input_int("A", 10) - get_input_int("B", 3))' in reset
    assert '(get_input_int("A", 10) + get_input_int("B", 3))' in reset
    # The pre-fix flattened form would place the sum's `+ ... /` and a trailing
    # `- get_input_int("B", 3)` as siblings of the top-level division.
    assert '+ get_input_int("B", 3) /' not in reset
    assert "ta::EMA(1)" not in reset  # must NOT collapse to the silent no-op


def test_reset_uses_float_division_for_two_int_operands():
    # 2 / poles must be FLOAT division (Pine semantics): 2/4 = 0.5, so
    # pow(2, 0.5) = 1.414…, *10 → 14.1 → round 14. Integer division would give
    # 2/4 = 0 → pow(2, 0) = 1 → *10 → round 10 — the discriminant/length
    # collapse behind the adxae zero-trades bug.
    src = """//@version=6
strategy("fdiv")
poles = input.int(4, "Poles")
lenv = int(math.round(10 * math.pow(2.0, 2 / poles)))
plot(ta.ema(close, lenv))
"""
    cpp = transpile(src)
    members = re.findall(r"(_ta_ema_\d+)\(", cpp)
    assert members, "no EMA member emitted"
    reset = _reset_line(cpp, members[0])
    assert reset, "no runtime reset emitted for float-division length"
    # The int input operand is coerced to double for the division; the bare
    # `2 / get_input_int(...)` integer-division form (the bug) must be gone.
    assert '(double)(get_input_int("Poles", 4))' in reset
    assert '2 / get_input_int("Poles", 4)' not in reset
    assert "std::pow" in reset
    assert "ta::EMA(1)" not in reset

