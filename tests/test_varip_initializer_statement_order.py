"""Issue #258: dependent ``varip`` aggregates must initialize in source order.

The inline and ``var`` controls supply the expected values. The shared driver
runs eight flat bars, so later dependencies change while the first value stays.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests.test_runtime_var_initialization import _compile_and_run
from tests.test_var_initializer_statement_order import (
    _CAPITAL,
    _CASES,
    _assignment_order,
    _dependent_source,
    _driver,
    _preamble_member_initializers,
    _run,
)


_ISSUE = """//@version=6
strategy("varip initializer order", initial_capital = 100000)
type State
    float value
float base = strategy.initial_capital / 100
varip state = State.new(value=base)
observed = state.value
"""


def _dependent_varip_source(preamble: str, initializer: str, read: str) -> str:
    return (
        _dependent_source(preamble, initializer, read)
        .replace(
            'strategy("persistent statement order")',
            'strategy("varip statement order", initial_capital = 100000)',
        )
        .replace("var held =", "varip held =")
    )


def test_issue_258_udt_initializer_follows_the_earlier_assignment() -> None:
    control = _ISSUE.replace("varip state =", "state =")
    expected_order = _assignment_order(transpile(control), "base", "state")
    assert _assignment_order(transpile(_ISSUE), "base", "state") == expected_order


@pytest.mark.parametrize("dynamic", [False, True], ids=["static", "dynamic"])
def test_issue_258_initialized_value_matches_inline_and_var_controls(
    dynamic: bool,
) -> None:
    inline = _ISSUE.replace(
        "State.new(value=base)",
        "State.new(value=strategy.initial_capital / 100)",
    )
    var_control = _ISSUE.replace("varip state =", "var state =")
    members = ("observed", "base")
    expected = _run(inline, *members, dynamic=dynamic)
    # With the declared capital, the control's base and field both hold 1000.
    assert expected[0] == expected[1] > 0
    assert _run(var_control, *members, dynamic=dynamic) == expected
    stdout = _compile_and_run(
        transpile(_ISSUE) + _driver(*members, dynamic=dynamic)
    )
    assert tuple(map(float, stdout.split())) == expected


@pytest.mark.parametrize("preamble,initializer,read", _CASES)
def test_dependent_varip_aggregate_initializers_follow_statement_order(
    preamble: str, initializer: str, read: str,
) -> None:
    source = _dependent_varip_source(preamble, initializer, read)
    control = source.replace("varip held =", "held =")
    expected_order = _assignment_order(transpile(control), "seed", "held")
    assert _assignment_order(transpile(source), "seed", "held") == expected_order


@pytest.mark.parametrize("preamble,initializer,read", _CASES)
def test_dependent_varip_aggregate_values_match_inline_and_var_controls(
    preamble: str, initializer: str, read: str,
) -> None:
    source = _dependent_varip_source(preamble, initializer, read)
    inline = source.replace(
        initializer.format(seed="seed"), initializer.format(seed=_CAPITAL)
    )
    var_control = source.replace("varip held =", "var held =")
    members = ("observed", "firstSeed", "current")
    expected = _run(inline, *members)
    assert expected[0] == expected[1] > 0
    assert _run(var_control, *members) == expected
    assert _run(source, *members) == expected


@pytest.mark.parametrize("preamble,initializer,read", _CASES)
def test_dependent_varip_aggregate_initializers_keep_the_first_value(
    preamble: str, initializer: str, read: str,
) -> None:
    source = _dependent_varip_source(preamble, initializer, read).replace(
        "current = seed\n",
        "seed := seed + close\ncurrent = seed\n",
    )
    inline = source.replace(
        initializer.format(seed="seed"), initializer.format(seed=_CAPITAL)
    )
    members = ("observed", "firstSeed", "current")
    expected = _run(inline, *members)
    assert expected[0] == expected[1]
    assert expected[0] != expected[2]
    assert _run(source, *members) == expected


def test_independent_varip_aggregate_initializers_keep_identical_preamble() -> None:
    source = """//@version=6
strategy("independent varip aggregates")
type State
    float value
float base = strategy.initial_capital / 100
varip a = array.new<float>()
varip s = State.new(value=1.0)
observed = s.value + a.size()
"""
    control = source.replace("float base = strategy.initial_capital / 100\n", "")
    expected_cpp = transpile(control)
    cpp = transpile(source)
    assert _preamble_member_initializers(
        cpp, "a", "s"
    ) == _preamble_member_initializers(expected_cpp, "a", "s")
    assert _assignment_order(cpp, "a", "s") == _assignment_order(
        expected_cpp, "a", "s"
    )
    for name in ("a", "s"):
        assert f"_pf_var_init_{name}" not in cpp
