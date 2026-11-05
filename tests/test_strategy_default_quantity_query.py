"""The public contract says what PineForge does with strategy.default_entry_qty().

The behaviour itself (the lowering to the host query and the refusals) is pinned
by tests/test_strategy_default_entry_qty.py; this file pins the contract row that
describes it.
"""

from pathlib import Path

CONTRACT = Path(__file__).resolve().parent.parent / "docs" / "PUBLIC_CONTRACT.md"


def test_the_public_contract_row_says_the_form_reads_the_host_query():
    rows = [line for line in CONTRACT.read_text(encoding="utf-8").splitlines()
            if line.startswith("- `strategy.default_entry_qty(fill_price)`")]
    assert len(rows) == 1, rows
    row = rows[0]
    # The row says what the call lowers to and what the paired engine must provide.
    assert "lowers to the engine strategy host's query `default_entry_qty(price)`" in row
    assert "paired engine whose strategy host provides the query" in row
    # The refusals TradingView makes are named with their codes.
    for code in ("PF-E1088", "PF-E2083", "PF-E2086"):
        assert f"`{code}`" in row, code
    # The earlier refusal is described as retired, not as current behaviour.
    assert "`PF-E5084`" in row and "is retired" in row and "no longer emitted" in row
