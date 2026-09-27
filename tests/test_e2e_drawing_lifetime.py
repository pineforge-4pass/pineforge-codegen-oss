"""A deleted or collected drawing is na, and TradingView's collection decides which.

``fixtures/drawing_lifetime`` holds TradingView's own tapes of synthetic
readout scripts on BINANCE:ETHUSDT.P 15 (see its README). Each readout is an
entry whose comment carries the values the script read on its bar. The rules
they pin:

1. A deleted drawing reads like a na handle: every getter returns na, every
   setter does nothing and ``na()`` is true.
2. A new line, box or label that makes its kind's live count reach
   ``max_<kind>_count + 6`` deletes the oldest drawings of that kind that no
   script variable holds, until ``max_<kind>_count`` remain (default 50).
   Linefills are never collected.
3. A ``var`` holds its drawing; a drawing held only by an array, an object
   field, a local or a plain non-var variable is collectable, as is one a
   ``var`` held before it was reassigned.

Each replayable tape runs end to end -- ``transpile_json``, the built runtime,
``run_strategy.py`` over the corpus 15m feed from 2025-04-01 00:00 UTC (where
TradingView's chart starts, so ``bar_index`` is the tape's) -- and every trade
must be the tape's. The values are read back through ``@pf-trace`` lines
appended to the source: trace ``w<i>`` is 1 on the bars of readout ``i`` and
``v<i>_<j>`` is value ``j`` there, evaluated only on those bars (as the
script's own comment is), so every entry of the tape is checked value by
value on its signal bar. The pre-lane build (``BASE``) is replayed beside it:
it reads a deleted or evicted drawing's data, evicts at exactly the cap and
halts on a getter of a na handle, and misses every tape whose drawings
TradingView deleted.
"""

from __future__ import annotations

import concurrent.futures
import csv
import datetime as dt
import hashlib
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests._e2e import (
    REPO_ROOT, build_strategy_library, derive_chart_feed, reference_codegen,
    skip_unless_e2e_env, transpile_json,
)


FIXTURES = Path(__file__).parent / "fixtures" / "drawing_lifetime"
# codegen before this lane (cg/tvdefaults d7e095f).
BASE = "d7e095fd7733303f8102c6f27d4c9ab12d631b8a"
QTY_STEP = 0.0001  # TradingView's BINANCE:ETHUSDT.P quantity step
TAPE_UTC_OFFSET_HOURS = 8
BAR_MS = 15 * 60 * 1000
# TradingView's chart of every tape: 2025-04-01 .. 2025-05-01.
WINDOW_MS = (1743465600000, 1746057600000)


@dataclass(frozen=True)
class Readout:
    when: str                # the script's own condition for the readout bars
    values: tuple[str, ...]  # one Pine expression per value of the comment


@dataclass(frozen=True)
class Case:
    tape: str
    readouts: tuple[Readout, ...]
    bits: bool = False            # the comment's value is one 0/1 digit per value
    fails_before: bool = True     # BASE misses it


def _top(handle: str) -> str:
    return f"box.get_top({handle})"


CASES = (
    Case("w8b-box-evict-readout", (
        Readout("time == t(1, 0) or time == t(1, 15) or time == t(1, 30)", (_top("a"),)),
        Readout("time == t(2, 15)", (_top("d"),)),
    )),
    Case("w8b-box-evict-readout2", (
        Readout("time == t(1, 30) or time == t(2, 15) or time == t(5, 15) or time == t(11, 15)",
                (_top("a"), _top("b"))),
    ), fails_before=False),
    Case("w8b-box-evict-readout3", (
        Readout("n == 99 or n == 100 or n == 101 or n == 150 or n == 300 or n == 600 or n == 1000",
                ("n", _top("a"))),
    ), fails_before=False),
    Case("w8b-box-evict-setter", (
        Readout("time == t(1, 30) or time == t(2, 30) or time == t(6, 30) or time == t(11, 0)",
                (_top("a"), _top("b"), _top("d"))),
    )),
    Case("w8b-box-evict-array", (
        Readout("time == t(1, 0) or time == t(1, 30) or time == t(3, 0) or time == t(11, 0)",
                (_top("array.get(zones, 0)"), _top("array.get(zones, 1)"), _top("a"))),
    )),
    Case("w8b-box-evict-timing", (
        Readout("time >= t(1, 0) and time <= t(3, 0)",
                (_top("a"), _top("array.get(zones, 0)"), _top("array.get(zones, 1)"))),
    )),
    Case("w8b-box-gc-cap100", (
        Readout("n >= 195 and n <= 225",
                ("n", *(_top(f"array.get(zones, {i})") for i in (0, 1, 99, 100, 101)),
                 _top("array.get(zones, array.size(zones) - 1)"))),
    )),
    Case("w8b-box-gc-steps", (
        Readout("n >= 1", ("n", "prevDead")),
    )),
    Case("w8b-box-delete-setter", (
        Readout("time == t(0, 15) or time == t(0, 45) or time == t(2, 30) or time == t(4, 0)",
                (_top("d"), "box.get_bottom(d)", _top("array.get(keep, 0)"),
                 "box.get_bottom(array.get(keep, 0))")),
    )),
    Case("w8b-line-delete-collect", (
        Readout("time == t(0, 15) or time == t(0, 45) or time == t(4, 0)",
                ("line.get_y1(d)", "line.get_y2(d)", "line.get_y1(f)", "line.get_y2(f)",
                 "line.get_y1(array.get(keep, 0))", "line.get_y2(array.get(keep, 0))")),
    )),
    Case("w8b-drawing-na-after-delete", (
        Readout("time == t(0, 0) or time == t(0, 15) or time == t(4, 0)",
                ("na(d) ? 1 : 0", "na(b) ? 1 : 0", "na(array.get(lines, 0)) ? 1 : 0",
                 "na(array.get(boxes, 0)) ? 1 : 0")),
    ), bits=True),
    Case("w9dg-pin-holders3", (
        Readout("time == t(1, 0) or time == t(1, 15) or time == t(3, 0)",
                (_top("u.bx"), _top("array.get(keep, 0)"), _top("f"), _top("array.get(keep, 1)"),
                 _top("array.get(w, 0).bx"), _top("array.get(keep, 2)"), _top("o"))),
    )),
    Case("w9dg-pin-locals", (
        Readout("time == t(1, 30) or time == t(1, 45) or time == t(3, 30)",
                tuple(_top(f"array.get(ka, {i})") for i in range(4))),
    )),
    Case("w9dg-label-collect2", (
        Readout("time >= t(2, 0) and time <= t(3, 0)",
                ("label.get_y(a)", "label.get_y(array.get(keep, 0))",
                 "label.get_y(array.get(keep, 1))",
                 "na(label.get_x(array.get(keep, 0))) ? na : float(label.get_x(array.get(keep, 0)))")),
    )),
    Case("w9dg-default-cap", (
        Readout("n >= 1", ("n", "prevDeadBoxes", "prevDeadFills")),
    )),
)
# Exported for the rules above but not replayable through PineForge today:
# map<int, box> is outside the supported map subset (w9dg-pin-holders); a
# non-var drawing whose history is read declares as Series<double> and does not
# compile (w9dg-pin-holders2); na() of a string does not compile
# (w9dg-label-collect). Their variants -holders3 / -label-collect2 replay.
EVIDENCE_ONLY = ("w9dg-pin-holders", "w9dg-pin-holders2", "w9dg-label-collect")
BUILDS = ("lane", "base")


def traced_source(case: Case) -> str:
    source = (FIXTURES / case.tape / "strategy.pine").read_text(encoding="utf-8")
    lines = [source.rstrip("\n")]
    for i, readout in enumerate(case.readouts):
        lines.append(f"// @pf-trace w{i}=({readout.when}) ? 1 : 0")
        for j, value in enumerate(readout.values):
            lines.append(f"// @pf-trace v{i}_{j}=({readout.when}) ? ({value}) : na")
    return "\n".join(lines) + "\n"


def comment_values(comment: str, bits: bool) -> list[float]:
    """The values of one readout comment: ``five:111``, ``111|NaN|333``,
    ``195:NaN|NaN|100|...``, ``deleted:1100`` (``bits``). A leading word is
    the readout's name."""
    tokens = re.split(r"[:|/]", comment)
    if not re.fullmatch(r"-?\d+(\.\d+)?|NaN", tokens[0]):
        tokens = tokens[1:]
    if bits:
        assert len(tokens) == 1, comment
        return [float(ch) for ch in tokens[0]]
    return [math.nan if token == "NaN" else float(token) for token in tokens]


def _utc_ms(stamp: str, offset_hours: int) -> int:
    local = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M")
    return int((local - dt.timedelta(hours=offset_hours))
               .replace(tzinfo=dt.timezone.utc).timestamp()) * 1000


def trades(path: Path, offset_hours: int) -> list[tuple]:
    """(entry ms, side, entry price, quantity, exit ms, exit price) per trade,
    in entry order, from TradingView's tape (UTC+8) or engine_trades.csv (UTC)."""
    with path.open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    by_number: dict[str, dict[str, tuple]] = {}
    for row in rows:
        number = row.get("Trade number") or row["Trade #"]
        price = next(value for key, value in row.items() if key.startswith("Price"))
        qty = row.get("Size (qty)") or row["Qty"]
        leg = "exit" if row["Type"].startswith("Exit") else "entry"
        by_number.setdefault(number, {})[leg] = (
            row["Type"].split()[1], _utc_ms(row["Date and time"], offset_hours),
            float(price), float(qty))
    out = []
    for legs in by_number.values():
        side, entry_ms, entry_price, qty = legs["entry"]
        _, exit_ms, exit_price, _ = legs["exit"]
        out.append((entry_ms, side, entry_price, qty, exit_ms, exit_price))
    return sorted(out)


def readout_entries(case: Case) -> list[tuple[int, str]]:
    """(signal bar ms, comment) of every entry of the tape: a market entry
    fills at the next bar's open."""
    with (FIXTURES / case.tape / "tv_trades.csv").open(encoding="utf-8-sig") as fh:
        rows = [row for row in csv.DictReader(fh) if row["Type"].startswith("Entry")]
    return sorted((_utc_ms(row["Date and time"], TAPE_UTC_OFFSET_HOURS) - BAR_MS, row["Signal"])
                  for row in rows)


def window_feed(chart_feed: Path, out: Path) -> Path:
    """The chart feed's bars of TradingView's window."""
    with chart_feed.open() as src, out.open("w") as dst:
        header = next(src)
        dst.write(header)
        for line in src:
            ms = int(line.split(",", 1)[0])
            if WINDOW_MS[0] <= ms < WINDOW_MS[1]:
                dst.write(line)
    return out


@dataclass
class Outcome:
    cpp: str | None = None
    engine: list[tuple] | None = None
    traces: dict[tuple[str, int], float] = field(default_factory=dict)
    error: str | None = None


def _replay(engine_root: Path, feed: Path, workdir: Path, case: Case, codegen: Path) -> Outcome:
    outcome = Outcome()
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        pine = workdir / "strategy.pine"
        pine.write_text(traced_source(case), encoding="utf-8")
        (workdir / "inputs.json").write_text(
            json.dumps({"runtime_overrides": {"qty_step": QTY_STEP}}))
        transpiled = transpile_json(pine, codegen)
        if not transpiled.get("ok"):
            raise RuntimeError(json.dumps(transpiled.get("diagnostics"), indent=1))
        outcome.cpp = transpiled["cpp"]
        build_strategy_library(outcome.cpp, workdir)
        out = workdir / "engine_trades.csv"
        trace = workdir / "trace.json"
        cmd = [sys.executable, str(engine_root / "scripts" / "run_strategy.py"), str(workdir),
               "--ohlcv", str(feed), "--no-trim-output", "-o", str(out),
               "--trace-json", str(trace)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(f"run_strategy failed:\n{proc.stdout}\n{proc.stderr}")
        outcome.engine = trades(out, 0)
        for record in json.loads(trace.read_text())["trace"]:
            outcome.traces[(record["name"], int(record["timestamp"]))] = record["value"]
    except Exception as exc:  # recorded and asserted by the case's own test
        outcome.error = str(exc)
    return outcome


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory) -> dict[tuple[str, str], Outcome]:
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("drawing_lifetime")
    feed = window_feed(derive_chart_feed(engine_root, base / "ohlcv_15m.csv"),
                       base / "ohlcv_15m_window.csv")
    trees = {"lane": REPO_ROOT, "base": reference_codegen(BASE)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {(case.tape, build): pool.submit(_replay, engine_root, feed,
                                                base / build / case.tape, case, trees[build])
                for case in CASES for build in BUILDS if trees[build] is not None}
        return {key: job.result() for key, job in jobs.items()}


def _outcome(outcomes: dict, tape: str, build: str) -> Outcome:
    if (tape, build) not in outcomes:
        pytest.skip(f"codegen {BASE} is not in this checkout's history")
    outcome = outcomes[(tape, build)]
    if outcome.error is not None:
        pytest.fail(f"[{build} {tape}] {outcome.error}", pytrace=False)
    return outcome


def _same(a: float, b: float) -> bool:
    return (math.isnan(a) and math.isnan(b)) or a == b


def mismatches(case: Case, outcome: Outcome) -> list[str]:
    """Every way the run differs from the tape: its trades, then each readout
    value on each entry's signal bar."""
    found = []
    tape = trades(FIXTURES / case.tape / "tv_trades.csv", TAPE_UTC_OFFSET_HOURS)
    if outcome.engine != tape:
        pairs = list(zip(outcome.engine or [], tape))
        first = next(((i, e, t) for i, (e, t) in enumerate(pairs) if e != t), None)
        found.append(f"trades: {len(outcome.engine or [])} vs the tape's {len(tape)}, "
                     f"first difference (index, engine, TradingView) {first}")
    for signal_ms, comment in readout_entries(case):
        active = [i for i in range(len(case.readouts))
                  if outcome.traces.get((f"w{i}", signal_ms)) == 1.0]
        if len(active) != 1:
            found.append(f"{comment} at {signal_ms}: readouts {active} active")
            continue
        i = active[0]
        want = comment_values(comment, case.bits)
        got = [outcome.traces.get((f"v{i}_{j}", signal_ms), math.nan)
               for j in range(len(case.readouts[i].values))]
        if len(want) != len(got) or not all(map(_same, got, want)):
            found.append(f"{comment} at {signal_ms}: engine read {got}")
    return found


@pytest.mark.parametrize("case", CASES, ids=[c.tape for c in CASES])
def test_every_readout_is_tradingviews(outcomes, case):
    found = mismatches(case, _outcome(outcomes, case.tape, "lane"))
    assert found == []


@pytest.mark.parametrize("case", [c for c in CASES if c.fails_before],
                         ids=[c.tape for c in CASES if c.fails_before])
def test_the_pre_lane_build_missed_it(outcomes, case):
    if (case.tape, "base") not in outcomes:
        pytest.skip(f"codegen {BASE} is not in this checkout's history")
    outcome = outcomes[(case.tape, "base")]
    assert outcome.error is not None or mismatches(case, outcome) != []


def test_every_drawing_script_has_the_lifetime_machinery(outcomes):
    cpp = _outcome(outcomes, "w9dg-pin-holders3", "lane").cpp
    # The arenas never evict; the box collector pins the var box and the
    # function's var, not the object held by a var, not the plain non-var p.
    assert "DrawingArena<BoxRec> _pf_boxes_{_PF_DRAWING_UNBOUNDED};" in cpp
    assert "const int32_t _pf_held[] = {_pf_new.id, this->o.id, this->fb.id};" in cpp
    assert "_pf_collect_drawings(this->_pf_boxes_, 5, _pf_held," in cpp


def test_the_tapes_are_the_recorded_exports():
    for tape in (*(case.tape for case in CASES), *EVIDENCE_ONLY):
        metrics = json.loads((FIXTURES / tape / "metrics.json").read_text())
        meta = json.loads((FIXTURES / tape / "meta.json").read_text())
        digest = hashlib.sha256((FIXTURES / tape / "tv_trades.csv").read_bytes()).hexdigest()
        pine = hashlib.sha256((FIXTURES / tape / "strategy.pine").read_bytes()).hexdigest()
        assert digest == metrics["tvTradesCsvHash"] == meta["tv_trades_sha256"], tape
        assert pine == meta["pine_sha256"], tape
        assert metrics["wsProvenance"]["rangeProof"] == "covered", tape
        assert (metrics["symbol"], metrics["interval"]) == ("BINANCE:ETHUSDT.P", "15"), tape
        assert (meta["chart"]["from"], meta["chart"]["to"]) == ("2025-04-01", "2025-05-01"), tape
