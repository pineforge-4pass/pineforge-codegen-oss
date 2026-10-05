"""Source-host configuration, overrides, and dynamic risk setter emission."""

from __future__ import annotations

import re

from tests._legacy_cpp import legacy_cpp
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
    override = _override(legacy_cpp(transpile(_CONFIG_SOURCE)))
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
        line.strip() for line in cpp.splitlines() if line.lstrip().startswith("set_pine_risk_")
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


def test_risk_direction_uses_the_pine_signed_encoding_for_all_values():
    cpp = transpile('''//@version=6
strategy("source host direction encoding")
strategy.risk.allow_entry_in(strategy.direction.short)
strategy.risk.allow_entry_in(strategy.direction.long)
strategy.risk.allow_entry_in(strategy.direction.all)
''')

    calls = [
        line.strip() for line in cpp.splitlines() if line.lstrip().startswith("set_pine_risk_direction(")
    ]
    assert calls == [
        "set_pine_risk_direction(-1);",
        "set_pine_risk_direction(1);",
        "set_pine_risk_direction(0);",
    ]


def test_risk_percent_flags_are_true_only_for_percent_of_equity():
    cpp = transpile('''//@version=6
strategy("source host percent flags")
strategy.risk.max_drawdown(10, strategy.percent_of_equity)
strategy.risk.max_drawdown(500, strategy.cash)
strategy.risk.max_intraday_loss(10, strategy.percent_of_equity)
strategy.risk.max_intraday_loss(500, strategy.cash)
''')

    calls = [
        line.strip() for line in cpp.splitlines() if line.lstrip().startswith("set_pine_risk_max_")
    ]
    assert calls == [
        "set_pine_risk_max_drawdown((double)(10), true);",
        "set_pine_risk_max_drawdown((double)(500), false);",
        "set_pine_risk_max_intraday_loss((double)(10), true);",
        "set_pine_risk_max_intraday_loss((double)(500), false);",
    ]


_MAGNIFIER_SOURCE = '''//@version=6
strategy("magnifier"{declaration})
if close > open
    strategy.entry("L", strategy.long)
'''
_MAGNIFIER_EXPORT = (
    "    int strategy_declares_bar_magnifier(void) {\n"
    "        return 1;\n"
    "    }\n"
    "}\n"
)


def test_a_declared_bar_magnifier_is_exported_to_the_host():
    """tests/test_e2e_bar_magnifier_flag.py replays the TradingView tapes."""
    cpp = transpile(_MAGNIFIER_SOURCE.format(declaration=", use_bar_magnifier=true"))
    extern_c = cpp[cpp.index('extern "C" {'):]
    assert extern_c.endswith(_MAGNIFIER_EXPORT)


def test_no_export_unless_the_magnifier_is_declared_true():
    omitted = transpile(_MAGNIFIER_SOURCE.format(declaration=""))
    declared_off = transpile(_MAGNIFIER_SOURCE.format(declaration=", use_bar_magnifier=false"))
    assert "strategy_declares_bar_magnifier" not in omitted
    assert declared_off == omitted.replace(
        'strategy("magnifier")', 'strategy("magnifier", use_bar_magnifier=false)')


def test_a_non_literal_declaration_warns_that_the_host_runs_without_it():
    from pineforge_codegen import transpile_full

    result = transpile_full(
        "//@version=6\n"
        "const bool MAGNIFY = true\n"
        + _MAGNIFIER_SOURCE.format(declaration=", use_bar_magnifier=MAGNIFY")
        .removeprefix("//@version=6\n"))
    assert "strategy_declares_bar_magnifier" not in result["cpp"]
    messages = [d.message for d in result["diagnostics"]]
    assert any("not a literal bool" in m and "without one" in m for m in messages), messages


# TradingView's Pine v6 defaults for an omitted initial_capital,
# default_qty_type and default_qty_value (tests/fixtures/strategy_defaults):
# the constructor declares them, because the source host's own
# PineStrategyConfig defaults are the pre-2026-09-24 values.
_V6_CAPITAL = "cfg.initial_capital = 100000.0;"
_V6_PERCENT = "cfg.default_qty_type = static_cast<int>(QtyType::PERCENT_OF_EQUITY);"
_V6_VALUE = "cfg.default_qty_value = 100.0;"


def _sizing_lines(declaration: str) -> list[str]:
    constructor = _constructor(transpile(f'//@version=6\n{declaration}\n'))
    return [line.strip() for line in constructor.splitlines()
            if line.strip().startswith(("cfg.initial_capital", "cfg.default_qty_"))]


def test_an_omitted_capital_and_quantity_are_tradingviews_v6_defaults():
    assert _sizing_lines('strategy("omits all three")') == [
        _V6_CAPITAL, _V6_PERCENT, _V6_VALUE]


def test_each_omitted_parameter_takes_its_own_v6_default():
    # Only the omitted parameter is defaulted; a declared one is emitted as is.
    assert _sizing_lines('strategy("x", initial_capital=5000)') == [
        "cfg.initial_capital = 5000.0;", _V6_PERCENT, _V6_VALUE]
    # default_qty_value is 100 whatever the type: 100 contracts, or 100 cash.
    assert _sizing_lines('strategy("x", default_qty_type=strategy.fixed)') == [
        _V6_CAPITAL, "cfg.default_qty_type = static_cast<int>(QtyType::FIXED);", _V6_VALUE]
    assert _sizing_lines('strategy("x", default_qty_type=strategy.cash)') == [
        _V6_CAPITAL, "cfg.default_qty_type = static_cast<int>(QtyType::CASH);", _V6_VALUE]
    # An omitted type sizes the declared value as a percent of equity.
    assert _sizing_lines('strategy("x", default_qty_value=1)') == [
        _V6_CAPITAL, _V6_PERCENT, "cfg.default_qty_value = 1.0;"]


def test_a_declaration_of_all_three_is_emitted_exactly_as_declared():
    assert _sizing_lines('strategy("x", initial_capital=1000000, '
                         'default_qty_type=strategy.fixed, default_qty_value=1)') == [
        "cfg.initial_capital = 1000000.0;",
        "cfg.default_qty_type = static_cast<int>(QtyType::FIXED);",
        "cfg.default_qty_value = 1.0;",
    ]
