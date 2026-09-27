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


PINNED = "no data is pinned for this request, and its value was read"


def test_trade_relevant_request_is_a_deferred_refusal():
    """A value that can reach a trade is a deferred refusal: binding it is no
    read, and each read stops the run with the request named."""
    result = transpile_full(HEAD + WATCH + 'if other > close\n    strategy.entry("L", strategy.long)\n'
                            + 'if other[1] > open\n    strategy.close("L")\n')
    (warning,) = [d for d in result["diagnostics"] if "no data is pinned" in d.message]
    assert warning.message == (
        'request.security("BINANCE:BTCUSDT", timeframe.period, ...) at line 3: no data is '
        "pinned for this request; the run stops with an error where its value is read.")
    assert "it reaches strategy.entry(...)" in warning.hint
    cpp = result["cpp"]
    message = 'request.security(\\"BINANCE:BTCUSDT\\", timeframe.period, ...) at line 3: ' + PINNED
    # Two reads -- ``other`` and ``other[1]`` -- and no registration.
    assert cpp.count(f'pine_runtime_error(std::string("{message}"))') == 2
    assert "return other[0]; }())" in cpp and "return other[1]; }())" in cpp
    assert "register_security_eval" not in cpp


def test_request_reassigned_or_inside_an_expression_stops_where_it_is_evaluated():
    """No declaration holds its value alone: evaluating the request is its read."""
    for body in ('x = request.financial(syminfo.tickerid, "FQ_X", "FQ")\nx := nz(x, 1)\n',
                 'x = nz(request.financial(syminfo.tickerid, "FQ_X", "FQ"), 1)\n'):
        cpp = transpile(HEAD + body + 'strategy.entry("L", strategy.long, qty = x)\n')
        assert cpp.count(PINNED) == 1
        assert f'{PINNED}")); return na<double>(); }}())' in cpp


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


def test_request_read_only_in_an_arm_never_taken_runs(tmp_path_factory):
    """TradingView never evaluates a switch arm its selector does not take:
    with the default "Session" its ``xa_default_arm`` tape books 265 closes
    although the "Boom" arm stops the script. PineForge reproduces every exit
    Signal; overriding the selector to the arm reading request.earnings stops
    the run with the request named, and to "Boom" with its runtime.error."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xsym_default_arm")
    from tests._security_tapes import mismatches, replay, tape_exits
    exits = replay(engine, base, {"probe": Build(source("xa_default_arm", XSYM_TV))})
    tape = tape_exits("xa_default_arm", XSYM_TV)
    assert len(tape) == 265
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    feed = base / "tape_chart.csv"
    with pytest.raises(RuntimeError, match=(
            r"request\.earnings\(syminfo\.tickerid, earnings\.actual, \.\.\.\) at line 7: "
            "no data is pinned for this request, and its value was read")):
        closed_trades(engine, base / "probe", feed, {"Anchor": "Earnings"})
    with pytest.raises(RuntimeError, match="a non-default arm was evaluated"):
        closed_trades(engine, base / "probe", feed, {"Anchor": "Boom"})
