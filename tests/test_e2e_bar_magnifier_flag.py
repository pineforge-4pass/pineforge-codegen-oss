"""A script that declares ``use_bar_magnifier = true`` says so to its host.

TradingView runs a strategy that declares ``use_bar_magnifier = true`` on its
bar magnifier and one that does not on the chart bars alone. The generated
strategy library exports ``strategy_declares_bar_magnifier()`` (returning 1)
exactly when the script declares the magnifier; which feed and run parameters
realise it is the host's choice. ``fixtures/bar_magnifier`` holds two
TradingView tapes of one synthetic bracket script on BINANCE:ETHUSDT.P 15
(see its README): ``mi-fx-eth-15`` declares the magnifier, its twin
``w9mag-fx-eth-15-off`` does not, and TradingView's exits differ on 7 of their
24 trades.

The test is the host. It builds each script (``transpile_json``, the built
runtime), reads the export through ctypes and runs a declaring script on the
corpus 1m feed with ``input_tf = 1``, the 15m script timeframe and
``runtime_overrides.bar_magnifier``, any other on the 15m chart feed, both
over the tapes' window. Every trade must be the tape's. The pre-lane build
(``BASE``) exports nothing, so its host runs the declaring script unmagnified
and misses its tape; the twin passes on both builds with the same C++.
"""

from __future__ import annotations

import concurrent.futures
import csv
import ctypes
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._e2e import (
    REPO_ROOT, build_strategy_library, derive_chart_feed, reference_codegen,
    skip_unless_e2e_env, transpile_json,
)


FIXTURES = Path(__file__).parent / "fixtures" / "bar_magnifier"
# codegen before this lane: main at the CGINT3 integration base, which
# exports no magnifier declaration, as the lane base (cg/tvdefaults d7e095f).
BASE = "fdcdcbb908b9ea6bdbb135802a7e2aed6a7ec3de"
TAPES = {"mi-fx-eth-15": True, "w9mag-fx-eth-15-off": False}  # tape -> declares
BUILDS = ("lane", "base")
TAPE_UTC_OFFSET_HOURS = 8
# TradingView's chart of both tapes: 2026-01-29 .. 2026-02-01.
WINDOW_MS = (1769644800000, 1769904000000)
MINTICK = 0.01  # BINANCE:ETHUSDT.P; the bracket is +-400 ticks


def _window(src: Path, out: Path) -> Path:
    with src.open() as a, out.open("w") as b:
        b.write(next(a))
        for line in a:
            if WINDOW_MS[0] <= int(line.split(",", 1)[0]) < WINDOW_MS[1]:
                b.write(line)
    return out


def trades(path: Path, offset_hours: int) -> list[tuple]:
    """(entry time, entry price, exit time, exit price) per trade, in entry
    order, from TradingView's tape (UTC+8) or engine_trades.csv (UTC)."""
    with path.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    by_number: dict[str, dict[str, tuple]] = {}
    for row in rows:
        number = row.get("Trade number") or row["Trade #"]
        price = next(value for key, value in row.items() if key.startswith("Price"))
        local = dt.datetime.strptime(row["Date and time"], "%Y-%m-%d %H:%M")
        stamp = (local - dt.timedelta(hours=offset_hours)).strftime("%Y-%m-%d %H:%M")
        leg = "exit" if row["Type"].startswith("Exit") else "entry"
        by_number.setdefault(number, {})[leg] = (stamp, float(price))
    return sorted((*legs["entry"], *legs["exit"]) for legs in by_number.values())


@dataclass
class Outcome:
    cpp: str | None = None
    declares: bool | None = None
    engine: list[tuple] | None = None
    error: str | None = None


def _host(engine_root: Path, feeds: dict[str, Path], workdir: Path, tape: str,
          codegen: Path) -> Outcome:
    """Build the script, ask the library whether it declares the magnifier and
    run it the way the declaration says."""
    outcome = Outcome()
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / tape / "strategy.pine", workdir / "strategy.pine")
        transpiled = transpile_json(workdir / "strategy.pine", codegen)
        if not transpiled.get("ok"):
            raise RuntimeError(json.dumps(transpiled.get("diagnostics"), indent=1))
        outcome.cpp = transpiled["cpp"]
        build_strategy_library(outcome.cpp, workdir)
        library = ctypes.CDLL(str(workdir / "strategy.so"))
        export = getattr(library, "strategy_declares_bar_magnifier", None)
        if export is not None:
            export.restype = ctypes.c_int
        outcome.declares = export is not None and export() == 1
        if outcome.declares:
            inputs = {"input_tf": "1", "script_tf": "15",
                      "runtime_overrides": {"bar_magnifier": True, "mintick": MINTICK}}
            feed = feeds["1"]
        else:
            inputs = {"runtime_overrides": {"mintick": MINTICK}}
            feed = feeds["15"]
        (workdir / "inputs.json").write_text(json.dumps(inputs))
        out = workdir / "engine_trades.csv"
        cmd = [sys.executable, str(engine_root / "scripts" / "run_strategy.py"), str(workdir),
               "--ohlcv", str(feed), "--no-trim-output", "-o", str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(f"run_strategy failed:\n{proc.stdout}\n{proc.stderr}")
        outcome.engine = trades(out, 0)
    except Exception as exc:  # recorded and asserted by the case's own test
        outcome.error = str(exc)
    return outcome


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory) -> dict[tuple[str, str], Outcome]:
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("bar_magnifier")
    feeds = {
        "1": _window(engine_root / "corpus" / "data" / "ohlcv_ETH-USDT-USDT_1m.csv",
                     base / "ohlcv_1m.csv"),
        "15": _window(derive_chart_feed(engine_root, base / "ohlcv_15m_all.csv"),
                      base / "ohlcv_15m.csv"),
    }
    trees = {"lane": REPO_ROOT, "base": reference_codegen(BASE)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {(tape, build): pool.submit(_host, engine_root, feeds, base / build / tape,
                                           tape, trees[build])
                for tape in TAPES for build in BUILDS if trees[build] is not None}
        return {key: job.result() for key, job in jobs.items()}


def _outcome(outcomes: dict, tape: str, build: str) -> Outcome:
    if (tape, build) not in outcomes:
        pytest.skip(f"codegen {BASE} is not in this checkout's history")
    outcome = outcomes[(tape, build)]
    if outcome.error is not None:
        pytest.fail(f"[{build} {tape}] {outcome.error}", pytrace=False)
    return outcome


def _tape(tape: str) -> list[tuple]:
    rows = trades(FIXTURES / tape / "tv_trades.csv", TAPE_UTC_OFFSET_HOURS)
    metrics = json.loads((FIXTURES / tape / "metrics.json").read_text())
    assert len(rows) == metrics["trades"] > 0, tape
    return rows


@pytest.mark.parametrize("tape", TAPES)
def test_the_library_says_what_the_script_declares(outcomes, tape):
    assert _outcome(outcomes, tape, "lane").declares is TAPES[tape]


@pytest.mark.parametrize("tape", TAPES)
def test_every_trade_is_tradingviews(outcomes, tape):
    assert _outcome(outcomes, tape, "lane").engine == _tape(tape)


def test_the_pre_lane_build_ran_the_declaring_script_unmagnified(outcomes):
    outcome = _outcome(outcomes, "mi-fx-eth-15", "base")
    assert outcome.declares is False
    tape = _tape("mi-fx-eth-15")
    exits = [(e, t) for e, t in zip(outcome.engine, tape) if e != t]
    assert len(outcome.engine) == len(tape) and len(exits) == 7


def test_a_script_that_does_not_declare_it_is_unchanged(outcomes):
    lane = _outcome(outcomes, "w9mag-fx-eth-15-off", "lane")
    base = _outcome(outcomes, "w9mag-fx-eth-15-off", "base")
    assert lane.cpp == base.cpp
    assert "strategy_declares_bar_magnifier" not in lane.cpp
    assert lane.engine == base.engine == _tape("w9mag-fx-eth-15-off")


def test_the_tapes_are_the_recorded_exports():
    for tape in TAPES:
        metrics = json.loads((FIXTURES / tape / "metrics.json").read_text())
        meta = json.loads((FIXTURES / tape / "meta.json").read_text())
        digest = hashlib.sha256((FIXTURES / tape / "tv_trades.csv").read_bytes()).hexdigest()
        pine = hashlib.sha256((FIXTURES / tape / "strategy.pine").read_bytes()).hexdigest()
        assert digest == metrics["tvTradesCsvHash"] == meta["tv_trades_sha256"], tape
        assert pine == meta["pine_sha256"], tape
        assert metrics["wsProvenance"]["rangeProof"] == "covered", tape
        assert (metrics["symbol"], metrics["interval"]) == ("BINANCE:ETHUSDT.P", "15"), tape
        assert (meta["chart"]["from"], meta["chart"]["to"]) == ("2026-01-29", "2026-02-01"), tape
        declared = "use_bar_magnifier=true" in (FIXTURES / tape / "strategy.pine").read_text()
        assert declared is TAPES[tape], tape
