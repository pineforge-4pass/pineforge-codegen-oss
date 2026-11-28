"""#495: once refused at the subscript, a later store reads the requested string."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp
from tests.test_issue_495_requested_string_history import _requested_values
from tests.test_security_helper_arg_history import _run_requested


@pytest.mark.parametrize("name", [
    "txt", "open", "high", "low", "close", "hl2", "hlc3", "hlcc4", "ohlc4", "volume",
])
def test_later_store_reads_its_own_four_hour_history(name):
    # A different request already has a string ring. The 240-minute evaluator
    # must sample the declaration itself, even though only its later store reads it.
    source = f"""//@version=6
strategy("location of requested history")
string {name} = str.tostring(close)
h() =>
    previous = {name}[2]
    str.length(previous)
kept = request.security(syminfo.tickerid, "60", str.length({name}[1]))
var int held = 0
v = request.security(syminfo.tickerid, "240", held)
held := h()
plot(kept + v)
"""
    assert _requested_values(source, sec_id=1, minutes=240)[2:] == [1, 2, 3]


def test_builtin_in_later_store_reads_requested_history():
    # This site used to compile but str.length saw a six-digit chart close.
    source = """//@version=6
strategy("string history in a builtin in a later store")
string open = str.tostring(close)
h() => str.length(open[2])
kept = request.security(syminfo.tickerid, "60", str.length(open[1]))
var int held = 0
v = request.security(syminfo.tickerid, "240", held)
held := h()
plot(kept + v)
"""
    assert _requested_values(source, sec_id=1, minutes=240)[2:] == [1, 2, 3]


def test_later_store_reads_the_reassigned_string_not_its_initializer():
    # Only the later store needs history. Replaying just the global's seed
    # would give length 1 every hour; chart history would see six-digit closes.
    source = """//@version=6
strategy("reassigned string history in a later store")
string open = "x"
if close != 42
    open := ">" + str.tostring(close)
h() =>
    previous = open[1]
    str.length(previous)
var int held = 0
v = request.security(syminfo.tickerid, "60", held)
held := h()
plot(v)
"""
    # After its possible store the first four strings are ">7", "x",
    # ">316", ">2024". Each value is observed on the following requested bar.
    assert _requested_values(source)[1:] == [2, 1, 4, 5]


def test_only_the_replayed_store_adds_its_string_history():
    # `a` is already in the request as a current value. Once held's later
    # store indexes it, its samples need counter too. The guard runs for
    # that store; its sibling and an ordinary chart read do not add state.
    source = """//@version=6
strategy("string dependencies of replayed stores")
var string a = "seed"
guardText = str.tostring(low)
b = str.tostring(high)
var int counter = 0
var int chartOnly = 0
var int held = 0
var int other = 0
v = request.security(syminfo.tickerid, "240", held + str.length(a))
w = request.security(syminfo.tickerid, "60", close)
counter += 1
a := str.tostring(counter)
if str.length(guardText[1]) > 0
    held := str.length(a[1]) + chartOnly
    other := str.length(b[1])
plot(v + w + other)
"""
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    requests = Analyzer(ast).analyze().security_calls
    assert [call.mutable_globals for call in requests] == [
        ("a", "counter", "guardText", "held"), (),
    ]


def test_later_store_does_not_newly_require_replaying_a_top_level_loop():
    # This builtin already compiled using chart history. A request cannot
    # yet replay the loop that writes txt; adding it as mutable would refuse.
    source = """//@version=6
strategy("existing loop-written string")
string txt = ""
for i = 0 to 1
    txt := str.tostring(close + i)
h() => str.length(txt[2])
var int held = 0
v = request.security(syminfo.tickerid, "240", held)
held := h()
plot(v)
"""
    compile_cpp(transpile(source), label="later store with a loop-written string")


@pytest.mark.parametrize("writer", [
    pytest.param("""if close > open
    for i = 0 to 1
        txt := str.tostring(close + i)""", id="store-in-loop"),
    pytest.param("""var int other = 0
if close < open
    other := 0
else if close > open
    txt := str.tostring(close)
    for i = 0 to 1
        other += i
plot(other)""", id="loop-beside-store"),
])
def test_later_store_keeps_chart_history_for_if_with_loop(writer):
    source = f"""//@version=6
strategy("store in an if with a loop")
string txt = ""
{writer}
h() => str.length(txt[2])
var int held = 0
v = request.security(syminfo.tickerid, "240", held)
held := h()
plot(v)
"""
    # The first three hourly children rise and write strings of the same
    # length. At the four-hour close, chart txt[2] therefore has that length.
    groups = tuple(
        (start, start + 1, start + 2, final)
        for start, final in ((100, 7), (1000, 42), (10000, 316), (100000, 2024), (1000000, 5))
    )
    rows = _run_requested(transpile(source), {"chart60": (60, groups)}, group_minutes=240)
    completed = [row for row in rows["chart60"] if row.complete]
    actual = [(row.timestamp, row.close) for row in completed]
    expected = [(slot * 240 * 60_000, group[-1]) for slot, group in enumerate(groups)]
    assert actual == expected, f"NOT COMPARED: completed bars {actual}, expected {expected}"
    # Four-hour bars two slots back closed at 7, 42, 316; their strings have lengths 1, 2, 3.
    assert [row.value for row in completed[2:]] == [5, 6, 7]
