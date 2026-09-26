"""A session.* flag read with a history offset is the value the flag had on
that earlier bar, as TradingView's tapes prove.

Two TradingView tapes of one probe on NASDAQ:AAPL 15, 2025-03-03 .. 03-15
(US DST 03-09), with and without extended hours
(``fixtures/session_ismarket/cgs2-hist-aapl-15-*``). The probe reverses its
position at the close of every bar (``process_orders_on_close``), so each Entry
row is one chart bar, dated at its open, and its Signal carries what
TradingView evaluated there: the seven flags (``C``), the same flags ``[1]``
(``H``) and ``[2]`` (``T``), and single reads in other places of the script
(``B`` in a block run on every third bar, ``U`` in a function called on odd
bars, ``Z`` on the lazy side of an ``and``, ``K`` at an offset that changes by
bar, ``D`` at ``[0]``, ``W`` at ``[64]``; see the README).

TradingView reads a flag's history by bars everywhere at the top level of the
script, in a block and on a lazy operand too: ``H`` is the previous bar's
``C`` on all 899 bars. Inside a function it reads the function's own calls:
``U``, ``session.ispostmarket[1]`` in a function called on odd bars, is the
flag on the previous call, two bars back, and differs from the previous bar's
flag on 7 extended-hours bars. Codegen gives each flag read at the top level
one Series pushed on every chart bar, and a read in a function body the
synthetic history of its call site (``_hist_call_*``), like an operator
expression's; in a ``request.security`` payload the read runs on the requested
clock, one value per requested bar.

The pre-lane build emitted ``<flag>[k]`` on a C++ bool and did not compile.
Each tape is replayed end to end -- ``transpile_json``, the built runtime,
``run_strategy.py`` with the session and timezone as runtime overrides -- on
flat bars stamped at the tape's entry times, plus one bar after the last: the
flags are a function of the bars' times and the symbol's session and timezone
only.
"""

from __future__ import annotations

import concurrent.futures
import csv
import datetime as dt
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._compile import compile_cpp
from tests._e2e import (
    REPO_ROOT, build_strategy_library, reference_codegen, run_strategy,
    skip_unless_e2e_env, transpile_json,
)


FIXTURES = Path(__file__).parent / "fixtures" / "session_ismarket"
TAPES = ("cgs2-hist-aapl-15-reg", "cgs2-hist-aapl-15-ext")
EXTENDED = "cgs2-hist-aapl-15-ext"
SESSION, TIMEZONE = "0930-1600", "America/New_York"
# codegen before this lane: every session.*[k] read failed the C++ compile.
LEGACY = "7a39cb3cfe18cfbd393a380babacdcbc3b62667f"
FLAGS = ("ismarket", "ispremarket", "ispostmarket", "isfirstbar", "islastbar",
         "isfirstbar_regular", "islastbar_regular")
GROUPS = (("C", "c", 0), ("H", "h", 1), ("T", "t", 2))
BITS = ("B", "U", "Z", "K", "D", "W")
TRACE = "".join(
    f"\n// @pf-trace {prefix}_{flag}=session.{flag}" + (f"[{offset}]" if offset else "")
    for _, prefix, offset in GROUPS for flag in FLAGS
) + ("\n// @pf-trace b=blk\n// @pf-trace u=fn\n// @pf-trace z=lz"
     "\n// @pf-trace k=session.isfirstbar[k]\n// @pf-trace d=session.ismarket[0]"
     "\n// @pf-trace w=session.ismarket[64]\n")
# The kernel's session-day facts on the extended-hours chart: the engine holds
# one session string, so isfirstbar is isfirstbar_regular and islastbar is
# islastbar_regular, the regular day's 09:30 and 15:45 bars, where
# TradingView's are the extended day's 04:00 and 19:45 bars (engine findings
# F1/F2 of lane CG-ISMARKET); the history reads carry them.
ENGINE = {EXTENDED: {"isfirstbar": 20, "islastbar": 20}}
QUARTER = 15 * 60_000


def _utc_ms(stamp: str, offset_hours: int) -> int:
    local = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M")
    return int((local - dt.timedelta(hours=offset_hours))
               .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def parse_signal(signal: str) -> dict[str, bool]:
    """Trace name -> TradingView's value: ``c_<flag>``, ``h_<flag>`` ([1]) and
    ``t_<flag>`` ([2]) for the seven flags, then ``b u z k d w``."""
    values: dict[str, bool] = {}
    pos = 0
    for letter, prefix, _offset in GROUPS:
        assert signal[pos] == letter, signal
        for i, flag in enumerate(FLAGS):
            assert signal[pos + 1 + i] in "01", signal
            values[f"{prefix}_{flag}"] = signal[pos + 1 + i] == "1"
        pos += 1 + len(FLAGS)
    for letter in BITS:
        assert signal[pos] == letter and signal[pos + 1] in "01", signal
        values[letter.lower()] = signal[pos + 1] == "1"
        pos += 2
    assert pos == len(signal), signal
    return values


def read_tape(slug: str) -> list[tuple[int, dict[str, bool]]]:
    """(the chart bar's open in UTC ms, TradingView's values) per Entry row,
    in time order. The exports render times at UTC+8."""
    with (FIXTURES / slug / "tv_trades.csv").open(encoding="utf-8-sig") as fh:
        rows = [row for row in csv.DictReader(fh) if row["Type"].startswith("Entry")]
    return sorted(((_utc_ms(row["Date and time"], 8), parse_signal(row["Signal"]))
                   for row in rows), key=lambda bar: bar[0])


def sources(n: int) -> dict[str, list[int | None]]:
    """Trace name -> per bar, the bar whose flag the read returns (``None``: no
    such bar, the read is false). TradingView's rules, from its tapes: by bars
    at the top level, by calls in a function."""
    out: dict[str, list[int | None]] = {}

    def at(i: int) -> int | None:
        return i if i >= 0 else None

    for _, prefix, offset in GROUPS:
        for flag in FLAGS:
            out[f"{prefix}_{flag}"] = [at(i - offset) for i in range(n)]
    block, held = [], None
    for i in range(n):
        if i % 3 == 0:  # the block runs; blk := session.ispremarket[1]
            held = at(i - 1)
        block.append(held)
    out["b"] = block
    # post1() runs on odd bars; its [1] is its previous call, two bars back.
    out["u"] = [at(i - 2) if i % 2 == 1 and i >= 3 else None for i in range(n)]
    out["z"] = [at(i - 1) if i % 2 == 0 else None for i in range(n)]
    out["k"] = [at(i - (i % 2 + 1)) for i in range(n)]
    out["d"] = list(range(n))
    out["w"] = [at(i - 64) for i in range(n)]
    return out


READS = {"b": "ispremarket", "u": "ispostmarket", "z": "islastbar", "k": "isfirstbar",
         "d": "ismarket", "w": "ismarket",
         **{f"{prefix}_{flag}": flag for _, prefix, _o in GROUPS for flag in FLAGS}}


def _write_feed(stamps: list[int], path: Path) -> Path:
    path.write_text("timestamp,open,high,low,close,volume\n"
                    + "".join(f"{ts},100,101,99,100.5,1\n" for ts in stamps))
    return path


def _replay_stamps(slug: str) -> list[int]:
    stamps = [ts for ts, _ in read_tape(slug)]
    return stamps + [stamps[-1] + QUARTER]


OVERRIDES = {"input_tf": "15", "script_tf": "15",
             "runtime_overrides": {"session": SESSION, "timezone": TIMEZONE}}


@dataclass
class Replay:
    cpp: str
    diagnostics: list[dict]
    traces: list[dict]
    trades: bytes


def _replay(engine: Path, work: Path, source: str, stamps: list[int],
            codegen: Path = REPO_ROOT) -> Replay:
    work.mkdir(parents=True, exist_ok=True)
    feed = _write_feed(stamps, work / "chart.csv")
    pine = work / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    transpiled = transpile_json(pine, codegen)
    if not transpiled.get("ok"):
        raise RuntimeError("transpile_json refused it:\n"
                           + json.dumps(transpiled.get("diagnostics"), indent=1))
    build_strategy_library(transpiled["cpp"], work)
    trades, records, _ = run_strategy(engine, work, feed, OVERRIDES, "replay", trace=True)
    return Replay(transpiled["cpp"], transpiled.get("diagnostics", []), records or [], trades)


PAYLOAD_PROBE = '''//@version=6
strategy("session history on the requested clock", overlay=true)
sm() => session.ismarket
sp() => session.ispremarket
sq() => session.ispostmarket
pre1() => session.ispremarket[1]
m1 = request.security(syminfo.tickerid, "60", session.ismarket[1])
p1 = request.security(syminfo.tickerid, "60", session.ispremarket[1])
q2 = request.security(syminfo.tickerid, "60", session.ispostmarket[2])
m1_ref = request.security(syminfo.tickerid, "60", sm()[1])
p1_ref = request.security(syminfo.tickerid, "60", sp()[1])
q2_ref = request.security(syminfo.tickerid, "60", sq()[2])
p1_fn = request.security(syminfo.tickerid, "60", pre1())
if m1 or p1 or q2
    strategy.entry("L", strategy.long)
// @pf-trace m1=m1
// @pf-trace p1=p1
// @pf-trace q2=q2
// @pf-trace m1_ref=m1_ref
// @pf-trace p1_ref=p1_ref
// @pf-trace q2_ref=q2_ref
// @pf-trace p1_fn=p1_fn
'''


@pytest.fixture(scope="session")
def replays(tmp_path_factory) -> dict[str, Replay | Exception]:
    """Each tape's probe replayed with a trace of every read, and the payload
    probe on the extended tape's bars; an exception when a build failed."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("session_history")
    jobs = {slug: ((FIXTURES / slug / "strategy.pine").read_text(encoding="utf-8") + TRACE,
                   _replay_stamps(slug), REPO_ROOT) for slug in TAPES}
    jobs["payload"] = (PAYLOAD_PROBE, _replay_stamps(EXTENDED), REPO_ROOT)
    legacy = reference_codegen(LEGACY)
    if legacy is not None:
        jobs["legacy"] = (jobs[EXTENDED][0], jobs[EXTENDED][1], legacy)
    results: dict[str, Replay | Exception] = {}
    workers = max(2, min(4, (os.cpu_count() or 4) // 2))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {key: pool.submit(_replay, engine, base / key, *job)
                   for key, job in jobs.items()}
        for key, future in futures.items():
            try:
                results[key] = future.result()
            except Exception as exc:  # judged by the test that needs it
                results[key] = exc
    return results


def _ok(replays: dict, key: str) -> Replay:
    replay = replays[key]
    if isinstance(replay, Exception):
        pytest.fail(f"[{key}] {replay}", pytrace=False)
    return replay


def traced(replay: Replay, name: str) -> list[bool]:
    return [rec["value"] == 1.0 for rec in replay.traces if rec["name"] == name]


def test_tapes_are_the_recorded_exports() -> None:
    """Every fixture is its export byte for byte, and TradingView's reads
    follow one rule on every bar of both tapes: the top-level reads are the
    flags of earlier bars, the function's read that of its previous call."""
    for slug in TAPES:
        metrics = json.loads((FIXTURES / slug / "metrics.json").read_text())
        tape_bytes = (FIXTURES / slug / "tv_trades.csv").read_bytes()
        pine_bytes = (FIXTURES / slug / "strategy.pine").read_bytes()
        assert hashlib.sha256(tape_bytes).hexdigest() == metrics["tvTradesCsvHash"], slug
        assert hashlib.sha256(pine_bytes).hexdigest() == metrics["sourceArtifactHash"], slug
        assert metrics["wsProvenance"]["rangeProof"] == "covered", slug
        tape = read_tape(slug)
        assert len(tape) == metrics["trades"], slug
        values = [flags for _, flags in tape]
        for name, src in sources(len(tape)).items():
            want = [False if j is None else values[j][f"c_{READS[name]}"] for j in src]
            assert [v[name] for v in values] == want, (slug, name)
    ext = [flags for _, flags in read_tape(EXTENDED)]
    by_bar = [ext[i - 1]["c_ispostmarket"] if i % 2 == 1 else False for i in range(len(ext))]
    by_call = [v["u"] for v in ext]
    assert sum(a != b for a, b in zip(by_bar, by_call)) == 7
    assert sum(v["c_ispremarket"] for v in ext) == 220
    assert sum(v["c_ispostmarket"] for v in ext) == 159


@pytest.mark.parametrize("slug", TAPES)
def test_history_reads_are_the_earlier_values(slug: str, replays) -> None:
    """Every read returns the flag PineForge computed on the bar TradingView's
    rule names: by bars at the top level, by calls in a function."""
    replay = _ok(replays, slug)
    n = len(read_tape(slug)) + 1
    current = {flag: traced(replay, f"c_{flag}") for flag in FLAGS}
    assert all(len(values) == n for values in current.values())
    for name, src in sources(n).items():
        want = [False if j is None else current[READS[name]][j] for j in src]
        assert traced(replay, name) == want, name


@pytest.mark.parametrize("slug", TAPES)
def test_history_reads_are_tradingviews(slug: str, replays) -> None:
    """Each read equals TradingView's on every bar, except where the flag it
    reads already differs from TradingView on the bar it reads (the kernel's
    session-day facts on the extended-hours chart, pinned)."""
    replay = _ok(replays, slug)
    tape = read_tape(slug)
    n = len(tape)
    wrong_current = {}
    for flag in FLAGS:
        got = traced(replay, f"c_{flag}")[:n]
        wrong_current[flag] = {i for i in range(n) if got[i] != tape[i][1][f"c_{flag}"]}
    assert {flag: len(bars) for flag, bars in wrong_current.items() if bars} == ENGINE.get(slug, {})
    for name, src in sources(n).items():
        got = traced(replay, name)[:n]
        missed = {i for i in range(n) if got[i] != tape[i][1][name]}
        expected = {i for i in range(n) if src[i] is not None
                    and src[i] in wrong_current[READS[name]]}
        assert missed == expected, (name, sorted(missed)[:5], sorted(expected)[:5])
    reads = sum(len(src) for src in sources(n).values()) - 7 * n
    print(f"session history {slug}: {reads} reads on {n} bars == TradingView"
          + (f" but for the pinned kernel facts {ENGINE[slug]}" if slug in ENGINE else ""))


def test_payload_history_runs_on_the_requested_clock(replays) -> None:
    """In a request.security payload a flag's history is the requested
    bars': ``session.ismarket[1]`` equals ``sm()[1]`` with ``sm() =>
    session.ismarket`` on every chart bar, and a function's read equals it."""
    replay = _ok(replays, "payload")
    for name in ("m1", "p1", "q2"):
        assert traced(replay, name) == traced(replay, f"{name}_ref"), name
        assert len(traced(replay, name)) == len(_replay_stamps(EXTENDED))
    assert traced(replay, "p1_fn") == traced(replay, "p1_ref")
    assert any(traced(replay, "p1")) and not all(traced(replay, "p1"))
    assert re.search(r"_sec\d+_expr_hist_\d+", replay.cpp)


def test_pre_lane_build_did_not_compile(replays) -> None:
    if "legacy" not in replays:
        pytest.skip(f"the pre-lane codegen ({LEGACY[:12]}) is not in this checkout's history")
    legacy = replays["legacy"]
    assert isinstance(legacy, Exception) and "compile failed" in str(legacy)
    assert "subscripted value is not an array" in str(legacy)


def _transpiled(tmp_path: Path, body: str) -> dict:
    pine = tmp_path / "strategy.pine"
    pine.write_text('//@version=6\nstrategy("session history", overlay=true)\n' + body,
                    encoding="utf-8")
    result = transpile_json(pine)
    assert result["ok"], result["diagnostics"]
    return result


def test_top_level_reads_share_one_series_per_flag(tmp_path: Path) -> None:
    """Needs no engine: each flag read at the top level has one Series,
    declared once and pushed once at the top of every chart bar with the
    flag's own lowering, whatever block or operand reads it; a flag read
    without an offset has none."""
    cpp = _transpiled(tmp_path, "var bool held = false\n"
                                "if bar_index % 3 == 0\n"
                                "    held := session.ispremarket[1]\n"
                                "lazy = bar_index % 2 == 0 and session.ispremarket[2]\n"
                                "k = bar_index % 2 + 1\n"
                                "if held or lazy or session.isfirstbar[k] or session.ismarket\n"
                                '    strategy.entry("L", strategy.long)\n')["cpp"]
    assert cpp.count("Series<bool> _pf_session_hist_ispremarket") == 1
    assert cpp.count("Series<bool> _pf_session_hist_isfirstbar") == 1
    assert "_pf_session_hist_ismarket" not in cpp
    pushes = [line.strip() for line in cpp.splitlines()
              if "_pf_session_hist_" in line and (".push(" in line or ".update(" in line)]
    assert pushes == [
        "if (history_advances_new_bar()) _pf_session_hist_isfirstbar.push(session_isfirstbar_);",
        "else _pf_session_hist_isfirstbar.update(session_isfirstbar_);",
        "if (history_advances_new_bar()) _pf_session_hist_ispremarket.push("
        "(!_pf_session_market_(syminfo_.session, syminfo_.timezone, script_tf_, "
        "current_bar_.timestamp) && pine_session_ispremarket(syminfo_.session, "
        "syminfo_.timezone, current_bar_.timestamp)));",
        "else _pf_session_hist_ispremarket.update((!_pf_session_market_(syminfo_.session, "
        "syminfo_.timezone, script_tf_, current_bar_.timestamp) && "
        "pine_session_ispremarket(syminfo_.session, syminfo_.timezone, "
        "current_bar_.timestamp)));",
    ]
    assert "_pf_session_hist_ispremarket[1]" in cpp
    assert "_pf_session_hist_ispremarket[2]" in cpp
    body = cpp.index("void on_source_bar(")
    assert body < cpp.index("_pf_session_hist_ispremarket.push(") < cpp.index(
        "_pf_session_hist_ispremarket[1]", body)


def test_function_reads_the_calls_history(tmp_path: Path) -> None:
    """Needs no engine: a read in a function body is its call site's own
    history, pushed where the read runs, not the chart bar's Series."""
    cpp = _transpiled(tmp_path, "post1() => session.ispostmarket[1]\n"
                                "fn = bar_index % 2 == 1 ? post1() : false\n"
                                'if fn\n    strategy.entry("L", strategy.long)\n')["cpp"]
    assert "_pf_session_hist_" not in cpp
    assert re.search(r"Series<bool> _hist_call_\d+", cpp)
    assert re.search(r"bool _hv = \(\(!_pf_session_market_\(.*\) && "
                     r"pine_session_ispostmarket\(.*\)\)\); if \(history_advances_new_bar\(\)\) "
                     r"_hist_call_\d+\.push\(_hv\)", cpp)


def test_every_flag_and_place_compiles(tmp_path: Path) -> None:
    """Every flag at every offset shape, at the top level, in a function, in a
    payload and in a function a payload calls: the TU compiles (it did not)."""
    reads = "\n".join(
        f"top_{flag} = session.{flag}[1] or session.{flag}[0] or session.{flag}[bar_index % 3]\n"
        f"fn_{flag}() => session.{flag}[2]\n"
        f"call_{flag} = fn_{flag}()\n"
        f'sec_{flag} = request.security(syminfo.tickerid, "60", session.{flag}[1])\n'
        f'secfn_{flag} = request.security(syminfo.tickerid, "60", fn_{flag}())'
        for flag in FLAGS)
    uses = " or ".join(f"top_{f} or call_{f} or sec_{f} or secfn_{f}" for f in FLAGS)
    result = _transpiled(tmp_path, reads + f"\nif {uses}\n    strategy.entry(\"L\", strategy.long)\n")
    compile_cpp(result["cpp"], label="session history everywhere")
