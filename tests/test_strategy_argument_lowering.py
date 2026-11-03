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
    argument, = _calls(cpp, "set_pine_risk_direction")
    control_argument, = _calls(control, "set_pine_risk_direction")
    # Read the condition and both signed enum values from the emitted call.
    selected = re.fullmatch(
        r'\(\(\((\w+) == std::string\(("(?:[^"\\]|\\.)*")\)\)\)'
        r" \? \((-?\d+)\) : \((-?\d+)\)\)",
        argument,
    )
    assert selected is not None, argument
    name, comparison, true_value, false_value = selected.groups()
    default = re.search(
        rf'{name} = get_input_string\("(?:[^"\\]|\\.)*", '
        r'std::string\(("(?:[^"\\]|\\.)*")\)\);',
        cpp,
    )
    assert default is not None
    # The default input chooses the same signed value as the literal control.
    assert (true_value if default[1] == comparison else false_value) == control_argument
    on_bar = cpp[cpp.index("void on_source_bar("):]
    assert on_bar.index(f"{name} = get_input_string(") < on_bar.index(
        f"set_pine_risk_direction({argument});",
    )


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


@pytest.mark.parametrize("true_direction,false_direction", [
    ("long", "short"), ("short", "long"), ("long", "all"), ("all", "short"),
])
@pytest.mark.parametrize("default", ["Long", "Short"])
def test_input_direction_retains_both_runtime_arms(true_direction, false_direction, default):
    source, _control = _risk_pair()
    source = source.replace(
        "strategy.direction.long : strategy.direction.short",
        f"strategy.direction.{true_direction} : strategy.direction.{false_direction}",
    ).replace("input.string('Long'", f"input.string('{default}'")
    prefix = _HEADER + _RISK_INPUT.replace("input.string('Long'", f"input.string('{default}'")
    chosen = true_direction if default == "Long" else false_direction
    control = prefix + f"strategy.risk.allow_entry_in(strategy.direction.{chosen})\n" + _RISK_ENTRIES
    cpp = transpile(source)
    _assert_risk_pair(cpp, transpile(control))
    argument, = _calls(cpp, "set_pine_risk_direction")
    for direction, arm in ((true_direction, "?"), (false_direction, ":")):
        literal = transpile(prefix + f"strategy.risk.allow_entry_in(strategy.direction.{direction})\n")
        value, = _calls(literal, "set_pine_risk_direction")
        assert f"{arm} ({value})" in argument


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
