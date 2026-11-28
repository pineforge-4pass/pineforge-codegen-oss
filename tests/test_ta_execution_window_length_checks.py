"""``ta.lowest`` / ``ta.highest`` in the execution-window form stop on their
first call when the length is zero or less.

A direct ``x := ta.lowest(source, length)`` in the then-body of a top-level
``if`` counts the last ``length`` executions of the call
(``analyzer/call_handlers.py::_mark_execution_window_sites``). Such a site is
declared with the window class written into the translation unit
(``_PFExecutedExtreme``), not with the engine's checked fixed-length type the
same call gets as a top-level declaration
(tests/test_ta_fixed_length_checks.py, whose rows are not changed here).

TradingView stops both forms at run time, on the bar where the call first
executes: "Invalid value of the 'length' argument (0) in the 'lowest'
function. It must be > 0.", with the value the script passed and the function
it called. It refuses neither at compile time, a constant length included.

The window class makes that check on a length it receives on the call (the
series-length form) and has none for the length its constructor holds: for a
constructor length of zero or less it answers na on every call. So the call
of a constant or an input length passes its length too, as the series-length
form does, and the check the class already makes stops the run.

The rows are the four execution-window cases of the measured table: a
constant 0, a constant -1, an input whose default is 0, and ``ta.highest``
with a constant 0. The first test is the emitter's shape. The hosted tests
run the emitted translation unit on eight invented bars, with the call first
reached on the third one, so that a stop on the first call is told apart from
a stop on the first bar. No row is a value recorded on TradingView: a stop is
compared with the engine's text for the call.
"""

import json
import re
from typing import NamedTuple

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


BARS = 8
# The bar_index of the first call: the third bar.
FIRST_CALL = 2


class Case(NamedTuple):
    name: str      # the row's name
    function: str  # the Pine function, without its namespace
    source: str    # the bar value the call reads
    length: str    # the length as the call spells it
    inputs: str    # the script lines that declare the length, or ""
    window: str    # the type the site is declared with
    shown: str     # the value the stop's text shows


_LENGTH_INPUT = 'lengthInput = input.int(0, "Length")\n'
_CASES = (
    Case("lowest-constant-zero", "lowest", "low", "0", "",
         "_PFExecutedExtreme<false>", "0"),
    Case("lowest-constant-negative", "lowest", "low", "-1", "",
         "_PFExecutedExtreme<false>", "-1"),
    Case("lowest-input-zero", "lowest", "low", "lengthInput", _LENGTH_INPUT,
         "_PFExecutedExtreme<false>", "0"),
    Case("highest-constant-zero", "highest", "high", "0", "",
         "_PFExecutedExtreme<true>", "0"),
)
_CASE_ROWS = tuple(pytest.param(case, id=case.name) for case in _CASES)


def _script(case):
    """The call in the execution-window form, first reached on the third bar,
    between two counters; ``present`` counts the bars on which it answered."""
    return (
        '//@version=6\nstrategy("execution-window length checks")\n'
        + case.inputs
        + "var int visited = 0\nvar int completed = 0\nvar int present = 0\n"
        + "visited += 1\n"
        + "float winValue = na\n"
        + f"if bar_index >= {FIRST_CALL}\n"
        + f"    winValue := ta.{case.function}({case.source}, {case.length})\n"
        + "if not na(winValue)\n"
        + "    present += 1\n"
        + "completed += 1\n"
        + "plot(winValue + visited + completed + present)\n"
    )


def _window_member(cpp, case):
    """The one object of the call, which must be the execution-window class:
    the precondition of every row, on any emitter."""
    members = re.findall(
        rf"^\s*(\S.*?)\s+(_ta_{case.function}_\w+);$", cpp, re.M
    )
    assert len(members) == 1, (
        f"NOT COMPARED: ta.{case.function} members; expected=1,"
        f" actual={len(members)} {members}"
    )
    kind, name = members[0]
    assert kind == case.window, (
        f"NOT COMPARED: the site is not the execution-window form;"
        f" expected={case.window}, actual={kind}"
    )
    return name


def _call_arguments(cpp, member):
    """The arguments of every ``compute`` / ``recompute`` call of ``member``,
    each call split at its top-level commas."""
    calls = []
    for match in re.finditer(rf"\b{re.escape(member)}\.(?:re)?compute\(", cpp):
        arguments, depth, start = [], 0, match.end()
        for index in range(match.end(), len(cpp)):
            char = cpp[index]
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    arguments.append(cpp[start:index].strip())
                    break
                depth -= 1
            elif char == "," and depth == 0:
                arguments.append(cpp[start:index].strip())
                start = index + 1
        calls.append(arguments)
    return calls


@pytest.mark.parametrize("case", _CASE_ROWS)
def test_the_call_passes_its_length_to_the_window(case):
    # Emitter shape only. What the generated code does is the hosted tests'.
    cpp = transpile(_script(case))
    member = _window_member(cpp, case)
    # Every way the call steps the window (a new bar, and the same bar again)
    # passes the bar, the source and then the call's own length.
    calls = _call_arguments(cpp, member)
    assert len(calls) >= 2, f"NOT COMPARED: calls of {member}; actual={calls}"
    for arguments in calls:
        assert len(arguments) == 3 and arguments[0] == "bar_index_", calls
        passed = re.fullmatch(
            r"pineforge::source::ta_number\((.*)\)", arguments[2]
        )
        assert passed, calls
        length = passed.group(1)
        if case.inputs:
            # The input, as the script's variable or as its live read.
            assert re.search(r"\blengthInput\b|\bget_input_int\(", length), calls
        else:
            digits = re.sub(r"[()\s]", "", length)
            assert re.fullmatch(r"-?\d+", digits), calls
            assert int(digits) == int(case.length), calls
    # The class, and the header that declares its check, are written for the
    # site.
    assert "class _PFExecutedExtreme" in cpp
    assert "#include <pineforge/source/pine_ta_length.hpp>\n" in cpp


# The host owns the lifecycle: run() admits the bars, prepares the script
# once and dispatches every bar to on_source_bar. Both of its overloads are
# run: the bare one, which offers a precalculated route (this script has none:
# an execution-window site is never precalculated), and the one with a
# timeframe. The host records a stop the script raises instead of rethrowing
# it, so the driver prints the failure record of every run; one handle runs
# once.
_DRIVER = r"""
#include <cstddef>
#include <iostream>
#include <pineforge/run_failure.hpp>

// Eight invented one-minute bars: open, high, low, close, volume.
static const double window_len_bars[][5] = {
    {9, 12, 8, 10, 100}, {10, 13, 9, 12, 140}, {12, 15, 11, 14, 90},
    {14, 14, 10, 11, 210}, {11, 12, 7, 8, 160}, {8, 9, 5, 6, 120},
    {6, 11, 6, 10, 180}, {10, 16, 9, 15, 75},
};
constexpr int window_len_count = 8;

// Counts the host's own per-bar calls that return. A bar the script stops on
// is not counted: the stop leaves the generated callback first.
struct RecordingStrategy : GeneratedStrategy {
    int recorded = 0;
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        ++recorded;
    }
};

struct WindowLenCase {
    const char* name;
    const char* length;  // the "Length" input; null keeps the script's own
};

int main() {
    static const WindowLenCase cases[] = {
CASE_ROWS
    };
    Bar bars[window_len_count];
    for (int i = 0; i < window_len_count; ++i)
        bars[i] = {window_len_bars[i][0], window_len_bars[i][1], window_len_bars[i][2],
                   window_len_bars[i][3], window_len_bars[i][4],
                   1743465600000LL + i * 60000LL};
    for (const WindowLenCase& item : cases) {
        for (bool bare : {true, false}) {
            RecordingStrategy strategy;
            if (item.length) strategy.set_input("Length", item.length);
            if (bare) strategy.run(bars, window_len_count);
            else strategy.run(bars, window_len_count, "1", "1");
            const auto state = strategy.native_state();
            std::cout << "CASE " << item.name << (bare ? " bare" : " timeframe") << "\n"
                      << "STATUS " << strategy.last_run_status() << "\n"
                      << "NAME " << pineforge::run_failure_code_of(strategy) << "\n"
                      << "ARGS " << pineforge::run_failure_args_of(strategy) << "\n"
                      << "CALLBACK "
                      << (state.kind == pineforge::NativeLifecycleKind::Failed &&
                          state.failure.code == pineforge::NativeFailureCode::CallbackException)
                      << "\n"
                      << "VISITED " << strategy.visited << "\n"
                      << "COMPLETED " << strategy.completed << "\n"
                      << "RECORDED " << strategy.recorded << "\n"
                      << "PRESENT " << strategy.present << "\n"
                      << "TEXT " << strategy.last_error() << "\n";
        }
    }
    return 0;
}
"""
_KEYS = (
    "CASE", "STATUS", "NAME", "ARGS", "CALLBACK", "VISITED", "COMPLETED",
    "RECORDED", "PRESENT", "TEXT",
)
_ROUTES = ("bare", "timeframe")


def _cpp_text(value):
    return "nullptr" if value is None else f'"{value}"'


def _hosted_rows(cpp, runs, *, label):
    """Run every run on both overloads; one row per run, keyed by
    ``"<run> bare"`` / ``"<run> timeframe"``. A run is
    ``(name, "Length" input)``."""
    rows_cpp = "".join(
        f'        {{"{name}", {_cpp_text(length)}}},\n' for name, length in runs
    )
    driver = _DRIVER.replace("CASE_ROWS\n", rows_cpp)
    output = run_emitted_tu(cpp, driver, opt="-O0", label=label)
    print(output, end="")
    lines = output.splitlines()
    assert len(lines) == len(_KEYS) * len(_ROUTES) * len(runs), output
    rows = {}
    for start in range(0, len(lines), len(_KEYS)):
        row = {}
        for expected, line in zip(_KEYS, lines[start:start + len(_KEYS)]):
            key, _separator, value = line.partition(" ")
            assert key == expected, output
            row[key] = value
        assert row["CASE"] not in rows, output
        rows[row["CASE"]] = row
    assert set(rows) == {
        f"{name} {route}" for name, _length in runs for route in _ROUTES
    }, output
    return rows


def _assert_stopped(row, case):
    """The run stopped inside the script callback of the bar that first
    reaches the call, at the call: the bars before it completed, the statement
    before the call ran on that bar, the ones after it did not, and the call
    never answered. The text is the engine's for the call, with the value the
    script passed."""
    expected = {
        "STATUS": "1",
        "NAME": "pine_invalid_argument",
        "CALLBACK": "1",
        "VISITED": str(FIRST_CALL + 1),
        "COMPLETED": str(FIRST_CALL),
        "RECORDED": str(FIRST_CALL),
        "PRESENT": "0",
        "TEXT": (
            f"Error on bar {FIRST_CALL}: Invalid value of the 'length' argument"
            f" ({case.shown}) in the '{case.function}' function. It must be > 0."
        ),
    }
    assert {key: row[key] for key in expected} == expected, row
    coded = json.loads(row["ARGS"])
    assert (coded.get("function"), coded.get("argument")) == (
        case.function, "length",
    ), row


@pytest.mark.parametrize("case", _CASE_ROWS)
def test_a_length_of_zero_or_less_stops_on_the_first_call(case):
    cpp = transpile(_script(case))
    _window_member(cpp, case)
    runs = [("script", None)]
    if case.inputs:
        # The control: the same script with the input set to a valid length
        # runs every bar, and the call answers on bars that reach it.
        runs.append(("valid", "2"))
    rows = _hosted_rows(
        cpp, runs, label=f"window_length_{case.name.replace('-', '_')}",
    )
    for route in _ROUTES:
        _assert_stopped(rows[f"script {route}"], case)
    if case.inputs:
        for route in _ROUTES:
            row = rows[f"valid {route}"]
            expected = {
                "STATUS": "0", "NAME": "", "CALLBACK": "0",
                "VISITED": str(BARS), "COMPLETED": str(BARS),
                "RECORDED": str(BARS), "TEXT": "",
            }
            assert {key: row[key] for key in expected} == expected, row
            assert int(row["PRESENT"]) >= 1, row
