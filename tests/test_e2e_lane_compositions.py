"""Where two lanes integrated together change one function, or lower one
shape, the integration keeps both lanes' rules (integration lane CGINT2:
CG-POPFIX, K-RUNERR, K-TA-DYNLEN, CG-SECURITY-2 and CG-SESSION-2 on one
branch). Each case below runs the composition end to end -- ``transpile_json``,
the built runtime, ``run_strategy.py`` -- against a spelling that reaches each
lane's rule on its own, or pins the C++ where the rules decide the lowering:

* ``SecurityEmitter._emit_security_evaluators``: CG-SECURITY-2 moved the
  evaluator body into ``_emit_security_evaluator`` (a per-variant prologue,
  all or nothing per evaluator); K-TA-DYNLEN lets a TA constructor argument
  that depends on a rebound mutable global run after the rebinds when its
  lowering reads the length on the call. One payload holding both, a
  ``ta.lowest`` whose length a ``var`` global rebinds and a user call under
  ``nz()``, equals the two payloads apart, bar for bar.
* TA constructor arguments CG-SECURITY-2's spelled string literals bring to
  the constructor: an ``input.string`` choice of a length is input-derived and
  keeps the constructor and its reset (CG-SECURITY-2); any other such
  argument -- a ``syminfo.*`` or ``timeframe.*`` comparison, or a choice of a
  float parameter, which the reset would cast to an int -- keeps K-TA-DYNLEN's
  lowering, spelled from the declarations as K-TA-DYNLEN spelled it: a
  payload's ``timeframe.*`` is the requested timeframe, a float keeps its
  value, a callable's one evaluator refuses call sites passing different
  lengths, and one it cannot spell stays refused.
* ``session.<flag>[k]`` under a builtin call in a payload: CG-SECURITY-2's
  builder lowers a builtin's arguments on the requested clock, and
  CG-SESSION-2 keeps a payload's flag history in a bool Series there, so
  ``nz(session.ismarket[1] ? 1.0 : na)`` equals the direct read. An
  evaluator CG-SECURITY-2 keeps on the chart's terms still refuses it.
* ``TopLevelEmitter._emit_func_def``: K-RUNERR widens a declared-int
  parameter an epoch reaches, CG-SESSION-2 emits a function reading a flag at
  an offset once per call site: a function doing both equals the top-level
  spelling.
* ``TypeInferer._infer_type``: K-RUNERR types ``chart.is_*`` as bool,
  CG-SECURITY-2 types a string payload ``std::string``: the requested
  ``str.tostring(chart.is_standard)`` reads ``"true"``.
* ``CodeGen.generate``: K-RUNERR's 64-bit epoch global, CG-SECURITY-2's
  string helper-tuple element, K-TA-DYNLEN's constant-length class built on
  the first call and CG-SESSION-2's flag Series in one TU each equal a
  spelling of their own.
* ``scripts/gen_host_members.py``: CG-SESSION-2 derives the reserved host
  members from the emitter's string constants, leaving out the Pine names the
  emitter only matches on; K-TA-DYNLEN's ``_SIMPLE_STR_FUNCS`` set names
  ``str.replace``'s ``replace``, a member of the engine host. A set literal is
  a lookup, so the derived set stays CG-SESSION-2's, and a script variable
  named ``replace`` keeps its name and reads its own value beside
  ``str.replace``.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok, same,
    skip_unless_e2e_env, transpile_json,
)
from tests.test_e2e_session_history import EXTENDED, _replay, _replay_stamps

HEAD = '//@version=6\nstrategy("lane composition", overlay=true)\n'
BARS = 4000


def _values(records: list[dict], name: str) -> list[float]:
    return [rec["value"] for rec in records if rec["name"] == name]


# ---------------------------------------------------------------------------
# Runs on the corpus 15m chart feed
# ---------------------------------------------------------------------------

EVALUATOR = HEAD + '''var int n = 1
if close > open
    n := n % 5 + 1
f() => close - open
both = request.security(syminfo.tickerid, "60", ta.lowest(low, n) + nz(f()))
// @pf-trace both=both
'''
EVALUATOR_APART = HEAD + '''var int n = 1
if close > open
    n := n % 5 + 1
lo = request.security(syminfo.tickerid, "60", ta.lowest(low, n))
d = request.security(syminfo.tickerid, "60", close - open)
// @pf-trace both=lo + d
'''

TF_LENGTH = HEAD + '''lenT = timeframe.period == "D" ? 9 : 14
r = request.security(syminfo.tickerid, "D", ta.rsi(close, lenT))
// @pf-trace r=r
'''
TF_LENGTH_9 = HEAD + '''r = request.security(syminfo.tickerid, "D", ta.rsi(close, 9))
// @pf-trace r=r
'''
TF_LENGTH_14 = HEAD + '''r = request.security(syminfo.tickerid, "D", ta.rsi(close, 14))
// @pf-trace r=r
'''

CHART_BOOL = HEAD + '''s = request.security(syminfo.tickerid, "60", str.tostring(chart.is_standard))
c = str.tostring(chart.is_standard)
// @pf-trace ok=s == "true" ? 1 : 0
// @pf-trace empty=s == "" ? 1 : 0
// @pf-trace chart=c == "true" ? 1 : 0
'''

MEMBERS = HEAD + '''var int t0 = na
if na(t0)
    t0 := time
int alias = t0
h() => [close, close > open ? "up" : "down"]
[hc, hs] = request.security(syminfo.tickerid, "60", h())
hc_ref = request.security(syminfo.tickerid, "60", close)
hs_ref = request.security(syminfo.tickerid, "60", close > open ? 1 : 0)
lenS = syminfo.type == "crypto" ? 9 : 14
r = ta.rsi(close, lenS)
r_ref = ta.rsi(close, 9)
sm() => session.ismarket
m1 = session.ismarket[1]
m1_ref = sm()[1]
// @pf-trace alias=alias % 1000000007
// @pf-trace hc=hc
// @pf-trace hc_ref=hc_ref
// @pf-trace hs=hs == "up" ? 1 : hs == "down" ? 0 : -1
// @pf-trace hs_ref=hs_ref
// @pf-trace r=r
// @pf-trace r_ref=r_ref
// @pf-trace m1=m1 ? 1 : 0
// @pf-trace m1_ref=m1_ref ? 1 : 0
'''

HOST_NAME = HEAD + '''replace = close * 2
t = str.replace("abc", "b", "c")
// @pf-trace rep=replace
// @pf-trace ref=close * 2
// @pf-trace t=t == "acc" ? 1 : 0
'''

FLOAT_SAR = HEAD + '''speed = input.string("Normal", "Speed", options=["Normal", "Slow"])
s = ta.sar(speed == "Normal" ? 0.02 : 0.01, 0.02, 0.2)
// @pf-trace s=s
'''
FLOAT_SAR_TWIN = HEAD + '''s = ta.sar(0.02, 0.02, 0.2)
// @pf-trace s=s
'''
FLOAT_BB = HEAD + '''[m, u, l] = ta.bb(close, 20, timeframe.period != "D" ? 2.5 : 2.0)
[pm, pu, pl] = request.security(syminfo.tickerid, "60", ta.bb(close, 20, timeframe.period != "D" ? 2.5 : 2.0))
// @pf-trace w=u - m
// @pf-trace pw=pu - pm
'''
FLOAT_BB_TWIN = HEAD + '''[m, u, l] = ta.bb(close, 20, 2.5)
[pm, pu, pl] = request.security(syminfo.tickerid, "60", ta.bb(close, 20, 2.5))
// @pf-trace w=u - m
// @pf-trace pw=pu - pm
'''
# Arguments whose context-free spelling is empty or unknown to the
# parameter table: a supertrend factor reassigned under an input.string
# choice (read on its first execution, like the same factor reassigned on
# every bar), a length reassigned after the call (the call reads 14), and a
# VWAP band multiplier chosen by an input.string.
FLOAT_MORE = HEAD + '''mode = input.string("Wide", "Mode", options=["Wide", "Narrow"])
m = 2.5
if mode == "Wide"
    m := 3.5
[st, d] = ta.supertrend(m, 10)
len = 14
hi = ta.highest(high, len)
if timeframe.period != "D"
    len := 5
[v, vu, vl] = ta.vwap(hlc3, timeframe.change("D"), mode == "Wide" ? 2.5 : 1.5)
// @pf-trace st=st
// @pf-trace hi=hi
// @pf-trace vu=vu
'''
FLOAT_MORE_TWIN = HEAD + '''m = 2.5
if bar_index >= 0
    m := 3.5
[st, d] = ta.supertrend(m, 10)
hi = ta.highest(high, 14)
[v, vu, vl] = ta.vwap(hlc3, timeframe.change("D"), 2.5)
// @pf-trace st=st
// @pf-trace hi=hi
// @pf-trace vu=vu
'''

BUILDS = {
    "evaluator": EVALUATOR, "evaluator_apart": EVALUATOR_APART,
    "tf_length": TF_LENGTH, "tf_length_9": TF_LENGTH_9, "tf_length_14": TF_LENGTH_14,
    "chart_bool": CHART_BOOL, "members": MEMBERS, "host_name": HOST_NAME,
    "float_sar": FLOAT_SAR, "float_sar_twin": FLOAT_SAR_TWIN,
    "float_bb": FLOAT_BB, "float_bb_twin": FLOAT_BB_TWIN,
    "float_more": FLOAT_MORE, "float_more_twin": FLOAT_MORE_TWIN,
}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("lane_compositions")
    feed = chart_feed_head(engine, base, BARS)
    outcomes = execute_all(engine, feed, base, {
        key: Build(source, trace=True) for key, source in BUILDS.items()})
    first_ts = int(feed.read_text().splitlines()[1].split(",", 1)[0])
    return outcomes, first_ts


def test_one_evaluator_holds_a_rebound_length_and_a_requested_call(runs):
    outcomes, _ = runs
    subject, apart = ok(outcomes, "evaluator"), ok(outcomes, "evaluator_apart")
    cpp = subject.transpiled["cpp"]
    body = cpp.split("void _eval_security_0(", 1)[1].split("\n    }\n", 1)[0]
    # K-TA-DYNLEN: the rebound length is read on the call, after the rebind.
    assert re.search(r"pineforge::source::SeriesLowest\s+_sec0__ta_lowest_\d+;", cpp)
    assert body.index("_sec0_n = ") < body.index(".compute(bar.low, pineforge::source::ta_number(_sec0_n))")
    # CG-SECURITY-2: the user call under nz() reads the requested bar.
    assert "nz_v = ((bar.close - bar.open))" in body and "f()" not in body
    print("evaluator composition:", assert_same_runs(subject, apart))


def test_a_payload_timeframe_length_reads_the_requested_timeframe(runs):
    outcomes, _ = runs
    subject = ok(outcomes, "tf_length")
    twin9, twin14 = ok(outcomes, "tf_length_9"), ok(outcomes, "tf_length_14")
    # The constructor reset would compare the chart's "15" (length 14).
    assert "pineforge::source::FirstCallBound<ta::RSI> _sec0__ta_rsi_" in subject.transpiled["cpp"]
    summary = assert_same_runs(subject, twin9)
    values9 = _values(twin9.traces["default"], "r")
    values14 = _values(twin14.traces["default"], "r")
    assert any(not same(a, b) for a, b in zip(values9, values14))
    print("payload timeframe length:", summary)


def test_a_float_choice_keeps_its_value(runs):
    # An input.string choice of ta.sar's start and a timeframe comparison of
    # ta.bb's multiplier, on the chart and in a payload: the literal twin's
    # values bar for bar (the reset path would pass (int)0.02 and 2).
    outcomes, _ = runs
    sar, bb = ok(outcomes, "float_sar"), ok(outcomes, "float_bb")
    assert "pineforge::source::FirstCallBound<ta::SAR>" in sar.transpiled["cpp"]
    factories = [line for line in bb.transpiled["cpp"].splitlines()
                 if "return ta::BB(" in line]
    assert len(factories) == 2 and all("(2.5)" in line and "(int)" not in line
                                       for line in factories)
    assert '"60" != std::string("D")' in factories[1]
    more = ok(outcomes, "float_more")
    bands = [line for line in more.transpiled["cpp"].splitlines()
             if "return _PFAnchoredVWAPBands(" in line]
    assert bands and all("(2.5)" in line and "(int)" not in line for line in bands)
    print("float choices:", assert_same_runs(sar, ok(outcomes, "float_sar_twin")),
          "|", assert_same_runs(bb, ok(outcomes, "float_bb_twin")),
          "|", assert_same_runs(more, ok(outcomes, "float_more_twin")))


def test_a_requested_chart_flag_string_reads_true(runs):
    outcomes, _ = runs
    subject = ok(outcomes, "chart_bool")
    assert "std::string _req_sec_0 = na<std::string>();" in subject.transpiled["cpp"]
    records = subject.traces["default"]
    ok_, empty, chart = (_values(records, name) for name in ("ok", "empty", "chart"))
    assert chart and all(v == 1.0 for v in chart)
    assert all(a + b == 1.0 for a, b in zip(ok_, empty))
    assert ok_.count(1.0) > len(ok_) * 0.9, (ok_.count(1.0), len(ok_))


def test_each_lanes_members_in_one_tu(runs):
    outcomes, first_ts = runs
    subject = ok(outcomes, "members")
    cpp = subject.transpiled["cpp"]
    assert re.search(r"^\s*int64_t alias = ", cpp, re.M)
    assert re.search(r"^\s*std::string hs = ", cpp, re.M)
    assert "pineforge::source::FirstCallBound<ta::RSI> _ta_rsi_" in cpp
    assert "Series<bool> _pf_session_hist_ismarket" in cpp
    records = subject.traces["default"]
    alias = _values(records, "alias")
    assert alias and all(v == first_ts % 1000000007 for v in alias)
    for name in ("hc", "r", "m1"):
        got, want = _values(records, name), _values(records, f"{name}_ref")
        assert len(got) == len(want) == len(alias)
        assert all(same(a, b) for a, b in zip(got, want)), name
    hs, hs_ref = _values(records, "hs"), _values(records, "hs_ref")
    defined = [(a, b) for a, b in zip(hs, hs_ref) if not math.isnan(b)]
    assert len(defined) > len(hs) * 0.9 and all(a == b for a, b in defined)
    assert all(a == -1.0 for a, b in zip(hs, hs_ref) if math.isnan(b))


def test_a_matched_pine_name_is_not_reserved(runs):
    from pineforge_codegen.codegen.host_members import HOST_MEMBER_NAMES
    assert "replace" not in HOST_MEMBER_NAMES
    outcomes, _ = runs
    subject = ok(outcomes, "host_name")
    cpp = subject.transpiled["cpp"]
    assert re.search(r"^\s*double replace = ", cpp, re.M)
    records = subject.traces["default"]
    rep, ref = _values(records, "rep"), _values(records, "ref")
    assert rep and rep == ref
    assert all(v == 1.0 for v in _values(records, "t"))


# ---------------------------------------------------------------------------
# Runs on the session flags' extended-hours bars (CG-SESSION-2's harness)
# ---------------------------------------------------------------------------

SESSION_BUILTIN = '''//@version=6
strategy("session history under a builtin", overlay=true)
a = request.security(syminfo.tickerid, "60", nz(session.ismarket[1] ? 1.0 : na))
a_ref = request.security(syminfo.tickerid, "60", session.ismarket[1] ? 1.0 : 0.0)
c = request.security(syminfo.tickerid, "60", math.max(nz(session.ispremarket[2] ? 1.0 : na), 0.0))
c_ref = request.security(syminfo.tickerid, "60", session.ispremarket[2] ? 1.0 : 0.0)
if a > 0 or c > 0
    strategy.entry("L", strategy.long)
// @pf-trace a=a
// @pf-trace a_ref=a_ref
// @pf-trace c=c
// @pf-trace c_ref=c_ref
'''

EPOCH_PARAMETER = '''//@version=6
strategy("an epoch parameter beside a flag's history", overlay=true)
f(int t) => (session.ismarket[1] ? 1 : 0) + (t > 1700000000000 ? 10 : 0)
a = f(time)
b = f(time - 900000)
r = (session.ismarket[1] ? 1 : 0) + (time > 1700000000000 ? 10 : 0)
if a != r or b != r
    strategy.entry("L", strategy.long)
// @pf-trace a=a
// @pf-trace b=b
// @pf-trace r=r
'''


@pytest.fixture(scope="module")
def session_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("lane_compositions_session")
    stamps = _replay_stamps(EXTENDED)
    return {key: _replay(engine, base / key, source, stamps)
            for key, source in (("builtin", SESSION_BUILTIN), ("epoch", EPOCH_PARAMETER))}


def test_session_history_under_a_builtin_is_the_direct_read(session_runs):
    replay = session_runs["builtin"]
    assert len(re.findall(r"Series<bool> _sec\d+_expr_hist_\d+", replay.cpp)) == 4
    n = len(_replay_stamps(EXTENDED))
    for name in ("a", "c"):
        got, want = _values(replay.traces, name), _values(replay.traces, f"{name}_ref")
        assert len(got) == len(want) == n
        assert all(same(x, y) for x, y in zip(got, want)), name
    assert {0.0, 1.0} <= set(_values(replay.traces, "c"))


def test_session_history_beside_a_chart_read_is_still_refused(tmp_path: Path) -> None:
    # CG-SECURITY-2 keeps this evaluator on the chart's terms (the builtin
    # also reads the per-run global g); there CG-SESSION-2 has no requested
    # history. A global the builder re-evaluates on the requested bar
    # (``g = close``, CG-OPEN-ITEMS 5b3791d) no longer keeps it there:
    # tests/test_e2e_cgint4_compositions.py runs that one.
    pine = tmp_path / "strategy.pine"
    pine.write_text(HEAD + 'g = timeframe.multiplier * 1.0\n'
                    'x = request.security(syminfo.tickerid, "60", nz(session.ismarket[1] ? g : na)) > 0\n'
                    'if x\n    strategy.entry("L", strategy.long)\n', encoding="utf-8")
    result = transpile_json(pine)
    assert not result["ok"]
    [error] = [d for d in result["diagnostics"] if d["severity"] == "error"]
    assert (error["line"], error["col"]) == (4, 65) and "request.security" in error["message"]


def test_an_epoch_parameter_beside_a_flags_history(session_runs):
    replay = session_runs["epoch"]
    assert re.search(r"^\s*int f_cs0\(int64_t t\) \{", replay.cpp, re.M)
    assert re.search(r"^\s*int f_cs1\(int64_t t\) \{", replay.cpp, re.M)
    r = _values(replay.traces, "r")
    assert len(r) == len(_replay_stamps(EXTENDED)) and {10.0, 11.0} <= set(r)
    for name in ("a", "b"):
        assert _values(replay.traces, name) == r, name
    assert replay.trades.decode().count("\n") <= 1   # no entry: a == b == r


# ---------------------------------------------------------------------------
# The lowering K-TA-DYNLEN and CG-SECURITY-2 give a TA length (no engine)
# ---------------------------------------------------------------------------

LENGTHS = HEAD + '''mode = input.string("B", "Mode", options=["A", "B"])
lenI = mode == "A" ? 5 : 10
lenS = syminfo.type == "crypto" ? 9 : 14
lenT = timeframe.period == "D" ? 9 : 14
f(len) => ta.sma(close, len)
cI = ta.ema(close, lenI)
sI = request.security(syminfo.tickerid, "60", f(lenI))
sS = request.security(syminfo.tickerid, "60", f(lenS))
sT = request.security(syminfo.tickerid, "D", ta.rsi(close, lenT))
if cI + sI + sS + sT > 0
    strategy.entry("L", strategy.long)
'''


def _member_type(cpp: str, member: str) -> str:
    match = re.search(rf"^\s*(\S.*?)\s+{re.escape(member)};$", cpp, re.M)
    assert match, member
    return match.group(1)


def _line(cpp: str, pattern: str) -> str:
    return next(line for line in cpp.splitlines() if re.search(pattern, line))


def test_each_ta_length_takes_its_lanes_lowering() -> None:
    cpp = transpile(LENGTHS)
    choice = 'get_input_string("Mode", std::string("B")) == std::string("A")'
    # CG-SECURITY-2: the input.string choice keeps the constructor, reset
    # from the input, on the chart and in the payload.
    assert _member_type(cpp, "_ta_ema_2") == "ta::EMA"
    assert choice in _line(cpp, r"^\s*_ta_ema_2 = ta::EMA\(")
    assert _member_type(cpp, "_sec0__ta_sma_1") == "ta::SMA"
    assert choice in _line(cpp, r"^\s*_sec0__ta_sma_1 = ta::SMA\(")
    # K-TA-DYNLEN: the syminfo comparison is built on the call's first
    # execution, the timeframe one in the requested timeframe.
    assert _member_type(cpp, "_sec1__ta_sma_1") == "pineforge::source::FirstCallBound<ta::SMA>"
    assert 'syminfo_.type == std::string("crypto")' in _line(cpp, r"_sec1__ta_sma_1\.compute\(")
    assert _member_type(cpp, "_sec2__ta_rsi_3") == "pineforge::source::FirstCallBound<ta::RSI>"
    rsi = _line(cpp, r"_sec2__ta_rsi_3\.compute\(")
    assert '"D" == std::string("D")' in rsi and "script_tf_" not in rsi
    compile_cpp(cpp, label="lane composition lengths")


def test_a_shared_evaluator_refuses_different_simple_lengths() -> None:
    # K-TA-DYNLEN's rule for a callable's one request.security evaluator:
    # the constructor would size both calls from the first call site's. An
    # input.string choice (CG-SECURITY-2) no longer shares one: XSYM-A copies
    # the request per value of a payload parameter it can lower, one
    # evaluator per call path (tests/test_e2e_cgint3_compositions.py). A
    # simple syminfo length is no such value and still shares it.
    src = HEAD + '''g(len) => request.security(syminfo.tickerid, "60", ta.ema(close, len))
a = g(syminfo.type == "crypto" ? 9 : 14)
b = g(syminfo.type == "crypto" ? 21 : 30)
if a > b
    strategy.entry("L", strategy.long)
'''
    with pytest.raises(CompileError, match="its call sites pass different lengths"):
        transpile(src)


def test_a_length_the_lowering_cannot_spell_stays_refused() -> None:
    # A timeframe comparison reached through a user function: K-TA-DYNLEN
    # cannot spell it context-free and refused it; the constructor reset
    # would read the chart's timeframe in the "D" payload.
    src = HEAD + '''f_len() => timeframe.period == "D" ? 9 : 14
lenT = f_len()
r = request.security(syminfo.tickerid, "D", ta.rsi(close, lenT))
if r > 50
    strategy.entry("L", strategy.long)
'''
    with pytest.raises(CompileError, match="Unsupported TA constructor length 'lenT'"):
        transpile(src)


def test_literals_that_reached_the_constructor_before_keep_it() -> None:
    # A literal written in a helper's call always reached the constructor
    # (its AST is stable), in both lanes: it keeps that C++.
    src = HEAD + '''f(n) => ta.rsi(close, n)
r = request.security(syminfo.tickerid, "60", f(syminfo.type == "crypto" ? 9 : 14))
if r > 50
    strategy.entry("L", strategy.long)
'''
    cpp = transpile(src)
    assert _member_type(cpp, "_sec0__ta_rsi_1") == "ta::RSI"


def test_a_derived_name_is_spelled_from_its_declarations() -> None:
    # ``b`` reads ``a``, a timeframe comparison: the constant class is built
    # from the declarations, not from the chart's member ``b``.
    src = HEAD + '''a = timeframe.period == "D" ? 5 : 20
b = a * 2
e = ta.ema(close, b)
if close > e
    strategy.entry("L", strategy.long)
'''
    cpp = transpile(src)
    line = _line(cpp, r"_ta_ema_\d+\.compute\(")
    assert 'script_tf_ == std::string("D")' in line and "simple_ta_length((b)" not in line


def test_an_input_choice_read_twice_is_still_input_derived() -> None:
    # ``c`` is read twice by ``len``: still an input.string choice.
    src = HEAD + '''mode = input.string("Slow", "Mode", options=["Fast", "Slow"])
m = "A"
c = m == "A" ? 1 : 2
len = mode == "Fast" ? 5 + c : 20 + c
e = ta.ema(close, len)
if close > e
    strategy.entry("L", strategy.long)
'''
    cpp = transpile(src)
    assert _member_type(cpp, "_ta_ema_1") == "ta::EMA"
