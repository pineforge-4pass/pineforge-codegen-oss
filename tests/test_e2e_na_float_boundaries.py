"""Missing integers retain missingness at floating-point destinations."""

from __future__ import annotations

import csv
import io
import math
from pathlib import Path

import pytest

from tests._e2e import (
    Build, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)


FIXTURES = Path(__file__).parent / "fixtures" / "na_float_boundaries"

# TradingView entry quantities encode missing as 1 and finite values above 2.
QUANTITIES = {
    "selection": [1.0, 2.002, 2.005, 2.008],
    "selection_control": [1.0, 2.002, 2.005, 2.008],
    "selection_renamed": [1.0, 2.002, 2.005, 2.008],
    "plain": [1.0, 2.002, 2.005, 2.008],
    "plain_renamed": [1.0, 2.002, 2.005, 2.008],
    "persistent": [1.0, 2.002, 2.005, 2.008],
    "arithmetic": [1.0, 2.003, 2.006, 2.009],
    "condition": [1.0],
}


def _entries(blob: bytes) -> list[dict[str, str]]:
    rows = [
        row for row in csv.DictReader(io.StringIO(blob.decode()))
        if row["Type"].startswith("Entry")
    ]
    # The CSV lists the newest trade first; expectations follow entry order.
    return sorted(rows, key=lambda row: int(row["Trade #"]))


@pytest.fixture(scope="module")
def boundary_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("na_float")
    feed = chart_feed_head(engine, base, 12)
    builds = {
        name: Build((FIXTURES / f"{name}.pine").read_text(encoding="utf-8"))
        for name in QUANTITIES
    }
    return execute_all(engine, feed, base, builds)


@pytest.mark.parametrize("name", QUANTITIES)
def test_integer_missing_entry_quantities(boundary_runs, name):
    rows = _entries(ok(boundary_runs, name).trades["default"])
    assert [float(row["Qty"]) for row in rows] == pytest.approx(
        QUANTITIES[name], abs=0.00001, rel=0,
    )
    if name == "condition":
        # The missing comparison is false; the first order comes from bar 3.
        control = _entries(ok(boundary_runs, "plain").trades["default"])
        assert len(rows) == 1
        assert rows[0]["Date and time"] == control[1]["Date and time"]


@pytest.mark.parametrize("name,value", [
    ("reassign", "a"), ("conditional_reassign", "a"),
    ("arithmetic_decl", "w"), ("persistent_reassign", "x"),
])
def test_reduced_missing_writes(tmp_path, name, value):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 12)
    source = (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")
    source += f"\n// @pf-trace missing=na({value}) ? 1.0 : 0.0\n"
    source += f"// @pf-trace value={value}\n"
    if name != "conditional_reassign":
        source += (
            f"reducedQty = na({value}) ? 1.0 : 2.0 + {value} / 1000.0\n"
            "if bar_index % 3 == 0\n"
            '    strategy.entry("sample", strategy.long, qty=reducedQty)\n'
        )
    source += (
        "if bar_index % 3 != 0 and strategy.position_size != 0\n"
        "    strategy.close_all()\n"
    )
    runs = execute_all(engine, feed, tmp_path, {"value": Build(source, trace=True)})
    records = ok(runs, "value").traces["default"]
    missing = [r["value"] for r in records if r["name"] == "missing"]
    expected_missing = (
        [1.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
        if name == "conditional_reassign"
        else [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    )
    assert missing[:8] == expected_missing
    values = [r["value"] for r in records if r["name"] == "value"]
    expected = 3.0 if name == "arithmetic_decl" else 2.0
    assert values[3] == expected
    rows = _entries(ok(runs, "value").trades["default"])
    expected_qty = (
        [1.0, 2.003, 2.006, 2.009]
        if name == "arithmetic_decl" else [1.0, 2.002, 2.005, 2.008]
    )
    assert [float(row["Qty"]) for row in rows] == pytest.approx(
        expected_qty, abs=0.00001, rel=0,
    )


DESTINATIONS = {
    "declaration": "float value = counter[1]\n",
    "series_declaration": "float value = counter[1]\nplot(value[1])\n",
    "history_if": (
        "float value = na\n"
        "value := if bar_index % 3 == 0\n    counter[1]\nelse\n    5.375\n"
        "plot(value[1])\n"
    ),
    "history_switch": (
        "float value = na\n"
        "value := switch\n    bar_index % 3 == 0 => counter[1]\n    => 5.375\n"
        "plot(value[1])\n"
    ),
    "compound_add": "float value = 17.5\nvalue += counter[1]\n",
    "compound_subtract": "float value = 17.5\nvalue -= counter[1]\n",
    "compound_multiply": "float value = 17.5\nvalue *= counter[1]\n",
    "compound_divide": "float value = 17.5\nvalue /= counter[1]\n",
    "compound_modulo": "float value = 17.5\nvalue %= counter[1]\n",
    "compound_series": "float value = 17.5\nvalue += counter[1]\nplot(value[1])\n",
    "persistent_init": "var float value = counter[1]\n",
    "persistent_history_init": "var float value = counter[1]\nplot(value[1])\n",
    "intrabar_init": "varip float value = counter[1]\n",
    "loop_for": (
        "float value = 0.5\nfor step = 0 to 1\n"
        "    value += counter[1]\n"
    ),
    "loop_while": (
        "float value = 0.5\nint step = 0\nwhile step < 2\n"
        "    value += counter[1]\n    step += 1\n"
    ),
    "loop_each": (
        "items = array.new<int>(2, counter[1])\nfloat value = 0.5\n"
        "for item in items\n    value += item\n"
    ),
    "ternary": "float value = bar_index % 3 == 0 ? counter[1] : 5.375\n",
    "switch": (
        "float value = switch\n    bar_index % 3 == 0 => counter[1]\n"
        "    => 5.375\n"
    ),
    "return_selection": (
        "choose(int item, bool gate) =>\n"
        "    if gate\n        item\n    else\n        5.375\n"
        "float value = choose(counter[1], true)\n"
    ),
    "return_integer": "identity(int item) => item\nfloat value = identity(counter[1])\n",
    "shadowed_integer": (
        "var float shared = 0.0\nreadCode(int item) =>\n"
        "    int shared = item\n    shared := item\n    shared\n"
        "float value = readCode(counter[1])\n"
    ),
    "shadowed_float": (
        "var int shared = 0\nreadCode(int item) =>\n"
        "    float shared = na\n    shared := item\n    shared\n"
        "float value = readCode(counter[1])\n"
    ),
    "argument": "identity(float item) => item\nfloat value = identity(counter[1])\n",
    "argument_keyword": "identity(float item) => item\nfloat value = identity(item=counter[1])\n",
    "argument_method": (
        "method identity(float item, float other) => other\n"
        "float value = close.identity(counter[1])\n"
    ),
    "tuple": (
        "pair(int item) =>\n    if bar_index % 3 == 0\n"
        "        [item, 13]\n    else\n        [6.375, 29]\n"
        "[first, tag] = pair(counter[1])\nfloat value = first\n"
    ),
    "cast": "float value = float(counter[1])\n",
    "math": "float value = math.max(counter[1], -40.0)\n",
    "ta_source": "float value = ta.sma(counter[1], 2)\n",
    "array_init": (
        "items = array.new<float>(1, counter[1])\n"
        "float value = array.get(items, 0)\n"
    ),
    "array_set": (
        "items = array.new<float>(1, 0.0)\narray.set(items, 0, counter[1])\n"
        "float value = array.get(items, 0)\n"
    ),
    "array_push": (
        "items = array.new<float>()\narray.push(items, counter[1])\n"
        "float value = array.get(items, 0)\n"
    ),
    "matrix_init": (
        "grid = matrix.new<float>(1, 1, counter[1])\n"
        "float value = matrix.get(grid, 0, 0)\n"
    ),
    "matrix_set": (
        "grid = matrix.new<float>(1, 1, 0.0)\nmatrix.set(grid, 0, 0, counter[1])\n"
        "float value = matrix.get(grid, 0, 0)\n"
    ),
    "map_put": (
        "lookup = map.new<string, float>()\nmap.put(lookup, \"key\", counter[1])\n"
        "float value = map.get(lookup, \"key\")\n"
    ),
    "field": (
        "type Cell\n    float amount\ncell = Cell.new(0.0)\n"
        "cell.amount := counter[1]\nfloat value = cell.amount\n"
    ),
    "request": (
        "float value = request.security(syminfo.tickerid, timeframe.period, counter[1])\n"
    ),
}


@pytest.mark.parametrize("name", DESTINATIONS)
def test_numeric_destination_paths(tmp_path, name):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 12)
    source = (
        '//@version=6\nstrategy("Numeric destinations", currency=currency.USDT, '
        "initial_capital=1000000000, default_qty_type=strategy.fixed)\n"
        "int counter = bar_index\n" + DESTINATIONS[name]
        + "encoded = na(value) ? 1.0 : 2.0 + value / 100.0\n"
        "if bar_index % 3 == 0\n"
        '    strategy.entry("sample", strategy.long, qty=encoded)\n'
        "else if strategy.position_size != 0\n    strategy.close_all()\n"
    )
    if name == "ta_source":
        source += "// @pf-trace value=value\n"
    runs = execute_all(engine, feed, tmp_path, {
        "value": Build(source, trace=name == "ta_source"),
    })
    rows = _entries(ok(runs, "value").trades["default"])
    expected = [1.0, 2.02, 2.05, 2.08]
    if name in ("persistent_init", "persistent_history_init", "intrabar_init"):
        expected = [1.0, 1.0, 1.0, 1.0]
    elif name in ("loop_for", "loop_while", "loop_each"):
        expected = [1.0, 2.045, 2.105, 2.165]
    elif name == "ta_source":
        expected = [1.0, 2.015, 2.045, 2.075]
    elif name in ("compound_add", "compound_series"):
        expected = [1.0, 2.195, 2.225, 2.255]
    elif name == "compound_subtract":
        expected = [1.0, 2.155, 2.125, 2.095]
    elif name == "compound_multiply":
        expected = [1.0, 2.35, 2.875, 3.4]
    elif name == "compound_divide":
        expected = [1.0, 2.0875, 2.035, 2.021875]
    elif name == "compound_modulo":
        expected = [1.0, 2.015, 2.025, 2.015]
    assert [float(row["Qty"]) for row in rows] == pytest.approx(
        expected, abs=0.00001, rel=0,
    )
    if name == "ta_source":
        values = [
            r["value"] for r in ok(runs, "value").traces["default"]
            if r["name"] == "value"
        ]
        assert math.isnan(values[0]) and math.isnan(values[1])
        assert values[2] == 0.5


@pytest.mark.parametrize("expression,expected", [
    ("counter[1] + 1", [1.0, 2.03, 2.06, 2.09]),
    ("7 - counter[1]", [1.0, 2.05, 2.02, 1.99]),
    ("counter[1] * 3", [1.0, 2.06, 2.15, 2.24]),
    ("counter[1] / 8", [1.0, 2.0025, 2.00625, 2.01]),
    ("counter[1] % 11", [1.0, 2.02, 2.05, 2.08]),
    ("-counter[1]", [1.0, 1.98, 1.95, 1.92]),
    ("counter[1] + 0.375", [1.0, 2.02375, 2.05375, 2.08375]),
])
def test_missing_arithmetic_operands(tmp_path, expression, expected):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 12)
    source = (
        '//@version=6\nstrategy("Numeric arithmetic", currency=currency.USDT, '
        "initial_capital=1000000000, default_qty_type=strategy.fixed)\n"
        f"int counter = bar_index\nfloat value = {expression}\n"
        "encoded = na(value) ? 1.0 : 2.0 + value / 100.0\n"
        "if bar_index % 3 == 0\n"
        '    strategy.entry("sample", strategy.long, qty=encoded)\n'
        "else if strategy.position_size != 0\n    strategy.close_all()\n"
    )
    runs = execute_all(engine, feed, tmp_path, {"value": Build(source)})
    rows = _entries(ok(runs, "value").trades["default"])
    assert [float(row["Qty"]) for row in rows] == pytest.approx(
        expected, abs=0.00001, rel=0,
    )


# Native expectations retain zero, sign, fractional writes and epoch depth.
SUPPLEMENTAL_QUANTITIES = {
    "fractional_depth1_signed": [1.9325, 1.9625, 1.9925, 2.0225],
    "fractional_depth0_zero": [2.0125, 2.0425, 2.0725, 2.1025],
    "fractional_depth0_signed": [1.9425, 1.9725, 2.0025, 2.0325],
    "fractional_depth3_signed": [1.0, 1.9425, 1.9725, 2.0025],
    "fractional_depth7_signed": [1.0, 1.0, 1.9325, 1.9625],
    "divide_zero": [1.0, 2.05833333, 2.02916667, 2.01944444],
    "divide_signed": [1.975, 1.95625, 1.825, 2.0875],
    "modulo_zero": [1.0, 2.025, 2.055, 2.085],
    "modulo_signed": [2.035, 2.015, 2.005, 2.015],
    "recurrence_zero": [2.175, 2.235, 2.385, 2.625],
    "recurrence_signed": [2.105, 1.955, 1.895, 1.925],
    "epoch_depth0": [2.0, 2.03, 2.06, 2.09],
    "epoch_depth3": [1.0, 2.0, 2.03, 2.06],
    "epoch_depth7": [1.0, 1.0, 1.0, 2.02],
    "divide_signed_phase1": [1.97083333, 1.94166667, 1.0, 2.05833333],
    "modulo_signed_phase1": [2.055, 2.025, 1.0, 2.025],
}


@pytest.fixture(scope="module")
def supplemental_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("numeric_values")
    feed = base / "hourly.csv"
    feed.write_text(
        "timestamp,open,high,low,close,volume\n"
        + "".join(
            f"{1704067200000 + bar * 3600000},100,101,99,100,10\n"
            for bar in range(13)
        ),
        encoding="utf-8",
    )
    return execute_all(engine, feed, base, {
        name: Build(
            (FIXTURES / f"{name}.pine").read_text(encoding="utf-8"), trace=True,
        )
        for name in SUPPLEMENTAL_QUANTITIES
    })


@pytest.mark.parametrize("name", SUPPLEMENTAL_QUANTITIES)
def test_numeric_depth_and_signed_controls(supplemental_runs, name):
    result = ok(supplemental_runs, name)
    rows = _entries(result.trades["default"])
    assert [float(row["Qty"]) for row in rows] == pytest.approx(
        SUPPLEMENTAL_QUANTITIES[name], abs=0.00001, rel=0,
    )
    zero_division_bars = {"divide_zero": 0, "divide_signed_phase1": 7}
    if name in zero_division_bars:
        values = [r["value"] for r in result.traces["default"] if r["name"] == "current"]
        # Infinity keeps its arithmetic value and selects the declared default
        # order quantity; converting the result to NaN would hide that boundary.
        assert values[zero_division_bars[name]] == math.inf
    if name.startswith("fractional"):
        values = [r["value"] for r in result.traces["default"] if r["name"] == "current"]
        if name == "fractional_depth1_signed":
            assert values[1] == -6.75
        elif name == "fractional_depth0_zero":
            assert values[1] == 1.25
        elif name == "fractional_depth0_signed":
            assert values[1] == -5.75
        else:
            assert math.isnan(values[1])
    if name.startswith("epoch"):
        values = [r["value"] for r in result.traces["default"] if r["name"] == "wide"]
        if name == "epoch_depth0":
            assert values[0] == 1704067200000.0
        else:
            assert math.isnan(values[0])
            assert values[9] == (
                1704088800000.0 if name == "epoch_depth3" else 1704074400000.0
            )


@pytest.mark.parametrize("kind,expected", [
    ("qty", [1.0, 66.0, 69.0, 72.0]),
    ("limit", [1.0, 2.02, 2.05, 2.08]),
])
def test_direct_float_order_arguments(tmp_path, kind, expected):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 12)
    argument = (
        "qty=quantityStamp[1]" if kind == "qty"
        else "qty=encoded, limit=priceStamp[1]"
    )
    source = (
        '//@version=6\nstrategy("Numeric order arguments", currency=currency.USDT, '
        "initial_capital=1000000000, default_qty_type=strategy.fixed, default_qty_value=1)\n"
        "int quantityStamp = bar_index + 64\nint priceStamp = bar_index + 1000000\n"
        "encoded = bar_index == 0 ? 1.0 : 2.0 + (bar_index - 1) / 100.0\n"
        "if bar_index % 3 == 0\n"
        f'    strategy.entry("sample", strategy.long, {argument})\n'
        "else if strategy.position_size != 0\n    strategy.close_all()\n"
    )
    runs = execute_all(engine, feed, tmp_path, {"value": Build(source)})
    rows = _entries(ok(runs, "value").trades["default"])
    assert [float(row["Qty"]) for row in rows] == pytest.approx(
        expected, abs=0.00001, rel=0,
    )


@pytest.mark.parametrize("seed,expected", [
    (0, [2.0, 2.03, 2.06, 2.09]),
    (-7, [1.93, 1.96, 1.99, 2.02]),
])
def test_real_zero_and_negative_integer(tmp_path, seed, expected):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 13)
    source = (
        '//@version=6\nstrategy("Signed numeric writes", currency=currency.USDT, '
        "initial_capital=1000000000, default_qty_type=strategy.fixed)\n"
        f"int counter = bar_index + ({seed})\nfloat value = na\nvalue := counter[1]\n"
        "encoded = na(value) ? 1.0 : 2.0 + value / 100.0\n"
        "if bar_index % 3 == 1\n"
        '    strategy.entry("sample", strategy.long, qty=encoded)\n'
        "else if strategy.position_size != 0\n    strategy.close_all()\n"
    )
    runs = execute_all(engine, feed, tmp_path, {"value": Build(source)})
    rows = _entries(ok(runs, "value").trades["default"])
    assert [float(row["Qty"]) for row in rows] == pytest.approx(
        expected, abs=0.00001, rel=0,
    )
