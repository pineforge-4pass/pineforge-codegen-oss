"""Timestamp values retain their width at scalar and array boundaries."""

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp, run_emitted_tu


HEADER = '//@version=6\nstrategy("timestamp boundaries")\n'
INPUT = 'start = input.time(timestamp("2024-01-01T00:00:00"), "Start")\n'
DRIVER = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    pineforge::Bar bar{100.0, 104.0, 99.0, 103.0, 10.0, 1743466500000LL};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    std::cout << strategy.ok << '\n';
}
'''


def _run(cpp):
    return run_emitted_tu(cpp, DRIVER, opt="-O0").strip()


@pytest.mark.parametrize("hint", ["", "int ", "simple int "])
@pytest.mark.parametrize("actual", ["start", "time"])
def test_integer_identity_preserves_time_inputs(hint, actual):
    # start is 1704067200000 and time is 1743466500000; identity preserves both.
    cpp = transpile(HEADER + INPUT + f"identity({hint}value) => value\n"
                    f"got = identity({actual})\nok = got == {actual} ? 1 : 0\n")
    compile_cpp(cpp)
    assert _run(cpp) == "1"


@pytest.mark.parametrize("actual", ["1577836800000", "-1577836800000"])
def test_large_literal_scalar_argument(actual):
    # Positive NumberLiteral is newly wide; unary minus was already a wide overflow pin.
    cpp = transpile(HEADER + "identity(int value) => value\n"
                    f"got = identity({actual})\nok = got == {actual} ? 1 : 0\n")
    compile_cpp(cpp)
    assert _run(cpp) == "1"


def test_time_input_array_from():
    # A time input seeds the epoch array scan: declaration and list element are int64.
    cpp = transpile(HEADER + INPUT + "var stamps = array.from(start)\n"
                    "ok = stamps.get(0) == start ? 1 : 0\n")
    compile_cpp(cpp)
    assert _run(cpp) == "1"


def test_time_input_alias_keyword_and_wrapper():
    # start + 60000 reaches the keyword parameter through a local and two returns.
    cpp = transpile(HEADER + INPUT + '''identity(int value) => value
wrapper(simple int value) =>
    copied = value
    identity(value = copied)
shifted = start + 60000
got = wrapper(value = shifted)
ok = got == start + 60000 ? 1 : 0
''')
    compile_cpp(cpp)
    assert _run(cpp) == "1"


def test_direct_time_input_argument_and_plain_default():
    # The written input.time call and the omitted large-literal default both feed int64 slots.
    cpp = transpile(HEADER + '''identity(int value) => value
defaulted(int value = 1704067200000) => value
got = identity(input.time(timestamp("2024-01-01T00:00:00"), "Start"))
saved = defaulted()
ok = got == 1704067200000 and saved == got ? 1 : 0
''')
    compile_cpp(cpp)
    assert _run(cpp) == "1"


def test_count_parameter_and_literal_array_keep_count_result():
    # Small counter calls still produce the Pine sum 0+1+2+3.
    cpp = transpile(HEADER + '''count(int value) => value
var counts = array.new<int>()
counts.push(3000000000)
int total = 0
for i = 0 to 3
    total += count(i)
ok = total == 6 ? 1 : 0
''')
    compile_cpp(cpp)
    assert _run(cpp) == "1"


@pytest.mark.parametrize("hint", ["int ", ""])
@pytest.mark.parametrize("actual", ["time", "value = time", ""])
def test_method_parameter_and_assignment_return(hint, actual):
    # The tail writes the same epoch to self.t and the returned result; neither may narrow.
    cpp = transpile(HEADER + f'''type Stamp
    int t = 0
method store(Stamp self, {hint}value = 1704067200000) =>
    self.t := value
var s = Stamp.new()
got = s.store({actual})
ok = s.t == {"time" if actual else "1704067200000"} and got == s.t ? 1 : 0
''')
    compile_cpp(cpp)
    assert _run(cpp) == "1"


def test_method_keyword_gap_defaults_and_helper_chain():
    # Binding inserts offset=0 before value=time; both method and helper tails yield time.
    cpp = transpile(HEADER + '''type Stamp
    int t = 0
identity(int value) =>
    int copied = value
method store(Stamp self, int offset = 0, int value = 0) =>
    self.t := identity(value) + offset
wrap(Stamp object, int value) =>
    object.store(value = value)
var s = Stamp.new()
got = wrap(s, time)
ok = got == time and s.t == time ? 1 : 0
''')
    compile_cpp(cpp)
    assert _run(cpp) == "1"


def test_method_receiver_resolution_keeps_other_type_narrow():
    # Only Stamp.store receives an epoch; Count.store receives the loop's 0..3.
    cpp = transpile(HEADER + '''type Stamp
    int t = 0
type Count
    int n = 0
method store(Stamp self, int value) =>
    self.t := value
method store(Count self, int value) =>
    int base = self.n
    value
var s = Stamp.new()
var c = Count.new()
got = s.store(time)
int total = 0
for i = 0 to 3
    total += c.store(i)
ok = got == time and total == 6 ? 1 : 0
''')
    compile_cpp(cpp)
    assert _run(cpp) == "1"


@pytest.mark.parametrize("tail", [
    "    int base = self.t\n    int copied = value",
    "    int base = self.t\n    int copied = 0\n    copied := value",
    "    if bar_index == 0\n        self.t := value\n    else\n        self.t := value",
    "    switch\n        bar_index == 0 => self.t := value\n        => self.t := value",
])
def test_method_value_producing_tails(tail):
    # Every reachable tail yields the epoch argument, including declarations and selections.
    cpp = transpile(HEADER + f'''type Stamp
    int t = 0
method store(Stamp self, int value) =>
{tail}
var s = Stamp.new()
got = s.store(time)
ok = got == time ? 1 : 0
''')
    compile_cpp(cpp)
    assert _run(cpp) == "1"
