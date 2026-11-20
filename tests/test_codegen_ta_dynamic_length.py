"""The C++ of a ``ta.*`` call whose length (or supertrend factor) the
constructor cannot take -- neither a compile-time constant nor an
input/timeframe expression the runtime reset re-reads.

TradingView answers such a call by its Pine qualifier; the
rules and their tapes are in the engine header
``pineforge/source/pine_ta_length.hpp`` and the replays in
``tests/test_e2e_ta_dynamic_length.py``):

* series length, ta.highest / ta.lowest / ta.highestbars / ta.lowestbars:
  ``pineforge::source::Series*``, the length passed on every call;
* series length, ta.percentile_nearest_rank:
  ``pineforge::source::SeriesPercentileNearestRank`` appends on every call;
* ta.supertrend: ``pineforge::source::PineSupertrend``, factor and atrPeriod
  passed on the call and read on its first execution;
* simple length (fixed for the run) of any family:
  ``pineforge::source::FirstCallBound<class>``, the constant-length class built
  on the call's first execution, the length checked by ``simple_ta_length``;
* series length of another family: still refused (TradingView refuses it for
  the recursive ones; the other window functions are not lowered yet).

Constant- and input-length calls keep their constructor cadence; EMA-family
objects use the paired checked source adapters for every qualifier.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp, run_emitted_tu
from tests._checked_ta_cpp import with_checked_ta
from tests._e2e import reference_codegen, transpile_json
from tests._legacy_cpp import without_restore_script_state, without_trade_history_policy


# Accepted scaffold for whole-C++ noninterference only; historical semantic
# and compile comparisons keep their separate pre-feature references.
CPP_IDENTITY_REFERENCE = "1e6eb83354b74ede1326fe55e576e91ea81f156b"


def _member_type(cpp: str, member: str) -> str:
    match = re.search(rf"^\s*(\S.*?)\s+{re.escape(member)};$", cpp, re.M)
    assert match, member
    return match.group(1)


def _members(cpp: str, cpp_type: str) -> list[str]:
    return re.findall(rf"^\s*{re.escape(cpp_type)}\s+(\w+);$", cpp, re.M)


def _percentile_compute_line(cpp: str) -> str:
    return next(
        line for line in cpp.splitlines()
        if "_ta_percentile_nearest_rank_" in line and ".compute(" in line
    )


def test_series_percentile_loop_length_uses_the_engine_series_class() -> None:
    src = """//@version=6
strategy("repro")
length = input.int(20, "Length")
float acc = 0.0
for len = 2 to 4
    acc += ta.percentile_nearest_rank(close, len, 50)
plot(acc)
"""
    cpp = transpile(src)
    assert _members(cpp, "pineforge::source::SeriesPercentileNearestRank")
    line = _percentile_compute_line(cpp)
    assert re.search(r"\.compute\([^,\n]+,\s*[^,\n]+,\s*[^)\n]+\)", line), line
    assert "pineforge::source::ta_number(len)" in line


def test_series_percentile_loop_counter_shadows_same_named_input() -> None:
    src = """//@version=6
strategy("shadowed percentile")
length = input.int(20, "Length")
float acc = 0.0
for length = 2 to 4
    acc += ta.percentile_nearest_rank(close, length, 50)
plot(acc)
"""
    cpp = transpile(src)
    line = _percentile_compute_line(cpp)
    assert "pineforge::source::ta_number(length)" in line
    assert 'get_input_int("Length"' not in line


def test_series_percentile_keeps_the_ema_refusal() -> None:
    cases = (
        (
            "ta.ema(close, length)",
            "PF-E5009",
            "ta::EMA",
            "",
        ),
    )
    reason = (
        "it is neither a compile-time constant nor derived from an input, so "
        "PineForge cannot size the indicator buffer."
    )
    for call, code, name, suffix in cases:
        src = (
            '//@version=6\nstrategy("refused loop length")\n'
            "float out = 0.0\n"
            "for length = 2 to 4\n"
            f"    out += {call}\n"
            "plot(out)\n"
        )
        with pytest.raises(CompileError) as caught:
            transpile(src)
        diagnostic = caught.value.diagnostics[0]
        assert diagnostic.code == code
        expected = (
            f"Unsupported TA constructor length 'length' for {name}: {reason}"
        )
        if suffix:
            expected += f" {suffix}"
        assert diagnostic.message == expected


def test_series_sma_loop_length_reads_the_loop_counter() -> None:
    # Same script bytes as the former ta.sma arm of the refusal test above.
    src = (
        '//@version=6\nstrategy("refused loop length")\n'
        "float out = 0.0\n"
        "for length = 2 to 4\n"
        "    out += ta.sma(close, length)\n"
        "plot(out)\n"
    )
    cpp = transpile(src)
    [member] = _members(cpp, "_PFSeriesSma")
    line = _compute_line(cpp, member)
    assert "current_bar_.close" in line
    assert "pineforge::source::ta_number(length)" in line
    compile_cpp(cpp, label="series_sma_loop_length")


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
    for cls in ("ta::RSI", "ta::RMA", "ta::ATR", "ta::DMI", "pineforge::source::PineMacd",
                "ta::WMA", "ta::SMA", "ta::Lowest"):
        assert _members(cpp, f"pineforge::source::FirstCallBound<{cls}>"), cls
    # ``lenP`` alone is an input.string choice: input-derived, so its EMA keeps
    # the constructor and the runtime reset re-reads the input (the
    # request.security helpers row; tests/test_e2e_lane_compositions.py).
    [ema] = _members(cpp, "pineforge::source::PineEma")
    assert re.search(rf"^\s*{ema} = pineforge::source::PineEma\(.*get_input_string\(\"Preset\"", cpp, re.M)
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
        r"(_sec\d+__ta_ema_\w+)\.compute\(\[&\]\(\) \{ return pineforge::source::PineEma\(pineforge::source::"
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


def test_a_series_sma_length_shadowed_in_a_block_reads_the_block() -> None:
    src = """//@version=6
strategy("shadowed sma")
n = syminfo.type == "crypto" ? 10 : 20
float out = na
if close > open
    n = bar_index % 7 + 1
    out := ta.sma(close, n)
plot(out)
"""
    cpp = transpile(src)
    [member] = _members(cpp, "_PFSeriesSma")
    line = _compute_line(cpp, member)
    assert "current_bar_.close" in line
    assert "pineforge::source::ta_number(n)" in line
    assert '"crypto"' not in line
    compile_cpp(cpp, label="shadowed_series_sma")


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
    "ta.wma(close, n)", "ta.stdev(close, n)",
    "ta.rsi(close, n)", "ta.ema(close, n)", "ta.atr(n)",
])
def test_other_series_lengths_stay_refused(call: str) -> None:
    src = ("//@version=6\nstrategy(\"refused\")\n"
           "n = math.max(1, ta.barssince(close > open) + 1)\n"
           f"x = {call}\nplot(x)\n")
    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        transpile(src)


def test_a_barssince_series_sma_length_is_bound_per_call() -> None:
    # Same script bytes as the former ta.sma parameter of the test above.
    src = ("//@version=6\nstrategy(\"refused\")\n"
           "n = math.max(1, ta.barssince(close > open) + 1)\n"
           "x = ta.sma(close, n)\nplot(x)\n")
    cpp = transpile(src)
    [member] = _members(cpp, "_PFSeriesSma")
    line = _compute_line(cpp, member)
    assert "current_bar_.close" in line
    assert "pineforge::source::ta_number(n)" in line
    compile_cpp(cpp, label="barssince_series_sma")


_SERIES_SMA_VALUE_DRIVER = r"""
#include <iostream>

int main() {
    std::vector<pineforge::Bar> bars(6);
    for (std::size_t i = 0; i < bars.size(); ++i) {
        bars[i].open = bars[i].high = bars[i].low = bars[i].close = double(i + 1);
        bars[i].volume = 1.0;
        bars[i].timestamp = 1704067200000LL + static_cast<long long>(i) * 60000LL;
    }
    GeneratedStrategy strategy;
    strategy.run(bars.data(), bars.size(), "1", "1");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << "\n";
        return 14;
    }
    std::cout << strategy.outer_trace << "\n" << strategy.inner_trace << "\n";
    return 0;
}
"""


def test_series_sma_values_follow_each_bars_length_and_binding() -> None:
    # Closes are 1..6. The top-level call reads n = bar_index % 3 + 1
    # (1, 2, 3, 1, 2, 3); the block call reads its own n = bar_index % 2 + 1
    # (1, 2, 1, 2, 1, 2). Each is the mean of the last n closes:
    #   outer: 1, (1+2)/2, (1+2+3)/3, 4, (4+5)/2, (4+5+6)/3
    #   inner: 1, (1+2)/2, 3, (3+4)/2, 5, (5+6)/2
    # A constant length, a length of 1, or the block call reading the
    # top-level n (bar 2 would be 2, not 3) all print different rows.
    src = (
        '//@version=6\nstrategy("series sma values")\n'
        "n = bar_index % 3 + 1\n"
        "s = ta.sma(close, n)\n"
        "float t = na\n"
        "if close > 0\n"
        "    n = bar_index % 2 + 1\n"
        "    t := ta.sma(close, n)\n"
        'var string outer_trace = ""\n'
        'var string inner_trace = ""\n'
        'outer_trace := outer_trace + str.tostring(s, "#.##") + ","\n'
        'inner_trace := inner_trace + str.tostring(t, "#.##") + ","\n'
        "plot(s + t)\n"
    )
    cpp = transpile(src)
    assert len(_members(cpp, "_PFSeriesSma")) == 2
    output = run_emitted_tu(
        cpp, _SERIES_SMA_VALUE_DRIVER, opt="-O0", label="series_sma_values",
    )
    assert output == "1,1.5,2,4,4.5,5,\n1,1.5,3,3.5,5,5.5,\n"


# Explicit sizing originally isolated TA lengths from LEGACY (7a39cb3):
# that build left an omitted initial_capital /
# default_qty_type / default_qty_value to the host's defaults, which are not
# TradingView's Pine v6 defaults (test_e2e_strategy_defaults).
# The active whole-C++ comparison uses the immutable accepted scaffold pinned
# by CPP_IDENTITY_REFERENCE; the probe's original sizing remains unchanged.
_CONSTANT_AND_INPUT = """//@version=6
strategy("constant and input lengths", initial_capital=1000000, default_qty_type=strategy.fixed, default_qty_value=1)
len = input.int(14, "Length")
fast = len * 2 - 1
up = ta.crossover(close, ta.sma(close, 9))
lo = ta.lowest(low, 10)
hi = ta.highest(high, len)
r = ta.rsi(close, fast)
[st, dir] = ta.supertrend(3.0, len)
e = request.security(syminfo.tickerid, "60", ta.ema(close, len))
p_const = ta.percentile_nearest_rank(close, 9, 50)
p_input = ta.percentile_nearest_rank(close, len, 50)
if up and close > lo
    strategy.entry("L", strategy.long)
plot(hi + r + st + dir + e + p_const + p_input)
"""


def test_constant_and_input_lengths_keep_their_cpp(tmp_path: Path) -> None:
    parent = reference_codegen(CPP_IDENTITY_REFERENCE)
    if parent is None:
        pytest.fail(f"required C++ identity reference {CPP_IDENTITY_REFERENCE} unavailable; "
                    "restore git history")
    pine = tmp_path / "strategy.pine"
    pine.write_text(_CONSTANT_AND_INPUT, encoding="utf-8")
    ours = transpile_json(pine)
    before = transpile_json(pine, parent)
    assert ours["ok"] and before["ok"]

    # d2e5855 writes direct Program tuple results into their existing members.
    # Require both exact replacements, preserving the complete reference
    # comparison and its existing rollback/trade-history normalisation.
    expected = before["cpp"]
    for name, field in (("st", "value"), ("dir", "direction")):
        assignment = f"{name} = _result__ta_supertrend_6.{field};"
        previous = "double " + assignment
        assert expected.count(previous) == 1
        expected = expected.replace(previous, assignment, 1)
    # The checked replay is the documented emission: its source adapter,
    # failure headers, guarded replay, typed invalid-argument catch and cache
    # clearing are all part of the expected C++.
    expected = with_checked_ta(expected, precalc=True)
    # The rollback hook leaves requested-evaluation state alone since the
    # fill-restore fix; everything else must stay identical.
    assert without_restore_script_state(
        without_trade_history_policy(ours["cpp"])
    ) == without_restore_script_state(without_trade_history_policy(expected))
    assert "#include <pineforge/source/pine_ta_length.hpp>" in ours["cpp"]
