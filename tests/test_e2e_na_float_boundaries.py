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
        "method identity(float item, float other) => item, other\n"
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


_INPUT_HISTORY_SOURCE = (
    '//@version=6\nstrategy("Input-sensitive missing history", currency=currency.USDT, '
    "initial_capital=1000000000, default_qty_type=strategy.fixed)\n"
    'gapPeriod = input.int(4, "Gap period", minval=2, maxval=8)\n'
    'gapPhase = input.int(1, "Gap phase", minval=0, maxval=7)\n'
    'valueShift = input.int(-7, "Value shift")\n'
    'historyDepth = input.int(1, "History depth", minval=0, maxval=4)\n'
    "acceptHistory(float item) => item\n"
    "int counter = bar_index + valueShift\n"
    "if bar_index % gapPeriod == gapPhase\n"
    "    counter := int(na)\n"
    "float declaredValue = counter[historyDepth]\n"
    "float assignedValue = na\n"
    "assignedValue := counter[historyDepth]\n"
    "var float retainedValue = na\n"
    "retainedValue := counter[historyDepth]\n"
    "float branchValue = 37.25\n"
    "if bar_index % 2 == 0\n"
    "    branchValue := counter[historyDepth]\n"
    "float argumentValue = acceptHistory(counter[historyDepth])\n"
    "items = array.new<float>(1, 37.25)\n"
    "items.set(0, counter[historyDepth])\n"
    "float collectionValue = items.get(0)\n"
    "float loopValue = 0.5\n"
    "for step = 0 to 1\n"
    "    loopValue += counter[historyDepth]\n"
    "float arithmeticValue = counter[historyDepth] + 1\n"
    "float explicitControl = na(counter[historyDepth]) ? na : counter[historyDepth]\n"
    "encoded = na(assignedValue) ? 1.0 : 2.0 + assignedValue / 100.0\n"
    "if bar_index % 3 == 0\n"
    '    strategy.entry("sample", strategy.long, qty=encoded)\n'
    "else if strategy.position_size != 0\n"
    "    strategy.close_all()\n"
    "// @pf-trace declaration=declaredValue\n"
    "// @pf-trace assignment=assignedValue\n"
    "// @pf-trace persistent=retainedValue\n"
    "// @pf-trace branch=branchValue\n"
    "// @pf-trace argument=argumentValue\n"
    "// @pf-trace collection=collectionValue\n"
    "// @pf-trace loop=loopValue\n"
    "// @pf-trace arithmetic=arithmeticValue\n"
    "// @pf-trace control=explicitControl\n"
    "// @pf-trace missing=na(assignedValue) ? 1.0 : 0.0\n"
    "// @pf-trace fallback=nz(assignedValue, 37.25)\n"
    "// @pf-trace comparison=assignedValue < 5 ? 1.0 : 0.0\n"
)


def test_history_controls_consume_input_overrides(tmp_path):
    """Native witness only; TradingView evidence for this new source: NONE."""
    engine = skip_unless_e2e_env()
    feed = tmp_path / "hourly.csv"
    feed.write_text(
        "timestamp,open,high,low,close,volume\n"
        + "".join(
            f"{1704067200000 + bar * 3600000},100,101,99,100,10\n"
            for bar in range(13)
        ),
        encoding="utf-8",
    )
    result = ok(execute_all(engine, feed, tmp_path, {
        "history": Build(_INPUT_HISTORY_SOURCE, trace=True, overrides={
            "Gap period": 3, "Gap phase": 0, "Value shift": 11, "History depth": 3,
        }),
    }), "history")
    observed_values = {}
    for tag, period, phase, shift, depth in (
        ("default", 4, 1, -7, 1),
        ("override", 3, 0, 11, 3),
    ):
        # This oracle comes from the authored input schedule, including holes
        # after bar zero; it does not use emitted expressions or reference C++.
        expected = []
        for bar in range(13):
            previous = bar - depth
            expected.append(
                math.nan if previous < 0 or previous % period == phase
                else float(previous + shift)
            )
        traces = result.traces[tag]
        expected_traces = {
            name: expected
            for name in (
                "declaration", "assignment", "persistent", "argument",
                "collection", "control",
            )
        }
        expected_traces.update({
            "branch": [value if bar % 2 == 0 else 37.25
                       for bar, value in enumerate(expected)],
            "loop": [0.5 + 2.0 * value for value in expected],
            "arithmetic": [value + 1.0 for value in expected],
            "missing": [1.0 if math.isnan(value) else 0.0 for value in expected],
            "fallback": [37.25 if math.isnan(value) else value for value in expected],
            "comparison": [1.0 if value < 5.0 else 0.0 for value in expected],
        })
        for name, values in expected_traces.items():
            actual = [row["value"] for row in traces if row["name"] == name]
            assert len(actual) == len(values), (tag, name, actual)
            for bar, (got, want) in enumerate(zip(actual, values)):
                if math.isnan(want):
                    assert math.isnan(got), (tag, name, bar, got)
                else:
                    assert got == want, (tag, name, bar, got, want)
        observed_values[tag] = [
            row["value"] for row in traces if row["name"] == "assignment"
        ]
        quantities = [
            1.0 if math.isnan(expected[bar]) else 2.0 + expected[bar] / 100.0
            for bar in range(0, 12, 3)
        ]
        rows = _entries(result.trades[tag])
        assert [float(row["Qty"]) for row in rows] == pytest.approx(
            quantities, abs=0.00001, rel=0,
        )
    # Bar 1 is finite at defaults and missing after the depth override; bar 4
    # is finite in both runs and must consume the changed numeric input too.
    assert observed_values["default"][1] == -7.0
    assert math.isnan(observed_values["override"][1])
    assert observed_values["default"][4] == -4.0
    assert observed_values["override"][4] == 12.0


# TV-safe input-derived probe. There is no TradingView tape
# for this source yet. The native oracle below comes from the input schedule.
_FINITE_RESULT_INPUT_SOURCE = """//@version=6
strategy("Finite integer result probe", currency=currency.USDT, initial_capital=1000000000, default_qty_type=strategy.fixed)
sourceSeed = input.int(-2147483647, "Source seed")
decrement = input.int(1, "Decrement")
holePeriod = input.int(4, "Hole period", minval=2, maxval=8)
holePhase = input.int(1, "Hole phase", minval=0, maxval=7)
int sourceValue = sourceSeed
if bar_index % holePeriod == holePhase
    sourceValue := int(na)
float promotedControl = float(sourceValue) - float(decrement)
float observedValue = sourceValue - decrement
sameMissing = na(observedValue) == na(promotedControl)
sameValue = na(promotedControl) or observedValue == promotedControl
qty = sameMissing and sameValue ? (na(promotedControl) ? 1.0 : 2.0) : 3.0
if bar_index % 2 == 0
    strategy.entry("sample", strategy.long, qty=qty)
else
    strategy.close_all()
plot(observedValue, "Candidate")
plot(promotedControl, "Promoted control")
// @pf-trace candidate=observedValue
// @pf-trace control=promotedControl
"""


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_native_input_derived_finite_result_capture_binding(opt):
    """Bind the exact capture source to int storage and consumed native bars."""
    from pineforge_codegen import transpile
    from tests._compile import run_emitted_tu

    driver = r"""
#include <cmath>
#include <cstdio>
#include <type_traits>

int main() {
    pineforge::Bar bars[9];
    for (unsigned index = 0; index < 9; ++index) {
        bars[index] = pineforge::Bar{
            100.0, 101.0, 99.0, 100.0, 10.0,
            1704067200000LL + index * 60000LL,
        };
    }
    for (unsigned count = 1; count <= 9; ++count) {
        GeneratedStrategy strategy;
        static_assert(std::is_same_v<decltype(strategy.sourceValue), int>);
        strategy.run(bars, count);
        if (!strategy.last_error().empty()) return 1;
        const bool missing = (count - 1) % 4 == 1;
        if (pineforge::is_na(strategy.sourceValue) != missing) return 2;
        if (!missing && strategy.sourceValue != -2147483647) return 3;
        const double expected = -2147483648.0;
        const double values[] = {strategy.observedValue, strategy.promotedControl};
        for (double value : values) {
            if (missing ? !std::isnan(value) : value != expected) {
                std::printf("bar=%u missing=%d expected=%.17g actual=%.17g\n",
                            count - 1, missing, expected, value);
                return 4;
            }
        }
    }
    std::puts("ok");
}
"""
    assert run_emitted_tu(
        transpile(_FINITE_RESULT_INPUT_SOURCE), driver, opt=opt,
        label="input-derived-finite-result-capture",
    ) == "ok\n"


def test_input_derived_finite_result_probe_and_missing_control(tmp_path):
    """Native binding of the capture source; TradingView evidence: NONE."""
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 13)
    result = ok(execute_all(engine, feed, tmp_path, {
        "finite": Build(_FINITE_RESULT_INPUT_SOURCE, trace=True, overrides={
            "Source seed": -1073741824, "Decrement": 3,
            "Hole period": 3, "Hole phase": 0,
        }),
    }), "finite")
    for tag, seed, decrement, period, phase in (
        ("default", -2147483647, 1, 4, 1),
        ("override", -1073741824, 3, 3, 0),
    ):
        expected = [
            math.nan if bar % period == phase else float(seed - decrement)
            for bar in range(13)
        ]
        for name in ("candidate", "control"):
            actual = [
                row["value"] for row in result.traces[tag] if row["name"] == name
            ]
            assert len(actual) == len(expected), (tag, name, actual)
            for bar, (got, want) in enumerate(zip(actual, expected)):
                if math.isnan(want):
                    assert math.isnan(got), (tag, name, bar, got)
                else:
                    assert math.isfinite(got) and got == want, (
                        tag, name, bar, got, want,
                    )
        rows = _entries(result.trades[tag])
        assert [float(row["Qty"]) for row in rows] == [
            1.0 if math.isnan(expected[bar]) else 2.0
            for bar in range(0, 12, 2)
        ]
