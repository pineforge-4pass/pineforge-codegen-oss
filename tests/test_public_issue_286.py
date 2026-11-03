"""Order keywords must bind to the called function's declared parameters."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("order keywords")\n'

# Each refusal starts with a complete, otherwise valid call. In particular,
# exit has a price parameter, so its separate price requirement cannot pass
# an unknown-keyword test for the wrong reason.
ORDER_CALLS = (
    ("entry", ('"L"', "strategy.long")),
    ("order", ('"O"', "strategy.short", "qty = 2.5")),
    ("exit", ('"X"', '"L"', "stop = low")),
    ("close", ('"L"',)),
    ("close_all", ()),
    ("cancel", ('"L"',)),
    ("cancel_all", ()),
)


def _call(name, arguments):
    return f"strategy.{name}({', '.join(arguments)})"


def _assert_unknown_keyword(source, name, keyword, *, check_support=True):
    with pytest.raises(CompileError) as caught:
        transpile(
            source, filename="order_keywords.pine", check_support=check_support,
        )
    errors = [
        diagnostic for diagnostic in caught.value.diagnostics
        if diagnostic.level == Level.ERROR
    ]
    assert len(errors) == 1, caught.value.diagnostics
    diagnostic, = errors
    assert diagnostic.code == "PF-E2027"
    assert diagnostic.args == {"key": f"strategy.{name}", "name": keyword}
    assert diagnostic.message == (
        f"strategy.{name}: unknown keyword argument '{keyword}'"
    )
    start = source.index(f"{keyword} =")
    line = source.count("\n", 0, start) + 1
    col = start - source.rfind("\n", 0, start)
    assert diagnostic.location.file == "order_keywords.pine"
    assert (
        diagnostic.location.line, diagnostic.location.col,
        diagnostic.location.end_col,
    ) == (line, col, col + len(keyword))


@pytest.mark.parametrize("name, arguments", ORDER_CALLS)
@pytest.mark.parametrize("keyword", ("when", "bogus", "misspelled_parameter"))
@pytest.mark.parametrize("value", (
    "true", "false", "bar_index % 20 == 0", "input.bool(false)",
))
@pytest.mark.parametrize("check_support", (True, False))
def test_unknown_order_keywords_are_located_errors(
    name, arguments, keyword, value, check_support,
):
    call = _call(name, (*arguments, f"{keyword} = {value}"))
    _assert_unknown_keyword(
        HEADER + call + "\n", name, keyword, check_support=check_support,
    )


@pytest.mark.parametrize("name, arguments", ORDER_CALLS)
@pytest.mark.parametrize("body", (
    "{call}\n",
    "if false\n    {call}\n",
    "unused() =>\n    {call}\n",
))
def test_unreachable_and_multiline_order_calls_still_validate_names(
    name, arguments, body,
):
    call = _call(name, (*arguments, "\n        when = close > open"))
    _assert_unknown_keyword(HEADER + body.format(call=call), name, "when")


@pytest.mark.parametrize("name, arguments, keyword, value", (
    ("entry", ('"L"', "strategy.long"), "qty_percent", "50"),
    ("order", ('"O"', "strategy.long", "qty = 1"), "immediately", "true"),
    ("exit", ('"X"', '"L"', "limit = high"), "direction", "strategy.long"),
    ("close", ('"L"',), "limit", "high"),
    ("close_all", (), "id", '"L"'),
    ("cancel", ('"L"',), "comment", '"not a cancel parameter"'),
    ("cancel_all", (), "id", '"L"'),
))
@pytest.mark.parametrize("check_support", (True, False))
def test_keywords_from_another_order_function_are_not_accepted(
    name, arguments, keyword, value, check_support,
):
    call = _call(name, (*arguments, f"{keyword} = {value}"))
    _assert_unknown_keyword(
        HEADER + call + "\n", name, keyword, check_support=check_support,
    )


@pytest.mark.parametrize("keyword", ("when", "bogus", "misspelled_parameter"))
def test_existing_ta_unknown_keyword_control(keyword):
    source = HEADER + f"value = ta.sma(close, 10, {keyword} = false)\n"
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="ta_keywords.pine")
    errors = [
        diagnostic for diagnostic in caught.value.diagnostics
        if diagnostic.level == Level.ERROR
    ]
    assert len(errors) == 1
    diagnostic, = errors
    assert f"ta.sma has no parameter '{keyword}'" in diagnostic.message
    line = source.splitlines()[2]
    assert diagnostic.location.file == "ta_keywords.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (
        3, line.index("false") + 1,
    )


# Written independently of the registry: valid optional names must not become
# casualties of enabling validation. Retain the existing qty_type extension
# and alert metadata policy as well as the parameters used by the runtime.
VALID_ARGUMENTS = (
    ("entry", (
        ("id", '"L"'), ("direction", "strategy.long"), ("qty", "2.5"),
        ("limit", "low"), ("stop", "high"), ("oca_name", '"entries"'),
        ("oca_type", "strategy.oca.cancel"), ("comment", '"entry comment"'),
        ("alert_message", '"entry alert"'), ("disable_alert", "false"),
        ("qty_type", "strategy.fixed"),
    )),
    ("order", (
        ("id", '"O"'), ("direction", "strategy.short"), ("qty", "3.5"),
        ("limit", "high"), ("stop", "low"), ("oca_name", '"orders"'),
        ("oca_type", "strategy.oca.reduce"), ("comment", '"order comment"'),
        ("alert_message", '"order alert"'), ("disable_alert", "true"),
        ("qty_type", "strategy.fixed"),
    )),
    ("exit", (
        ("id", '"X"'), ("from_entry", '"L"'), ("qty", "1.5"),
        ("qty_percent", "50"), ("profit", "10"), ("loss", "5"),
        ("limit", "high"), ("stop", "low"), ("trail_price", "high"),
        ("trail_points", "10"), ("trail_offset", "2"), ("oca_name", '"exits"'),
        ("comment", '"exit comment"'), ("comment_profit", '"profit"'),
        ("comment_loss", '"loss"'), ("comment_trailing", '"trailing"'),
        ("alert_message", '"exit alert"'), ("alert_profit", '"profit alert"'),
        ("alert_loss", '"loss alert"'), ("alert_trailing", '"trailing alert"'),
        ("disable_alert", "false"),
    )),
    ("close", (
        ("id", '"L"'), ("comment", '"close comment"'), ("qty", "1.5"),
        ("qty_percent", "50"), ("alert_message", '"close alert"'),
        ("immediately", "true"), ("disable_alert", "false"),
    )),
    ("close_all", (
        ("comment", '"close all comment"'), ("alert_message", '"close all alert"'),
        ("immediately", "true"), ("disable_alert", "false"),
    )),
    ("cancel", (("id", '"O"'),)),
    ("cancel_all", ()),
)


def _arguments_for_form(parameters, form):
    if form == "positional":
        return [value for _, value in parameters]
    if form == "named":
        return [f"{key} = {value}" for key, value in reversed(parameters)]
    assert form == "mixed"
    return [
        value if index == 0 else f"{key} = {value}"
        for index, (key, value) in enumerate(parameters)
    ]


@pytest.mark.parametrize("name, parameters", VALID_ARGUMENTS)
@pytest.mark.parametrize("form", ("named", "mixed"))
@pytest.mark.parametrize("check_support", (True, False))
def test_valid_order_keywords_keep_the_positional_emission(
    name, parameters, form, check_support,
):
    positional = _call(name, _arguments_for_form(parameters, "positional"))
    named = _call(name, _arguments_for_form(parameters, form))
    assert transpile(HEADER + named + "\n", check_support=check_support) == \
        transpile(HEADER + positional + "\n", check_support=check_support)


@pytest.mark.parametrize("name, arguments", ORDER_CALLS)
def test_valid_minimal_order_calls_still_transpile(name, arguments):
    cpp = transpile(HEADER + _call(name, arguments) + "\n")
    assert cpp


@pytest.mark.parametrize("form", ("positional", "named", "mixed"))
def test_valid_order_arguments_compile_and_evaluate_once(form):
    source = '''//@version=6
strategy("order evaluation", initial_capital=25000, pyramiding=2,
    default_qty_type=strategy.fixed, default_qty_value=1,
    process_orders_on_close=true)
quantity = input.float(2.0, "Quantity")
var array<int> counts = array.new<int>(6, 0)
var array<float> sums = array.new<float>(6, 0.0)
record(array<int> events, array<float> totals, int slot, float value) =>
    array.set(events, slot, array.get(events, slot) + 1)
    array.set(totals, slot, array.get(totals, slot) + value)
    value
'''
    calls = (
        ("entry", (
            ("id", '"L"'), ("direction", "strategy.long"),
            ("qty", "record(counts, sums, 0, quantity)"),
        )),
        ("order", (
            ("id", '"O"'), ("direction", "strategy.long"),
            ("qty", "record(counts, sums, 1, quantity + 1)"),
        )),
        ("exit", (
            ("id", '"X"'), ("from_entry", '"L"'), ("qty", "na"),
            ("qty_percent", "100"),
            ("profit", "record(counts, sums, 2, quantity + 3)"),
        )),
        ("close", (
            ("id", '"L"'), ("comment", '""'),
            ("qty", "record(counts, sums, 3, quantity)"),
        )),
        ("close_all", (
            ("comment", '""'), ("alert_message", '""'),
            ("immediately", "record(counts, sums, 4, close) > 0"),
        )),
        ("cancel", (
            ("id", "str.tostring(record(counts, sums, 5, close))"),
        )),
        ("cancel_all", ()),
    )
    source += "\n".join(
        _call(name, _arguments_for_form(parameters, form))
        for name, parameters in calls
    ) + "\n"
    prices = (11.0, 17.0, 13.0, 19.0, 23.0)
    quantities = (1.25, 3.5)
    driver = r'''
#include <iostream>
#include <vector>
int main() {
    for (const char* quantity : {QUANTITIES}) {
        GeneratedStrategy strategy;
        strategy_set_input(&strategy, "Quantity", quantity);
        std::vector<Bar> bars;
        for (double price : {PRICES}) {
            const auto time = 1577836800000LL
                + static_cast<long long>(bars.size()) * 60000LL;
            bars.push_back({price, price + 1, price - 1, price, 1, time});
        }
        strategy.run(bars.data(), static_cast<int>(bars.size()));
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 2;
        }
        for (int count : strategy.counts) std::cout << count << " ";
        std::cout << "|";
        for (double sum : strategy.sums) std::cout << sum << " ";
        std::cout << "\n";
    }
}
'''
    driver = driver.replace(
        "QUANTITIES", ", ".join(f'"{value}"' for value in quantities),
    ).replace("PRICES", ", ".join(str(price) for price in prices))
    rows = run_emitted_tu(
        transpile(source), driver, opt="-O0", label=f"order-keywords-{form}",
    ).splitlines()
    assert len(rows) == len(quantities)
    for row, quantity in zip(rows, quantities):
        counts, sums = row.split("|")
        assert [int(value) for value in counts.split()] == [len(prices)] * 6
        assert [float(value) for value in sums.split()] == [
            quantity * len(prices), (quantity + 1) * len(prices),
            (quantity + 3) * len(prices), quantity * len(prices),
            sum(prices), sum(prices),
        ]
