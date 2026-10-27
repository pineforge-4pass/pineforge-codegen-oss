"""Codegen's closed-trade history policy flag and host export."""

from __future__ import annotations

import re

import pytest

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


# The export is 1 exactly when the emitted code reads a closed-trade row by
# index, a strategy.closedtrades.<accessor>(index) call. The engine may retire
# rows below the published base only for a script that exports 0, so a wrong 0
# loses rows the script reads. The tests below pin the export for each shape
# that reaches a row and for the shapes that must keep streaming.

_EXPORT = re.compile(
    r"int strategy_reads_trade_history\(void\) \{\n\s+return (\d+);\n\s+\}"
)

# Every accessor strategy.closedtrades takes (the Pine v6 inventory). The last
# test fails when the accessor tables gain a name this list lacks.
_CLOSED_TRADE_ACCESSORS = (
    "commission", "entry_bar_index", "entry_comment", "entry_id", "entry_price",
    "entry_time", "exit_bar_index", "exit_comment", "exit_id", "exit_price",
    "exit_time", "max_drawdown", "max_drawdown_percent", "max_runup",
    "max_runup_percent", "profit", "profit_percent", "size",
)


def _exported_reads(cpp: str) -> int:
    found = _EXPORT.findall(cpp)
    assert len(found) == 1, f"expected one strategy_reads_trade_history export, got {found!r}"
    return int(found[0])


def _assert_export(cpp: str, expected: int) -> None:
    """The export, the constructor's policy call and the emitted row reads agree."""
    assert _exported_reads(cpp) == expected
    # A reader keeps its rows and calls no policy; any other script streams once.
    policy_calls = _constructor(cpp).count("set_trade_history_streamable(true);")
    assert policy_calls == (0 if expected else 1)
    # A row read is emitted as closed_trade_<accessor>(index). The export follows
    # the emitted code, and the fixture reads (or avoids) what its test claims.
    assert ("closed_trade_" in cpp) == bool(expected)


def test_newest_closed_trade_read_by_count_keeps_rows() -> None:
    cpp = transpile(
        _PRELUDE
        + "if strategy.closedtrades > 0\n"
        + "    last_profit = strategy.closedtrades.profit(strategy.closedtrades - 1)\n"
        + "plot(close)\n"
    )

    assert "closed_trade_profit(" in cpp
    # The row at closedtrades - 1 is read by index, so rows must stay resident.
    _assert_export(cpp, 1)


def test_closed_trade_loop_over_a_runtime_bound_keeps_rows() -> None:
    cpp = transpile(
        _PRELUDE
        + "total = 0.0\n"
        + "if strategy.closedtrades > 0\n"
        + "    for i = 0 to strategy.closedtrades - 1\n"
        + "        total += strategy.closedtrades.profit(i)\n"
        + "plot(total)\n"
    )

    assert "closed_trade_profit(i)" in cpp
    # Each iteration reads row i of 0..closedtrades - 1, the oldest rows included.
    _assert_export(cpp, 1)


def test_closed_trade_read_of_a_constant_old_index_keeps_rows() -> None:
    cpp = transpile(_PRELUDE + "first_entry = strategy.closedtrades.entry_price(0)\n")

    assert "closed_trade_entry_price(0)" in cpp
    # Row 0 is the oldest row, the first one retirement would drop.
    _assert_export(cpp, 1)


def test_closed_trade_read_inside_a_called_function_keeps_rows() -> None:
    cpp = transpile(
        _PRELUDE
        + "profit_at(int idx) =>\n"
        + "    strategy.closedtrades.profit(idx)\n"
        + "read_back = profit_at(0)\n"
    )

    assert "closed_trade_profit(" in cpp
    # The call makes the function emitted, and its accessor call reads a row.
    _assert_export(cpp, 1)


def test_closed_trade_read_inside_a_function_nothing_calls_is_not_emitted() -> None:
    cpp = transpile(
        _PRELUDE
        + "unused_profit(int idx) =>\n"
        + "    strategy.closedtrades.profit(idx)\n"
        + "price = close\n"
    )

    # A function no call names is not emitted, so its accessor call is not in
    # the C++: no row is read and the export is 0.
    _assert_export(cpp, 0)


def test_position_size_and_equity_reads_keep_streaming() -> None:
    cpp = transpile(
        _PRELUDE
        + "position_now = strategy.position_size\n"
        + "equity_now = strategy.equity\n"
        + "if position_now == 0 and close > open\n"
        + '    strategy.entry("L", strategy.long)\n'
        + "if position_now > 0 and equity_now > strategy.initial_capital\n"
        + '    strategy.close("L")\n'
        + "plot(equity_now)\n"
    )

    assert "signed_position_size()" in cpp
    # Position and equity are engine state, not closed-trade rows.
    _assert_export(cpp, 0)


def test_closed_trade_count_and_its_history_keep_streaming() -> None:
    cpp = transpile(
        _PRELUDE
        + "has_closed = strategy.closedtrades > 0\n"
        + "count_before = strategy.closedtrades[1]\n"
    )

    assert "((int)trades_.size())" in cpp
    assert "_strat_closedtrades" in cpp
    # The count and its history are the trade book's size, never a row by index.
    _assert_export(cpp, 0)


def test_trade_aggregates_keep_streaming() -> None:
    cpp = transpile(
        _PRELUDE
        + "win_count = strategy.wintrades\n"
        + "loss_count = strategy.losstrades\n"
        + "net = strategy.netprofit\n"
        + "gross = strategy.grossprofit\n"
    )

    for engine_total in (
        "count_wintrades()", "count_losstrades()", "net_profit()", "gross_profit()",
    ):
        assert engine_total in cpp
    # Win and loss counts and net and gross profit are engine totals, not rows.
    _assert_export(cpp, 0)


@pytest.mark.parametrize("accessor", ["profit", "entry_price"])
def test_open_trade_reads_keep_streaming(accessor: str) -> None:
    cpp = transpile(_PRELUDE + f"open_value = strategy.opentrades.{accessor}(0)\n")

    assert f"open_trade_{accessor}(0)" in cpp
    # Only closed-trade accessors set the flag: open trades stay resident, so an
    # open-trade read exports 0 (the behavior today).
    _assert_export(cpp, 0)


def test_first_closed_trade_index_is_a_constant_and_keeps_streaming() -> None:
    cpp = transpile(_PRELUDE + "oldest = strategy.closedtrades.first_index\n")

    # first_index lowers to the literal 0: no accessor call, so no row is read.
    _assert_export(cpp, 0)


@pytest.mark.parametrize("accessor", _CLOSED_TRADE_ACCESSORS)
def test_every_closed_trade_accessor_keeps_rows(accessor: str) -> None:
    cpp = transpile(_PRELUDE + f"row_value = strategy.closedtrades.{accessor}(0)\n")

    assert f"closed_trade_{accessor}(0)" in cpp
    # Each accessor reads a row by index.
    _assert_export(cpp, 1)


def test_closed_trade_accessor_pins_cover_the_accessor_tables() -> None:
    from pineforge_codegen.codegen.capabilities import READ_ONLY_STRATEGY_CALLS
    from pineforge_codegen.support_checker import CLOSED_TRADE_ACCESSOR_METHODS

    assert len(set(_CLOSED_TRADE_ACCESSORS)) == len(_CLOSED_TRADE_ACCESSORS)
    # A new closed-trade accessor must join the list the accessor test runs over.
    assert set(_CLOSED_TRADE_ACCESSORS) == CLOSED_TRADE_ACCESSOR_METHODS
    assert {
        f"strategy.closedtrades.{name}" for name in _CLOSED_TRADE_ACCESSORS
    } <= READ_ONLY_STRATEGY_CALLS
