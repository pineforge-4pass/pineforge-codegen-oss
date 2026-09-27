"""Requests PineForge has no data for, refused only when a trade can read them.

``request.security`` on another symbol and ``request.financial`` /
``earnings`` / ``dividends`` / ``splits`` / ``footprint`` read data the engine
does not load, and each was refused outright. ``external_requests.TradeSlice``
follows the request's value through the script: one that reaches alerts,
plots and tables only is lowered to ``na`` with a warning (a watchlist, a
market-cap cell -- michaellitton16-money-mike-macd-rsi-watchlist-mtf-strategy,
job-1361-shitholed-lema-system-v2-4); one that can reach an order, a
``runtime.error``, a collection, a drawing or a history offset keeps its
refusal. TradingView's tapes of a strategy with such a watchlist and of the
same strategy with it deleted are byte for byte one tape
(``fixtures/xsym_tv/xa_watchlist``), on BINANCE:ETHUSDT.P 15 and on
NASDAQ:AAPL 15.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.ast_nodes import ASTNode, FuncCall, MemberAccess
from pineforge_codegen.errors import CompileError
from pineforge_codegen.external_requests import TradeSlice
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp
from tests._e2e import Build, closed_trades, execute_all, ok, skip_unless_e2e_env
from tests._security_tapes import TAPE_TZ, source, tape_feed


XSYM_TV = Path(__file__).parent / "fixtures" / "xsym_tv"
HEAD = '//@version=6\nstrategy("requests", overlay=true)\n'
WATCH = ('other = request.security("BINANCE:BTCUSDT", timeframe.period, close)\n'
         'shares = request.financial(syminfo.tickerid, "TOTAL_SHARES_OUTSTANDING", "FQ")\n')
TRADE = ('if ta.crossover(ta.sma(close, 5), ta.sma(close, 13))\n'
         '    strategy.entry("L", strategy.long)\n')


def _nodes(value):
    if isinstance(value, ASTNode):
        yield value
        for key, item in vars(value).items():
            if key not in ("loc", "annotations"):
                yield from _nodes(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _nodes(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _nodes(item)


def _first_request(src: str):
    program = Parser(Lexer(src).tokenize(), source=src).parse()
    request = next(n for n in _nodes(program)
                   if isinstance(n, FuncCall) and isinstance(n.callee, MemberAccess)
                   and getattr(n.callee.object, "name", None) == "request")
    return program, request


def _reason(body: str) -> str | None:
    program, request = _first_request(HEAD + WATCH + body)
    return TradeSlice(program).reason(request)


@pytest.mark.parametrize("body", [
    # An alert message, a plot, a table cell -- through a helper's parameter.
    'if other > close\n    alert("above " + str.tostring(other), alert.freq_once_per_bar)\n'
    'plot(other)\nvar table t = table.new(position.top_right, 1, 1)\n'
    'cell(v) => table.cell(t, 0, 0, na(v) ? "n/a" : str.tostring(v, "#.##"))\n'
    'if barstate.islast\n    cell(other / 2)\n',
    # State that feeds a label-free display only: a var counter in a table.
    'var int ups = 0\nif other > other[1]\n    ups += 1\n'
    'var table t = table.new(position.top_right, 1, 1)\n'
    'if barstate.islast\n    table.cell(t, 0, 0, str.tostring(ups))\n',
])
def test_display_only_values_are_inert(body):
    assert _reason(body + TRADE) is None


@pytest.mark.parametrize("body, reason", [
    ('if other > close\n    strategy.entry("L", strategy.long)\n', "strategy.entry"),
    ('strategy.entry("L", strategy.long, qty = other > 0 ? 2 : 1)\n', "strategy.entry"),
    ('var float acc = 0.0\nacc += other\nif acc > close\n    strategy.entry("L", strategy.long)\n',
     "strategy.entry"),
    ('go(x) =>\n    if x > 0\n        strategy.entry("L", strategy.long)\ngo(other)\n',
     "strategy.entry"),
    ('if na(other)\n    runtime.error("no data")\n', "runtime.error"),
    ('arr = array.new_float()\narray.push(arr, other)\n', "array.push"),
    ('if other > close\n    label.new(bar_index, high, "x")\n', "label.new"),
    ('x = close[int(nz(other))]\nplot(x)\n', "history offset"),
    ('for i = 0 to 3\n    if other > close\n        break\n', "break"),
])
def test_values_that_can_reach_a_trade_are_not(body, reason):
    found = _reason(body + TRADE)
    assert found is not None and reason in found, found


def test_payload_with_side_effects_is_not_inert():
    program, request = _first_request(
        HEAD + 'boom() =>\n    runtime.error("x")\n    close\n'
        'v = request.security("BINANCE:BTCUSDT", "60", boom())\nplot(v)\n' + TRADE)
    assert "runtime.error" in TradeSlice(program).reason(request)


def test_inert_requests_are_lowered_with_a_warning():
    result = transpile_full(
        HEAD + WATCH + 'plot(other)\nplot(shares)\n' + TRADE)
    warnings = [d.message for d in result["diagnostics"]]
    for spelled in ('request.security("BINANCE:BTCUSDT", timeframe.period, ...) at line 3',
                    'request.financial(syminfo.tickerid, "TOTAL_SHARES_OUTSTANDING", ...) '
                    'at line 4'):
        assert (f"{spelled}: value reaches only display/alert sinks; lowered to na; "
                "trades are unaffected.") in warnings, warnings
    assert "register_security_eval" not in result["cpp"]


def test_lowered_helper_tuple_keeps_its_element_types():
    """A watchlist helper returning a bool pair from another symbol lowers to
    ``[false, false]``: its callers still destructure bools."""
    cpp = transpile(HEAD + (
        'sig() => [close > open, ta.crossover(close, open)]\n'
        'scan(sym) => request.security(sym, timeframe.period, sig())\n'
        '[b1, r1] = scan("BINANCE:BTCUSDT")\n'
        'if b1 or r1\n    alert("x", alert.freq_once_per_bar)\n') + TRADE)
    assert "std::tuple<bool, bool> scan(" in cpp
    compile_cpp(cpp, label="lowered-helper-tuple")


def test_trade_relevant_request_keeps_its_refusal():
    with pytest.raises(CompileError) as err:
        transpile(HEAD + WATCH + 'if other > close\n    strategy.entry("L", strategy.long)\n')
    (diag,) = [d for d in err.value.diagnostics if d.level.name == "ERROR"]
    assert diag.message == "request.security symbol must reference the current chart symbol."
    assert "this value can reach a trade: it reaches strategy.entry(...)" in diag.hint


def _tape_trades(name: str) -> list[tuple[int, int, float, float]]:
    """(entry, exit, entry price, exit price) of every trade on a tape."""
    with (XSYM_TV / f"{name}_tv_trades.csv").open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    trades: dict[str, dict] = {}
    for row in rows:
        stamp = int(dt.datetime.strptime(row["Date and time"], "%Y-%m-%d %H:%M")
                    .replace(tzinfo=TAPE_TZ).timestamp() * 1000)
        price = float(row["Price USDT"])
        trade = trades.setdefault(row["Trade number"], {})
        key = "entry" if row["Type"].startswith("Entry") else "exit"
        trade[key], trade[key + "_price"] = stamp, price
    return sorted((t["entry"], t["exit"], t["entry_price"], t["exit_price"])
                  for t in trades.values())


def test_watchlist_trades_like_the_strategy_without_it(tmp_path_factory):
    """TradingView books the same 22 trades with and without the watchlist;
    PineForge builds both, books one set of trades for the two, and holds
    every one of TradingView's."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xsym_watchlist")
    feed = tape_feed(engine, base)
    runs = execute_all(engine, feed, base, {
        "watch": Build(source("xa_watchlist", XSYM_TV)),
        "plain": Build(source("xa_watchlist_plain", XSYM_TV))})
    for key in runs:
        ok(runs, key)
    assert runs["watch"].trades["default"] == runs["plain"].trades["default"]
    engine_trades = {(t["entry_time"], t["exit_time"]): (t["entry_price"], t["exit_price"])
                     for t in closed_trades(engine, base / "watch", feed)}
    tape = _tape_trades("xa_watchlist")
    assert len(tape) == 22
    missed = [t for t in tape if engine_trades.get(t[:2]) != pytest.approx(t[2:])]
    assert not missed, missed[:5]
