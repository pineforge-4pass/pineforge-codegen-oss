"""Plain Pine v6 declarations need a type for a bare na initializer."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase


HEADER = (
    '//@version=6\n'
    'strategy("Missing value declaration", pyramiding = 0, calc_on_every_tick = false, '
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
            "x = na\nfloat leaf = nz(x + open, -1.0)\n"
            "float signal = float(leaf) + (0.22)\n",
            3, 1,  # The untyped declaration starts at column 1 of the first body line.
            id="open-source",
        ),
        pytest.param(
            "x = na\nfloat leaf = nz(x + hl2, -1.0)\n"
            "float signal = float(leaf) + (0.55)\n",
            3, 1,  # Changing the later source to hl2 leaves the declaration at 3:1.
            id="midpoint-source",
        ),
        pytest.param(
            "x = na\nfloat leaf = nz(x + low, -1.0)\n"
            "float signal = float(leaf) + (0.44)\n",
            3, 1,  # Changing the later source to low leaves the declaration at 3:1.
            id="low-source",
        ),
    ],
)
def test_plain_na_declaration_is_located(body: str, line: int, col: int) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + body + TAIL, filename="missing_value.pine")
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == "PF-E2061"
    assert diagnostic.args == {}
    assert diagnostic.message == (
        "Value with NA type cannot be assigned to a variable that was "
        "defined without type keyword."
    )
    assert diagnostic.location.file == "missing_value.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            "float x = na\nplot(nz(x))\n",
            id="typed-float",  # An explicit float type gives na its type.
        ),
        pytest.param(
            "int x = na\nplot(nz(x))\n",
            id="typed-int",  # An explicit int type gives na its type.
        ),
        pytest.param(
            "string s = na\nplot(na(s) ? 0 : str.length(s))\n",
            id="typed-string",  # An explicit string type retains its nullable value.
        ),
        pytest.param(
            "var float x = na\nplot(nz(x))\n",
            id="persistent-float",  # A typed persistent initializer is unchanged.
        ),
        pytest.param(
            "varip float x = na\nplot(nz(x))\n",
            id="intrabar-float",  # A typed intrabar initializer is unchanged.
        ),
        pytest.param(
            "float x = close\nx := na\nplot(nz(x))\n",
            id="reassignment",  # Reassignment uses an existing float binding.
        ),
        pytest.param(
            "var x = na\nplot(nz(x + open, -1.0))\n",
            id="untyped-persistent",  # Untyped persistent declarations retain their lowering.
        ),
        pytest.param(
            "varip x = na\nplot(nz(x + open, -1.0))\n",
            id="untyped-intrabar",  # Untyped intrabar declarations retain their lowering.
        ),
        pytest.param(
            "x = 1.0\nplot(x)\n",
            id="inferred-float",  # A float literal supplies its own inferred type.
        ),
        pytest.param(
            "x = float(na)\nplot(nz(x))\n",
            id="explicit-cast",  # A cast is an expression rather than a bare na initializer.
        ),
        pytest.param(
            "x = (na)\nplot(nz(x + open, -1.0))\n",
            id="parenthesized-value",  # Parentheses remain outside the bare-token rule.
        ),
        pytest.param(
            "pair() => [na, 1.0]\n[x, y] = pair()\nplot(nz(x) + y)\n",
            id="tuple-binding",  # Tuple bindings do not become plain na declarations.
        ),
        pytest.param(
            "const x = na\nplot(nz(x + open, -1.0))\n",
            id="qualified-binding",  # A qualified declaration keeps its existing lowering.
        ),
    ],
)
def test_other_declaration_shapes_stay_accepted(body: str) -> None:
    assert transpile(HEADER + body)


def test_imported_v5_na_declaration_stays_accepted() -> None:
    source = (
        HEADER + "import examples/MissingValues/1 as values\n"
        "plot(values.choose(open))\n"
    )
    library = (
        '//@version=5\nlibrary("MissingValues")\n'
        "export choose(float price) =>\n"
        "    x = na\n"
        "    nz(x + price, -1.0)\n"
    )
    # The imported definition keeps v5 rules inside the v6 caller.
    assert transpile(source, libraries={"examples/MissingValues/1": library})
