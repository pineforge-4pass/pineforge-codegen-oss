"""Requested records and collections retain their value types."""

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp, run_emitted_tu


CLOSES = (11.0, 23.0, 37.0)
TIMES = tuple(1743465600000 + index * 3600000 for index in range(len(CLOSES)))


def _observe(source, members):
    cpp = transpile(source)
    compile_cpp(cpp, label="request result type")
    bars = ",\n".join(
        f"Bar{{{close - 1}, {close + 2}, {close - 2}, {close}, 1.0, "
        f"{TIMES[index]}LL}}"
        for index, close in enumerate(CLOSES)
    )
    values = " << ' ' << ".join(f"strategy.{member}" for member in members)
    driver = f"""
#include <iomanip>
#include <iostream>
int main() {{
    Bar bars[] = {{{bars}}};
    std::cout << std::setprecision(17);
    for (int count = 1; count <= {len(CLOSES)}; ++count) {{
        GeneratedStrategy strategy;
        strategy.run(bars, count, "60", "60");
        if (!strategy.last_error().empty()) return 1;
        std::cout << {values} << '\\n';
    }}
}}
"""
    return [
        tuple(map(float, line.split()))
        for line in run_emitted_tu(
            cpp, driver, opt="-O2", label="request result values"
        ).splitlines()
    ]


def test_requested_record_field():
    source = """//@version=6
strategy("Record request storage")
type Reading
    float value
result = request.security(syminfo.tickerid, "60", Reading.new(close))
observed = result.value
plot(observed)
"""
    assert _observe(source, ("observed",)) == [(close,) for close in CLOSES]


@pytest.mark.parametrize("payload, expected", (("close", CLOSES), ("time", TIMES)))
def test_requested_array_size_and_element(payload, expected):
    source = f"""//@version=6
strategy("Array request storage")
values = request.security(syminfo.tickerid, "60", array.from({payload}))
observed_size = array.size(values)
observed_element = array.get(values, 0)
plot(observed_size + observed_element)
"""
    assert _observe(source, ("observed_size", "observed_element")) == [
        (1.0, value) for value in expected
    ]


def test_requested_tuple_array_and_scalar():
    source = """//@version=6
strategy("Tuple request storage")
[values, price] = request.security(syminfo.tickerid, "60", [array.from(close), close])
observed_size = array.size(values)
observed_element = array.get(values, 0)
plot(observed_size + observed_element + price)
"""
    assert _observe(source, ("observed_size", "observed_element", "price")) == [
        (1.0, close, close) for close in CLOSES
    ]


def test_requested_array_reassignment():
    source = """//@version=6
strategy("Array request assignment")
var values = array.from(0.0)
values := request.security(syminfo.tickerid, "60", array.from(close))
observed_size = array.size(values)
observed_element = array.get(values, 0)
plot(observed_size + observed_element)
"""
    assert _observe(source, ("observed_size", "observed_element")) == [
        (1.0, close) for close in CLOSES
    ]
