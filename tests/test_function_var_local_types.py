"""Function-local persistent scalar declarations keep their declared types."""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("function var local types")\n'


TIMEFRAME_CASES = (
    pytest.param("timeframe.in_seconds(s)", id="in-seconds-positional"),
    pytest.param(
        "timeframe.in_seconds(timeframe = s)",
        id="in-seconds-named",
    ),
    pytest.param("timeframe.change(s)", id="change-positional"),
    pytest.param("timeframe.change(timeframe = s)", id="change-named"),
)


NATIVE_STRING_DRIVER = r"""
#include <iostream>
#include <vector>
int main() {
    std::vector<pineforge::Bar> bars(3);
    for (std::size_t i = 0; i < bars.size(); ++i) {
        bars[i].open = 100.0;
        bars[i].high = 103.0;
        bars[i].low = 97.0;
        bars[i].close = 101.0;
        bars[i].volume = 1.0;
        bars[i].timestamp = 1704067200000LL
            + static_cast<long long>(i) * 3600000LL;
    }
    for (std::size_t count = 1; count <= bars.size(); ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars.data(), count, "60", "60");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 14;
        }
        std::cout << strategy.held << "\n";
    }
}
"""


@pytest.mark.parametrize("call", TIMEFRAME_CASES)
def test_function_var_string_reaches_timeframe_helper(call: str) -> None:
    # Use a different timeframe from the driver's hourly chart so a chart
    # fallback would change the seconds or the boundary on the second bar.
    source = (
        HEADER + "f(string a) =>\n"
        "    var string s = a\n"
        f"    {call}\n"
        'held = f("120")\n'
    )
    cpp = transpile(source)
    control = source.replace("var string s = a", "string s = a")
    expected = run_emitted_tu(
        transpile(control),
        NATIVE_STRING_DRIVER,
        opt="-O0",
        label=f"plain string control: {call}",
    )
    assert run_emitted_tu(
        cpp,
        NATIVE_STRING_DRIVER,
        opt="-O0",
        label=f"persistent string local: {call}",
    ) == expected


NATIVE_PERSISTENCE_DRIVER = r"""
#include <iostream>
#include <vector>
int main() {
    std::vector<pineforge::Bar> bars(3);
    for (std::size_t i = 0; i < bars.size(); ++i) {
        bars[i].open = 100.0;
        bars[i].high = 103.0;
        bars[i].low = 97.0;
        bars[i].close = 101.0;
        bars[i].volume = 1.0;
        bars[i].timestamp = 1704067200000LL
            + static_cast<long long>(i) * 3600000LL;
    }
    GeneratedStrategy strategy;
    strategy.run(bars.data(), bars.size(), "60", "60");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << "\n";
        return 14;
    }
    std::cout << strategy.held << "\n";
}
"""


NATIVE_STRING_CASES = (
    pytest.param('s + "x"', id="concatenation"),
    pytest.param('str.tostring(s) + "y"', id="string-conversion"),
    pytest.param("str.length(s)", id="length"),
    pytest.param('s == "60" ? 1.0 : 2.0', id="comparison"),
    pytest.param("s", id="return"),
    pytest.param('s := p + "z"\n    s', id="reassignment"),
)


@pytest.mark.parametrize("result", NATIVE_STRING_CASES)
def test_function_var_string_matches_plain_string_control(result: str) -> None:
    subject_body = (
        "f(string p) =>\n"
        "    var string s = p\n"
        f"    {result}\n"
        'held = f("60")\n'
    )
    control_body = subject_body.replace("var string s = p", "string s = p")
    expected = run_emitted_tu(
        transpile(HEADER + control_body),
        NATIVE_STRING_DRIVER,
        opt="-O0",
        label=f"plain string control: {result}",
    )
    actual = run_emitted_tu(
        transpile(HEADER + subject_body),
        NATIVE_STRING_DRIVER,
        opt="-O0",
        label=f"persistent string local: {result}",
    )
    assert actual == expected


NATIVE_TYPE_CASES = (
    pytest.param(
        "var int count = 0\n"
        "    count += 1\n"
        "    count",
        "int count = bar_index + 1\n"
        "    count",
        "int",
        id="var-int-persistent",
    ),
    pytest.param(
        "var float count = 0.0\n"
        "    count += 1.0\n"
        "    count",
        "float count = bar_index + 1.0\n"
        "    count",
        "double",
        id="var-float-persistent",
    ),
    pytest.param(
        "var bool count = false\n"
        "    count := not count\n"
        "    count",
        "bool count = bar_index % 2 == 0\n"
        "    count",
        "bool",
        id="var-bool-persistent",
    ),
    pytest.param(
        'varip string count = ""\n'
        '    count := count + "x"\n'
        "    count",
        'string count = bar_index == 0 ? "x" : bar_index == 1 ? "xx" : "xxx"\n'
        "    count",
        "std::string",
        id="varip-string-persistent",
    ),
)


@pytest.mark.parametrize("body,control_body,type_name", NATIVE_TYPE_CASES)
def test_function_var_scalar_types_and_persistence(
    body: str, control_body: str, type_name: str,
) -> None:
    source = HEADER + "f() =>\n    " + body + "\nheld = f()\n"
    cpp = transpile(source)
    assert re.search(rf"\b{re.escape(type_name)}\s+count\b", cpp)
    control_source = HEADER + "f() =>\n    " + control_body + "\nheld = f()\n"
    expected = run_emitted_tu(
        transpile(control_source),
        NATIVE_PERSISTENCE_DRIVER,
        opt="-O0",
        label=f"plain {type_name} control",
    )
    assert run_emitted_tu(
        cpp,
        NATIVE_PERSISTENCE_DRIVER,
        opt="-O0",
        label=f"function-local {type_name} persistence",
    ) == expected
