"""session.ismarket is TradingView's flag, as its tapes prove.

TradingView flags every one of the 1,138 bars of six 60-minute charts in market
(``fixtures/session_ismarket``: the engine lane H-MEASURE's tapes
``hm-g236-*`` of CME_MINI:ES1! across both 2025 US DST switches and the
Thanksgiving week, OANDA:EURUSD, OANDA:XAUUSD and BINANCE:ETHUSDT.P). Each
probe reverses its position at the close of every bar
(``process_orders_on_close``), so each Entry row is one chart bar, dated at its
open, and its Signal is the flag TradingView evaluated there: ``M1`` in
market, ``M0`` not (see the README).

The engine's source host stores the kernel's in-session fact of the script bar
(``NativeDecisionContext::in_session``, read off the session day the bar
belongs to) as ``session_ismarket_`` before every source callback, and codegen
reads it. The pre-lane lowering, the time-of-day predicate
``pine_session_ismarket(syminfo_.session, syminfo_.timezone, time)``, tests
each instant's own weekday: it read every Sunday-evening open of a ``:23456``
session (the session day it opens is Monday's) and every bar of ``0000-2400``
as out of market.

Each tape is replayed end to end -- ``transpile_json``, the built runtime,
``run_strategy.py`` with the session and timezone as runtime overrides -- on
flat bars stamped at the tape's entry times, plus one bar an hour after the
last: the flag is a function of a bar's time and the symbol's session and
timezone only, and every tape's session day goes on past its window. The
tape's own probe runs with a ``@pf-trace`` of every session flag, under each
session spelling H-MEASURE measured: the campaign's lane facts, the same
sessions with TradingView's weekday mask, and a 24-hour day spelled
``0000-2400`` and ``0000-0000``. The pre-lane build (``LEGACY``) is replayed
beside it and misses exactly the pinned bars.

A ``request.security`` payload is evaluated on its own bars, for which the
host holds no session fact: it keeps the time-of-day predicate at the
security bar's time, so its values are the pre-lane build's. So does a batch
of one bar given no timeframe, where the engine detects none and presents no
facts.
"""

from __future__ import annotations

import concurrent.futures
import csv
import datetime as dt
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests._compile import compile_cpp
from tests._e2e import (
    REPO_ROOT, build_strategy_library, reference_codegen, run_strategy,
    skip_unless_e2e_env, transpile_json,
)


FIXTURES = Path(__file__).parent / "fixtures" / "session_ismarket"
# codegen main before this lane: session.ismarket was the time-of-day predicate.
LEGACY = "b0ed4967b3b5ab7aea80fea3edc783b16b04b16e"
CHARTS = ("es1-60-dst-mar", "es1-60-dst-nov", "es1-60-thanksgiving",
          "eurusd-60-dst-mar", "xauusd-60-dst-mar", "eth-60-24x7")
TAPES = tuple(f"hm-g236-{chart}" for chart in CHARTS)
FLAGS = {"M": "ismarket", "P": "ispremarket", "Q": "ispostmarket",
         "F": "isfirstbar", "L": "islastbar",
         "f": "isfirstbar_regular", "l": "islastbar_regular"}
TRACE = "".join(f"\n// @pf-trace {name}=session.{name}" for name in FLAGS.values()) + "\n"
HOUR = 3_600_000


@dataclass(frozen=True)
class Case:
    slug: str
    session: str
    timezone: str
    # flag letter -> bars where the pre-lane build was not TradingView's flag
    legacy: dict[str, int] = field(hash=False)
    legacy_sunday: int = 0  # of its session.ismarket misses, those on a local Sunday

    @property
    def key(self) -> str:
        return f"{self.slug}@{self.session}"


def _cases(chart: str, session: str, timezone: str, hm: dict[str, int],
           sunday: int = 0) -> list[Case]:
    return [Case(f"hm-g236-{chart}", session, timezone, hm, sunday)]


CASES = tuple(case for cases in (
    # The campaign's lane facts (pineforge-lab config/symbol-lanes-v1.json).
    _cases("es1-60-dst-mar", "1700-1600", "America/Chicago", {}),
    _cases("es1-60-dst-nov", "1700-1600", "America/Chicago", {}),
    _cases("es1-60-thanksgiving", "1700-1600", "America/Chicago", {}),
    _cases("eurusd-60-dst-mar", "1700-1700", "America/New_York", {}),
    _cases("xauusd-60-dst-mar", "1800-1700", "America/New_York", {}),
    _cases("eth-60-24x7", "24x7", "UTC", {}),
    # The same sessions with TradingView's weekday mask: the time-of-day
    # predicate missed every Sunday-evening open.
    _cases("es1-60-dst-mar", "1700-1600:23456", "America/Chicago", {"M": 14}, 14),
    _cases("es1-60-dst-nov", "1700-1600:23456", "America/Chicago", {"M": 14}, 14),
    _cases("es1-60-thanksgiving", "1700-1600:23456", "America/Chicago", {"M": 14}, 14),
    _cases("eurusd-60-dst-mar", "1700-1700:23456", "America/New_York", {"M": 14}, 14),
    _cases("xauusd-60-dst-mar", "1800-1700:23456", "America/New_York", {"M": 12}, 12),
    # A 24-hour day: "0000-2400" (the predicate: never in market) and
    # TradingView's spelling "0000-0000" (both: always).
    _cases("eth-60-24x7", "0000-2400", "UTC", {"M": 97}, 24),
    _cases("eth-60-24x7", "0000-0000", "UTC", {}),
) for case in cases)
SECURITY_CASE = next(case for case in CASES if case.key == "hm-g236-es1-60-dst-mar@1700-1600:23456")
SECURITY_TRACE = "".join(
    f'\nsecurity_{name} = request.security(syminfo.tickerid, "60", session.{name})'
    f"\n// @pf-trace security_{name}=security_{name}"
    for name in ("ismarket",)) + "\n"


def _utc_ms(stamp: str, offset_hours: int) -> int:
    local = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M")
    return int((local - dt.timedelta(hours=offset_hours))
               .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def read_tape(slug: str) -> list[tuple[int, dict[str, bool]]]:
    """(the chart bar's open in UTC ms, TradingView's flags by letter) per
    Entry row, in time order. ``lab tv`` renders times at UTC+8; a Signal is a
    letter and a digit per flag (``M1``)."""
    with (FIXTURES / slug / "tv_trades.csv").open(encoding="utf-8-sig") as fh:
        rows = [row for row in csv.DictReader(fh) if row["Type"].startswith("Entry")]
    tape = []
    for row in rows:
        signal = row["Signal"]
        flags = {signal[i]: signal[i + 1] == "1" for i in range(0, len(signal), 2)}
        assert set(flags) <= set(FLAGS) and len(signal) == 2 * len(flags), (slug, signal)
        assert all(signal[i + 1] in "01" for i in range(0, len(signal), 2)), (slug, signal)
        tape.append((_utc_ms(row["Date and time"], 8), flags))
    return sorted(tape, key=lambda bar: bar[0])


def engine_entry_times(trades_csv: bytes) -> list[int]:
    rows = csv.DictReader(trades_csv.decode().splitlines())
    return sorted(_utc_ms(row["Date and time"], 0) for row in rows
                  if row["Type"].startswith("Entry"))


def traced(records: list[dict], name: str) -> list[tuple[int, bool]]:
    return [(rec["timestamp"], rec["value"] == 1.0) for rec in records if rec["name"] == name]


def _write_feed(stamps: list[int], path: Path) -> Path:
    path.write_text("timestamp,open,high,low,close,volume\n"
                    + "".join(f"{ts},100,101,99,100.5,1\n" for ts in stamps))
    return path


def _replay_stamps(slug: str) -> list[int]:
    stamps = [ts for ts, _ in read_tape(slug)]
    return stamps + [stamps[-1] + HOUR]


def _overrides(case: Case) -> dict:
    return {"input_tf": "60", "script_tf": "60",
            "runtime_overrides": {"session": case.session, "timezone": case.timezone}}


@dataclass
class Replay:
    cpp: str
    trades: dict[str, bytes]
    traces: dict[str, list[dict]]


def _replay(engine: Path, work: Path, source: str, codegen: Path,
            cases: list[Case]) -> Replay:
    work.mkdir(parents=True, exist_ok=True)
    feed = _write_feed(_replay_stamps(cases[0].slug), work / "chart.csv")
    pine = work / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    transpiled = transpile_json(pine, codegen)
    if not transpiled.get("ok"):
        raise RuntimeError("transpile_json refused it:\n"
                           + json.dumps(transpiled.get("diagnostics"), indent=1))
    build_strategy_library(transpiled["cpp"], work)
    replay = Replay(cpp=transpiled["cpp"], trades={}, traces={})
    for i, case in enumerate(cases):
        trades, records, _ = run_strategy(engine, work, feed, _overrides(case),
                                          f"case{i}", trace=True)
        replay.trades[case.key] = trades
        replay.traces[case.key] = records or []
    return replay


@pytest.fixture(scope="session")
def replays(tmp_path_factory) -> dict[tuple[str, str], Replay]:
    """``(build, slug)`` -> the tape's probe replayed under every session
    spelling of its chart; build is ``current``, ``legacy`` (when LEGACY is in
    this checkout's history), or ``security-current`` / ``security-legacy``."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("session_ismarket")
    legacy = reference_codegen(LEGACY)
    builds = {"current": REPO_ROOT} | ({"legacy": legacy} if legacy is not None else {})
    jobs: dict[tuple[str, str], tuple[str, Path, list[Case]]] = {}
    for slug in TAPES:
        probe = (FIXTURES / slug / "strategy.pine").read_text(encoding="utf-8") + TRACE
        cases = [case for case in CASES if case.slug == slug]
        for label, codegen in builds.items():
            jobs[(label, slug)] = (probe, codegen, cases)
    probe = (FIXTURES / SECURITY_CASE.slug / "strategy.pine").read_text(encoding="utf-8")
    for label, codegen in builds.items():
        jobs[(f"security-{label}", SECURITY_CASE.slug)] = (
            probe + SECURITY_TRACE + TRACE, codegen, [SECURITY_CASE])
    workers = max(2, min(8, (os.cpu_count() or 4) // 2))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {key: pool.submit(_replay, engine, base / key[0] / key[1], *job)
                   for key, job in jobs.items()}
        return {key: future.result() for key, future in futures.items()}


def _misses(case: Case, replay: Replay, prefix: str = "") -> dict[str, list[int]]:
    """The tape's bars where the replay's flag is not TradingView's, per flag
    the tape carries."""
    tape = read_tape(case.slug)
    records = replay.traces[case.key]
    misses = {}
    for letter in tape[0][1]:
        flags = traced(records, prefix + FLAGS[letter])
        assert [ts for ts, _ in flags] == _replay_stamps(case.slug), (
            f"[{case.key}] {FLAGS[letter]} traced {len(flags)} bars, "
            f"the replay holds {len(tape) + 1}")
        misses[letter] = [ts for (ts, tv), (_, got) in zip(tape, flags) if got != tv[letter]]
    return misses


def _on_sunday(stamps: list[int], timezone: str) -> int:
    zone = ZoneInfo(timezone)
    return sum(dt.datetime.fromtimestamp(ts / 1000, zone).isoweekday() == 7 for ts in stamps)


def test_tapes_are_the_recorded_exports() -> None:
    """Every fixture is its export byte for byte, and TradingView flagged every
    one of the tapes' 1,138 bars in market."""
    bars = {"hm-g236": 0}
    for slug in TAPES:
        metrics = json.loads((FIXTURES / slug / "metrics.json").read_text())
        meta = json.loads((FIXTURES / slug / "meta.json").read_text())
        tape_bytes = (FIXTURES / slug / "tv_trades.csv").read_bytes()
        pine_bytes = (FIXTURES / slug / "strategy.pine").read_bytes()
        assert hashlib.sha256(tape_bytes).hexdigest() == metrics["tvTradesCsvHash"], slug
        assert hashlib.sha256(pine_bytes).hexdigest() == meta["pine_sha256"], slug
        assert metrics["wsProvenance"]["rangeProof"] == "covered", slug
        tape = read_tape(slug)
        assert len(tape) == metrics["trades"], slug
        assert all(flags["M"] and not flags.get("P") and not flags.get("Q")
                   for _, flags in tape), slug
        bars["hm-g236"] += len(tape)
    assert bars == {"hm-g236": 1138}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.key)
def test_session_flags_are_tradingviews(case: Case, replays) -> None:
    replay = replays[("current", case.slug)]
    misses = _misses(case, replay)
    wrong = {FLAGS[letter]: len(bars) for letter, bars in misses.items() if bars}
    assert not wrong, f"[{case.key}] bars where a flag is not TradingView's: {wrong}"
    # The probe entered on every bar TradingView held, at the same time.
    assert engine_entry_times(replay.trades[case.key]) == _replay_stamps(case.slug), case.key
    assert "session_ismarket_" in replay.cpp
    print(f"session flags {case.key}: {''.join(misses)} == TradingView on "
          f"{len(read_tape(case.slug))} bars")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.key)
def test_legacy_predicates_missed_the_pinned_bars(case: Case, replays) -> None:
    if ("legacy", case.slug) not in replays:
        pytest.skip(f"the pre-lane codegen ({LEGACY[:12]}) is not in this checkout's history")
    replay = replays[("legacy", case.slug)]
    assert "session_ismarket_" not in replay.cpp
    misses = _misses(case, replay)
    assert {letter: len(bars) for letter, bars in misses.items() if bars} == case.legacy, case.key
    assert _on_sunday(misses["M"], case.timezone) == case.legacy_sunday, case.key
    print(f"session flags {case.key}: pre-lane build missed "
          f"{ {FLAGS[k]: v for k, v in case.legacy.items()} } of "
          f"{len(read_tape(case.slug))} bars ({case.legacy_sunday} ismarket on a local Sunday)")


def test_security_payload_keeps_its_own_bars_predicates(replays) -> None:
    """A request.security payload reads the flag at the security bar's time,
    exactly as the pre-lane build did; the chart read beside it is
    TradingView's flag."""
    case = SECURITY_CASE
    current = replays[("security-current", case.slug)]
    for name in ("ismarket",):
        assert (f"pine_session_{name}(syminfo_.session, syminfo_.timezone, bar.timestamp)"
                in current.cpp), name
    assert not any(_misses(case, current).values())
    if ("security-legacy", case.slug) not in replays:
        pytest.skip(f"the pre-lane codegen ({LEGACY[:12]}) is not in this checkout's history")
    legacy = replays[("security-legacy", case.slug)]
    for name in ("ismarket",):
        payload = traced(current.traces[case.key], f"security_{name}")
        assert payload and payload == traced(legacy.traces[case.key], f"security_{name}"), name
    print(f"request.security session flags {case.key}: "
          f"{len(read_tape(case.slug)) + 1} bars == pre-lane build")


ONE_BAR_PROBE = ('//@version=6\nstrategy("session.ismarket one bar", overlay=true, '
                 "process_orders_on_close=true)\n"
                 'if session.ismarket\n    strategy.entry("L", strategy.long)\n' + TRACE)
MONDAY_OPEN = 1741017600000  # 2025-03-03 10:00 America/Chicago, inside 1700-1600


@pytest.fixture(scope="session")
def one_bar(tmp_path_factory) -> dict[str, tuple[bytes, list[dict]]]:
    """The probe on a single bar with no timeframe given, per build."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("session_ismarket_one_bar")
    builds = {"current": REPO_ROOT}
    legacy = reference_codegen(LEGACY)
    if legacy is not None:
        builds["legacy"] = legacy
    runs = {}
    for label, codegen in builds.items():
        work = base / label
        work.mkdir()
        feed = _write_feed([MONDAY_OPEN], work / "chart.csv")
        pine = work / "strategy.pine"
        pine.write_text(ONE_BAR_PROBE, encoding="utf-8")
        transpiled = transpile_json(pine, codegen)
        assert transpiled.get("ok"), transpiled.get("diagnostics")
        build_strategy_library(transpiled["cpp"], work)
        overrides = {"runtime_overrides": {"session": "1700-1600", "timezone": "America/Chicago"}}
        trades, records, _ = run_strategy(engine, work, feed, overrides, "one", trace=True)
        runs[label] = (trades, records or [])
    return runs


def test_one_bar_without_timeframe_keeps_the_predicates(one_bar) -> None:
    trades, records = one_bar["current"]
    assert traced(records, "ismarket") == [(MONDAY_OPEN, True)]
    assert engine_entry_times(trades) == [MONDAY_OPEN]
    if "legacy" not in one_bar:
        pytest.skip(f"the pre-lane codegen ({LEGACY[:12]}) is not in this checkout's history")
    legacy_trades, legacy_records = one_bar["legacy"]
    assert trades == legacy_trades
    assert traced(records, "ismarket") == traced(legacy_records, "ismarket")
    print("session.ismarket on one bar without a timeframe: in market, == pre-lane build")


def test_pine_names_of_the_host_members_read_stay_distinct(tmp_path: Path) -> None:
    """A script may name its own variables after the host members the lowering
    reads: they are renamed, so the TU still compiles, session.ismarket reads
    the host's fact rather than the script's variable, and the input keeps its
    Pine name as its key."""
    pine = tmp_path / "strategy.pine"
    pine.write_text('//@version=6\nstrategy("host member names", overlay=true)\n'
                    "session_ismarket_ = close > open\n"
                    "script_tf_ = input.int(2)\n"
                    "if session.ismarket and session_ismarket_ and script_tf_ > 0\n"
                    '    strategy.entry("L", strategy.long)\n', encoding="utf-8")
    result = transpile_json(pine)
    assert result["ok"], result["diagnostics"]
    assert [entry["title"] for entry in result["inputs"]] == ["script_tf_"]
    assert "bool session_ismarket_" not in result["cpp"]
    compile_cpp(result["cpp"], label="host member names")
