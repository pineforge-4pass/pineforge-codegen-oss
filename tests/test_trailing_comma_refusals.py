"""Statement commas respect indentation; call commas require another argument."""

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
            "a = close,\n    b = open\nsignalValue = a - b\n",
            "PF-E0040",
            "A statement cannot end with a trailing comma.",
            6, 5,  # One comma still precedes the deeper statement at line 6, column 5.
            id="one-comma-deeper-statement",
        ),
        pytest.param(
            "measure() =>\n    int firstMark = 0,\n"
            "        int secondMark = 0\n    firstMark\n"
            "signalValue = measure()\n",
            "PF-E0040",
            "A statement cannot end with a trailing comma.",
            7, 9,  # Eight spaces place the deeper typed declaration at line 7, column 9.
            id="deeper-function-declaration",
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
    "body,control",
    [
        pytest.param(
            "measure() =>\n"
            "    int firstMark = 0, int secondMark = firstMark + 1,\n"
            "    int thirdMark = secondMark + 2\n"
            "    firstMark + secondMark + thirdMark\n"
            "signalValue = measure()\n",
            "measure() =>\n"
            "    int firstMark = 0, int secondMark = firstMark + 1 \n"
            "    int thirdMark = secondMark + 2\n"
            "    firstMark + secondMark + thirdMark\n"
            "signalValue = measure()\n",
            # The control keeps the same line and column layout (the emitted code carries source
            # positions) and only turns the line-ending comma into a space.
            # Both layouts declare 0, 1 and 3 in order, then return their sum, 4.
            id="function-declaration-list",
        ),
        pytest.param(
            "int firstMark = 0, int secondMark = firstMark + 1,\n"
            "int thirdMark = secondMark + 2\n"
            "signalValue = firstMark + secondMark + thirdMark\n",
            "int firstMark = 0, int secondMark = firstMark + 1 \n"
            "int thirdMark = secondMark + 2\n"
            "signalValue = firstMark + secondMark + thirdMark\n",
            # Same line and column layout as the body (only the line-ending comma is a space).
            # Zero indentation keeps all three declarations global and their sum is 4.
            id="top-level-declaration-list",
        ),
        pytest.param(
            "float signalValue = close\nif flip\n"
            "    int firstMark = 0, int secondMark = firstMark + 1,\n"
            "    int thirdMark = secondMark + 2\n"
            "    signalValue := firstMark + secondMark + thirdMark\n",
            "float signalValue = close\nif flip\n"
            "    int firstMark = 0, int secondMark = firstMark + 1 \n"
            "    int thirdMark = secondMark + 2\n"
            "    signalValue := firstMark + secondMark + thirdMark\n",
            # Same line and column layout as the body (only the line-ending comma is a space).
            # The branch assigns 0+1+3=4; the declaration boundary adds no statement.
            id="if-declaration-list",
        ),
        pytest.param(
            "float signalValue = close\nfor stepMark = 0 to 1\n"
            "    signalValue += stepMark,\n    signalValue += 2\n",
            "float signalValue = close\nfor stepMark = 0 to 1\n"
            "    signalValue += stepMark\n    signalValue += 2\n",
            # Both loop statements run for 0 and 1, adding 0+2+1+2=5.
            id="for-same-indent",
        ),
        pytest.param(
            "float signalValue = close\nint stepMark = 0\nwhile stepMark < 2\n"
            "    signalValue += stepMark,\n    stepMark += 1\n",
            "float signalValue = close\nint stepMark = 0\nwhile stepMark < 2\n"
            "    signalValue += stepMark\n    stepMark += 1\n",
            # Each iteration advances the counter, so the two additions total 0+1=1.
            id="while-same-indent",
        ),
        pytest.param(
            "signalValue = switch\n    flip =>\n"
            "        int firstMark = 0, int secondMark = firstMark + 1,\n"
            "        int thirdMark = secondMark + 2\n"
            "        firstMark + secondMark + thirdMark\n    => close\n",
            "signalValue = switch\n    flip =>\n"
            "        int firstMark = 0, int secondMark = firstMark + 1 \n"
            "        int thirdMark = secondMark + 2\n"
            "        firstMark + secondMark + thirdMark\n    => close\n",
            # Same line and column layout as the body (only the line-ending comma is a space).
            # The indented arm returns 0+1+3=4; the default arm still returns close.
            id="switch-declaration-list",
        ),
        pytest.param(
            "float signalValue = close\nif flip\n    signalValue := open,\n"
            "signalValue += 2\n",
            "float signalValue = close\nif flip\n    signalValue := open\n"
            "signalValue += 2\n",
            # The dedent keeps the addition outside the branch on every bar.
            id="before-dedent",
        ),
        pytest.param(
            "measure() =>\n    int firstMark = 2\n    firstMark + 1,\n"
            "signalValue = measure()\n",
            "measure() =>\n    int firstMark = 2\n    firstMark + 1\n"
            "signalValue = measure()\n",
            # The dedent keeps the call global and the callable's return value is 2+1=3.
            id="function-before-dedent",
        ),
        pytest.param(
            "float signalValue = close\nif flip\n    signalValue := open,\n"
            "else\n    signalValue := high\n",
            "float signalValue = close\nif flip\n    signalValue := open\n"
            "else\n    signalValue := high\n",
            # The dedent attaches else to if, selecting open or high exactly once.
            id="before-else",
        ),
        pytest.param(
            "signalValue = close,\n// The next declaration remains at zero indentation.\n"
            "\notherMark = open\nsignalValue += otherMark\n",
            "signalValue = close\n// The next declaration remains at zero indentation.\n"
            "\notherMark = open\nsignalValue += otherMark\n",
            # Blank and comment lines add no statements; the final value is close+open.
            id="same-indent-after-comments",
        ),
        pytest.param(
            "signalValue = close,\n+ open\n",
            "signalValue = close\n+ open\n",
            # The operator-first join produces the single expression close+open.
            id="same-indent-operator",
        ),
        pytest.param(
            "float signalValue = close\nif flip\n"
            "    signalValue := math.max(\n        close,\n        open),\n"
            "    signalValue += 1\n",
            "float signalValue = close\nif flip\n"
            "    signalValue := math.max(\n        close,\n        open)\n"
            "    signalValue += 1\n",
            # Four spaces are shallower than the comma's eight, keeping both branch statements.
            id="multiline-call-before-shallower-line",
        ),
        pytest.param(
            "measure() =>\n\tint firstMark = 0, int secondMark = firstMark + 1,\n"
            "\tint thirdMark = secondMark + 2\n"
            "\tfirstMark + secondMark + thirdMark\nsignalValue = measure()\n",
            "measure() =>\n\tint firstMark = 0, int secondMark = firstMark + 1 \n"
            "\tint thirdMark = secondMark + 2\n"
            "\tfirstMark + secondMark + thirdMark\nsignalValue = measure()\n",
            # Same line and column layout as the body (only the line-ending comma is a space).
            # One tab keeps all three declarations in the callable, whose tail is 0+1+3=4.
            id="tabbed-function-declaration-list",
        ),
    ],
)
def test_statement_comma_matches_separate_statements(body: str, control: str) -> None:
    assert transpile(HEADER + body + TAIL) == transpile(HEADER + control + TAIL)


@pytest.mark.parametrize(
    "body,control",
    [
        pytest.param(
            "signalValue = close,",
            "signalValue = close",
            # EOF adds no statement after the global declaration.
            id="top-level-eof",
        ),
        pytest.param(
            "signalValue = close,\n",
            "signalValue = close\n",
            # A final newline adds no statement after the global declaration.
            id="top-level-final-newline",
        ),
        pytest.param(
            "float signalValue = close\nif flip\n    signalValue := open,",
            "float signalValue = close\nif flip\n    signalValue := open",
            # EOF closes the branch after its single assignment, without a final newline.
            id="block-eof",
        ),
        pytest.param(
            "if flip\n    strategy.close(\"L\"),\n",
            "if flip\n    strategy.close(\"L\")\n",
            # The final block still holds one conditional close call.
            id="block-final-newline",
        ),
    ],
)
def test_terminal_statement_comma_matches_no_comma(body: str, control: str) -> None:
    assert transpile(HEADER + body) == transpile(HEADER + control)


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
