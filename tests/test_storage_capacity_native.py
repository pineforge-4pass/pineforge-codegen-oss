"""Synthetic list-history and drawing-collection witnesses with native inputs."""

import json

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("storage capacity values")\n'


@pytest.mark.parametrize("offset", ["600", "bar_index % 2 == 0 ? 600 : 599"])
def test_default_history_reads_the_actual_older_sample(offset):
    source = HEADER + (
        f"lag = {offset}\n"
        "value = close * 2 + 3\n"
        f"observed_close = close[{offset if offset == '600' else 'lag'}]\n"
        f"observed_user = value[{offset if offset == '600' else 'lag'}]\n"
    )
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    std::vector<Bar> bars;
    for (int i = 0; i < 701; ++i) {
        double price = i + 1.0;
        bars.push_back(Bar{price, price, price, price, 1.0, i * int64_t{60000}});
    }
    strategy.run(bars.data(), bars.size(), "1", "1");
    std::cout << strategy.observed_close << " " << strategy.observed_user;
}
'''
    values = [float(i + 1) for i in range(701)]
    user = [value * 2 + 3 for value in values]
    # At index 700, lag 600 selects index 100: close=101 and user=205.
    expected = (101.0, 205.0)
    assert (values[-601], user[-601]) == expected
    result = run_emitted_tu(transpile(source), driver, opt="-O2", label="history capacity")
    assert tuple(map(float, result.split())) == expected


@pytest.mark.parametrize("offset, code, args", [
    (150, "pine_runtime_limit", {"limit": "max_bars_back", "max": 100}),
    (10001, "strategy_runtime_error", {}),
    (-1, "strategy_runtime_error", {}),
])
def test_operator_history_failure_keeps_its_code_and_guard_priority(offset, code, args):
    source = HEADER + f'lag = input.int({offset}, "Lag")\nobserved = close[lag]\n'
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1, 1, 1, 1, 1, 0};
    ReportC report{};
    run_backtest_full(&strategy, &bar, 1, "1", "1", 0, 4, 0, &report);
    std::cout << run_failure_code_of(strategy) << "\n"
              << run_failure_args_of(strategy) << "\n"
              << strategy.last_error();
}
'''
    result = run_emitted_tu(
        transpile(source, history_capacity=100), driver,
        opt="-O2", label="typed history capacity",
    ).splitlines()
    assert result[0] == code
    assert json.loads(result[1]) == args
    # The operator stop and the pre-existing invalid-offset stop are distinct.
    if offset == 150:
        assert "The historical buffer's limit is 100 bars." in "\n".join(result[2:])


def test_operator_history_last_retained_offset_returns_the_older_value():
    source = HEADER + 'lag = input.int(99, "Lag")\nobserved = close[lag]\n'
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    std::vector<Bar> bars;
    for (int i = 0; i < 201; ++i) {
        double price = i + 1.0;
        bars.push_back(Bar{price, price, price, price, 1, i * int64_t{60000}});
    }
    ReportC report{};
    run_backtest_full(&strategy, bars.data(), bars.size(), "1", "1", 0, 4, 0, &report);
    std::cout << run_failure_code_of(strategy) << "\n" << strategy.observed;
}
'''
    values = [float(i + 1) for i in range(201)]
    # Final index 200 minus lag 99 selects index 101, whose price is 102.
    expected = 102.0
    assert values[-100] == expected
    result = run_emitted_tu(
        transpile(source, history_capacity=100), driver,
        opt="-O2", label="last retained history value",
    ).splitlines()
    assert result[0] == ""
    assert float(result[1]) == expected


def test_overflowing_history_offset_is_evaluated_once():
    source = HEADER + """var calls = array.new<int>(1, 0)
next_offset(array<int> counter) =>
    counter.set(0, counter.get(0) + 1)
    150
observed = close[next_offset(calls)]
"""
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1, 1, 1, 1, 1, 0};
    try {
        strategy.on_source_bar(bar);
        std::cout << "unexpected success";
    } catch (const std::exception& error) {
        auto failure = classify_run_failure(error);
        std::cout << run_failure_code_name(failure.code) << "\n"
                  << (failure.args ? *failure.args : "{}") << "\n"
                  << strategy.calls[0];
    }
}
'''
    result = run_emitted_tu(
        transpile(source, history_capacity=100), driver,
        opt="-O2", label="history offset evaluated once",
    ).splitlines()
    assert result[0] == "pine_runtime_limit"
    assert json.loads(result[1]) == {"limit": "max_bars_back", "max": 100}
    # One reached history expression calls its offset producer once.
    assert int(result[2]) == 1


@pytest.mark.parametrize("body", [
    'lag = input.int(150)\nx = close * 2 + 3\nobserved = x[lag]\n',
    'lag = input.int(150)\na = array.from(close)\nobserved = na(a[lag])\n',
    'lag = input.int(150)\nvar a = array.from(2, 5)\nobserved = na(a[lag])\n',
    'lag = input.int(150)\nobserved = request.security(syminfo.tickerid, "1", close[lag])\n',
    'lag = input.int(60)\nread(float src, int k) => src[k]\n'
    'observed = request.security(syminfo.tickerid, "1", read(close[lag], lag))\n',
    'lag = input.int(150)\nread(int k) =>\n    x = close + 1\n    x[k]\n'
    'observed = request.security(syminfo.tickerid, "1", read(lag))\n',
    'lag = input.int(150)\nread(int k) =>\n    x = str.tostring(close)\n    x[k]\n'
    'observed = request.security(syminfo.tickerid, "1", read(lag))\n',
    'length = input.int(151)\nobserved = close > 0 and ta.roc(close, length) > 0\n',
])
def test_each_history_store_propagates_the_operator_failure(body):
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bars[] = {Bar{1, 1, 1, 1, 1, 0}, Bar{2, 2, 2, 2, 1, 60000}};
    ReportC report{};
    run_backtest_full(&strategy, bars, 2, "1", "1", 0, 4, 0, &report);
    std::cout << run_failure_code_of(strategy) << "\n" << run_failure_args_of(strategy);
}
'''
    result = run_emitted_tu(
        transpile(HEADER + body, history_capacity=100), driver,
        opt="-O2", label="history store capacity",
    ).splitlines()
    assert result[0] == "pine_runtime_limit"
    # Lag 150, composed offset 60+60, and held length 151 all exceed 100 slots.
    assert json.loads(result[1]) == {"limit": "max_bars_back", "max": 100}


def test_sixty_default_labels_keep_the_existing_collection_readouts():
    source = HEADER + """var handles = array.new<label>()
array.push(handles, label.new(bar_index, close))
observed_count = array.size(label.all)
observed_first = label.get_x(array.get(label.all, 0))
observed_last = label.get_x(array.get(label.all, array.size(label.all) - 1))
observed_missing = na(array.get(handles, 0))
"""
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    std::vector<Bar> bars;
    for (int i = 0; i < 60; ++i)
        bars.push_back(Bar{1, 1, 1, 1, 1, i * int64_t{60000}});
    strategy.run(bars.data(), bars.size(), "1", "1");
    std::cout << strategy.observed_count << " " << strategy.observed_first
              << " " << strategy.observed_last << " " << strategy.observed_missing;
}
'''
    live = []
    for identity in range(60):
        live.append(identity)
        if len(live) >= 56:
            live = live[-50:]
    # Creation 56 collects six; four more leave IDs 6..59, 54 alive.
    expected = (54, 6, 59, 1)
    assert (len(live), live[0], live[-1], int(0 not in live)) == expected
    result = run_emitted_tu(transpile(source), driver, opt="-O2", label="label collection")
    assert tuple(map(int, result.split())) == expected


@pytest.mark.parametrize("kind, arena, ctor, getter", [
    ("line", "_pf_lines_", "line.new(bar_index, close, bar_index + 1, close + 1)", "get_x1"),
    ("box", "_pf_boxes_", "box.new(bar_index, close + 1, bar_index + 1, close)", "get_left"),
    ("label", "_pf_labels_", "label.new(bar_index, close)", "get_x"),
])
@pytest.mark.parametrize("capacity", [40, 55, 60])
def test_drawing_operator_bound_and_post_collection_controls(kind, arena, ctor, getter, capacity):
    source = HEADER + (
        f"var handles = array.new<{kind}>()\n"
        "var int completed = 0\n"
        f"before_count = array.size({kind}.all)\n"
        f"array.push(handles, {ctor})\n"
        "completed += 1\n"
        f"observed_first = {kind}.{getter}(array.get({kind}.all, 0))\n"
        f"observed_last = {kind}.{getter}(array.get({kind}.all, array.size({kind}.all) - 1))\n"
        "observed_missing = na(array.get(handles, 0))\n"
    )
    driver = r'''
#include <iostream>
struct DrawingWitness : GeneratedStrategy {
    int failed_before = -1;
    int failed_completed = -1;
    std::size_t failed_handles = 0;
    std::size_t failed_live = 0;
    void on_source_bar(const Bar& bar) override {
        try {
            GeneratedStrategy::on_source_bar(bar);
        } catch (...) {
            failed_before = before_count;
            failed_completed = completed;
            failed_handles = handles.size();
            failed_live = @ARENA@.order().size();
            throw;
        }
    }
};
int main() {
    DrawingWitness strategy;
    std::vector<Bar> bars;
    for (int i = 0; i < 60; ++i)
        bars.push_back(Bar{1, 1, 1, 1, 1, i * int64_t{60000}});
    ReportC report{};
    run_backtest_full(&strategy, bars.data(), bars.size(), "1", "1", 0, 4, 0, &report);
    std::cout << run_failure_code_of(strategy) << "\n"
              << run_failure_args_of(strategy) << "\n";
    if (strategy.failed_before >= 0) {
        std::cout << strategy.failed_before << " " << strategy.failed_completed << " "
                  << strategy.failed_handles << " " << strategy.failed_live;
    } else {
        std::cout << strategy.before_count << " " << strategy.completed << " "
                  << strategy.handles.size() << " " << strategy.@ARENA@.order().size()
                  << " " << strategy.observed_first << " " << strategy.observed_last
                  << " " << strategy.observed_missing;
    }
    std::cout << "\n" << strategy.last_error();
}
'''.replace("@ARENA@", arena)
    result = run_emitted_tu(
        transpile(source, drawing_capacity=capacity), driver,
        opt="-O2", label="live drawing operator capacity",
    ).splitlines()
    if capacity == 40:
        assert result[0] == "pine_runtime_limit"
        assert json.loads(result[1]) == {"limit": "live_drawings", "max": 40}
        published = list(range(40))
        pending = published + [40]
        # Forty handles were published; creation 41 exists before its collector stops.
        expected = (40, 40, 40, 41)
        assert (len(published), len(published), len(published), len(pending)) == expected
        assert tuple(map(int, result[2].split())) == expected
        assert "The script keeps more than 40 live drawings of one type;" in "\n".join(result[3:])
    else:
        assert result[0] == ""
        live = []
        published = []
        for identity in range(60):
            before_count = len(live)
            live.append(identity)
            if len(live) >= 56:
                live = live[-50:]
            published.append(identity)
        # Creation 56 collects six before checking the bound; IDs 6..59 survive.
        expected = (53, 60, 60, 54, 6, 59, 1)
        assert (
            before_count, len(published), len(published), len(live),
            live[0], live[-1], int(0 not in live),
        ) == expected
        assert tuple(map(int, result[2].split())) == expected


@pytest.mark.parametrize("create_lines, expected", [
    (False, (40, 0, 0)),
    (True, (40, 40, 0)),
])
def test_live_drawing_counts_are_independent_between_kinds(create_lines, expected):
    body = """if bar_index < 40
    label.new(bar_index, close)
"""
    if create_lines:
        body += """if bar_index >= 40
    line.new(bar_index, close, bar_index + 1, close)
"""
    source = HEADER + body + """observed_labels = array.size(label.all)
observed_lines = array.size(line.all)
observed_boxes = array.size(box.all)
"""
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    std::vector<Bar> bars;
    for (int i = 0; i < 80; ++i)
        bars.push_back(Bar{1, 1, 1, 1, 1, i * int64_t{60000}});
    ReportC report{};
    run_backtest_full(&strategy, bars.data(), bars.size(), "1", "1", 0, 4, 0, &report);
    std::cout << run_failure_code_of(strategy) << "\n"
              << strategy.observed_labels << " " << strategy.observed_lines
              << " " << strategy.observed_boxes;
}
'''
    result = run_emitted_tu(
        transpile(source, drawing_capacity=40), driver,
        opt="-O2", label="independent live drawing capacities",
    ).splitlines()
    assert result[0] == ""
    labels = list(range(40))
    lines = list(range(40, 80)) if create_lines else []
    boxes = []
    # Forty label creations, optionally forty line creations, and no box creations.
    assert (len(labels), len(lines), len(boxes)) == expected
    assert tuple(map(int, result[1].split())) == expected


def test_pinned_labels_still_obey_the_live_drawing_bound_after_collection():
    source = HEADER.replace(
        'strategy("storage capacity values")',
        'strategy("storage capacity values", max_labels_count=1)',
    )
    source += "".join(f"var label pin{i} = na\n" for i in range(7))
    source += "var int completed = 0\nbefore_count = array.size(label.all)\n"
    source += "".join(
        f"if bar_index == {i}\n    pin{i} := label.new(bar_index, close)\n"
        for i in range(7)
    )
    source += "completed += 1\n"
    driver = r'''
#include <iostream>
struct PinnedWitness : GeneratedStrategy {
    int failed_before = -1;
    int failed_completed = -1;
    std::size_t failed_live = 0;
    bool first_live = false;
    void on_source_bar(const Bar& bar) override {
        try {
            GeneratedStrategy::on_source_bar(bar);
        } catch (...) {
            failed_before = before_count;
            failed_completed = completed;
            failed_live = _pf_labels_.order().size();
            first_live = _pf_labels_.alive(pin0.id);
            throw;
        }
    }
};
int main() {
    PinnedWitness strategy;
    std::vector<Bar> bars;
    for (int i = 0; i < 7; ++i)
        bars.push_back(Bar{1, 1, 1, 1, 1, i * int64_t{60000}});
    ReportC report{};
    run_backtest_full(&strategy, bars.data(), bars.size(), "1", "1", 0, 4, 0, &report);
    std::cout << run_failure_code_of(strategy) << "\n"
              << run_failure_args_of(strategy) << "\n"
              << strategy.failed_before << " " << strategy.failed_completed << " "
              << strategy.failed_live << " " << strategy.first_live;
}
'''
    result = run_emitted_tu(
        transpile(source, drawing_capacity=6), driver,
        opt="-O2", label="pinned drawing capacity",
    ).splitlines()
    assert result[0] == "pine_runtime_limit"
    assert json.loads(result[1]) == {"limit": "live_drawings", "max": 6}
    published = list(range(6))
    live = published + [6]
    protected = set(live)
    eligible = [identity for identity in live if identity not in protected]
    survivors = [identity for identity in live if identity not in eligible[:6]]
    # At trigger 1+6, all six older IDs and the new ID are protected; none can collect.
    expected = (6, 6, 7, 1)
    assert (len(published), len(published), len(survivors), int(0 in survivors)) == expected
    assert tuple(map(int, result[2].split())) == expected


def test_linefills_do_not_consume_the_collected_drawing_bound():
    source = HEADER + """var line first = line.new(0, 1, 1, 1)
var line second = line.new(0, 2, 1, 2)
var fills = array.new<linefill>()
array.push(fills, linefill.new(first, second, color.blue))
observed_lines = array.size(line.all)
observed_fills = array.size(linefill.all)
"""
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    std::vector<Bar> bars;
    for (int i = 0; i < 60; ++i)
        bars.push_back(Bar{1, 1, 1, 1, 1, i * int64_t{60000}});
    ReportC report{};
    run_backtest_full(&strategy, bars.data(), bars.size(), "1", "1", 0, 4, 0, &report);
    std::cout << run_failure_code_of(strategy) << "\n"
              << strategy.observed_lines << " " << strategy.observed_fills;
}
'''
    result = run_emitted_tu(
        transpile(source, drawing_capacity=2), driver,
        opt="-O2", label="linefills outside collected drawing capacity",
    ).splitlines()
    assert result[0] == ""
    lines = [0, 1]
    fills = list(range(60))
    # Two persistent lines and sixty uncollected linefill creations stay live.
    assert (len(lines), len(fills)) == (2, 60)
    assert tuple(map(int, result[1].split())) == (2, 60)
