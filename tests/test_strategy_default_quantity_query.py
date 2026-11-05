"""A missing host quantity query must fail before emitting invalid C++."""

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError

MESSAGE = (
    "TradingView accepts strategy.default_entry_qty(); PineForge does not "
    "support it yet: it requires a default-quantity query on the engine "
    "strategy host.")
CONTRACT = Path(__file__).resolve().parent.parent / "docs" / "PUBLIC_CONTRACT.md"


@pytest.mark.parametrize("argument", ["close", "fill_price=close", "open + 1"])
def test_default_entry_quantity_requires_engine_query(argument):
    source = (
        '//@version=6\nstrategy("Default quantity", '
        'default_qty_type=strategy.percent_of_equity, default_qty_value=10)\n'
        f'quantity = strategy.default_entry_qty({argument})\n'
        'strategy.entry("Long", strategy.long, qty=quantity)\n'
    )
    with pytest.raises(CompileError, match="engine strategy host") as caught:
        transpile(source)
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E5084"
    assert diagnostic.location.line == 3
    # The refusal names the construct and says that TradingView accepts it.
    assert diagnostic.message == MESSAGE
    assert diagnostic.hint == (
        "Use the default quantity of strategy.entry() by omitting qty.")
    # The supported alternative still delegates default sizing to the host.
    control = transpile(
        '//@version=6\nstrategy("Default quantity", '
        'default_qty_type=strategy.percent_of_equity, default_qty_value=10)\n'
        'strategy.entry("Long", strategy.long)\n')
    assert 'strategy_entry(std::string("Long"), true, na<double>()' in control


def test_the_public_contract_row_says_the_form_is_valid_and_quotes_the_message():
    rows = [line for line in CONTRACT.read_text(encoding="utf-8").splitlines()
            if "`PF-E5084`" in line]
    assert len(rows) == 1, rows
    row = rows[0]
    # The row says the form is valid in TradingView, names the refusal and what
    # it waits for, and quotes the refusal's message word for word.
    assert "strategy.default_entry_qty()` is valid in TradingView" in row
    assert "PineForge refuses it with `PF-E5084`" in row
    assert "until the engine strategy host exposes a default-quantity query" in row
    assert f'"{MESSAGE}"' in row
