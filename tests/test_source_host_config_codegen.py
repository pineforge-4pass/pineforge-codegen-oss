"""Source-host configuration, overrides, and dynamic risk setter emission."""

from __future__ import annotations

import re

import pytest

from tests._legacy_cpp import legacy_cpp
from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Phase


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


@pytest.mark.parametrize("risk_func", ("max_drawdown", "max_intraday_loss"))
@pytest.mark.parametrize("enum_member", ("percent_of_equity", "cash"))
@pytest.mark.parametrize(
    ("declaration", "type_argument", "value"),
    (
        ("", "type = strategy.{enum_member}", 11),
        ("t = strategy.{enum_member}\n", "t", 12),
        ("t = strategy.{enum_member}\n", "type = t", 13),
    ),
    ids=("A2-keyword", "A3-variable", "variable-keyword"),
)
def test_risk_type_argument_uses_the_supplied_enum(
    risk_func, enum_member, declaration, type_argument, value,
):
    source = (
        '//@version=6\nstrategy("source host risk type")\n'
        + declaration.format(enum_member=enum_member)
        + f"strategy.risk.{risk_func}({value}, "
        + type_argument.format(enum_member=enum_member)
        + ")\n"
    )
    cpp = transpile(source)
    calls = [
        line.strip()
        for line in cpp.splitlines()
        if line.lstrip().startswith("set_pine_risk_max_")
    ]
    expected_flag = "true" if enum_member == "percent_of_equity" else "false"
    assert calls == [
        f"set_pine_risk_{risk_func}((double)({value}), {expected_flag});"
    ]


@pytest.mark.parametrize("risk_func", ("max_drawdown", "max_intraday_loss"))
def test_risk_omitted_type_preserves_the_cash_flag(risk_func):
    cpp = transpile(
        '//@version=6\nstrategy("omitted risk type")\n'
        f"strategy.risk.{risk_func}(21)\n"
    )
    assert f"set_pine_risk_{risk_func}((double)(21), false);" in cpp


@pytest.mark.parametrize("risk_func", ("max_drawdown", "max_intraday_loss"))
@pytest.mark.parametrize("type_argument", ("t", "type = t"))
@pytest.mark.parametrize(
    ("initial", "assigned", "assigned_value"),
    (("cash", "percent_of_equity", 1), ("percent_of_equity", "cash", 2)),
)
def test_risk_reassigned_type_reads_the_current_value(
    risk_func, type_argument, initial, assigned, assigned_value,
):
    cpp = transpile(
        '//@version=6\nstrategy("reassigned risk type")\n'
        f"t = strategy.{initial}\n"
        f"t := strategy.{assigned}\n"
        f"strategy.risk.{risk_func}(14, {type_argument})\n"
    )
    calls = [
        line.strip() for line in cpp.splitlines()
        if line.lstrip().startswith(f"set_pine_risk_{risk_func}(")
    ]
    assert len(calls) == 1
    # The flag must read t after its write, rather than use its initializer.
    assert re.fullmatch(
        rf"set_pine_risk_{risk_func}\(\(double\)\(14\), "
        r"\(\(t(?:\[0\])?\) == static_cast<int>\(QtyType::PERCENT_OF_EQUITY\)\)\);",
        calls[0],
    )
    write = re.search(
        rf"\bt(?:\.update\({assigned_value}\)| = {assigned_value});", cpp,
    )
    assert write is not None
    assert write.start() < cpp.index(calls[0])


@pytest.mark.parametrize("risk_func", ("max_drawdown", "max_intraday_loss"))
@pytest.mark.parametrize("keyword", ("", "type = "))
def test_risk_computed_type_emits_the_selection(risk_func, keyword):
    cpp = transpile(
        '//@version=6\nstrategy("computed risk type")\n'
        'use_percent = input.bool(true, "Use percent")\n'
        f"strategy.risk.{risk_func}(15, {keyword}"
        "use_percent ? strategy.percent_of_equity : strategy.cash)\n"
    )
    calls = [
        line.strip() for line in cpp.splitlines()
        if line.lstrip().startswith(f"set_pine_risk_{risk_func}(")
    ]
    assert len(calls) == 1
    assert calls[0].startswith(f"set_pine_risk_{risk_func}((double)(15), ")
    assert "use_percent" in calls[0]
    assert "? (1) : (2)" in calls[0]
    assert calls[0].endswith(
        " == static_cast<int>(QtyType::PERCENT_OF_EQUITY)));"
    )


@pytest.mark.parametrize("risk_func", ("max_drawdown", "max_intraday_loss"))
def test_risk_type_alias_uses_its_lexical_declaration(risk_func):
    cpp = transpile(
        '//@version=6\nstrategy("shadowed risk type")\n'
        "t = strategy.cash\n"
        "if close > open\n"
        "    t = strategy.percent_of_equity\n"
        f"    strategy.risk.{risk_func}(16, type = t)\n"
        f"strategy.risk.{risk_func}(17, type = t)\n"
    )
    calls = [
        line.strip() for line in cpp.splitlines()
        if line.lstrip().startswith(f"set_pine_risk_{risk_func}(")
    ]
    assert calls == [
        f"set_pine_risk_{risk_func}((double)(16), true);",
        f"set_pine_risk_{risk_func}((double)(17), false);",
    ]


@pytest.mark.parametrize("argument", ("{depth}", "num = {depth}"))
@pytest.mark.parametrize(
    ("declarations", "expected"),
    (
        ("const int DEPTH = 800\n", 800),
        ("const int BASE = 640\nconst int DEPTH = BASE + 160\n", 800),
        ("const int DEPTH = 1200\n", 1200),
    ),
    ids=("constant", "constant-expression", "larger-constant"),
)
def test_max_bars_back_const_depth_matches_literal(argument, declarations, expected):
    # Both forms request the same buffer, including beyond the engine's 500.
    for prefix, depth in (("", str(expected)), (declarations, "DEPTH")):
        cpp = transpile(
            '//@version=6\nstrategy("supplied history depth")\n'
            + prefix
            + f"max_bars_back(close, {argument.format(depth=depth)})\n"
            + "y = close[700]\nplot(y)\n"
        )
        assert f"Series<double> _s_close{{{expected}}};" in cpp


@pytest.mark.parametrize(
    ("body", "expected"),
    (
        (
            "decoy() =>\n"
            "    const int DEPTH = 1200\n"
            "    DEPTH\n"
            "max_bars_back(close, DEPTH)\n",
            800,
        ),
        (
            "if close > open\n"
            "    const int DEPTH = 1200\n"
            "    max_bars_back(close, DEPTH)\n",
            1200,
        ),
    ),
    ids=("out-of-scope-decoy", "local-shadow"),
)
def test_max_bars_back_const_depth_uses_the_lexical_binding(body, expected):
    cpp = transpile(
        '//@version=6\nstrategy("scoped history depth")\n'
        "const int DEPTH = 800\n"
        + body
        + "y = close[700]\nplot(y)\n"
    )
    assert f"Series<double> _s_close{{{expected}}};" in cpp


@pytest.mark.parametrize("keyword", ("", "num = "))
@pytest.mark.parametrize(
    ("prefix", "depth", "indent"),
    (
        ("", "0", ""),
        ("", "-800", ""),
        ("const int DEPTH = 0\n", "DEPTH", ""),
        ("const int DEPTH = -800\n", "DEPTH", ""),
        ('DEPTH = input.int(800, "Depth")\n', "DEPTH", ""),
        ("DEPTH = 800\nDEPTH := 1200\n", "DEPTH", ""),
        ("const int DEPTH = 800\n", "bar_index + DEPTH", ""),
        (
            "const int DEPTH = 800\n"
            "if close > open\n"
            "    int DEPTH = bar_index\n",
            "DEPTH",
            "    ",
        ),
    ),
    ids=(
        "zero", "negative", "const-zero", "const-negative",
        "input", "reassigned", "series-expression", "dynamic-shadow",
    ),
)
def test_max_bars_back_unsupported_depth_has_a_located_diagnostic(
    keyword, prefix, depth, indent,
):
    call = f"{indent}max_bars_back(close, {keyword}{depth})"
    source = (
        '//@version=6\nstrategy("unsupported history depth")\n'
        + prefix + call + "\ny = close[700]\nplot(y)\n"
    )
    with pytest.raises(CompileError) as caught:
        transpile(source)
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E5091"
    assert diagnostic.phase == Phase.CODEGEN
    assert diagnostic.message == (
        "max_bars_back(): num must resolve to a positive compile-time integer."
    )
    assert diagnostic.args == {}
    assert (diagnostic.location.line, diagnostic.location.col) == (
        source.splitlines().index(call) + 1,
        call.index(depth, call.index(",")) + 1,
    )


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


def test_an_earlier_constant_enables_the_bar_magnifier_export():
    from pineforge_codegen import transpile_full

    result = transpile_full(
        "//@version=6\n"
        "const bool MAGNIFY = true\n"
        + _MAGNIFIER_SOURCE.format(declaration=", use_bar_magnifier=MAGNIFY")
        .removeprefix("//@version=6\n"))
    # The earlier MAGNIFY constant is true, so the host receives the magnifier export.
    assert result["strategyParams"]["use_bar_magnifier"] is True
    extern_c = result["cpp"][result["cpp"].index('extern "C" {'):]
    assert extern_c.endswith(_MAGNIFIER_EXPORT)
    messages = [d.message for d in result["diagnostics"]]
    assert not any("not a literal bool" in m and "without one" in m for m in messages), messages


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
