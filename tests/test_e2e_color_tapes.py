"""Colors read back as TradingView reads them, replayed end to end.

TradingView stores a color's transparency ``t`` as the alpha byte nearest
``255 * (100 - t) / 100`` and reads ``color.t`` back as
``100 - a * 100 / 255`` rounded, so a whole transparency reads back as itself
and a fractional one goes through the byte: ``color.new(c, 10.5)`` reads 11
and ``color.new(c, 20.5)`` 20. The engine's ``color.hpp`` computes both from
a double transparency since lane W11-ENG-TIME-COLOR, and the transpiler hands
it the transparency unrounded (``helpers.color_alpha_cast``); it used to
truncate one to an int, so ``color.new(c, 10.5)`` read 10.

The tape ``w11-color-v6-eth15`` (``fixtures/color_tv``, the engine's
``lab tv`` export) spells what a synthetic probe read in every exit comment on
BINANCE:ETHUSDT.P 15: the r,g,b,t of the seventeen named constants and
``color.t`` of ``color.new`` / ``color.rgb`` over whole and half
transparencies. The probe keys its readings on ``bar_index`` alone, so it
replays on flat bars stamped at the chart's bars, from the range's first bar
to the tape's last exit, and every trade carries the tape's entry id and exit
comment at the tape's times.

PineForge transpiles Pine v6 scripts only: the tape's ``//@version=5`` twin
``w11-color-v5-eth15`` is refused, so v5's color reads reach the transpiler
only as a v5 library's code, whose ``color.red``, ``color.teal`` and
``color.yellow`` are v5's (#FF5252, #00897B, #FFEB3B: ``library_v5``) and
every other constant the engine's, which v5 and v6 share. The twin's global
code calling the synthetic v5 library ``pftest/W11ColorV5/1`` that holds its
color reads (``fixtures/library_scripts/w11_color_v5_import.pine``) replays
the v5 tape the same way.
"""

from __future__ import annotations

import collections
import concurrent.futures
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from tests._e2e import (
    build_strategy_library, closed_trades, skip_unless_e2e_env, transpile_json,
)
from tests._pine_libraries import Layout


FIXTURES = Path(__file__).parent / "fixtures" / "color_tv"
# tape -> (the script replayed for it, the library it imports)
SOURCES = {
    "w11-color-v6-eth15": (FIXTURES / "w11-color-v6-eth15" / "strategy.pine", None),
    "w11-color-v5-eth15": (Path(__file__).parent / "fixtures" / "library_scripts"
                           / "w11_color_v5_import.pine", "pftest/W11ColorV5/1"),
}
TAPES = tuple(SOURCES)
QUARTER_HOUR_MS = 900_000


def _utc_ms(stamp: str) -> int:
    """A tape time (UTC+8, lab tv's rendering) in UTC ms."""
    local = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M")
    return int((local - dt.timedelta(hours=8)).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def read_tape(slug: str) -> list[tuple[int, int, str, str]]:
    """(entry time, exit time, entry id, exit comment) of every closed trade,
    in trade order; times in UTC ms."""
    trades: dict[int, dict[str, dict]] = {}
    with (FIXTURES / slug / "tv_trades.csv").open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            trades.setdefault(int(row["Trade number"]), {})[row["Type"].split()[0]] = row
    return [(_utc_ms(t["Entry"]["Date and time"]), _utc_ms(t["Exit"]["Date and time"]),
             t["Entry"]["Signal"], t["Exit"]["Signal"]) for _, t in sorted(trades.items())]


def chart_bars(slug: str) -> list[int]:
    """The chart's 15-minute bars from the range's first to the tape's last exit."""
    metrics = json.loads((FIXTURES / slug / "metrics.json").read_text())
    first = metrics["wsProvenance"]["returnedRange"]["from"]
    last = max(exit_ms for _, exit_ms, _, _ in read_tape(slug))
    return list(range(first, last + 1, QUARTER_HOUR_MS))


def _replay(engine: Path, work: Path, slug: str) -> list[dict]:
    source, library = SOURCES[slug]
    work.mkdir(parents=True)
    pine = work / "strategy.pine"
    pine.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    env = (None if library is None else
           Layout(work / "env").pin_all(slug, pine.read_text(encoding="utf-8"), library))
    transpiled = transpile_json(pine, extra_env=env)
    assert transpiled["ok"], transpiled["diagnostics"]
    build_strategy_library(transpiled["cpp"], work)
    feed = work / "chart.csv"
    feed.write_text("timestamp,open,high,low,close,volume\n"
                    + "".join(f"{ts},100,101,99,100.5,1\n" for ts in chart_bars(slug)))
    return closed_trades(engine, work, feed)


@pytest.fixture(scope="module")
def replays(tmp_path_factory) -> dict[str, list[dict]]:
    """Each tape's script, transpiled by this checkout and run on its chart's bars."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("color_tapes")
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(TAPES)) as pool:
        futures = {slug: pool.submit(_replay, engine, base / slug, slug) for slug in TAPES}
        return {slug: future.result() for slug, future in futures.items()}


def test_tapes_are_the_recorded_exports() -> None:
    """Every fixture is its export byte for byte; TradingView reads a half
    transparency through the alpha byte, and v5 and v6 read the same but for
    color.red, color.teal and color.yellow."""
    for slug in TAPES:
        metrics = json.loads((FIXTURES / slug / "metrics.json").read_text())
        tape_bytes = (FIXTURES / slug / "tv_trades.csv").read_bytes()
        pine_bytes = (FIXTURES / slug / "strategy.pine").read_bytes()
        assert hashlib.sha256(tape_bytes).hexdigest() == metrics["tvTradesCsvHash"], slug
        assert hashlib.sha256(pine_bytes).hexdigest() == metrics["sourceArtifactHash"], slug
        assert metrics["wsProvenance"]["rangeProof"] == "covered", slug
        assert len(read_tape(slug)) == metrics["trades"], slug
    # t<k>: color.t of color.new(color.red, k) and of color.new(color.red, k + 0.5), ...
    sweep = {comment.split(":")[0]: comment.split(":")[1].split(",")
             for _, _, _, comment in read_tape("w11-color-v6-eth15") if comment.startswith("t")}
    assert len(sweep) == 101
    assert sweep["t10"][:2] == ["10", "11"] and sweep["t20"][:2] == ["20", "20"]
    v6, v5 = ({comment for _, _, _, comment in read_tape(slug)}
              for slug in ("w11-color-v6-eth15", "w11-color-v5-eth15"))
    assert v6 - v5 == {"n2 242,54,69,0|178,181,190,0|8,153,129,0|255,255,255,0|253,216,53,0"}
    assert v5 - v6 == {"n2 255,82,82,0|178,181,190,0|0,137,123,0|255,255,255,0|255,235,59,0"}


@pytest.mark.parametrize("slug", TAPES)
def test_every_exit_reads_like_the_tape(slug: str, replays) -> None:
    tape = read_tape(slug)
    engine = [(t["entry_time"], t["exit_time"], t["entry_id"], t["exit_comment"])
              for t in replays[slug]]
    missing = collections.Counter(tape) - collections.Counter(engine)
    extra = collections.Counter(engine) - collections.Counter(tape)
    assert not missing and not extra, (
        f"[{slug}] {sum(missing.values())} of {len(tape)} tape trades differ; "
        f"tape {sorted(missing)[:3]}, engine {sorted(extra)[:3]}")
    print(f"colors {slug}: {len(tape)} exit comments == TradingView's")


def test_the_v5_twin_itself_is_refused() -> None:
    """Needs no engine: a //@version=5 strategy is refused before any
    lowering, so no v5 script's colors reach the C++."""
    result = transpile_json(FIXTURES / "w11-color-v5-eth15" / "strategy.pine")
    assert not result["ok"]
    (message,) = [d["message"] for d in result["diagnostics"]]
    assert message.startswith("PineForge supports PineScript v6 only (found //@version=5)."), message


def test_a_fractional_transparency_reaches_color_hpp_unrounded(tmp_path: Path) -> None:
    """Needs no engine: a transparency that is not an integer literal from 0
    to 100 reaches ``new_color`` as a double, an na one as 100; such a literal
    keeps its historic ``(int)`` spelling, which leaves it as it is."""
    pine = tmp_path / "strategy.pine"
    pine.write_text('//@version=6\nstrategy("alpha")\n'
                    "t = input.float(10.5)\n"
                    "a = color.t(color.new(color.red, t)) + color.t(color.new(color.red, 50))\n"
                    "b = color.t(color.rgb(1, 2, 3, t * 2)) + color.t(color.new(color.red, na))\n"
                    "c = color.t(color.new(color.red, 150))\n"
                    'if a + b + c > 0\n    strategy.entry("L", strategy.long)\n', encoding="utf-8")
    result = transpile_json(pine)
    assert result["ok"], result["diagnostics"]
    cpp = result["cpp"]
    assert cpp.count("is_na(_pf_color_v) ? 100 : (double)_pf_color_v") == 4
    assert cpp.count("is_na(_pf_color_v) ? 100 : (int)_pf_color_v") == 1
