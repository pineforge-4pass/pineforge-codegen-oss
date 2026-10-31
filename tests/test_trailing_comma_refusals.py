"""Statement and call commas require another item in their own context."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase


HEADER = (
    '//@version=6\n'
    'strategy("Comma boundaries", overlay = true, initial_capital = 1000000, '
    'pyramiding = 2, process_orders_on_close = false)\n'
    'cycleIndex = int(math.floor(bar_index / 20))\n'
    'flip = cycleIndex % 2 == 0\n'
)
TAIL = """tradeQty = na(signalValue) ? 0.75 : (signalValue > 0 ? 1.25 : signalValue < 0 ? 2.5 : 3.75) + math.abs(signalValue) / (1.0 + math.abs(signalValue))
if bar_index % 20 == 5
    strategy.entry("L", strategy.long, qty = tradeQty)
if bar_index % 20 == 15
    strategy.close("L", comment = "scheduled")
"""


@pytest.mark.parametrize(
    "body,code,message,line,col",
    [
        pytest.param(
            "a = close,\n    b = open,\nsignalValue = a - b\n",
            "PF-E0040",
            "A statement cannot end with a trailing comma.",
            6, 5,  # Four spaces put the next statement's b at line 6, column 5.
            id="continued-statement",
        ),
        pytest.param(
            "signalValue = math.max(close, open,),\n",
            "PF-E0041",
            "A function call cannot end with a trailing comma.",
            5, 36,  # The call's closing parenthesis follows the comma at column 35.
            id="call-before-statement-comma",
        ),
    ],
)
def test_trailing_comma_is_a_located_syntax_error(
    body: str, code: str, message: str, line: int, col: int,
) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + body + TAIL, filename="commas.pine")
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.PARSER
    assert diagnostic.code == code
    assert diagnostic.args == {}
    assert diagnostic.message == message
    assert diagnostic.location.file == "commas.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "signalValue = math.max(close, open)\n",
            id="ordinary-call",  # Both arguments precede the closing parenthesis.
        ),
        pytest.param(
            "signalValue = math.max(\n    close,\n    open)\n",
            id="multiline-call",  # A comma between lines remains inside the open call.
        ),
        pytest.param(
            "a = close, b = open\nsignalValue = a - b\n",
            id="same-line-declarations",  # Both declarations share a physical line.
        ),
        pytest.param(
            "float signalValue = close\nsignalValue := open, signalValue += 1.0\n",
            id="same-line-assignments",  # Both assignments share a physical line.
        ),
        pytest.param(
            "values = array.from(close, open)\nsignalValue = array.get(values, 0)\n",
            id="array-values",  # Array construction retains both arguments.
        ),
        pytest.param(
            "pair() => [close, open]\n[a, b] = pair()\nsignalValue = a - b\n",
            id="bracket-literal",  # Literal elements are parsed by the bracket grammar.
        ),
        pytest.param(
            "pair() => [close, open,]\n[a, b] = pair()\nsignalValue = a - b\n",
            id="bracket-trailing-comma",  # A literal's trailing comma keeps its old treatment.
        ),
        pytest.param(
            "choose(float price) => price + 1.0, price * 2.0,\n"
            "    price * 3.0\nsignalValue = choose(close)\n",
            id="callable-comma-continuation",  # The callable still returns its last expression.
        ),
        pytest.param(
            "choose() => a = close,\n    b = open, a - b\n"
            "signalValue = choose()\n",
            id="callable-declaration-continuation",  # Both declarations stay inside the callable.
        ),
        pytest.param(
            'signalValue = str.length("text,)") + close\n',
            id="commas-in-string",  # Comma text inside a string is not punctuation.
        ),
    ],
)
def test_other_comma_shapes_stay_accepted(body: str) -> None:
    assert transpile(HEADER + body + TAIL)
