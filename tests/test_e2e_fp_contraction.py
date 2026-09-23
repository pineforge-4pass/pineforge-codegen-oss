"""E2E: the emitted strategy translation unit rounds each product on its own,
as TradingView does (lane FPC).

TradingView runs Pine in JavaScript, which has no fused multiply-add: in
``close * 1.1 - open * 1.1`` each product rounds to a double before the
subtraction does. The engine compiles libpineforge with ``-ffp-contract=off``
and hands the option to every target that links it; a strategy TU compiled
without it lets AppleClang and aarch64 g++ fuse one product into the
subtraction, whose result then carries the exact product instead of the
rounded one -- a last-bit difference on most bars that compounds through any
recurrence and can flip a threshold crossing.

The strategy traces ``d = close * 1.1 - open * 1.1`` (``@pf-trace``) through
the harness recipe (``build_strategy_library``), and every traced bar must
equal, bit for bit, the same expression evaluated by Python on the chart
feed's doubles: IEEE-754 arithmetic that rounds each operation, as
JavaScript does. A target without an FMA instruction (baseline x86-64)
rounds twice either way.

Interfaces: ``tests/_e2e.py``.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tests._e2e import Build, Outcome, derive_chart_feed, execute_all, ok, skip_unless_e2e_env


SOURCE = ('//@version=6\nstrategy("e2e-fpc-contraction", overlay=true)\n'
          "d = close * 1.1 - open * 1.1\n"
          "// @pf-trace d=d\n"
          "if d > 0\n"
          '    strategy.entry("L", strategy.long)\n'
          "if d < 0\n"
          '    strategy.close("L")\n')


@pytest.fixture(scope="session")
def contraction_run(tmp_path_factory) -> tuple[Outcome, Path]:
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("e2e_fp_contraction")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    outcomes = execute_all(engine_root, feed, base, {"subject": Build(SOURCE, trace=True)})
    return ok(outcomes, "subject"), feed


def test_products_round_before_the_subtraction(contraction_run) -> None:
    outcome, feed = contraction_run
    with feed.open() as fh:
        bars = {int(row["timestamp"]): (float(row["open"]), float(row["close"]))
                for row in csv.DictReader(fh)}
    compared = mismatched = 0
    first = None
    for rec in outcome.traces["default"]:
        if rec["name"] != "d":
            continue
        open_, close = bars[rec["timestamp"]]
        expected = close * 1.1 - open_ * 1.1
        compared += 1
        if rec["value"] != expected:
            mismatched += 1
            if first is None:
                first = (f"bar {rec['bar_index']} (ts {rec['timestamp']}): traced "
                         f"{rec['value']!r}, twice-rounded {expected!r}")
    assert compared > 0, "the run traced no bar of d"
    assert mismatched == 0, (f"{mismatched} of {compared} bars fused a product into the "
                             f"subtraction; first {first}")
    print(f"E2E fp-contraction: {compared} bars of close * 1.1 - open * 1.1 "
          "equal the twice-rounded value")
