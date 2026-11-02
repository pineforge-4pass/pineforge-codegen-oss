"""Native scalar rendering versus the requested-timeframe inventory.

The existing e2e harness builds generated C++, runs the paired engine, and
retains log.info output. Every row observes both chart-selected strings.
Missing engine inputs skip through skip_unless_e2e_env; no native result is
substituted by a Python evaluation.
"""

import pytest

from pineforge_codegen.ast_nodes import BinOp
from pineforge_codegen.codegen import timeframe_values
from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)
from tests._timeframe_value_cases import BY_NAME, CASES, inventory, quota_source, values


@pytest.fixture(scope="module")
def native_rows(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("timeframe_values")
    feed = chart_feed_head(engine, base, 128)
    builds = {
        case.name: Build(case.source(), trace=case.request) for case in CASES
    }
    builds.update({f"quota_{count}": Build(quota_source(count), trace=True)
                   for count in (65, 128)})
    return execute_all(engine, feed, base, builds)


def selected_strings(outcome):
    prefix = "[INFO] selected-frame="
    return tuple(line[len(prefix):] for line in outcome.logs["default"].splitlines()
                 if line.startswith(prefix))


def assert_contains_observed(observed, candidates):
    assert observed, "the native run logged no selected timeframe"
    assert set(observed) <= set(candidates), (observed, candidates)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_native_selected_timeframe_is_in_the_interpreters_set(case, native_rows):
    outcome = ok(native_rows, case.name)
    observed = selected_strings(outcome)
    # Authored literal expectations, independent of the folder and formatter.
    assert observed == (*case.expected, *case.expected)
    evaluator, expression = inventory(case.source())
    candidates = values(evaluator, expression)
    assert candidates == case.expected
    assert_contains_observed(observed, candidates)
    if case.request:
        control = ok(native_rows, "literal")
        assert outcome.traces["default"], "no requested-value trace"
        assert_same_runs(outcome, control)


@pytest.mark.parametrize("case_name,operation,mutant_values", (
    ("ratio", "/", ("0",)),
    ("negmod", "%", ("1240", "2440")),
))
def test_native_comparison_detects_a_fold_rule_mutation(
        case_name, operation, mutant_values, native_rows, monkeypatch):
    case = BY_NAME[case_name]
    observed = selected_strings(ok(native_rows, case_name))
    assert observed == ("240", "1440", "240", "1440")
    # Build and bind with the healthy interpreter first. Mutate only the value
    # proof, leaving the C++ whose logs we compare entirely unchanged.
    evaluator, expression = inventory(case.source())
    original = timeframe_values.fold_constant_expression

    def mutant(node, known, named):
        if isinstance(node, BinOp) and node.op == operation:
            left, right = node.left.value, node.right.value
            return (left // right if operation == "/" else left % right), True
        return original(node, known, named)

    monkeypatch.setattr(timeframe_values, "fold_constant_expression", mutant)
    candidates = values(evaluator, expression)
    assert candidates == mutant_values
    # The very same assertion used by each healthy native row must turn red.
    with pytest.raises(AssertionError):
        assert_contains_observed(observed, candidates)


@pytest.mark.parametrize("count", (65, 128))
def test_native_inventory_runs_beyond_the_64th_reached_timeframe(count, native_rows):
    # This pins PineForge's disagreement; it is not an expectation for TV.
    outcome = ok(native_rows, f"quota_{count}")
    prefix = "[INFO] quota-frame="
    observed = tuple(line[len(prefix):]
                     for line in outcome.logs["default"].splitlines()
                     if line.startswith(prefix))
    assert len(observed) == 128
    assert observed[0] == "1D"
    assert observed[63:65] == ("64D", "65D")
    assert observed[-1] == ("63D" if count == 65 else "128D")
    assert set(observed) == {f"{day}D" for day in range(1, count + 1)}
    assert len(outcome.traces["default"]) == 128
