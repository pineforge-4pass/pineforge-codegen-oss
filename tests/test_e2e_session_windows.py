"""Pre- and post-market on a session with more than one window.

TradingView's every-flag tapes (lane CG-ISMARKET's probe) on three charts
whose session has two windows, 15-minute bars 2025-03-03 .. 03-15
(``fixtures/session_ismarket/cgs2-flags-*``): TSE:7203 (09:00-11:30 and
12:30-15:30 Asia/Tokyo), HKEX:700 (09:30-12:00 and 13:00-16:00
Asia/Hong_Kong) and CBOT:ZC1! (19:00-07:45 and 08:30-13:20 America/Chicago).
TradingView flags all 1,160 of their bars in market and none pre- or
post-market: no bar opens between the windows or after the close, and these
symbols have no extended session.

Replayed under those sessions, every flag PineForge computes is TradingView's
on every bar of HKEX:700 and CBOT:ZC1!. On TSE:7203 TradingView keeps the bar
that opens at 15:30 in the session, which the published 15:30 end leaves out:
the calendar puts it out of market and the post-market window holds it
(pinned), and it is the last bar of the chart's session day, as the engine
reads it; a session ending at 15:45 gives TradingView's flags on every bar.

The pre- and post-market windows are the engine's (``session_in_premarket``
and ``session_in_postmarket`` in src/session_time.cpp, which the emitted
``pine_session_ispremarket`` / ``pine_session_ispostmarket`` call; engine lane
K-SESSION-WINDOWS): 04:00 to the session day's first open and its last close
to 20:00, over every window in either order; a bar between two windows is
neither, and a session with an overnight window has neither. Codegen gates
them with the calendar's in-market answer. No TradingView chart above holds
an off-market bar, so the synthetic day below pins the rule as the tapes show
it where a chart holds such a bar: NASDAQ:AAPL's extended hours
(``cgim-flags-aapl-60-ext``) for one window.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests._e2e import build_strategy_library, run_strategy, skip_unless_e2e_env, transpile_json
from tests.test_e2e_session_ismarket import FIXTURES, FLAGS, TRACE, next_chart_bar, read_tape


TAPES = {"cgs2-flags-tse7203-15": ("Asia/Tokyo", 230),
         "cgs2-flags-hkex700-15": ("Asia/Hong_Kong", 220),
         "cgs2-flags-zc1-15": ("America/Chicago", 710)}
QUARTER = 15 * 60_000


@dataclass(frozen=True)
class Case:
    slug: str
    session: str
    # flag letter -> bars where PineForge's flag is not TradingView's
    pinned: dict[str, int] = field(default_factory=dict, hash=False)

    @property
    def key(self) -> str:
        return f"{self.slug}@{self.session}"


CASES = (
    Case("cgs2-flags-tse7203-15", "0900-1130,1230-1530", {"M": 10, "Q": 10}),
    Case("cgs2-flags-tse7203-15", "0900-1130,1230-1545"),
    Case("cgs2-flags-hkex700-15", "0930-1200,1300-1600"),
    Case("cgs2-flags-zc1-15", "1900-0745,0830-1320"),
)


def _write_feed(stamps: list[int], path: Path) -> Path:
    path.write_text("timestamp,open,high,low,close,volume\n"
                    + "".join(f"{ts},100,101,99,100.5,1\n" for ts in stamps))
    return path


def _run(engine: Path, work: Path, stamps: list[int], session: str, timezone: str) -> dict:
    """Flag name -> its trace on the bars ``stamps``, the every-flag probe
    under ``session`` and ``timezone``."""
    work.mkdir(parents=True, exist_ok=True)
    feed = _write_feed(stamps, work / "chart.csv")
    pine = work / "strategy.pine"
    if not pine.exists():
        pine.write_text((FIXTURES / "cgs2-flags-hkex700-15" / "strategy.pine")
                        .read_text(encoding="utf-8") + TRACE, encoding="utf-8")
        transpiled = transpile_json(pine)
        assert transpiled["ok"], transpiled["diagnostics"]
        build_strategy_library(transpiled["cpp"], work)
    overrides = {"input_tf": "15", "script_tf": "15",
                 "runtime_overrides": {"session": session, "timezone": timezone}}
    tag = hashlib.sha256(f"{session}@{timezone}".encode()).hexdigest()[:10]
    _, records, _ = run_strategy(engine, work, feed, overrides, tag, trace=True)
    return {name: [rec["value"] == 1.0 for rec in records if rec["name"] == name]
            for name in FLAGS.values()}


@pytest.fixture(scope="module")
def engine() -> Path:
    return skip_unless_e2e_env()


def test_tapes_are_the_recorded_exports() -> None:
    """Every fixture is its export byte for byte; TradingView flags every bar
    of the three charts in market and none pre- or post-market, and each
    session day's first and last bars open at the first window's open and in
    the last window."""
    first_last = {}
    for slug, (timezone, bars) in TAPES.items():
        metrics = json.loads((FIXTURES / slug / "metrics.json").read_text())
        assert hashlib.sha256((FIXTURES / slug / "tv_trades.csv").read_bytes()).hexdigest() \
            == metrics["tvTradesCsvHash"], slug
        assert hashlib.sha256((FIXTURES / slug / "strategy.pine").read_bytes()).hexdigest() \
            == metrics["sourceArtifactHash"], slug
        assert metrics["wsProvenance"]["rangeProof"] == "covered", slug
        tape = read_tape(slug)
        assert len(tape) == bars == metrics["trades"], slug
        assert all(flags["M"] and not flags["P"] and not flags["Q"] for _, flags in tape), slug
        zone = ZoneInfo(timezone)
        opens = {letter: {dt.datetime.fromtimestamp(ts / 1000, zone).strftime("%H:%M")
                          for ts, flags in tape if flags[letter]} for letter in "FL"}
        first_last[slug] = (opens["F"], opens["L"])
    assert first_last == {"cgs2-flags-tse7203-15": ({"09:00"}, {"15:30"}),
                          "cgs2-flags-hkex700-15": ({"09:30"}, {"15:45"}),
                          "cgs2-flags-zc1-15": ({"19:00"}, {"13:15"})}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.key)
def test_multi_window_charts_are_tradingviews(case: Case, engine: Path, tmp_path: Path) -> None:
    timezone, _ = TAPES[case.slug]
    tape = read_tape(case.slug)
    stamps = [ts for ts, _ in tape]
    traces = _run(engine, tmp_path / "run", stamps + [next_chart_bar(tape, QUARTER, timezone)],
                  case.session, timezone)
    misses = {}
    for letter, name in FLAGS.items():
        got = traces[name][:len(tape)]
        missed = sum(g != flags[letter] for g, (_, flags) in zip(got, tape))
        if missed:
            misses[letter] = missed
    assert misses == case.pinned, case.key
    print(f"session windows {case.key}: every flag == TradingView on {len(tape)} bars"
          + (f" but {case.pinned} (the 15:30 bar)" if case.pinned else ""))


def _day(timezone: str) -> list[int]:
    """Every 15-minute open of Tuesday 2025-03-04 .. Thursday 03-06 in
    ``timezone``: off-market bars between and after the windows included."""
    zone = ZoneInfo(timezone)
    return [int((dt.datetime.combine(dt.date(2025, 3, 4) + dt.timedelta(days=d), dt.time(0, 0),
                                     zone) + dt.timedelta(minutes=15 * q)).timestamp() * 1000)
            for d in range(3) for q in range(96)]


def _wednesday(timezone: str, stamps: list[int], traces: dict) -> dict[str, list[str]]:
    """``M``/``P``/``Q``/``PQ``/``-`` -> the Wednesday bar opens with those flags."""
    zone = ZoneInfo(timezone)
    rows: dict[str, list[str]] = {}
    for i, ts in enumerate(stamps):
        local = dt.datetime.fromtimestamp(ts / 1000, zone)
        if local.date() != dt.date(2025, 3, 5):
            continue
        key = "".join(letter for letter, name in (("M", "ismarket"), ("P", "ispremarket"),
                                                  ("Q", "ispostmarket")) if traces[name][i])
        rows.setdefault(key or "-", []).append(local.strftime("%H:%M"))
    return rows


def _span(opens: list[str]) -> tuple[int, str, str]:
    return len(opens), opens[0], opens[-1]


def test_pre_and_post_market_read_every_window(engine: Path, tmp_path: Path) -> None:
    """Pre-market is 04:00 to the session day's first open and post-market its
    last close to 20:00, whichever window the session names first; a bar in
    the break between two windows is neither, and so is every bar of a
    session with an overnight window (engine lane K-SESSION-WINDOWS F6, which
    the tapes above hold no off-market bar to test: every one of their bars is
    in market and neither pre- nor post-market)."""
    work = tmp_path / "run"
    hk = _day("Asia/Hong_Kong")
    in_order = _wednesday("Asia/Hong_Kong", hk, _run(engine, work, hk, "0930-1200,1300-1600",
                                                     "Asia/Hong_Kong"))
    reversed_ = _wednesday("Asia/Hong_Kong", hk, _run(engine, work, hk, "1300-1600,0930-1200",
                                                      "Asia/Hong_Kong"))
    assert in_order == reversed_
    assert _span(in_order["M"]) == (22, "09:30", "15:45")
    assert _span(in_order["P"]) == (22, "04:00", "09:15")
    assert _span(in_order["Q"]) == (16, "16:00", "19:45")
    lunch = ["12:00", "12:15", "12:30", "12:45"]
    assert set(lunch) <= set(in_order["-"]) and "PQ" not in in_order
    corn = _day("America/Chicago")
    overnight = _wednesday("America/Chicago", corn, _run(engine, work, corn, "1900-0745,0830-1320",
                                                         "America/Chicago"))
    day_first = _wednesday("America/Chicago", corn, _run(engine, work, corn, "0830-1320,1900-0745",
                                                         "America/Chicago"))
    assert overnight == day_first
    assert len(overnight["M"]) == 71 and set(overnight) == {"M", "-"}
    assert _span(overnight["-"]) == (25, "07:45", "18:45")
    print("session windows: pre-/post-market read every window: HKEX lunch "
          f"{lunch[0]}-{lunch[-1]} neither, either order; CBOT:ZC1! "
          f"{len(overnight['-'])} off-market bars neither")
