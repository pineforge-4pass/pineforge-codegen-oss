"""Structured values retain their types across callable boundaries."""

import re

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp, run_emitted_tu


def signatures(cpp, name):
    return re.findall(
        rf"^    ([^\n]+?) {re.escape(name)}(?:_cs\d+|__ni\d+)?\(([^\n]*)\) \{{$",
        cpp,
        re.MULTILINE,
    )


CASES = [
    pytest.param(
        """choose(array<float> values, bool enabled) =>
    if enabled
        values
    else
        values
items = array.from(close)
selected = choose(items, close > open)
plot(selected.get(0))
""",
        "choose", "std::vector<double>", "std::vector<double>& values, bool enabled", 1,
        id="array-block-return",
    ),
    pytest.param(
        """type Reading
    float value
choose(bool enabled) =>
    Reading item = Reading.new(close)
    if enabled
        item
    else
        item
chosen = choose(close > open)
plot(chosen.value)
""",
        "choose", "Reading", "bool enabled", 1,
        id="object-block-return",
    ),
    pytest.param(
        """choose(bool enabled, array<string> left, array<string> right) =>
    enabled ? left : right
left = array.from("first")
right = array.from("second")
chosen = choose(close > open, left, right)
log.info(chosen.get(0))
""",
        "choose", "std::vector<std::string>",
        "bool enabled, std::vector<std::string>& left, std::vector<std::string>& right", 1,
        id="array-ternary-return",
    ),
    pytest.param(
        """source = ta.ema(close, 7)
features(int offset) =>
    array<float> items = array.from(source[offset])
    items
current = features(0)
previous = features(2)
plot(current.get(0) - previous.get(0))
""",
        "features", "std::vector<double>", "int offset", 2,
        id="array-history-clone-returns",
    ),
    pytest.param(
        """suffix(value) =>
    pieces = str.split(value, "/")
    pieces.size() > 1 ? pieces.get(1) : value
items = array.from("left/right", "whole")
for index = 0 to items.size() - 1
    value = items.get(index)
    log.info(suffix(value))
""",
        "suffix", "std::string", "std::string value", 1,
        id="variable-string-return",
    ),
    pytest.param(
        """first(values) => values.get(0)
outer(values) => first(values) / values.size()
items = array.from(close)
plot(outer(items))
""",
        "first", "double", "std::vector<double>& values", 1,
        id="array-forwarding",
    ),
    pytest.param(
        """pair() =>
    a = array.from(high)
    b = array.from(low)
    [a, b]
last(values) =>
    float result = na
    for item in values
        result := item
    result
[upper, lower] = pair()
plot(last(upper) + last(lower))
""",
        "last", "double", "std::vector<double>& values", 1,
        id="tuple-array-argument",
    ),
    pytest.param(
        """parts(value) =>
    [str.substring(value, 0, 1), str.substring(value, 1, 2)]
outer(value) =>
    [first, second] = parts(value)
    first + second
value = input.string("ab")
log.info(outer(value))
""",
        "parts", "std::tuple<std::string, std::string>", "std::string value", 1,
        id="string-forwarding",
    ),
]


@pytest.mark.parametrize("body,name,result,parameters,count", CASES)
def test_callable_boundaries_keep_the_value_type(body, name, result, parameters, count):
    cpp = transpile('//@version=6\nstrategy("value type flow")\n' + body)
    assert signatures(cpp, name) == [(result, parameters)] * count
    compile_cpp(cpp, label=name)


def test_untyped_array_calls_keep_distinct_element_types():
    cpp = transpile("""//@version=6
strategy("distinct array types")
trim(values, int maximum) =>
    if values.size() > maximum
        values.shift()
integers = array.from(bar_index)
decimals = array.from(close)
trim(integers, 4)
trim(decimals, 4)
""")
    found = signatures(cpp, "trim")
    # Small, non-epoch int arrays retain int storage; widening is a separate
    # provenance rule pinned by test_wide_int_arrays_and_literals.py.
    assert sorted(parameters for _, parameters in found) == [
        "std::vector<double>& values, int maximum",
        "std::vector<int>& values, int maximum",
    ]
    targets = re.findall(
        r"^        (trim(?:_cs\d+|__ni\d+)?)\((integers|decimals), 4\);$",
        cpp, re.MULTILINE,
    )
    assert len(targets) == 2 and len({name for name, _ in targets}) == 2
    assert {argument for _, argument in targets} == {"integers", "decimals"}
    bound_targets = {argument: name for name, argument in targets}
    driver = r'''
#include <iomanip>
#include <iostream>
#include <vector>

template <typename T>
void dump_array_values(const std::vector<T>& values) {
    std::cout << values.size();
    for (auto value : values) std::cout << ' ' << value;
    std::cout << '\n';
}

int main() {
    const Bar bars[] = {
        Bar{9.0, 11.0, 8.0, 10.25, 10.0, 1743465600000LL},
        Bar{19.0, 21.0, 18.0, 20.5, 11.0, 1743466500000LL},
        Bar{33.0, 35.0, 32.0, 33.75, 12.0, 1743467400000LL},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << '\n';
        return 2;
    }
    if (strategy.integers.size() != 1 || strategy.decimals.size() != 1) return 3;
    std::cout << std::setprecision(17)
              << strategy.integers.size() << ' ' << strategy.integers.front()
              << ' ' << strategy.decimals.size() << ' ' << strategy.decimals.front()
              << '\n';

    // The unchanged Pine source creates singleton arrays. Exercise both
    // emitted bodies with larger native arguments as well, including the
    // untaken branch, one removal, and equality at the size boundary.
    std::vector<int> integer_values{-3, 0, 7, 21, 41};
    std::vector<double> decimal_values{-3.25, 0.5, 7.75, 21.125, 41.875};
    for (int maximum : {5, 4, 4}) {
        strategy.INTEGER_TARGET(integer_values, maximum);
        strategy.DECIMAL_TARGET(decimal_values, maximum);
        dump_array_values(integer_values);
        dump_array_values(decimal_values);
    }
}
'''.replace("INTEGER_TARGET", bound_targets["integers"]).replace(
        "DECIMAL_TARGET", bound_targets["decimals"],
    )
    output = run_emitted_tu(
        cpp, driver, opt="-O2", label="distinct array argument types",
    )
    rows = [[float(value) for value in line.split()] for line in output.splitlines()]
    assert rows == [
        [1, 2, 1, 33.75],
        [5, -3, 0, 7, 21, 41],
        [5, -3.25, 0.5, 7.75, 21.125, 41.875],
        [4, 0, 7, 21, 41],
        [4, 0.5, 7.75, 21.125, 41.875],
        [4, 0, 7, 21, 41],
        [4, 0.5, 7.75, 21.125, 41.875],
    ]


def test_reassigned_matrix_local_keeps_its_initializer_type():
    cpp = transpile("""//@version=6
strategy("matrix local type")
nextValue(matrix<float> source) =>
    result = matrix.new<float>(1, 1, 0.0)
    result.set(0, 0, source.get(0, 0) / 2)
    result
var state = matrix.new<float>(1, 1, 0.0)
state.set(0, 0, close)
if barstate.islast
    working = matrix.copy(state)
    for index = 1 to 2
        working := nextValue(working)
    log.info(str.tostring(working.get(0, 0)))
""")
    declarations = re.findall(
        r"^\s+(PineMatrix|double) working(?: = [^\n;]*)?;$",
        cpp, re.MULTILINE,
    )
    assert declarations == ["PineMatrix"]
    driver = r'''
#include <iomanip>
#include <iostream>
#include <type_traits>
#include <utility>

// The emitter's PineMatrix spelling denotes the actual numeric matrix handle.
static_assert(std::is_same_v<
    decltype(std::declval<GeneratedStrategy>().working), pineforge::NumericMatrix>);

int main() {
    const Bar bars[] = {
        Bar{9.0, 11.0, 8.0, 10.25, 10.0, 1743465600000LL},
        Bar{19.0, 21.0, 18.0, 20.5, 11.0, 1743466500000LL},
        Bar{33.0, 35.0, 32.0, 33.75, 12.0, 1743467400000LL},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << '\n';
        return 2;
    }
    std::cout << std::setprecision(17)
              << strategy.state.rows() << ' ' << strategy.state.columns()
              << ' ' << strategy.state.get(0, 0)
              << ' ' << strategy.working.rows() << ' ' << strategy.working.columns()
              << ' ' << strategy.working.get(0, 0) << '\n';
    strategy.working.set(0, 0, -17.5);
    std::cout << strategy.state.get(0, 0) << ' ' << strategy.working.get(0, 0) << '\n';
    strategy.state.set(0, 0, 99.25);
    std::cout << strategy.state.get(0, 0) << ' ' << strategy.working.get(0, 0) << '\n';
}
'''
    output = run_emitted_tu(
        cpp, driver, opt="-O2", label="reassigned matrix local",
    )
    rows = [[float(value) for value in line.split()] for line in output.splitlines()]
    assert rows == [
        [1, 1, 33.75, 1, 1, 33.75 / 4],
        [33.75, -17.5],
        [99.25, -17.5],
    ]


def test_nested_array_variants_keep_their_argument_and_return_types():
    cpp = transpile("""//@version=6
strategy("nested array profiles")
identity(values) => values
outer(values) => identity(values)
integerInput = array.new<int>()
decimalInput = array.new<float>()
integerInput.push(7)
decimalInput.push(close)
integerOutput = outer(integerInput)
decimalOutput = outer(decimalInput)
plot(integerOutput.get(0) + decimalOutput.get(0))
""")
    for function in ("outer", "identity"):
        found = signatures(cpp, function)
        assert ("std::vector<int>", "std::vector<int>& values") in found
        assert ("std::vector<double>", "std::vector<double>& values") in found
    compile_cpp(cpp, label="nested structured type profiles")
