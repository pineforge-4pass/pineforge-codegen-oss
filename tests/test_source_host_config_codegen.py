"""Source-host configuration, overrides, and dynamic risk setter emission."""

from __future__ import annotations

import re

from pineforge_codegen import transpile


_CONFIG_SOURCE = '''//@version=6
strategy("source host config", process_orders_on_close=true,
    calc_on_order_fills=true, initial_capital=123.0,
    default_qty_type=strategy.cash, default_qty_value=2.0, pyramiding=3,
    commission_type=strategy.commission.cash_per_contract,
    commission_value=4.0, slippage=5, margin_long=6.0, margin_short=7.0,
    close_entries_rule="ANY")
src = input.source(close, "source")
'''


def _constructor(cpp: str) -> str:
    start = cpp.index("    explicit GeneratedStrategy()")
    end = cpp.index("    void set_strategy_override", start)
    return cpp[start:end]


def _override(cpp: str) -> str:
    start = cpp.index("    void set_strategy_override")
    end = cpp.index("\n#ifndef PINEFORGE_HAS_SCRIPT_RUN_PREPARE_V1", start)
    return cpp[start:end]


def test_constructor_configures_the_source_host_once_in_legacy_write_order():
    constructor = _constructor(transpile(_CONFIG_SOURCE))
    expected = [
        "pineforge::source::PineStrategyConfig cfg{};",
        "cfg.process_orders_on_close = true;",
        "cfg.calc_on_order_fills = true;",
        "cfg.initial_capital = 123.0;",
        "cfg.default_qty_type = static_cast<int>(QtyType::CASH);",
        "cfg.default_qty_value = 2.0;",
        "cfg.pyramiding = 3;",
        "cfg.commission_type = static_cast<int>(CommissionType::CASH_PER_CONTRACT);",
        "cfg.commission_value = 4.0;",
        "cfg.slippage = 5;",
        "cfg.margin_long = 6.0;",
        "cfg.margin_short = 7.0;",
        "cfg.close_entries_rule_any = true;",
        "cfg.src_series_active = true;",
        "configure_pine_strategy(cfg);",
    ]
    assert [constructor.index(line) for line in expected] == sorted(
        constructor.index(line) for line in expected
    )
    assert constructor.count("configure_pine_strategy(cfg);") == 1

    for member in (
        "process_orders_on_close_",
        "calc_on_order_fills_",
        "initial_capital_",
        "default_qty_type_",
        "default_qty_value_",
        "pyramiding_",
        "commission_type_",
        "commission_value_",
        "slippage_",
        "margin_long_",
        "margin_short_",
        "close_entries_rule_any_",
        "_src_series_active_",
    ):
        assert f"{member} =" not in constructor


def test_runtime_overrides_use_the_source_host_adapter_entry_for_all_keys():
    override = _override(transpile(_CONFIG_SOURCE))
    assert re.findall(r'key == "([^"]+)"', override) == [
        "initial_capital",
        "commission_value",
        "default_qty_value",
        "pyramiding",
        "slippage",
        "process_orders_on_close",
        "calc_on_order_fills",
        "close_entries_rule",
        "default_qty_type",
        "commission_type",
    ]
    for field in (
        "initial_capital",
        "commission_value",
        "default_qty_value",
        "pyramiding",
        "slippage",
        "process_orders_on_close",
        "calc_on_order_fills",
        "close_entries_rule",
        "default_qty_type",
        "commission_type",
    ):
        assert f"overrides.{field} =" in override
    assert override.count(
        "pineforge::source::PineStrategyHost::set_strategy_override(overrides);"
    ) == 1

    for member in (
        "initial_capital_",
        "commission_value_",
        "default_qty_value_",
        "pyramiding_",
        "slippage_",
        "process_orders_on_close_",
        "calc_on_order_fills_",
        "close_entries_rule_any_",
        "default_qty_type_",
        "commission_type_",
    ):
        assert f"{member} =" not in override


def test_risk_calls_use_explicit_source_host_setters_without_member_writes():
    cpp = transpile('''//@version=6
strategy("source host risk")
strategy.risk.allow_entry_in(strategy.direction.long)
strategy.risk.max_cons_loss_days(2)
strategy.risk.max_drawdown(3.0, strategy.percent_of_equity)
strategy.risk.max_drawdown(4.0, strategy.cash)
strategy.risk.max_intraday_loss(5.0, strategy.percent_of_equity)
strategy.risk.max_intraday_filled_orders(6)
strategy.risk.max_position_size(7.0)
''')
    calls = [
        line.strip() for line in cpp.splitlines() if "set_pine_risk_" in line
    ]
    assert calls == [
        "set_pine_risk_direction(1);",
        "set_pine_risk_max_cons_loss_days((int)(2));",
        "set_pine_risk_max_drawdown((double)(3.0), true);",
        "set_pine_risk_max_drawdown((double)(4.0), false);",
        "set_pine_risk_max_intraday_loss((double)(5.0), true);",
        "set_pine_risk_max_intraday_filled_orders((int)(6));",
        "set_pine_risk_max_position_size((double)(7.0));",
    ]
    for member in (
        "risk_direction_",
        "risk_max_cons_loss_days_",
        "risk_max_drawdown_",
        "risk_max_drawdown_is_pct_",
        "risk_max_intraday_loss_",
        "risk_max_intraday_loss_is_pct_",
        "max_intraday_filled_orders_",
        "risk_max_position_size_",
    ):
        assert f"{member} =" not in cpp
