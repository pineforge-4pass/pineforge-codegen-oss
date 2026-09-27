"""A script identifier never hides a host member the generated code reads.

The generated strategy class derives from the engine's host
(``pineforge::source::PineStrategyHost``). Inside it an unqualified name finds
the class's own members first, so a script variable named like a host member
the emitter reads hid that member: ``session.isfirstbar`` (``session_isfirstbar_``)
and ``barstate.isfirst`` (``bar_index_ == 0``) silently read the script's
variable, and ``time`` (``current_bar_.timestamp``) or ``syminfo.mintick``
(``syminfo_.mintick``) stopped compiling. TradingView accepts these names.

``codegen/host_members.py`` holds the host members the emitter can read or
write, derived -- never written by hand -- by ``scripts/gen_host_members.py``
from the emitter's string constants and clang's AST of the host header the
emitted C++ includes; ``_safe_name`` renames a script identifier in it the way
it renames one spelled like a C++ keyword. This module regenerates the set,
checks it against every host member a transpiled battery actually names, and
replays TradingView's tape of a probe using such names
(``fixtures/host_member_names``).
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

from tests import _compile as compile_env
from tests._e2e import (
    REPO_ROOT, Build, assert_same_runs, chart_feed_head, execute_all, ok,
    reference_codegen, skip_unless_e2e_env, transpile_json,
)
from tests.test_e2e_session_history import LEGACY, QUARTER, _replay, _utc_ms
from tests.test_e2e_session_ismarket import next_chart_bar


GENERATOR = REPO_ROOT / "scripts" / "gen_host_members.py"


def _generator():
    name = "_pf_gen_host_members"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, GENERATOR)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _clang_env():
    """The generator's compiler and include directories, or a skip."""
    compile_env.skip_if_no_compile_env()
    gen = _generator()
    cxx = gen.find_clang(os.environ.get("CXX") or compile_env._COMPILER)
    if cxx is None:
        pytest.skip("deriving the host members needs clang's AST dump; no clang++ found")
    includes = [compile_env._ENGINE_INC, compile_env._EIGEN_INC]
    if compile_env._GENERATED_INC is not None:
        includes.append(compile_env._GENERATED_INC)
    return gen, cxx, [str(p) for p in includes]


def test_committed_host_members_are_the_derived_set() -> None:
    """``codegen/host_members.py`` is exactly what the generator derives from
    this checkout's emitter and the engine's host header; after either
    changes, regenerate it with ``scripts/gen_host_members.py``."""
    gen, cxx, includes = _clang_env()
    derived = gen.derive(REPO_ROOT, cxx, includes)
    from pineforge_codegen.codegen.host_members import HOST_MEMBER_NAMES
    missing = sorted(derived.names - HOST_MEMBER_NAMES)
    stale = sorted(HOST_MEMBER_NAMES - derived.names)
    assert not missing and not stale, (
        f"host members to reserve: {missing}; no longer read: {stale} -- "
        "run scripts/gen_host_members.py")
    assert (REPO_ROOT / "pineforge_codegen" / "codegen" / "host_members.py").read_text() \
        == gen.render(derived)
    for name in ("session_isfirstbar_", "session_islastbar_", "session_isfirstbar_regular_",
                 "session_islastbar_regular_", "syminfo_", "current_bar_",
                 "bar_index_", "trades_", "closed_trade_profit", "open_trade_max_drawdown"):
        assert name in derived.names, name
    # A private member cannot be read by the generated class, and a Pine name
    # the emitter only looks up or matches on (``str.replace``'s "replace",
    # ``strategy.position_avg_price``'s member name) is not a spelling of
    # generated code; one it joins into a call is (``"pine_session_" +
    # member``).
    for name in ("scheduler_prepare_script_run", "staged_configuration", "apply_overrides"):
        assert name not in derived.host, name
    for name in ("replace", "position_avg_price"):
        assert name in derived.host and name not in derived.names, name
    assert {"pine_session_ispremarket", "pine_session_ispostmarket"} <= derived.names
    print(f"{len(derived.names)} of the host's {len(derived.host)} accessible members "
          f"reserved ({derived.host_class})")


def test_identifier_scan_skips_comments_and_literals_only() -> None:
    """Needs no engine or clang: the battery's scan drops comments and string
    and character literals, and keeps code around a literal holding ``//`` or
    ``/*``."""
    gen = _generator()
    names = gen.unqualified_identifiers(
        'x = f(std::string("http://a"), bar_index_); // trades_\n'
        'y = g("a/*b", \'/\', syminfo_.mintick); /* current_bar_ */ z = trace_enabled_;\n'
        "#include <pineforge/source/pine_strategy_host.hpp>\n")
    assert {"bar_index_", "syminfo_", "trace_enabled_", "x", "f", "y", "g", "z"} <= names
    assert not names & {"trades_", "current_bar_", "mintick", "http", "pine_strategy_host"}


def _battery() -> list[Path]:
    sources = sorted((REPO_ROOT / "tests" / "fixtures").rglob("*.pine"))
    sources += sorted((REPO_ROOT / "tests" / "gate-corpus" / "ok").glob("*.pine"))
    corpus = os.environ.get("PINEFORGE_ENGINE_CORPUS")
    if corpus and Path(corpus).is_dir():
        sources += sorted(Path(corpus).rglob("*.pine"))
    return sources


def test_generated_code_reads_no_unreserved_host_member() -> None:
    """Every host member a transpiled battery names unqualified (the test
    fixtures, the gate corpus and the engine's public corpus) is in the
    reserved set: a new lowering that reads a host member the derivation
    cannot see fails here."""
    gen, cxx, includes = _clang_env()
    from pineforge_codegen import transpile
    from pineforge_codegen.errors import CompileError
    from pineforge_codegen.codegen.host_members import HOST_MEMBER_NAMES
    host = gen.derive(REPO_ROOT, cxx, includes).host
    named: dict[str, str] = {}
    transpiled = 0
    for path in _battery():
        try:
            cpp = transpile(path.read_text(encoding="utf-8"), filename=str(path))
        except CompileError:
            continue
        transpiled += 1
        for name in gen.unqualified_identifiers(cpp) & host:
            named.setdefault(name, str(path))
    assert transpiled > 100
    unreserved = {name: where for name, where in named.items() if name not in HOST_MEMBER_NAMES}
    assert not unreserved, f"host members read but not reserved: {unreserved}"
    print(f"{transpiled} TUs name {len(named)} host members, all reserved")


def test_script_names_of_host_members_are_renamed(tmp_path: Path) -> None:
    """Needs no engine: a variable, function, parameter, loop variable,
    tuple name or UDT field named like a host member gets a safe C++ name, and
    an input keeps its Pine name as its key."""
    pine = tmp_path / "strategy.pine"
    pine.write_text('//@version=6\nstrategy("host names", overlay=true)\n'
                    "type Holder\n    float current_bar_ = 0\n"
                    "input_tf_(x) => x * 2\n"
                    "f(prev_bar_timestamp_) => prev_bar_timestamp_ + 1\n"
                    "syminfo_ = input.float(2.0)\n"
                    "trades_ = 3\n"
                    "session_islastbar_regular_ = close > open\n"
                    "[_src_close_, _src_open_] = [close, open]\n"
                    "total = 0\n"
                    "for security_eval_states_ = 0 to 1\n"
                    "    total += security_eval_states_\n"
                    "h = Holder.new(close)\n"
                    "if time > 0 and syminfo.mintick > 0 and h.current_bar_ > 0 and "
                    "input_tf_(1) + f(1) + syminfo_ + trades_ + _src_close_ + total > 0 and "
                    "(session.islastbar_regular or session_islastbar_regular_)\n"
                    '    strategy.entry("L", strategy.long)\n', encoding="utf-8")
    result = transpile_json(pine)
    assert result["ok"], result["diagnostics"]
    cpp = result["cpp"]
    for name in ("current_bar_", "input_tf_", "prev_bar_timestamp_", "syminfo_", "trades_",
                 "_src_close_", "_src_open_", "security_eval_states_",
                 "session_islastbar_regular_"):
        assert f"pf_safe_{name}" in cpp, name
    assert [entry["title"] for entry in result["inputs"]] == ["syminfo_"]
    compile_cpp_or_skip(cpp)


def compile_cpp_or_skip(cpp: str) -> None:
    compile_env.compile_cpp(cpp, label="host member names")


SILENT = '''//@version=6
strategy("host member names", overlay=true, process_orders_on_close=true)
{fb} = close > open
{lb} = not {fb}
{fbr} = high > low
{lbr} = not {fbr}
{bi} = 7
{bl} = true
{lt} = false
{ic} = 12345.0
{lbt} = 5
if session.isfirstbar or barstate.isfirst
    strategy.entry("L", strategy.long)
if session.islastbar and barstate.isconfirmed
    strategy.close("L")
if session.isfirstbar_regular and session.islastbar_regular
    strategy.entry("S", strategy.short)
// @pf-trace fb=session.isfirstbar
// @pf-trace lb=session.islastbar
// @pf-trace fbr=session.isfirstbar_regular
// @pf-trace lbr=session.islastbar_regular
// @pf-trace first=barstate.isfirst
// @pf-trace last=barstate.islast
// @pf-trace conf=barstate.isconfirmed
// @pf-trace cap=strategy.initial_capital
// @pf-trace lbt=last_bar_time
// @pf-trace own={fb} ? 1 : 0
// @pf-trace own2={bi} + {ic} + {lbt} + ({bl} ? 1 : 0) + ({lt} ? 1 : 0) + ({lb} ? 1 : 0)
// @pf-trace own3=({fbr} ? 1 : 0) + ({lbr} ? 2 : 0)
'''
SILENT_NAMES = {"fb": "session_isfirstbar_", "lb": "session_islastbar_",
                "fbr": "session_isfirstbar_regular_", "lbr": "session_islastbar_regular_",
                "bi": "bar_index_",
                "bl": "barstate_islast_", "lt": "is_last_tick_", "ic": "initial_capital_",
                "lbt": "last_bar_time_"}

LOUD = '''//@version=6
strategy("host member names, loud", overlay=true, process_orders_on_close=true)
type Holder
    float {cb} = 0
{cb} = close * 2
{si} = 2.0
{tr} = 3
{sm} = 4.0
{it}(x) => x * 2
f({pb}) => {pb} + 1
[{sc}, {so}] = [close, open]
total = 0
for {se} = 0 to 1
    total += {se}
h = Holder.new(close)
own = {cb} + {si} + {tr} + {sm} + {it}(1) + f(1) + {sc} - {so} + total + h.{cb}
if time > 0 and syminfo.mintick > 0 and strategy.closedtrades >= 0 and close > open
    strategy.entry("L", strategy.long)
if close < open
    strategy.close("L")
// @pf-trace t=time
// @pf-trace mt=syminfo.mintick
// @pf-trace ct=strategy.closedtrades
// @pf-trace own=own
'''
LOUD_NAMES = {"cb": "current_bar_", "si": "syminfo_", "tr": "trades_", "sm": "syminfo_mintick_",
              "it": "input_tf_", "pb": "prev_bar_timestamp_", "sc": "_src_close_",
              "so": "_src_open_", "se": "security_eval_states_"}


def _pair(template: str, names: dict[str, str]) -> tuple[str, str]:
    """The probe spelled with the host's names, and with neutral ones."""
    return (template.format(**names),
            template.format(**{key: f"v_{key}" for key in names}))


@pytest.fixture(scope="module")
def runs(tmp_path_factory) -> dict:
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("host_member_names")
    feed = chart_feed_head(engine, base, 400)
    builds = {}
    for label, (template, names) in {"silent": (SILENT, SILENT_NAMES),
                                     "loud": (LOUD, LOUD_NAMES)}.items():
        shadow, reference = _pair(template, names)
        builds[f"{label}-host"] = Build(shadow, trace=True)
        builds[f"{label}-ref"] = Build(reference, trace=True)
    return execute_all(engine, feed, base, builds)


@pytest.mark.parametrize("label", ("silent", "loud"))
def test_host_named_script_runs_like_its_reference(label: str, runs) -> None:
    """The same probe with the host's names and with neutral names traces
    every builtin and its own variables bar for bar alike and books the same
    trades. With the names unreserved, the silent probe compiled and its
    session.isfirstbar, barstate.isfirst / islast / isconfirmed,
    strategy.initial_capital and last_bar_time read its variables; the loud
    one did not compile."""
    summary = assert_same_runs(ok(runs, f"{label}-host"), ok(runs, f"{label}-ref"))
    print(f"host member names, {label} probe: {summary} == neutral names")


TAPE = Path(__file__).parent / "fixtures" / "host_member_names" / "cgs2-hostnames-aapl-15-reg"
# The probe's Signal, pair by pair: the built-in a script variable named like
# a host member used to hide, then that variable (strategy.pine's header).
SIGNAL = re.compile(r"F([01])([01])L([01])([01])B([01])(\d+)M(\d+)C(\d+)Z([01])(.+)")
TAPE_TRACE = {
    "fb": "session.isfirstbar", "fbv": "session_isfirstbar_",
    "lb": "session.islastbar", "lbv": "session_islastbar_",
    "first": "barstate.isfirst", "biv": "bar_index_",
    "minute": "minute(time)", "cbv": "current_bar_",
    "tz": 'syminfo.timezone == "America/New_York"', "siv": "syminfo_",
}
# The names that kept the pre-lane build from compiling the probe.
LOUD_IN_TAPE = {"current_bar_": "cb_v", "syminfo_": "si_v"}


def read_host_tape() -> list[tuple[int, dict[str, float]]]:
    """(the chart bar's open in UTC ms, TradingView's values by trace name)
    per Entry row, in time order. The export renders times at UTC+8."""
    with (TAPE / "tv_trades.csv").open(encoding="utf-8-sig") as fh:
        rows = [row for row in csv.DictReader(fh) if row["Type"].startswith("Entry")]
    return sorted(((_utc_ms(row["Date and time"], 8),
                    dict(zip(TAPE_TRACE, map(float, SIGNAL.fullmatch(row["Signal"]).groups()))))
                   for row in rows), key=lambda bar: bar[0])


def _tape_probe(renames: dict[str, str] | None = None) -> str:
    """The exported probe with a trace of every pair, a variable renamed per
    ``renames``."""
    source = (TAPE / "strategy.pine").read_text(encoding="utf-8") + "".join(
        f"\n// @pf-trace {name}={expr}" for name, expr in TAPE_TRACE.items()) + "\n"
    for old, new in (renames or {}).items():
        source = re.sub(rf"\b{re.escape(old)}(?!\w)", new, source)
    return source


def test_tradingview_keeps_builtins_beside_host_named_variables() -> None:
    """Needs no engine: the tape is its export byte for byte. TradingView
    compiles the probe and, on its 260 NASDAQ:AAPL 15 bars, reads every
    built-in beside the variable named like the host member PineForge read it
    from: session.isfirstbar / islastbar flag the 10 session opens / closes,
    barstate.isfirst the first bar, and each variable holds its own value."""
    metrics = json.loads((TAPE / "metrics.json").read_text())
    assert hashlib.sha256((TAPE / "tv_trades.csv").read_bytes()).hexdigest() \
        == metrics["tvTradesCsvHash"]
    assert hashlib.sha256((TAPE / "strategy.pine").read_bytes()).hexdigest() \
        == metrics["sourceArtifactHash"]
    assert metrics["wsProvenance"]["rangeProof"] == "covered"
    tape = [values for _, values in read_host_tape()]
    assert len(tape) == metrics["trades"] == 260

    def column(name: str) -> list[float]:
        return [values[name] for values in tape]

    assert column("fbv") == [float(i % 2 == 0) for i in range(260)]
    assert column("lbv") == [float(i % 3 == 0) for i in range(260)]
    assert column("cbv") == [float(i % 4) for i in range(260)]
    assert column("first") == [1.0] + [0.0] * 259
    assert set(column("biv")) == {7.0} and set(column("siv")) == {2.5}
    assert set(column("tz")) == {1.0}
    assert sum(column("fb")) == sum(column("lb")) == 10
    assert set(column("minute")) == {0.0, 15.0, 30.0, 45.0}


@pytest.fixture(scope="module")
def tape_replays(tmp_path_factory) -> dict:
    """The probe replayed on the tape's bars and the chart's next one
    (``next_chart_bar``), and with the pre-lane codegen as exported and
    without its two loud names; an exception when a build failed."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("host_member_names_tape")
    tape = [(ts, {"L": values["lb"] == 1.0}) for ts, values in read_host_tape()]
    stamps = [ts for ts, _ in tape] + [next_chart_bar(tape, QUARTER, "America/New_York")]
    jobs = {"now": (_tape_probe(), REPO_ROOT)}
    legacy = reference_codegen(LEGACY)
    if legacy is not None:
        jobs["legacy"] = (_tape_probe(), legacy)
        jobs["legacy-silent"] = (_tape_probe(LOUD_IN_TAPE), legacy)
    results: dict = {}
    for key, (source, codegen) in jobs.items():
        try:
            results[key] = _replay(engine, base / key, source, stamps, codegen)
        except Exception as exc:  # judged by the test that needs it
            results[key] = exc
    return results


def _tape_traced(replay, name: str) -> list[float]:
    return [rec["value"] for rec in replay.traces if rec["name"] == name]


def test_host_named_probe_reads_tradingviews_values(tape_replays) -> None:
    """Every built-in and every host-named variable of the probe equals
    TradingView's on all 260 bars."""
    replay = tape_replays["now"]
    if isinstance(replay, Exception):
        pytest.fail(str(replay), pytrace=False)
    tape = [values for _, values in read_host_tape()]
    for name in TAPE_TRACE:
        got = _tape_traced(replay, name)[:len(tape)]
        want = [values[name] for values in tape]
        assert got == want, (name, [i for i, (a, b) in enumerate(zip(got, want)) if a != b][:5])


def test_pre_lane_build_read_the_variables(tape_replays) -> None:
    """At 7a39cb3 the probe did not compile (``current_bar_`` and ``syminfo_``
    hid the host's bar and symbol). Without those two names it compiled and
    read session.isfirstbar, session.islastbar and barstate.isfirst from the
    script's variables, off TradingView's values."""
    if "legacy" not in tape_replays:
        pytest.skip(f"the pre-lane codegen ({LEGACY[:12]}) is not in this checkout's history")
    legacy = tape_replays["legacy"]
    assert isinstance(legacy, Exception) and "compile failed" in str(legacy)
    silent = tape_replays["legacy-silent"]
    if isinstance(silent, Exception):
        pytest.fail(str(silent), pytrace=False)
    tape = [values for _, values in read_host_tape()]
    for builtin, variable in (("fb", "fbv"), ("lb", "lbv")):
        assert _tape_traced(silent, builtin)[:len(tape)] == [v[variable] for v in tape], builtin
    assert _tape_traced(silent, "first")[:len(tape)] == [0.0] * len(tape)
