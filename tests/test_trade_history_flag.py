"""Codegen's closed-trade history policy flag and host export."""

from __future__ import annotations

from pineforge_codegen import transpile


_PRELUDE = "//@version=6\nstrategy(\"trade history\")\n"


def _constructor(cpp: str) -> str:
    start = cpp.index("    explicit GeneratedStrategy()")
    end = cpp.index("    void set_strategy_override", start)
    return cpp[start:end]


def test_scripts_without_closed_trade_rows_opt_into_streaming() -> None:
    cpp = transpile(_PRELUDE + "open_profit = strategy.opentrades.profit(0)\n")
    constructor = _constructor(cpp)

    assert constructor.count("set_trade_history_streamable(true);") == 1
    assert "#if defined(PINEFORGE_HAS_TRADE_HISTORY_POLICY_V1)" in constructor
    assert "strategy_reads_trade_history" in cpp
    assert "int strategy_reads_trade_history(void) {\n        return 0;\n" in cpp


def test_closed_trade_accessor_keeps_rows_and_sets_export() -> None:
    cpp = transpile(_PRELUDE + "closed_profit = strategy.closedtrades.profit(0)\n")
    constructor = _constructor(cpp)

    assert "set_trade_history_streamable(true);" not in constructor
    assert "int strategy_reads_trade_history(void) {\n        return 1;\n" in cpp


def test_closed_trade_count_does_not_disable_streaming() -> None:
    cpp = transpile(_PRELUDE + "closed_count = strategy.closedtrades\n")
    constructor = _constructor(cpp)

    assert constructor.count("set_trade_history_streamable(true);") == 1
    assert "int strategy_reads_trade_history(void) {\n        return 0;\n" in cpp
