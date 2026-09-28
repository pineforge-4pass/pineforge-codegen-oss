"""Where a lane integrated by CGINT4 meets a rule already on main, or changes
a function one of main's lanes changed, the integration keeps both rules
(integration lane CGINT4: CG-OPEN-ITEMS on main's CGINT3). Each case runs the
composition end to end -- ``transpile_json``, the built runtime,
``run_strategy.py`` -- or pins the C++ where the rules decide the lowering.
``MAIN`` is the integration base, replayed beside a case to show what it
missed:

* ``TypeInferer._series_type_for``: CG-OPEN-ITEMS bf805c2 resolves a
  function's local spelled like another user function in its own function's
  scope (``_variable_symbol``); CG-W9-SEC c86c0de widens a name an epoch
  reaches to ``int64_t`` only when its symbol is no float, bool or string.
  Both now read the one symbol: an ``int f = time`` local beside a string
  function ``f`` keeps 64 bits and a ``float h = time`` beside a string
  function ``h`` is a double. Main resolved both to the string functions
  (``Series<std::string>``, which did not compile); the lane alone declared
  ``Series<int>`` and narrowed the epoch.
* ``StmtVisitor._visit_selection_value``: CG-OPEN-ITEMS 9ad5694 gives an if
  without else (a switch without default) that runs no arm the na of its
  slot; K-RUNERR stores an ``int`` an epoch reaches as ``int64_t``. That
  slot's na is ``na<int64_t>()``: the lane spelled ``na<int>()`` for a
  global, switch or reassigned target (Pine's ``int``), which widens to a
  value, so ``na(t)`` read false. Main kept the previous bar's value.
* ``pineforge_codegen._generate``: CG-OPEN-ITEMS f3e1816 rewrites ``nz`` /
  ``fixnan`` keyword arguments to their positions (``bind_builtin_keywords``)
  after XSYM-A's passes in main's ``_generate`` loop: ``lower_no_data_requests``
  and ``specialize_security_contexts``, which copies a helper per context and
  puts a payload parameter's value in its place. A keyword call through each
  is C++ byte for byte its positional twin; main raised IndexError on each.
* One function on two clocks: CG-W9-FN reads ``bar_index[k]`` in a function
  by its chart call site's calls (``_fn_global_hist_*``), CG-OPEN-ITEMS
  41c5e4f a payload's ``bar_index`` on the requested bars
  (``_sec<N>_bar_index_``). One function called on irregular chart bars and
  inside a request equals, on each clock, its own twin.
* A payload's series TA length over ``bar_index``: K-TA-DYNLEN windows
  ``ta.highest(high, bar_index % 5 + 1)`` every call
  (``pineforge::source::SeriesHighest``), and 41c5e4f counts the requested
  bars, where K-TA-DYNLEN's pin spelled the chart's ``pine_bar_index()``
  (none of its tapes reads ``bar_index`` in a payload). The window equals
  its spelled-out ``math.max`` over the requested bars' highs.
* A flag's history beside a global under a payload's builtin: CG-SESSION-2
  keeps ``session.<flag>[k]`` on the requested clock only where the
  evaluator is on the requested bar's terms, and CG-SECURITY-2 kept an
  evaluator whose builtin also read a global on the chart's (CGINT2 pinned
  ``nz(session.ismarket[1] ? g : na)`` with ``g = close`` as refused).
  CG-OPEN-ITEMS 5b3791d re-evaluates such a global on the requested bar, so
  the read now runs and equals its spelling with ``close``; a per-run global
  (``timeframe.*``) still keeps the chart's terms and the refusal
  (``tests/test_e2e_lane_compositions.py``).
* An imported library in the pipeline: XSYM-C 046687a inlines a script's
  libraries before the support check, main runs the passes in
  ``_generate``'s session-clone loop, and f3e1816 binds ``nz`` keywords after
  them. Inlined on every pass, a library function reading a flag at an
  offset gets a clone per call site (``_session_call_*``), and a keyword
  ``nz`` in library code is its positional twin byte for byte.
* CG-OPEN-ITEMS d504053 parenthesizes a lambda history offset; CG-SESSION-2's
  flag histories and CG-W9-FN's function histories index through
  ``pine_index_int_cast``. A fractional runtime offset on every emitter
  compiles and opens no C++ attribute.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile, transpile_full
from tests import _compile as compile_env
from tests._e2e import (
    Build, chart_feed_head, execute_all, ok, reference_codegen, same,
    skip_unless_e2e_env, transpile_json,
)
from tests.test_e2e_session_history import EXTENDED, _replay, _replay_stamps

# The integration base: codegen main before the CGINT4 picks.
MAIN = "9361dbb3927cb6a545e97ba0cf5a5574e3c9fc28"
HEAD = ('//@version=6\nstrategy("cgint4 composition", overlay=true, '
        'default_qty_type=strategy.fixed, default_qty_value=1)\n')
BARS = 400


def _values(records: list[dict], name: str) -> list[float]:
    return [rec["value"] for rec in records if rec["name"] == name]


def _series_type(cpp: str, name: str) -> str | None:
    match = re.search(rf"^\s*Series<([\w:]+)> {name};$", cpp, re.M)
    return match.group(1) if match else None


def _main_transpiled(tmp_path: Path, source: str) -> dict:
    main = reference_codegen(MAIN)
    if main is None:
        pytest.skip(f"codegen {MAIN} is not in this checkout's history")
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    return transpile_json(pine, main)


# ---------------------------------------------------------------------------
# A function's local named like a function keeps its epoch width
# (CG-OPEN-ITEMS bf805c2 x CG-W9-SEC c86c0de x K-RUNERR)
# ---------------------------------------------------------------------------

FUNC_LOCAL = HEAD + '''f(x) => str.tostring(x)
h(x) => str.tostring(x)
g() =>
    int f = time
    float h = time
    [f - f[1], h - h[1]]
g2() =>
    int fa = time
    float ha = time
    [fa - fa[1], ha - ha[1]]
[a, b] = g()
[ra, rb] = g2()
if bar_index == 3
    strategy.entry("L", strategy.long, comment = f(a) + h(b))
// @pf-trace a=a
// @pf-trace b=b
// @pf-trace ra=ra
// @pf-trace rb=rb
'''


def test_a_local_named_like_a_function_keeps_its_own_width() -> None:
    # The renamed twins' own types are not this composition's: ``fa`` is
    # int64_t, and ``ha``, which no symbol lookup reaches after the
    # analyzer leaves g2's scope, is int64_t on main too.
    cpp = transpile(FUNC_LOCAL)
    assert _series_type(cpp, "f") == "int64_t"
    assert _series_type(cpp, "h") == "double"
    assert _series_type(cpp, "fa") == "int64_t"


def test_main_typed_both_locals_as_the_string_functions(tmp_path: Path) -> None:
    cpp = _main_transpiled(tmp_path, FUNC_LOCAL)["cpp"]
    assert _series_type(cpp, "f") == "std::string"
    assert _series_type(cpp, "h") == "std::string"


# ---------------------------------------------------------------------------
# An if without else in an epoch slot is the 64-bit na
# (CG-OPEN-ITEMS 9ad5694 x K-RUNERR)
# ---------------------------------------------------------------------------

IF_NA = HEAD + '''var int w = na
up = close > open
t = if up
    time
u = switch
    up => time
w := if up
    time
g() =>
    int x = if close > open
        time
    x
v = g()
twin = up ? time : na
tn = na(t) ? 1 : 0
un = na(u) ? 1 : 0
wn = na(w) ? 1 : 0
vn = na(v) ? 1 : 0
twn = na(twin) ? 1 : 0
dt = na(t) ? 0 : t - time
// @pf-trace tn=tn
// @pf-trace un=un
// @pf-trace wn=wn
// @pf-trace vn=vn
// @pf-trace twn=twn
// @pf-trace dt=dt
'''
IF_NA_TARGETS = ("t", "u", "w", "x")


def test_an_epoch_slot_takes_the_64_bit_na() -> None:
    cpp = transpile(IF_NA)
    for name in IF_NA_TARGETS:
        assert re.search(rf"^\s*int64_t (this->)?{name}\b", cpp, re.M), name
        assert re.search(rf"^\s*{name} = na<int64_t>\(\);$", cpp, re.M), name
        assert not re.search(rf"^\s*{name} = na<int>\(\);$", cpp, re.M), name


# ---------------------------------------------------------------------------
# One function on the chart's clock and on the requested clock
# (CG-W9-FN x CG-OPEN-ITEMS 41c5e4f)
# ---------------------------------------------------------------------------

BAR_CLOCKS = HEAD + '''f() => bar_index[1]
fc() => bar_index[1]
var float a = na
var float ac = na
if bar_index % 3 != 1
    a := f()
    ac := fc()
b = request.security(syminfo.tickerid, "60", f())
tb = request.security(syminfo.tickerid, "60", bar_index[1])
chart = bar_index[1]
// @pf-trace a=a
// @pf-trace ac=ac
// @pf-trace b=b
// @pf-trace tb=tb
// @pf-trace chart=chart
'''


# ---------------------------------------------------------------------------
# A series TA length over a payload's bar_index
# (K-TA-DYNLEN x CG-OPEN-ITEMS 41c5e4f)
# ---------------------------------------------------------------------------

DYN_BAR_INDEX = HEAD + '''w = request.security(syminfo.tickerid, "240", ta.highest(high, bar_index % 5 + 1))
twin = request.security(syminfo.tickerid, "240", bar_index % 5 == 0 ? high : bar_index % 5 == 1 ? math.max(high, high[1]) : bar_index % 5 == 2 ? math.max(high, high[1], high[2]) : bar_index % 5 == 3 ? math.max(high, high[1], high[2], high[3]) : math.max(high, high[1], high[2], high[3], high[4]))
rc = request.security(syminfo.tickerid, "240", bar_index)
cc = bar_index
// @pf-trace w=w
// @pf-trace twin=twin
// @pf-trace rc=rc
// @pf-trace cc=cc
'''


# ---------------------------------------------------------------------------
# Chart-feed runs, the integrated tree beside main
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("cgint4_compositions")
    feed = chart_feed_head(engine, base, BARS)
    builds = {
        "func_local": Build(FUNC_LOCAL, trace=True),
        "if_na": Build(IF_NA, trace=True),
        "bar_clocks": Build(BAR_CLOCKS, trace=True),
        "dyn_bar_index": Build(DYN_BAR_INDEX, trace=True),
    }
    main = reference_codegen(MAIN)
    if main is not None:
        builds["if_na_main"] = Build(IF_NA, trace=True, codegen=main)
    return execute_all(engine, feed, base, builds)


# Compared from bar 1: integer arithmetic on an ``na`` epoch (``f - f[1]`` on
# bar 0) reads garbage instead of ``na``, a gap that predates these lanes.
def test_the_named_locals_read_like_their_renamed_twins(runs):
    records = ok(runs, "func_local").traces["default"]
    for name, twin in (("a", "ra"), ("b", "rb")):
        got, want = _values(records, name), _values(records, twin)
        assert len(got) == len(want) == BARS, name
        assert all(same(x, y) for x, y in zip(got[1:], want[1:])), name
    # The 15m chart's bar spacing, in 64 bits: a 32-bit epoch never reads it.
    assert _values(records, "a")[1:].count(900000.0) > BARS // 2


def test_the_unmatched_arm_is_na_on_every_slot(runs):
    records = ok(runs, "if_na").traces["default"]
    twin = _values(records, "twn")
    assert len(twin) == BARS and {0.0, 1.0} <= set(twin)
    for name in ("tn", "un", "wn", "vn"):
        assert _values(records, name) == twin, name
    assert set(_values(records, "dt")) == {0.0}


def test_main_kept_the_previous_bars_epoch(runs):
    if "if_na_main" not in runs:
        pytest.skip(f"codegen {MAIN} is not in this checkout's history")
    records = ok(runs, "if_na_main").traces["default"]
    twin = _values(records, "twn")
    assert _values(records, "tn") != twin
    first_up = twin.index(0.0)
    assert set(_values(records, "tn")[first_up:]) == {0.0}


def test_one_function_reads_each_clocks_own_history(runs):
    outcome = ok(runs, "bar_clocks")
    cpp = outcome.transpiled["cpp"]
    assert re.search(r"_fn_global_hist_\d+\[", cpp)
    assert re.search(r"_sec\d+_bar_index_", cpp)
    records = outcome.traces["default"]
    for name, twin in (("a", "ac"), ("b", "tb")):
        got, want = _values(records, name), _values(records, twin)
        assert len(got) == len(want) == BARS, name
        assert all(same(x, y) for x, y in zip(got, want)), name
    # Each rule is live: neither clock reads the chart's bar_index[1].
    chart = _values(records, "chart")
    for name in ("a", "b"):
        got = _values(records, name)
        assert any(not same(x, y) for x, y in zip(got, chart)), name


def test_a_payload_series_length_counts_the_requested_bars(runs):
    outcome = ok(runs, "dyn_bar_index")
    cpp = outcome.transpiled["cpp"]
    member = re.search(r"pineforge::source::SeriesHighest (_sec\d+__ta_highest_\d+);", cpp)
    assert member, "the payload's series length windows every call"
    compute = next(line for line in cpp.splitlines()
                   if f"{member.group(1)}.compute(" in line)
    assert re.search(r"_sec\d+_bar_index_", compute) and "pine_bar_index()" not in compute
    records = outcome.traces["default"]
    got, want = _values(records, "w"), _values(records, "twin")
    assert len(got) == len(want) == BARS
    assert all(same(x, y) for x, y in zip(got, want))
    assert len({x for x in got if x == x}) > 10
    # The requested count is not the chart's: 16 chart bars per 240 bar.
    rc, cc = _values(records, "rc"), _values(records, "cc")
    assert max(x for x in rc if x == x) < max(cc) // 8


# ---------------------------------------------------------------------------
# nz / fixnan keyword calls through XSYM-A's request passes
# (CG-OPEN-ITEMS f3e1816 x XSYM-A)
# ---------------------------------------------------------------------------

KEYWORD_TWINS = {
    "context_copies": (
        HEAD + '''f(tf) => request.security(syminfo.tickerid, tf, nz(replacement = 0.0, source = close[1]))
a = f("60")
b = f("240")
if a > b
    strategy.entry("L", strategy.long)
''',
        HEAD + '''f(tf) => request.security(syminfo.tickerid, tf, nz(close[1], 0.0))
a = f("60")
b = f("240")
if a > b
    strategy.entry("L", strategy.long)
'''),
    "no_data_request": (
        HEAD + '''x = nz(source = request.security("NASDAQ:AAPL", "60", close), replacement = 0.0)
plot(x)
if close > open
    strategy.entry("L", strategy.long)
''',
        HEAD + '''x = nz(request.security("NASDAQ:AAPL", "60", close), 0.0)
plot(x)
if close > open
    strategy.entry("L", strategy.long)
'''),
    "payload_parameter": (
        HEAD + '''g(src, tf) => request.security(syminfo.tickerid, tf, fixnan(source = src))
a = g(close, "60")
b = g(open, "60")
if a > b
    strategy.entry("L", strategy.long)
''',
        HEAD + '''g(src, tf) => request.security(syminfo.tickerid, tf, fixnan(src))
a = g(close, "60")
b = g(open, "60")
if a > b
    strategy.entry("L", strategy.long)
'''),
}


@pytest.mark.parametrize("case", sorted(KEYWORD_TWINS))
def test_a_keyword_call_is_its_positional_twin(case: str) -> None:
    keyword, positional = KEYWORD_TWINS[case]
    got, want = transpile_full(keyword), transpile_full(positional)
    assert got["cpp"] == want["cpp"]
    assert ([d.message for d in got["diagnostics"]]
            == [d.message for d in want["diagnostics"]])
    compile_env.compile_cpp(got["cpp"], label=f"cgint4-keywords-{case}")


@pytest.mark.parametrize("case", sorted(KEYWORD_TWINS))
def test_main_crashed_on_the_keyword_call(tmp_path: Path, case: str) -> None:
    with pytest.raises(RuntimeError, match="IndexError"):
        _main_transpiled(tmp_path, KEYWORD_TWINS[case][0])


# ---------------------------------------------------------------------------
# A flag's history beside a global under a payload's builtin
# (CG-SESSION-2 x CG-SECURITY-2 x CG-OPEN-ITEMS 5b3791d), on the session
# tapes' bars
# ---------------------------------------------------------------------------

SESSION_GLOBAL = '''//@version=6
strategy("a flag's history beside a global under a builtin", overlay=true, default_qty_type=strategy.fixed, default_qty_value=1)
g = close
a = request.security(syminfo.tickerid, "60", nz(session.ismarket[1] ? g : na))
a_ref = request.security(syminfo.tickerid, "60", nz(session.ismarket[1] ? close : na))
if a > a_ref
    strategy.entry("L", strategy.long)
// @pf-trace a=a
// @pf-trace a_ref=a_ref
'''


@pytest.fixture(scope="module")
def session_global_run(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("cgint4_session_global")
    return _replay(engine, base / "session_global", SESSION_GLOBAL, _replay_stamps(EXTENDED))


def test_a_flags_history_beside_a_requested_global(session_global_run):
    replay = session_global_run
    assert re.search(r"Series<bool> _sec\d+_expr_hist_\d+", replay.cpp)
    n = len(_replay_stamps(EXTENDED))
    got, want = _values(replay.traces, "a"), _values(replay.traces, "a_ref")
    assert len(got) == len(want) == n
    assert all(same(x, y) for x, y in zip(got, want))
    # The flag's history is live: off-market bars read nz's 0, the others g.
    assert 0.0 in got and any(x > 0 for x in got)


def test_main_refused_the_flags_history_beside_the_global(tmp_path: Path) -> None:
    result = _main_transpiled(tmp_path, SESSION_GLOBAL)
    assert not result["ok"]
    assert any("session.ismarket[...] cannot be read here" in d["message"]
               for d in result["diagnostics"])


# ---------------------------------------------------------------------------
# An imported library through _generate's passes
# (XSYM-C 046687a x CG-SESSION-2 x CG-OPEN-ITEMS f3e1816)
# ---------------------------------------------------------------------------

LIBRARY = '''//@version=6
// @description CGINT4 composition probe
library("Probe")
export flagHist() => session.ismarket[1] ? 1.0 : 0.0
export keep(float x) => nz(source = x, replacement = -1.0)
'''
LIBRARY_SCRIPT = HEAD + '''import cgint4/Probe/1 as P
a = P.flagHist()
b = P.flagHist()
c = P.keep(close[1])
if a + b + c > 0
    strategy.entry("L", strategy.long)
'''


def test_an_inlined_library_takes_every_pass() -> None:
    cpp = transpile(LIBRARY_SCRIPT, libraries={"cgint4/Probe/1": LIBRARY})
    assert {"Probe_v1__flagHist_cs0", "Probe_v1__flagHist_cs1"} <= set(
        re.findall(r"\b(Probe_v1__flagHist_cs\d+)\(", cpp))
    assert len(set(re.findall(r"_session_call_\d+", cpp))) == 2
    positional = LIBRARY.replace("nz(source = x, replacement = -1.0)", "nz(x, -1.0)")
    assert cpp == transpile(LIBRARY_SCRIPT, libraries={"cgint4/Probe/1": positional})
    compile_env.compile_cpp(cpp, label="cgint4-library-passes")


# ---------------------------------------------------------------------------
# A fractional history offset on every emitter
# (CG-OPEN-ITEMS d504053 x CG-SESSION-2 x CG-W9-FN)
# ---------------------------------------------------------------------------

FRACTIONAL = HEAD + '''lag = (bar_index % 3) / 2
gv = close * 2
fs() => session.ismarket[lag] ? 1.0 : 0.0
fg() => nz(gv[lag], -1.0)
a = session.ismarket[lag] ? 1.0 : 0.0
b = fs()
c = fg()
d = ta.tr(true)[lag]
e = (close - open)[lag]
if a + b + c + d + e > 0
    strategy.entry("L", strategy.long)
'''


def test_every_history_emitter_takes_a_fractional_offset() -> None:
    cpp = transpile(FRACTIONAL)
    code = re.sub(r'"(?:[^"\\]|\\.)*"', '""', cpp)
    assert "[[" not in code
    assert re.search(r"_session_call_\d+\[", code)
    assert re.search(r"_pf_session_hist_ismarket\[", code)
    assert re.search(r"_fn_global_hist_\d+\[", code)
    compile_env.compile_cpp(cpp, label="cgint4-fractional-offsets")
