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

# base.py:6641-6648 reparses expanded lengths through _visit_expr.
# visit_expr.py:270: return str(node.value)
# visit_expr.py:337: return f"(({c}) ? ({t}) : ({f}))"
_ROWS = [
    # fast binds to 5 as a parameter; "fast" stays text in the getter comparison.
    (
        'smooth(int fast) => ta.sma(close, mode == "fast" ? fast : 10)\n'
        'x = smooth(5)\n',
        (
            'get_input_string("Mode", std::string("a")) == std::string("fast")',
            # Substitution supplies 5; the visitor parenthesizes both arms.
            '? (5) : (10)',
        ),
    ),
    # fast expands to (5) as a local; "fast" stays text in the getter comparison.
    (
        'smooth() =>\n'
        '    fast = 5\n'
        '    ta.sma(close, mode == "fast" ? fast : 10)\n'
        'x = smooth()\n',
        (
            'get_input_string("Mode", std::string("a")) == std::string("fast")',
            # Re-parsing (5) yields integer 5, then the visitor adds arm brackets.
            '? (5) : (10)',
        ),
    ),
    # pine_string_literal canonicalizes 'fast' to "fast" before substitution.
    (
        "smooth(int fast) => ta.sma(close, mode == 'fast' ? fast : 10)\n"
        'x = smooth(5)\n',
        (
            'get_input_string("Mode", std::string("a")) == std::string("fast")',
            # The parameter's 5 replaces only the unquoted true arm.
            '? (5) : (10)',
        ),
    ),
    # _STRING_OR_IDENT keeps fast after an escaped quote inside the literal.
    (
        'smooth() =>\n'
        '    fast = 5\n'
        '    ta.sma(close, mode == "say \\"fast\\"" ? fast : 10)\n'
        'x = smooth()\n',
        (
            # _cpp_string_escape re-escapes the two embedded quotation marks.
            r'get_input_string("Mode", std::string("a")) == std::string("say \"fast\"")',
            # The local's (5) re-parses as the parenthesized integer true arm.
            '? (5) : (10)',
        ),
    ),
    # The parameter between two literal spans is replaced; both spans stay text.
    (
        'smooth(int fast) => '
        'ta.sma(close, mode == "a" ? fast : mode == "fast" ? 10 : 20)\n'
        'x = smooth(5)\n',
        (
            # The first comparison keeps its own literal "a".
            'get_input_string("Mode", std::string("a")) == std::string("a")',
            # The outer true arm becomes 5 before the nested false-arm ternary.
            '? (5) : (',
            # The second literal's fast is protected from parameter replacement.
            'get_input_string("Mode", std::string("a")) == std::string("fast")',
        ),
    ),
    # Local expansion protects literal math.pi fast beside the real math member.
    (
        'smooth() =>\n'
        '    fast = 5\n'
        '    ta.sma(close, "math.pi fast" == mode ? math.pi : fast)\n'
        'x = smooth()\n',
        (
            # StringLiteral uses std::string; the input still uses its getter.
            'std::string("math.pi fast") == get_input_string("Mode", std::string("a"))',
            # visit_expr.py:847-848 maps real math.pi to M_PI; local fast is 5.
            '? (M_PI) : (5)',
        ),
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
        for fragment in expected:
            assert fragment in reset, reset
