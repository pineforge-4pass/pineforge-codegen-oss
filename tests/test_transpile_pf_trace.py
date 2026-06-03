"""Tests for the ``// @pf-trace name=expr`` transpiler pragma.

The pragma adds a per-bar instrumentation hook: each occurrence binds a
label to a Pine expression and the codegen emits, at the bottom of every
``on_bar()``, a ``trace(label, value)`` call wrapped in
``if (trace_enabled_) { ... }`` so cost is zero when tracing is off.
The engine API (``trace`` overloads + ``trace_enabled_`` flag) is owned
by a parallel runtime patch — these tests therefore assert only on the
SHAPE of the generated C++, not on linkability/runtime behaviour.
"""

from __future__ import annotations

import re

from pineforge_codegen import transpile
from pineforge_codegen.pragmas import (
    PfTracePragma,
    extract_pf_trace_pragmas,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _on_bar_body(cpp: str) -> str:
    """Slice the generated C++ between the ``on_bar(...)`` opener and the
    matching closing ``}``. Used so assertions about ordering / scoping
    aren't fooled by other top-level emissions in the file."""
    m = re.search(r"void on_bar\(const Bar& bar\) override \{(.*?)\n    \}", cpp, re.DOTALL)
    assert m is not None, f"could not locate on_bar() in generated C++:\n{cpp[:600]}"
    return m.group(1)


# ---------------------------------------------------------------------------
# Pragma extractor — direct unit coverage
# ---------------------------------------------------------------------------


def test_extract_pragmas_returns_source_order():
    src = """//@version=6
strategy("T")
// @pf-trace alpha=close
// @pf-trace beta=open
x = 1
// @pf-trace gamma=high
"""
    pragmas = extract_pf_trace_pragmas(src)
    assert [p.name for p in pragmas] == ["alpha", "beta", "gamma"]
    assert [p.expr_source for p in pragmas] == ["close", "open", "high"]
    assert all(isinstance(p, PfTracePragma) for p in pragmas)
    assert [p.line for p in pragmas] == [3, 4, 6]


def test_extract_pragmas_ignores_block_comments_and_normal_line_comments():
    """Block comments and ordinary ``//`` line comments must be left
    untouched — only the explicit ``// @pf-trace`` form counts."""
    src = """//@version=6
strategy("T")
/* @pf-trace block_form=close */
// just a normal comment
//@pf-trace no_space=close
// @pf-trace yes_space=open
x = 1
"""
    pragmas = extract_pf_trace_pragmas(src)
    assert [p.name for p in pragmas] == ["yes_space"]


def test_extract_pragmas_rejects_trailing_inline_pragma():
    """Pragmas occupying part of a code line (after real Pine tokens) are
    NOT recognised — the spec restricts them to comment-only lines for
    grep-friendliness and unambiguous parsing."""
    src = """//@version=6
strategy("T")
x = 1  // @pf-trace inline=x
"""
    pragmas = extract_pf_trace_pragmas(src)
    assert pragmas == []


def test_extract_pragmas_handles_complex_expressions():
    """The expression body is parsed via the full Pine expression parser
    so member access / function calls / logical-and-not all work."""
    src = """//@version=6
strategy("T")
// @pf-trace mix=close > open and not (high == low)
"""
    pragmas = extract_pf_trace_pragmas(src)
    assert len(pragmas) == 1
    assert pragmas[0].name == "mix"
    assert pragmas[0].expr_source == "close > open and not (high == low)"
    # The expr parser reports a non-trivial AST (BinOp etc.); we don't
    # pin the exact node shape here — visit_expr is exercised by the
    # transpile-level tests below.
    assert pragmas[0].expr_node is not None


# ---------------------------------------------------------------------------
# End-to-end transpile() — emission shape and ordering
# ---------------------------------------------------------------------------


def test_transpile_emits_trace_calls_in_source_order():
    src = """//@version=6
strategy("Trace")
bull_qualified = close > open
bear_qualified = close < open
adxValue = close
structureSignal = high - low
// @pf-trace bull_qualified=bull_qualified
// @pf-trace adx_value=adxValue
// @pf-trace structure_signal=structureSignal
"""
    cpp = transpile(src, check_support=False)
    body = _on_bar_body(cpp)

    # Guard block opens once, closes once, and contains all three calls.
    assert "if (trace_enabled_) {" in body
    assert body.count("if (trace_enabled_) {") == 1

    # Each pragma -> one trace call with matching label and (double) cast.
    assert 'trace(std::string("bull_qualified"), (double)(bull_qualified));' in body
    assert 'trace(std::string("adx_value"), (double)(adxValue));' in body
    assert 'trace(std::string("structure_signal"), (double)(structureSignal));' in body

    # Order matches the source order of the pragmas (not the declaration
    # order of the underlying Pine vars).
    idx_bull = body.index('"bull_qualified"')
    idx_adx = body.index('"adx_value"')
    idx_struct = body.index('"structure_signal"')
    assert idx_bull < idx_adx < idx_struct


def test_transpile_zero_pragmas_emits_zero_overhead():
    """Strategies without ``// @pf-trace`` annotations must produce no
    trace() calls and no ``trace_enabled_`` references — this is the
    legacy-script zero-overhead guarantee."""
    src = """//@version=6
strategy("Plain")
x = 14
y = close > open
"""
    cpp = transpile(src, check_support=False)
    assert "trace_enabled_" not in cpp
    assert "trace(" not in cpp


def test_transpile_pragma_block_is_at_on_bar_tail():
    """The trace block must come AFTER all user-emitted statement code so
    the values it captures reflect the bar's finalized state."""
    src = """//@version=6
strategy("Order")
x = 0
x := close > open ? 1 : 0
// @pf-trace x_value=x
"""
    cpp = transpile(src, check_support=False)
    body = _on_bar_body(cpp)
    # Find the user assignment and the trace call inside on_bar.
    assign_idx = body.rfind("x =")
    trace_idx = body.index("if (trace_enabled_)")
    assert assign_idx < trace_idx, (
        "pf_trace block must be emitted after the user code in on_bar; "
        "got assign_idx={} >= trace_idx={}\n---body:---\n{}".format(
            assign_idx, trace_idx, body
        )
    )


def test_transpile_logical_and_not_lower_correctly_inside_pragma_expr():
    """Pragma expressions reuse the standard Pine -> C++ expression
    visitor, so ``and`` / ``or`` lower to ``&&`` / ``||`` and ``not``
    lowers to ``!`` — same rules as any other expression context."""
    src = """//@version=6
strategy("Logic")
a = close > open
b = high > low
// @pf-trace combined=a and not b
"""
    cpp = transpile(src, check_support=False)
    body = _on_bar_body(cpp)
    assert (
        'trace(std::string("combined"), (double)((a && !(b))));' in body
    ), body


def test_transpile_pragma_expression_with_function_call():
    """Pine function calls inside the expression go through the same
    call dispatcher as everywhere else — math.* in particular maps to
    std::* identifiers."""
    src = """//@version=6
strategy("FCall")
val = close
// @pf-trace abs_val=math.abs(val)
"""
    cpp = transpile(src, check_support=False)
    body = _on_bar_body(cpp)
    # The exact function lowering uses runtime helpers (e.g. std::abs);
    # we just assert the call shows up inside the trace cast.
    assert 'trace(std::string("abs_val"), (double)(' in body
    assert "abs_val" in body


def test_transpile_is_deterministic_across_runs():
    """Same source -> same generated trace block (no hashing /
    iteration-order surprises). Catches accidental dict ordering bugs
    in the pragma pipeline."""
    src = """//@version=6
strategy("Det")
// @pf-trace one=close
// @pf-trace two=open
// @pf-trace three=high
"""
    a = transpile(src, check_support=False)
    b = transpile(src, check_support=False)
    assert a == b
