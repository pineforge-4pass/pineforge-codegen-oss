"""Pine Script v6 division semantics in the transpiler.

In v5, ``int / int`` returned ``int`` (truncated). In v6, ``a / b`` always
returns ``float`` regardless of operand qualifiers — i.e., ``1 / 2 == 0.5``
in v6 (was ``0`` in v5).

PineForge supports v6 only (the analyzer rejects ``//@version=`` < 6), so
the transpiler must emit C++ that matches v6's float-division behaviour.
The C++ default of integer-division on integer operands silently produces
v5 semantics, so we emit ``((double)(left) / (double)(right))`` which:

* yields full fractional precision on int / int operands (v6-correct), and
* is a no-op cast when either side is already double.

Reference: https://www.tradingview.com/pine-script-docs/concepts/operators/
"""

from __future__ import annotations

from pineforge_codegen import transpile


def _wrap(body: str) -> str:
    return (
        "//@version=6\n"
        "strategy('div', overlay=true)\n"
        + body
    )


def test_int_lit_div_emits_double_cast():
    """``1 / 2`` -> ``((double)(1) / (double)(2))`` so the C++ result is 0.5,
    matching Pine v6 (where ``1 / 2 == 0.5``)."""
    cpp = transpile(_wrap("float x = 1 / 2\nplot(x)\n"))
    # Look for the cast pattern. We don't pin exact whitespace because the
    # codegen may evolve; we only require both numerator and denominator be
    # cast to double.
    assert "(double)(1)" in cpp
    assert "(double)(2)" in cpp


def test_int_var_div_emits_double_cast():
    """``int a = 7; int b = 2; float x = a / b`` -> 3.5, not 3."""
    cpp = transpile(_wrap("int a = 7\nint b = 2\nfloat x = a / b\nplot(x)\n"))
    # The expression should preserve the v6 float result, so both operands
    # must be cast to double in the emitted division.
    assert "/ (double)(" in cpp
    # And the result is float-promoted.
    assert "double x" in cpp


def test_int_series_div_emits_double_cast():
    """``int_series / int_lit`` -> float series in Pine v6."""
    src = _wrap(
        "var int counter = 0\n"
        "counter := counter + 1\n"
        "float x = counter / 4\n"
        "plot(x)\n"
    )
    cpp = transpile(src)
    # Cast both sides to double for v6-correct float division.
    assert "(double)(" in cpp
    assert "/ (double)(" in cpp


def test_div_inside_complex_expr():
    """Nested divisions and mixed operators should each be cast.

    ``(a + b + c + d) / 4`` -> all four ints sum to int, then ``int / 4`` must
    still be float-divided. This is the exact pattern in community/VCP that
    motivated the fix — ``trendAlignment = (struct + mom + vol + mtf) / 4``
    where struct/mom/vol/mtf are all ``int`` series in {-1, 0, 1}.
    """
    src = _wrap(
        "int a = 1\nint b = 0\nint c = 0\nint d = 1\n"
        "float trendAlignment = (a + b + c + d) / 4\n"
        "plot(trendAlignment)\n"
    )
    cpp = transpile(src)
    # The outer / must be cast to double.
    assert "/ (double)(4))" in cpp
    # And the LHS variable is float.
    assert "double trendAlignment" in cpp


def test_double_cast_idempotent_for_float():
    """Float-typed operands should still emit the cast (it's a no-op);
    we don't want to special-case 'already double' because the codegen
    doesn't always have access to inferred types at emit time, and
    ``(double)x`` on a ``double x`` is free in optimized C++."""
    cpp = transpile(_wrap("float x = 3.14 / 2.0\nplot(x)\n"))
    assert "(double)(3.14)" in cpp
    assert "(double)(2.0)" in cpp


def test_modulo_not_affected_by_div_fix():
    """``%`` -> ``std::fmod`` is unchanged. The div fix should not break
    the existing ``%``-as-fmod conversion."""
    cpp = transpile(_wrap("float x = 5 % 3\nplot(x)\n"))
    # Modulo path: std::fmod with double casts on both sides.
    assert "std::fmod((double)(5), (double)(3))" in cpp
    # And no double-cast slash form for the % operator.
    assert "(double)(5) / (double)(3)" not in cpp


def test_other_arithmetic_ops_unchanged():
    """``+``, ``-``, ``*`` should NOT receive the double cast — they are
    well-defined for int+int (giving int), and Pine v6 only changed ``/``."""
    cpp = transpile(_wrap("int a = 1 + 2\nint b = 3 - 1\nint c = 2 * 3\nplot(a)\nplot(b)\nplot(c)\n"))
    # Plus/minus/times should keep their plain form.
    assert "(1 + 2)" in cpp
    assert "(3 - 1)" in cpp
    assert "(2 * 3)" in cpp
    # And no spurious cast on these operators.
    assert "(double)(1) + (double)(2)" not in cpp
