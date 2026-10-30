"""Literal-safe namespace and logical rewrites in TA length resets.

The expression visitor normally handles these lengths. Force its fallback
and inspect constructor resets so a correct comparison emitted elsewhere
cannot hide a changed literal in the reset.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen import CodeGen

_HEADER = (
    '//@version=6\nstrategy("fallback literal passes")\n'
    'mode = input.string("a", "Mode")\n'
    'enabled = input.bool(true, "Enabled")\n'
    'ready = input.bool(false, "Ready")\n'
)
_TRADE = 'if close > x\n    strategy.entry("L", strategy.long)\n'

_ROWS = [
    # "buy and hold" is data, so its conjunction stays the three letters and.
    ('mode == "buy and hold" ? 5 : 10', '== "buy and hold"'),
    # "buy or hold" is data, so its disjunction stays the two letters or.
    ('mode == "buy or hold" ? 5 : 10', '== "buy or hold"'),
    # "not ready" is data, so its prefix stays the three letters not.
    ('mode == "not ready" ? 5 : 10', '== "not ready"'),
    # The literal "math.pi" keeps its namespace spelling rather than M_PI.
    ('mode == "math.pi" ? 5 : 10', '== "math.pi"'),
    # The literal "math.floor" keeps its namespace spelling rather than std::floor.
    ('mode == "math.floor" ? 5 : 10', '== "math.floor"'),
    # The literal "timeframe.period" keeps its property spelling rather than script_tf_.
    ('mode == "timeframe.period" ? 5 : 10', '== "timeframe.period"'),
    # The literal "timeframe.isdaily" keeps its spelling rather than tf_is_daily(script_tf_).
    ('mode == "timeframe.isdaily" ? 5 : 10', '== "timeframe.isdaily"'),
    # base.py:6303: _PINE_LOGICAL_OPS: dict[str, str] = {"and": "&&", "or": "||", "not": "!"}
    ('enabled and ready ? 5 : 10',
     'get_input_bool("Enabled", true) && get_input_bool("Ready", false)'),
    # base.py:6290: "pi": "M_PI", "e": "M_E", "phi": "1.618033988749895",
    ('enabled ? int(math.pi) : 10',
     'get_input_bool("Enabled", true) ? int(M_PI) : 10'),
    # base.py:6273: "period": "script_tf_",
    ('timeframe.period == mode ? 5 : 10',
     'script_tf_ == get_input_string("Mode", std::string("a"))'),
]


@pytest.mark.parametrize("length,expected", _ROWS)
@pytest.mark.parametrize("placement", ["assigned", "inline"])
def test_forced_fallback_rewrites_only_outside_literals(
    monkeypatch, length, expected, placement,
):
    monkeypatch.setattr(CodeGen, "_lower_reset_expr_via_visitor",
                        lambda self, expanded: None)
    if placement == "assigned":
        body = f"len = {length}\nx = ta.sma(close, len)\n"
    else:
        body = f"x = ta.sma(close, {length})\n"
    cpp = transpile(_HEADER + body + _TRADE)
    resets = [
        line.strip()
        for line in cpp.splitlines()
        if re.match(r"_ta_sma_\d+\s*=\s*ta::SMA\(", line.strip())
    ]
    assert resets, "no SMA constructor reset emitted"
    for reset in resets:
        assert expected in reset, reset
