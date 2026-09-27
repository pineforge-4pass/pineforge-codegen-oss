"""A ``request.security`` helper whose symbol or timeframe parameter every
call passes one value, and which has no TA or history state of its own.

``Analyzer._check_mixed_callsite_security_tf`` numbered such a helper's call
sites in ``func_call_cs_map`` before it knew whether they pass different
contexts. One context clones nothing, so the helper was emitted once as
``f`` while its calls, keyed off that map, read ``f_cs0(...)``: C++ that did
not compile (``f(string sym) => request.security(sym, "60", close[1])``,
``f(tf) => request.security(syminfo.tickerid, tf, close)`` called as
``f("60")``). The call sites are now numbered only when the request is
cloned per context.

TradingView's tape of ``xa2_single_context`` (``fixtures/xsym_a2_tv``,
BINANCE:ETHUSDT.P 15) spells the three helpers' values on each close.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits


XSYM_A2_TV = Path(__file__).parent / "fixtures" / "xsym_a2_tv"
PRELUDE = '//@version=6\nstrategy("T")\n'
TRADE = 'if x > close\n    strategy.entry("L", strategy.long)\n'


def _called_and_defined(cpp: str) -> tuple[set[str], set[str]]:
    """The names ``f`` is called and emitted by."""
    defined = set(re.findall(r"\n    \w[\w:<>]* (f(?:_cs\d+)?)\([^)]*\) \{", cpp))
    called = {name for line in cpp.splitlines() if line.strip().startswith("x = ")
              for name in re.findall(r"\b(f(?:_cs\d+)?)\(", line)}
    return called, defined


@pytest.mark.parametrize("body", [
    'f(string sym) => request.security(sym, "60", close[1])\nx = f(syminfo.tickerid)\n',
    'f(sym) => request.security(sym, "60", close)\nx = f(syminfo.tickerid)\n',
    'f(tf) => request.security(syminfo.tickerid, tf, close)\nx = f("60")\n',
    'f(string tf) => request.security(syminfo.tickerid, tf, close[1])\nx = f("60") + f("60")\n',
    'f(string sym, int n) => request.security(sym, "60", high[1]) + n\n'
    'x = f(syminfo.tickerid, 2)\n',
])
def test_one_context_calls_the_emitted_helper(body):
    cpp = transpile(PRELUDE + body + TRADE)
    called, defined = _called_and_defined(cpp)
    assert called == defined == {"f"}, (called, defined)
    compile_cpp(cpp, label="request.security helper with one context")


@pytest.mark.parametrize("body, contexts", [
    ('f(tf) => request.security(syminfo.tickerid, tf, close)\nx = f("60") + f("240")\n',
     ['0, "60", input_tf_, false, false', '1, "240", input_tf_, false, false']),
    ('f(string sym) => request.security(sym, "60", close)\n'
     'x = f(syminfo.tickerid) + f(ticker.heikinashi(syminfo.tickerid))\n',
     ['0, "60", input_tf_, false, false', '1, "60", input_tf_, false, false, true']),
])
def test_differing_contexts_still_clone_the_helper(body, contexts):
    cpp = transpile(PRELUDE + body + TRADE)
    called, defined = _called_and_defined(cpp)
    assert called == defined == {"f_cs0", "f_cs1"}, (called, defined)
    assert re.findall(r"register_security_eval\((.*)\);", cpp) == contexts
    compile_cpp(cpp, label="request.security helper with two contexts")


def test_single_context_helpers_replay_tradingview_tape(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xa2_single_context")
    exits = replay(engine, base, {"probe": Build(source("xa2_single_context", XSYM_A2_TV))})
    tape = tape_exits("xa2_single_context", XSYM_A2_TV)
    assert len(tape) == 336
    # The close exiting at 20:00 UTC on 2025-04-01: the hourly close one
    # requested bar back, the 4-hour close, and the hourly high one requested
    # bar back plus 1.
    assert tape[1743537600000] == "1907.5|1909.12|1916"
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    print(f"single-context helpers: {len(tape)} of {len(tape)} exit Signals equal TradingView's")
