"""An omitted strategy() argument is TradingView's Pine v6 default, as its tapes prove.

TradingView changed three Pine v6 ``strategy()`` defaults around
2026-09-24T20:45Z: an omitted ``initial_capital`` is 100000, an omitted
``default_qty_type`` is ``strategy.percent_of_equity``, and an omitted
``default_qty_value`` is 100 whatever the quantity type. The source host's own
``PineStrategyConfig`` defaults are the old values (1000000, ``strategy.fixed``,
1), so the constructor declares the three for a script that omits them
(``PINE_V6_STRATEGY_DEFAULTS``). ``fixtures/strategy_defaults`` holds
TradingView's own tapes of seven synthetic probes on BINANCE:ETHUSDT.P 15 (see
its README): one per omitted parameter, all three omitted at once, and two
that declare every one of them.

Each tape is replayed end to end -- ``transpile_json``, the built runtime,
``run_strategy.py`` over the corpus 15m feed in the tape's own window (the
tape and its ``metrics.json`` sit in the run directory, as in the corpus), with
TradingView's BINANCE:ETHUSDT.P lot of 0.0001 as the ``qty_step`` -- and every
trade must be the tape's: entry and exit time, side, price and quantity. The
pre-lane build (``LEGACY``) is replayed beside it: it misses every tape that
omits one of the three, and a script that declares all three transpiles to the
same C++ on both builds.
"""

from __future__ import annotations

from tests._legacy_cpp import legacy_cpp
import concurrent.futures
import csv
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


FIXTURES = Path(__file__).parent / "fixtures" / "strategy_defaults"
# codegen main before this lane: the three parameters were the host's defaults.
LEGACY = "a4259656d025f36411a7ad1e4ab6c6dd020b780a"
# TradingView's BINANCE:ETHUSDT.P quantity step (every tape quantity is on it).
QTY_STEP = 0.0001
TAPE_UTC_OFFSET_HOURS = 8


@dataclass(frozen=True)
class Case:
    tape: str
    omits: tuple[str, ...]  # which of the three parameters the probe leaves out


CASES = (
    Case("tvd-all-omit-v6-eth15",
         ("initial_capital", "default_qty_type", "default_qty_value")),
    Case("tvd-cap-omit-v6-eth15", ("initial_capital",)),
    Case("tvd-qty-value1-v6-eth15", ("default_qty_type",)),
    Case("tvd-qty-typefixed-v6-eth15", ("default_qty_value",)),
    Case("tvd-qty-typecash-v6-eth15", ("default_qty_value",)),
    Case("tvd-qty-fixed1-v6-eth15", ()),
    Case("tvd-cap-x1m-v6-eth15", ()),
)
BUILDS = ("lane", "legacy")


def _utc_seconds(stamp: str, offset_hours: int) -> int:
    local = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M")
    return int((local - dt.timedelta(hours=offset_hours))
               .replace(tzinfo=dt.timezone.utc).timestamp())


def trades(path: Path, offset_hours: int) -> list[tuple]:
    """(entry time, side, entry price, quantity, exit time, exit price) per trade,
    in entry order. Reads TradingView's tape (``Trade number``, ``Price USDT``,
    ``Size (qty)``, UTC+8) and the engine's ``engine_trades.csv`` (``Trade #``,
    ``Price``, ``Qty``, UTC) alike."""
    with path.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    by_number: dict[str, dict[str, tuple]] = {}
    for row in rows:
        number = row.get("Trade number") or row["Trade #"]
        price = next(value for key, value in row.items() if key.startswith("Price"))
        qty = row.get("Size (qty)") or row["Qty"]
        leg = "exit" if row["Type"].startswith("Exit") else "entry"
        by_number.setdefault(number, {})[leg] = (
            row["Type"].split()[1], _utc_seconds(row["Date and time"], offset_hours),
            float(price), float(qty))
    out = []
    for legs in by_number.values():
        side, entry_time, entry_price, qty = legs["entry"]
        _, exit_time, exit_price, _ = legs["exit"]
        out.append((entry_time, side, entry_price, qty, exit_time, exit_price))
    return sorted(out)


def run_on_tape(engine_root: Path, workdir: Path, feed: Path) -> Path:
    """``run_strategy.py`` in the tape's window: the tape and its metrics.json are
    in ``workdir``, so the harness trades from the bar before TradingView's first
    entry and bounds the feed at the tape's range end, exactly as for a corpus
    probe."""
    out = workdir / "engine_trades.csv"
    cmd = [sys.executable, str(engine_root / "scripts" / "run_strategy.py"), str(workdir),
           "--ohlcv", str(feed), "-o", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"run_strategy failed in {workdir}:\n{proc.stdout}\n{proc.stderr}")
    return out


@dataclass
class Outcome:
    cpp: str | None = None
    engine: list[tuple] | None = None
    error: str | None = None


def _replay(engine_root: Path, feed: Path, workdir: Path, case: Case,
            codegen: Path) -> Outcome:
    outcome = Outcome()
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        for name in ("strategy.pine", "tv_trades.csv", "metrics.json"):
            shutil.copyfile(FIXTURES / case.tape / name, workdir / name)
        (workdir / "inputs.json").write_text(
            json.dumps({"runtime_overrides": {"qty_step": QTY_STEP}}))
        transpiled = transpile_json(workdir / "strategy.pine", codegen)
        if not transpiled.get("ok"):
            raise RuntimeError(json.dumps(transpiled.get("diagnostics"), indent=1))
        outcome.cpp = transpiled["cpp"]
        build_strategy_library(outcome.cpp, workdir)
        outcome.engine = trades(run_on_tape(engine_root, workdir, feed), 0)
    except Exception as exc:  # recorded and asserted by the case's own test
        outcome.error = str(exc)
    return outcome


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory) -> dict[tuple[str, str], Outcome]:
    """Every case under this checkout's codegen, and under ``LEGACY`` when that
    commit is in the checkout's history (its rows skip otherwise)."""
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("strategy_defaults")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    trees = {"lane": REPO_ROOT, "legacy": reference_codegen(LEGACY)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {(case.tape, build): pool.submit(_replay, engine_root, feed,
                                                base / build / case.tape, case, trees[build])
                for case in CASES for build in BUILDS if trees[build] is not None}
        return {key: job.result() for key, job in jobs.items()}


def _outcome(outcomes: dict, tape: str, build: str) -> Outcome:
    if (tape, build) not in outcomes:
        pytest.skip(f"codegen {LEGACY} is not in this checkout's history")
    outcome = outcomes[(tape, build)]
    if outcome.error is not None:
        pytest.fail(f"[{build} {tape}] {outcome.error}", pytrace=False)
    return outcome


def _tape(case: Case) -> list[tuple]:
    tape = trades(FIXTURES / case.tape / "tv_trades.csv", TAPE_UTC_OFFSET_HOURS)
    # Every closed trade of the export, as its metrics.json counts them.
    metrics = json.loads((FIXTURES / case.tape / "metrics.json").read_text())
    assert len(tape) == metrics["trades"] > 0, case.tape
    return tape


@pytest.mark.parametrize("case", CASES, ids=[c.tape for c in CASES])
def test_every_trade_is_tradingviews(outcomes, case):
    engine = _outcome(outcomes, case.tape, "lane").engine
    tape = _tape(case)
    assert len(engine) == len(tape)
    mismatches = [(i, e, t) for i, (e, t) in enumerate(zip(engine, tape)) if e != t]
    assert mismatches == [], f"first mismatch (index, engine, TradingView): {mismatches[0]}"


@pytest.mark.parametrize("case", [c for c in CASES if c.omits], ids=[c.tape for c in CASES if c.omits])
def test_the_pre_lane_build_missed_every_omitted_default(outcomes, case):
    engine = _outcome(outcomes, case.tape, "legacy").engine
    assert engine != _tape(case)
    # The legacy build left the omitted parameters to the host's own defaults.
    cpp = _outcome(outcomes, case.tape, "legacy").cpp
    lane = _outcome(outcomes, case.tape, "lane").cpp
    emitted = {
        "initial_capital": "cfg.initial_capital = 100000.0;",
        "default_qty_type": "cfg.default_qty_type = static_cast<int>(QtyType::PERCENT_OF_EQUITY);",
        "default_qty_value": "cfg.default_qty_value = 100.0;",
    }
    for key in case.omits:
        assert emitted[key] in lane
        assert emitted[key] not in cpp


@pytest.mark.parametrize("case", [c for c in CASES if not c.omits], ids=[c.tape for c in CASES if not c.omits])
def test_a_script_that_declares_every_default_is_unchanged(outcomes, case):
    lane = _outcome(outcomes, case.tape, "lane")
    legacy = _outcome(outcomes, case.tape, "legacy")
    assert legacy_cpp(lane.cpp) == legacy_cpp(legacy.cpp)
    assert lane.engine == legacy.engine == _tape(case)


def test_the_tapes_are_the_recorded_exports():
    for case in CASES:
        metrics = json.loads((FIXTURES / case.tape / "metrics.json").read_text())
        meta = json.loads((FIXTURES / case.tape / "meta.json").read_text())
        digest = hashlib.sha256((FIXTURES / case.tape / "tv_trades.csv").read_bytes()).hexdigest()
        assert digest == metrics["tvTradesCsvHash"] == meta["tv_trades_sha256"], case.tape
        assert metrics["wsProvenance"]["rangeProof"] == "covered", case.tape
        assert (metrics["symbol"], metrics["interval"]) == ("BINANCE:ETHUSDT.P", "15"), case.tape
