"""Conditional-use warnings for ALL na-accept syminfo fields (audit item A7).

``_SYMINFO_SILENT_GAP_FIELDS`` used to cover only 6 fields; the metadata-
backed fields (employees, shareholders, shares_outstanding_*,
recommendations_*, target_price_*) and the na-literal fields (root,
pricescale, minmove) silently returned na with NO warning when used in a
condition. The set is now derived from SYMINFO_MEMBER_MAP so it cannot drift.
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


def _warnings_for(field: str):
    src = (
        '//@version=6\n'
        'strategy("T")\n'
        f'x = na(syminfo.{field}) ? 0.0 : 1.0\n'
    )
    return [
        d for d in _diags(src)
        if d.level == Level.WARNING and f"syminfo.{field}" in d.message
    ]


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
    assert _warnings_for(field), (
        f"syminfo.{field} in a condition must emit a silent-gap warning"
    )


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
