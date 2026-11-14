"""Structured values retain their types across callable boundaries."""

import re

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp


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
    assert sorted(parameters for _, parameters in found) == [
        "std::vector<double>& values, int maximum",
        "std::vector<int64_t>& values, int maximum",
    ]
    targets = re.findall(
        r"^        (trim(?:_cs\d+|__ni\d+)?)\((integers|decimals), 4\);$",
        cpp, re.MULTILINE,
    )
    assert len(targets) == 2 and len({name for name, _ in targets}) == 2
    compile_cpp(cpp, label="distinct array argument types")


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
        r"^\s+(NumericMatrix|double) working(?: = [^\n;]*)?;$",
        cpp, re.MULTILINE,
    )
    assert declarations == ["NumericMatrix"]
    compile_cpp(cpp, label="reassigned matrix local")


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
