"""TradingView's tapes of lane TAIL-F's constructs, replayed.

Two open library chains the population imports needed them:
``TradingView/Request/3`` (with ``TradingView/LibraryCOT/5``) writes a switch
arm on its ``=>`` line as a comma statement list (``=> runtime.error(...),
""``), overloads ``cryptoDerivativeMetric`` by its parameters' qualifiers
alone and holds a ``barmerge`` constant it passes as a request's gaps; the
script importing it declares ``k`` with two types in two blocks and draws a
Park-Miller generator whose products leave int32. ``thequantscience/
XGBoostMini/1`` passes ``matrix.row(X, i)`` to a method's array parameter.
Each rule is pinned by a synthetic probe's tape (``fixtures/tail_f_tv``,
lab tv --no-note), replayed here on the corpus ETH 15m feed from
TradingView's first bar, 2025-04-01 00:00 UTC: every TradingView trade's
entry, exit and exit comment. Two replays read the synthetic libraries
``pftest/ArmLine/1`` and ``pftest/Overloads/1`` through the verifier's path
(``transpile_json`` with the library environment of the script's own
requests manifest).
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._e2e import Build, closed_trades, derive_chart_feed, execute_all, ok, skip_unless_e2e_env
from tests._pine_libraries import Layout

FIXTURES = Path(__file__).parent / "fixtures" / "tail_f_tv"
SCRIPTS = Path(__file__).parent / "fixtures" / "library_scripts"
TAPE_TZ = ZoneInfo("Asia/Taipei")  # the exporting account's chart timezone
WINDOW_START_MS = int(dt.datetime(2025, 4, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
WINDOW_END_MS = int(dt.datetime(2025, 4, 3, tzinfo=dt.timezone.utc).timestamp() * 1000)

# build -> the tape its trades replay; the library builds replay the tape of
# the script that spells the same code in.
REPLAYS = {
    "arm_commas": "arm_commas",
    "arm_commas_decl": "arm_commas_decl",
    "arm_line_import": "arm_commas",
    "array_ref_fresh": "array_ref_fresh",
    "overloads_import": "overload_qualifiers",
    "barmerge_values": "barmerge_values",
    "block_local_types": "block_local_types",
    "int_product": "int_product",
}
LIBRARIES = {
    "arm_line_import": ("pftest/ArmLine/1",),
    "overloads_import": ("pftest/Overloads/1",),
}


def _tape(name: str) -> list[dict]:
    trades: dict[str, dict] = {}
    with (FIXTURES / f"{name}_tv_trades.csv").open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            side = "entry" if row["Type"].startswith("Entry") else "exit"
            when = dt.datetime.strptime(row["Date and time"], "%Y-%m-%d %H:%M")
            trades.setdefault(row["Trade number"], {})[side] = (
                int(when.replace(tzinfo=TAPE_TZ).timestamp() * 1000), row["Signal"])
    return list(trades.values())


def _source(name: str) -> str:
    path = (SCRIPTS if name in LIBRARIES else FIXTURES) / f"{name}.pine"
    return path.read_text(encoding="utf-8")


def _window_feed(engine: Path, base: Path) -> Path:
    full = derive_chart_feed(engine, base / "full_chart.csv")
    feed = base / "window.csv"
    with full.open() as inp, feed.open("w") as out:
        out.write(next(inp))
        for line in inp:
            ts = int(line.split(",", 1)[0])
            if WINDOW_START_MS <= ts < WINDOW_END_MS:
                out.write(line)
    return feed


@pytest.fixture(scope="module")
def tape_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("tail_f_tapes")
    feed = _window_feed(engine, base)
    layout = Layout(base / "env")
    builds = {}
    for name in REPLAYS:
        source = _source(name)
        env = layout.pin_all(name, source, *LIBRARIES[name]) if name in LIBRARIES else {}
        builds[name] = Build(source, env=tuple(env.items()))
    return engine, base, feed, execute_all(engine, feed, base, builds)


@pytest.mark.parametrize("name", list(REPLAYS))
def test_the_build_replays_the_tradingview_tape(tape_runs, name):
    engine, base, feed, runs = tape_runs
    ok(runs, name)
    by_entry = {t["entry_time"]: t for t in closed_trades(engine, base / name, feed)}
    tape = _tape(REPLAYS[name])
    assert len(tape) == 41
    misses = []
    for trade in tape:
        twin = by_entry.get(trade["entry"][0])
        got = None if twin is None else (twin["exit_time"], twin["exit_comment"])
        if got != trade["exit"]:
            misses.append(f"tape {trade['exit']}, engine {got}")
    assert not misses, misses[:3]


def test_a_returned_array_passed_on_is_refused():
    """TradingView passes the array a function returns as itself: the push
    in ``addTwo(getHeld(held2))`` reaches ``held2`` (``array_ref_args``'s
    tape). PineForge holds an array by value and would push to a copy."""
    source = (FIXTURES / "array_ref_args.pine").read_text(encoding="utf-8")
    assert _tape("array_ref_args")[0]["exit"][1].split("|")[-1] == "6"
    with pytest.raises(CompileError) as err:
        transpile(source)
    assert "an array a call returns is passed to parameter 'a' of 'addTwo'" in str(err.value)
    # Only that call: the same script without it transpiles.
    transpile((FIXTURES / "array_ref_fresh.pine").read_text(encoding="utf-8"))
