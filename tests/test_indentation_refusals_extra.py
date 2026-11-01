"""Additional operand and block indentation errors retain source positions."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase


HEADER = (
    '//@version=6\n'
    'strategy("Indentation boundaries", pyramiding = 0, calc_on_every_tick = false, '
    'calc_on_order_fills = false, process_orders_on_close = false, '
    'default_qty_type = strategy.fixed, default_qty_value = 1, initial_capital = 1000000)\n'
)
TAIL = """float quantity = 1.0 + math.abs(nz(signal, -0.75)) % 7.0
var string active = ""
var string value = ""
if bar_index % 20 == 5 and strategy.position_size == 0
    value := str.tostring(signal)
    active := "order-" + value
    strategy.entry(active, strategy.long, qty = quantity)
if bar_index % 20 == 15 and strategy.position_size != 0
    strategy.close(active, comment = value)
"""


@pytest.mark.parametrize(
    "body,line,col",
    [
        pytest.param(
            "float leaf = close +\n        0.5\n"
            "float signal = float(leaf) + (0.11)\n",
            3, 21,  # The plus is at column 20, so the line ends at column 21.
            id="close-operand",
        ),
        pytest.param(
            "float leaf = open +\n        0.5\n"
            "float signal = float(leaf) + (0.22)\n",
            3, 20,  # The four-letter source puts the line end at column 20.
            id="open-operand",
        ),
        pytest.param(
            "float leaf = high +\n        0.5\n"
            "float signal = float(leaf) + (0.33)\n",
            3, 20,  # high has the same width as open, ending the line at column 20.
            id="high-operand",
        ),
        pytest.param(
            "float leaf = low +\n        0.5\n"
            "float signal = float(leaf) + (0.44)\n",
            3, 19,  # The three-letter source puts the line end at column 19.
            id="low-operand",
        ),
        pytest.param(
            "float leaf = hl2 +\n        0.5\n"
            "float signal = float(leaf) + (0.55)\n",
            3, 19,  # hl2 has the same width as low, ending the line at column 19.
            id="midpoint-operand",
        ),
    ],
)
def test_eight_space_operand_is_located(body: str, line: int, col: int) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + body + TAIL, filename="indentation.pine")
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.LEXER
    assert diagnostic.code == "PF-E0038"
    assert diagnostic.args == {}
    assert diagnostic.message == (
        "Line continuation outside parentheses or brackets requires "
        "space indentation that is not a multiple of four."
    )
    assert diagnostic.location.file == "indentation.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)


def test_spaces_before_tab_are_located() -> None:
    source = (
        HEADER + "float leaf = close\n"
        "if int(bar_index / 20) % 2 == 0\n"
        "    \tleaf := leaf + 1.0\n"
        "float signal = float(leaf) + (0.11)\n" + TAIL
    )
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="indentation.pine")
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.LEXER
    assert diagnostic.code == "PF-E0042"
    assert diagnostic.args == {}
    assert diagnostic.message == "Indentation cannot contain spaces followed by a tab."
    assert diagnostic.location.file == "indentation.pine"
    # Four spaces and one tab put the first statement character at 5:6.
    assert (diagnostic.location.line, diagnostic.location.col) == (5, 6)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "float leaf = close +\n 0.5\n",
            id="one-space-operand",  # One space still joins the operand to close.
        ),
        pytest.param(
            "float leaf = close +\n  0.5\n",
            id="two-space-operand",  # Two spaces still join the operand to close.
        ),
        pytest.param(
            "float leaf = close +\n   0.5\n",
            id="three-space-operand",  # Three spaces still join the operand to close.
        ),
        pytest.param(
            "float leaf = close\nif close > open\n\tleaf += 1.0\n",
            id="tab-block",  # A single tab still opens one block level.
        ),
        pytest.param(
            "float leaf = close\nif close > open\n    leaf += 1.0\n",
            id="space-block",  # Four spaces still open one block level.
        ),
        pytest.param(
            "float leaf = close\nif close > open\n\tleaf += 1.0\n    leaf += 2.0\n",
            id="equivalent-block-widths",  # Separate pure tab and pure space lines share a level.
        ),
        pytest.param(
            "float leaf = close\nif close > open\n\t    leaf += 1.0\n",
            id="tab-before-spaces",  # The reverse mixing order keeps its existing treatment.
        ),
        pytest.param(
            "float leaf = (close +\n        0.5)\n",
            id="parenthesized-operand",  # Eight spaces are cosmetic inside parentheses.
        ),
        pytest.param(
            "float leaf = math.max(close,\n    \topen)\n",
            id="mixed-call-whitespace",  # Indentation inside an open call stays cosmetic.
        ),
        pytest.param(
            "choose() =>\n    close +\n        0.5\nfloat leaf = choose()\n",
            id="block-operand",  # The existing callable block wrap still joins its operand.
        ),
        pytest.param(
            "choose() => close +\n        0.5\nfloat leaf = choose()\n",
            id="inline-callable-operand",  # An inline arrow retains its existing continuation.
        ),
        pytest.param(
            "float leaf = close +\n            0.5\n",
            id="other-space-width",  # Other existing widths remain outside the added restriction.
        ),
        pytest.param(
            "float leaf = close\n        + 0.5\n",
            id="leading-operator",  # An operator on the next line retains its existing join.
        ),
    ],
)
def test_other_indentation_shapes_stay_accepted(body: str) -> None:
    assert transpile(HEADER + body + "float signal = float(leaf) + (0.11)\n" + TAIL)
