"""Closed-trade folds preserve every binary64 value and avoid repeated prefixes.

The plain twin adds a final ``continue`` to each loop body. It executes the
same statements in the same order, while falling outside the fold grammar.
Both twins pass through the JSON glue and the shared native build recipe.
"""

from __future__ import annotations

import ctypes
import importlib.util
import re
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._e2e import build_strategy_library, skip_unless_e2e_env, transpile_json


FIXTURES = Path(__file__).parent / "fixtures" / "closed_trade_fold"
FOLD_MARKER = "// closed-trade fold:"
BODY_END = "        // fold-body-end\n"
START_MS = 1735689600000  # 2025-01-01 00:00 UTC, on a two-minute boundary.


@pytest.fixture(scope="module")
def native_runner():
    """Native coverage: skipped cleanly without the paired engine, like every e2e test."""
    root = skip_unless_e2e_env()
    runner = root / "scripts" / "run_strategy.py"
    assert runner.is_file(), runner
    scripts = str(runner.parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location("_pf_closed_trade_fold_runner", runner)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bits(value: float) -> int:
    return struct.unpack("=Q", struct.pack("=d", value))[0]


def _source(name: str, coof: bool = False) -> str:
    source = (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")
    if name != "performance":
        assert source.count("calc_on_order_fills = false") == 1
        if coof:
            source = source.replace("calc_on_order_fills = false",
                                    "calc_on_order_fills = true")
    return source


def _plain(source: str, folds: int) -> str:
    assert source.count(BODY_END) == folds
    return source.replace(BODY_END, "        continue\n" + BODY_END)


def _write_feed(path: Path, bars: int, *, kind: str = "ordinary",
                native_per_bar: int = 1) -> Path:
    # Each six-trade cycle yields +1.25, +1.25, -1.25, -1.25, 0, 0.
    closes = (101.25, 98.75, 98.75, 101.25, 100.0, 100.0)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("timestamp,open,high,low,close,volume\n")
        for bar in range(bars):
            high, low, close = 104.0, 96.0, 100.0
            if bar % 4 == 2:
                close = closes[(bar // 4) % 6]
            if kind == "flat":
                high = low = close = 100.0
            elif kind == "exit_key_change" and bar < 8:
                high = 104.0 if bar == 2 else 100.0
                low = close = 100.0
            elif kind == "margin" and bar >= 2400:
                high, low, close = 100.0, 100.0, 100.0
                if bar == 2401:
                    low = close = 20.0
            for part in range(native_per_bar):
                timestamp = START_MS + (bar * native_per_bar + part) * 60000
                fh.write(f"{timestamp},100.0,{high},{low},{close},1000.0\n")
    return path


@dataclass
class Compiled:
    strategy: object
    cpp: str


def _build(native_runner, source: str, directory: Path, *, fast: bool,
           folds: int = 1, verify: bool = True, count_steps: bool = False) -> Compiled:
    directory.mkdir()
    pine = directory / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    result = transpile_json(pine)
    assert result.get("ok"), result.get("diagnostics")
    cpp = result["cpp"]
    # A differential must exercise one optimized twin and one ordinary loop.
    assert cpp.count(FOLD_MARKER) == (folds if fast else 0)
    if fast and verify:
        assert "PINEFORGE_CLOSED_TRADE_FOLD_VERIFY" in cpp
        compiled_cpp = "#define PINEFORGE_CLOSED_TRADE_FOLD_VERIFY\n" + cpp
    else:
        compiled_cpp = "#undef PINEFORGE_CLOSED_TRADE_FOLD_VERIFY\n" + cpp
    if count_steps:
        compiled_cpp = _instrument_steps(compiled_cpp)
    build_strategy_library(compiled_cpp, directory)
    return Compiled(native_runner.Strategy(directory / "strategy.so"), cpp)


def _run(compiled: Compiled, feed: Path, **kwargs) -> dict:
    report = compiled.strategy.run(
        feed, trace_enabled=True, syminfo_mintick=0.01, syminfo_pointvalue=1.0,
        **kwargs)
    try:
        return {"trades": list(report["trades"]), "trace": list(report["trace"])}
    finally:
        close = getattr(report, "close", None)
        if close is not None:
            close()


def _trace(report: dict, name: str) -> list[dict]:
    records = [row for row in report["trace"] if row["name"] == name]
    assert records, f"no trace for {name}"
    return records


def _at(report: dict, name: str, bar: int) -> float:
    values = [row["value"] for row in _trace(report, name) if row["bar_index"] == bar]
    assert values, (name, bar)
    return values[-1]


def _last(report: dict, name: str) -> float:
    return _trace(report, name)[-1]["value"]


def _assert_same(fast: dict, plain: dict, source: str, bars: int) -> None:
    expected_names = set(re.findall(r"^// @pf-trace (\w+)=", source, re.MULTILINE))
    assert expected_names
    for report in (fast, plain):
        assert {row["name"] for row in report["trace"]} == expected_names
        for name in expected_names:
            # Every declared series must cover all chart bars, including zero trades.
            assert {row["bar_index"] for row in _trace(report, name)} == set(range(bars))
        assert not any(trade.get("open_at_end") for trade in report["trades"])
    assert len(fast["trace"]) == len(plain["trace"])
    for index, (actual, expected) in enumerate(zip(fast["trace"], plain["trace"])):
        assert (actual["name"], actual["bar_index"], actual["timestamp"]) == (
            expected["name"], expected["bar_index"], expected["timestamp"]), index
        assert _bits(actual["value"]) == _bits(expected["value"]), (
            actual["name"], actual["bar_index"],
            f'{_bits(actual["value"]):016x}', f'{_bits(expected["value"]):016x}')
    assert len(fast["trades"]) == len(plain["trades"])
    for index, (actual, expected) in enumerate(zip(fast["trades"], plain["trades"])):
        assert actual.keys() == expected.keys(), index
        for key in actual:
            if isinstance(actual[key], float):
                assert _bits(actual[key]) == _bits(expected[key]), (index, key)
            else:
                assert actual[key] == expected[key], (index, key)


def _assert_last_literals(report: dict, expected: dict[str, float]) -> None:
    for name, value in expected.items():
        assert _bits(_last(report, name)) == _bits(value), (name, _last(report, name), value)


def _assert_witnesses(name: str, report: dict, coof: bool) -> None:
    trades = report["trades"]
    if name == "summation_loop":
        _assert_last_literals(report, {
            "longTrades": 300.0,  # 2400 / 8 long entries.
            "shortTrades": 300.0,  # 2400 / 8 short entries.
            "longWins": 100.0,  # One winning long in each six-trade cycle.
            "shortWins": 100.0,  # One winning short in each six-trade cycle.
        })
    elif name == "literal_updates":
        _assert_last_literals(report, {
            "rows": 1203.0,  # 3 + 600 * 2.
            "fraction": 74.75,  # -0.25 + 600 * 0.125.
        })
        # Reads before the guard see the per-bar literals, never the cached prefix.
        assert {row["value"] for row in _trace(report, "beforeRows")} == {3.0}
        assert {_bits(row["value"]) for row in _trace(report, "beforeFraction")} == {
            _bits(-0.25),
        }
    elif name == "grammar":
        _assert_last_literals(report, {
            "rows": 593.0,  # -7 + 600 trades.
            "eqLong": 300.0,  # Half of 600 entries are Long.
            "eqShort": 300.0,  # Half of 600 entries are Short.
            "neLong": 300.0,  # Every Short differs from Long.
            "neShort": 300.0,  # Every Long differs from Short.
            "greater": 200.0,  # Two of six profits are positive.
            "greaterEqual": 400.0,  # Two positive and two zero profits per six.
            "less": 200.0,  # Two of six profits are negative.
            "lessEqual": 400.0,  # Two negative and two zero profits per six.
            "equal": 200.0,  # Two of six profits are zero.
            "unequal": 400.0,  # Four of six profits are nonzero.
            "reverseGreater": 200.0,  # 0 > amount selects the negative two of six.
            "reverseGreaterEqual": 400.0,  # 0 >= amount includes negatives and zeros.
            "reverseLess": 200.0,  # 0 < amount selects the positive two of six.
            "reverseLessEqual": 400.0,  # 0 <= amount includes positives and zeros.
            "reverseEqual": 200.0,  # Reversing equality keeps the two zeros per six.
            "reverseUnequal": 400.0,  # Reversing inequality keeps four nonzeros per six.
            "profitTotal": -0.25,  # Each six-trade cycle sums exactly to zero.
            "literalTotal": 75.5,  # 0.5 + 600 * 0.125.
            "negativeZero": 0.0,  # The first -0.0 + 0.0 produces positive zero.
        })
        # Before any trade, the declared negative zero must keep its sign bit.
        assert _bits(_at(report, "negativeZero", 0)) == 0x8000000000000000
        assert _at(report, "rows", 0) == -7.0  # The empty ledger keeps the literal.
    elif name == "two_folds":
        _assert_last_literals(report, {
            "longRows": 300.0,  # Half of 600 trades enter as Long.
            "shortRows": 300.0,  # Half of 600 trades enter as Short.
            "longTotal": -0.5,  # Each long's +1.25, -1.25, 0 cycle sums to zero.
            "shortTotal": 0.25,  # Each short's +1.25, -1.25, 0 cycle sums to zero.
        })
    elif name == "brackets":
        bracket_ids = {"LongFirst", "LongSecond", "ShortFirst", "ShortSecond"}
        groups = defaultdict(list)
        for trade in trades:
            if trade["exit_id"] in bracket_ids:
                groups[trade["entry_id"], trade["entry_time"], trade["exit_time"]].append(trade)
        assert len(groups) == 600  # One two-leg bracket group per four bars.
        for group in groups.values():
            assert len(group) == 2  # Two separately reserved 50% slices.
            assert {row["qty"] for row in group} == {2.0}  # 4 * 50% per leg.
            assert len({row["exit_id"] for row in group}) == 2  # Both legs filled.
        assert max(row["value"] for row in _trace(report, "sameBarPair")) == 1.0
        _assert_last_literals(report, {
            "rows": 1800.0,  # 600 * (two bracket slices + one spare lot).
            "positive": 1200.0,  # Both bracket legs win; spare lots close flat.
            "total": 3600.0,  # 600 * (2 * 1 + 2 * 2), plus flat spare lots.
        })
    elif name == "partial_close":
        assert Counter(row["exit_comment"] for row in trades) == {
            "half": 600,  # 3000 / 5 partial-close commands.
            "remainder": 600,  # One final close follows each partial close.
        }
        assert {row["qty"] for row in trades} == {2.0}  # Each half of a four-unit lot.
        groups = defaultdict(list)
        for trade in trades:
            groups[trade["entry_id"], trade["entry_time"]].append(trade)
        assert len(groups) == 600  # 3000 / 5 independently opened lots.
        for group in groups.values():
            assert len(group) == 2  # The partial and remaining slice share an entry.
            assert len({row["exit_time"] for row in group}) == 2  # Separate exit bars.
    elif name == "close_all":
        assert Counter(row["entry_id"] for row in trades) == {
            "Long": 300,  # One named long per eight bars.
            "OtherLong": 300,  # A second long lot pyramids on that bar.
            "Short": 300,  # One named short per eight bars.
            "OtherShort": 300,  # A second short lot pyramids on that bar.
        }
        assert {row["exit_comment"] for row in trades} == {"flatten"}
        _assert_last_literals(report, {
            "rows": 1200.0,  # Two lots per four-bar close_all cycle.
            "namedRows": 600.0,  # One named lot and one other lot per cycle.
        })
    elif name == "reversals":
        # 600 entries cause 599 reversals, then the last-bar immediate close.
        assert sum(row["exit_id"] in {"Long", "Short"} for row in trades) == 599
        assert trades[-1]["exit_comment"] == "flatten"
        _assert_last_literals(report, {
            "longRows": 300.0,  # 600 alternating entries start with a long.
            "shortRows": 300.0,  # The final flatten closes the 300th short.
        })
    elif name == "immediate_close":
        before = _trace(report, "beforeClose")
        after = _trace(report, "afterClose")
        deltas = [right["value"] - left["value"] for left, right in zip(before, after)]
        assert len(before) == len(after)
        assert set(deltas) == {0.0, 1.0}  # Only the one-unit immediate close adds a row.
        assert deltas.count(1.0) == 600  # Exactly one close before the loop per four bars.
    elif name == "margin":
        # 50% margin on 19900 * 100 uses 995000; a drop to 20 exhausts equity.
        assert any(row["exit_id"] == "__margin_call__" for row in trades)
        assert max(row["value"] for row in _trace(report, "marginExit")) == 1.0
        assert len(trades) >= 601  # 600 ordinary trades plus at least one liquidation.

    if name == "summation_loop":
        # Orders filled at the consumed close do not schedule fill callbacks.
        assert _last(report, "calculations") == 2400.0
    elif name == "immediate_close":
        if coof:
            assert _last(report, "calculations") > 2400.0  # Fills add script executions.
        else:
            assert _last(report, "calculations") == 2400.0  # One execution per chart bar.


@pytest.mark.parametrize("coof", [False, True], ids=["bar_close", "order_fills"])
@pytest.mark.parametrize("name,bars,trades,folds", [
    ("summation_loop", 2400, 600, 1),  # 2400 / 4 single-lot closes; one six-accumulator fold.
    ("literal_updates", 2400, 600, 1),  # 2400 / 4 closes; literal updates without row reads.
    ("grammar", 2400, 600, 1),  # 2400 / 4 closes; every comparison shares one fold.
    ("two_folds", 2400, 600, 2),  # 2400 / 4 closes; disjoint long and short folds.
    ("brackets", 2400, 1800, 1),  # 2400 / 4 * (two bracket slices + one spare).
    ("partial_close", 3000, 1200, 1),  # 3000 / 5 * two slices per entry.
    ("close_all", 2400, 1200, 1),  # 2400 / 4 * two pyramided entry IDs.
    ("reversals", 2400, 600, 1),  # 599 reversals and one immediate final flatten.
    ("immediate_close", 2400, 600, 1),  # 2400 / 4 closes booked inside the body.
    ("margin", 2408, None, 1),  # 600 regular closes, then a variable number of margin slices.
])
def test_closed_trade_folds_match_plain_at_every_calculation(
        native_runner, tmp_path, name, bars, trades, folds, coof):
    source = _source(name, coof)
    feed = _write_feed(tmp_path / "bars.csv", bars, kind=name)
    fast = _build(native_runner, source, tmp_path / "fast", fast=True, folds=folds)
    plain = _build(native_runner, _plain(source, folds), tmp_path / "plain", fast=False,
                   folds=folds)
    actual, expected = _run(fast, feed), _run(plain, feed)
    _assert_same(actual, expected, source, bars)
    for report in (actual, expected):
        if trades is not None:
            assert len(report["trades"]) == trades
        _assert_witnesses(name, report, coof)


def test_fold_matches_plain_on_an_aggregated_chart(native_runner, tmp_path):
    source = _source("summation_loop")
    # Two 1m inputs per chart bar yield 2400 two-minute bars and 600 closes.
    feed = _write_feed(tmp_path / "minute_bars.csv", 2400, native_per_bar=2)
    fast = _build(native_runner, source, tmp_path / "fast", fast=True)
    plain = _build(native_runner, _plain(source, 1), tmp_path / "plain", fast=False)
    actual = _run(fast, feed, input_tf="1", script_tf="2")
    expected = _run(plain, feed, input_tf="1", script_tf="2")
    _assert_same(actual, expected, source, 2400)
    assert len(actual["trades"]) == 600  # One close per four aggregated chart bars.
    _assert_witnesses("summation_loop", actual, False)


def test_trailing_exit_order_can_change_without_a_new_trade(native_runner, tmp_path):
    source = _source("exit_key_change")
    feed = _write_feed(tmp_path / "bars.csv", 2400, kind="exit_key_change")
    fast = _build(native_runner, source, tmp_path / "fast", fast=True)
    plain = _build(native_runner, _plain(source, 1), tmp_path / "plain", fast=False)
    actual, expected = _run(fast, feed), _run(plain, feed)
    _assert_same(actual, expected, source, 2400)
    assert len(actual["trades"]) == 601  # Three initial slices + (2400 - 8) / 4 closes.
    for report in (actual, expected):
        for bar in (2, 3, 4):
            assert _at(report, "closedCount", bar) == 2.0  # No fill joins the trailing pair.
        assert _at(report, "firstExit", 2) == 1.0  # The global X was placed before paired Y.
        assert _at(report, "firstExit", 3) == 1.0  # X is reissued after this publication.
        assert _at(report, "firstExit", 4) == 2.0  # Paired X now sorts after paired Y.
        # ((2**53 + 1) + 2) rounds to 2**53 + 2, whose low mantissa bit is one.
        assert _bits(_at(report, "seededProfit", 3)) == 0x4340000000000001
        # ((2**53 + 2) + 1) ties upward to 2**53 + 4 after the two rows swap.
        assert _bits(_at(report, "seededProfit", 4)) == 0x4340000000000002
        assert _at(report, "total", 4) == 3.0  # Both row orders still have profit 1 + 2.

    # A cursor that settles every previously seen row must miss the changed order.
    mutant_cpp, replacements = re.subn(
        r"(?m)^([ \t]*)if \(_pf_ctf0_n < _pf_ctf0_seen",
        r"\1_pf_ctf0_limit = _pf_ctf0_final;\n\1if (_pf_ctf0_n < _pf_ctf0_seen",
        fast.cpp)
    assert replacements == 1
    directory = tmp_path / "settle_entire_previous_count"
    directory.mkdir()
    build_strategy_library("#undef PINEFORGE_CLOSED_TRADE_FOLD_VERIFY\n" + mutant_cpp,
                           directory)
    mutant = Compiled(native_runner.Strategy(directory / "strategy.so"), mutant_cpp)
    broken = _run(mutant, feed)
    assert _at(broken, "firstExit", 4) == 2.0  # Ledger still swaps; only the cursor changed.
    # The incorrect prefix preserves the pre-swap 2**53 + 2 instead of 2**53 + 4.
    assert _bits(_at(broken, "seededProfit", 4)) == 0x4340000000000001
    assert _bits(_at(broken, "seededProfit", 4)) != _bits(_at(expected, "seededProfit", 4))


def _instrument_steps(cpp: str) -> str:
    # Count the identical first body statement in the lambda and the plain loop.
    cpp, replacements = re.subn(
        r"(?m)^([ \t]*)std::string entryId = closed_trade_entry_id\(i\);$",
        r"\1++pf_ctf_test_steps;\n\1std::string entryId = closed_trade_entry_id(i);",
        cpp)
    assert replacements == 1, "the step counter must instrument exactly one loop body"
    return (
        "#include <cstdint>\n"
        "static std::uint64_t pf_ctf_test_steps = 0;\n"
        + cpp
        + '\nextern "C" std::uint64_t pf_ctf_test_step_count() {\n'
          "    return pf_ctf_test_steps;\n"
          "}\n"
          'extern "C" void pf_ctf_test_reset_steps() {\n'
          "    pf_ctf_test_steps = 0;\n"
          "}\n"
    )


@pytest.mark.parametrize("bars,plain_steps,fold_steps,trades", [
    (8, 12, 10, 4),  # Plain: 0+0+1+1+2+2+3+3; fold: 0+0+1+1+2+2+2+2.
    (1000, 249500, 1994, 500),  # Plain: (B/2)(B/2-1); fold: 2B-6; trades: B/2.
])
def test_fold_step_count_is_linear(native_runner, tmp_path, bars, plain_steps,
                                   fold_steps, trades):
    source = _source("performance")
    feed = _write_feed(tmp_path / "bars.csv", bars, kind="flat")
    fast = _build(native_runner, source, tmp_path / "fast", fast=True,
                  verify=False, count_steps=True)
    plain = _build(native_runner, _plain(source, 1), tmp_path / "plain", fast=False,
                   verify=False, count_steps=True)
    reports = []
    for compiled, expected_steps in ((fast, fold_steps), (plain, plain_steps)):
        counter = compiled.strategy.lib.pf_ctf_test_step_count
        counter.argtypes = []
        counter.restype = ctypes.c_uint64
        reset = compiled.strategy.lib.pf_ctf_test_reset_steps
        reset.argtypes = []
        reset.restype = None
        reset()
        report = _run(compiled, feed)
        rows = report["trades"]
        assert len(rows) == trades
        # Close orders fill after odd bars' calculations, at bars 1, 3, ..., B-1.
        assert [(row["exit_time"] - START_MS) // 60000 for row in rows] == list(
            range(1, bars, 2))
        assert [(row["entry_time"] - START_MS) // 60000 for row in rows] == list(
            range(0, bars, 2))
        assert {row["qty"] for row in rows} == {1.0}  # One unit per even-bar entry.
        assert {_bits(row["pnl"]) for row in rows} == {0}  # Flat prices and no fees.
        for row in _trace(report, "rows"):
            # At t, floor(t/2) closes were published before that script calculation.
            assert row["value"] == row["bar_index"] // 2
        assert counter() == expected_steps
        # Check work first: without the optimization this must fail as 12 != 10.
        assert compiled.cpp.count(FOLD_MARKER) == (1 if compiled is fast else 0)
        reports.append(report)
    _assert_same(reports[0], reports[1], source, bars)
