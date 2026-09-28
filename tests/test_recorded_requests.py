"""``request.earnings`` / ``dividends`` / ``splits`` / ``financial`` read the
series TradingView returned per chart bar, recorded under the request's key.

Their values come from TradingView's own report-time and fiscal-period logic,
which PineForge does not model: the requests manifest records, per probe
chart, every bar TradingView answered non-na (``chart_open_ms,value``) under
``<fn>|<symbol>|<field-or-id>|<period-or->|gaps_<on|off>|lookahead_<on|off>``
(workflow ``formatRecordedKey``; lane XSYM-B), and the engine replays it on
the chart bar that opens at that time (``recorded_series_value``, lane
XSYM-D). A request whose value can reach a trade was a deferred refusal
(XSYM-A); it now reads its series. The key's symbol is the string the run
passes (``syminfo.tickerid`` is the lane symbol); field constants, financial
id and period, ``gaps`` and ``lookahead`` are constants of the call. A
spelling no key names (a series or input field, a ``currency``) keeps the
deferred refusal, and a key the run was given no series for reads na where
it is bound and stops the run where it is read.

A PEAD-like synthetic strategy (``request.earnings`` actual and estimate,
gaps on, lookahead off) books TradingView's trades exactly on NASDAQ:AAPL 1D
and NYSE:F 15 from the XSYM-B tapes (kept outside this repository).
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile, transpile_full
from tests._compile import compile_cpp
from tests._e2e import chart_feed_head, skip_unless_e2e_env
from tests._requests import build, run, same, write_root


HEAD = '//@version=6\nstrategy("xe recorded", overlay = true)\n'
TRADE = ('if bar_index % 2 == 0 and v > 0\n    strategy.entry("L", strategy.long)\n'
         'if bar_index % 2 == 1\n    strategy.close("L")\n')
PINNED = "no data is pinned for this request, and its value was read"


@pytest.mark.parametrize("call, key", [
    ('request.earnings(syminfo.tickerid)', '"earnings|") + syminfo_.tickerid + '
     'std::string("|actual|-|gaps_off|lookahead_off"'),
    ('request.earnings(syminfo.tickerid, earnings.estimate, barmerge.gaps_on, '
     'barmerge.lookahead_on)', '"earnings|") + syminfo_.tickerid + '
     'std::string("|estimate|-|gaps_on|lookahead_on"'),
    ('request.dividends("NYSE:F", dividends.net, gaps = barmerge.gaps_on)',
     '"dividends|") + std::string("NYSE:F") + std::string("|net|-|gaps_on|lookahead_off"'),
    ('request.splits(syminfo.tickerid, splits.numerator, ignore_invalid_symbol = true)',
     '"splits|") + syminfo_.tickerid + std::string("|numerator|-|gaps_off|lookahead_off"'),
    ('request.financial(syminfo.tickerid, "TOTAL_SHARES_OUTSTANDING", "FQ", barmerge.gaps_on)',
     '"financial|") + syminfo_.tickerid + '
     'std::string("|TOTAL_SHARES_OUTSTANDING|FQ|gaps_on|lookahead_off"'),
])
def test_recorded_key_spellings(call, key):
    cpp = transpile(HEAD + f'v = {call}\n' + TRADE)
    # The key is computed where the request is evaluated, which sets the
    # request's flag; its reads test the flag.
    assert f"v = _pf_recorded((std::string({key})), _pf_rec_missing_0);" in cpp
    assert "if (_pf_rec_missing_0) pine_runtime_error" in cpp
    compile_cpp(cpp, label="recorded-key")


@pytest.mark.parametrize("call", [
    'request.earnings(syminfo.tickerid, earnings.actual, currency = currency.EUR)',
    'request.financial(syminfo.tickerid, input.string("REVENUE", "Id"), "FY")',
    'request.financial(syminfo.tickerid, "REVENUE", "YEARLY")',
    'request.security(syminfo.tickerid, "D", request.earnings(syminfo.tickerid))',
])
def test_spelling_no_key_names_keeps_the_deferred_refusal(call):
    result = transpile_full(HEAD + f'v = {call}\n' + TRADE)
    assert any(d.message.endswith("no data is pinned for this request; the run stops with an "
                                  "error where its value is read.")
               for d in result["diagnostics"]), [d.message for d in result["diagnostics"]]
    assert "_pf_recorded(" not in result["cpp"]


def test_display_only_request_stays_na():
    cpp = transpile(HEAD + 'v = request.earnings(syminfo.tickerid)\nplot(v)\n'
                    'if bar_index % 2 == 0\n    strategy.entry("L", strategy.long)\n')
    assert "_pf_recorded" not in cpp


def _chart(engine, base, bars=120):
    feed = chart_feed_head(engine, base, bars)
    return feed, [int(line.split(",", 1)[0]) for line in feed.read_text().splitlines()[1:]]


def test_recorded_values_land_on_their_chart_bars(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_recorded")
    feed, opens = _chart(engine, base)
    rows = [(opens[7], 1.65), (opens[40], -0.25), (opens[41], 2.5)]
    key = "earnings|BINANCE:ETHUSDT|actual|-|gaps_on|lookahead_off"
    work = base / "xe-recorded"
    build("// @pf-trace pf_v=v\n" + HEAD
          + 'v = request.earnings(syminfo.tickerid, earnings.actual, barmerge.gaps_on)\n'
          + TRADE, work)
    result = run(engine, work, feed, write_root(base, "xe-recorded", "BINANCE:ETHUSDT", "15",
                                               recorded={key: rows}))
    assert result.ok, result.error
    by_open = dict(rows)
    for chart_open in opens:
        assert same(result.trace[chart_open]["pf_v"], by_open.get(chart_open, float("nan")))
    # The key's symbol is the chart's: another chart reads another key.
    result = run(engine, work, feed, write_root(base / "f", "xe-recorded", "NYSE:F", "15",
                                               recorded={key: rows}), tickerid="NYSE:F")
    assert not result.ok and PINNED in result.error


def test_header_only_tape_reads_na_throughout(tmp_path_factory):
    """TradingView answers na on every bar (a crypto chart's earnings): the
    capture records a tape with no row, and the run reads na and books its
    trades without the value."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_recorded_empty")
    feed, opens = _chart(engine, base)
    key = "earnings|BINANCE:ETHUSDT|actual|-|gaps_off|lookahead_off"
    work = base / "xe-recorded-empty"
    build("// @pf-trace pf_v=v\n" + HEAD + 'v = request.earnings(syminfo.tickerid)\n'
          + 'if bar_index % 2 == 0 and (na(v) or v > 0)\n    strategy.entry("L", strategy.long)\n'
          + 'if bar_index % 2 == 1\n    strategy.close("L")\n', work)
    result = run(engine, work, feed, write_root(base, "xe-recorded-empty", "BINANCE:ETHUSDT",
                                               "15", recorded={key: []}))
    assert result.ok, result.error
    assert all(values["pf_v"] != values["pf_v"] for values in result.trace.values())
    assert result.trades.count(b"Entry long") > 0


def test_missing_tape_stops_the_run_only_where_read(tmp_path_factory):
    """No series installed: binding the request is no read, so a script
    reading it only under an input left off runs; turned on, the run stops
    with the request named."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_recorded_missing")
    feed, _ = _chart(engine, base)
    work = base / "xe-recorded-missing"
    build(HEAD + 'useEps = input.bool(false, "EPS")\n'
          'v = request.earnings(syminfo.tickerid)\n'
          'if bar_index % 2 == 0 and (not useEps or v > 0)\n    strategy.entry("L", strategy.long)\n'
          'if bar_index % 2 == 1\n    strategy.close("L")\n', work)
    assert run(engine, work, feed, None).ok
    result = run(engine, work, feed, None, inputs={"EPS": "true"}, tag="eps")
    assert not result.ok
    assert ("request.earnings(syminfo.tickerid) at line 4: " + PINNED) in result.error


@pytest.mark.parametrize("body", [
    # A helper's parameter, then a local, shadowing the global the key reads.
    'g(sym) => e > sym\nif g(1.0)\n    strategy.entry("L", strategy.long)\n',
    'f() =>\n    sym = 5.0\n    e > sym\nif f()\n    strategy.entry("L", strategy.long)\n',
])
def test_key_is_computed_where_the_request_is_evaluated(body):
    """A read of ``e`` tests the flag the request set where it was evaluated:
    it re-rendered the key in the read's scope, where ``sym`` named a float
    (the C++ did not compile)."""
    cpp = transpile(HEAD + 'sym = "NASDAQ:AAPL"\ne = request.earnings(sym)\n' + body)
    assert cpp.count('std::string("earnings|")') == 1
    compile_cpp(cpp, label="recorded-key-scope")


def test_read_in_a_helper_reads_the_key_the_request_was_evaluated_with(tmp_path_factory):
    """``f(sym) => e > 0 and sym != ""`` called with another symbol: the read
    of ``e`` checked that symbol's key, a false stop without its series."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_recorded_scope")
    feed, opens = _chart(engine, base)
    key = "earnings|PF:A|actual|-|gaps_off|lookahead_off"
    work = base / "xe-recorded-scope"
    build(HEAD + 'sym = "PF:A"\ne = request.earnings(sym)\nf(sym) => e > 0 and sym != ""\n'
          'if bar_index % 2 == 0 and f("PF:B")\n    strategy.entry("L", strategy.long)\n'
          'if bar_index % 2 == 1\n    strategy.close("L")\n', work)
    rows = [(t, 1.0) for t in opens[::2]]
    result = run(engine, work, feed, write_root(base, "xe-recorded-scope", "BINANCE:ETHUSDT",
                                               "15", recorded={key: rows}))
    assert result.ok, result.error
    assert result.trades.count(b"Entry long") > 0
    result = run(engine, work, feed, None, tag="none")
    assert not result.ok and PINNED in result.error


def test_request_in_a_helper_a_request_expression_calls_keeps_the_deferred_refusal():
    """The payload evaluator runs the helper on the requested bars, where no
    chart bar's recorded value is: as when written in the expression."""
    result = transpile_full(HEAD + 'g() => request.earnings(syminfo.tickerid)\n'
                            'v = request.security(syminfo.tickerid, "D", g())\n' + TRADE)
    assert any(d.message.endswith("no data is pinned for this request; the run stops with an "
                                  "error where its value is read.")
               for d in result["diagnostics"]), [d.message for d in result["diagnostics"]]
    assert "_pf_recorded(" not in result["cpp"]
