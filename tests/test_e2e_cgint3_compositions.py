"""Where a lane integrated by CGINT3 meets a rule already on main, or two
CGINT3 lanes change one function, the integration keeps both rules
(integration lane CGINT3: W2-CG-LOWERING-TRIO, CG-W9-MISC and CG-W9-SEC on
main's CGINT2 and CG-SESSION-3). Each case runs the composition end to end --
``transpile_json``, the built runtime, ``run_strategy.py`` -- and pins the C++
where the rules decide the lowering. ``MAIN`` is the integration base, the
build before these lanes, replayed beside each case to show what it missed:

* ``TypeInferer._series_type_for`` / ``_expr_returns_wide_int``: K-RUNERR
  (on main) widens every name an epoch reaches to ``int64_t``; CG-W9-SEC
  c86c0de ends that provenance at ``/``, a comparison, ``and`` / ``or`` and
  ``not``, and widens only a name whose Pine type is no float, bool or
  string. Main stored ``hours = (time - time[1]) / 3600000.0`` in a
  ``Series<int64_t>`` and read 0 for a quarter hour; the integrated tree
  keeps floats and bools at their own types while int names, and a copy of
  one, keep their 64 bits.
* ``CallVisitor`` ``na(x)``: main reads a string ``na`` as the empty string
  (``.empty()``, with its warning), CG-W9-MISC asks a drawing's arena whether
  it is alive. One TU holds both: a deleted box is ``na`` and a string
  assigned after ``na`` is not; main read the deleted box as live.
* ``TopLevelEmitter._emit_func_def``: W2-CG-LOWERING-TRIO returns the value
  of a function's last statement whatever it is (``acc += v``), CG-SESSION-2
  emits a function reading a flag at an offset once per call site and pushes
  the flag at entry, K-RUNERR widens its ``int`` parameter an epoch reaches.
  A function doing all three equals the same function ending in ``acc``;
  main returned the default 0.0.
* ``CodeGen._prepare_inline_history_members``: CG-SESSION-2 keeps a flag read
  at an offset in a function by the calls of its call site
  (``_session_call_*``), CG-W9-FN keeps a script variable read through
  history there by the chart bars at its call site (``_fn_global_hist_*``).
  A function reading both, called on irregular bars, equals the two reads in
  functions of their own called on the same bars, and each differs from the
  chart's history of the same expression.
* A callable's ``request.security`` evaluator (XSYM-A x K-TA-DYNLEN x
  CG-SECURITY-2): XSYM-A copies a helper's request per value of a payload
  parameter it can lower, CG-SECURITY-2 keeps an ``input.string`` choice of a
  length on the constructor, and K-TA-DYNLEN refuses one evaluator whose call
  sites pass different lengths. Two calls passing different input choices now
  get an evaluator each, and each equals its literal twin at the default and
  under an override; main refused them, and a simple ``syminfo`` length, which
  XSYM-A does not copy, is still refused
  (``tests/test_e2e_lane_compositions.py``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._e2e import (
    Build, chart_feed_head, execute_all, ok, reference_codegen, same,
    skip_unless_e2e_env, transpile_json,
)
from tests.test_e2e_session_history import EXTENDED, _replay, _replay_stamps

# The integration base: codegen main before the CGINT3 picks.
MAIN = "fdcdcbb908b9ea6bdbb135802a7e2aed6a7ec3de"
HEAD = '//@version=6\nstrategy("cgint3 composition", overlay=true)\n'
BARS = 400


def _values(records: list[dict], name: str) -> list[float]:
    return [rec["value"] for rec in records if rec["name"] == name]


# ---------------------------------------------------------------------------
# An epoch's provenance ends at a float or a bool (CG-W9-SEC x K-RUNERR)
# ---------------------------------------------------------------------------

EPOCH = HEAD + '''var int t0 = time
hours = (time - time[1]) / 3600000.0
late = time > t0
both = time > t0 and close > open
either = time < t0 or close > open
early = not (time > t0)
float ft = time
int age = time - t0
alias = age
h1 = hours[1]
ref = (time[1] - time[2]) / 3600000.0
dft = ft[1] - time[1]
dage = age[1] - (time[1] - t0)
dalias = alias[1] - age[1]
flags = (late[1] ? 1 : 0) + (both[1] ? 2 : 0) + (either[1] ? 4 : 0) + (early[1] ? 8 : 0)
// @pf-trace h1=h1
// @pf-trace ref=ref
// @pf-trace dft=dft
// @pf-trace dage=dage
// @pf-trace dalias=dalias
// @pf-trace flags=flags
'''
FLOAT_OR_BOOL = {"hours": "double", "ft": "double", "late": "bool",
                 "both": "bool", "either": "bool", "early": "bool"}
WIDE = ("age", "alias")


def _series_type(cpp: str, name: str) -> str:
    match = re.search(rf"^\s*Series<([\w:]+)> {name};$", cpp, re.M)
    assert match, name
    return match.group(1)


def test_an_epoch_reaches_no_float_or_bool_slot() -> None:
    cpp = transpile(EPOCH)
    for name, cpp_type in FLOAT_OR_BOOL.items():
        assert _series_type(cpp, name) == cpp_type, name
    for name in WIDE:
        assert _series_type(cpp, name) == "int64_t", name


def test_main_had_widened_them_all(tmp_path: Path) -> None:
    main = reference_codegen(MAIN)
    if main is None:
        pytest.skip(f"codegen {MAIN} is not in this checkout's history")
    pine = tmp_path / "strategy.pine"
    pine.write_text(EPOCH, encoding="utf-8")
    cpp = transpile_json(pine, main)["cpp"]
    for name in (*FLOAT_OR_BOOL, *WIDE):
        assert _series_type(cpp, name) == "int64_t", name


# ---------------------------------------------------------------------------
# na() of a string and of a drawing in one TU (main x CG-W9-MISC)
# ---------------------------------------------------------------------------

NA_KINDS = HEAD + '''var box b = na
var string s = na
if bar_index == 5
    b := box.new(bar_index, high, bar_index + 1, low)
if bar_index == 8
    box.delete(b)
    s := "x"
boxna = na(b) ? 1 : 0
strna = na(s) ? 1 : 0
// @pf-trace boxna=boxna
// @pf-trace strna=strna
'''


def _na_kinds_expected(n: int) -> tuple[list[float], list[float]]:
    boxna = [0.0 if 5 <= i < 8 else 1.0 for i in range(n)]
    strna = [1.0 if i < 8 else 0.0 for i in range(n)]
    return boxna, strna


# ---------------------------------------------------------------------------
# One evaluator per call path (XSYM-A x K-TA-DYNLEN x CG-SECURITY-2)
# ---------------------------------------------------------------------------

PER_PATH = HEAD + '''mode = input.string("Slow", "Mode", options=["Fast", "Slow"])
g(len) => request.security(syminfo.tickerid, "60", ta.ema(close, len))
a = g(mode == "Fast" ? 9 : 14)
b = g(mode == "Fast" ? 21 : 30)
// @pf-trace a=a
// @pf-trace b=b
'''
PER_PATH_TWIN = HEAD + '''a = request.security(syminfo.tickerid, "60", ta.ema(close, {a}))
b = request.security(syminfo.tickerid, "60", ta.ema(close, {b}))
// @pf-trace a=a
// @pf-trace b=b
'''


# ---------------------------------------------------------------------------
# Chart-feed runs, the integrated tree beside main
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("cgint3_compositions")
    feed = chart_feed_head(engine, base, BARS)
    main = reference_codegen(MAIN)
    builds = {"epoch": Build(EPOCH, trace=True), "na_kinds": Build(NA_KINDS, trace=True)}
    if main is not None:
        builds |= {f"{key}_main": Build(b.source, trace=True, codegen=main)
                   for key, b in list(builds.items())}
    builds |= {
        "per_path": Build(PER_PATH, overrides={"Mode": "Fast"}, trace=True),
        "per_path_slow": Build(PER_PATH_TWIN.format(a=14, b=30), trace=True),
        "per_path_fast": Build(PER_PATH_TWIN.format(a=9, b=21), trace=True),
    }
    return execute_all(engine, feed, base, builds)


# Compared from the first bar whose history reads are all defined: integer
# arithmetic on an ``na`` epoch (``time[1] - time[2]`` on bar 1) reads garbage
# instead of ``na``, a gap that predates these lanes (CG-W9-SEC report).
def test_a_float_an_epoch_reaches_keeps_its_fraction(runs):
    records = ok(runs, "epoch").traces["default"]
    h1, ref = _values(records, "h1"), _values(records, "ref")
    assert len(h1) == len(ref) == BARS
    assert h1[2:] == ref[2:]
    assert ref.count(0.25) > BARS // 2       # the 15m chart's quarter hours
    for name in ("dft", "dage", "dalias"):
        assert set(_values(records, name)[1:]) == {0.0}, name
    # late (1) on every bar after the first; both (2) and either (4) on an
    # up bar; early (8) only on bar 0, read on bar 1.
    flags = _values(records, "flags")
    assert flags[1] == 8.0 and set(flags[2:]) == {1.0, 1.0 + 2.0 + 4.0}


def test_main_truncated_the_quarter_hour(runs):
    if "epoch_main" not in runs:
        pytest.skip(f"codegen {MAIN} is not in this checkout's history")
    records = ok(runs, "epoch_main").traces["default"]
    assert 0.25 not in _values(records, "h1")
    assert 0.25 in _values(records, "ref")


def test_na_of_a_string_and_of_a_deleted_box(runs):
    outcome = ok(runs, "na_kinds")
    cpp = outcome.transpiled["cpp"]
    assert ").empty()" in cpp                       # main's string na
    assert "_pf_drawing_na(" in cpp                 # CG-W9-MISC's liveness
    records = outcome.traces["default"]
    boxna, strna = _na_kinds_expected(BARS)
    assert _values(records, "boxna") == boxna
    assert _values(records, "strna") == strna


def test_main_read_the_deleted_box_as_live(runs):
    if "na_kinds_main" not in runs:
        pytest.skip(f"codegen {MAIN} is not in this checkout's history")
    records = ok(runs, "na_kinds_main").traces["default"]
    boxna, strna = _na_kinds_expected(BARS)
    assert _values(records, "strna") == strna
    got = _values(records, "boxna")
    assert got != boxna and got[8:] == [0.0] * (BARS - 8)


def test_each_call_path_gets_its_own_evaluator(runs):
    outcome = ok(runs, "per_path")
    cpp = outcome.transpiled["cpp"]
    assert len(re.findall(r"void _eval_security_\d+\(", cpp)) == 2
    for key, tag in (("per_path_slow", "default"), ("per_path_fast", "override")):
        twin = ok(runs, key).traces["default"]
        got = outcome.traces[tag]
        for name in ("a", "b"):
            want = _values(twin, name)
            assert len(_values(got, name)) == len(want) == BARS
            assert all(same(x, y) for x, y in zip(_values(got, name), want)), (key, name)
    slow, fast = ok(runs, "per_path_slow").traces["default"], ok(runs, "per_path_fast").traces["default"]
    assert _values(slow, "a") != _values(fast, "a")


def test_main_refused_the_two_call_paths(tmp_path: Path) -> None:
    main = reference_codegen(MAIN)
    if main is None:
        pytest.skip(f"codegen {MAIN} is not in this checkout's history")
    pine = tmp_path / "strategy.pine"
    pine.write_text(PER_PATH, encoding="utf-8")
    result = transpile_json(pine, main)
    assert not result["ok"]
    assert any("its call sites pass different lengths" in d["message"]
               for d in result["diagnostics"])


# ---------------------------------------------------------------------------
# A function tail beside a flag's history and an epoch parameter
# (W2-CG-LOWERING-TRIO x CG-SESSION-2 x K-RUNERR), on the session tapes' bars
# ---------------------------------------------------------------------------

TAIL = '''//@version=6
strategy("a function tail beside a flag's history", overlay=true)
f(int t) =>
    float acc = session.ismarket[1] ? 1.0 : 0.0
    acc += t > 1700000000000 ? 10 : 0
g(int t) =>
    float acc = session.ismarket[1] ? 1.0 : 0.0
    acc += t > 1700000000000 ? 10 : 0
    acc
a = f(time)
b = f(time - 900000)
r = g(time)
if a != r or b != r
    strategy.entry("L", strategy.long)
// @pf-trace a=a
// @pf-trace b=b
// @pf-trace r=r
'''


@pytest.fixture(scope="module")
def tail_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("cgint3_tail")
    stamps = _replay_stamps(EXTENDED)
    out = {"tail": _replay(engine, base / "tail", TAIL, stamps)}
    main = reference_codegen(MAIN)
    if main is not None:
        out["tail_main"] = _replay(engine, base / "tail_main", TAIL, stamps, main)
    return out


def test_a_tail_assignment_returns_beside_a_flags_history(tail_runs):
    replay = tail_runs["tail"]
    assert re.search(r"^\s*double f_cs0\(int64_t t\) \{", replay.cpp, re.M)
    assert re.search(r"^\s*double f_cs1\(int64_t t\) \{", replay.cpp, re.M)
    assert len(re.findall(r"_session_call_\w+", replay.cpp)) >= 2
    r = _values(replay.traces, "r")
    assert len(r) == len(_replay_stamps(EXTENDED)) and {10.0, 11.0} <= set(r)
    for name in ("a", "b"):
        assert _values(replay.traces, name) == r, name
    assert replay.trades.decode().count("\n") <= 1   # no entry: a == b == r


def test_main_returned_the_default(tail_runs):
    if "tail_main" not in tail_runs:
        pytest.skip(f"codegen {MAIN} is not in this checkout's history")
    replay = tail_runs["tail_main"]
    assert set(_values(replay.traces, "a")) == {0.0}
    assert {10.0, 11.0} <= set(_values(replay.traces, "r"))


# ---------------------------------------------------------------------------
# A flag's and a script variable's history in one function body
# (CG-SESSION-2 x CG-W9-FN), on the session tapes' bars
# ---------------------------------------------------------------------------

TWO_CLOCKS = '''//@version=6
strategy("a flag's and a variable's history in one function", overlay=true)
gv = bar_index * 2.0
fboth() => (session.ismarket[2] ? 1000.0 : 0.0) + nz(gv[2], -1.0)
fs() => session.ismarket[2] ? 1000.0 : 0.0
fg() => nz(gv[2], -1.0)
var float both = na
var float apart = na
var float sflag = na
var float gvar = na
if bar_index % 3 != 1 and bar_index % 7 != 2
    both := fboth()
    sflag := fs()
    gvar := fg()
    apart := sflag + gvar
chart_s = session.ismarket[2] ? 1000.0 : 0.0
chart_g = nz(gv[2], -1.0)
// @pf-trace both=both
// @pf-trace apart=apart
// @pf-trace sflag=sflag
// @pf-trace gvar=gvar
// @pf-trace chart_s=chart_s
// @pf-trace chart_g=chart_g
'''


@pytest.fixture(scope="module")
def two_clock_run(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("cgint3_two_clocks")
    return _replay(engine, base / "two_clocks", TWO_CLOCKS, _replay_stamps(EXTENDED))


def test_a_flags_and_a_variables_history_in_one_body(two_clock_run):
    cpp, records = two_clock_run.cpp, two_clock_run.traces
    match = re.search(r"double fboth_cs0\(\) \{(.*?)\n    \}\n", cpp, re.S)
    assert match, "fboth has one emitted body per call site"
    body = match.group(1)
    assert re.search(r"_session_call_\d+\.push\(", body), body
    assert re.search(r"_fn_global_hist_\d+\.update\(", body), body
    n = len(_replay_stamps(EXTENDED))
    both, apart = _values(records, "both"), _values(records, "apart")
    assert len(both) == len(apart) == n
    assert all(same(a, b) for a, b in zip(both, apart))
    # Each rule is live: the call sites' histories are not the chart's.
    calls = [i for i in range(n) if i % 3 != 1 and i % 7 != 2]
    for name, chart in (("sflag", "chart_s"), ("gvar", "chart_g")):
        got, ref = _values(records, name), _values(records, chart)
        assert any(not same(got[i], ref[i]) for i in calls), name
    assert {0.0, 1000.0} <= set(_values(records, "sflag"))

