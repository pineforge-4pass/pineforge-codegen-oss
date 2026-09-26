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
it renames one spelled like a C++ keyword. This module regenerates the set, and
checks it against every host member a transpiled battery actually names.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from tests import _compile as compile_env
from tests._e2e import (
    REPO_ROOT, Build, assert_same_runs, chart_feed_head, execute_all, ok,
    skip_unless_e2e_env, transpile_json,
)


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
    for name in ("session_isfirstbar_", "session_islastbar_", "syminfo_", "current_bar_",
                 "bar_index_", "trades_", "closed_trade_profit", "open_trade_max_drawdown"):
        assert name in derived.names, name
    print(f"{len(derived.names)} of the host's {len(derived.host)} accessible members "
          f"reserved ({derived.host_class})")


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
                    "[_src_close_, _src_open_] = [close, open]\n"
                    "total = 0\n"
                    "for security_eval_states_ = 0 to 1\n"
                    "    total += security_eval_states_\n"
                    "h = Holder.new(close)\n"
                    "if time > 0 and syminfo.mintick > 0 and h.current_bar_ > 0 and "
                    "input_tf_(1) + f(1) + syminfo_ + trades_ + _src_close_ + total > 0\n"
                    '    strategy.entry("L", strategy.long)\n', encoding="utf-8")
    result = transpile_json(pine)
    assert result["ok"], result["diagnostics"]
    cpp = result["cpp"]
    for name in ("current_bar_", "input_tf_", "prev_bar_timestamp_", "syminfo_", "trades_",
                 "_src_close_", "_src_open_", "security_eval_states_"):
        assert f"pf_safe_{name}" in cpp, name
    assert [entry["title"] for entry in result["inputs"]] == ["syminfo_"]
    compile_cpp_or_skip(cpp)


def compile_cpp_or_skip(cpp: str) -> None:
    compile_env.compile_cpp(cpp, label="host member names")


SILENT = '''//@version=6
strategy("host member names", overlay=true, process_orders_on_close=true)
{fb} = close > open
{lb} = not {fb}
{bi} = 7
{bl} = true
{lt} = false
{ic} = 12345.0
{lbt} = 5
if session.isfirstbar or barstate.isfirst
    strategy.entry("L", strategy.long)
if session.islastbar and barstate.isconfirmed
    strategy.close("L")
// @pf-trace fb=session.isfirstbar
// @pf-trace lb=session.islastbar
// @pf-trace first=barstate.isfirst
// @pf-trace last=barstate.islast
// @pf-trace conf=barstate.isconfirmed
// @pf-trace cap=strategy.initial_capital
// @pf-trace lbt=last_bar_time
// @pf-trace own={fb} ? 1 : 0
// @pf-trace own2={bi} + {ic} + {lbt} + ({bl} ? 1 : 0) + ({lt} ? 1 : 0) + ({lb} ? 1 : 0)
'''
SILENT_NAMES = {"fb": "session_isfirstbar_", "lb": "session_islastbar_", "bi": "bar_index_",
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
