"""Ordinary global tuples and later Pine readers share typed member storage."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("global tuple consumers")\n'


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
@pytest.mark.parametrize("call_style", ["helper", "method"])
def test_mixed_global_tuple_and_later_udfs_keep_values_and_types(opt, call_style):
    declaration = (
        "method makeValues(int count, float price, int stamp)"
        if call_style == "method"
        else "makeValues(int count, float price, int stamp)"
    )
    call = (
        "units.makeValues(close, time)" if call_style == "method"
        else "makeValues(units, close, time)"
    )
    source = HEADER + f'''
units = input.int(3, "Units")
bias = input.float(0.5, "Bias")
{declaration} =>
    [count * 7 + 2, price * 2.0 + bias, count > 0,
     count > 0 ? "up" : "down", stamp + count, stamp * 8192 + count]
[wholePart, fractionPart, isPositive, sideName, epochValue, exactValue] = {call}
[selectedEpoch, selectedExact] = if isPositive
    [epochValue, exactValue]
else
    [epochValue - 1, exactValue - 1]
getWhole() => wholePart * 3 + units
getFraction() => fractionPart + open
getFlag() => isPositive
getSide() => sideName
getEpoch() => epochValue
getExact() => exactValue
getSelectedEpoch() => selectedEpoch
getSelectedExact() => selectedExact
wholeOut = getWhole()
fractionOut = getFraction()
flagOut = getFlag()
sideOut = getSide()
epochOut = getEpoch()
exactOut = getExact()
selectedEpochOut = getSelectedEpoch()
selectedExactOut = getSelectedExact()
plot(fractionOut)
'''
    driver = r'''
#include <iomanip>
#include <iostream>
#include <type_traits>
static_assert(std::is_integral_v<decltype(GeneratedStrategy::wholePart)>);
static_assert(std::is_same_v<decltype(GeneratedStrategy::fractionPart), double>);
static_assert(std::is_same_v<decltype(GeneratedStrategy::isPositive), bool>);
static_assert(std::is_same_v<decltype(GeneratedStrategy::sideName), std::string>);
static_assert(std::is_same_v<decltype(GeneratedStrategy::epochValue), int64_t>);
static_assert(std::is_same_v<decltype(GeneratedStrategy::exactValue), int64_t>);
static_assert(std::is_same_v<decltype(GeneratedStrategy::selectedEpoch), int64_t>);
static_assert(std::is_same_v<decltype(GeneratedStrategy::selectedExact), int64_t>);
int main() {
    struct Sample {
        double open, close;
        const char* units;
        const char* bias;
        int64_t stamp;
    };
    const Sample samples[] = {
        {10.0, 12.5, "3", "0.5", 1706745600000LL},
        {8.0, 7.25, "-4", "-1.25", 946684799000LL},
        {20.0, 23.75, "11", "2.75", 2142243197000LL},
    };
    std::cout << std::setprecision(17);
    for (const auto& sample : samples) {
        GeneratedStrategy strategy;
        strategy_set_input(&strategy, "Units", sample.units);
        strategy_set_input(&strategy, "Bias", sample.bias);
        Bar bar{sample.open, 30.0, 1.0, sample.close, 1.0, sample.stamp};
        strategy.run(&bar, 1, "60", "60");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 1;
        }
        std::cout << strategy.wholePart << " " << strategy.fractionPart << " "
                  << strategy.isPositive << " " << strategy.sideName << " "
                  << strategy.epochValue << " " << strategy.exactValue << " "
                  << strategy.wholeOut << " " << strategy.fractionOut << " "
                  << strategy.flagOut << " " << strategy.sideOut << " "
                  << strategy.epochOut << " " << strategy.exactOut << " "
                  << strategy.selectedEpochOut << " "
                  << strategy.selectedExactOut << "\n";
    }
}
'''
    output = run_emitted_tu(
        transpile(source), driver, opt=opt, label="mixed global tuple consumers",
    )
    samples = [
        (10.0, 12.5, 3, 0.5, 1706745600000),
        (8.0, 7.25, -4, -1.25, 946684799000),
        (20.0, 23.75, 11, 2.75, 2142243197000),
    ]
    rows = output.splitlines()
    assert len(rows) == len(samples)
    for row, (opening, close, units, bias, stamp) in zip(rows, samples):
        cells = row.split()
        assert len(cells) == 14
        whole = units * 7 + 2
        fraction = close * 2 + bias
        side = "up" if units > 0 else "down"
        epoch = stamp + units
        exact = stamp * 8192 + units
        assert int(cells[0]) == whole
        assert float(cells[1]) == fraction
        assert int(cells[2]) == int(units > 0)
        assert cells[3] == side
        assert int(cells[4]) == epoch
        assert int(cells[5]) == exact
        assert int(cells[6]) == whole * 3 + units
        assert float(cells[7]) == fraction + opening
        assert int(cells[8]) == int(units > 0)
        assert cells[9] == side
        assert int(cells[10]) == epoch
        assert int(cells[11]) == exact
        assert int(cells[12]) == epoch - int(units <= 0)
        assert int(cells[13]) == exact - int(units <= 0)


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_function_and_block_tuple_shadows_leave_global_consumers_intact(opt):
    source = HEADER + '''
pair(float seed) => [seed + 0.25, seed * 2.0]
[value, other] = pair(close)
readGlobals() => value * 10.0 + other
readLocals(float seed) =>
    [value, other] = pair(seed)
    value * 10.0 + other
float blockOut = 0.0
if close > 0.0
    [value, other] = pair(open)
    blockOut := value * 10.0 + other
localOut = readLocals(open + 1.0)
globalOut = readGlobals()
countPair(int seed) => [seed + 2, seed * 3]
[countValue, countOther] = countPair(int(close))
readCounts() => countValue * 10 + countOther
float blockCountOut = 0.0
if close > 0.0
    [countValue, countOther] = countPair(int(open))
    blockCountOut := countValue * 10 + countOther
countOut = readCounts()
plot(globalOut)
'''
    driver = r'''
#include <iostream>
int main() {
    const double samples[][2] = {{10.0, 12.5}, {8.0, 7.25}, {20.0, 23.75}};
    for (const auto& sample : samples) {
        GeneratedStrategy strategy;
        Bar bar{sample[0], 30.0, 1.0, sample[1], 1.0, 1706745600000LL};
        strategy.run(&bar, 1, "60", "60");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 1;
        }
        std::cout << strategy.value << " " << strategy.other << " "
                  << strategy.blockOut << " " << strategy.localOut << " "
                  << strategy.globalOut << " " << strategy.blockCountOut << " "
                  << strategy.countOut << "\n";
    }
}
'''
    rows = run_emitted_tu(
        transpile(source), driver, opt=opt, label="lexical tuple shadows",
    ).splitlines()
    samples = [(10.0, 12.5), (8.0, 7.25), (20.0, 23.75)]
    assert len(rows) == len(samples)
    for row, (opening, close) in zip(rows, samples):
        assert [float(cell) for cell in row.split()] == [
            close + 0.25, close * 2,
            opening * 12 + 2.5, (opening + 1) * 12 + 2.5,
            close * 12 + 2.5,
            int(opening) * 13 + 20, int(close) * 13 + 20,
        ]


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_global_tuple_evaluates_once_with_discards_history_and_selection(opt):
    source = HEADER + '''
once(float seed) =>
    var int calls = 0
    calls += 1
    [seed + calls, seed - calls, calls]
[held, _, callCount] = once(close)
[selected, selectedCount] = if close > open
    [held + 1.0, callCount * 2]
else
    [held - 1.0, callCount * 3]
readSelected() => selected + selectedCount
previous = held[1]
observed = readSelected()
plot(observed)
'''
    driver = r'''
#include <iostream>
int main() {
    for (double opening : {10.0, 20.0}) {
        GeneratedStrategy strategy;
        Bar bars[] = {
            {opening, 30.0, 1.0, 12.0, 1.0, 1706745600000LL},
            {opening, 30.0, 1.0, 14.0, 1.0, 1706745660000LL},
            {opening, 30.0, 1.0, 16.0, 1.0, 1706745720000LL},
        };
        strategy.run(bars, 3, "1", "1");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 1;
        }
        std::cout << strategy.callCount << " " << strategy.previous << " "
                  << strategy.selected << " " << strategy.selectedCount << " "
                  << strategy.observed << "\n";
    }
}
'''
    rows = run_emitted_tu(
        transpile(source), driver, opt=opt, label="global tuple once and history",
    ).splitlines()
    assert [[float(cell) for cell in row.split()] for row in rows] == [
        [3, 16, 20, 6, 26],
        [3, 16, 18, 9, 27],
    ]


def test_untyped_tuple_calls_keep_their_own_member_types():
    source = HEADER + '''
pair(seed) => [seed, seed]
[numberA, numberB] = pair(close)
[wordA, wordB] = pair("row")
plot(numberA)
'''
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{10.0, 15.0, 9.0, 12.5, 1.0, 1706745600000LL};
    strategy.run(&bar, 1, "60", "60");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 1;
    }
    std::cout << strategy.numberA << " " << strategy.numberB << " "
              << strategy.wordA << " " << strategy.wordB << "\n";
}
'''
    assert run_emitted_tu(
        transpile(source), driver, opt="-O0", label="untyped tuple member types",
    ) == "12.5 12.5 row row\n"


def test_float_tuple_shadow_of_integer_globals_keeps_both_values():
    source = HEADER + '''
intPair(int seed) => [seed + 2, seed * 3]
floatPair(float seed) => [seed + 0.25, seed * 2.0]
[value, other] = intPair(int(close))
float localOut = 0.0
if close > 0.0
    [value, other] = floatPair(open)
    localOut := value * 10.0 + other
readGlobals() => value * 10 + other
globalOut = readGlobals()
plot(globalOut)
'''
    driver = r'''
#include <iostream>
int main() {
    const double samples[][2] = {{10.0, 12.5}, {8.0, 7.25}};
    for (const auto& sample : samples) {
        GeneratedStrategy strategy;
        Bar bar{sample[0], 30.0, 1.0, sample[1], 1.0, 1706745600000LL};
        strategy.run(&bar, 1, "60", "60");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 1;
        }
        std::cout << strategy.localOut << " " << strategy.globalOut << "\n";
    }
}
'''
    rows = run_emitted_tu(
        transpile(source), driver, opt="-O0", label="cross-type tuple shadow",
    ).splitlines()
    samples = [(10.0, 12.5), (8.0, 7.25)]
    assert len(rows) == len(samples)
    for row, (opening, close) in zip(rows, samples):
        assert [float(cell) for cell in row.split()] == [
            opening * 12 + 2.5, int(close) * 13 + 20,
        ]


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_tuple_shadow_writes_use_their_own_binding_types(opt):
    source = HEADER + '''
intPair(int seed) => [seed + 2, seed * 3]
floatPair(float seed) => [seed + 0.25, seed * 2.0]
[value, other] = intPair(int(close))
readGlobals() => value * 10 + other
readLocals(float seed) =>
    [value, other] = floatPair(seed)
    value += 0.5
    other := other + 0.125
    value * 10.0 + other
functionOut = readLocals(open + 1.0)
float localOut = 0.0
if close > 0.0
    [value, other] = floatPair(open)
    value += 0.5
    other := other + 0.125
    value := if close > open
        value + 0.25
    else
        value - 0.25
    localOut := value * 10.0 + other
globalOut = readGlobals()
value += 1
afterOut = readGlobals()
plot(globalOut)
'''
    driver = r'''
#include <iomanip>
#include <iostream>
int main() {
    const double samples[][2] = {{10.0, 12.5}, {8.0, 7.25}};
    std::cout << std::setprecision(17);
    for (const auto& sample : samples) {
        GeneratedStrategy strategy;
        Bar bar{sample[0], 30.0, 1.0, sample[1], 1.0, 1706745600000LL};
        strategy.run(&bar, 1, "60", "60");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 1;
        }
        std::cout << strategy.localOut << " " << strategy.functionOut << " "
                  << strategy.globalOut << " " << strategy.afterOut << "\n";
    }
}
'''
    rows = run_emitted_tu(
        transpile(source), driver, opt=opt, label="tuple shadow binding writes",
    ).splitlines()
    samples = [(10.0, 12.5), (8.0, 7.25)]
    assert len(rows) == len(samples)
    for row, (opening, close) in zip(rows, samples):
        direction = 0.25 if close > opening else -0.25
        assert [float(cell) for cell in row.split()] == [
            (opening + 0.25 + 0.5 + direction) * 10 + opening * 2 + 0.125,
            (opening + 1 + 0.25 + 0.5) * 10 + (opening + 1) * 2 + 0.125,
            (int(close) + 2) * 10 + int(close) * 3,
            (int(close) + 3) * 10 + int(close) * 3,
        ]
