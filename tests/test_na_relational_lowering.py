"""KI-71: na-aware relational lowering.

Pine Script relational comparisons (``==`` ``!=`` ``<`` ``>`` ``<=`` ``>=``)
with an ``na`` operand evaluate *falsy*. The engine's na sentinels are
``INT_MIN`` for ``int``/``int64_t`` and NaN for ``double``:

* An ``int`` sentinel is a *finite* very-negative integer, so naive C++
  diverges from Pine on EVERY relational (ordered and equality alike).
* A ``double`` NaN already yields ``false`` under IEEE for
  ``==`` ``<`` ``>`` ``<=`` ``>=`` (matching Pine); the only diverging float
  cell is ``!=`` (IEEE ``NaN != x`` is true, Pine is falsy).

The codegen therefore wraps a relational in an na-aware lambda
(``!is_na(l) && !is_na(r) && (l op r)``) exactly when a diverging cell is
present — any na-capable integer operand, or a ``!=`` with an na-capable
float operand — and leaves the already-correct IEEE cells (pure ``double``
``==`` ``<`` ``>`` ``<=`` ``>=``) as naive relationals.

Exemplar mechanism (pf-probe-concord-lockedregime-composed, event-level
792/792 vs TV): ``trend[1] == na`` on bar 0 makes ``raw_regime`` na; the
``barstate.isfirst`` latch ``raw_regime == 0 ? 1 : raw_regime`` takes the
false branch (na ``==`` is falsy) and stores na into ``var int
locked_regime`` — permanently, since every downstream ``lr == +/-1`` is
falsy. Naive C++ ``INT_MIN != x`` is true, so the pre-fix engine *heals* the
latch; the na-aware lowering keeps it na, matching TV.

REDs contract: every assertion that pins the wrapped form fails against the
pre-fix (aa774ff) lowering, which emits naive relationals with no ``is_na``.
"""

from __future__ import annotations

from pineforge_codegen import transpile

from tests._compile import compile_cpp, skip_if_no_compile_env


def _gen(body: str) -> str:
    return transpile('//@version=6\nstrategy("T")\n' + body + "\n")


# The na-aware wrapper's fingerprint in the emitted C++.
def _wrapped(cpp: str, op: str) -> bool:
    """True iff ``cpp`` contains an na-aware relational lambda using ``op``."""
    return (
        "_pna_l = (" in cpp
        and "!is_na(_pna_l) && !is_na(_pna_r)" in cpp
        and f"(_pna_l {op} _pna_r)" in cpp
    )


# ---------------------------------------------------------------------------
# Integer relationals — the INT_MIN sentinel poisons ALL SIX operators.
# `a`/`b` are na-capable `var int` series (either may latch INT_MIN).
# ---------------------------------------------------------------------------
_INT_PRELUDE = "var int a = na\nvar int b = na\n"


def test_int_eq_wraps():  # na == na / x == na
    cpp = _gen(_INT_PRELUDE + "x = a == b ? 1 : 0\nplot(x)")
    assert _wrapped(cpp, "=="), cpp


def test_int_neq_wraps():  # x != na
    cpp = _gen(_INT_PRELUDE + "x = a != b ? 1 : 0\nplot(x)")
    assert _wrapped(cpp, "!="), cpp


def test_int_gt_wraps():  # x > na
    cpp = _gen(_INT_PRELUDE + "x = a > b ? 1 : 0\nplot(x)")
    assert _wrapped(cpp, ">"), cpp


def test_int_lt_wraps():  # na < x
    cpp = _gen(_INT_PRELUDE + "x = a < b ? 1 : 0\nplot(x)")
    assert _wrapped(cpp, "<"), cpp


def test_int_le_wraps():  # na <= x
    cpp = _gen(_INT_PRELUDE + "x = a <= b ? 1 : 0\nplot(x)")
    assert _wrapped(cpp, "<="), cpp


def test_int_ge_wraps():  # x >= na
    cpp = _gen(_INT_PRELUDE + "x = a >= b ? 1 : 0\nplot(x)")
    assert _wrapped(cpp, ">="), cpp


def test_int_compared_to_literal_wraps():
    """``locked_regime == 0`` — one operand a literal, the other an na-capable
    int — still wraps (the int var can be INT_MIN)."""
    cpp = _gen("var int lr = na\nx = lr == 0 ? 1 : 0\nplot(x)")
    assert _wrapped(cpp, "=="), cpp


def test_int_compared_to_na_literal_wraps():
    """``someInt == na`` — the bare ``na`` lowers to ``na<double>()`` but the
    int operand is INT_MIN-capable, so all-op wrapping applies."""
    cpp = _gen("var int lr = na\nx = lr == na ? 1 : 0\nplot(x)")
    assert _wrapped(cpp, "=="), cpp


# ---------------------------------------------------------------------------
# Float relationals — IEEE already falsy-on-NaN for ==/</>/<=/>=; only != diverges.
# ---------------------------------------------------------------------------
def test_float_neq_wraps():  # the sole diverging float cell
    cpp = _gen("float f = close\nfloat g = open\nx = f != g ? 1 : 0\nplot(x)")
    assert _wrapped(cpp, "!="), cpp


def test_float_neq_na_literal_wraps():
    cpp = _gen("float f = close\nx = f != na ? 1 : 0\nplot(x)")
    assert _wrapped(cpp, "!="), cpp


# ---------------------------------------------------------------------------
# GUARD — pure double ==/</>/<=/>= must stay NAIVE (IEEE already matches TV).
# Wrapping them would be a pure no-op, so churn must be avoided AND the
# already-correct IEEE behaviour must not be disturbed.
# ---------------------------------------------------------------------------
def test_float_eq_is_naive():
    cpp = _gen("float f = close\nfloat g = open\nx = f == g ? 1 : 0\nplot(x)")
    assert "_pna_" not in cpp, cpp
    assert "(f == g)" in cpp, cpp


def test_float_lt_is_naive():
    cpp = _gen("float f = close\nfloat g = open\nx = f < g ? 1 : 0\nplot(x)")
    assert "_pna_" not in cpp, cpp
    assert "(f < g)" in cpp, cpp


def test_float_gt_is_naive():
    cpp = _gen("float f = close\nfloat g = open\nx = f > g ? 1 : 0\nplot(x)")
    assert "_pna_" not in cpp, cpp
    assert "(f > g)" in cpp, cpp


def test_float_ge_is_naive():
    cpp = _gen("float f = close\nfloat g = open\nx = f >= g ? 1 : 0\nplot(x)")
    assert "_pna_" not in cpp, cpp


def test_float_le_is_naive():
    cpp = _gen("float f = close\nfloat g = open\nx = f <= g ? 1 : 0\nplot(x)")
    assert "_pna_" not in cpp, cpp


def test_numeric_literal_comparison_is_naive():
    """Two compile-time constants can never be na — stay naive, no churn."""
    cpp = _gen("x = 3 > 2 ? 1 : 0\nplot(x)")
    assert "_pna_" not in cpp, cpp


def test_bool_and_or_unaffected():
    """``and``/``or`` are boolean logic, never relational — untouched."""
    cpp = _gen("bool p = close > open\nbool q = close < open\n"
               "x = p and q ? 1 : 0\nplot(x)")
    # the AND itself is naive &&; only the inner close>open (double >) matters,
    # which is naive. No na-aware wrapping anywhere.
    assert "_pna_" not in cpp, cpp
    assert "&&" in cpp, cpp


# ---------------------------------------------------------------------------
# request.security is a SECOND relational emission site — must be covered.
# ---------------------------------------------------------------------------
def test_security_int_relational_wraps():
    cpp = _gen('var int r = na\n'
               'htf = request.security(syminfo.tickerid, "D", r == 0 ? 1 : 2)\n'
               'plot(htf)')
    assert _wrapped(cpp, "=="), cpp


def test_security_double_ordered_is_naive():
    cpp = _gen('htf = request.security(syminfo.tickerid, "D", '
               'ta.change(close) > 0 ? 1 : 0)\nplot(htf)')
    assert "_pna_" not in cpp, cpp


# ---------------------------------------------------------------------------
# Concord latch shape, end-to-end: var int latched na at bar 0 via isfirst +
# ternary, stays na, and every downstream lr comparison is falsy.
# ---------------------------------------------------------------------------
def test_concord_latch_shape_end_to_end():
    src = (
        "int trend = na\n"           # trend[1] on bar 0 is na
        "int raw = trend\n"
        "var int lr = 0\n"
        "if barstate.isfirst\n"
        "    lr := raw == 0 ? 1 : raw\n"   # na == 0 falsy -> latches raw (na)
        "var int cb = 0\n"
        "cb := raw != lr ? cb + 1 : 0\n"   # na-aware != feeds the reset branch
        "up = lr == 1\n"                   # falsy forever once lr is na
        "dn = lr == -1\n"
        "x = up ? 1 : (dn ? -1 : 0)\n"
        "plot(x)"
    )
    cpp = _gen(src)
    # The isfirst latch condition, the cb reset != and both lr==±1 reads
    # must all be na-aware.
    assert _wrapped(cpp, "=="), cpp
    assert _wrapped(cpp, "!="), cpp
    # There must be several wrapped comparisons (latch, cb, up, dn).
    assert cpp.count("_pna_l = (") >= 4, cpp


# ---------------------------------------------------------------------------
# The emitted lambda must actually compile against the engine headers
# (is_na resolves via `using namespace pineforge;`; auto temps deduce int/double).
# ---------------------------------------------------------------------------
def test_na_relational_compiles():
    skip_if_no_compile_env()
    cpp = _gen(
        "var int a = na\nvar int b = na\nfloat f = close\n"
        "x = (a == b) or (a < b) or (a != b) or (f != f) ? 1 : 0\nplot(x)"
    )
    assert "_pna_l = (" in cpp
    compile_cpp(cpp, label="ki71_na_relational")
