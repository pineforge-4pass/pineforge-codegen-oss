"""Inside a user function, ``x[k]`` on a script variable reads its call site's history.

TradingView builds the history of a series used inside a function through
each successive call of it: ``gv[1]`` in ``f() => gv[1]`` is ``gv`` as the
previous call saw it, whether ``gv`` is ``var`` or not, and the same holds
for ``bar_index``. The chart built-ins (``close``, ``open``, ``time``, ``hl2``,
...) keep the chart's history. ``fixtures/function_global_history`` holds
TradingView's own tapes of three synthetic probes on BINANCE:ETHUSDT.P 15 (see
its README for what each trade encodes): the r4 witness of lane W8E-EXITS, its
r5 built-ins control, and this lane's model probe (``[2]``, two call sites, a
nested call, a loop, a global reassigned after the call, a function called on
every bar) and conditional probe (a call whose body skips the read).

Each tape is replayed end to end -- ``transpile_json``, the built runtime,
``run_strategy.py`` over the corpus 15m feed in the tape's own window (the
tape and its ``metrics.json`` sit in the run directory, as in the corpus), with
TradingView's BINANCE:ETHUSDT.P lot of 0.0001 as the ``qty_step`` -- and every
trade must be the tape's: entry and exit time, side, price and quantity. The
pre-lane build (``LEGACY``) is replayed beside it: it read the chart's history
and misses the r4, model and conditional tapes, and the built-ins control
transpiles to the same C++ on both builds.
"""

from __future__ import annotations

import concurrent.futures
import csv
import datetime as dt
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


FIXTURES = Path(__file__).parent / "fixtures" / "function_global_history"
# cg/tvdefaults, the lane's base: a function read the chart's history.
LEGACY = "d7e095fd7733303f8102c6f27d4c9ab12d631b8a"
# TradingView's BINANCE:ETHUSDT.P quantity step.
QTY_STEP = 0.0001
TAPE_UTC_OFFSET_HOURS = 8

PER_CALL_SITE = ("w8e-r4-fn-global-history", "w9-fn-history-model",
                 "w9-fn-history-conditional")
BUILTINS = "w8e-r5-fn-builtin-history"
TAPES = (*PER_CALL_SITE, BUILTINS)
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
    """``run_strategy.py`` in the tape's window, exactly as for a corpus probe."""
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


def _replay(engine_root: Path, feed: Path, workdir: Path, tape: str,
            codegen: Path) -> Outcome:
    outcome = Outcome()
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        for name in ("strategy.pine", "tv_trades.csv", "metrics.json"):
            shutil.copyfile(FIXTURES / tape / name, workdir / name)
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
    """Every tape under this checkout's codegen, and under ``LEGACY`` when that
    commit is in the checkout's history (its rows skip otherwise)."""
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("function_global_history")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    trees = {"lane": REPO_ROOT, "legacy": reference_codegen(LEGACY)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {(tape, build): pool.submit(_replay, engine_root, feed,
                                           base / build / tape, tape, trees[build])
                for tape in TAPES for build in BUILDS if trees[build] is not None}
        return {key: job.result() for key, job in jobs.items()}


def _outcome(outcomes: dict, tape: str, build: str) -> Outcome:
    if (tape, build) not in outcomes:
        pytest.skip(f"codegen {LEGACY} is not in this checkout's history")
    outcome = outcomes[(tape, build)]
    if outcome.error is not None:
        pytest.fail(f"[{build} {tape}] {outcome.error}", pytrace=False)
    return outcome


def _tape(tape: str) -> list[tuple]:
    rows = trades(FIXTURES / tape / "tv_trades.csv", TAPE_UTC_OFFSET_HOURS)
    # Every closed trade of the export, as its metrics.json counts them.
    metrics = json.loads((FIXTURES / tape / "metrics.json").read_text())
    assert len(rows) == metrics["trades"] > 0, tape
    return rows


@pytest.mark.parametrize("tape", TAPES)
def test_every_trade_is_tradingviews(outcomes, tape):
    engine = _outcome(outcomes, tape, "lane").engine
    expected = _tape(tape)
    assert len(engine) == len(expected)
    mismatches = [(i, e, t) for i, (e, t) in enumerate(zip(engine, expected)) if e != t]
    assert mismatches == [], f"first mismatch (index, engine, TradingView): {mismatches[0]}"


@pytest.mark.parametrize("tape", PER_CALL_SITE)
def test_the_pre_lane_build_read_the_charts_history(outcomes, tape):
    assert _outcome(outcomes, tape, "legacy").engine != _tape(tape)


def test_chart_builtins_keep_the_pre_lane_lowering(outcomes):
    assert (_outcome(outcomes, BUILTINS, "lane").cpp
            == _outcome(outcomes, BUILTINS, "legacy").cpp)
