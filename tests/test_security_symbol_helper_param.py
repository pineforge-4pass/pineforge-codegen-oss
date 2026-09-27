"""A ``request.security`` symbol that is a helper parameter.

The support checker gave a helper parameter no binding, so
``noRepaintSecurity(_symbol, _tf, _expr) => request.security(_symbol, ...)``
was refused as an alternate symbol although its only call passes
``syminfo.tickerid`` (talaeaelhussein-prop-scalper-v2-final, retired once as
a cross-symbol refusal). A parameter is the chart's symbol when the argument
every call of its helper binds to it is, read in the caller's scope --
through further helpers' parameters too. Each call path's symbol is part of
its request's context (``security_contexts``): a Heikin-Ashi and a plain
chart symbol through one helper are two contexts. TradingView's tape of
``xa_nested_tf`` (``fixtures/xsym_tv``) passes both the symbol and the
timeframe through two helper levels, one call with
``ticker.heikinashi(syminfo.tickerid)``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import Level
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.support_checker import SupportChecker
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits


XSYM_TV = Path(__file__).parent / "fixtures" / "xsym_tv"
PRELUDE = '//@version=6\nstrategy("T")\n'
DEFERRED = "no data is pinned for this request; the run stops with an error where its value is read."
ALTERNATE = "request.security symbol can select an alternate symbol"


def _diagnostics(src: str):
    ast = Parser(Lexer(src).tokenize(), source=src).parse()
    return SupportChecker(ast, filename="<test>").check()


def _messages(src: str, level: Level) -> list[str]:
    return [d.message for d in _diagnostics(src) if d.level == level]


@pytest.mark.parametrize("body", [
    # The talaeaelhussein shape: the symbol reaches the request through two
    # helpers, the outer one passing syminfo.tickerid.
    'nr(_symbol, _tf, _expr) => request.security(_symbol, _tf, _expr[1], '
    'lookahead = barmerge.lookahead_on)\n'
    'reso(_exp, _res) => nr(syminfo.tickerid, _res, _exp)\n'
    'x = reso(close, "D")\n',
    # Every call passes a chart symbol, a Heikin-Ashi one among them.
    'f(s) => request.security(s, "60", close)\n'
    'a = f(syminfo.tickerid)\nb = f(ticker.heikinashi(syminfo.tickerid))\n',
    # A keyword argument, and an alias of the chart symbol.
    'sym = syminfo.ticker\nf(tf, s) => request.security(s, tf, close)\n'
    'a = f("60", s = sym)\n',
    # A helper nothing calls never runs.
    'f(s) => request.security(s, "60", close)\n',
])
def test_chart_symbol_through_helpers_is_accepted(body):
    src = PRELUDE + body
    assert _messages(src, Level.ERROR) == []
    assert not any(ALTERNATE in m for m in _messages(src, Level.WARNING))


FEED = ("another symbol's bars, read from the feed the requests manifest pins for it; "
        "with none installed, the run stops with an error where its value is read.")


@pytest.mark.parametrize("body", [
    'f(s) => request.security(s, "60", close)\n'
    'a = f(syminfo.tickerid)\nb = f("BINANCE:BTCUSDT")\n',
    'g(s) => request.security(s, "60", close)\nh(s) => g(s)\n'
    'b = h(input.symbol("BINANCE:BTCUSDT", "Sym"))\n',
])
def test_alternate_symbol_through_helpers_is_another_symbol(body):
    # Another symbol whose value reaches a trade reads the feed pinned for
    # it, its symbol resolved per call path (tests/test_foreign_requests.py).
    src = PRELUDE + body + 'if b > close\n    strategy.entry("L", strategy.long)\n'
    assert _messages(src, Level.ERROR) == []
    assert any(m.endswith(FEED) for m in _messages(src, Level.WARNING))


def test_alternate_symbol_through_a_method_is_deferred():
    # A method's parameter: its calls are not followed, so no feed is keyed
    # by it and a value that reaches a trade is a deferred refusal, its first
    # read stopping the run (tests/test_external_requests.py).
    src = PRELUDE + (
        'type P\n    float v\n'
        'method pull(P self, string s) => request.security(s, "60", close)\n'
        'p = P.new(1.0)\nb = p.pull(syminfo.tickerid)\n'
        'if b > close\n    strategy.entry("L", strategy.long)\n')
    assert _messages(src, Level.ERROR) == []
    assert any(m.endswith(DEFERRED) for m in _messages(src, Level.WARNING))


def test_alternate_branch_through_a_helper_warns():
    src = PRELUDE + (
        'useOther = input.bool(false, "Other")\n'
        'f(s) => request.security(s, "60", close)\n'
        'a = f(useOther ? "BINANCE:BTCUSDT" : syminfo.tickerid)\n')
    assert _messages(src, Level.ERROR) == []
    assert any(ALTERNATE in m for m in _messages(src, Level.WARNING))


def test_heikin_ashi_and_plain_symbols_are_two_contexts():
    cpp = transpile(PRELUDE + (
        'f(s) => request.security(s, "60", close)\n'
        'a = f(syminfo.tickerid)\nb = f(ticker.heikinashi(syminfo.tickerid))\n'
        'if a > b\n    strategy.entry("L", strategy.long)\n'))
    assert re.findall(r"register_security_eval\((.*)\);", cpp) == [
        '0, "60", input_tf_, false, false',
        '1, "60", input_tf_, false, false, true',
    ]


def test_nested_helper_symbols_replay_tradingview_tape(tmp_path_factory):
    """The symbol and the timeframe through ``h(sym, tf) => g(sym, tf)``,
    one call with the chart's Heikin-Ashi symbol: all 265 exit Signals."""
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xsym_nested_tf")
    exits = replay(engine, base, {"probe": Build(source("xa_nested_tf", XSYM_TV))})
    tape = tape_exits("xa_nested_tf", XSYM_TV)
    assert len(tape) == 265
    # The 02:45 UTC close: the Heikin-Ashi 60-minute SMA (the fifth value)
    # differs from the plain one (the first).
    assert tape[1743476400000] == "1831.75|na|1831.75|na|1828.94"
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
