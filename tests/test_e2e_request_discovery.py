"""End to end: the feeds ``transpile_json``'s ``requests`` names are exactly
the ones a run needs (``pineforge_codegen.request_discovery``).

The script requests an ``input.symbol`` at the chart's timeframe and at
``"60"``, and a literal ``.P`` symbol at ``"D"``. Resolved as a host resolves
them -- an input's override or default, ``chart`` the chart's timeframe --
the entries name three (symbol, timeframe) feeds: the run given exactly
those runs, the run missing any one of them stops with the request named,
and an override of the input by the entry's title moves the input's two
feeds to the overriding symbol. The bars are generated
(``tests/_requests.py``) and reach the engine through a requests manifest,
as the validation runner installs them (``strategy_set_symbol_feed``).
"""

from __future__ import annotations

from pathlib import Path

from tests._e2e import chart_feed_head, skip_unless_e2e_env
from tests._requests import build, hourly, run, write_root
from tests.test_request_discovery import resolve

SOURCE = (
    '//@version=6\nstrategy("discovery e2e", overlay = true)\n'
    'other = input.symbol("PF:A", "Other symbol")\n'
    'a = request.security(other, timeframe.period, close)\n'
    'b = request.security(other, "60", ta.sma(close, 3))\n'
    'd = request.security("PF:B.P", "D", close)\n'
    'c = a + b + d\n'
    'if bar_index % 2 == 0 and c > 0\n    strategy.entry("L", strategy.long)\n'
    'if bar_index % 2 == 1\n    strategy.close("L")\n'
)
PINNED = "no data is pinned for this request, and its value was read"
DAY = 86_400_000
MINUTES = {"15": 15, "60": 60, "1D": 1440}


def _root(base: Path, slug: str, keys: set[tuple[str, str]], first: int, last: int) -> Path:
    """A requests root holding a feed for each (symbol, timeframe) in
    ``keys``, from three days before the chart's first bar past its last."""
    start = first - first % DAY - 3 * DAY
    feeds = [hourly(symbol, start, (last - start) // (MINUTES[tf] * 60_000) + 2,
                    MINUTES[tf], timeframe=tf)
             for symbol, tf in sorted(keys)]
    return write_root(base, slug, "BINANCE:ETHUSDT", "15", feeds)


def test_requests_name_exactly_the_feeds_the_run_needs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("discovery")
    feed = chart_feed_head(engine, base, 240)
    opens = [int(line.split(",", 1)[0]) for line in feed.read_text().splitlines()[1:]]
    work = base / "pf-discovery"
    entries = build(SOURCE, work)["requests"]
    needed = resolve(entries, "15")
    assert needed == {("PF:A", "15"), ("PF:A", "60"), ("PF:B.P", "1D")}

    def attempt(tag: str, keys: set, inputs: dict | None = None):
        root = _root(base / tag, work.name, keys, opens[0], opens[-1])
        return run(engine, work, feed, root, inputs=inputs, tag=tag)

    complete = attempt("complete", needed)
    assert complete.ok, complete.error
    assert complete.trades.count(b"\n") > 1, complete.trades
    for i, missing in enumerate(sorted(needed)):
        result = attempt(f"without-{i}", needed - {missing})
        assert not result.ok, f"ran without the feed {missing}"
        assert PINNED in result.error, result.error

    override = {"Other symbol": "PF:C"}
    moved = resolve(entries, "15", override)
    assert moved == {("PF:C", "15"), ("PF:C", "60"), ("PF:B.P", "1D")}
    result = attempt("override", moved, override)
    assert result.ok, result.error
    # The generated bars are the same for every symbol: the same trades.
    assert result.trades == complete.trades
    stale = attempt("override-stale", needed, override)
    assert not stale.ok and PINNED in stale.error, stale.error
