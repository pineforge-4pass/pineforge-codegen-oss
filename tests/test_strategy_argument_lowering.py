"""Strategy arguments retain omitted, missing, and input-dependent values."""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp


_HEADER = """//@version=6
strategy('repro', default_qty_type = strategy.percent_of_equity, default_qty_value = 10)
fast = ta.ema(close, 9)
slow = ta.ema(close, 21)
"""
_ENTRIES = """if ta.crossover(fast, slow)
    strategy.entry('Long', strategy.long, qty = q)
if ta.crossunder(fast, slow)
    strategy.entry('Short', strategy.short, qty = q)
"""
_SIZE_INPUTS = """mode = input.string('Default', 'Size', options = ['Default', 'Fixed'])
fixedQty = input.int(1, 'Fixed qty')
"""
_SWITCH = """q = switch mode
    'Fixed' => fixedQty
    => na
"""
_TERNARY = "q = mode == 'Fixed' ? fixedQty : na\n"
_RISK_INPUT = "dir = input.string('Long', 'Direction', options = ['Long', 'Short'])\n"
_RISK_ENTRIES = _ENTRIES.replace(", qty = q", "")


def _calls(cpp: str, name: str) -> list[str]:
    return re.findall(rf"^\s*{name}\((.*)\);$", cpp, re.MULTILINE)


def _order_pair(long_id="Long", short_id="Short") -> tuple[str, str]:
    body = (
        "if ta.crossover(fast, slow)\n"
        f"    strategy.order('{long_id}', strategy.long)\n"
        "if ta.crossunder(fast, slow)\n"
        f"    strategy.order('{short_id}', strategy.short)\n"
    )
    control = body.replace(
        "strategy.long)", "strategy.long, qty = na)",
    ).replace("strategy.short)", "strategy.short, qty = na)")
    return _HEADER + body, _HEADER + control


def _quantity_pair(selection=_SWITCH) -> tuple[str, str]:
    prefix = _HEADER + _SIZE_INPUTS
    return prefix + selection + _ENTRIES, prefix + _TERNARY + _ENTRIES


def _risk_pair() -> tuple[str, str]:
    prefix = _HEADER + _RISK_INPUT
    return (
        prefix + "strategy.risk.allow_entry_in("
        "dir == 'Long' ? strategy.direction.long : strategy.direction.short)\n"
        + _RISK_ENTRIES,
        prefix + "strategy.risk.allow_entry_in(strategy.direction.long)\n"
        + _RISK_ENTRIES,
    )


def _assert_order_pair(cpp: str, control: str) -> None:
    calls = _calls(cpp, "strategy_order")
    control_calls = _calls(control, "strategy_order")
    assert len(calls) == len(control_calls) == 2
    assert [call.split(", ")[2] for call in calls] == ["na<double>()"] * 2
    assert calls == control_calls


def _assert_quantity_pair(cpp: str, control: str) -> None:
    calls = _calls(cpp, "strategy_entry")
    control_calls = _calls(control, "strategy_entry")
    assert len(calls) == len(control_calls) == 2
    quantity, = {call.split(", ")[4] for call in calls}
    assert re.search(rf"^\s*double {quantity} = 0\.0;$", cpp, re.MULTILINE)
    assert re.search(rf"^\s*{quantity} = na<double>\(\);$", cpp, re.MULTILINE)
    assert f"{quantity} = na<int>();" not in cpp
    control_quantity, = {call.split(", ")[4] for call in control_calls}
    assert re.search(rf"^\s*double {control_quantity} = 0\.0;$", control, re.MULTILINE)
    assert ": (na<double>())" in control
    assert calls == control_calls


def _assert_risk_pair(cpp: str, control: str) -> None:
    # The bare constant is lowered to its signed value (long: 1). The input
    # selection is lowered by its value as well, so it is not the constant
    # "all directions" (0) that an argument which is not the bare constant used
    # to get; the entries each lowering admits are held to the bare literal's
    # by value in tests/test_e2e_risk_direction_values.py.
    argument, = _calls(cpp, "set_pine_risk_direction")
    control_argument, = _calls(control, "set_pine_risk_direction")
    assert control_argument == "1"
    assert argument != "0"
    # The input is read before the call that uses it, in the same bar.
    on_bar = cpp[cpp.index("void on_source_bar("):]
    assert on_bar.index("dir = get_input_string(") < on_bar.index(
        "set_pine_risk_direction(")


@pytest.mark.parametrize("long_id,short_id", [("Long", "Short"), ("Buy", "Sell")])
def test_order_omitted_quantity_matches_explicit_na(long_id, short_id):
    source, control = _order_pair(long_id, short_id)
    cpp = transpile(source)
    _assert_order_pair(cpp, transpile(control))
    # Zero remains a written zero; it is not the default-size sentinel.
    zero = transpile(control.replace("qty = na", "qty = 0"))
    assert [call.split(", ")[2] for call in _calls(zero, "strategy_order")] == ["0"] * 2


@pytest.mark.parametrize("selection", [
    _SWITCH,
    "q = switch mode\n    'Default' => na\n    => fixedQty\n",
    "q = switch\n    mode == 'Fixed' => fixedQty\n    => na\n",
    "q = if mode == 'Fixed'\n    fixedQty\nelse\n    na\n",
    "q = if mode == 'Default'\n    na\nelse\n    fixedQty\n",
])
def test_integer_or_na_quantity_matches_ternary(selection):
    source, control = _quantity_pair(selection)
    _assert_quantity_pair(transpile(source), transpile(control))


@pytest.mark.parametrize("qualifier", ["int ", "var ", "var int ", "varip ", "varip int "])
@pytest.mark.parametrize("selection", [
    _SWITCH,
    "q = if mode == 'Fixed'\n    fixedQty\nelse\n    na\n",
])
def test_nullable_selection_declaration_storage(qualifier, selection):
    source, _control = _quantity_pair(qualifier + selection)
    cpp = transpile(source)
    calls = _calls(cpp, "strategy_entry")
    assert len(calls) == 2
    quantity, = {call.split(", ")[4] for call in calls}
    initial = r"na<double>\(\)" if qualifier.startswith("var") else r"0\.0"
    assert re.search(
        rf"^\s*double {quantity} = {initial};$", cpp, re.MULTILINE,
    )
    assert re.search(rf"^\s*{quantity} = fixedQty;$", cpp, re.MULTILINE)
    assert re.search(rf"^\s*{quantity} = na<double>\(\);$", cpp, re.MULTILINE)
    assert f"{quantity} = na<int>();" not in cpp


@pytest.mark.parametrize("qualifier", ["", "int "])
@pytest.mark.parametrize("integer_arm", ["fixedQty", "units"])
def test_callable_nullable_selection_declaration_and_reassignment(qualifier, integer_arm):
    source = _HEADER + _SIZE_INPUTS + (
        "read_quantity(int units) =>\n"
        f"    {qualifier}amount = switch mode\n"
        f"        'Fixed' => {integer_arm}\n"
        "        => na\n"
        "    amount := if mode == 'Fixed'\n"
        f"        {integer_arm}\n"
        "    else\n"
        "        na\n"
        "    na(amount) ? 1.0 : amount\n"
        "q = read_quantity(fixedQty)\n"
    ) + _ENTRIES
    cpp = transpile(source)
    assert re.search(r"^\s*double amount = 0\.0;$", cpp, re.MULTILINE)
    assert len(re.findall(
        r"^\s*amount = na<double>\(\);$", cpp, re.MULTILINE,
    )) >= 2
    assert "amount = na<int>();" not in cpp


@pytest.mark.parametrize("selection", [
    "q = switch mode\n    'Fixed' => fixedQty\n    => 2\n",
    "q = if mode == 'Fixed'\n    fixedQty\nelse\n    2\n",
    "q = switch mode\n    'Fixed' => fixedQty\n",
    "q = if mode == 'Fixed'\n    fixedQty\n",
])
def test_integer_selection_without_explicit_na_keeps_integer_storage(selection):
    source, _control = _quantity_pair(selection)
    cpp = transpile(source)
    assert re.search(r"^\s*int q = 0;$", cpp, re.MULTILINE)
    assert re.search(r"^\s*q = fixedQty;$", cpp, re.MULTILINE)
    assert "double q = " not in cpp


# The eight input-direction shapes (each pair of constants, each default) that
# a text pin of the emitted call used to cover are run, with the input at its
# default and overridden, against the bare literal of the side they select in
# tests/test_e2e_risk_direction_values.py.


@pytest.mark.parametrize("pair,check", [
    (_order_pair(), _assert_order_pair),
    (_quantity_pair(), _assert_quantity_pair),
    (_risk_pair(), _assert_risk_pair),
], ids=["default-order-quantity", "nullable-switch-quantity", "input-risk-direction"])
def test_issue_arguments_compile_with_their_controls(pair, check):
    source, control_source = pair
    cpp, control = transpile(source), transpile(control_source)
    # Assert before the optional native harness so the base fails on emission.
    check(cpp, control)
    compile_cpp(cpp, label="strategy argument")
    compile_cpp(control, label="strategy argument control")
