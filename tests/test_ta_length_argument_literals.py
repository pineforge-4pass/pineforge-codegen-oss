"""Callable TA length substitution preserves literal comparison values."""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile

_HEADER = (
    '//@version=6\nstrategy("callable length literals")\n'
    'mode = input.string("a", "Mode")\n'
)
_TRADE = 'if close > x\n    strategy.entry("L", strategy.long)\n'

_ROWS = [
    # fast binds to 5 as a parameter; "fast" stays text in the getter comparison.
    (
        'smooth(int fast) => ta.sma(close, mode == "fast" ? fast : 10)\n'
        'x = smooth(5)\n',
        'get_input_string("Mode", std::string("a")) == std::string("fast")',
    ),
    # fast expands to (5) as a local; "fast" stays text in the getter comparison.
    (
        'smooth() =>\n'
        '    fast = 5\n'
        '    ta.sma(close, mode == "fast" ? fast : 10)\n'
        'x = smooth()\n',
        'get_input_string("Mode", std::string("a")) == std::string("fast")',
    ),
]


@pytest.mark.parametrize("body,expected", _ROWS)
def test_callable_length_substitution_keeps_string_literals(body, expected):
    cpp = transpile(_HEADER + body + _TRADE)
    resets = [
        line.strip()
        for line in cpp.splitlines()
        if re.match(r"_ta_sma_\d+\s*=\s*ta::SMA\(", line.strip())
    ]
    assert resets, "no SMA constructor reset emitted"
    for reset in resets:
        assert expected in reset, reset
