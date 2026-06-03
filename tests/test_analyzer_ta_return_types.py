"""F3.1 regression guard: TA functions returning array/tuple must keep their
correct structured type, not silently collapse to a scalar.

Investigation outcome (see the module-level note below) is the matrix-inference
precedent from ``tests/test_analyzer_matrix_inference.py``:

The analyzer's :class:`~pineforge_codegen.symbols.PineType` enum is deliberately
small (``INT / FLOAT / BOOL / STRING / COLOR / VOID / NA / UNKNOWN``) and has no
``ARRAY`` or ``TUPLE`` member. Structured shape (``array<float>`` etc.) is
carried on ``Symbol.type_spec`` (a :class:`TypeSpec`), produced by the separate
``_type_spec_from_expr`` pass in ``analyzer/types.py``. Downstream array/tuple
handling reads ``type_spec``, not ``pine_type``.

Therefore F3.1's stated fix ("``_handle_ta_call`` should return
``PineType.ARRAY`` / ``PineType.TUPLE``") does not apply here — there is nothing
to return. The genuinely-load-bearing facts are:

- ``ta.pivot_point_levels(...)`` -> ``Symbol.type_spec`` is ``array<float>``
  (via the ``ns == "ta" and func == "pivot_point_levels"`` arm of
  ``_type_spec_from_expr``). The legacy ``pine_type`` slot stays ``FLOAT``.
- Tuple-returning TA (``ta.macd``, ``ta.bb``, ``ta.kc``, ``ta.dmi``,
  ``ta.supertrend``, 3-arg ``ta.vwap`` bands) is consumed through tuple
  destructuring; each destructured element is a scalar ``FLOAT`` symbol. No
  single ``PineType.TUPLE`` variable is ever produced.

These tests lock that behavior in so a future change to the inference paths
cannot regress array shape back to a bare scalar without tripping a test.
"""

from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.symbols import PineType


def _analyze(src: str):
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    return Analyzer(ast).analyze()


def test_pivot_point_levels_infers_array_type_spec():
    """``ta.pivot_point_levels`` must carry an ``array<float>`` type_spec."""
    src = '''//@version=6
strategy("t")
lvls = ta.pivot_point_levels("Traditional", barstate.islast)
'''
    ctx = _analyze(src)
    sym = ctx.symbols.resolve("lvls")
    assert sym is not None, "analyzer did not register 'lvls' in symbol table"
    assert sym.type_spec is not None, (
        "expected a structured TypeSpec for ta.pivot_point_levels; got None "
        "(array shape would be lost downstream)"
    )
    assert sym.type_spec.kind == "array", (
        f"expected array TypeSpec for ta.pivot_point_levels; got "
        f"kind={sym.type_spec.kind!r}"
    )
    assert sym.type_spec.element is not None
    assert sym.type_spec.element.kind == "primitive"
    assert sym.type_spec.element.name == "float", (
        f"expected array<float>; got array<{sym.type_spec.element.name}>"
    )


def test_macd_tuple_destructure_elements_are_float():
    """``[m, s, h] = ta.macd(...)`` destructures into three scalar FLOAT symbols."""
    src = '''//@version=6
strategy("t")
[macdLine, signalLine, histLine] = ta.macd(close, 12, 26, 9)
'''
    ctx = _analyze(src)
    for name in ("macdLine", "signalLine", "histLine"):
        sym = ctx.symbols.resolve(name)
        assert sym is not None, f"analyzer did not register {name!r}"
        assert sym.pine_type == PineType.FLOAT, (
            f"expected FLOAT for ta.macd element {name!r}; got {sym.pine_type}"
        )


def test_bb_tuple_destructure_elements_are_float():
    """``[mid, up, low] = ta.bb(...)`` destructures into scalar FLOAT symbols."""
    src = '''//@version=6
strategy("t")
[mid, upper, lower] = ta.bb(close, 20, 2.0)
'''
    ctx = _analyze(src)
    for name in ("mid", "upper", "lower"):
        sym = ctx.symbols.resolve(name)
        assert sym is not None, f"analyzer did not register {name!r}"
        assert sym.pine_type == PineType.FLOAT, (
            f"expected FLOAT for ta.bb element {name!r}; got {sym.pine_type}"
        )


def test_vwap_bands_tuple_destructure_elements_are_float():
    """3-arg ``ta.vwap`` (bands) destructures into three scalar FLOAT symbols."""
    src = '''//@version=6
strategy("t")
[vwapVal, upperBand, lowerBand] = ta.vwap(close, time, 1.0)
'''
    ctx = _analyze(src)
    for name in ("vwapVal", "upperBand", "lowerBand"):
        sym = ctx.symbols.resolve(name)
        assert sym is not None, f"analyzer did not register {name!r}"
        assert sym.pine_type == PineType.FLOAT, (
            f"expected FLOAT for ta.vwap bands element {name!r}; got {sym.pine_type}"
        )
