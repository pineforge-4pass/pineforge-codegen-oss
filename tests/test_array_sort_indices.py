"""sort_indices returns integer indices in the requested order.

The two complete reporter programs are retained here. Their native drivers use
small, explicit bars to isolate the compile/type/order contracts; they do not
claim to replay the reporter's BTCUSDT feed or reproduce its trade statistics.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


REPORTER_TYPED = """//@version=6
strategy("sort_indices into array<int>", overlay = true, initial_capital = 10000)
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
var vals = array.new<float>()
vals.push(close)
if vals.size() > 5
    vals.shift()
if barstate.islast
    array<int> ord = vals.sort_indices(order.descending)
    log.info(str.tostring(array.get(ord, 0)))
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""

REPORTER_DESCENDING = """//@version=6
strategy("sort_indices descending", overlay = true, initial_capital = 10000)
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
vals = array.from(1.0, 3.0, 2.0)
ord = vals.sort_indices(order.descending)
first = array.get(ord, 0)
// @pf-trace first=first
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""

CONTROL_UNTYPED = REPORTER_TYPED.replace(
    "array<int> ord = vals.sort_indices(order.descending)",
    "ord = vals.sort_indices(order.descending)",
)
CONTROL_INDEXOF_MAX = REPORTER_DESCENDING.replace(
    "first = array.get(ord, 0)", "first = vals.indexof(vals.max())",
)

_BARS = r"""
    Bar bars[] = {
        {8.0, 8.0, 8.0, 8.0, 1.0, 1704067200000LL},
        {9.0, 9.0, 9.0, 9.0, 1.0, 1704070800000LL},
        {5.0, 5.0, 5.0, 5.0, 1.0, 1704074400000LL},
        {12.0, 12.0, 12.0, 12.0, 1.0, 1704078000000LL},
        {11.0, 11.0, 11.0, 11.0, 1.0, 1704081600000LL},
        {7.0, 7.0, 7.0, 7.0, 1.0, 1704085200000LL},
    };
"""

_WINDOW_DRIVER = r"""
#include <iostream>
int main() {
BARS
    GeneratedStrategy strategy;
    strategy.run(bars, 6);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 2;
    }
    std::cout << strategy.vals.size() << '\n';
}
""".replace("BARS", _BARS)


def test_reporter_untyped_control_runs():
    assert run_emitted_tu(
        transpile(CONTROL_UNTYPED), _WINDOW_DRIVER,
        opt="-O0", label="reporter untyped control",
    ).splitlines() == ["5"]


def test_reporter_indexof_max_control_runs():
    driver = r"""
#include <iostream>
int main() {
BARS
    for (int count = 1; count <= 6; ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars, count);
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 2;
        }
        std::cout << strategy.first << '\n';
    }
}
""".replace("BARS", _BARS)
    assert run_emitted_tu(
        transpile(CONTROL_INDEXOF_MAX), driver,
        opt="-O0", label="reporter indexof max control",
    ).splitlines() == ["1"] * 6


def test_reporter_typed_result_compiles_and_runs():
    assert run_emitted_tu(
        transpile(REPORTER_TYPED), _WINDOW_DRIVER,
        opt="-O0", label="reporter typed integer array",
    ).splitlines() == ["5"]


def test_reporter_descending_keeps_the_complete_index_order():
    driver = r"""
#include <iostream>
int main() {
BARS
    for (int count = 1; count <= 6; ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars, count);
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 2;
        }
        std::cout << strategy.first;
        for (auto index : strategy.ord) std::cout << ' ' << index;
        std::cout << '\n';
    }
}
""".replace("BARS", _BARS)
    actual = run_emitted_tu(
        transpile(REPORTER_DESCENDING), driver,
        opt="-O0", label="reporter descending full index order",
    ).splitlines()
    assert actual == ["1 1 2 0"] * 6


def test_reporter_untyped_result_supports_a_method_read():
    source = REPORTER_DESCENDING.replace(
        "first = array.get(ord, 0)", "first = ord.get(0)",
    )
    driver = _WINDOW_DRIVER.replace(
        "strategy.vals.size()", "strategy.first",
    )
    assert run_emitted_tu(
        transpile(source), driver, opt="-O0", label="reporter untyped ord.get",
    ).splitlines() == ["1"]


CALLS = [
    ("method-default", "vals.sort_indices()", False),
    ("namespace-default", "array.sort_indices(vals)", False),
    ("namespace-named-default", "array.sort_indices(id = vals)", False),
    ("method-ascending", "vals.sort_indices(order.ascending)", False),
    ("namespace-ascending", "array.sort_indices(vals, order.ascending)", False),
    ("method-descending", "vals.sort_indices(order.descending)", True),
    ("namespace-descending", "array.sort_indices(vals, order.descending)", True),
    ("namespace-mixed-descending", "array.sort_indices(vals, order = order.descending)", True),
    ("method-named-ascending", "vals.sort_indices(order = order.ascending)", False),
    ("namespace-named-ascending",
     "array.sort_indices(id = vals, order = order.ascending)", False),
    ("method-named-descending", "vals.sort_indices(order = order.descending)", True),
    ("namespace-named-descending",
     "array.sort_indices(order = order.descending, id = vals)", True),
]


@pytest.mark.parametrize("case,call,descending", CALLS, ids=[c[0] for c in CALLS])
@pytest.mark.parametrize("element", ["float", "int", "string"])
def test_result_elements_are_integer_indices(case, call, descending, element):
    initializer = {
        "float": "array.from(close, -close, 0.5)",
        "int": "array.from(bar_index, bar_index + 1, -3)",
        "string": 'array.from(input.string("z", "Word"), "b", "a")',
    }[element]
    cpp = transpile(
        '//@version=6\nstrategy("sort index result type")\n'
        f"vals = {initializer}\nord = {call}\n"
    )
    output = run_emitted_tu(cpp, r"""
#include <iostream>
#include <type_traits>
int main() {
    GeneratedStrategy strategy;
    static_assert(std::is_integral_v<
        typename decltype(strategy.ord)::value_type>);
    static_assert(!std::is_same_v<
        typename decltype(strategy.ord)::value_type, bool>);
    Bar bar{4.0, 4.0, 4.0, 4.0, 1.0, 1704067200000LL};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 2;
    }
    for (auto index : strategy.ord) std::cout << index << ' ';
    std::cout << '\n';
}
""", opt="-O0", label=f"integer sort indices {element} {case}")
    ascending = {
        "float": [1, 2, 0],  # [4.0, -4.0, 0.5]
        "int": [2, 0, 1],  # [0, 1, -3]
        "string": [2, 1, 0],  # ["z", "b", "a"]
    }[element]
    expected = list(reversed(ascending)) if descending else ascending
    assert [int(value) for value in output.split()] == expected


@pytest.mark.parametrize("case,call,descending", CALLS, ids=[c[0] for c in CALLS])
def test_complete_order_uses_runtime_inputs_without_mutating_the_source(
    case, call, descending,
):
    source = (
        '//@version=6\nstrategy("sort index input order")\n'
        'pivot = input.float(2.5, "Pivot")\n'
        "vals = array.from(pivot, -pivot, close)\n"
        f"ord = {call}\n"
    )
    driver = r"""
#include <cassert>
#include <iomanip>
#include <iostream>
int main() {
    for (const char* pivot : {"2.5", "-3.25"}) {
        for (double price : {4.0, -8.0, 0.75}) {
            GeneratedStrategy strategy;
            strategy_set_input(&strategy, "Pivot", pivot);
            Bar bar{price, price, price, price, 1.0, 1704067200000LL};
            strategy.run(&bar, 1);
            if (!strategy.last_error().empty()) {
                std::cerr << strategy.last_error();
                return 2;
            }
            const double value = std::stod(pivot);
            assert(strategy.vals.size() == 3);
            assert(strategy.vals[0] == value);
            assert(strategy.vals[1] == -value);
            assert(strategy.vals[2] == price);
            std::cout << std::setprecision(17) << value << ' ' << price;
            for (auto index : strategy.ord) std::cout << ' ' << index;
            std::cout << '\n';
        }
    }
}
"""
    actual = run_emitted_tu(
        transpile(source), driver, opt="-O0", label=f"runtime order {case}",
    ).splitlines()
    expected = []
    for pivot in (2.5, -3.25):
        for price in (4.0, -8.0, 0.75):
            values = [pivot, -pivot, price]
            order = sorted(range(len(values)), key=values.__getitem__, reverse=descending)
            expected.append((pivot, price, order))
    assert len(actual) == len(expected)
    for line, (pivot, price, order) in zip(actual, expected):
        fields = line.split()
        assert [float(value) for value in fields[:2]] == [pivot, price]
        assert [int(value) for value in fields[2:]] == order


def test_distinct_helper_call_sites_keep_their_integer_array_results():
    source = """//@version=6
strategy("sort indices helper call sites")
rank(array<float> values, bool reverse) =>
    array<int> indices = values.sort_indices(reverse ? order.descending : order.ascending)
    indices
left = array.from(close, close + 2.0, close - 1.0)
right = array.from(-close, close + 3.0, close)
ascending = rank(left, false)
descending = rank(right, true)
first = ascending.get(0)
second = descending.get(0)
"""
    output = run_emitted_tu(transpile(source), r"""
#include <iostream>
#include <type_traits>
int main() {
    GeneratedStrategy strategy;
    static_assert(std::is_integral_v<
        typename decltype(strategy.ascending)::value_type>);
    static_assert(std::is_integral_v<
        typename decltype(strategy.descending)::value_type>);
    Bar bar{4.0, 4.0, 4.0, 4.0, 1.0, 1704067200000LL};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 2;
    }
    for (auto index : strategy.ascending) std::cout << index << ' ';
    std::cout << '\n';
    for (auto index : strategy.descending) std::cout << index << ' ';
    std::cout << '\n' << strategy.first << ' ' << strategy.second << '\n';
}
""", opt="-O0", label="separate helper sort results")
    assert output.splitlines() == ["2 0 1 ", "1 2 0 ", "2 1"]


@pytest.mark.parametrize("method", [False, True], ids=["namespace", "method"])
@pytest.mark.parametrize("descending", [False, True], ids=["ascending", "descending"])
@pytest.mark.parametrize("element", ["float", "int"])
def test_empty_arrays_ties_and_missing_values(method, descending, element):
    order = "order.descending" if descending else "order.ascending"
    call = (
        f"vals.sort_indices(order = {order})" if method else
        f"array.sort_indices(order = {order}, id = vals)"
    )
    initializer = (
        "array.from(close, float(na), close, -close, float(na))"
        if element == "float" else
        "array.from(bar_index + 4, int(na), bar_index + 4, -4, int(na))"
    )
    source = (
        '//@version=6\nstrategy("sort ties and missing values")\n'
        f"vals = {initializer}\nord = {call}\n"
        f"empty_values = array.new<{element}>()\n"
        f"empty_order = {call.replace('vals', 'empty_values')}\n"
    )
    output = run_emitted_tu(transpile(source), r"""
#include <cassert>
#include <iostream>
#include <type_traits>
int main() {
    GeneratedStrategy strategy;
    static_assert(std::is_integral_v<
        typename decltype(strategy.ord)::value_type>);
    Bar bar{4.0, 4.0, 4.0, 4.0, 1.0, 1704067200000LL};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 2;
    }
    assert(strategy.vals.size() == 5);
    assert(strategy.vals[0] == 4 && strategy.vals[2] == 4);
    assert(strategy.vals[3] == -4);
    assert(is_na(strategy.vals[1]) && is_na(strategy.vals[4]));
    assert(strategy.empty_order.empty());
    for (auto index : strategy.ord) std::cout << index << ' ';
    std::cout << '\n';
}
""", opt="-O0", label=f"{element} missing/tied indices {method} {descending}")
    indices = [int(value) for value in output.split()]
    assert sorted(indices) == list(range(5))
    # NA is larger than present numeric values. Equal elements may exchange
    # their indices; verify every rank group without inventing a TV tie order.
    groups = [{3}, {0, 2}, {1, 4}]
    if descending:
        groups.reverse()
    offset = 0
    for group in groups:
        assert set(indices[offset:offset + len(group)]) == group
        offset += len(group)


@pytest.mark.parametrize("descending", [False, True], ids=["ascending", "descending"])
def test_string_order_includes_empty_strings(descending):
    order = "order.descending" if descending else "order.ascending"
    source = (
        '//@version=6\nstrategy("string sort indices")\n'
        'vals = array.from("", input.string("z", "Word"), "A", "a")\n'
        f"ord = array.sort_indices(vals, order = {order})\n"
    )
    output = run_emitted_tu(transpile(source), r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{4.0, 4.0, 4.0, 4.0, 1.0, 1704067200000LL};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 2;
    }
    for (auto index : strategy.ord) std::cout << index << ' ';
    std::cout << '\n';
}
""", opt="-O0", label=f"string index ordering {descending}")
    ascending = [0, 2, 3, 1]
    expected = list(reversed(ascending)) if descending else ascending
    assert [int(value) for value in output.split()] == expected


@pytest.mark.parametrize("empty", [False, True], ids=["populated", "empty"])
def test_receiver_and_order_are_evaluated_once_before_sorting(empty):
    initializer = "array.new<float>()" if empty else "array.from(close, close + 2, close + 1)"
    source = (
        '//@version=6\nstrategy("sort indices argument evaluation")\n'
        f"vals = {initializer}\n"
        "events = array.new<int>()\n"
        "source_array() =>\n    events.push(1)\n    vals\n"
        "direction() =>\n    events.push(2)\n    order.descending\n"
        "ord = array.sort_indices(order = direction(), id = source_array())\n"
    )
    output = run_emitted_tu(transpile(source), r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{4.0, 4.0, 4.0, 4.0, 1.0, 1704067200000LL};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 2;
    }
    for (auto event : strategy.events) std::cout << event << ' ';
    std::cout << '\n';
    for (auto index : strategy.ord) std::cout << index << ' ';
    std::cout << '\n';
}
""", opt="-O0", label=f"sort receiver/order evaluation {empty}")
    assert output.splitlines() == ["1 2 ", "" if empty else "1 2 0 "]


def test_wide_integer_values_do_not_widen_the_returned_indices():
    source = """//@version=6
strategy("sort wide values into integer indices")
vals = array.from(time, time + 60000, time - 60000)
array<int> typed = array.sort_indices(vals, order.descending)
inferred = vals.sort_indices(order = order.ascending)
"""
    output = run_emitted_tu(transpile(source), r"""
#include <iostream>
#include <type_traits>
int main() {
    GeneratedStrategy strategy;
    static_assert(std::is_same_v<
        typename decltype(strategy.typed)::value_type, int>);
    static_assert(std::is_same_v<
        typename decltype(strategy.inferred)::value_type, int>);
    Bar bar{4.0, 4.0, 4.0, 4.0, 1.0, 1704067200000LL};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 2;
    }
    for (auto index : strategy.typed) std::cout << index << ' ';
    std::cout << '\n';
    for (auto index : strategy.inferred) std::cout << index << ' ';
    std::cout << '\n';
}
""", opt="-O0", label="wide source with integer indices")
    assert output.splitlines() == ["1 0 2 ", "2 0 1 "]
