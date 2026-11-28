"""#495: once refused at the subscript, a later store reads the requested string."""

import pytest

from pineforge_codegen import transpile, transpile_full
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
    result = transpile_full(source, filename="loop.pine")
    compile_cpp(result["cpp"], label="later store with a loop-written string")
    warnings = [(d.args, d.location.file, d.location.line, d.location.col)
                for d in result["diagnostics"] if d.code == "PF-W3013"]
    assert warnings == [({"object_name": "txt"}, "loop.pine", 6, 22)]


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
    assert _four_hour_values_after_warmup(source) == [5, 6, 7]


@pytest.mark.parametrize("source", [
    pytest.param("""//@version=6
strategy("store in an if expression")
string txt = ""
x = if close > open
    txt := str.tostring(close)
    1
else
    0
h() => str.length(txt[2])
var int held = 0
v = request.security(syminfo.tickerid, "240", held)
held := h()
plot(v + x)
""", id="store-in-if-value"),
    pytest.param("""//@version=6
strategy("store in a switch expression")
string txt = ""
x = switch
    close > open =>
        txt := str.tostring(close)
        1
    => 0
h() => str.length(txt[2])
var int held = 0
v = request.security(syminfo.tickerid, "240", held)
held := h()
plot(v + x)
""", id="store-in-switch-value"),
    pytest.param("""//@version=6
strategy("string from an if that writes a plain global")
float last = na
string txt = ""
txt := if close > open
    last := close
    str.tostring(close)
else
    ""
h() => str.length(txt[2])
var int held = 0
v = request.security(syminfo.tickerid, "240", held)
held := h()
plot(v)
""", id="value-writes-plain-global"),
])
def test_later_store_keeps_chart_history_for_selection_value(source):
    assert _four_hour_values_after_warmup(source) == [5, 6, 7]


def test_blocked_request_reads_chart_string_even_when_another_request_samples_it():
    source = """//@version=6
strategy("two strings on a blocked request")
string txt = str.tostring(close)
string u = ""
var int other = 0
if close > open
    u := "up"
    for i = 0 to 1
        other += i
hourly = request.security(syminfo.tickerid, "60", str.length(txt[1]))
h() => 10 * str.length(txt[2]) + str.length(u[1])
var int held = 0
v = request.security(syminfo.tickerid, "240", held)
held := h()
plot(hourly + v + other)
"""
    # The tens digit measures txt alone. The preceding rising chart child
    # wrote "up" to u, contributing 2 in each group.
    assert _four_hour_values_after_warmup(source, sec_id=1) == [52, 62, 72]


def _four_hour_values_after_warmup(source, *, sec_id=0):
    # The first three hourly children rise and write strings of the same
    # length. At the four-hour close, chart txt[2] therefore has that length.
    groups = tuple(
        (start, start + 1, start + 2, final)
        for start, final in ((100, 7), (1000, 42), (10000, 316), (100000, 2024), (1000000, 5))
    )
    rows = _run_requested(transpile(source), {"chart60": (60, groups)},
                          group_minutes=240, sec_id=sec_id)
    completed = [row for row in rows["chart60"] if row.complete]
    actual = [(row.timestamp, row.close) for row in completed]
    expected = [(slot * 240 * 60_000, group[-1]) for slot, group in enumerate(groups)]
    assert actual == expected, f"NOT COMPARED: completed bars {actual}, expected {expected}"
    # In contrast, txt[2] on the requested clock would see closes 7, 42, 316
    # and return lengths 1, 2, 3 in these same three slots.
    return [row.value for row in completed[2:]]


def test_first_requested_missing_string_length_matches_chart_na():
    source = """//@version=6
strategy("first requested missing string")
f_cls(string s) =>
    int n = str.length(s)
    na(n) ? -1 : n
h() =>
    int k = int(time / 3600000) % 3
    string txt = k == 0 ? "a" : k == 1 ? "bb" : "ccc"
    string prev = txt[1]
    int cls = na(prev) ? f_cls(prev) : 1000 + f_cls(prev)
    var int firstCls = na
    if na(firstCls)
        firstCls := cls
    firstCls
string none = na
int a = f_cls(none)
int chartFieldA = a + 2
int c = request.security(syminfo.tickerid, "60", h())
plot(chartFieldA + c)
"""
    # The first hour closes after three earlier chart bars have evaluated A.
    # Keep C4's +2 on A so an unwritten chart member cannot stand in for 0.
    rows = _run_requested(
        transpile(source), text_result=True,
        value_expression='std::to_string(chartFieldA) + " " + std::to_string(_req_sec_0)',
    )["chart15"]
    first = [row for row in rows if row.complete and row.timestamp == 0]
    bars = [(row.timestamp, row.close) for row in first]
    assert bars == [(0, 3)], f"NOT COMPARED: first completed hour {bars}, expected [(0, 3)]"
    fields = first[0].value.split()
    assert len(fields) == 2, f"NOT COMPARED: expected fields A and C, got {fields}"
    chart_field, requested_length = map(float, fields)
    assert chart_field == 2  # Field A: str.length(none) == 0.
    assert requested_length == 0  # Field C, slot 0 of the requested evaluator.
