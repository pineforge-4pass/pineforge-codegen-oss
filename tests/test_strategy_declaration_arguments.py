"""Strategy declaration constants bind once in the language's parameter order."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._compile import run_emitted_tu
from tests.test_order_shapes import receipt
from tests.test_source_host_config_codegen import _constructor
from tests.test_strategy_capabilities import emitted_receipt


_ONE_ENTRY = """if bar_index % 20 == 5
    strategy.entry("L", strategy.long)
if bar_index % 20 == 15
    strategy.close("L")
observed_lots = strategy.opentrades
"""
_THREE_ENTRIES = """if bar_index % 20 == 5
    strategy.entry("A", strategy.long, qty=1)
    strategy.entry("B", strategy.long, qty=2)
    strategy.entry("C", strategy.long, qty=3)
if bar_index % 20 == 15
    strategy.close_all()
observed_lots = strategy.opentrades
"""

_DRIVER = r"""
#include <iomanip>
#include <iostream>

int main() {
    Bar bars[] = {
        {100, 100, 100, 100, 1, 60000LL},
        {100, 100, 100, 100, 1, 120000LL},
        {100, 100, 100, 100, 1, 180000LL},
        {100, 100, 100, 100, 1, 240000LL},
        {100, 100, 100, 100, 1, 300000LL},
        {100, 100, 100, 100, 1, 360000LL},
        {100, 100, 100, 100, 1, 420000LL},
        {100, 100, 100, 100, 1, 480000LL},
        {100, 100, 100, 100, 1, 540000LL},
        {100, 100, 100, 100, 1, 600000LL},
        {100, 100, 100, 100, 1, 660000LL},
        {100, 100, 100, 100, 1, 720000LL},
        {100, 100, 100, 100, 1, 780000LL},
        {100, 100, 100, 100, 1, 840000LL},
        {100, 100, 100, 100, 1, 900000LL},
        {100, 100, 100, 100, 1, 960000LL},
        {100, 100, 100, 100, 1, 1020000LL},
    };
    GeneratedStrategy partial;
    partial.run(bars, 7, "1", "1");
    if (!partial.last_error().empty()) {
        std::cerr << partial.last_error();
        return 1;
    }
    std::cout << std::setprecision(17) << partial.observed_lots << ' '
              << partial.live_position_size() << '\n';
    void* strategy = strategy_create(nullptr);
    ReportC report{};
    run_backtest_full(strategy, bars, 17, "1", "1", 0, 4, 1, &report);
    const char* error = strategy_get_last_error(strategy);
    if (error && *error) {
        std::cerr << error;
        return 2;
    }
    for (int i = 0; i < report.trades_len; ++i) {
        const auto& trade = report.trades[i];
        std::cout << trade.qty << ' ' << trade.entry_price << ' '
                  << trade.exit_price << ' ' << trade.entry_bar_index << ' '
                  << trade.exit_bar_index << '\n';
    }
    report_free(&report);
    strategy_free(strategy);
}
"""


def _source(declaration: str, body: str = _ONE_ENTRY, prefix: str = "") -> str:
    return f"//@version=6\n{prefix}{declaration}\n{body}"


def _run(cpp: str, *, distinct_prices: bool = False):
    driver = _DRIVER
    if distinct_prices:
        driver = driver.replace(
            "{100, 100, 100, 100, 1, 360000LL}",
            "{100, 110, 100, 110, 1, 360000LL}",
        ).replace(
            "{100, 100, 100, 100, 1, 420000LL}",
            "{130, 130, 130, 130, 1, 420000LL}",
        ).replace(
            "{100, 100, 100, 100, 1, 960000LL}",
            "{100, 150, 100, 150, 1, 960000LL}",
        ).replace(
            "{100, 100, 100, 100, 1, 1020000LL}",
            "{170, 170, 170, 170, 1, 1020000LL}",
        )
    output = run_emitted_tu(cpp, driver, opt="-O0", label="declaration arguments")
    rows = [tuple(float(word) for word in line.split()) for line in output.splitlines()]
    return rows[0], rows[1:]


@pytest.mark.parametrize("form", ["literal", "name"])
def test_default_quantity_name_configures_every_entry(form):
    prefix = "const int LOTS = 3\n" if form == "name" else ""
    value = "LOTS" if form == "name" else "3"
    source = _source(
        'strategy("Fixed quantity", initial_capital=1000000, '
        f"default_qty_type=strategy.fixed, default_qty_value={value})",
        prefix=prefix,
    )
    cpp = transpile(source)
    # One fixed lot of size 3 opens on bar 6 and closes on bar 16 at the flat price 100.
    assert "cfg.default_qty_value = 3.0;" in _constructor(cpp)
    assert receipt("", source)["settings"]["default_qty_value"] == 3.0
    opened, trades = _run(cpp)
    assert opened == (1.0, 3.0)
    assert trades == [(3.0, 100.0, 100.0, 6.0, 16.0)]


@pytest.mark.parametrize("value", ["true", "not false"], ids=["literal", "expression"])
def test_close_processing_expression_uses_signal_closes(value):
    body = _ONE_ENTRY.replace(
        'strategy.entry("L", strategy.long)',
        'strategy.entry("L", strategy.long, qty=1 + bar_index % 3)',
    )
    source = _source(
        f'strategy("Close processing", initial_capital=1000000, process_orders_on_close={value})',
        body,
    )
    cpp = transpile(source)
    # Close processing fills on bars 5 and 15 at 110 and 150; 1 + 5 % 3 = 3.
    assert "cfg.process_orders_on_close = true;" in _constructor(cpp)
    assert receipt("", source)["process_orders_on_close"] is True
    assert emitted_receipt(source)["declarations"]["process_orders_on_close"] is True
    opened, trades = _run(cpp, distinct_prices=True)
    assert opened == (1.0, 3.0)
    assert trades == [(3.0, 110.0, 150.0, 5.0, 15.0)]


@pytest.mark.parametrize("value", ["3", "2 + 1"], ids=["literal", "expression"])
def test_pyramiding_expression_keeps_three_open_lots(value):
    source = _source(
        f'strategy("Multiple entries", initial_capital=1000000, pyramiding={value})',
        _THREE_ENTRIES,
    )
    cpp = transpile(source)
    # Three allowed entries leave three lots with total size 1 + 2 + 3 = 6.
    assert "cfg.pyramiding = 3;" in _constructor(cpp)
    assert receipt("", source)["settings"]["pyramiding"] == 3
    opened, trades = _run(cpp)
    assert opened == (3.0, 6.0)
    assert trades == [
        (1.0, 100.0, 100.0, 6.0, 16.0),  # A fills one bar after each signal, with quantity 1.
        (2.0, 100.0, 100.0, 6.0, 16.0),  # B uses the same flat fills, with quantity 2.
        (3.0, 100.0, 100.0, 6.0, 16.0),  # C uses the same flat fills, with quantity 3.
    ]


@pytest.mark.parametrize("form", ["named", "positional"])
def test_seventh_positional_argument_is_pyramiding(form):
    declaration = (
        'strategy("Positional entries", initial_capital=1000000, pyramiding=3)'
        if form == "named" else
        'strategy("Positional entries", "Entries", true, format.inherit, 2, '
        "scale.right, 3, initial_capital=1000000)"
    )
    source = _source(declaration, _THREE_ENTRIES)
    cpp = transpile(source)
    # Position seven is pyramiding=3, admitting all three differently named lots.
    assert "cfg.pyramiding = 3;" in _constructor(cpp)
    opened, trades = _run(cpp)
    assert opened == (3.0, 6.0)
    assert trades == [
        (1.0, 100.0, 100.0, 6.0, 16.0),  # A fills at the next opens, with quantity 1.
        (2.0, 100.0, 100.0, 6.0, 16.0),  # B fills at the next opens, with quantity 2.
        (3.0, 100.0, 100.0, 6.0, 16.0),  # C fills at the next opens, with quantity 3.
    ]


@pytest.mark.parametrize("form", ["named", "positional"])
def test_positional_sizing_uses_one_percent_of_declared_capital(form):
    declaration = (
        'strategy("Equity quantity", default_qty_type=strategy.percent_of_equity, '
        "default_qty_value=1, initial_capital=1000000)"
        if form == "named" else
        'strategy("Equity quantity", "Equity", true, format.inherit, 2, scale.right, '
        "0, false, false, 0, 0, strategy.percent_of_equity, 1, 1000000)"
    )
    source = _source(declaration)
    cpp = transpile(source)
    constructor = _constructor(cpp)
    # 1,000,000 * 1 / 100 / 100 = 100 contracts; next-open fills are on bars 6 and 16.
    assert "cfg.initial_capital = 1000000.0;" in constructor
    assert "cfg.default_qty_type = static_cast<int>(QtyType::PERCENT_OF_EQUITY);" in constructor
    assert "cfg.default_qty_value = 1.0;" in constructor
    assert receipt("", source)["settings"]["default_qty_value"] == 1.0
    opened, trades = _run(cpp)
    assert opened == (1.0, 100.0)
    assert trades == [(100.0, 100.0, 100.0, 6.0, 16.0)]


# Each row gives the same constant as a literal, an earlier name and an expression.
_SETTINGS = [
    ("title", '"Constant settings"', '"Constant " + "settings"', "Constant settings"),  # Concatenate two strings.
    ("shorttitle", '"Short"', 'true ? "Short" : "Other"', "Short"),  # Select the true string arm.
    ("overlay", "true", "not false", True),  # Negating false gives true.
    ("format", "format.price", "true ? format.price : format.inherit", "format.price"),  # Select price formatting.
    ("precision", "4", "2 * 2", 4),  # Two times two is four.
    ("scale", "scale.left", "false ? scale.right : scale.left", "scale.left"),  # Select the false arm.
    ("pyramiding", "4", "1 + 3", 4),  # One plus three admits four entries.
    ("calc_on_order_fills", "true", "2 > 1", True),  # Two exceeds one.
    ("calc_on_every_tick", "true", "not false", True),  # Negating false gives true.
    ("max_bars_back", "700", "350 * 2", 700),  # Two windows of 350 need 700 slots.
    ("backtest_fill_limits_assumption", "2", "7 % 5", 2),  # Seven divided by five leaves two.
    ("default_qty_type", "strategy.cash", "true ? strategy.cash : strategy.fixed", "strategy.cash"),  # Select cash sizing.
    ("default_qty_value", "7.5", "15.0 / 2", 7.5),  # Fifteen divided by two is 7.5.
    ("initial_capital", "800000", "400000 * 2", 800000),  # Two contributions of 400,000.
    ("currency", "currency.EUR", "true ? currency.EUR : currency.USD", "currency.EUR"),  # Select the euro account.
    ("slippage", "4", "9 - 5", 4),  # Nine minus five is four ticks.
    ("commission_type", "strategy.commission.cash_per_order",
     "true ? strategy.commission.cash_per_order : strategy.commission.percent",
     "strategy.commission.cash_per_order"),  # Select the per-order commission.
    ("commission_value", "1.25", "5.0 / 4", 1.25),  # Five divided by four is 1.25.
    ("process_orders_on_close", "true", "not false", True),  # Negating false gives true.
    ("close_entries_rule", '"ANY"', '2 == 2 ? "ANY" : "FIFO"', "ANY"),  # Equality selects ANY.
    ("margin_long", "25", "100 / 4", 25),  # A quarter of 100 is 25.
    ("margin_short", "50", "100 - 50", 50),  # Half of 100 is 50.
    ("explicit_plot_zorder", "true", "false or true", True),  # One true operand makes or true.
    ("max_lines_count", "60", "30 * 2", 60),  # Thirty pairs are sixty lines.
    ("max_labels_count", "70", "35 * 2", 70),  # Thirty-five pairs are seventy labels.
    ("max_boxes_count", "80", "40 * 2", 80),  # Forty pairs are eighty boxes.
    ("calc_bars_count", "600", "300 * 2", 600),  # Two groups of 300 bars.
    ("risk_free_rate", "3.5", "7.0 / 2", 3.5),  # Seven divided by two is 3.5.
    ("use_bar_magnifier", "true", "not false", True),  # Negating false enables the magnifier.
    ("fill_orders_on_standard_ohlc", "true", "true and not false", True),  # Both operands are true.
    ("max_polylines_count", "20", "10 + 10", 20),  # Ten plus ten is twenty.
    ("dynamic_requests", "false", "1 > 2", False),  # One does not exceed two.
    ("behind_chart", "false", "not true", False),  # Negating true gives false.
    ("calc_on_every_history_tick", "true", "1 <= 2", True),  # One is at most two.
]

# Positions are written independently of the compiler's declaration table.
_POSITIONAL = [
    '"Defaults"', '"Default"', "true", "format.inherit", "2", "scale.right",
    "0", "false", "false", "500", "0", "strategy.fixed", "1", "1000000",
    "currency.NONE", "0", "strategy.commission.percent", "0", "false", '"FIFO"',
    "100", "100", "false", "50", "50", "50", "0", "2", "false", "false",
    "50", "true", "true", "false",
]

_CONFIG = {
    "pyramiding": "cfg.pyramiding = 4;",  # The constant permits four concurrent entries.
    "calc_on_order_fills": "cfg.calc_on_order_fills = true;",  # A true flag enables fill recalculation.
    "default_qty_type": "cfg.default_qty_type = static_cast<int>(QtyType::CASH);",  # Cash uses the cash enum.
    "default_qty_value": "cfg.default_qty_value = 7.5;",  # The declaration supplies 7.5 currency units.
    "initial_capital": "cfg.initial_capital = 800000.0;",  # Capital is stored as a double.
    "slippage": "cfg.slippage = 4;",  # Slippage is four integral ticks.
    "commission_type": "cfg.commission_type = static_cast<int>(CommissionType::CASH_PER_ORDER);",  # Per-order cash uses its enum.
    "commission_value": "cfg.commission_value = 1.25;",  # Commission is 1.25 per order.
    "process_orders_on_close": "cfg.process_orders_on_close = true;",  # A true flag enables close processing.
    "close_entries_rule": "cfg.close_entries_rule_any = true;",  # ANY enables the non-FIFO rule.
    "margin_long": "cfg.margin_long = 25.0;",  # Long margin is twenty-five percent.
    "margin_short": "cfg.margin_short = 50.0;",  # Short margin is fifty percent.
}


@pytest.mark.parametrize("form", ["literal", "name", "expression", "positional"])
@pytest.mark.parametrize("position,setting", list(enumerate(_SETTINGS)),
                         ids=[row[0] for row in _SETTINGS])
def test_all_settings_share_constant_binding(position, setting, form):
    name, literal, expression, expected = setting
    prefix = ""
    value = literal
    if form == "name":
        prefix = f"const SETTING = {literal}\n"
        value = "SETTING"
    elif form == "expression":
        value = expression
    if form == "positional":
        arguments = _POSITIONAL[:position] + [literal]
        declaration = f"strategy({', '.join(arguments)})"
    else:
        arguments = f"{name}={value}" if name == "title" else f'"Settings", {name}={value}'
        declaration = f"strategy({arguments})"
    result = transpile_full(_source(declaration, body="", prefix=prefix))
    assert result["strategyParams"][name] == expected
    if name in _CONFIG:
        assert _CONFIG[name] in _constructor(result["cpp"])
    # Earlier names and expressions are proven constants, so metadata has no unresolved setting.
    assert name not in emitted_receipt(_source(declaration, body="", prefix=prefix))["unresolved"]


@pytest.mark.parametrize("name", [row[0] for row in _SETTINGS])
@pytest.mark.parametrize("form", ["series", "input", "persistent", "qualified", "reassigned"])
def test_nonconstant_settings_are_located_errors(name, form):
    prefix, value, suffix = "", "close", ""
    if form == "input":
        prefix, value = "value = input.int(2)\n", "value"
    elif form == "persistent":
        prefix, value = "var int value = 2\n", "value"
    elif form == "qualified":
        prefix, value = "series int value = 2\n", "value"
    elif form == "reassigned":
        prefix, value, suffix = "value = 2\n", "value", "value := 3\n"
    arguments = f"{name}={value}" if name == "title" else f'"Nonconstant", {name}={value}'
    source = _source(f"strategy({arguments})", body=suffix, prefix=prefix)
    with pytest.raises(CompileError) as error:
        transpile(source, filename="nonconstant.pine")
    diagnostic = error.value.diagnostics[0]
    # The setting argument follows one directive and any one-line declaration.
    assert diagnostic.location.line == (2 if form == "series" else 3)
    assert diagnostic.location.file == "nonconstant.pine"
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert name in diagnostic.message and "constant" in diagnostic.message
    assert diagnostic.code == "PF-E5080"
    assert diagnostic.args == {"name": name}


@pytest.mark.parametrize("position", [
    4,   # Precision is the fifth positional parameter.
    5,   # Scale is the sixth positional parameter.
    7,   # Fill recalculation is the eighth positional parameter.
    11,  # Quantity type is the twelfth positional parameter.
    12,  # Quantity value is the thirteenth positional parameter.
    13,  # Initial capital is the fourteenth positional parameter.
    15,  # Slippage is the sixteenth positional parameter.
    16,  # Commission type is the seventeenth positional parameter.
    17,  # Commission value is the eighteenth positional parameter.
])
def test_positional_series_arguments_are_located_errors(position):
    arguments = ", ".join(_POSITIONAL[:position])
    source = f"//@version=6\nstrategy(\n    {arguments},\n    close)\n"
    with pytest.raises(CompileError, match="constant") as error:
        transpile(source, filename="positional.pine")
    # The series argument starts on its own fourth line at column five.
    diagnostic = error.value.diagnostics[0]
    assert diagnostic.location.line == 4
    assert diagnostic.location.col == 5
    assert diagnostic.location.file == "positional.pine"


def test_named_series_error_points_at_the_argument_value():
    source = '//@version=6\nstrategy("Arguments",\n    pyramiding=close)\n'
    with pytest.raises(CompileError, match="constant") as error:
        transpile(source, filename="named.pine")
    # Four spaces, ten letters and '=' put close at column sixteen on line three.
    diagnostic = error.value.diagnostics[0]
    assert diagnostic.location.line == 3
    assert diagnostic.location.col == 16
    assert diagnostic.location.file == "named.pine"


@pytest.mark.parametrize("expression", [
    "true ? 3 : close",
    "false and close > open",
    "true or bar_index > 0",
    "strategy.equity",
    "unknown_name",
])
def test_constant_selection_never_hides_a_series_dependency(expression):
    with pytest.raises(CompileError, match="constant"):
        transpile(_source(f'strategy("Dependencies", pyramiding={expression})', body=""))


@pytest.mark.parametrize("name,value,expected", [
    ("default_qty_value", "8 / 2", 4.0),  # Pine v6 divides constant integers as floating point.
    ("precision", "int(8 / 2)", 4),  # The explicit integer cast restores an integer constant.
    ("pyramiding", "math.max(2, 5)", 5),  # The larger of two and five is five.
    ("pyramiding", "math.max(y=5, x=2)", 5),  # Keywords bind to x and y before taking the maximum.
    ("default_qty_value", "math.avg(2, 6)", 4.0),  # The mean of two and six is four.
    ("precision", "math.round(2.5)", 3),  # Pine rounds a positive half upward.
    ("precision", "math.round(x=2.5)", 3),  # The named x argument has the same half-up result.
    ("precision", "math.floor(3.75)", 3),  # Floor takes the greatest integer at most 3.75.
    ("overlay", "not (1 < 2)", False),  # One is less than two, so its negation is false.
    ("precision", "false ? int(1 / 0) : 4", 4),  # The constant condition leaves the invalid arm unevaluated.
])
def test_constant_operators_use_pine_values(name, value, expected):
    result = transpile_full(_source(f'strategy("Constant operators", {name}={value})', body=""))
    assert result["strategyParams"][name] == expected
    assert type(result["strategyParams"][name]) is type(expected)


def test_earlier_constant_aliases_resolve_in_source_order():
    source = _source(
        'strategy("Aliases", default_qty_type=strategy.fixed, default_qty_value=TOTAL)',
        prefix="const int BASE = 2\nconst int TOTAL = BASE * 2 + 1\n",
    )
    # Twice two plus one is five contracts.
    assert "cfg.default_qty_value = 5.0;" in _constructor(transpile(source))


@pytest.mark.parametrize("declaration,fragment", [
    ('strategy("Duplicate", title="Again")', "both positionally and by keyword"),
    ('strategy("Duplicate", "Short", shorttitle="Again")', "both positionally and by keyword"),
    ('strategy("Duplicate", pyramiding=2, pyramiding=3)', "duplicate keyword argument"),
    ("strategy(" + ", ".join(_POSITIONAL + ["false"]) + ")", "too many positional arguments"),
])
def test_declaration_binding_rejects_duplicate_and_excess_arguments(declaration, fragment):
    # A parameter has one slot, and the complete signature has no extra positional slot.
    with pytest.raises(CompileError) as error:
        transpile(_source(declaration, body=""))
    assert fragment in str(error.value)


@pytest.mark.parametrize("value", ["true", "not false", "ENABLED"])
def test_magnifier_export_uses_the_resolved_boolean(value):
    prefix = "const bool ENABLED = true\n" if value == "ENABLED" else ""
    source = _source(f'strategy("Magnification", use_bar_magnifier={value})',
                     body="", prefix=prefix)
    # Every spelling is true, so each advertises the same magnifier requirement.
    assert "int strategy_declares_bar_magnifier(void)" in transpile(source)


@pytest.mark.parametrize("value", ["700", "350 * 2", "DEPTH"])
def test_history_capacity_uses_the_resolved_constant(value):
    prefix = "const int DEPTH = 700\n" if value == "DEPTH" else ""
    source = _source(
        f'strategy("History capacity", max_bars_back={value})',
        body="previous = close[1]\n", prefix=prefix,
    )
    # The declaration requests 700 slots for the history buffer.
    assert "{700}" in transpile(source)


def test_scale_constant_carriers_need_no_runtime_initializer():
    source = _source(
        'strategy("Scale constant", scale=SIDE, initial_capital=1000000, '
        "default_qty_type=strategy.fixed, default_qty_value=1)",
        prefix="const SIDE = scale.left\n",
    )
    cpp = transpile(source)
    # The scale is left; fixed size 1 opens on bar 6 and closes on bar 16 at price 100.
    assert transpile_full(source)["strategyParams"]["scale"] == "scale.left"
    assert "scale" not in emitted_receipt(source)["unresolved"]
    opened, trades = _run(cpp)
    assert opened == (1.0, 1.0)
    assert trades == [(1.0, 100.0, 100.0, 6.0, 16.0)]


@pytest.mark.parametrize("body", [
    "observed = SIDE\n",
    "consume(x) =>\n    strategy.entry(\"L\", strategy.long, qty=x)\nunused = consume(SIDE)\n",
])
def test_visual_constants_with_runtime_reads_keep_their_existing_error(body):
    source = _source(
        'strategy("Scale usage", scale=SIDE)', body=body,
        prefix="const SIDE = scale.left\n",
    )
    # A runtime read of a visual constant still has no faithful numeric lowering.
    with pytest.raises(CompileError, match="visual/style constant"):
        transpile(source)


@pytest.mark.parametrize("value", [
    'strategy.cash + ""',
    'true ? "cash" : "fixed"',
    "MODE",
])
def test_computed_enum_strings_reach_the_sizing_enum(value):
    prefix = 'const string MODE = "cash"\n' if value == "MODE" else ""
    source = _source(
        f'strategy("Cash sizing", default_qty_type={value}, default_qty_value=500)',
        body="", prefix=prefix,
    )
    # Every expression selects cash sizing rather than contracts or equity percent.
    assert "cfg.default_qty_type = static_cast<int>(QtyType::CASH);" in _constructor(transpile(source))
