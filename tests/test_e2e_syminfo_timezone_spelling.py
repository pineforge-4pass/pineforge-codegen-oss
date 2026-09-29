"""``syminfo.timezone`` spells a UTC exchange zone "Etc/UTC" (lane TAIL-E,
item 4).

TradingView names the zone of BINANCE:ETHUSDT.P and BINANCE:BTCUSDT
"Etc/UTC": ``syminfo.timezone == "Etc/UTC"`` holds and ``== "UTC"`` does not
(tapes ``te_syminfo_timezone`` on ETHUSDT.P 15 and BTCUSDT 1D,
``fixtures/tail_e_tv``). The engine names that zone "UTC", the default of its
symbol facts, and the Pine read returned it as is, so the synthetic oracles
``pf-probe-quant-roc-admission-*`` and ``pf-probe-quant-*stateful-roc-*``,
whose guard requires "Etc/UTC" beside BINANCE:ETHUSDT.P, stopped with
``runtime.error`` where TradingView traded. The Pine read now spells the zone
TradingView's way; the engine calls taking the symbol's zone keep reading its
own name, and every other zone reads as it is.
"""

from __future__ import annotations

from pineforge_codegen import transpile
from tests._e2e import skip_unless_e2e_env
from tests._tail_e_tapes import (
    BAR_MS, DAY_MS, START_MS, build, engine_rows, feed, mismatches, source, tape_rows,
)

NAME = "te_syminfo_timezone"
ETH = {"tickerid": "BINANCE:ETHUSDT.P", "ticker": "ETHUSDT.P"}


def test_the_pine_read_spells_utc_as_etc_utc():
    spelled = ('(syminfo_.timezone.empty() || syminfo_.timezone == "UTC"'
               ' ? std::string("Etc/UTC") : syminfo_.timezone)')
    assert spelled in transpile(source(NAME))
    # A tz-less calendar read keeps handing the engine its own zone name.
    tzless = transpile('//@version=6\nstrategy("z")\nh = hour(time)\n'
                       'if h == 3\n    strategy.entry("L", strategy.long)\n')
    assert spelled not in tzless and "syminfo_.timezone" in tzless


def test_the_timezone_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    tape = tape_rows(f"{NAME}_eth15")
    assert len(tape) == 82
    # Entries carry their id ("L"); closes spell the facts.
    tape = {key: value for key, value in tape.items() if key[0] == "X"}
    chart = feed(engine, tmp_path, START_MS + DAY_MS + BAR_MS)
    workdir = build(tmp_path, NAME, source(NAME))
    for zone in (None, "UTC", "Etc/UTC"):
        kwargs = {"syminfo_strings": ETH}
        if zone is not None:
            kwargs["syminfo_timezone"] = zone
        rows, _ = engine_rows(engine, workdir, chart, **kwargs)
        missed = mismatches(tape, rows)
        assert not missed, f"zone {zone!r}: {len(missed)} of {len(tape)} rows differ:\n" \
            + "\n".join(missed[:5])


def test_another_zone_reads_as_it_is(tmp_path):
    engine = skip_unless_e2e_env()
    chart = feed(engine, tmp_path, START_MS + DAY_MS + BAR_MS)
    workdir = build(tmp_path, NAME, source(NAME))
    rows, _ = engine_rows(engine, workdir, chart, syminfo_strings=ETH,
                          syminfo_timezone="America/New_York")
    zones = {value.split("|")[0] for value in rows.values() if value and "|" in value}
    assert zones == {"America/New_York"}
    assert all(value.split("|")[6:8] == ["-", "-"] for value in rows.values()
               if value and "|" in value)
