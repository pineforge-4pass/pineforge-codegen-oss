"""Issue #226: persistent initializers must see earlier statement results.

Controls keep the same strategy and replace the dependency with its inline
expression, or remove ``var`` when checking the generated statement order.
Runtime expectations come from those controls, not captured codegen output.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from tests.test_runtime_var_initialization import _compile_and_run


_ISSUE = """//@version=6
strategy('UDT initializer order')
type State
    float value
float base = strategy.initial_capital / 100
var state = State.new(value=base)
if bar_index == 2 and state.value > 0
    strategy.entry('L', strategy.long)
if bar_index == 5
    strategy.close('L')
observed = state.value
positive = state.value > 0
"""


def _assignment_order(cpp: str, *names: str) -> tuple[str, ...]:
    on_bar = cpp[cpp.index("    void on_source_bar("):]
    positions = {}
    for name in names:
        match = re.search(rf"\b{re.escape(name)}(?:\.update)?\s*[(=]", on_bar)
        assert match is not None, f"missing assignment to {name}"
        positions[name] = match.start()
    return tuple(sorted(names, key=positions.__getitem__))


def test_issue_226_udt_initializer_follows_the_earlier_assignment() -> None:
    control = _ISSUE.replace("var state =", "state =")
    expected_order = _assignment_order(transpile(control), "base", "state")
    assert _assignment_order(transpile(_ISSUE), "base", "state") == expected_order


def _driver(*members: str, dynamic: bool = False) -> str:
    output = " << ' ' << ".join(f"strategy.{member}" for member in members)
    run_args = ', "1", "1"' if dynamic else ""
    return r"""
#include <iomanip>
#include <iostream>

int main() {
    Bar bars[8];
    for (int i = 0; i < 8; ++i) {
        const double price = 10.0 + i;
        bars[i] = Bar{price, price, price, price, 1.0, (i + 1) * 60000LL};
    }
    GeneratedStrategy strategy;
    strategy.run(bars, 8""" + run_args + r""");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 2;
    }
    std::cout << std::setprecision(17) << """ + output + r""" << '\n';
}
"""


def _run(source: str, *members: str, dynamic: bool = False) -> tuple[float, ...]:
    stdout = _compile_and_run(
        transpile(source) + _driver(*members, dynamic=dynamic)
    )
    return tuple(float(value) for value in stdout.split())


@pytest.mark.parametrize("dynamic", [False, True], ids=["static", "dynamic"])
def test_issue_226_initialized_value_matches_inline_control(
    dynamic: bool,
) -> None:
    control = _ISSUE.replace(
        "State.new(value=base)",
        "State.new(value=strategy.initial_capital / 100)",
    )
    members = ("positive", "observed")
    expected = _run(control, *members, dynamic=dynamic)
    # The symptom this harness can see is the field value: the control holds
    # initial_capital / 100 (positive), the unfixed order leaves the default 0.
    # The eight synthetic flat bars place no trade even in the control, so no
    # trade-level claim is made here; that belongs to the gate sweep.
    assert expected[0] == 1.0
    assert expected[1] > 0
    assert _run(_ISSUE, *members, dynamic=dynamic) == expected


_CAPITAL = "strategy.initial_capital / 100"
_CASES = [
    pytest.param(
        "type Record\n    float value\n",
        "Record.new({seed})",
        "held.value",
        id="udt",
    ),
    pytest.param(
        "",
        "array.from({seed})",
        "held.get(0)",
        id="array",
    ),
    pytest.param(
        "",
        "matrix.new<float>(1, 1, {seed})",
        "held.get(0, 0)",
        id="matrix",
    ),
    pytest.param(
        "make(float seed) =>\n"
        "    map<string, float> result = map.new<string, float>()\n"
        '    result.put("value", seed)\n'
        "    result\n",
        "make({seed})",
        'held.get("value")',
        id="map-helper",
    ),
    pytest.param(
        "type Record\n    float value\n"
        "make(float seed) =>\n"
        "    Record.new(seed)\n",
        "make({seed})",
        "held.value",
        id="udt-helper",
    ),
]


def _dependent_source(preamble: str, initializer: str, read: str) -> str:
    return (
        '//@version=6\nstrategy("persistent statement order")\n'
        + preamble
        + f"float seed = {_CAPITAL} + bar_index * close\n"
        # The already-correct scalar route is exercised alongside each
        # aggregate. Later bars change seed, while both first values persist.
        "var float firstSeed = seed\n"
        + f"var held = {initializer.format(seed='seed')}\n"
        + f"observed = {read}\n"
        "current = seed\n"
    )


@pytest.mark.parametrize("preamble,initializer,read", _CASES)
def test_dependent_aggregate_initializers_follow_statement_order(
    preamble: str, initializer: str, read: str,
) -> None:
    source = _dependent_source(preamble, initializer, read)
    control = source.replace("var held =", "held =")
    expected_order = _assignment_order(transpile(control), "seed", "held")
    cpp = transpile(source)
    assert _assignment_order(cpp, "seed", "held") == expected_order
    assert cpp.count("if (!_pf_var_init_held)") == 1


@pytest.mark.parametrize("preamble,initializer,read", _CASES)
def test_dependent_aggregate_initializers_keep_the_first_value(
    preamble: str, initializer: str, read: str,
) -> None:
    source = _dependent_source(preamble, initializer, read)
    control = source.replace(
        initializer.format(seed="seed"), initializer.format(seed=_CAPITAL)
    )
    members = ("observed", "firstSeed", "current")
    expected = _run(control, *members)
    assert expected[0] == expected[1]
    assert expected[0] != expected[2]
    assert _run(source, *members) == expected


@pytest.mark.parametrize("preamble,initializer,read", _CASES)
def test_aggregate_initializers_observe_an_earlier_reassignment(
    preamble: str, initializer: str, read: str,
) -> None:
    source = _dependent_source(preamble, initializer, read).replace(
        f"float seed = {_CAPITAL} + bar_index * close",
        f"var float seed = -1.0\nseed := {_CAPITAL} + bar_index * close",
    )
    control = source.replace(
        initializer.format(seed="seed"), initializer.format(seed=_CAPITAL)
    )
    members = ("observed", "firstSeed", "current")
    expected = _run(control, *members)
    assert expected[0] == expected[1]
    assert _run(source, *members) == expected


def test_aggregate_initializer_follows_a_moved_scalar_var() -> None:
    """An aggregate that reads a scalar `var` whose own initializer moved."""
    source = """//@version=6
strategy("aggregate reads a moved scalar var")
type Record
    float value
float seed = strategy.initial_capital / 100 + bar_index * close
var float firstSeed = seed
var held = Record.new(firstSeed)
observed = held.value
current = seed
"""
    # At the first bar bar_index * close is 0, so firstSeed holds the capital
    # term; the control writes that value inline instead of reading firstSeed
    # and drops `var` from the aggregate, which gives the statement order (an
    # independent `var` constructor would stay in the first-bar preamble).
    control = source.replace(
        "Record.new(firstSeed)", "Record.new(strategy.initial_capital / 100)"
    ).replace("var held =", "held =")
    expected_order = _assignment_order(
        transpile(control), "seed", "firstSeed", "held"
    )
    assert _assignment_order(
        transpile(source), "seed", "firstSeed", "held"
    ) == expected_order
    members = ("observed", "firstSeed", "current")
    expected = _run(control, *members)
    assert expected[0] > 0
    assert expected[0] == expected[1]
    assert expected[0] != expected[2]
    assert _run(source, *members) == expected


def test_aggregate_initializer_observes_an_earlier_collection_mutation() -> None:
    source = """//@version=6
strategy("initializer follows collection mutation")
type Record
    float value
var values = array.new<float>()
values.push(strategy.initial_capital / 100)
var held = Record.new(values.get(0))
observed = held.value
"""
    control = source.replace(
        "Record.new(values.get(0))",
        "Record.new(strategy.initial_capital / 100)",
    )
    expected = _run(control, "observed")
    assert _run(source, "observed") == expected


def test_dependent_udt_alias_and_history_follow_the_moved_initializer() -> None:
    source = _ISSUE.replace(
        "if bar_index == 2",
        "var alias = state\n"
        "previous = alias[1]\n"
        "priorValue = na(previous) ? na : previous.value\n"
        "if bar_index == 2",
    ).replace("observed = state.value", "observed = alias.value")
    control = source.replace(
        "State.new(value=base)",
        "State.new(value=strategy.initial_capital / 100)",
    )
    members = ("observed", "priorValue", "positive", "trade_count()")
    expected = _run(control, *members)
    assert expected[0] == expected[1]
    assert _run(source, *members) == expected


def test_helper_initializer_reads_earlier_global_once() -> None:
    source = """//@version=6
strategy("helper initializer statement order")
type Record
    float value
var calls = array.new<int>()
float seed = strategy.initial_capital / 100 + bar_index * close
make() =>
    calls.push(bar_index)
    Record.new(seed)
var held = make()
observed = held.value
current = seed
callCount = calls.size()
"""
    control = source.replace(
        "Record.new(seed)", "Record.new(strategy.initial_capital / 100)"
    )
    members = ("observed", "current", "callCount")
    expected = _run(control, *members)
    assert expected[0] != expected[1]
    assert _run(source, *members) == expected


def _preamble_member_initializers(cpp: str, *names: str) -> tuple[str, ...]:
    # Slice between the first-bar latch and its completion marker, then keep
    # each member's whole assignment line, including indentation and newline.
    # These small constructors emit one line each; no source line numbers or
    # whitespace normalization enter the comparison.
    start = cpp.index("        if (!_var_initialized) {")
    end = cpp.index("            _var_initialized = true;", start)
    preamble = cpp[start:end]
    initializers = []
    for name in names:
        matches = re.findall(
            rf"^[ \t]*{re.escape(name)}[ \t]*=(?!=)[^\n]*\n",
            preamble,
            flags=re.MULTILINE,
        )
        assert len(matches) == 1, f"missing or duplicate preamble init for {name}"
        initializers.append(matches[0])
    return tuple(initializers)


def test_independent_aggregate_initializers_keep_identical_preamble() -> None:
    """Compatibility pin: an unrelated statement must not move constructors."""
    source = """//@version=6
strategy("independent aggregate routes")
type State
    float value
float base = strategy.initial_capital / 100
var a = array.new<float>()
var s = State.new(value=1.0)
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


def test_nested_dependent_udt_initializes_once_on_first_entry() -> None:
    """A block's dependent UDT initializes on its later first execution."""
    source = """//@version=6
strategy("nested aggregate first entry")
type Record
    float value
float seed = strategy.initial_capital / 100 + bar_index * close
var float firstSeed = seed
var float observed = na
if bar_index >= 3
    var h = Record.new(seed)
    observed := h.value
current = seed
"""
    control = source.replace(
        "if bar_index >= 3\n"
        "    var h = Record.new(seed)\n"
        "    observed := h.value\n",
        "var bool done = false\n"
        "if bar_index >= 3 and not done\n"
        "    h = Record.new(seed)\n"
        "    observed := h.value\n"
        "    done := true\n",
    )
    expected_order = _assignment_order(transpile(control), "seed", "h")
    assert _assignment_order(transpile(source), "seed", "h") == expected_order
    members = ("observed", "firstSeed", "current")
    expected = _run(control, *members)
    assert expected[0] != expected[1]
    assert expected[0] != expected[2]
    assert _run(source, *members) == expected
