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
    for cls in ("ta::RSI", "ta::EMA", "ta::RMA", "ta::ATR", "ta::DMI", "ta::MACD",
                "ta::WMA", "ta::SMA", "ta::Lowest"):
        assert _members(cpp, f"pineforge::source::FirstCallBound<{cls}>"), cls
    # The factory spells the length context-free: the input through its
    # override-aware getter, syminfo through the engine's record.
    assert 'simple_ta_length(' in cpp and '"rsi", "length")' in cpp
    assert '"dmi", "diLength")' in cpp and '"dmi", "adxSmoothing")' in cpp
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
