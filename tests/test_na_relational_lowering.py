"""KI-71/KI-73: Pine-compatible relational lowering.

Pine Script relational comparisons (``==`` ``!=`` ``<`` ``>`` ``<=`` ``>=``)
with an ``na`` operand evaluate *falsy*. The engine's na sentinels are
``INT_MIN`` for ``int``/``int64_t`` and NaN for ``double``:

* An ``int`` sentinel is a *finite* very-negative integer, so naive C++
  diverges from Pine on EVERY relational (ordered and equality alike).
* A ``double`` NaN already yields ``false`` under IEEE for
  ``==`` ``<`` ``>`` ``<=`` ``>=`` (matching Pine); the only diverging float
  cell is ``!=`` (IEEE ``NaN != x`` is true, Pine is falsy).

The codegen wraps every numeric relational involving a float in a single-
evaluation lambda. It rejects ``na`` operands and derives all six operators
from one magnitude-independent equality predicate:
``left == right or abs(left - right) <= 1e-10``. Pure integer comparisons keep
the smaller KI-71 wrapper only when an operand can carry the integer ``na``
sentinel. Boolean, string, and keyed-switch equality remain native C++.

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


def _float_cmp(cpp: str, op: str) -> bool:
    """True iff a float relational applies Pine's fixed-band comparator."""
    compare = {
        "==": "(_pfc_eq)",
        "!=": "(!_pfc_eq)",
        "<": "((_pfc_l < _pfc_r) && !_pfc_eq)",
        ">": "((_pfc_l > _pfc_r) && !_pfc_eq)",
        "<=": "((_pfc_l < _pfc_r) || _pfc_eq)",
        ">=": "((_pfc_l > _pfc_r) || _pfc_eq)",
    }[op]
    return (
        "_pna_l = (" in cpp
        and "!is_na(_pna_l) && !is_na(_pna_r)" in cpp
        and "std::fabs(_pfc_l - _pfc_r) <= 1e-10" in cpp
        and compare in cpp
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
    mixed inferred types route through the float comparator; both sentinels
    are still rejected before the equality result is returned."""
    cpp = _gen("var int lr = na\nx = lr == na ? 1 : 0\nplot(x)")
    assert _float_cmp(cpp, "=="), cpp


# ---------------------------------------------------------------------------
# Float relationals — all six share one fixed absolute equality band. The same
# wrapper also preserves Pine's falsy-on-na rule for every operator.
# ---------------------------------------------------------------------------
def test_float_neq_wraps():
    cpp = _gen("float f = close\nfloat g = open\nx = f != g ? 1 : 0\nplot(x)")
    assert _float_cmp(cpp, "!="), cpp


def test_float_neq_na_literal_wraps():
    cpp = _gen("float f = close\nx = f != na ? 1 : 0\nplot(x)")
    assert _float_cmp(cpp, "!="), cpp


# ---------------------------------------------------------------------------
# The other five float operators use the same equality-band lowering.
# ---------------------------------------------------------------------------
def test_float_eq_uses_fixed_band():
    cpp = _gen("float f = close\nfloat g = open\nx = f == g ? 1 : 0\nplot(x)")
    assert _float_cmp(cpp, "=="), cpp


def test_float_lt_uses_fixed_band():
    cpp = _gen("float f = close\nfloat g = open\nx = f < g ? 1 : 0\nplot(x)")
    assert _float_cmp(cpp, "<"), cpp


def test_float_gt_uses_fixed_band():
    cpp = _gen("float f = close\nfloat g = open\nx = f > g ? 1 : 0\nplot(x)")
    assert _float_cmp(cpp, ">"), cpp


def test_float_ge_uses_fixed_band():
    cpp = _gen("float f = close\nfloat g = open\nx = f >= g ? 1 : 0\nplot(x)")
    assert _float_cmp(cpp, ">="), cpp


def test_float_le_uses_fixed_band():
    cpp = _gen("float f = close\nfloat g = open\nx = f <= g ? 1 : 0\nplot(x)")
    assert _float_cmp(cpp, "<="), cpp


def test_finn_ratio_boundary_uses_fixed_band():
    cpp = _gen(
        "float body = math.abs(close - open)\n"
        "float upper = high - math.max(close, open)\n"
        "float ratio = upper / body\n"
        "x = ratio >= 2.5 ? 1 : 0\nplot(x)"
    )
    assert _float_cmp(cpp, ">="), cpp


def test_mixed_float_int_comparison_uses_fixed_band():
    cpp = _gen("float f = close\nx = f >= 2 ? 1 : 0\nplot(x)")
    assert _float_cmp(cpp, ">="), cpp


def test_float_band_is_overflow_safe_and_preserves_infinities():
    cpp = _gen("float f = close\nx = f == 1e300 ? 1 : 0\nplot(x)")
    assert _float_cmp(cpp, "=="), cpp
    assert "(_pfc_l == _pfc_r) ||" in cpp, cpp
    assert "std::isfinite(_pfc_l) && std::isfinite(_pfc_r)" in cpp, cpp
    assert "_pfc_l *" not in cpp, cpp


def test_float_relational_evaluates_stateful_operand_once():
    cpp = _gen("x = math.random(0, 1, 7) >= 0.5 ? 1 : 0\nplot(x)")
    assert _float_cmp(cpp, ">="), cpp
    assert cpp.count("pine_random(") == 1, cpp


def test_numeric_literal_comparison_is_naive():
    """Two compile-time constants can never be na — stay naive, no churn."""
    cpp = _gen("x = 3 > 2 ? 1 : 0\nplot(x)")
    assert "_pna_" not in cpp, cpp


def test_bool_and_or_unaffected():
    """``and``/``or`` are boolean logic, never fixed-band relationals."""
    cpp = _gen("bool p = true\nbool q = false\n"
               "x = p and q ? 1 : 0\nplot(x)")
    assert "_pna_" not in cpp, cpp
    assert "_pfc_eq" not in cpp, cpp
    assert "&&" in cpp, cpp


def test_string_equality_does_not_use_float_band():
    cpp = _gen('string a = "a"\nstring b = "b"\nx = a == b ? 1 : 0\nplot(x)')
    assert "_pfc_eq" not in cpp, cpp
    assert 'std::string("a") == std::string("b")' in cpp, cpp


# ---------------------------------------------------------------------------
# Keyed switch uses exact matching, not float comparison's equality band.
# TradingView oracle: switch 1.0000000004 with case 1.0 takes the default arm.
# ---------------------------------------------------------------------------
def test_float_keyed_switch_expression_stays_exact():
    cpp = _gen(
        "float f = close\n"
        "int x = switch f\n"
        "    1.0000000004 => 1\n"
        "    => 0\n"
        "plot(x)"
    )
    assert "auto __switch_val_" in cpp, cpp
    assert "__switch_val_0 == 1.0000000004" in cpp, cpp
    assert "_pfc_eq" not in cpp, cpp


def test_float_keyed_switch_multiple_cases_stay_exact():
    cpp = _gen(
        "float f = close\n"
        "int x = switch f\n"
        "    1.0000000004 => 1\n"
        "    2.0 => 2\n"
        "    => 0\n"
        "plot(x)"
    )
    assert "__switch_val_0 == 1.0000000004" in cpp, cpp
    assert "__switch_val_0 == 2.0" in cpp, cpp
    assert "_pfc_eq" not in cpp, cpp


def test_float_keyed_switch_statement_stays_exact():
    cpp = _gen(
        "float f = close\n"
        "int x = 0\n"
        "switch f\n"
        "    1.0000000004 =>\n"
        "        x := 1\n"
        "plot(x)"
    )
    assert "auto __switch_val_" in cpp, cpp
    assert "__switch_val_0 == 1.0000000004" in cpp, cpp
    assert "_pfc_eq" not in cpp, cpp


def test_int_and_string_keyed_switches_stay_exact():
    cpp_int = _gen(
        "int mode = 1\n"
        "int x = switch mode\n"
        "    1 => 1\n"
        "    => 0\n"
        "plot(x)"
    )
    cpp_string = _gen(
        'string mode = "A"\n'
        "int x = switch mode\n"
        '    "A" => 1\n'
        "    => 0\n"
        "plot(x)"
    )
    assert "_pfc_eq" not in cpp_int, cpp_int
    assert "_pfc_eq" not in cpp_string, cpp_string


def test_float_keyed_switch_evaluates_discriminator_once():
    cpp = _gen(
        "int x = switch math.random(0, 1, 7)\n"
        "    0.5 => 1\n"
        "    => 0\n"
        "plot(x)"
    )
    assert "_pfc_eq" not in cpp, cpp
    assert cpp.count("pine_random(") == 1, cpp


# ---------------------------------------------------------------------------
# request.security is a SECOND relational emission site — must be covered.
# ---------------------------------------------------------------------------
def test_security_int_relational_wraps():
    cpp = _gen('var int r = na\n'
               'htf = request.security(syminfo.tickerid, "D", r == 0 ? 1 : 2)\n'
               'plot(htf)')
    assert _wrapped(cpp, "=="), cpp


def test_security_double_ordered_uses_float_band():
    cpp = _gen('htf = request.security(syminfo.tickerid, "D", '
               'ta.change(close) > 0 ? 1 : 0)\nplot(htf)')
    assert _float_cmp(cpp, ">"), cpp


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
    assert "_pfc_eq" in cpp
    compile_cpp(cpp, label="ki71_na_relational")
