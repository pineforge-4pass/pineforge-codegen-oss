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
    ('mode == "buy and hold" ? 5 : 10', ('== "buy and hold"',)),
    # "buy or hold" is data, so its disjunction stays the two letters or.
    ('mode == "buy or hold" ? 5 : 10', ('== "buy or hold"',)),
    # "not ready" is data, so its prefix stays the three letters not.
    ('mode == "not ready" ? 5 : 10', ('== "not ready"',)),
    # The literal "math.pi" keeps its namespace spelling rather than M_PI.
    ('mode == "math.pi" ? 5 : 10', ('== "math.pi"',)),
    # The literal "math.floor" keeps its namespace spelling rather than std::floor.
    ('mode == "math.floor" ? 5 : 10', ('== "math.floor"',)),
    # The literal "timeframe.period" keeps its property spelling rather than script_tf_.
    ('mode == "timeframe.period" ? 5 : 10', ('== "timeframe.period"',)),
    # The literal "timeframe.isdaily" keeps its spelling rather than tf_is_daily(script_tf_).
    ('mode == "timeframe.isdaily" ? 5 : 10', ('== "timeframe.isdaily"',)),
    # base.py:6303: _PINE_LOGICAL_OPS: dict[str, str] = {"and": "&&", "or": "||", "not": "!"}
    ('enabled and ready ? 5 : 10',
     ('get_input_bool("Enabled", true) && get_input_bool("Ready", false)',)),
    # base.py:6290: "pi": "M_PI", "e": "M_E", "phi": "1.618033988749895",
    ('enabled ? int(math.pi) : 10',
     ('get_input_bool("Enabled", true) ? int(M_PI) : 10',)),
    # base.py:6273: "period": "script_tf_",
    ('timeframe.period == mode ? 5 : 10',
     ('script_tf_ == get_input_string("Mode", std::string("a"))',)),
    # pine_string_literal normalizes single quotes; the literal and stays text.
    (
        "mode == 'buy and hold' and enabled ? 5 : 10",
        (
            '== "buy and hold"',
            # base.py:6353,6582 maps the real conjunction outside the literal.
            '&& get_input_bool("Enabled", true)',
        ),
    ),
    # _STRING_OR_IDENT's escaped-character branch keeps the quoted and as data.
    (
        r'mode == "say \"and\"" and enabled ? 5 : 10',
        (
            # pine_string_literal re-escapes both embedded quotes.
            r'== "say \"and\""',
            # The conjunction after the literal still uses the operator table.
            '&& get_input_bool("Enabled", true)',
        ),
    ),
    # Both literal spans survive; the mode identifier between them is rendered.
    (
        'mode == "buy or hold" or mode == "not ready" ? 5 : 10',
        (
            # base.py:6590-6596 renders the first mode as its input getter.
            'get_input_string("Mode", std::string("a")) == "buy or hold"',
            # diagnostics.py:113-116 groups comparisons; the real or becomes ||.
            ') || (get_input_string("Mode", std::string("a")) == "not ready"',
        ),
    ),
    # The math pass skips the literal span and still lowers the real member.
    (
        '"math.pi" == mode ? math.pi : 10',
        (
            # The fallback keeps literal quotes instead of visiting StringLiteral.
            '"math.pi" == get_input_string("Mode", std::string("a"))',
            # base.py:6340,6577 maps only the unquoted math.pi to M_PI.
            '? M_PI : 10',
        ),
    ),
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
        for fragment in expected:
            assert fragment in reset, reset
