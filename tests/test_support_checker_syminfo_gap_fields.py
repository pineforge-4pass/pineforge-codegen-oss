"""Silent-gap warnings for ALL na-accept syminfo fields (audit item A7).

``_SYMINFO_SILENT_GAP_FIELDS`` used to cover only 6 fields; the metadata-
backed fields (employees, shareholders, shares_outstanding_*,
recommendations_*, target_price_*) and the na-literal fields (root,
pricescale, minmove) silently returned na with NO warning. The set is now
derived from SYMINFO_MEMBER_MAP so it cannot drift.

The warning used to fire ONLY inside an if/ternary condition, so a field read
directly in a plain expression (``x = syminfo.pricescale * 2``) slipped out as
na with no signal. The gate now fires for EVERY read — conditional AND plain.
"""
import pytest

from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.support_checker import SupportChecker
from pineforge_codegen.errors import Level


def _diags(src: str):
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens).parse()
    return SupportChecker(ast).check()


def _conditional_warnings_for(field: str):
    src = (
        '//@version=6\n'
        'strategy("T")\n'
        f'x = na(syminfo.{field}) ? 0.0 : 1.0\n'
    )
    return [
        d for d in _diags(src)
        if d.level == Level.WARNING and f"syminfo.{field}" in d.message
    ]


def _plain_warnings_for(field: str):
    """Field read in a plain (non-conditional) expression."""
    src = (
        '//@version=6\n'
        'strategy("T")\n'
        f'x = syminfo.{field}\n'
    )
    return [
        d for d in _diags(src)
        if d.level == Level.WARNING and f"syminfo.{field}" in d.message
    ]


# Back-compat alias for the original helper name.
_warnings_for = _conditional_warnings_for


NA_ACCEPT_FIELDS = [
    # original 6
    "sector", "industry", "isin",
    "expiration_date", "current_contract", "mincontract",
    # na-literal fields
    "root", "pricescale", "minmove",
    # metadata-backed fields
    "employees", "shareholders",
    "shares_outstanding_float", "shares_outstanding_total",
    "recommendations_buy", "recommendations_buy_strong",
    "recommendations_hold", "recommendations_sell",
    "recommendations_sell_strong", "recommendations_total",
    "recommendations_date",
    "target_price_average", "target_price_high", "target_price_low",
    "target_price_median", "target_price_date", "target_price_estimates",
]


@pytest.mark.parametrize("field", NA_ACCEPT_FIELDS)
def test_conditional_use_warns(field):
    assert _conditional_warnings_for(field), (
        f"syminfo.{field} in a condition must emit a silent-gap warning"
    )


@pytest.mark.parametrize("field", NA_ACCEPT_FIELDS)
def test_plain_expression_use_warns(field):
    """A silent-gap field read OUTSIDE any conditional must warn too — the
    bug was that such reads slipped out as na with no signal."""
    warns = _plain_warnings_for(field)
    assert warns, (
        f"syminfo.{field} in a plain expression must emit a silent-gap warning"
    )
    # Stays a WARNING (not escalated to ERROR).
    assert all(d.level == Level.WARNING for d in warns)


def test_plain_arithmetic_use_warns():
    """The exact shape from the bug report: field used directly in a number."""
    src = (
        '//@version=6\n'
        'strategy("T")\n'
        'x = syminfo.pricescale * 2.0\n'
    )
    warns = [
        d for d in _diags(src)
        if d.level == Level.WARNING and "syminfo.pricescale" in d.message
    ]
    assert warns, "syminfo.pricescale * 2.0 must warn (read flows out as na)"
    errs = [d for d in _diags(src) if d.level == Level.ERROR]
    assert errs == [], "silent-gap fields warn, they do not error"


@pytest.mark.parametrize("field", ["mintick", "tickerid", "currency", "timezone"])
def test_real_fields_do_not_warn(field):
    src = (
        '//@version=6\n'
        'strategy("T")\n'
        f's = syminfo.{field}\n'
    )
    diags = [
        d for d in _diags(src)
        if d.level == Level.WARNING and "returns na" in d.message
    ]
    assert not diags
