"""Phase D Task 2: analyzer must propagate matrix element type through method calls.

- assert ``v = m.get(0, 0)`` types ``v`` as the element type, not VOID/FLOAT.

Two outcomes are tracked by this file:
- Outcome (a): analyzer already infers correctly via some other code path
  (e.g. ``_type_spec_from_expr`` matrix arm in ``analyzer/types.py``). Test
  becomes a regression guard.
- Outcome (b): analyzer returns wrong PineType (defaults to FLOAT or VOID),
  which would mean the missing ``"matrix"`` arm in
  ``pineforge_codegen/analyzer/call_handlers.py`` is a real bug that needs
  fixing.
"""

from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.symbols import PineType


def _analyze(src: str):
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    return Analyzer(ast).analyze()


def test_matrix_get_int_element_inferred_as_int():
    """``v = m.get(0, 0)`` on ``matrix<int>`` must type ``v`` as INT, not FLOAT/VOID."""
    src = '''//@version=6
strategy("t")
var m = matrix.new<int>(2, 2, 0)
v = m.get(0, 0)
'''
    ctx = _analyze(src)
    sym = ctx.symbols.resolve("v")
    assert sym is not None, "analyzer did not register 'v' in symbol table"
    assert sym.pine_type == PineType.INT, (
        f"expected PineType.INT for v from matrix<int>.get(); got {sym.pine_type}"
    )


def test_matrix_get_bool_element_inferred_as_bool():
    """``v = m.get(0, 0)`` on ``matrix<bool>`` must type ``v`` as BOOL."""
    src = '''//@version=6
strategy("t")
var m = matrix.new<bool>(2, 2, false)
v = m.get(0, 0)
'''
    ctx = _analyze(src)
    sym = ctx.symbols.resolve("v")
    assert sym is not None
    assert sym.pine_type == PineType.BOOL, (
        f"expected PineType.BOOL for v from matrix<bool>.get(); got {sym.pine_type}"
    )


def test_matrix_get_udt_element_inferred_as_udt():
    """``v = m.get(0, 0)`` on ``matrix<Pt>`` (UDT) must carry the UDT name on the symbol."""
    src = '''//@version=6
strategy("t")
type Pt
    float x
var m = matrix.new<Pt>(1, 1)
v = m.get(0, 0)
'''
    ctx = _analyze(src)
    sym = ctx.symbols.resolve("v")
    assert sym is not None
    # UDT instances are recorded via Symbol.udt_type_name (kept distinct
    # from the small PineType enum). type_spec also carries it.
    udt_marker = sym.udt_type_name or (
        sym.type_spec.name if sym.type_spec is not None and sym.type_spec.kind == "udt" else None
    )
    assert udt_marker == "Pt", (
        f"expected UDT 'Pt' for v from matrix<Pt>.get(); "
        f"got udt_type_name={sym.udt_type_name!r} type_spec={sym.type_spec!r}"
    )
