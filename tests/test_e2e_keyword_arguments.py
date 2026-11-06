"""A keyword argument of a built-in call reaches its own parameter, or the call is refused.

`timeframe.in_seconds(timeframe = "D")` read the chart's timeframe, and
`strategy.closedtrades.profit(trade_num = 1)` read trade 0: the lowering read
the call's positional list, where a keyword is not, and used its default. No
diagnostic was raised. Each row below runs the keyword spelling on the engine
and holds it to a value that comes from the language, not from the code under
test:

* `timeframe.in_seconds("D")` is the number of seconds in a day and
  `timeframe.in_seconds("60")` in an hour;
* `timeframe.change("D")` is true on the first bar of each calendar day, which
  the bar's own traced timestamp says (UTC chart, no session);
* the closed trade `k` of the strategy below was entered at the open of the
  bar after its entry signal and closed at the open of the bar after its exit
  signal, so its entry price and profit are the traced opens of those bars;
  the open trade `k` of the pyramiding strategy likewise.

The positional spelling of each call is a control in the same script. A keyword
no parameter has is refused with the typed unknown-keyword code, as
TradingView refuses it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._e2e import Build, chart_feed_head, execute_all, skip_unless_e2e_env

BARS = 1500
# One contract per order: the engine's default quantity is a share of equity, so
# a trade's profit would not be a difference of two traced opens.
HEADER = ('//@version=6\nstrategy("keyword arguments", default_qty_type = strategy.fixed, '
          'default_qty_value = 1{options})\n')

TIMEFRAME = (
    'float day_pos = timeframe.in_seconds("D")\n'
    'float day_kw = timeframe.in_seconds(timeframe = "D")\n'
    'float hour_pos = timeframe.in_seconds("60")\n'
    'float hour_kw = timeframe.in_seconds(timeframe = "60")\n'
    'float change_pos = timeframe.change("D") ? 1.0 : 0.0\n'
    'float change_kw = timeframe.change(timeframe = "D") ? 1.0 : 0.0\n'
    "// @pf-trace t=time\n"
    "// @pf-trace day_pos=day_pos\n// @pf-trace day_kw=day_kw\n"
    "// @pf-trace hour_pos=hour_pos\n// @pf-trace hour_kw=hour_kw\n"
    "// @pf-trace change_pos=change_pos\n// @pf-trace change_kw=change_kw\n"
)

# Trade k: entry signal on bar 10 + 7k, exit signal two bars later; the order
# of each fills at the open of the next bar (default market-order timing).
CLOSED = (
    'if bar_index > 6 and bar_index % 7 == 3\n'
    '    strategy.entry("L", strategy.long)\n'
    'if bar_index > 6 and bar_index % 7 == 5\n'
    '    strategy.close("L")\n'
    'float profit_pos = strategy.closedtrades.profit(1)\n'
    'float profit_kw = strategy.closedtrades.profit(trade_num = 1)\n'
    'float profit_first_kw = strategy.closedtrades.profit(trade_num = 0)\n'
    'float entry_kw = strategy.closedtrades.entry_price(trade_num = 1)\n'
    "// @pf-trace o=open\n"
    "// @pf-trace profit_pos=profit_pos\n// @pf-trace profit_kw=profit_kw\n"
    "// @pf-trace profit_first_kw=profit_first_kw\n// @pf-trace entry_kw=entry_kw\n"
)

# Four entries (signals on bars 10, 30, 50, 70) that stay open together.
OPEN_SIGNALS = (10, 30, 50, 70)
OPEN = (
    'if bar_index == 10 or bar_index == 30 or bar_index == 50 or bar_index == 70\n'
    '    strategy.entry("L" + str.tostring(bar_index), strategy.long)\n'
    'float price_pos = strategy.opentrades.entry_price(1)\n'
    'float price_kw = strategy.opentrades.entry_price(trade_num = 1)\n'
    'float price_first_kw = strategy.opentrades.entry_price(trade_num = 0)\n'
    "// @pf-trace o=open\n"
    "// @pf-trace price_pos=price_pos\n// @pf-trace price_kw=price_kw\n"
    "// @pf-trace price_first_kw=price_first_kw\n"
)

SOURCES = {
    "timeframe": HEADER.format(options="") + TIMEFRAME,
    "closed": HEADER.format(options="") + CLOSED,
    "open": HEADER.format(options=", pyramiding = 10") + OPEN,
}


def _column(outcome, name):
    return {r["bar_index"]: r["value"] for r in outcome.traces["default"] if r["name"] == name}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("keyword_arguments")
    feed = chart_feed_head(engine, base, BARS)
    outcomes = execute_all(engine, feed, base / "runs",
                           {name: Build(source, trace=True) for name, source in SOURCES.items()})
    for name, outcome in outcomes.items():
        assert outcome.error is None, f"[{name}] {outcome.error}"
    return outcomes


def _mismatches(column, expected, first=0):
    # A traced price difference is a double: compare to nine decimals.
    return [(bar, column[bar], expected(bar)) for bar in sorted(column)
            if bar >= first and not abs(column[bar] - expected(bar)) < 1e-9]


def test_in_seconds_keyword_is_the_named_timeframe(runs):
    outcome = runs["timeframe"]
    for name, seconds in (("day_pos", 86400.0), ("day_kw", 86400.0),
                          ("hour_pos", 3600.0), ("hour_kw", 3600.0)):
        column = _column(outcome, name)
        assert len(column) == BARS, name
        wrong = _mismatches(column, lambda _bar: seconds)
        assert not wrong, f"{name}: {len(wrong)} of {BARS} bars differ from {seconds}; first {wrong[:3]}"


def test_change_keyword_is_true_on_the_first_bar_of_each_day(runs):
    outcome = runs["timeframe"]
    stamps = _column(outcome, "t")
    day = {bar: datetime.fromtimestamp(stamp / 1000.0, timezone.utc).date()
           for bar, stamp in stamps.items()}

    def first_bar_of_a_day(bar):
        return 1.0 if bar >= 1 and day[bar] != day[bar - 1] else 0.0

    # Non-vacuity: about fifteen day starts in 1,500 fifteen-minute bars.
    assert 10 <= sum(first_bar_of_a_day(bar) for bar in stamps) <= 20
    for name in ("change_pos", "change_kw"):
        column = _column(outcome, name)
        assert len(column) == BARS, name
        wrong = _mismatches(column, first_bar_of_a_day)
        assert not wrong, f"{name}: {len(wrong)} of {BARS} bars differ from the day starts; first {wrong[:3]}"


def test_closed_trade_keyword_reads_its_own_trade(runs):
    outcome = runs["closed"]
    opens = _column(outcome, "o")

    def entry(k):
        return opens[11 + 7 * k]

    def profit(k):
        return opens[13 + 7 * k] - opens[11 + 7 * k]

    # Trade 1 is closed from bar 20 on (its exit fills at the open of bar 20).
    assert profit(0) != profit(1), "the two trades must differ for the rows to mean anything"
    for name, expected in (("profit_pos", profit(1)), ("profit_kw", profit(1)),
                           ("profit_first_kw", profit(0)), ("entry_kw", entry(1))):
        column = _column(outcome, name)
        wrong = _mismatches(column, lambda _bar: expected, first=20)
        assert not wrong, (f"{name}: {len(wrong)} bars from bar 20 differ from {expected}; "
                           f"first {wrong[:3]}")


def test_open_trade_keyword_reads_its_own_trade(runs):
    outcome = runs["open"]
    opens = _column(outcome, "o")
    # Entry k is signalled on bar OPEN_SIGNALS[k] and fills at the open of the next bar;
    # with four entries open from bar 71 on, trade k keeps its number.
    expected = {k: opens[signal + 1] for k, signal in enumerate(OPEN_SIGNALS)}
    assert expected[0] != expected[1], "the two entries must differ for the rows to mean anything"
    for name, want in (("price_pos", expected[1]), ("price_kw", expected[1]),
                       ("price_first_kw", expected[0])):
        column = _column(outcome, name)
        wrong = _mismatches(column, lambda _bar: want, first=OPEN_SIGNALS[-1] + 1)
        assert not wrong, f"{name}: {len(wrong)} bars from bar 71 differ from {want}; first {wrong[:3]}"


UNKNOWN = [
    ("timeframe.in_seconds", 'x = timeframe.in_seconds(period = "D")\n'),
    ("timeframe.change", 'x = timeframe.change(period = "D")\n'),
    ("strategy.closedtrades.profit", "x = strategy.closedtrades.profit(trade = 1)\n"),
    ("strategy.opentrades.entry_price", "x = strategy.opentrades.entry_price(trade = 1)\n"),
]


@pytest.mark.parametrize("name,line", UNKNOWN, ids=[row[0] for row in UNKNOWN])
def test_a_keyword_no_parameter_has_is_refused_with_the_typed_code(name, line):
    source = HEADER.format(options="") + line + "plot(x)\n"
    with pytest.raises(CompileError) as caught:
        transpile(source)
    codes = [d.code for d in caught.value.diagnostics]
    assert codes == ["PF-E2027"], codes
    assert name in caught.value.diagnostics[0].message
