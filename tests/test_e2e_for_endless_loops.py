"""Native runs reproduce TradingView's loop stops and dead-loop quantities."""

from __future__ import annotations

import csv

import pytest

from pineforge_codegen import transpile
from tests import _compile as compile_env
from tests._e2e import Build, chart_feed_head, execute_all, ok, skip_unless_e2e_env
from tests._endless_for_cases import (
    FIXTURES, LIVE_REACHABILITY_CONTROLS, OMITTED_RUNS, TV, source,
)


STOP_DRIVER = r'''
#include <cassert>
#include <iostream>
int main() {
    Bar bar{10.0, 10.0, 10.0, 10.0, 1.0, 60000};
    GeneratedStrategy strategy;
    strategy.run(&bar, 1, "1", "1");
    assert(strategy.last_error() == "@EXPECTED@");
    assert(strategy.trade_count() == 0);
    std::cout << "stopped before trades";
}
'''


@pytest.mark.parametrize("name", TV["stops"])
def test_used_endless_loop_stops_at_bar_zero_without_trades(name):
    compile_env.skip_if_no_engine_lib()
    pine = (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")
    cpp = transpile(pine)
    # Fail before executing an unchanged translator's endless loop.
    assert "Loop takes too long to execute" in cpp
    expected = TV["stop"]["message"].format(**TV["stop"])
    assert compile_env.run_emitted_tu(
        cpp, STOP_DRIVER.replace("@EXPECTED@", expected), opt="-O2", label=name,
    ) == "stopped before trades"


@pytest.mark.parametrize("name", LIVE_REACHABILITY_CONTROLS)
def test_transitive_order_dependencies_stop_at_bar_zero_without_trades(name):
    compile_env.skip_if_no_engine_lib()
    cpp = transpile(LIVE_REACHABILITY_CONTROLS[name])
    assert "Loop takes too long to execute" in cpp
    expected = TV["stop"]["message"].format(**TV["stop"])
    assert compile_env.run_emitted_tu(
        cpp, STOP_DRIVER.replace("@EXPECTED@", expected), opt="-O2", label=name,
    ) == "stopped before trades"


@pytest.fixture(scope="module")
def clean_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("dead_for")
    feed = chart_feed_head(engine, base, 1442)
    builds = {}
    for name in TV["runs"]:
        pine = (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")
        cpp = transpile(pine)
        assert "Loop takes too long" not in cpp
        if name in OMITTED_RUNS:
            # Fail promptly before running an unmodified endless chart loop.
            assert "_for_start_" not in cpp
        builds[name] = Build(pine)
    return execute_all(engine, feed, base, builds)


@pytest.mark.parametrize("name", TV["runs"])
def test_dead_chart_or_uncalled_function_loop_runs_the_measured_schedule(clean_runs, name):
    # Includes a function call in a branch that is never taken.
    outcome = ok(clean_runs, name)
    rows = csv.DictReader(outcome.trades["default"].decode("utf-8-sig").splitlines())
    entries = [row for row in rows if row["Type"].startswith("Entry")]
    assert len(entries) == TV["runs"][name]["entries"]
    assert {float(row["Qty"]) for row in entries} == {TV["runs"][name]["quantity"]}


@pytest.fixture(scope="module")
def break_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("for_with_break")
    # The first closed entry of the measured schedule is filled by bar 13.
    feed = chart_feed_head(engine, base, 14)
    builds = {}
    for end in TV["break_control"]["end_bounds"]:
        pine = source(
            "float acc = 0.0\n"
            f"for i = 0 to {end}\n"
            "    acc += 1.0\n"
            f'    if acc >= {TV["break_control"]["break_at"]}.0\n'
            "        break\n",
            'if bar_index > 6 and bar_index % 7 == 3\n'
            '    strategy.entry("L", strategy.long, qty=1.0 + acc / 1000000.0)\n'
            'if bar_index > 6 and bar_index % 7 == 5\n'
            '    strategy.close("L")\n',
        )
        cpp = transpile(pine)
        assert "Loop takes too long" not in cpp
        assert "_for_start_" in cpp
        builds[end] = Build(pine)
    return execute_all(engine, feed, base, builds)


@pytest.mark.parametrize("end", TV["break_control"]["end_bounds"])
def test_reachable_break_keeps_the_measured_iteration_count(break_runs, end):
    outcome = ok(break_runs, end)
    rows = csv.DictReader(outcome.trades["default"].decode("utf-8-sig").splitlines())
    entries = [row for row in rows if row["Type"].startswith("Entry")]
    assert len(entries) == 1
    assert float(entries[0]["Qty"]) == TV["break_control"]["entry_quantity"]
