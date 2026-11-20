"""A user variable named like a builtin variable is read as the user variable.

``float second = na`` declares a script variable; its reads were emitted as the
builtin ``pine_second(...)`` because the builtin lookup came before the user's
declaration binding. A read bound to a user declaration is the user's; a read
of the builtin itself keeps its emitted text.
"""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer.tables import BUILTIN_VARS
from pineforge_codegen.symbols import PineType
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("builtin name shadow", initial_capital = 100000)\n'


def test_user_variable_named_like_a_builtin_is_read_as_the_user_variable():
    cpp = transpile(
        HEADER
        + "float second = na\n"
        "float minute = na\n"
        "for i = 1 to 2\n"
        "    float observed = close + i\n"
        "    if i == 1\n"
        "        second := observed\n"
        "    else\n"
        "        minute := observed\n"
        "float code = math.abs(second - minute) < 0.5 ? 1.0 : 2.0\n"
        "plot(code)\n"
    )
    assert "pine_second(" not in cpp
    assert "pine_minute(" not in cpp
    assignments = [line for line in cpp.splitlines() if line.startswith("        code = ")]
    assert len(assignments) == 1
    assert "std::abs((second - minute))" in assignments[0]


def test_read_of_the_builtin_variable_keeps_its_emitted_text():
    cpp = transpile(HEADER + "float s = second + minute\nplot(s)\n")
    assert "pine_second(current_bar_.timestamp, syminfo_.timezone)" in cpp
    assert "pine_minute(current_bar_.timestamp, syminfo_.timezone)" in cpp


BUILTIN_ROWS = [
    # IDs name the pinned TradingView probe: f-<name> or i-<name>.
    pytest.param(name, "123.5" if kind == PineType.FLOAT else "7",
                 id=f"{'f' if kind == PineType.FLOAT else 'i'}-{name}")
    for name, kind in BUILTIN_VARS.items()
    if name != "na"
]


def _out_rhs(cpp):
    (rhs,) = re.findall(r"^\s*out = ([^;\n]+);$", cpp, re.MULTILINE)
    return rhs


@pytest.mark.parametrize("name,literal", BUILTIN_ROWS)
def test_bare_builtin_name_reads_script_member(name, literal):
    # Reassign after the read to keep the ordinary constant inliner from
    # obscuring which member the read uses. The read still sees the literal.
    cpp = transpile(HEADER + f"{name} = {literal}\nout = {name}\n{name} := {literal}\n")
    rhs = _out_rhs(cpp)
    # Allow codegen's C++ collision escapes for names such as time_close.
    assert re.fullmatch(rf"(?:{name}|_{name}_|pf_safe_{name})(?:_\d+)?", rhs), rhs


@pytest.mark.parametrize("name", ["hour", "minute", "second"],
                         ids=["s-hour", "s-minute", "s-second"])
def test_string_shadow_is_not_rendered_as_a_number(name):
    cpp = transpile(HEADER + f'{name} = "x"\nout = {name} + "y"\n')
    rhs = _out_rhs(cpp)
    assert "std::to_string(" not in rhs
    assert 'std::string("x") + std::string("y")' in rhs, rhs


def test_price_shadow_bypasses_precalculation_of_an_unset_member():
    # f-hl2 pins binding. Precalculation cannot read an on_bar assignment.
    cpp = transpile(HEADER + 'hl2 = close * 2\nout = ta.sma(hl2, 2)\n')
    assert ".compute(hl2)" in cpp
    assert "_precalc_" not in cpp


def test_price_history_reads_the_declared_series():
    # f-hl2 pins the name binding. The history lowering must use that binding.
    cpp = transpile(HEADER + "hl2 = close * 2\nout = hl2[1]\n")
    assert _out_rhs(cpp) == "hl2[1]"


def test_calendar_history_before_and_after_declaration_use_distinct_series():
    # i-second pins the script value; the earlier read is still the calendar.
    cpp = transpile(HEADER + "before = second[1]\nsecond = 7\nout = second[1]\n")
    assert "before = _pf_builtin_hist_second[1];" in cpp
    assert "out = second[1];" in cpp
    assert "_pf_builtin_hist_second.push(pine_second(" in cpp
    assert not re.search(r"\bsecond\.push\(pine_second\(", cpp)


DRIVER = r'''
#include <iostream>
#include <vector>
int main() {
    std::vector<pineforge::Bar> bars(3);
    for (std::size_t i = 0; i < bars.size(); ++i) {
        bars[i].open = 100.0;
        bars[i].high = 103.0;
        bars[i].low = 97.0;
        bars[i].close = i % 2 == 0 ? 101.0 : 99.0;
        bars[i].volume = 1.0;
        bars[i].timestamp = 1704067200000LL + static_cast<long long>(i) * 3600000LL;
    }
    for (std::size_t count = 1; count <= bars.size(); ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars.data(), count, "60", "60");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 1;
        }
        std::cout << strategy.out << "\n";
    }
}
'''


@pytest.mark.parametrize("body,expected", [
    # f-hl2 pins binding; the values are 2 * each close on the driver.
    pytest.param("hl2 = close * 2\nout = hl2\n", "202\n198\n202\n", id="f-hl2"),
    # 2024-01-01 is a Monday; the builtin would return 2 for all three bars.
    pytest.param("dayofweek = 7\nout = dayofweek\n", "7\n" * 3, id="i-dayofweek"),
    pytest.param("close = 123.5\nout = close\n", "123.5\n" * 3, id="f-close"),
    # x-<name>: an int-typed built-in name declared with a float value keeps
    # the fraction; the built-in's integer type must not be applied to it.
    pytest.param("time = 123.5\nout = time\n", "123.5\n" * 3, id="x-time"),
    pytest.param("time_close = 123.5\nout = time_close\n", "123.5\n" * 3,
                 id="x-time_close"),
    pytest.param("timenow = 123.5\nout = timenow\n", "123.5\n" * 3,
                 id="x-timenow"),
    pytest.param("dayofweek = 123.5\nout = dayofweek\n", "123.5\n" * 3,
                 id="x-dayofweek"),
    # s-second pins "x"; concatenating "y" gives "xy".
    pytest.param('second = "x"\nout = second + "y"\n', "xy\n" * 3, id="s-second"),
    # Before f-hl2's declaration, the chart's (103 + 97) / 2 is still 100.
    pytest.param("out = hl2\n", "100\n" * 3, id="f-hl2-unshadowed"),
    # f-hl2-before pins PineForge's behavior, not a TradingView value:
    # TradingView rejects a script that reads a built-in and then declares a
    # variable of the same name in the same scope.
    pytest.param("out = hl2\nhl2 = 123.5\n", "100\n" * 3, id="f-hl2-before"),
    # i-hour pins the spelling; echo(int v) => v gives the expected 7.
    pytest.param("echo(int hour) => hour\nout = echo(7)\n", "7\n" * 3,
                 id="i-hour-parameter"),
])
def test_builtin_name_binding_runs(body, expected):
    cpp = transpile(HEADER + body)
    assert run_emitted_tu(cpp, DRIVER, opt="-O0", label="built-in name binding") == expected
