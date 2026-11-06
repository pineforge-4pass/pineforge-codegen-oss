"""Native reproducers for floating arrays with an integral missing index.

TV provenance (2026-11-06): probe SHA256
4564a4e717366c9dd92758a2382ae130c1dd1831736fd1a8c8fd2699045a01a1,
raw CSV SHA256
9d9abb7ef8e3d3850aa85dd20d8a04154839284ff3c664d14246b87f57ee37ee.
The function-spelled TV probe observed only get and element 0 after set:
bar 0: close=82483.49, index=na, get=na, first=82483.49;
bar 1: close=82640, index=0, get=82640, first=82641.
Element 1 was not observed. Whole-array preservation, method spelling,
perturbed inputs, evaluation order and journaling below are design-model
checks, not additional TV observations.

The observed top-level int(na) operand pattern is retained, with a native
integral-type assertion. Floating NaN indices intentionally retain their error.
These are input-derived native reproducers for the supervisor's Spark2 proof,
not a measured parity sweep or acceptance of the original JOAT strategy.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


_PRICE_PAIRS = ((82483.49, 82640.0), (12.5, 13.75), (0.125, 0.375))


def _get(spelling: str, receiver: str, index: str) -> str:
    if spelling == "function":
        return f"array.get({receiver}, {index})"
    return f"{receiver}.get({index})"


def _set(spelling: str, receiver: str, index: str, value: str) -> str:
    if spelling == "function":
        return f"array.set({receiver}, {index}, {value})"
    return f"{receiver}.set({index}, {value})"


@pytest.mark.parametrize("spelling", ["function", "method"])
@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_tv_price_filled_float_array_missing_integer_index(spelling, opt):
    source = f"""//@version=6
strategy("Price-filled float array missing integer index")
values = array.new<float>(2, close)
int index = barstate.isfirst ? int(na) : 0
float fetched = {_get(spelling, "values", "index")}
{_set(spelling, "values", "index", "close + 1.0")}
first_value = {_get(spelling, "values", "0")}
index_missing = na(index)
"""
    pairs = ", ".join(
        f"{{{first!r}, {second!r}}}" for first, second in _PRICE_PAIRS
    )
    driver = r"""
#include <cassert>
#include <cmath>
#include <iostream>
#include <type_traits>
int main() {
    const double prices[][2] = {PRICE_PAIRS};
    for (const auto& pair : prices) {
        Bar bars[] = {
            {pair[0], pair[0], pair[0], pair[0], 7.0, 1700000000000LL},
            {pair[1], pair[1], pair[1], pair[1], 9.0, 1700000900000LL},
        };
        for (int count : {1, 2}) {
            GeneratedStrategy strategy;
            static_assert(std::is_integral_v<decltype(strategy.index)>);
            static_assert(!std::is_same_v<decltype(strategy.index), bool>);
            strategy.run(bars, count);
            if (!strategy.last_error().empty()) {
                std::cerr << "count=" << count << " close=" << pair[count - 1]
                          << " native error: " << strategy.last_error() << "\n";
                return 2;
            }
            const double price = bars[count - 1].close;
            assert(strategy.index_missing == (count == 1));
            if (count == 1) {
                assert(std::isnan(strategy.fetched));
                assert(strategy.first_value == price);
            } else {
                assert(strategy.fetched == price);
                assert(strategy.first_value == price + 1.0);
            }
            // No element-1 assertion: the TV capture did not print it.
        }
    }
}
""".replace("PRICE_PAIRS", pairs)
    assert run_emitted_tu(
        transpile(source), driver, opt=opt, label="TV float-array integer-NA pattern",
    ) == ""


@pytest.mark.parametrize("spelling", ["function", "method"])
@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_joat_nested_clamp_get_set_during_warmup(spelling, opt):
    nested = _get(spelling, "lattice", "idx") + " + volume[i]"
    source = f"""//@version=6
strategy("JOAT-shaped integer-NA lattice access")
latticeRows = 7
latticeLookback = 3
rangeHigh = ta.highest(high, 120)
rangeLow = ta.lowest(low, 120)
step = (rangeHigh - rangeLow) / (latticeRows - 1)
lattice = array.new<float>(latticeRows, close)
attempts = 0
index_missing = false
for i = 0 to latticeLookback - 1
    float samplePrice = close[i]
    if not na(samplePrice)
        int idx = int(math.floor((samplePrice - rangeLow) / step))
        idx := math.min(math.max(idx, 0), latticeRows - 1)
        attempts += 1
        index_missing := na(idx)
        {_set(spelling, "lattice", "idx", nested)}
"""
    driver = r"""
#include <algorithm>
#include <cassert>
#include <cmath>
#include <iostream>
#include <vector>
int main() {
    for (double seed : {82483.49, 105.25}) {
        std::vector<Bar> bars;
        for (int i = 0; i < 121; ++i) {
            const double price = seed + (i % 17) * 0.5;
            bars.push_back(Bar{price, price + 2.0, price - 1.0, price,
                               10.0 + i, 1700000000000LL + i * 900000LL});
        }
        for (int count : {1, 119, 120, 121}) {
            GeneratedStrategy strategy;
            strategy.run(bars.data(), count);
            if (!strategy.last_error().empty()) {
                std::cerr << "count=" << count
                          << " native error: " << strategy.last_error() << "\n";
                return 2;
            }
            const bool warmup = count < 120;
            assert(strategy.index_missing == warmup);
            assert(strategy.attempts == std::min(3, count));
            std::vector<double> expected(7, bars[count - 1].close);
            if (warmup) {
                assert(std::isnan(strategy.rangeHigh));
                assert(std::isnan(strategy.rangeLow));
                assert(std::isnan(strategy.step));
            } else {
                double high = bars[count - 120].high;
                double low = bars[count - 120].low;
                for (int i = count - 119; i < count; ++i) {
                    high = std::max(high, bars[i].high);
                    low = std::min(low, bars[i].low);
                }
                const double width = (high - low) / 6.0;
                assert(strategy.rangeHigh == high);
                assert(strategy.rangeLow == low);
                assert(strategy.step == width);
                for (int i = 0; i < std::min(3, count); ++i) {
                    const auto& sample = bars[count - 1 - i];
                    const int index = std::clamp(
                        static_cast<int>(std::floor((sample.close - low) / width)),
                        0, 6);
                    expected[index] += sample.volume;
                }
            }
            // All-cell preservation is the no-address/no-write design model.
            // TV observed only element 0 of its separate two-element probe.
            assert(strategy.lattice == expected);
        }
    }
}
"""
    assert run_emitted_tu(
        transpile(source), driver, opt=opt, label="JOAT integer-NA lattice warmup",
    ) == ""


@pytest.mark.parametrize("spelling", ["function", "method"])
@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_missing_set_evaluates_value_and_nested_get_once(spelling, opt):
    source = f"""//@version=6
strategy("Missing-index argument evaluation")
var values = array.new<float>(2, close)
var order = array.new<int>()
receiver() =>
    order.push(1)
    array.copy(values)
index() =>
    order.push(2)
    int missing = na
    missing
nested_receiver() =>
    order.push(4)
    array.copy(values)
nested_index() =>
    order.push(5)
    int missing = na
    missing
value() =>
    order.push(3)
    float fetched = {_get(spelling, "nested_receiver()", "nested_index()")}
    order.push(6)
    fetched + volume
{_set(spelling, "receiver()", "index()", "value()")}
finished = true
"""
    driver = r"""
#include <cassert>
#include <iostream>
#include <vector>
int main() {
    for (double price : {82483.49, 105.25}) {
        GeneratedStrategy strategy;
        Bar bar{price, price + 1.0, price - 1.0, price, 37.0, 1700000000000LL};
        strategy.run(&bar, 1);
        if (!strategy.last_error().empty()) {
            std::cerr << "native error: " << strategy.last_error() << "\n";
            return 2;
        }
        assert(strategy.finished);
        assert((strategy.order == std::vector<int>{1, 2, 3, 4, 5, 6}));
        assert((strategy.values == std::vector<double>{price, price}));
    }
}
"""
    assert run_emitted_tu(
        transpile(source), driver, opt=opt, label="missing-index operand evaluation",
    ) == ""


@pytest.mark.parametrize("spelling", ["function", "method"])
@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_missing_set_has_no_journal_entry_and_controls_keep_checks(spelling, opt):
    source = f"""//@version=6
strategy("Missing float-array index journal", calc_on_order_fills=true)
var values = array.new<float>(2, close)
fetch(int index) =>
    {_get(spelling, "values", "index")}
store(int index, float value) =>
    {_set(spelling, "values", "index", "value")}
    0
float_fetch(float index) =>
    {_get(spelling, "values", "index")}
float_store(float index, float value) =>
    {_set(spelling, "values", "index", "value")}
    0
empty_fetch() =>
    empty = array.new<float>(0)
    {_get(spelling, "empty", "0")}
empty_store(float value) =>
    empty = array.new<float>(0)
    {_set(spelling, "empty", "0", "value")}
    0
armed = input.bool(false)
if armed
    // Expose every driver control without running the erroring calls at setup.
    store(0, close)
    fetch(0)
    float_fetch(0.0)
    float_store(0.0, close)
    empty_fetch()
    empty_store(close)
"""
    driver = r"""
#include <cassert>
#include <cmath>
#include <iostream>
#include <limits>
#include <string>
#include <vector>
int main() {
    for (double price : {82483.49, 105.25}) {
        GeneratedStrategy strategy;
        Bar bar{price, price + 1.0, price - 1.0, price, 37.0, 1700000000000LL};
        strategy.run(&bar, 1);
        if (!strategy.last_error().empty()) {
            std::cerr << "native error: " << strategy.last_error() << "\n";
            return 2;
        }
        const std::vector<double> original{price, price};
        strategy.snapshot_script_state();
        // Design model: missing integral index has no address or undo entry.
        assert(std::isnan(strategy.fetch(na<int>())));
        strategy.store(na<int>(), price + 1.0);
        assert(strategy._pf_array_journal.empty());
        assert(strategy.values == original);
        // Finite zero and negative indices keep their existing addresses.
        assert(strategy.fetch(0) == price);
        assert(strategy.fetch(-1) == price);
        strategy.store(0, price + 1.0);
        strategy.store(-1, price + 2.0);
        assert(!strategy._pf_array_journal.empty());
        assert(strategy.fetch(0) == price + 1.0);
        assert(strategy.fetch(-1) == price + 2.0);
        strategy.restore_script_state();
        assert(strategy.values == original);
        assert(strategy._pf_array_journal.empty());
        const auto expect_error = [](auto operation, const std::string& message) {
            try {
                operation();
            } catch (const std::exception& error) {
                assert(std::string(error.what()) == message);
                return;
            }
            assert(false && "expected the existing checked-array error");
        };
        for (int index : {2, -3}) {
            const std::string message = "Index " + std::to_string(index)
                + " is out of bounds. Array size is 2";
            expect_error([&] { strategy.fetch(index); }, message);
            expect_error([&] { strategy.store(index, price + 3.0); }, message);
            assert(strategy.values == original);
            assert(strategy._pf_array_journal.empty());
        }
        const std::string missing = "Index na is out of bounds. Array size is 2";
        expect_error([&] { strategy.float_fetch(na<double>()); }, missing);
        expect_error([&] { strategy.float_store(na<double>(), price + 3.0); }, missing);
        for (double index : {std::numeric_limits<double>::infinity(),
                             -std::numeric_limits<double>::infinity()}) {
            const std::string message = std::string("Index ")
                + (index > 0 ? "inf" : "-inf")
                + " is out of bounds. Array size is 2";
            expect_error([&] { strategy.float_fetch(index); }, message);
            expect_error([&] { strategy.float_store(index, price + 3.0); }, message);
        }
        assert(strategy.values == original);
        assert(strategy._pf_array_journal.empty());
        const std::string empty = "Index 0 is out of bounds. Array size is 0";
        expect_error([&] { strategy.empty_fetch(); }, empty);
        expect_error([&] { strategy.empty_store(price + 3.0); }, empty);
    }
}
"""
    assert run_emitted_tu(
        transpile(source), driver, opt=opt, label="missing-index journal and controls",
    ) == ""
