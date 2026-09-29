"""A user call read at an offset below a lazy edge reads the call's every-execution history.

``f(...)[k]`` on a user function's call in the lazily evaluated right operand
of ``and``/``or``, or in a ternary's arm, is the call's value ``k`` executions
of its scope ago -- ``k`` bars at the top level, ``k`` calls inside a function:
the call runs on every execution of its scope and the lazy edge gates only the
read. TradingView's tape ``fixtures/lazy_call_history/tg-lazyhist-btc15``
(BINANCE:BTCUSDT 15, see its README) reads ``t(bar_index)[1] == bar_index - 1``
on all 95 of its every-third-bar reads, in functions and at the top level, and
``isNew(s) => inS(s) and not inS(s)[1]`` on the first bar of every session. The
codegen used to push the call's history only where the operand ran
(``_hist_call_*`` in the lazy lambda), so ``[1]`` read the previous time the
operand ran, three bars back, and ``isNew`` was true once in a whole run.

The BTCUSDT feed does not ship here: the tape's own structure is checked, and
the rule is pinned end to end (``tests/_e2e.py``: ``transpile_json``, the built
runtime, ``run_strategy.py`` over the corpus 15m feed, ``@pf-trace`` values)
bar by bar against the spelled-out every-bar reads. The lane's base build
(``LEGACY``) reads the previous execution instead.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._e2e import (
    Build, derive_chart_feed, execute_all, ok, per_bar_mismatches,
    reference_codegen, same, skip_unless_e2e_env,
)


TAPE = Path(__file__).parent / "fixtures" / "lazy_call_history" / "tg-lazyhist-btc15"
# Main at the lane's base (cg/tail-g 76a5b26): the call-local history.
LEGACY = "76a5b264b7ffe3e1e97d2aabf218d122ad5d037c"

HEADER = '//@version=6\nstrategy("e2e-tail-g-lazy-call-history", overlay=true)\n'
NAMES = ("a", "l", "p1", "p3", "pn", "tn", "tna", "tt")
TRACES = "".join(f"// @pf-trace {name}={name}\n" for name in NAMES)
# The tape's shapes: in functions called on every bar, and at the top level.
SUBJECT = (HEADER
           + "t(int x) => x\n"
           + "inS(string s) => not na(time(timeframe.period, s))\n"
           + "isNew(string s) => inS(s) and not inS(s)[1]\n"
           + "prevIs(int k) => bar_index % 3 == 0 and t(bar_index)[1] == bar_index - k\n"
           + "prevNa() => bar_index % 3 == 0 and na(t(bar_index)[1])\n"
           + "tern() => bar_index % 3 == 0 ? t(bar_index)[1] : -1\n"
           + 'a = isNew("0000-0800") ? 1 : 0\n'
           + 'l = isNew("0800-1600") ? 1 : 0\n'
           + "p1 = prevIs(1) ? 1 : 0\n"
           + "p3 = prevIs(3) ? 1 : 0\n"
           + "pn = prevNa() ? 1 : 0\n"
           + "tn = nz(tern(), -7)\n"
           + "tna = bar_index % 3 == 0 and na(t(bar_index)[1]) ? 1 : 0\n"
           + "tt = nz(bar_index % 3 == 0 ? t(bar_index)[1] : -1, -7)\n"
           + TRACES)
# The every-bar reads, spelled out: the call's previous value is the previous
# bar's (na, traced as -7, only on the first bar), and a session's first bar
# is new.
REFERENCE = (HEADER
             + "inS(string s) => not na(time(timeframe.period, s))\n"
             + 's1 = inS("0000-0800")\n'
             + 's2 = inS("0800-1600")\n'
             + "a = s1 and not s1[1] ? 1 : 0\n"
             + "l = s2 and not s2[1] ? 1 : 0\n"
             + "third = bar_index % 3 == 0\n"
             + "p1 = third and bar_index > 0 ? 1 : 0\n"
             + "p3 = 0\n"
             + "pn = third and bar_index == 0 ? 1 : 0\n"
             + "tn = third ? (bar_index > 0 ? bar_index - 1 : -7) : -1\n"
             + "tna = pn\n"
             + "tt = tn\n"
             + TRACES)


def test_tradingview_tape_reads_the_call_on_every_bar():
    with (TAPE / "tv_trades.csv").open(encoding="utf-8-sig") as fh:
        exits = [row for row in csv.DictReader(fh) if row["Type"].startswith("Exit")]
    readings = [row["Signal"].split("|") for row in exits if row["Signal"]]
    assert len(readings) == 287
    assert all(len(fields) == 3 for fields in readings)
    # Off the every-third bars nothing is read; on them, the previous bar's
    # value in functions (1001) and at the top level (01), on all 95.
    assert Counter(fields[1] for fields in readings) == {"000-": 192, "1001": 95}
    assert Counter(fields[2] for fields in readings) == {"0-": 192, "01": 95}
    # isNew on the first bar of each of the range's five later sessions: an
    # exit fills on the bar after the one it reads.
    fired = sorted(row["Date and time"] for row in exits
                   if row["Signal"] and row["Signal"].split("|")[0] != "00")
    assert fired == ["2025-04-07 16:15", "2025-04-08 08:15", "2025-04-08 16:15",
                     "2025-04-09 08:15", "2025-04-09 16:15"]


def test_each_hoisted_read_has_one_writer():
    # A read in an every-bar ta.* hoist's argument keeps that hoist's own
    # push (it already runs on every bar); a read in a statement the lowering
    # drops is not hoisted; every Series is written on one line (a ta.* call
    # renders its argument twice there, for compute and recompute).
    cpp = transpile(HEADER
                    + "t(int x) => x\n"
                    + "c = bar_index % 3 == 0\n"
                    + "x = c and ta.sma(t(bar_index)[2], 3)[1] > 0 ? 1 : 0\n"
                    + "y = c and t(bar_index)[2] > 0 ? 1 : 0\n"
                    + "plot(c and t(bar_index)[1] > 0 ? 1 : 0)\n"
                    + "plot(x + y)\n")
    writers = Counter(member for line in cpp.splitlines()
                      for member in set(re.findall(r"(_hist_call_\d+)\.push\(", line)))
    assert len(writers) == 3
    assert set(writers.values()) == {1}
    assert cpp.count("a call read at an offset runs on every execution") == 1


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory):
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("lazy_call_history")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    builds = {"subject": Build(SUBJECT, trace=True),
              "reference": Build(REFERENCE, trace=True)}
    legacy = reference_codegen(LEGACY)
    if legacy is not None:
        builds["legacy"] = Build(SUBJECT, trace=True, codegen=legacy)
    return execute_all(engine_root, feed, base, builds)


def test_lazy_call_history_reads_every_bar(outcomes):
    subject = ok(outcomes, "subject").traces["default"]
    reference = ok(outcomes, "reference").traces["default"]
    compared, mismatched, first = per_bar_mismatches(
        subject, reference, ("lazy call", "spelled out"))
    assert compared > 0
    assert mismatched == 0, first
    # The feed has a session start on every day: isNew fires again and again.
    assert sum(1 for rec in subject if rec["name"] == "a" and rec["value"] == 1) > 1000


def test_the_pre_lane_build_read_the_previous_execution(outcomes):
    if "legacy" not in outcomes:
        pytest.skip(f"codegen {LEGACY} is not in this checkout's history")
    legacy = ok(outcomes, "legacy").traces["default"]
    reference = ok(outcomes, "reference").traces["default"]
    expected = {(rec["name"], rec["timestamp"]): rec["value"] for rec in reference}
    missed = Counter(rec["name"] for rec in legacy
                     if not same(rec["value"], expected[(rec["name"], rec["timestamp"])]))
    # Every read at an offset below the lazy edges read the previous
    # execution; isNew held after the first session of each kind.
    assert set(missed) == set(NAMES) - {"pn", "tna"}
    assert missed["a"] > 1000 and missed["p3"] > 1000
