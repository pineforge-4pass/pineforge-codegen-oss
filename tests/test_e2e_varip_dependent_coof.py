"""Issue #258: a dependent ``varip`` value must survive fill recalculations.

The existing fill strategy supplies the orders and execution counters. Its
dependency changes on every execution; a control constructs the first value
inline without ``varip``. Matching it throughout the run checks initialization
order and that later executions do not repeat the dependent initializer.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from tests._e2e import (
    Build,
    assert_same_runs,
    closed_trades,
    derive_chart_feed,
    execute_all,
    ok,
    skip_unless_e2e_env,
)
from tests.test_e2e_varip_coof import FIXTURES


def _dependent_source() -> str:
    source = (FIXTURES / "varip_coof_on.pine").read_text()
    source = source.replace("initial_capital = 1000000", "initial_capital = 100000")
    source = source.replace(
        "var int vCount = 0\n",
        "type State\n    float value\nvar int vCount = 0\n",
    )
    source = source.replace(
        "tag = str.tostring(vpCount - vCount)",
        "float base = strategy.initial_capital / 100 + vpCount - 1\n"
        "varip state = State.new(value=base)\n"
        "observed = state.value\n"
        'tag = str.tostring(vpCount - vCount) + ":" + str.tostring(observed)',
    )
    return (
        source
        + "\n// @pf-trace observed=observed\n"
        "// @pf-trace dependency=base\n"
        "// @pf-trace recalculations=vpCount-vCount\n"
    )


def _var_free_control(source: str) -> str:
    return source.replace(
        "varip state = State.new(value=base)",
        "state = State.new(value=strategy.initial_capital / 100)",
    )


@pytest.fixture(scope="session")
def dependent_varip_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("varip_dependent_fills")
    feed = derive_chart_feed(engine, base / "chart.csv")
    source = _dependent_source()
    builds = {
        "dependent": Build(source, trace=True),
        "control": Build(_var_free_control(source), trace=True),
    }
    runs = execute_all(engine, feed, base, builds)
    return engine, base, feed, runs


def test_dependent_varip_initializer_keeps_its_first_value_across_fills(
    dependent_varip_runs,
) -> None:
    engine, base, feed, runs = dependent_varip_runs
    control = ok(runs, "control")
    records = control.traces["default"]
    observed = [record["value"] for record in records
                if record["name"] == "observed"]
    dependency = [record["value"] for record in records
                  if record["name"] == "dependency"]
    recalculations = [record["value"] for record in records
                      if record["name"] == "recalculations"]
    assert observed and dependency and recalculations
    expected = dependency[0]
    assert expected > 0
    assert all(value == expected for value in observed)
    assert dependency[-1] != expected
    assert any(value > 0 for value in recalculations)
    expected_trades = closed_trades(engine, base / "control", feed)
    assert expected_trades

    # A repeated initializer would pick up the changing dependency. A lost
    # first value reads zero instead of the inline control's capital term.
    assert_same_runs(ok(runs, "dependent"), control)
    assert closed_trades(engine, base / "dependent", feed) == expected_trades


def test_dependent_varip_storage_and_latch_stay_out_of_the_checkpoint() -> None:
    cpp = transpile(_dependent_source())
    checkpoint = cpp.split("struct _PFScriptState {", 1)[1].split("};", 1)[0]
    restore = cpp.split("    void restore_script_state() override {", 1)[1]
    restore = restore.split("\n    }", 1)[0]
    on_bar = cpp.split("    void on_source_bar(", 1)[1]

    # Read any declaration-site latch's actual name from its guarded write.
    # Before the fix, only the shared preamble guard may surround this write;
    # it belongs to the ordinary checkpoint, rather than to the varip state.
    guards = re.findall(
        r"if \(!([A-Za-z_]\w*)\) \{\s*state(?:\.update)?\s*[(=]",
        on_bar,
    )
    members = {"state", "vpCount"}
    members.update(guard for guard in guards if guard != "_var_initialized")
    assert "GeneratedStrategy::vCount)" in checkpoint
    assert re.search(r"\bvCount\b", restore) is not None
    for member in sorted(members):
        assert f"GeneratedStrategy::{member})" not in checkpoint, member
        assert re.search(rf"\b{re.escape(member)}\b", restore) is None, member
        assert any(f" {member}{suffix}" in cpp
                   for suffix in (" = ", "{", ";")), member
