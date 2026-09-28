"""The C++ of a ``ta.*`` call whose length (or supertrend factor) the
constructor cannot take -- neither a compile-time constant nor an
input/timeframe expression the runtime reset re-reads.

TradingView answers such a call by its Pine qualifier (lane K-TA-DYNLEN; the
rules and their tapes are in the engine header
``pineforge/source/pine_ta_length.hpp`` and the replays in
``tests/test_e2e_ta_dynamic_length.py``):

* series length, ta.highest / ta.lowest / ta.highestbars / ta.lowestbars:
  ``pineforge::source::Series*``, the length passed on every call;
* ta.supertrend: ``pineforge::source::PineSupertrend``, factor and atrPeriod
  passed on the call and read on its first execution;
* simple length (fixed for the run) of any family:
  ``pineforge::source::FirstCallBound<class>``, the constant-length class built
  on the call's first execution, the length checked by ``simple_ta_length``;
* series length of another family: still refused (TradingView refuses it for
  the recursive ones; the other window functions are not lowered yet).

Constant- and input-length calls keep their constructor, byte for byte.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests._e2e import reference_codegen, transpile_json


def _member_type(cpp: str, member: str) -> str:
    match = re.search(rf"^\s*(\S.*?)\s+{re.escape(member)};$", cpp, re.M)
    assert match, member
    return match.group(1)


def _members(cpp: str, cpp_type: str) -> list[str]:
    return re.findall(rf"^\s*{re.escape(cpp_type)}\s+(\w+);$", cpp, re.M)


def test_series_length_extremes_take_the_series_classes() -> None:
    src = """//@version=6
strategy("series extremes")
up = ta.crossover(close, ta.sma(close, 9))
n = math.max(1, ta.barssince(up) + 1)
lo = ta.lowest(low, n)
hi = ta.highest(n)
lob = ta.lowestbars(n)
hib = ta.highestbars(high, n)
if close > open and ta.lowest(close, n) < open
    strategy.entry("L", strategy.long)
plot(lo + hi + lob + hib)
"""
    cpp = transpile(src)
    assert "#include <pineforge/source/pine_ta_length.hpp>" in cpp
    assert len(_members(cpp, "pineforge::source::SeriesLowest")) == 2
    assert len(_members(cpp, "pineforge::source::SeriesHighest")) == 1
    assert len(_members(cpp, "pineforge::source::SeriesLowestBars")) == 1
    assert len(_members(cpp, "pineforge::source::SeriesHighestBars")) == 1
    # The one-argument forms keep their default source; the length is read on
    # the bar, compute and recompute alike.
    assert re.search(r"\.compute\(current_bar_\.high, pineforge::source::ta_number\(", cpp)
    assert re.search(r"\.recompute\(current_bar_\.low, pineforge::source::ta_number\(", cpp)
    # No constructor argument and no precalculated series for these sites.
    assert not re.search(r"_ta_(lowest|highest)\w*_\d+\(", cpp.split("GeneratedStrategy()")[1].split("{")[0])
    assert "_precalc__ta_lowest" not in cpp
    compile_cpp(cpp, label="series_extremes")


def test_simple_lengths_build_the_constant_class_on_first_call() -> None:
    src = """//@version=6
strategy("simple lengths")
preset = input.string("Custom", "Preset", options=["Custom", "Fast"])
base = input.int(14, "Base")
isCrypto = str.contains(str.upper(syminfo.ticker), "BTC") or syminfo.type == "crypto"
len = isCrypto ? 9 : base
lenP = preset == "Fast" ? 5 : base
r = ta.rsi(close, len)
e = ta.ema(close, lenP)
m = ta.rma(close, len)
a = ta.atr(len)
[p, q, adx] = ta.dmi(len, lenP)
[mc, sg, hs] = ta.macd(close, len, lenP * 2, 9)
w = ta.wma(close, len)
s = ta.sma(close, len)
lo = ta.lowest(low, len)
plot(r + e + m + a + adx + mc + w + s + lo)
"""
    cpp = transpile(src)
    for cls in ("ta::RSI", "ta::RMA", "ta::ATR", "ta::DMI", "ta::MACD",
                "ta::WMA", "ta::SMA", "ta::Lowest"):
        assert _members(cpp, f"pineforge::source::FirstCallBound<{cls}>"), cls
    # ``lenP`` alone is an input.string choice: input-derived, so its EMA keeps
    # the constructor and the runtime reset re-reads the input (the
    # request.security helpers row; tests/test_e2e_lane_compositions.py).
    [ema] = _members(cpp, "ta::EMA")
    assert re.search(rf"^\s*{ema} = ta::EMA\(.*get_input_string\(\"Preset\"", cpp, re.M)
    # The factory spells the length context-free: the input through its
    # override-aware getter, syminfo through the engine's record.
    assert 'simple_ta_length(' in cpp and '"rsi", "length")' in cpp
    assert '"dmi", "diLength")' in cpp
    # adxSmoothing is ``lenP``, the input.string choice: the factory reads it
    # as the constructor's reset does, next to the simple diLength.
    dmi = next(line for line in cpp.splitlines() if "_ta_dmi_" in line and ".compute(" in line)
    assert '"dmi", "adxSmoothing")' not in dmi and 'get_input_string("Preset"' in dmi
    assert 'get_input_string("Preset"' in cpp and "syminfo_.ticker" in cpp
    # A simple window length reads the constant-length ring, not the series
    # classes.
    assert not _members(cpp, "pineforge::source::SeriesLowest")
    compile_cpp(cpp, label="simple_lengths")


def test_supertrend_factor_is_passed_on_the_call() -> None:
    src = """//@version=6
strategy("supertrend factor")
atrLen = input.int(10, "ATR Length")
var float mult = 1.75
[d1, d2, adx] = ta.dmi(14, 14)
if adx > 25
    mult := 1.75
else if adx < 20
    mult := 4.5
[st, dir] = ta.supertrend(mult, atrLen)
plot(st + dir)
"""
    cpp = transpile(src)
    assert _members(cpp, "pineforge::source::PineSupertrend")
    assert re.search(
        r"\.compute\(pineforge::source::ta_number\(mult\), "
        r"pineforge::source::ta_number\(get_input_int\(\"ATR Length\", 10\)\), "
        r"current_bar_\.high, current_bar_\.low, current_bar_\.close\)", cpp)
    compile_cpp(cpp, label="supertrend_factor")


def test_user_function_clones_lower_per_call_site() -> None:
    src = """//@version=6
strategy("clones")
lowOf(n) => ta.lowest(low, n)
fixed = lowOf(5)
dynamic = lowOf(math.max(1, ta.barssince(close > open) + 1))
plot(fixed + dynamic)
"""
    cpp = transpile(src)
    # The constant clone keeps its constructor; the series one is re-windowed.
    assert re.search(r"ta::Lowest\s+_ta_lowest_\w+;", cpp)
    assert _members(cpp, "pineforge::source::SeriesLowest")
    compile_cpp(cpp, label="clones")


def test_request_security_payload_lengths() -> None:
    src = """//@version=6
strategy("security payload")
base = input.int(14, "Base")
len = syminfo.type == "crypto" ? 9 : base
n = math.max(1, ta.barssince(close > open) + 1)
r = request.security(syminfo.tickerid, "60", ta.rsi(close, len))
lo = request.security(syminfo.tickerid, "60", ta.lowest(low, n))
plot(r + lo)
"""
    cpp = transpile(src)
    rsi = re.search(r"pineforge::source::FirstCallBound<ta::RSI>\s+(_sec\d+__ta_rsi_\d+);", cpp)
    lowest = re.search(r"pineforge::source::SeriesLowest\s+(_sec\d+__ta_lowest_\d+);", cpp)
    assert rsi and lowest
    assert re.search(rf"{rsi.group(1)}\.compute\(\[&\]\(\) \{{ return ta::RSI\(", cpp)
    assert re.search(rf"{lowest.group(1)}\.compute\(bar\.low, pineforge::source::ta_number\(", cpp)
    compile_cpp(cpp, label="security_payload")


def test_helper_bound_security_lengths_follow_each_call() -> None:
    # One helper reached from two request.security payloads with different
    # simple lengths: each requested context builds its EMA from the length
    # its own call binds, not from the first call site's.
    src = """//@version=6
strategy("helper-bound lengths")
f(n) => ta.ema(close, n)
lenA = syminfo.type == "crypto" ? 9 : 14
lenB = syminfo.type == "crypto" ? 21 : 30
a = request.security(syminfo.tickerid, "60", f(lenA))
b = request.security(syminfo.tickerid, "240", f(lenB))
plot(a + b)
"""
    cpp = transpile(src)
    factories = re.findall(
        r"(_sec\d+__ta_ema_\w+)\.compute\(\[&\]\(\) \{ return ta::EMA\(pineforge::source::"
        r"simple_ta_length\((.*?), \"ema\", \"length\"\)\); \}", cpp)
    assert factories
    lengths = {member.split("__")[0]: args for member, args in factories}
    assert any("9" in args and "14" in args for args in lengths.values()), lengths
    assert any("21" in args and "30" in args for args in lengths.values()), lengths
    for args in lengths.values():
        assert not ("9" in args and "30" in args), lengths
    compile_cpp(cpp, label="helper_bound_security")


def _compute_line(cpp: str, member: str) -> str:
    return next(line for line in cpp.splitlines()
                if re.search(rf"\b{re.escape(member)}\.compute\(", line))


def test_a_length_shadowed_in_a_block_reads_the_block() -> None:
    # The if-block's ``n`` and the loop variable shadow the top-level simple
    # ``n``: those calls read the series local; the top-level call reads the
    # simple one.
    src = """//@version=6
strategy("shadowed lengths")
n = syminfo.type == "crypto" ? 10 : 20
top = ta.highest(high, n)
float out = na
if close > open
    n = bar_index % 7 + 1
    out := ta.highest(high, n)
float acc = 0.0
for n = 1 to 3
    acc += ta.lowest(low, n)
if out + acc > top
    strategy.entry("L", strategy.long)
"""
    cpp = transpile(src)
    [local] = _members(cpp, "pineforge::source::SeriesHighest")
    [loop] = _members(cpp, "pineforge::source::SeriesLowest")
    [top] = _members(cpp, "pineforge::source::FirstCallBound<ta::Highest>")
    body = cpp.split("int n = ", 1)[1]
    assert f"{local}.compute(current_bar_.high, pineforge::source::ta_number(n))" in body
    assert re.search(r"for \(int n = [^\n]*\n[^\n]*" + re.escape(
        f"{loop}.compute(current_bar_.low, pineforge::source::ta_number(n))"), cpp)
    assert '"crypto"' in _compute_line(cpp, top)
    compile_cpp(cpp, label="shadowed_lengths")


def test_a_series_length_shadowed_in_a_block_keeps_the_refusal() -> None:
    src = """//@version=6
strategy("shadowed sma")
n = syminfo.type == "crypto" ? 10 : 20
float out = na
if close > open
    n = bar_index % 7 + 1
    out := ta.sma(close, n)
plot(out)
"""
    with pytest.raises(CompileError, match="Unsupported TA constructor length 'n'"):
        transpile(src)


def test_an_explicit_series_declaration_is_rewindowed() -> None:
    # ``series int m`` of a simple value is series to TradingView: a sparse
    # call re-windows, while ``simple int k`` reads the constant ring.
    src = """//@version=6
strategy("declared qualifiers")
series int m = syminfo.type == "crypto" ? 4 : 8
simple int k = syminfo.type == "crypto" ? 4 : 8
float a = na
float c = na
if bar_index % 9 < 2
    a := ta.lowest(low, m)
    c := ta.lowest(low, k)
plot(a + c)
"""
    cpp = transpile(src)
    [series] = _members(cpp, "pineforge::source::SeriesLowest")
    [simple] = _members(cpp, "pineforge::source::FirstCallBound<ta::Lowest>")
    assert "ta_number(m)" in _compute_line(cpp, series)
    assert '"crypto"' in _compute_line(cpp, simple)
    compile_cpp(cpp, label="declared_qualifiers")


@pytest.mark.parametrize("series_first", [False, True])
def test_helper_bound_security_copies_are_planned_per_call(series_first: bool) -> None:
    # One helper reached from two request.security payloads: each copy takes
    # the lowering of the length its own call binds, in either call order.
    calls = ['a = request.security(syminfo.tickerid, "60", f(lenA))',
             'b = request.security(syminfo.tickerid, "240", f(bar_index % 5 + 1))']
    if series_first:
        calls.reverse()
    src = ("//@version=6\nstrategy(\"helper copies\")\nf(n) => ta.highest(high, n)\n"
           "lenA = syminfo.type == \"crypto\" ? 9 : 14\n" + "\n".join(calls) + "\nplot(a + b)\n")
    cpp = transpile(src)
    simple_sec, series_sec = (1, 0) if series_first else (0, 1)
    assert _member_type(cpp, f"_sec{simple_sec}__ta_highest_1") == \
        "pineforge::source::FirstCallBound<ta::Highest>"
    assert _member_type(cpp, f"_sec{series_sec}__ta_highest_1") == \
        "pineforge::source::SeriesHighest"
    # A payload's bar_index is the requested bar's count (CG-OPEN-ITEMS
    # 41c5e4f; tests/test_e2e_cgint4_compositions.py runs the window).
    assert f"_sec{series_sec}_bar_index_" in _compute_line(cpp, f"_sec{series_sec}__ta_highest_1")
    assert '"crypto"' in _compute_line(cpp, f"_sec{simple_sec}__ta_highest_1")
    compile_cpp(cpp, label=f"helper_copies_{series_first}")


def test_a_payload_length_reads_the_requested_timeframe() -> None:
    # TradingView evaluates the payload in the requested context:
    # timeframe.isdaily is true inside a "D" request on any chart.
    src = """//@version=6
strategy("payload timeframe")
r = request.security(syminfo.tickerid, "D", ta.rsi(close, timeframe.isdaily and syminfo.type == "crypto" ? 9 : 14))
plot(r)
"""
    cpp = transpile(src)
    line = _compute_line(cpp, "_sec0__ta_rsi_1")
    assert 'tf_is_daily("D")' in line and "script_tf_" not in line
    compile_cpp(cpp, label="payload_timeframe")


def test_a_callable_request_security_takes_each_call_sites_length() -> None:
    # Cloned per call site (two timeframes): each copy its call's length.
    src = """//@version=6
strategy("callable copies")
lenA = syminfo.type == "crypto" ? 9 : 14
g(tf, len) => request.security(syminfo.tickerid, tf, ta.highest(high, len))
a = g("60", lenA)
b = g("240", syminfo.type == "crypto" ? 21 : 30)
c = g("120", 7)
plot(a + b + c)
"""
    cpp = transpile(src)
    lines = {sec: _compute_line(cpp, f"_sec{sec}__ta_highest_1") for sec in range(3)}
    assert "(9) : (14)" in lines[0]
    assert "(21) : (30)" in lines[1]
    assert "ta::Highest(7)" in lines[2]
    compile_cpp(cpp, label="callable_copies")


def test_one_evaluator_for_different_lengths_is_refused() -> None:
    # Not cloned (one timeframe): a single evaluator cannot serve two lengths.
    src = """//@version=6
strategy("shared evaluator")
lenA = syminfo.type == "crypto" ? 9 : 14
g(len) => request.security(syminfo.tickerid, "60", ta.highest(high, len))
a = g(lenA)
b = g(bar_index % 5 + 1)
plot(a + b)
"""
    with pytest.raises(CompileError, match="its call sites pass different lengths"):
        transpile(src)


@pytest.mark.parametrize("call", [
    "ta.sma(close, n)", "ta.wma(close, n)", "ta.stdev(close, n)",
    "ta.rsi(close, n)", "ta.ema(close, n)", "ta.atr(n)",
])
def test_other_series_lengths_stay_refused(call: str) -> None:
    src = ("//@version=6\nstrategy(\"refused\")\n"
           "n = math.max(1, ta.barssince(close > open) + 1)\n"
           f"x = {call}\nplot(x)\n")
    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        transpile(src)


_CONSTANT_AND_INPUT = """//@version=6
strategy("constant and input lengths")
len = input.int(14, "Length")
fast = len * 2 - 1
up = ta.crossover(close, ta.sma(close, 9))
lo = ta.lowest(low, 10)
hi = ta.highest(high, len)
r = ta.rsi(close, fast)
[st, dir] = ta.supertrend(3.0, len)
e = request.security(syminfo.tickerid, "60", ta.ema(close, len))
if up and close > lo
    strategy.entry("L", strategy.long)
plot(hi + r + st + dir + e)
"""


def test_constant_and_input_lengths_keep_their_cpp(tmp_path: Path) -> None:
    parent = reference_codegen("7a39cb3")
    if parent is None:
        pytest.skip("git or commit 7a39cb3 unavailable")
    pine = tmp_path / "strategy.pine"
    pine.write_text(_CONSTANT_AND_INPUT, encoding="utf-8")
    ours = transpile_json(pine)
    before = transpile_json(pine, parent)
    assert ours["ok"] and before["ok"]
    assert ours["cpp"] == before["cpp"]
    assert "pine_ta_length.hpp" not in ours["cpp"]
