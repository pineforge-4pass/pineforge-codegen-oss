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
bar, ``D`` at ``[0]``, ``W`` at ``[64]``; see the README). A third tape, of a
probe of functions on the extended-hours chart (``cgs2-histfn-aapl-15-ext``),
has one function at two call sites, reads on a lazy operand and in a block
inside a function, and a ``newSession()`` idiom at two call sites.

TradingView reads a flag's history by bars everywhere at the top level of the
script, in a block and on a lazy operand too: ``H`` is the previous bar's
``C`` on all 899 bars. Inside a function it reads the function's own calls,
each call site apart: ``U``, ``session.ispostmarket[1]`` in a function called
on odd bars, is the flag on the previous call, two bars back (7
extended-hours bars differ from the previous bar's flag), a second call site
of one function reads its own previous call, and a read on a lazy operand or
in a block inside a function called on every bar is the previous call's flag
too, not the previous time the read ran. Codegen gives each flag read at the
top level one Series pushed on every chart bar; a function that reads a flag
at an offset is emitted once per call site and pushes the flag at its entry,
once per call; in a ``request.security`` expression the read runs on the
requested clock, one value per requested bar; one it reaches through a
call's argument or a variable, and one in a function or method it evaluates,
is refused, located at the read.

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
FUNCTIONS = "cgs2-histfn-aapl-15-ext"
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
    tape = read_function_tape() if slug == FUNCTIONS else read_tape(slug)
    stamps = [ts for ts, _ in tape]
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
m1 = request.security(syminfo.tickerid, "60", session.ismarket[1])
p1 = request.security(syminfo.tickerid, "60", session.ispremarket[1])
q2 = request.security(syminfo.tickerid, "60", session.ispostmarket[2])
m1_ref = request.security(syminfo.tickerid, "60", sm()[1])
p1_ref = request.security(syminfo.tickerid, "60", sp()[1])
q2_ref = request.security(syminfo.tickerid, "60", sq()[2])
first = request.security(syminfo.tickerid, "60", session.ispremarket[1] == false)
if m1 or p1 or q2
    strategy.entry("L", strategy.long)
// @pf-trace m1=m1
// @pf-trace p1=p1
// @pf-trace q2=q2
// @pf-trace m1_ref=m1_ref
// @pf-trace p1_ref=p1_ref
// @pf-trace q2_ref=q2_ref
// @pf-trace first=first
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
    jobs[FUNCTIONS] = ((FIXTURES / FUNCTIONS / "strategy.pine").read_text(encoding="utf-8")
                       + FUNCTION_TRACE, _replay_stamps(FUNCTIONS), REPO_ROOT)
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
    metrics = json.loads((FIXTURES / FUNCTIONS / "metrics.json").read_text())
    assert hashlib.sha256((FIXTURES / FUNCTIONS / "tv_trades.csv").read_bytes()).hexdigest() \
        == metrics["tvTradesCsvHash"]
    assert hashlib.sha256((FIXTURES / FUNCTIONS / "strategy.pine").read_bytes()).hexdigest() \
        == metrics["sourceArtifactHash"]
    assert metrics["wsProvenance"]["rangeProof"] == "covered"
    tape = read_function_tape()
    assert len(tape) == metrics["trades"] == 639
    values = [flags for _, flags in tape]
    for name, want in function_rule([v["c"] for v in values]).items():
        assert [v[name] for v in values] == want, name
    by_bar = function_rule([v["c"] for v in values], by_calls=False)
    assert {name: sum(a != b for a, b in zip(by_bar[name], [v[name] for v in values]))
            for name in ("b", "z", "k")} == {"b": 7, "z": 12, "k": 13}


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
    session.ismarket`` on every chart bar, and a read before the first
    requested bar is false."""
    replay = _ok(replays, "payload")
    for name in ("m1", "p1", "q2"):
        assert traced(replay, name) == traced(replay, f"{name}_ref"), name
        assert len(traced(replay, name)) == len(_replay_stamps(EXTENDED))
    assert any(traced(replay, "p1")) and not all(traced(replay, "p1"))
    assert re.search(r"Series<bool> _sec\d+_expr_hist_\d+", replay.cpp)
    # Before the first requested bar the read is false, as a bool history is.
    first = [rec["value"] for rec in replay.traces
             if rec["name"] == "first" and rec["value"] == rec["value"]]
    assert first[:1] == [1.0]


def test_pre_lane_build_did_not_compile(replays) -> None:
    if "legacy" not in replays:
        pytest.skip(f"the pre-lane codegen ({LEGACY[:12]}) is not in this checkout's history")
    legacy = replays["legacy"]
    assert isinstance(legacy, Exception) and "compile failed" in str(legacy)


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


def test_function_reads_its_call_sites_calls(tmp_path: Path) -> None:
    """Needs no engine: a function that reads a flag at an offset is emitted
    once per call site, and each body pushes the flag into its own Series at
    entry, once per call, before any read of it can be skipped."""
    cpp = _transpiled(tmp_path, "post1() => bar_index % 2 == 0 and session.ispostmarket[1]\n"
                                "a = post1()\n"
                                "b = bar_index % 2 == 1 ? post1() : false\n"
                                'if a or b\n    strategy.entry("L", strategy.long)\n')["cpp"]
    assert "_pf_session_hist_" not in cpp
    members = re.findall(r"Series<bool> (_session_call_\d+)", cpp)
    assert len(members) == 2
    bodies = re.findall(r"bool post1_cs(\d)\(\) \{\n(.*?)\n    \}", cpp, flags=re.S)
    assert [index for index, _ in bodies] == ["0", "1"]
    for (_, body), member in zip(bodies, members):
        lines = body.strip().splitlines()
        assert lines[0].strip().startswith(f"if (history_advances_new_bar()) {member}.push(")
        assert lines[1].strip().startswith(f"else {member}.update(")
        assert f"{member}[1]" in body


def read_function_tape() -> list[tuple[int, dict]]:
    """(the chart bar's open in UTC ms, TradingView's values) per Entry row of
    the function probe: ``c`` the seven flags by name, then ``a b z k n o``."""
    with (FIXTURES / FUNCTIONS / "tv_trades.csv").open(encoding="utf-8-sig") as fh:
        rows = [row for row in csv.DictReader(fh) if row["Type"].startswith("Entry")]
    tape = []
    for row in rows:
        signal = row["Signal"]
        assert signal[0] == "C" and len(signal) == 20, signal
        flags = {flag: signal[1 + i] == "1" for i, flag in enumerate(FLAGS)}
        values = {"c": flags}
        for pos, letter in zip(range(8, 20, 2), "ABZKNO"):
            assert signal[pos] == letter and signal[pos + 1] in "01", signal
            values[letter.lower()] = signal[pos + 1] == "1"
        tape.append((_utc_ms(row["Date and time"], 8), values))
    return sorted(tape, key=lambda bar: bar[0])


def function_rule(flags: list[dict[str, bool]], by_calls: bool = True) -> dict[str, list[bool]]:
    """What each read of the function probe returns from the flags per bar:
    by the calls of its own call site (TradingView), or, ``by_calls=False``,
    by bars for ``b`` and by the reads that ran for ``z`` and ``k``."""
    n = len(flags)

    def flag(name: str, i: int) -> bool:
        return i >= 0 and flags[i][name]

    post, pre, market = "ispostmarket", "ispremarket", "ismarket"
    back = {"b": 2, "z": 1, "k": 1} if by_calls else {"b": 1, "z": 2, "k": 3}
    return {
        "a": [flag(post, i - 1) for i in range(n)],
        "b": [i % 2 == 1 and flag(post, i - back["b"]) and (i >= 3 or not by_calls)
              for i in range(n)],
        "z": [i % 2 == 0 and flag(post, i - back["z"]) for i in range(n)],
        "k": [i % 3 == 0 and flag(pre, i - back["k"]) for i in range(n)],
        "n": [flag(market, i) and not flag(market, i - 1) for i in range(n)],
        "o": [flag(market, i) and not flag(market, i - 1) for i in range(n)],
    }


FUNCTION_TRACE = "".join(f"\n// @pf-trace c_{flag}=session.{flag}" for flag in FLAGS) + "".join(
    f"\n// @pf-trace {name}={name}" for name in "abzkno") + "\n"


def test_function_reads_are_tradingviews(replays) -> None:
    """The function probe replayed: every read is TradingView's on every bar,
    by the calls of its own call site."""
    replay = _ok(replays, FUNCTIONS)
    tape = read_function_tape()
    n = len(tape)
    current = [{flag: v for flag, v in zip(FLAGS, bits)} for bits in zip(
        *(traced(replay, f"c_{flag}")[:n] for flag in FLAGS))]
    for flag in ("ismarket", "ispremarket", "ispostmarket"):
        assert [c[flag] for c in current] == [v["c"][flag] for _, v in tape], flag
    for name, want in function_rule(current).items():
        got = traced(replay, name)[:n]
        assert got == want, name
        assert got == [v[name] for _, v in tape], name
    print(f"session history in functions: {6 * n} reads on {n} bars == TradingView")


REFUSED = {
    "a function a request.security expression calls": (
        "pre1() => session.ispremarket[1]\n"
        'x = request.security(syminfo.tickerid, "60", pre1())\n', (3, 30), "request.security"),
    "a variable a request.security expression reads": (
        "m = session.ismarket\n"
        'x = request.security(syminfo.tickerid, "60", m[1])\n', (4, 48), "request.security"),
    "a function called inside nz() in the expression": (
        "f() => session.ispostmarket[1]\n"
        "g() => nz(f() ? 1.0 : na)\n"
        "a = g()\n"
        'x = request.security(syminfo.tickerid, "60", g()) > 0\n', (3, 28), "request.security"),
    "a method of a float": (
        "method post1(float self) => session.ispostmarket[1] and self > 0\n"
        'x = request.security(syminfo.tickerid, "60", close.post1())\n', (3, 49), "a method"),
    "a method a wrapper calls on the chart too": (
        "type Foo\n    float v = 1\n"
        "method m(Foo self) => session.ismarket[1] and self.v > 0\n"
        "w(Foo o) => o.m()\n"
        "foo = Foo.new()\n"
        "a = w(foo)\n"
        'x = request.security(syminfo.tickerid, "60", w(foo))\n', (5, 39), "a method"),
    "an argument of a call in the expression": (
        'x = request.security(syminfo.tickerid, "60", nz(session.ismarket[1] ? 1.0 : na)) > 0\n',
        (3, 65), "request.security"),
    "a method called on a receiver PineForge cannot type": (
        "type Foo\n    float v = 1\n"
        "method m(Foo self) => session.ismarket[1] and self.v > 0\n"
        "mk() => Foo.new()\n"
        "a = mk().m()\n"
        "x = bar_index % 2 == 0 ? mk().m() : a\n", (5, 39), "a method"),
    "a method called on a loop variable": (
        "type Foo\n    float v = 1\n"
        "method m(Foo self) => session.ismarket[1] and self.v > 0\n"
        "foos = array.from(Foo.new())\n"
        "x = false\n"
        "for f in foos\n    x := f.m()\n", (5, 39), "a method"),
    "a function a mutable global's statements call, the global's name shadowed": (
        "f() => session.ismarket[3] ? 1.0 : 0.0\n"
        "var float g = 0.0\n"
        "g := nz(f())\n"
        "h(float g) => g > open\n"
        'x = request.security(syminfo.tickerid, "60", h(close)) or g > 0\n', (3, 24), "request.security"),
    "a function a method calls": (
        "type Foo\n    float v = 1\n"
        "f() => session.ismarket[1]\n"
        "method m(Foo self) => f() and self.v > 0\n"
        "mk() => Foo.new()\n"
        "x = mk().m()\n", (5, 24), "which a method calls"),
    "a function a UDT field default calls": (
        "f() => session.ismarket[1]\n"
        "type Foo\n    bool v = f()\n"
        "x = Foo.new().v\n", (3, 24), "field default"),
}


@pytest.mark.parametrize("place", REFUSED)
def test_unsupported_places_are_refused_at_the_read(place: str, tmp_path: Path) -> None:
    """Needs no engine: a read that a request.security expression reaches
    through a function, a method, a variable or a call's argument has no
    history on the requested clock in PineForge; it is refused at the read,
    whichever way the evaluator would reach the function (the C++ did not
    compile before)."""
    body, (line, col), reason = REFUSED[place]
    pine = tmp_path / "strategy.pine"
    pine.write_text('//@version=6\nstrategy("session history", overlay=true)\n' + body
                    + 'if x\n    strategy.entry("L", strategy.long)\n', encoding="utf-8")
    result = transpile_json(pine)
    assert not result["ok"]
    errors = [d for d in result["diagnostics"] if d["severity"] == "error"]
    assert len(errors) == 1 and (errors[0]["line"], errors[0]["col"]) == (line, col), errors
    assert reason in errors[0]["message"], errors[0]["message"]


def test_a_payload_read_in_a_function_needs_no_call_history(tmp_path: Path) -> None:
    """Needs no engine: a function whose only read is written in its own
    request.security expression reads the requested clock's history; it keeps
    no per-call Series."""
    cpp = _transpiled(tmp_path, "f() => request.security(syminfo.tickerid, \"60\", session.ismarket[1])\n"
                                "a = f()\nb = bar_index % 2 == 0 ? f() : false\n"
                                'if a or b\n    strategy.entry("L", strategy.long)\n')["cpp"]
    assert "_session_call_" not in cpp
    assert re.search(r"Series<bool> _sec\d+_expr_hist_\d+", cpp)
    compile_cpp(cpp, label="payload read in a function")


def test_generated_member_names_stay_distinct(tmp_path: Path) -> None:
    """Needs no engine: a per-call session Series is numbered past a script
    name spelled like one (``_session_call_1``), which keeps its name, and so
    is the top-level Series (``_pf_session_hist_ismarket``): the TU compiles."""
    cpp = _transpiled(tmp_path, "_session_call_1 = close > open\n"
                                "_pf_session_hist_ismarket = 2.0\n"
                                "post1() => session.ispostmarket[1]\n"
                                "a = post1()\n"
                                "if a or _session_call_1 or session.ismarket[1] "
                                "or _pf_session_hist_ismarket > 1\n"
                                '    strategy.entry("L", strategy.long)\n')["cpp"]
    assert "Series<bool> _session_call_2" in cpp and "_session_call_1 = " in cpp
    assert "Series<bool> _pf_session_hist_ismarket__pf2" in cpp
    assert "_pf_session_hist_ismarket = " in cpp
    compile_cpp(cpp, label="generated member names")


STILL_COMPILE = {
    "a read in alert() of a method a request.security expression calls": (
        "type Foo\n    float v = 1\n"
        "method m(Foo self) =>\n"
        '    alert(session.ismarket[1] ? "in" : "out")\n'
        "    self.v\n"
        "foo = Foo.new()\n"
        'x = request.security(syminfo.tickerid, "60", foo.m()) > 0\n'),
    "a read in plot() at the top level": (
        "plot(session.ismarket[1] ? 1 : 0)\n"
        "x = close > open\n"),
    "a read in a strategy.entry alert_message in a function": (
        "f() =>\n"
        '    strategy.entry("L", strategy.long, alert_message = session.ismarket[1] ? "a" : "b")\n'
        "    close\n"
        "x = f() > 0\n"),
    "a read in a drawing's color in a method called on typed and untyped receivers": (
        "type Foo\n    float v = 1\n"
        "method m(Foo self) =>\n"
        "    line.new(bar_index - 1, low, bar_index, high, "
        "color = session.ismarket[1] ? color.green : color.red)\n"
        "    self.v > 0\n"
        "mk() => Foo.new()\n"
        "foo = Foo.new()\n"
        "x = mk().m() or foo.m()\n"),
    "a read in a drawing's color in a function a trace calls": (
        "f() =>\n"
        "    label.new(bar_index, high, \"x\", color = session.ismarket[1] ? color.green : color.red)\n"
        "    close > open\n"
        "x = f()\n"
        "// @pf-trace t=f()\n"),
    "a read in a drawing's color in a method": (
        "type Foo\n    float v = 1\n"
        "method m(Foo self) =>\n"
        "    line.new(bar_index - 1, low, bar_index, high, "
        "color = session.ismarket[1] ? color.green : color.red)\n"
        "    self.v > 0\n"
        "mk() => Foo.new()\n"
        "x = mk().m()\n"),
}
KEPT = {
    "a script name spelled like a generated history member": (
        "_series_arg_1 = close > open\n_hist_call_1 = 2.0\n"
        "x = _series_arg_1 and _hist_call_1 > 1\n"),
}


@pytest.mark.parametrize("place", STILL_COMPILE)
def test_reads_the_codegen_drops_refuse_nothing(place: str, tmp_path: Path) -> None:
    """A session read the codegen never emits (in a skipped call, a dropped
    strategy parameter or a drawing's style argument) refuses nothing, even in
    a method or a function a request.security expression calls: the script
    transpiles and compiles, as it did before this lane (7a39cb3). Its C++ may
    carry a Series for the read that nothing reads."""
    legacy = reference_codegen(LEGACY)
    if legacy is None:
        pytest.skip(f"the pre-lane codegen ({LEGACY[:12]}) is not in this checkout's history")
    pine = tmp_path / "strategy.pine"
    pine.write_text('//@version=6\nstrategy("session history", overlay=true)\n'
                    + STILL_COMPILE[place]
                    + 'if x\n    strategy.entry("L", strategy.long)\n', encoding="utf-8")
    now, before = transpile_json(pine), transpile_json(pine, legacy)
    assert now["ok"] and before["ok"], (now["diagnostics"], before["diagnostics"])
    compile_cpp(now["cpp"], label=place)


@pytest.mark.parametrize("place", KEPT)
def test_scripts_without_a_read_keep_their_cpp(place: str, tmp_path: Path) -> None:
    """A script with no session.<flag>[k] keeps its C++ (7a39cb3's), even with
    a name spelled like a generated history member."""
    legacy = reference_codegen(LEGACY)
    if legacy is None:
        pytest.skip(f"the pre-lane codegen ({LEGACY[:12]}) is not in this checkout's history")
    pine = tmp_path / "strategy.pine"
    pine.write_text('//@version=6\nstrategy("session history", overlay=true)\n' + KEPT[place]
                    + 'if x\n    strategy.entry("L", strategy.long)\n', encoding="utf-8")
    now, before = transpile_json(pine), transpile_json(pine, legacy)
    assert now["ok"] and before["ok"], (now["diagnostics"], before["diagnostics"])
    assert now["cpp"] == before["cpp"]
    compile_cpp(now["cpp"], label=place)


def test_a_trace_can_read_a_flag_at_an_offset(tmp_path: Path) -> None:
    """Needs no engine: a ``@pf-trace`` that is the only reader of a flag's
    history gets the flag's Series too."""
    cpp = _transpiled(tmp_path, 'if close > open\n    strategy.entry("L", strategy.long)\n'
                                "// @pf-trace m1=session.ismarket[1]\n")["cpp"]
    assert cpp.count("Series<bool> _pf_session_hist_ismarket") == 1
    compile_cpp(cpp, label="trace-only session history")


def test_every_flag_and_place_compiles(tmp_path: Path) -> None:
    """Every flag at every offset shape, at the top level, in a function
    called from two call sites and in a payload: the TU compiles (it did
    not)."""
    reads = "\n".join(
        f"top_{flag} = session.{flag}[1] or session.{flag}[0] or session.{flag}[bar_index % 3]\n"
        f"fn_{flag}() => session.{flag}[2]\n"
        f"call_{flag} = fn_{flag}()\n"
        f"call2_{flag} = bar_index % 2 == 0 ? fn_{flag}() : false\n"
        f'sec_{flag} = request.security(syminfo.tickerid, "60", session.{flag}[1])'
        for flag in FLAGS)
    uses = " or ".join(f"top_{f} or call_{f} or call2_{f} or sec_{f}" for f in FLAGS)
    result = _transpiled(tmp_path, reads + f"\nif {uses}\n    strategy.entry(\"L\", strategy.long)\n")
    compile_cpp(result["cpp"], label="session history everywhere")
