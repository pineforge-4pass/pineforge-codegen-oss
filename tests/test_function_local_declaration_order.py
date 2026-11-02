"""Callable reads use declarations already visible at the read."""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import diagnostics_catalog, render_diagnostic, transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp


HEADER = '//@version=6\nstrategy("declaration order")\n'
REAL = (
    Path(__file__).parent / "fixtures" / "function_definition_scope"
    / "function_uses_late_local.pine"
).read_text()

REFUSALS = [
    pytest.param(
        "read() =>\n    value = pending\n    float pending = close - open\n"
        "    value\nout = read()\n",
        "pending", 4, 13, id="renamed-local",
    ),
    pytest.param(
        "read() =>\n    if close > open\n        value = delayed\n"
        "        delayed = close - open\n        value\n"
        "    else\n        0.0\nout = read()\n",
        "delayed", 5, 17, id="if-body",
    ),
    pytest.param(
        "read() =>\n    value = inside\n    if close > open\n"
        "        float inside = 1.0\n    value\nout = read()\n",
        "inside", 4, 13, id="before-child-declaration",
    ),
    pytest.param(
        "read() =>\n    if close > open\n        value = upcoming\n"
        "    float upcoming = close - open\n    upcoming\nout = read()\n",
        "upcoming", 5, 17, id="nested-read-before-outer-declaration",
    ),
    pytest.param(
        "read() =>\n    float total = 0.0\n    for i = 0 to 1\n"
        "        value = increment\n        increment = close - open\n"
        "        total += value\n    total\nout = read()\n",
        "increment", 6, 17, id="counted-loop-body",
    ),
    pytest.param(
        "read() =>\n    float total = 0.0\n    for item in array.from(close, open)\n"
        "        value = adjustment\n        adjustment = item + 1.0\n"
        "        total += value\n    total\nout = read()\n",
        "adjustment", 6, 17, id="iterable-loop-body",
    ),
    pytest.param(
        "read() =>\n    int i = 0\n    while i < 1\n"
        "        value = difference\n        difference = close - open\n"
        "        i += 1\n    0.0\nout = read()\n",
        "difference", 6, 17, id="while-body",
    ),
    pytest.param(
        "read() =>\n    switch\n        close > open =>\n"
        "            value = payoff\n            payoff = close - open\n"
        "            value\n        => 0.0\nout = read()\n",
        "payoff", 6, 21, id="switch-arm",
    ),
    pytest.param(
        "method delta(float self) =>\n    value = nextDelta\n"
        "    nextDelta = self - open\n    value\nout = close.delta()\n",
        "nextDelta", 4, 13, id="method-body",
    ),
    pytest.param(
        "pair() => [close, open]\nread() =>\n    value = firstPart\n"
        "    [firstPart, secondPart] = pair()\n    value\nout = read()\n",
        "firstPart", 5, 13, id="tuple-declaration",
    ),
    pytest.param(
        "read() =>\n    value = math.abs(imminent)\n"
        "    imminent = close - open\n    value\nout = read()\n",
        "imminent", 4, 22, id="positional-argument",
    ),
    pytest.param(
        "read() =>\n    value = math.abs(number = imminent)\n"
        "    imminent = close - open\n    value\nout = read()\n",
        "imminent", 4, 31, id="keyword-argument",
    ),
    pytest.param(
        "read() =>\n    value = historical[1]\n"
        "    historical = close - open\n    value\nout = read()\n",
        "historical", 4, 13, id="history-base",
    ),
    pytest.param(
        "read() =>\n    value = read\n    float read = close - open\n"
        "    value\nout = read()\n",
        "read", 4, 13, id="callable-label-is-not-an-outer-value",
    ),
    pytest.param(
        "type Cell\n    float n\nread() =>\n    value = Cell\n"
        "    float Cell = close - open\n    value\nout = read()\n",
        "Cell", 6, 13, id="type-label-is-not-an-outer-value",
    ),
]


def _assert_late_local(source: str, name: str, line: int, col: int, **kwargs) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="order.pine", **kwargs)
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E2075"
    assert diagnostic.args == {"name": name}
    assert diagnostic.message == (
        f"Variable '{name}' is not declared in the current lexical scope before this use."
    )
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.location.file == "order.pine"
    assert (
        diagnostic.location.line, diagnostic.location.col, diagnostic.location.end_col,
    ) == (line, col, col + len(name))


def test_real_late_local_fixture_is_refused_at_its_read() -> None:
    _assert_late_local(REAL, "lateValue", 6, 13)


@pytest.mark.parametrize("body, name, line, col", REFUSALS)
def test_later_callable_binding_is_not_visible(
    body: str, name: str, line: int, col: int,
) -> None:
    _assert_late_local(HEADER + body, name, line, col)


def test_late_local_is_semantic_even_without_the_support_gate() -> None:
    _assert_late_local(REAL, "lateValue", 6, 13, check_support=False)


def test_direct_analyzer_uses_the_same_declaration_order() -> None:
    tokens = Lexer(REAL, filename="order.pine").tokenize()
    ast = Parser(tokens, source=REAL, filename="order.pine").parse()
    with pytest.raises(CompileError) as caught:
        Analyzer(ast, filename="order.pine").analyze()
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E2075"
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.location.file == "order.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (6, 13)


ACCEPTED = [
    pytest.param(
        "read() =>\n    float available = close - open\n    value = available\n"
        "    value\nout = read()\n", id="earlier-local",
    ),
    pytest.param(
        "available = close - open\nread() => available\nout = read()\n",
        id="earlier-global",
    ),
    pytest.param(
        "read() =>\n    float available = close - open\n"
        "    if close > open\n        value = available\n        value\n"
        "    else\n        available\nout = read()\n",
        id="earlier-enclosing-local",
    ),
    pytest.param(
        "available = close - open\nread() =>\n    value = available\n"
        "    float available = 42.0\n    value\nout = read()\n",
        id="outer-read-before-local-shadow",
    ),
    pytest.param(
        "read(float available) => available\n"
        "available = close - open\nout = read(available)\n",
        id="parameter-before-later-global",
    ),
    pytest.param(
        "method delta(float self) =>\n    nextDelta = self - open\n"
        "    value = nextDelta\n    value\nout = close.delta()\n",
        id="legal-method",
    ),
    pytest.param(
        "read() =>\n    float total = 0.0\n    for i = 0 to 1\n"
        "        float part = close - open\n        total += part + i\n"
        "    for item in array.from(close, open)\n"
        "        float part = item\n        total += part\n"
        "    int turns = 0\n    while turns < 1\n"
        "        float part = close - open\n        total += part\n"
        "        turns += 1\n    total\nout = read()\n",
        id="legal-loop-locals-and-binders",
    ),
    pytest.param(
        "read() =>\n    switch\n        close > open =>\n"
        "            float part = close - open\n            part\n"
        "        =>\n            float part = open - close\n            part\n"
        "out = read()\n", id="legal-switch-arm-locals",
    ),
    pytest.param(
        "pair() => [close, open]\nread() =>\n"
        "    [firstPart, secondPart] = pair()\n"
        "    firstPart + secondPart\nout = read()\n", id="legal-tuple",
    ),
    pytest.param(
        'g(payload) => request.security(syminfo.tickerid, "D", payload[1])\n'
        "later = ta.sma(close, 3)\nout = g(later)\n",
        id="later-global-at-payload-call-site",
    ),
    pytest.param(
        "read(float value = 1.0) => value\nout = read()\n",
        id="literal-parameter-default",
    ),
    pytest.param(
        "type Cell\n    float value = 1.0\ncell = Cell.new()\nout = cell.value\n",
        id="literal-field-default",
    ),
    pytest.param(
        "read() =>\n    float available = close - open\n"
        "    if close > open\n        value = available\n"
        "        float available = 42.0\n        value\n"
        "    else\n        available\nout = read()\n",
        id="enclosing-read-before-nested-shadow",
    ),
    pytest.param(
        "type Cell\n    float value\n"
        "enum Direction\n    up\n"
        "read() =>\n    cell = Cell.new(close)\n"
        "    choice = Direction.up\n"
        "    choice == Direction.up ? cell.value : open\nout = read()\n",
        id="type-enum-and-field-labels",
    ),
]


@pytest.mark.parametrize("body", ACCEPTED)
def test_legal_declaration_order_stays_accepted(body: str) -> None:
    assert "class GeneratedStrategy" in transpile(HEADER + body)


@pytest.mark.parametrize("body", ACCEPTED)
def test_legal_declaration_order_cpp_compiles(body: str) -> None:
    compile_cpp(transpile(HEADER + body), label="legal declaration order")


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            # TradingView accepts this form
            '''//@version=6
strategy("pq1a", currency=currency.NONE, initial_capital=1000000)
if close > open
    float str = close - open
shown = str.tostring(close)
if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = 1)
if bar_index > 6 and bar_index % 7 == 5
    strategy.close("L")
''',
            id="string-namespace",
        ),
        pytest.param(
            # TradingView accepts this form
            '''//@version=6
strategy("pq1b", currency=currency.NONE, initial_capital=1000000)
if close > open
    float color = close - open
shade = color.new(color.red, 50)
if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = 1)
if bar_index > 6 and bar_index % 7 == 5
    strategy.close("L")
''',
            id="color-namespace",
        ),
        pytest.param(
            # TradingView accepts this form
            '''//@version=6
strategy("pq1c", currency=currency.NONE, initial_capital=1000000)
if close > open
    float math = close - open
magnitude = math.abs(close - open)
if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = 1)
if bar_index > 6 and bar_index % 7 == 5
    strategy.close("L")
''',
            id="math-namespace",
        ),
        pytest.param(
            # TradingView accepts this form
            '''//@version=6
strategy("pq1d", currency=currency.NONE, initial_capital=1000000)
shown() =>
    if close > open
        float str = close - open
    str.tostring(close)
out = shown()
if bar_index > 6 and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = 1)
if bar_index > 6 and bar_index % 7 == 5
    strategy.close("L")
''',
            id="callable-namespace",
        ),
    ],
)
def test_namespace_call_after_expired_local_has_no_scope_refusal(
    source: str,
) -> None:
    """TradingView accepts this form; this change adds no refusal for it.

    Any remaining failure is a separate emission gap.
    """
    try:
        transpile(source)
    except CompileError as caught:
        codes = {diagnostic.code for diagnostic in caught.diagnostics}
        assert codes.isdisjoint({"PF-E2054", "PF-E2075"})


def test_corrected_real_fixture_stays_accepted() -> None:
    corrected = REAL.replace(
        "    value = lateValue\n    float lateValue = close - open\n",
        "    float lateValue = close - open\n    value = lateValue\n",
    )
    assert corrected != REAL
    assert "class GeneratedStrategy" in transpile(corrected)


def test_imported_v5_function_order_keeps_existing_lowering() -> None:
    source = HEADER + "import examples/Scope/1 as scope\nout = scope.read()\n"
    library = (
        '//@version=5\nlibrary("Scope")\n'
        "export read() =>\n    float available = close - open\n"
        "    value = available\n    value\n"
    )
    assert "class GeneratedStrategy" in transpile(
        source, libraries={"examples/Scope/1": library},
    )


def test_late_global_retains_its_existing_diagnostic() -> None:
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + "read() => later\nlater = close\nout = read()\n")
    assert caught.value.diagnostics[0].code == "PF-E2053"
    assert caught.value.diagnostics[0].phase is Phase.ANALYZER


def test_nested_callable_declarations_do_not_belong_to_the_outer_callable() -> None:
    source = (
        HEADER + "outer() =>\n    value = privateValue\n"
        "    inner() =>\n        privateValue = close\n        privateValue\n"
        "    value\nout = outer()\n"
    )
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="order.pine")
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E2057"
    assert diagnostic.phase is Phase.ANALYZER
    assert (diagnostic.location.line, diagnostic.location.col) == (5, 5)


def test_late_local_catalog_entry_roundtrips() -> None:
    _assert_late_local(REAL, "lateValue", 6, 13)
    with pytest.raises(CompileError) as caught:
        transpile(REAL)
    diagnostic = caught.value.diagnostics[0]
    entry = diagnostics_catalog()["codes"][diagnostic.code]
    assert entry["severity"] == "error"
    assert entry["area"] == "analysis"
    assert entry["args"] == {"name": {"kind": "identifier"}}
    assert entry["hint"] is None
    assert render_diagnostic(diagnostic.code, diagnostic.args) == (
        diagnostic.message, None,
    )
