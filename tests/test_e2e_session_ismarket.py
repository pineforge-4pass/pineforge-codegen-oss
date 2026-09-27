"""The session.* flags are TradingView's, as its tapes prove.

TradingView's own session flags on every bar of eight 60-minute charts and
one daily chart (``fixtures/session_ismarket``): CME_MINI:ES1! across both
2025 US DST switches and the Thanksgiving week, OANDA:EURUSD, OANDA:XAUUSD and
BINANCE:ETHUSDT.P (the engine lane H-MEASURE's session.ismarket tapes
``hm-g236-*`` and this lane's every-flag tapes ``cgim-flags-*`` of the same
windows: all 1,138 bars in market, none in an extended session), NASDAQ:AAPL
with and without extended hours, and OANDA:XAUUSD 1D, whose bars TradingView
stamps at the 17:00 ET break and flags in market: a D/W/M bar holds whole
session days. Each probe reverses its position
at the close of every bar (``process_orders_on_close``), so each Entry row is
one chart bar, dated at its open, and its Signal carries the flags TradingView
evaluated there: ``M1`` in market, ``M0`` not, and so on (see the README).

TradingView flags a bar by its own open time, asked of the session day the
instant belongs to: the extended-hours 09:00 bar, which holds the 09:30 open,
is pre-market and the 16:00 bar post-market, while a ``:23456`` day mask names
trading dates, so an overnight session's Sunday-evening open is Monday's.
Codegen asks the engine's session calendar for the bar's open
(``codegen/session_market.py``); a bar in market is in neither extended
session, and off it the engine's windows decide: pre-market from 04:00 to the
session day's first open, post-market from its last close to 20:00, and
neither for an overnight or 24-hour session (engine lane K-SESSION-WINDOWS;
before it the windows also held an overnight session's in-market bars from
04:00 to its open and from its close to 20:00). The pre-lane lowering,
time-of-day predicates such as
``pine_session_ismarket(syminfo_.session, syminfo_.timezone, time)``, tests
each instant's own weekday and window: it reads every Sunday-evening open of a
``:23456`` session and every bar of ``0000-2400`` as out of market.
session.isfirstbar / islastbar read the kernel's session-day facts, which the
engine widens to the chart's day on an extended-hours chart, and their
``_regular`` twins the regular day's facts (see ``EXTENDED``).

Each tape is replayed end to end -- ``transpile_json``, the built runtime,
``run_strategy.py`` with the session and timezone as runtime overrides -- on
flat bars stamped at the tape's entry times, plus the bar TradingView's chart
holds next (``next_chart_bar``): the flags are a function of the bars' times,
the chart's timeframe and the symbol's session and timezone only, and a
batch's final bar closes its session day, which the tape's last bar does only
where TradingView's does. The
tape's own probe runs with a ``@pf-trace`` of every flag, under each session
spelling H-MEASURE measured: the campaign's lane facts, the same sessions with
TradingView's weekday mask, and a 24-hour day spelled ``0000-2400`` and
``0000-0000``. The pre-lane build (``LEGACY``) is replayed beside it and
misses exactly the pinned bars.

A ``request.security`` payload is evaluated on its own bars, whose timeframe
the chart's helper does not know: it keeps the time-of-day predicates at the
security bar's time, so its values are the pre-lane build's.
"""

from __future__ import annotations

import concurrent.futures
import csv
import datetime as dt
import hashlib
import json
import os
import re
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
# codegen main before this lane: the session.is* flags were time-of-day predicates.
LEGACY = "b0ed4967b3b5ab7aea80fea3edc783b16b04b16e"
CHARTS = ("es1-60-dst-mar", "es1-60-dst-nov", "es1-60-thanksgiving",
          "eurusd-60-dst-mar", "xauusd-60-dst-mar", "eth-60-24x7")
TAPES = tuple(f"{kind}-{chart}" for kind in ("hm-g236", "cgim-flags") for chart in CHARTS) + (
    "cgim-flags-aapl-60-ext", "cgim-flags-aapl-60-reg", "cgim-flags-xauusd-1d")
FLAGS = {"M": "ismarket", "P": "ispremarket", "Q": "ispostmarket",
         "F": "isfirstbar", "L": "islastbar",
         "f": "isfirstbar_regular", "l": "islastbar_regular"}
TRACE = "".join(f"\n// @pf-trace {name}=session.{name}" for name in FLAGS.values()) + "\n"
HOUR = 3_600_000
INTERVAL_MS = {"60": HOUR, "1D": 24 * HOUR}


@dataclass(frozen=True)
class Case:
    slug: str
    session: str
    timezone: str
    # flag letter -> bars where the pre-lane build was not TradingView's flag
    legacy: dict[str, int] = field(hash=False)
    legacy_sunday: int = 0  # of its session.ismarket misses, those on a local Sunday
    # flag letter -> bars where this build is not TradingView's (see EXTENDED)
    pinned: dict[str, int] = field(default_factory=dict, hash=False)

    @property
    def key(self) -> str:
        return f"{self.slug}@{self.session}"


def _cases(chart: str, session: str, timezone: str, legacy: dict[str, int],
           sunday: int = 0) -> list[Case]:
    return [Case(f"hm-g236-{chart}", session, timezone, legacy, sunday),
            Case(f"cgim-flags-{chart}", session, timezone, legacy, sunday)]


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
) for case in cases) + (
    # NASDAQ:AAPL's regular session, whose extended hours TradingView reads as
    # the 04:00-09:30 pre-market and 16:00-20:00 post-market windows.
    Case("cgim-flags-aapl-60-reg", "0930-1600", "America/New_York", {}),
    Case("cgim-flags-aapl-60-ext", "0930-1600", "America/New_York", {"f": 20, "l": 20}),
    # A daily bar stamped at the 17:00 ET break, with and without the mask.
    Case("cgim-flags-xauusd-1d", "1800-1700", "America/New_York", {}),
    Case("cgim-flags-xauusd-1d", "1800-1700:23456", "America/New_York", {}),
)
# On the extended-hours chart TradingView's isfirstbar / islastbar are the
# extended day's 04:00 and 19:00 bars and the _regular twins the regular
# day's 10:00 and 15:00 bars. The engine's session_isfirstbar_ /
# session_islastbar_ are the chart's day and its session_isfirstbar_regular_ /
# session_islastbar_regular_ the regular day (lane K-SESSION-WINDOWS), which
# codegen reads for the _regular spellings; the pre-lane build read the
# chart's pair for them, so its isfirstbar_regular and islastbar_regular land
# on 04:00 and 19:00.
EXTENDED = "cgim-flags-aapl-60-ext"
SECURITY_CASE = next(case for case in CASES if case.key == "cgim-flags-es1-60-dst-mar@1700-1600:23456")
SECURITY_TRACE = "".join(
    f'\nsecurity_{name} = request.security(syminfo.tickerid, "60", session.{name})'
    f"\n// @pf-trace security_{name}=security_{name}"
    for name in ("ismarket", "ispremarket", "ispostmarket")) + "\n"


def _utc_ms(stamp: str, offset_hours: int) -> int:
    local = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M")
    return int((local - dt.timedelta(hours=offset_hours))
               .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def read_tape(slug: str) -> list[tuple[int, dict[str, bool]]]:
    """(the chart bar's open in UTC ms, TradingView's flags by letter) per
    Entry row, in time order. ``lab tv`` renders times at UTC+8; a Signal is a
    letter and a digit per flag (``M1``, ``M1P0Q0F1L0f1l0``)."""
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


def next_chart_bar(tape: list[tuple[int, dict[str, bool]]], interval_ms: int,
                   timezone: str) -> int:
    """The bar TradingView's chart holds after the tape's last one. A batch's
    final bar closes its session day, so a replay ends on this bar, past the
    tape: each tape bar's islastbar then turns on the bar after it, as on
    TradingView's chart. It is one interval on while the last bar's day goes
    on (``L0``, or a tape without the flag, whose every-flag twin ends
    ``L0``), else the next session day's first bar -- the bar that followed
    the same weekday and time one week before, one week on, in ``timezone``'s
    wall-clock time (a DST switch keeps the hour). One interval on can be a
    post-market bar the chart does not hold, which widens the day the
    engine's plain islastbar closes."""
    last, flags = tape[-1]
    if not flags.get("L", False):
        return last + interval_ms
    zone = ZoneInfo(timezone)

    def shift(ts: int, days: int) -> int:
        local = dt.datetime.fromtimestamp(ts / 1000, zone).replace(tzinfo=None)
        return int((local + dt.timedelta(days=days)).replace(tzinfo=zone).timestamp() * 1000)

    stamps = [ts for ts, _ in tape]
    week_before = shift(last, -7)
    assert week_before in stamps, "the tape holds no bar one week before its last"
    return shift(stamps[stamps.index(week_before) + 1], 7)


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


def _interval(slug: str) -> str:
    return json.loads((FIXTURES / slug / "metrics.json").read_text())["interval"]


def _replay_stamps(slug: str) -> list[int]:
    tape = read_tape(slug)
    timezone = next(case.timezone for case in CASES if case.slug == slug)
    return [ts for ts, _ in tape] + [next_chart_bar(tape, INTERVAL_MS[_interval(slug)], timezone)]


def _overrides(case: Case) -> dict:
    tf = _interval(case.slug)
    return {"input_tf": tf, "script_tf": tf,
            "runtime_overrides": {"session": case.session, "timezone": case.timezone}}


@dataclass
class Replay:
    cpp: str
    diagnostics: list[dict]
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
    replay = Replay(cpp=transpiled["cpp"], diagnostics=transpiled.get("diagnostics", []),
                    trades={}, traces={})
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
    """Every fixture is its export byte for byte. TradingView flagged every one
    of each six-chart set's 1,138 bars in market and none pre- or post-market,
    and the extended-hours AAPL bars by their own open time. A session.ismarket
    tape holds its every-flag twin's bars, whose last bar's session day goes on
    (``next_chart_bar``)."""
    bars = {"hm-g236": 0, "cgim-flags": 0}
    for slug in TAPES:
        metrics = json.loads((FIXTURES / slug / "metrics.json").read_text())
        tape_bytes = (FIXTURES / slug / "tv_trades.csv").read_bytes()
        pine_bytes = (FIXTURES / slug / "strategy.pine").read_bytes()
        assert hashlib.sha256(tape_bytes).hexdigest() == metrics["tvTradesCsvHash"], slug
        assert hashlib.sha256(pine_bytes).hexdigest() == metrics["sourceArtifactHash"], slug
        assert metrics["wsProvenance"]["rangeProof"] == "covered", slug
        tape = read_tape(slug)
        assert len(tape) == metrics["trades"], slug
        if slug.startswith("hm-g236-"):
            twin = read_tape(slug.replace("hm-g236-", "cgim-flags-", 1))
            assert [ts for ts, _ in tape] == [ts for ts, _ in twin], slug
            assert not twin[-1][1]["L"], slug
        if "-aapl-" in slug or slug.endswith("-1d"):
            continue
        assert all(flags["M"] and not flags.get("P") and not flags.get("Q")
                   for _, flags in tape), slug
        bars["hm-g236" if slug.startswith("hm-g236-") else "cgim-flags"] += len(tape)
    assert bars == {"hm-g236": 1138, "cgim-flags": 1138}
    zone = ZoneInfo("America/New_York")
    by_hour: dict[str, set[str]] = {}
    for ts, flags in read_tape(EXTENDED):
        hour = dt.datetime.fromtimestamp(ts / 1000, zone).strftime("%H:%M")
        by_hour.setdefault(hour, set()).add("".join(f"{k}{int(v)}" for k, v in flags.items()))
    assert by_hour["09:00"] == {"M0P1Q0F0L0f0l0"}   # holds the 09:30 open
    assert by_hour["10:00"] == {"M1P0Q0F0L0f1l0"}
    assert by_hour["15:00"] == {"M1P0Q0F0L0f0l1"}
    assert by_hour["16:00"] == {"M0P0Q1F0L0f0l0"}
    daily = read_tape("cgim-flags-xauusd-1d")
    assert {dt.datetime.fromtimestamp(ts / 1000, zone).strftime("%H:%M") for ts, _ in daily} == {"17:00"}
    assert all(all(flags[k] for k in "MFLfl") and not flags["P"] and not flags["Q"]
               for _, flags in daily)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.key)
def test_session_flags_are_tradingviews(case: Case, replays) -> None:
    replay = replays[("current", case.slug)]
    misses = _misses(case, replay)
    wrong = {FLAGS[letter]: len(bars) for letter, bars in misses.items()
             if len(bars) != case.pinned.get(letter, 0)}
    assert not wrong, f"[{case.key}] bars where a flag is not TradingView's: {wrong}"
    # The probe entered on every bar TradingView held, at the same time.
    assert engine_entry_times(replay.trades[case.key]) == _replay_stamps(case.slug), case.key
    assert "_pf_session_market_(" in replay.cpp
    assert ("session_isfirstbar_regular_" in replay.cpp
            and "session_islastbar_regular_" in replay.cpp), case.key
    exact = "".join(letter for letter in misses if letter not in case.pinned)
    pinned = {FLAGS[k]: v for k, v in case.pinned.items()}
    print(f"session flags {case.key}: {exact} == TradingView on {len(read_tape(case.slug))} bars"
          + (f"; off as pinned: {pinned}" if pinned else ""))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.key)
def test_legacy_predicates_missed_the_pinned_bars(case: Case, replays) -> None:
    if ("legacy", case.slug) not in replays:
        pytest.skip(f"the pre-lane codegen ({LEGACY[:12]}) is not in this checkout's history")
    replay = replays[("legacy", case.slug)]
    assert "_pf_session_market_(" not in replay.cpp
    misses = _misses(case, replay)
    assert {letter: len(bars) for letter, bars in misses.items() if bars} == case.legacy, case.key
    assert _on_sunday(misses["M"], case.timezone) == case.legacy_sunday, case.key
    missed = {FLAGS[k]: v for k, v in case.legacy.items()}
    print(f"session flags {case.key}: pre-lane build missed {missed} of "
          f"{len(read_tape(case.slug))} bars ({case.legacy_sunday} ismarket on a local Sunday)")


def test_security_payload_keeps_its_own_bars_predicates(replays) -> None:
    """A request.security payload reads the flags at the security bar's time,
    exactly as the pre-lane build did, and each read warns that it can differ
    from TradingView; the chart reads beside it are TradingView's flags."""
    case = SECURITY_CASE
    current = replays[("security-current", case.slug)]
    for name in ("ismarket", "ispremarket", "ispostmarket"):
        assert (f"pine_session_{name}(syminfo_.session, syminfo_.timezone, bar.timestamp)"
                in current.cpp), name
    warned = sorted(d["message"].split()[0] for d in current.diagnostics
                    if d["severity"] == "warning" and "inside request.security" in d["message"])
    assert warned == ["session.ismarket", "session.ispostmarket", "session.ispremarket"]
    assert not any(_misses(case, current).values())
    if ("security-legacy", case.slug) not in replays:
        pytest.skip(f"the pre-lane codegen ({LEGACY[:12]}) is not in this checkout's history")
    legacy = replays[("security-legacy", case.slug)]
    for name in ("ismarket", "ispremarket", "ispostmarket"):
        payload = traced(current.traces[case.key], f"security_{name}")
        assert payload and payload == traced(legacy.traces[case.key], f"security_{name}"), name
    print(f"request.security session flags {case.key}: "
          f"{len(read_tape(case.slug)) + 1} bars == pre-lane build")


ONE_BAR_PROBE = ('//@version=6\nstrategy("session.ismarket one bar", overlay=true, '
                 "process_orders_on_close=true)\n"
                 'if session.ismarket\n    strategy.entry("L", strategy.long)\n' + TRACE)
SUNDAY_OPEN = 1740956400000  # 2025-03-02 17:00 America/Chicago: Monday's session day opens


@pytest.fixture(scope="session")
def one_bar(tmp_path_factory) -> dict[str, tuple[bytes, list[dict]]]:
    """The probe on a single bar given no timeframe, per build: the engine
    detects none and presents no session-day facts for such a run."""
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
        feed = _write_feed([SUNDAY_OPEN], work / "chart.csv")
        pine = work / "strategy.pine"
        pine.write_text(ONE_BAR_PROBE, encoding="utf-8")
        transpiled = transpile_json(pine, codegen)
        assert transpiled.get("ok"), transpiled.get("diagnostics")
        build_strategy_library(transpiled["cpp"], work)
        overrides = {"runtime_overrides": {"session": "1700-1600:23456",
                                           "timezone": "America/Chicago"}}
        trades, records, _ = run_strategy(engine, work, feed, overrides, "one", trace=True)
        runs[label] = (trades, records or [])
    return runs


def test_one_bar_without_timeframe_reads_the_calendar(one_bar) -> None:
    """The calendar needs no timeframe: the Sunday-evening open of a ":23456"
    session is in market on a one-bar run too (the ES1! tapes' first bar)."""
    trades, records = one_bar["current"]
    assert traced(records, "ismarket") == [(SUNDAY_OPEN, True)]
    assert engine_entry_times(trades) == [SUNDAY_OPEN]
    if "legacy" not in one_bar:
        pytest.skip(f"the pre-lane codegen ({LEGACY[:12]}) is not in this checkout's history")
    legacy_trades, legacy_records = one_bar["legacy"]
    assert traced(legacy_records, "ismarket") == [(SUNDAY_OPEN, False)]
    assert engine_entry_times(legacy_trades) == []
    print("session.ismarket on one Sunday-open bar without a timeframe: in market "
          "(pre-lane build: out)")


def test_pine_names_of_the_emitted_helpers_stay_distinct(tmp_path: Path) -> None:
    """A script may name its own variables after the member the lowering calls
    and the host member it reads: they are renamed, so the TU still compiles
    and the input keeps its Pine name as its key."""
    pine = tmp_path / "strategy.pine"
    pine.write_text('//@version=6\nstrategy("emitted names", overlay=true)\n'
                    "_pf_session_market_ = close > open\n"
                    "script_tf_ = input.int(2)\n"
                    "if session.ismarket and _pf_session_market_ and script_tf_ > 0\n"
                    '    strategy.entry("L", strategy.long)\n', encoding="utf-8")
    result = transpile_json(pine)
    assert result["ok"], result["diagnostics"]
    assert [entry["title"] for entry in result["inputs"]] == ["script_tf_"]
    compile_cpp(result["cpp"], label="emitted names")


def _transpiled(tmp_path: Path, body: str) -> dict:
    pine = tmp_path / "strategy.pine"
    pine.write_text('//@version=6\nstrategy("session emission", overlay=true)\n' + body,
                    encoding="utf-8")
    result = transpile_json(pine)
    assert result["ok"], result["diagnostics"]
    return result


def test_session_market_is_emitted_once_when_read(tmp_path: Path) -> None:
    """Needs no engine: the calendar type precedes the strategy class once, the
    strategy holds one cache outside its checkpointed script state, and a
    script that reads no chart session flag gets neither."""
    cpp = _transpiled(tmp_path, "f() => session.ispremarket\n"
                                "if session.ismarket or f()\n"
                                '    strategy.entry("L", strategy.long)\n')["cpp"]
    assert cpp.count("struct _PFSessionMarket {") == 1
    assert cpp.index("struct _PFSessionMarket {") < cpp.index("class GeneratedStrategy")
    assert cpp.count("mutable _PFSessionMarket _pf_session_market_;") == 1
    assert cpp.index("class GeneratedStrategy") < cpp.index("_pf_session_market_;")
    assert not any("_pf_session_market_" in line and "_pf_script_state_checkpoint_" in line
                   for line in cpp.splitlines())
    assert cpp.count("_pf_session_market_(syminfo_.session, syminfo_.timezone, "
                     "script_tf_, current_bar_.timestamp)") == 2
    plain = _transpiled(tmp_path, 'if session.isfirstbar\n    strategy.entry("L", strategy.long)\n')
    assert "_PFSessionMarket" not in plain["cpp"]


def test_regular_flags_read_the_regular_day(tmp_path: Path) -> None:
    """Needs no engine: session.isfirstbar_regular / islastbar_regular read
    the host's regular-day facts and session.isfirstbar / islastbar the
    chart day's, on the bar and at an offset (the flag's Series takes the
    same member)."""
    cpp = _transpiled(tmp_path, "a = session.isfirstbar_regular and not session.islastbar_regular[1]\n"
                                "b = session.isfirstbar or session.islastbar\n"
                                "if a or b\n"
                                '    strategy.entry("L", strategy.long)\n')["cpp"]

    def reads(member: str) -> int:
        return len(re.findall(rf"(?<![\w.]){member}(?!\w)", cpp))

    assert reads("session_isfirstbar_regular_") == 1
    assert reads("session_isfirstbar_") == 1 and reads("session_islastbar_") == 1
    assert "_pf_session_hist_islastbar_regular.update(session_islastbar_regular_)" in cpp


def test_security_payload_session_read_warns_once_per_site(tmp_path: Path) -> None:
    """Needs no engine: a payload keeps the time-of-day predicate at the
    security bar's time and warns once for each session.* read, however many
    payloads reach it."""
    result = _transpiled(tmp_path,
                         "f() => session.ismarket\n"
                         'a = request.security(syminfo.tickerid, "240", f())\n'
                         'b = request.security(syminfo.tickerid, "D", f())\n'
                         "if a and b\n"
                         '    strategy.entry("L", strategy.long)\n')
    assert result["cpp"].count(
        "pine_session_ismarket(syminfo_.session, syminfo_.timezone, bar.timestamp)") == 2
    warned = [(d["line"], d["col"]) for d in result["diagnostics"]
              if "inside request.security" in d["message"]]
    assert warned == [(3, 16)]  # the `ismarket` of f's body, once for both payloads

