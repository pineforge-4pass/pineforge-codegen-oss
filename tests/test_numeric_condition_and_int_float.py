"""Explicit integer declarations and numeric if conditions have located errors."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.diagnostic_codes import render_diagnostic
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._compile import compile_cpp


_REFUSED = [
    pytest.param(
        """//@version=6
strategy("Float literal initializer", overlay=true, initial_capital=1000000)
int x = 1.5
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=x)
if bar_index % 20 == 15
    strategy.close("L")
""",
        "PF-E2045", (3, 1),
        # The explicit int declaration at line 3 directly initializes from a float literal.
        id="float_literal_initializer",
    ),
    pytest.param(
        """//@version=6
strategy("Numeric if condition", overlay=true, initial_capital=1000000)
q = 1.0
if close
    q := 2.0
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty=q)
if bar_index % 20 == 15
    strategy.close("L")
""",
        "PF-E2046", (4, 4),
        # close has float type and begins at line 4, column 4.
        id="numeric_if_condition",
    ),
    pytest.param(
        '//@version=6\nstrategy("Integral float spelling")\nint x = 2.0\nplot(x)\n',
        "PF-E2045", (3, 1),
        # A decimal point makes 2.0 a float literal even though its value is integral.
        id="integral_float_literal",
    ),
    pytest.param(
        '//@version=6\nstrategy("Local float initializer")\nf() =>\n    int x = 1.5\n    x\nplot(f())\n',
        "PF-E2045", (4, 5),
        # The local explicit int declaration directly initializes from the float literal 1.5.
        id="local_float_initializer",
    ),
    pytest.param(
        '//@version=6\nstrategy("Persistent float initializer")\nvar int x = 1.5\nplot(x)\n',
        "PF-E2045", (3, 1),
        # var changes persistence, while its explicit int initializer remains a float literal.
        id="persistent_float_initializer",
    ),
    pytest.param(
        '//@version=6\nstrategy("Integer if condition")\nif bar_index\n    strategy.entry("L", strategy.long)\n',
        "PF-E2046", (3, 4),
        # bar_index has int type and begins after the three characters "if ".
        id="integer_if_condition",
    ),
    pytest.param(
        '//@version=6\nstrategy("Typed numeric parameter")\nf(float x) =>\n    if x\n        1\n    else\n        2\nplot(f(close))\n',
        "PF-E2046", (4, 8),
        # The typed float parameter x is the numeric condition at column 8.
        id="numeric_parameter_condition",
    ),
    pytest.param(
        '//@version=6\nstrategy("Numeric conditional expression")\nx = if close\n    1\nelse\n    2\nplot(x)\n',
        "PF-E2046", (3, 8),
        # An if expression uses the same condition rule; close starts at column 8.
        id="numeric_if_expression",
    ),
]


_ACCEPTED = [
    pytest.param(
        '//@version=6\nstrategy("Integer initializer")\nint x = 2\nplot(x)\n',
        # The integer literal 2 agrees with the explicit int type.
        id="integer_initializer",
    ),
    pytest.param(
        '//@version=6\nstrategy("Float initializer")\nfloat x = 1.5\nplot(x)\n',
        # The float literal 1.5 agrees with the explicit float type.
        id="float_initializer",
    ),
    pytest.param(
        '//@version=6\nstrategy("Rounded initializer")\nint x = math.round(1.5)\nplot(x)\n',
        # A call is outside the measured literal-initializer restriction.
        id="rounded_initializer",
    ),
    pytest.param(
        '//@version=6\nstrategy("Computed initializer")\nint x = 1.0 * 2\nplot(x)\n',
        # Computed float expressions remain outside the literal-only restriction.
        id="computed_initializer",
    ),
    pytest.param(
        '//@version=6\nstrategy("Bound initializer")\nsource = 1.5\nint x = source\nplot(x)\n',
        # A named float expression is unmeasured and keeps its existing conversion.
        id="bound_float_initializer",
    ),
    pytest.param(
        '//@version=6\nstrategy("Signed initializer")\nint x = -1.5\nplot(x)\n',
        # A unary expression is unmeasured and keeps its existing lowering.
        id="signed_initializer",
    ),
    pytest.param(
        '//@version=6\nstrategy("Unavailable initializer")\nint x = na\nplot(x)\n',
        # na is not a float literal and keeps the existing int-na lowering.
        id="na_initializer",
    ),
    pytest.param(
        '//@version=6\nstrategy("Comparison condition")\nif close > open\n    strategy.entry("L", strategy.long)\n',
        # The comparison returns bool even though both operands are floats.
        id="comparison_condition",
    ),
    pytest.param(
        '//@version=6\nstrategy("Explicit boolean condition")\nif bool(close)\n    strategy.entry("L", strategy.long)\n',
        # bool(close) explicitly supplies a bool condition.
        id="explicit_bool_condition",
    ),
    pytest.param(
        '//@version=6\nstrategy("Boolean binding condition")\nbool ready = close > open\nif ready\n    strategy.entry("L", strategy.long)\n',
        # The declared bool binding supplies a bool condition.
        id="bool_binding_condition",
    ),
    pytest.param(
        '//@version=6\nstrategy("Boolean parameter condition")\nf(bool ready) =>\n    if ready\n        1\n    else\n        2\nplot(f(close > open))\n',
        # A bool parameter stays bool inside a function's conditional expression.
        id="bool_parameter_condition",
    ),
    pytest.param(
        '//@version=6\nstrategy("Boolean array condition")\nflags = array.from(true, close > open)\nfor [i, ready] in flags\n    if ready\n        strategy.entry("L", strategy.long)\n',
        # The destructured value has the bool element type, despite its legacy numeric fallback.
        id="bool_array_condition",
    ),
    pytest.param(
        '//@version=6\nstrategy("Boolean array alias")\nflags = array.from(true, close > open)\nfor [i, ready] in flags\n    alias = ready\n    if alias\n        strategy.entry("L", strategy.long)\n',
        # Assigning the bool element to an untyped local preserves its condition type.
        id="bool_array_alias_condition",
    ),
    pytest.param(
        '//@version=6\nstrategy("Boolean array selection")\nflags = array.from(true, close > open)\nfor [i, ready] in flags\n    alias = bar_index % 2 == 0 ? ready : false\n    if alias\n        strategy.entry("L", strategy.long)\n',
        # Both ternary arms have bool type, so the selected local is a bool condition.
        id="bool_array_selected_condition",
    ),
    pytest.param(
        '//@version=6\nstrategy("Unavailable condition")\nif na\n    strategy.entry("L", strategy.long)\n',
        # A condition of na type is unmeasured and retains its existing lowering.
        id="na_condition",
    ),
    pytest.param(
        '//@version=6\nstrategy("Numeric while condition")\nwhile close\n    break\n',
        # Numeric while is unmeasured; the existing truthiness lowering stays accepted.
        id="numeric_while_condition",
    ),
]


@pytest.mark.parametrize("source, code, position", _REFUSED)
def test_type_errors_are_coded_and_located(
    source: str, code: str, position: tuple[int, int],
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(source)
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == code
    assert (diagnostic.location.line, diagnostic.location.col) == position
    assert render_diagnostic(code, diagnostic.args) == (
        diagnostic.message, diagnostic.hint,
    )


@pytest.mark.parametrize("source", _ACCEPTED)
def test_other_initializers_and_conditions_translate(source: str) -> None:
    assert transpile(source)


@pytest.mark.parametrize("source", _ACCEPTED)
def test_other_initializers_and_conditions_compile(source: str) -> None:
    compile_cpp(transpile(source), label="numeric_condition_int_float")


_NUMERIC_LIBRARY = """//@version=5
library("Numeric conditions")
export choose(float value) =>
    if value
        1
    else
        2
"""
_LIBRARY_CALL = """//@version=6
strategy("Imported numeric condition")
import local/Numbers/1 as numbers
q = numbers.choose(close)
plot(q)
"""


def test_older_library_condition_keeps_its_lowering() -> None:
    # The v5 body retains numeric truthiness when called from a v6 script.
    cpp = transpile(_LIBRARY_CALL, libraries={"local/Numbers/1": _NUMERIC_LIBRARY})
    assert cpp
    compile_cpp(cpp, label="library_numeric_condition")


def test_library_condition_rules_end_at_the_callable_boundary() -> None:
    source = _LIBRARY_CALL + 'if close\n    strategy.entry("L", strategy.long)\n'
    with pytest.raises(CompileError) as caught:
        transpile(source, libraries={"local/Numbers/1": _NUMERIC_LIBRARY})
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E2046"  # The importing script still requires a bool condition.
    assert (diagnostic.location.line, diagnostic.location.col) == (
        6, 4,
    )  # The script's close follows "if " at line 6, column 4.
