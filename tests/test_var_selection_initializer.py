"""A ``var`` declaration whose initializer is a ``switch`` or an ``if``
expression.

Pine runs a ``var`` initializer once, on the first bar that reaches the
declaration. A primitive ``var`` with a selection initializer rendered its
value as ``/* unknown */`` inside the one-shot block, which did not compile:
``var string dashPos = switch dashPosInput`` was the last blocker of
job-1361-shitholed-lema-system-v2-4 once its request.security payload
lowered. The selection now runs inside the block, the flag set after it, as
a persistent map or matrix initializer's already did; a history-referenced
one (a callable's ``var`` read at ``[1]``) selects into a local of the
member's own element type (string, int, drawing) and replaces its current
slot. A top-level ``var`` read at ``[k]`` keeps the legacy first-bar
preamble, whose analyzer spelling of a selection (``<?>``) never compiled.

TradingView's tape of ``xa2_var_selection`` (``fixtures/xsym_a2_tv``,
BINANCE:ETHUSDT.P 15) spells every such ``var`` on each close.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp, narrowing_diagnostics
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits


XSYM_A2_TV = Path(__file__).parent / "fixtures" / "xsym_a2_tv"
PRELUDE = ('//@version=6\nstrategy("T")\n'
           'mode = input.string("A", "Mode", options = ["A", "B", "C"])\n')
TRADE = 'if close > open\n    strategy.entry("L", strategy.long)\n'


@pytest.mark.parametrize("body", [
    'var string p = switch mode\n    "A" => position.top_right\n    => position.bottom_left\n'
    'var t = table.new(p, 1, 1)\n',
    'var float p = switch mode\n    "A" => open\n    "B" => close\n    => high\nplot(p)\n',
    'var int p = switch mode\n    "A" => 1\n    "B" => 2\nplot(p)\n',
    'var bool p = switch\n    mode == "A" => true\n    => false\nplot(p ? 1 : 0)\n',
    'var float p = if close > open\n    close\nelse\n    open\nplot(p)\n',
    # Nested in a block, and a callable's, read at [1].
    'if close > open\n    var string p = switch mode\n        "A" => "a"\n        => "b"\n'
    '    label.new(bar_index, high, p)\n',
    'f() =>\n    var float p = switch mode\n        "A" => low\n        => high\n    p[1]\n'
    'plot(f())\n',
    # A callable's history-referenced string, int and drawing: the selection
    # goes through a local of the member's own element type.
    'f() =>\n    var string p = switch mode\n        "A" => "alpha"\n        => "beta"\n'
    '    p[1]\nplot(f() == "beta" ? 1 : 0)\n',
    'f() =>\n    var int p = switch mode\n        "A" => math.round(close)\n        => 0\n'
    '    p[1]\nplot(f())\n',
    'f() =>\n    var line p = if close > open\n        line.new(bar_index, low, bar_index + 1, high)\n'
    '    p[1]\nplot(na(f()) ? 0 : 1)\n',
])
def test_selection_initializer_runs_once(body):
    cpp = transpile(PRELUDE + body + TRADE)
    assert "/* unknown */" not in cpp
    # The one-shot block holds the selection; its flag is set after it.
    block = re.search(
        r"if \(!(?:this->)?(_pf_var_init_p\w*)\) \{\n(.*?)\n\s*(?:this->)?\1 = true;",
        cpp, re.S)
    assert block is not None, cpp
    assert "__switch_val_" in block.group(2) or "if (" in block.group(2)
    compile_cpp(cpp, label="var with a selection initializer")
    assert narrowing_diagnostics(cpp, label="var with a selection initializer") == []


def test_var_selection_replays_tradingview_tape(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xa2_var_selection")
    exits = replay(engine, base, {"probe": Build(source("xa2_var_selection", XSYM_A2_TV))})
    tape = tape_exits("xa2_var_selection", XSYM_A2_TV)
    assert len(tape) == 336
    # Every close reads the first bar's selections: its close under the
    # default mode "B", "beta", the first bar's direction, and the callable
    # var's low one bar back.
    assert set(tape.values()) == {"1821.47|beta|-1|1819.01"}
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    print(f"var selection initializers: {len(tape)} of {len(tape)} exit Signals equal "
          "TradingView's")
