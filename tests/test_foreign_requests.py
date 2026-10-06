"""``request.security`` of another symbol reads that symbol's pinned feed.

A request of another symbol whose value can reach a trade was a deferred
refusal (lane XSYM-A): its first read stopped the run whatever data the run
was given. The engine now takes another symbol's bars as an instrument feed
installed before the run (lane XSYM-D: ``register_security_eval`` with the
run-time symbol key, ``strategy_set_symbol_feed``), so the codegen registers
such a request by its symbol string as the run computes it before the first
bar -- a literal, an input's getter, or a helper's parameter resolved on each
call path (``security_contexts``) -- and the payload runs on that symbol's
own bars: its history, ``ta.*`` state (``ta.ema`` seeded as TradingView seeds
it there), ``bar_index``, ``time_close`` and ``syminfo.*`` are the requested
context's. A symbol string equal to the chart's reads the chart. Where no
feed is installed the request registers nothing and its reads stop the run
with it named (item 4): never the chart's bars.

TradingView's behaviour is pinned by synthetic tapes kept outside this
repository (they carry TradingView's bars of other symbols): a TVC:DXY EMA
cross on NYSE:F 15, BINANCE:BTCUSDT 1D and BINANCE:ETHUSDT.P 15, a TVC:VIX
240 ``ta.ema(close, 14)[1]`` lookahead_on strategy on NASDAQ:AAPL 1D and
OANDA:XAUUSD 15, a gaps_on variant and the XSYM-DESIGN alignment witnesses
match trade for trade and bar for bar on the XSYM-B captures. These tests
replay the same rules on generated bars, against an independent reckoning of
the merge rule: with lookahead off a chart bar sees the latest requested bar
closed by its own close, with lookahead on the latest opened by its open.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests._e2e import chart_feed_head, skip_unless_e2e_env
from tests._requests import FACTS, build, hourly, run, same, write_root


HEAD = '//@version=6\nstrategy("xe foreign", overlay = true)\n'
TRADE = ('if bar_index % 2 == 0 and c > 0\n    strategy.entry("L", strategy.long)\n'
         'if bar_index % 2 == 1\n    strategy.close("L")\n')
PINNED = "no data is pinned for this request, and its value was read"
M15, H1 = 15 * 60_000, 60 * 60_000


def _chart(engine: Path, base: Path, bars: int = 240) -> tuple[Path, list[int]]:
    feed = chart_feed_head(engine, base, bars)
    opens = [int(line.split(",", 1)[0]) for line in feed.read_text().splitlines()[1:]]
    return feed, opens


def _traced(names: str, body: str) -> str:
    return "".join(f"// @pf-trace pf_{n}={n}\n" for n in names.split(",")) + HEAD + body


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_registration_keys_the_symbol_string():
    cpp = transpile(HEAD + (
        'sym = input.symbol("PF:A", "Sym")\n'
        'skip = input.bool(true, "Skip invalid")\n'
        'c = request.security(sym, "60", close, ignore_invalid_symbol = skip)\n'
        'd = request.security("PF:B", timeframe.period, close, ignore_invalid_symbol = true)\n'
        + TRADE + 'if d > 0\n    strategy.close("L")\n'))
    assert 'const std::string _pf_symbol = get_input_string("Sym", std::string("PF:A"));' in cpp
    assert ('register_security_eval(0, _pf_symbol, "60", input_tf_, false, false, '
            'static_cast<bool>(get_input_bool("Skip invalid", true)));') in cpp
    assert 'const std::string _pf_symbol = std::string("PF:B");' in cpp
    assert ("register_security_eval(1, _pf_symbol, script_tf_, input_tf_, false, false, true);"
            in cpp)
    # The chart's own ticker id reads the chart: the same-symbol registration.
    assert 'if (_pf_symbol_is_chart(_pf_symbol)) {\n                register_security_eval(0, "60", input_tf_, false, false);' in cpp


def test_currency_and_ticker_constructors_stay_refused():
    for body, needle in (
            ('c = request.security("PF:A", "60", close, currency = currency.EUR)\n',
             "request.security parameter 'currency' is not allowed"),
            ('c = request.security(ticker.new("PF", "A"), "60", close)\n', "ticker.new(...)"),
            ('c = request.security(ticker.modify("PF:A"), "60", close)\n', "ticker.modify(...)")):
        with pytest.raises(CompileError, match=re.escape(needle)):
            transpile(HEAD + body + TRADE)


def test_helper_symbols_are_contexts_per_call_path():
    """Three symbols through three helper levels at two timeframes are six
    contexts, each registered with its own symbol string and timeframe (the
    analyzer's call-site clones told the calls apart by timeframe only, and
    every symbol read one context)."""
    result = transpile_full(HEAD + (
        'vix = input.symbol("PF:V", "VIX")\ndxy = input.symbol("PF:D", "DXY")\n'
        'usdt = input.symbol("PF:U", "USDT")\n'
        'mainTf = input.timeframe("240", "Main")\nconfTf = input.timeframe("60", "Confirm")\n'
        'pack() =>\n    m = ta.ema(close, 3)\n    [close[1], m[1]]\n'
        'htf(sym, tf) => request.security(sym, tf, pack(), lookahead = barmerge.lookahead_on)\n'
        'state(sym, tf) =>\n    [c, m] = htf(sym, tf)\n    c > m ? 1 : -1\n'
        'regime(sym) => state(sym, mainTf) + state(sym, confTf)\n'
        'c = regime(vix) + regime(dxy) + regime(usdt)\n' + TRADE))
    cpp = result["cpp"]
    keys = re.findall(r'const std::string _pf_symbol = get_input_string\("(\w+)"', cpp)
    assert sorted(keys) == ["DXY", "DXY", "USDT", "USDT", "VIX", "VIX"]
    registrations = re.findall(
        r'register_security_eval\(\d+, _pf_symbol, get_input_string\("(Main|Confirm)"', cpp)
    assert sorted(registrations) == ["Confirm"] * 3 + ["Main"] * 3


@pytest.mark.parametrize("symbol", ["syminfo.tickerid", '"PF:A"'])
def test_helper_returning_a_requested_tuple_returns_the_tuple(symbol):
    """``htf(sym, tf) => request.security(sym, tf, pack())`` returns the
    requested tuple, which its callers destructure. It was declared to
    return a double, which did not compile, for the chart's symbol too."""
    cpp = transpile(HEAD + (
        'pack() =>\n    m = ta.ema(close, 3)\n    [close[1], m[1]]\n'
        'htf(sym, tf) => request.security(sym, tf, pack(), lookahead = barmerge.lookahead_on)\n'
        'state(sym, tf) =>\n    [c, m] = htf(sym, tf)\n    c > m ? 1 : -1\n'
        f'c = state({symbol}, "60")\n' + TRADE))
    assert "std::tuple<double, double> htf_cs0(std::string sym, std::string tf) {" in cpp
    compile_cpp(cpp, label=f"helper-requested-tuple-{symbol}")


def test_unresolvable_symbol_keeps_the_deferred_refusal():
    """A symbol no registration computes before the first bar (a series on
    one call path) reads no feed: that request stays a deferred refusal,
    reported with the path, and the script still transpiles."""
    result = transpile_full(HEAD + (
        'f(sym) => request.security(sym, "60", close)\n'
        'c = f(close > open ? "PF:A" : "PF:B")\n' + TRADE))
    warnings = [d for d in result["diagnostics"] if "no data is pinned" in d.message]
    assert warnings and ("its symbol cannot be resolved before the first bar on the call "
                         "path f(): it reads 'close', a series") in warnings[-1].hint
    assert "register_security_eval" not in result["cpp"]
    # An unresolved symbol has no literal; the catalog supplies its line suffix.
    call = 'request.security(sym, \\"60\\", ...)'
    assert f'_PF_OTHER_SYMBOL_STOP("request.security", nullptr, "{call}", 3, "{call} at line 3: {PINNED}")' in result["cpp"]


@pytest.mark.parametrize("body, hint", [
    ('var string tf = "60"\nif bar_index > 10\n    tf := "240"\n'
     'c = request.security("PF:A", tf, close)\n', None),
    ('tf = close > open ? "60" : "240"\nc = request.security("PF:A", tf, close)\n', None),
    ('f(s, t) => request.security(s, t, close)\nvar string tf = "60"\n'
     'if bar_index > 10\n    tf := "240"\nc = f("PF:A", tf)\n',
     "its timeframe tf on the call path f() is not a value registration computes before "
     "the first bar"),
])
def test_timeframe_registration_cannot_compute_keeps_the_deferred_refusal(body, hint):
    """Registration reads the timeframe before the first bar, where a
    ``var`` or reassigned name holds its default: such a request registered
    on the chart's timeframe. It stays a deferred refusal."""
    result = transpile_full(HEAD + body + TRADE)
    (warning,) = [d for d in result["diagnostics"] if "no data is pinned" in d.message]
    assert hint is None or hint in warning.hint, warning.hint
    assert "_pf_symbol" not in result["cpp"]


def test_nested_request_through_a_helper_keeps_the_deferred_refusal():
    """A request in a helper another request's expression calls runs inside
    that request's evaluator, like one written in the expression: neither
    reads a feed (the outer one did, the direct spelling being refused)."""
    result = transpile_full(HEAD + 'g() => request.security("PF:B", "60", close)\n'
                            'c = request.security("PF:A", "240", g())\n' + TRADE)
    assert sum("no data is pinned" in d.message for d in result["diagnostics"]) == 2
    assert "_pf_symbol" not in result["cpp"]


def test_ticker_inherit_names_its_symbol_second():
    """``ticker.inherit(from_tickerid, symbol)`` is ``symbol``'s ticker: it
    was read as the chart's, its first argument."""
    cpp = transpile(HEAD + 'c = request.security(ticker.inherit(syminfo.tickerid, "PF:A"), '
                    '"60", close)\n' + TRADE)
    assert 'const std::string _pf_symbol = std::string("PF:A");' in cpp
    cpp = transpile(HEAD + 'c = request.security(ticker.inherit(syminfo.tickerid), "60", close)\n'
                    + TRADE)
    assert "_pf_symbol" not in cpp


# ---------------------------------------------------------------------------
# The payload runs in the requested context
# ---------------------------------------------------------------------------

def _visible(feed, chart_open: int, lookahead: bool) -> int | None:
    """TradingView's merge rule: the latest requested bar closed by the chart
    bar's close (lookahead off), or opened by its open (lookahead on)."""
    seen = None
    for i, (open_ms, close_ms, _) in enumerate(feed.bars):
        if (open_ms <= chart_open) if lookahead else (close_ms <= chart_open + M15):
            seen = i
    return seen


def _sma_seeded_ema(closes: list[float], length: int) -> list[float]:
    out, value, alpha = [], None, 2 / (length + 1)
    for i, x in enumerate(closes):
        if i < length - 1:
            out.append(float("nan"))
            continue
        value = sum(closes[i - length + 1:i + 1]) / length if value is None else (
            alpha * x + (1 - alpha) * value)
        out.append(value)
    return out


@pytest.mark.parametrize("lookahead", [False, True])
def test_payload_runs_on_the_symbols_own_bars(tmp_path_factory, lookahead):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_context")
    feed_path, opens = _chart(engine, base)
    # Two requested bars before the chart's first: the context's history.
    other = hourly("PF:A", opens[0] - 2 * H1, 70, skip=frozenset({20, 21}))
    flag = "barmerge.lookahead_on" if lookahead else "barmerge.lookahead_off"
    source = _traced("c,s,e,bi,tc,mt,c1", (
        f'[c, s, e, bi, tc, mt, c1] = request.security("PF:A", "60", [close, ta.sma(close, 3), '
        f'ta.ema(close, 3), bar_index, time_close, syminfo.mintick, close[1]], lookahead = {flag})\n'
        + TRADE))
    work = base / "xe-context"
    build(source, work)
    result = run(engine, work, feed_path, write_root(base, "xe-context", "BINANCE:ETHUSDT", "15",
                                                    [other]))
    assert result.ok, result.error
    closes = [bar[2] for bar in other.bars]
    ema = _sma_seeded_ema(closes, 3)
    nan = float("nan")
    checked = 0
    for chart_open in opens:
        got = result.trace[chart_open]
        i = _visible(other, chart_open, lookahead)
        want = ({"c": nan, "s": nan, "e": nan, "bi": nan, "tc": nan, "mt": nan, "c1": nan}
                if i is None else
                {"c": closes[i], "s": sum(closes[i - 2:i + 1]) / 3 if i >= 2 else nan,
                 "e": ema[i], "bi": float(i), "tc": float(other.bars[i][1]),
                 "mt": FACTS["mintick"], "c1": closes[i - 1] if i >= 1 else nan})
        for name, value in want.items():
            assert same(got[f"pf_{name}"], value), (chart_open, name, got[f"pf_{name}"], value)
        checked += 1
    assert checked == len(opens)


def test_gaps_on_reads_na_between_bars_and_keeps_the_payload_history(tmp_path_factory):
    """gaps_on: a chart bar the feed hands nothing reads na, and the
    requested context's own history carries on across it (``close[1]`` is
    the previous requested bar, never na for being on a gap)."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_gaps")
    feed_path, opens = _chart(engine, base)
    other = hourly("PF:A", opens[0], 60)
    source = _traced("c,d", (
        '[c, d] = request.security("PF:A", "60", [close, close - close[1]], '
        'gaps = barmerge.gaps_on)\n' + 'if not na(d) and d > 0\n'
        '    strategy.entry("L", strategy.long)\n' + 'if not na(d) and d < 0\n'
        '    strategy.close("L")\n'))
    work = base / "xe-gaps"
    build(source, work)
    result = run(engine, work, feed_path, write_root(base, "xe-gaps", "BINANCE:ETHUSDT", "15",
                                                    [other]))
    assert result.ok, result.error
    closes = [bar[2] for bar in other.bars]
    last = None
    for chart_open in opens:
        i = _visible(other, chart_open, False)
        got = result.trace[chart_open]
        if i is None or i == last:
            assert same(got["pf_c"], float("nan")) and same(got["pf_d"], float("nan"))
        else:
            assert same(got["pf_c"], closes[i])
            assert same(got["pf_d"], closes[i] - closes[i - 1] if i >= 1 else float("nan"))
        last = i


def test_helper_contexts_read_like_direct_requests(tmp_path_factory):
    """Two symbols through two helper levels at two timeframes read exactly
    what the four direct requests read, and an input override of a symbol
    registers the overriding string."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_paths")
    feed_path, opens = _chart(engine, base)
    feeds = [hourly(sym, opens[0], count, minutes)
             for sym in ("PF:A", "PF:B") for minutes, count in ((60, 70), (240, 20))]
    prelude = ('symA = input.symbol("PF:A", "A")\nsymB = input.symbol("PF:B", "B")\n'
               'mainTf = input.timeframe("240", "Main")\nconfTf = input.timeframe("60", "Confirm")\n')
    helpers = _traced("a1,a2,b1,b2", prelude + (
        'htf(sym, tf) => request.security(sym, tf, close - ta.sma(close, 2))\n'
        'state(sym, tf) => htf(sym, tf)\n'
        'regime(sym) => [state(sym, mainTf), state(sym, confTf)]\n'
        '[a1, a2] = regime(symA)\n[b1, b2] = regime(symB)\nc = a1 + a2 + b1 + b2\n' + TRADE))
    direct = _traced("a1,a2,b1,b2", prelude + ''.join(
        f'{name} = request.security({sym}, {tf}, close - ta.sma(close, 2))\n'
        for name, sym, tf in (("a1", "symA", "mainTf"), ("a2", "symA", "confTf"),
                              ("b1", "symB", "mainTf"), ("b2", "symB", "confTf")))
        + 'c = a1 + a2 + b1 + b2\n' + TRADE)
    runs = {}
    for key, source in (("xe-helpers", helpers), ("xe-direct", direct)):
        build(source, base / key)
        root = write_root(base, key, "BINANCE:ETHUSDT", "15", feeds)
        runs[key] = run(engine, base / key, feed_path, root)
        runs[key + "-b"] = run(engine, base / key, feed_path, root,
                               inputs={"A": "PF:B"}, tag="b")
    for key in runs:
        assert runs[key].ok, runs[key].error
    finite = 0
    for chart_open in opens:
        for name in ("a1", "a2", "b1", "b2"):
            h = runs["xe-helpers"].trace[chart_open][f"pf_{name}"]
            assert same(h, runs["xe-direct"].trace[chart_open][f"pf_{name}"]), (chart_open, name)
            finite += h == h
            # With A overridden to PF:B, A's contexts read B's bars.
            if name.startswith("a"):
                b = runs["xe-helpers"].trace[chart_open][f"pf_b{name[1]}"]
                assert same(runs["xe-helpers-b"].trace[chart_open][f"pf_{name}"], b)
    assert finite > len(opens)
    assert runs["xe-helpers"].trades == runs["xe-direct"].trades


def test_helper_copies_keep_their_locals_history_apart(tmp_path_factory):
    """A helper copied per symbol copies its locals too (``v`` is
    ``v__pfctx1`` in the copy): the analyzer keeps one history buffer per
    callable local name, and refused the copy's ``v[1]``."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_copies")
    feed_path, opens = _chart(engine, base)
    feeds = [hourly(sym, opens[0] + i * 7 * M15, 70) for i, sym in enumerate(("PF:A", "PF:B"))]
    helper = _traced("c", 'f(sym) =>\n    v = request.security(sym, "60", close)\n'
                          '    w = close * 2\n    v - v[1] + w[1]\n'
                          'c = f("PF:A") - f("PF:B")\n' + TRADE)
    direct = _traced("c", 'a = request.security("PF:A", "60", close)\n'
                          'b = request.security("PF:B", "60", close)\nw = close * 2\n'
                          'c = (a - a[1] + w[1]) - (b - b[1] + w[1])\n' + TRADE)
    runs = {}
    for key, source in (("xe-copies", helper), ("xe-copies-direct", direct)):
        build(source, base / key)
        runs[key] = run(engine, base / key, feed_path,
                        write_root(base, key, "BINANCE:ETHUSDT", "15", feeds))
        assert runs[key].ok, runs[key].error
    for chart_open in opens:
        assert same(runs["xe-copies"].trace[chart_open]["pf_c"],
                    runs["xe-copies-direct"].trace[chart_open]["pf_c"]), chart_open
    assert sum(v["pf_c"] == v["pf_c"] for v in runs["xe-copies"].trace.values()) > 100
    assert runs["xe-copies"].trades == runs["xe-copies-direct"].trades


def test_chart_symbol_string_reads_the_chart(tmp_path_factory):
    """A literal equal to the chart's ticker id reads the chart, as
    ``syminfo.tickerid`` does: no feed is needed."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_chart")
    feed_path, opens = _chart(engine, base)
    runs = {}
    for key, symbol in (("xe-literal", '"BINANCE:ETHUSDT"'), ("xe-tickerid", "syminfo.tickerid")):
        build(_traced("c", f'c = request.security({symbol}, "60", ta.sma(close, 2))\n' + TRADE),
              base / key)
        runs[key] = run(engine, base / key, feed_path, None)
        assert runs[key].ok, runs[key].error
    assert runs["xe-literal"].trace == runs["xe-tickerid"].trace
    assert any(v["pf_c"] == v["pf_c"] for v in runs["xe-literal"].trace.values())


def test_invalid_symbol_reads_na_under_ignore_invalid_symbol(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_invalid")
    feed_path, _ = _chart(engine, base)
    for key, flag in (("xe-ignore", "true"), ("xe-strict", "false")):
        build(_traced("c", f'c = request.security("PF:GONE", "60", close, '
                           f'ignore_invalid_symbol = {flag})\n' + TRADE), base / key)
        result = run(engine, base / key, feed_path,
                     write_root(base, key, "BINANCE:ETHUSDT", "15", symbols={"PF:GONE": None}))
        if flag == "true":
            assert result.ok, result.error
            assert all(v["pf_c"] != v["pf_c"] for v in result.trace.values())
        else:
            assert not result.ok and "symbol 'PF:GONE' is invalid" in result.error


# ---------------------------------------------------------------------------
# No feed installed (item 4): never the chart's bars
# ---------------------------------------------------------------------------

def test_missing_feed_stops_the_run_where_its_value_is_read(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_missing")
    feed_path, _ = _chart(engine, base)
    work = base / "xe-missing"
    build(HEAD + 'c = request.security("PF:A", "60", close)\n' + TRADE, work)
    for root in (None, write_root(base, "xe-missing", "BINANCE:ETHUSDT", "15",
                                  [hourly("PF:A", 0, 3, 240, timeframe="240")])):
        # Unset, or a feed of the symbol at another timeframe only.
        result = run(engine, work, feed_path, root)
        assert not result.ok
        assert (f'request.security("PF:A", "60", ...) at line 3: {PINNED}') in result.error


def test_missing_feed_read_only_in_a_branch_never_taken_runs(tmp_path_factory):
    """Binding the request is no read: a script reading it only under an
    input left off runs, with no feed and no chart bars in its place."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_untaken")
    feed_path, _ = _chart(engine, base)
    work = base / "xe-untaken"
    build(HEAD + ('useMacro = input.bool(false, "Macro")\n'
                  'm = request.security("PF:A", "60", close)\n'
                  'if bar_index % 2 == 0 and (not useMacro or m > 0)\n'
                  '    strategy.entry("L", strategy.long)\n'
                  'if bar_index % 2 == 1\n    strategy.close("L")\n'), work)
    assert run(engine, work, feed_path, None).ok
    result = run(engine, work, feed_path, None, inputs={"Macro": "true"},
                 tag="macro")
    assert not result.ok and PINNED in result.error


def test_symbol_that_can_select_another_never_reads_the_chart_for_it(tmp_path_factory):
    """``useOther ? "PF:A" : syminfo.tickerid`` read the chart's bars for
    both arms. Its value reaching a trade, it now registers by the string
    the run computes: the chart's arm reads the chart, PF:A's its feed, and
    with no feed PF:A's reads stop the run."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_alternate")
    feed_path, opens = _chart(engine, base)
    body = ('useOther = input.bool(false, "Other")\n'
            'c = request.security(useOther ? "PF:A" : syminfo.tickerid, "60", close)\n' + TRADE)
    result = transpile_full(HEAD + body)
    assert any("its symbol can select another symbol" in d.message
               for d in result["diagnostics"])
    other = hourly("PF:A", opens[0], 70)
    runs = {}
    for key, source in (("xe-alternate", _traced("c", body)),
                        ("xe-chart", _traced("c", body.replace(
                            'useOther ? "PF:A" : syminfo.tickerid', "syminfo.tickerid")))):
        build(source, base / key)
        runs[key] = run(engine, base / key, feed_path, None)
        assert runs[key].ok, runs[key].error
    assert runs["xe-alternate"].trace == runs["xe-chart"].trace
    work = base / "xe-alternate"
    missing = run(engine, work, feed_path, None, inputs={"Other": "true"}, tag="other")
    assert not missing.ok and PINNED in missing.error
    fed = run(engine, work, feed_path, write_root(base, "xe-alternate", "BINANCE:ETHUSDT", "15",
                                                 [other]), inputs={"Other": "true"}, tag="fed")
    assert fed.ok, fed.error
    closes = [bar[2] for bar in other.bars]
    for chart_open in opens:
        i = _visible(other, chart_open, False)
        assert same(fed.trace[chart_open]["pf_c"], float("nan") if i is None else closes[i])


def test_ticker_arm_of_a_symbol_that_can_select_another_reads_the_chart(tmp_path_factory):
    """Registration computes ``syminfo.ticker`` as the chart's ticker
    ("ETHUSDT"), which reads the chart as ``syminfo.tickerid`` does: it was
    compared with the ticker id only, and stopped the run."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_alternate_ticker")
    feed_path, _ = _chart(engine, base)
    runs = {}
    for key, symbol in (("xe-alt-ticker", 'useOther ? "PF:A" : syminfo.ticker'),
                        ("xe-alt-chart", "syminfo.tickerid")):
        build(_traced("c", 'useOther = input.bool(false, "Other")\n'
                           f'c = request.security({symbol}, "60", close)\n' + TRADE), base / key)
        runs[key] = run(engine, base / key, feed_path, None)
        assert runs[key].ok, runs[key].error
    assert runs["xe-alt-ticker"].trace == runs["xe-alt-chart"].trace


def test_symbol_that_can_select_the_chart_through_a_helper_keeps_the_chart_lowering():
    """``f(useOther ? "PF:A" : mysym)``, ``mysym`` a ``var`` (no value before
    the first bar): registration cannot key it, so the request keeps the
    chart lowering every earlier build emitted, with its warning. It became a
    deferred refusal, which stopped the run for the chart's arm too."""
    result = transpile_full(HEAD + 'useOther = input.bool(false, "Other")\n'
                            'var string mysym = syminfo.tickerid\n'
                            'f(s) => request.security(s, "60", close)\n'
                            'c = f(useOther ? "PF:A" : mysym)\n' + TRADE)
    (warning,) = [d for d in result["diagnostics"] if "alternate symbol" in d.message]
    assert ('its symbol useOther ? "PF:A" : mysym on the call path f() is not a value '
            'registration computes before the first bar') in warning.hint
    assert not any("no data is pinned" in d.message for d in result["diagnostics"])
    # Ignore helper definitions; this chart-only body still emits no stop.
    body = result["cpp"].split("class GeneratedStrategy", 1)[1]
    assert "pine_runtime_error" not in body and "_PF_OTHER_SYMBOL_STOP" not in body and "_pf_symbol" not in body


def test_reassigned_tuple_request_stops_where_it_is_evaluated(tmp_path_factory):
    """``[a, b] = request.security(...)`` with ``a := a * 2``: evaluating the
    request is its read. The tuple emitter never visited the request, so a
    missing feed read na silently."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_tuple_reassigned")
    feed_path, opens = _chart(engine, base)
    work = base / "xe-tuple"
    build(HEAD + '[a, b] = request.security("PF:A", "60", [close, open])\na := a * 2\n'
          'c = a - b\n' + TRADE, work)
    result = run(engine, work, feed_path, None)
    assert not result.ok
    assert f'request.security("PF:A", "60", ...) at line 3: {PINNED}' in result.error
    fed = run(engine, work, feed_path, write_root(base, "xe-tuple", "BINANCE:ETHUSDT", "15",
                                                 [hourly("PF:A", opens[0], 70)]), tag="fed")
    assert fed.ok, fed.error


def test_lower_tf_request_of_another_symbol_never_reads_the_chart(tmp_path_factory):
    """``request.security_lower_tf`` of another symbol read the chart's
    intrabars. Its value reaching a trade (an array reaches one through any
    ``array.*`` call), evaluating it stops the run with it named; a symbol
    that can select the chart's keeps its lowering and warns."""
    body = ('arr = request.security_lower_tf("PF:A", "5", close)\n'
            'c = array.size(arr) > 0 ? array.get(arr, 0) : 0.0\n' + TRADE)
    result = transpile_full(HEAD + body)
    assert any(d.message == 'request.security_lower_tf("PF:A", "5", ...) at line 3: no data '
                            'is pinned for this request; the run stops with an error where it '
                            'is evaluated.' for d in result["diagnostics"])
    either = transpile_full(HEAD + 'useOther = input.bool(false, "Other")\n' + body.replace(
        '"PF:A"', 'useOther ? "PF:A" : syminfo.tickerid'))
    assert any("whose lower-timeframe bars PineForge reads from the chart" in d.message
               for d in either["diagnostics"])
    assert "pine_runtime_error(std::string(\"request" not in either["cpp"]
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_lower_tf")
    feed_path, _ = _chart(engine, base)
    build(HEAD + body, base / "xe-lower-tf")
    result = run(engine, base / "xe-lower-tf", feed_path, None)
    assert not result.ok
    assert f'request.security_lower_tf("PF:A", "5", ...) at line 3: {PINNED}' in result.error
