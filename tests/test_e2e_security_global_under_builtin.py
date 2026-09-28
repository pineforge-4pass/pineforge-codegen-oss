"""A global read under a builtin call in a request.security payload is
evaluated on the requested bar.

TradingView evaluates a payload's globals in the requested context, wherever
they sit: ``nz(s)`` reads the requested bar's ``s`` as the bare payload
``s`` does. PineForge's builder inlines a bare global on the requested bar,
but a builtin call it does not lower itself (``nz``) went to the expression
visitor, which read the global's chart member: silently the chart's value
(P6 of lane CG-SECURITY-2's pre-existing defects), and beside a user call
the builder inlined (``nz(s) + nz(f())``) the evaluator fell back to the
chart for both. ``_security_fallback_owns_global`` now hands such a global
back to the builder. Its history under a builtin (``nz(s[1])``) stays the
visitor's chart series, as the builder keeps no requested history of a
global's expression, and now warns.

TradingView's tape of ``fixtures/open_items_tv/sec_global_builtin`` (lab tv
--no-note, BINANCE:ETHUSDT.P 15, 265 closes) spells ``nz(s)``, ``nz(g)``
with ``g`` a ta.sma, ``nz(s, -1) + 1``, ``math.max(nz(s), 0)``,
``nz(s) + nz(f())`` and the bare ``s`` on every close; the replay compares
all 265.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile_full
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay
from tests._tv_tapes import FIXTURES, exits_by_time


def test_the_global_under_builtin_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    name = "sec_global_builtin"
    engine_exits = replay(engine, tmp_path, {
        name: Build((FIXTURES / f"{name}.pine").read_text())})[name]
    tape = exits_by_time(name)
    assert len(tape) == 265
    missed = mismatches(tape, engine_exits)
    assert not missed, f"{len(missed)} of {len(tape)}: {missed[:3]}"


HEAD = ('//@version=6\nstrategy("global under builtin", overlay = true)\n'
        "s = close - open\ng = ta.sma(close, 3)\n")


def _payload(expr: str) -> tuple[str, list[str]]:
    result = transpile_full(
        HEAD + f'v = request.security(syminfo.tickerid, "60", {expr})\n'
        "if v > 0\n    strategy.entry(\"L\", strategy.long)\n")
    cpp = result["cpp"]
    body = cpp[cpp.index("void _eval_security_0"):]
    body = body[:body.index("\n    }\n")]
    return body, [d.message for d in result["diagnostics"] if "request.security" in d.message]


@pytest.mark.parametrize("expr", ["nz(s)", "nz(g)", "nz(s, -1.0) + 1"])
def test_a_global_under_a_builtin_reads_the_requested_bar(expr):
    body, warnings = _payload(expr)
    assert "bar.close" in body
    assert "(s)" not in body and "(g)" not in body
    assert warnings == []


@pytest.mark.parametrize("name", ["s", "g"])
def test_a_globals_history_under_a_builtin_warns(name):
    _body, warnings = _payload(f"nz({name}[1])")
    assert any(f"reads the history of '{name}' under a builtin call" in w for w in warnings)
