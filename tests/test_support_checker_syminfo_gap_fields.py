"""Warnings for symbol fields that can be missing, in and out of conditions."""
import pytest

from pineforge_codegen import transpile_full
from pineforge_codegen.diagnostic_codes import render
from pineforge_codegen.errors import Level
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.support_checker import SupportChecker


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


@pytest.mark.parametrize("condition", [
    pytest.param("na(syminfo.pricescale)", id="na-test-is-true"),
    pytest.param("syminfo.pricescale > 0", id="direct-comparison-is-false"),
])
def test_condition_warning_describes_the_field_read(condition):
    source = (
        '//@version=6\nstrategy("Missing symbol field in a condition")\n'
        f'if {condition}\n    strategy.entry("L", strategy.long)\n'
    )
    diagnostics = transpile_full(source)["diagnostics"]
    [warning] = [d for d in diagnostics if d.code == "PF-W1522"]
    expected = "In this condition, PineForge returns na for syminfo.pricescale."
    assert (warning.level, warning.args, warning.message) == (
        Level.WARNING, {"member": "pricescale"}, expected,
    )
    assert render(warning.user_message, warning.args) == expected


def test_plain_warning_does_not_claim_the_result_of_nz():
    source = (
        '//@version=6\nstrategy("Replace a missing symbol field")\n'
        'replacement = nz(syminfo.pricescale, 1.0)\nplot(replacement)\n'
    )
    diagnostics = transpile_full(source)["diagnostics"]
    [warning] = [d for d in diagnostics if d.code == "PF-W1521"]
    expected = "PineForge returns na for syminfo.pricescale."
    assert (warning.level, warning.args, warning.message) == (
        Level.WARNING, {"member": "pricescale"}, expected,
    )
    assert render(warning.user_message, warning.args) == expected


def test_symbol_field_that_the_run_can_supply_warns_about_missing_metadata():
    source = (
        '//@version=6\nstrategy("Symbol field supplied with the run")\n'
        'if syminfo.employees > 0\n    strategy.entry("L", strategy.long)\n'
    )
    diagnostics = transpile_full(source)["diagnostics"]
    [warning] = [
        d for d in diagnostics if d.code in {"PF-W1520", "PF-W1521", "PF-W1522"}
    ]
    assert (warning.code, warning.level, warning.args, warning.message) == (
        "PF-W1520", Level.WARNING, {"member": "employees"},
        'syminfo.employees is na unless the run declares a value for '
        '"employees" in its symbol metadata.',
    )
    assert render(warning.user_message, warning.args) == (
        "PineForge returns na for syminfo.employees unless the run supplies a value."
    )


def test_strategy_without_symbol_field_reads_matches_base_cpp(tmp_path):
    from tests._e2e import reference_codegen, transpile_json

    pine = tmp_path / "unrelated.pine"
    pine.write_text(
        '//@version=6\nstrategy("Strategy without symbol field reads")\n'
        'if close > open\n    strategy.entry("L", strategy.long)\n',
        encoding="utf-8",
    )
    head = transpile_json(pine)
    head_cpp = (head.get("cpp") or "").encode("utf-8")
    base_sha = "9b17c40e074a1bcab7ec581b5e71f13b171deb42"
    base_root = reference_codegen(base_sha)
    if base_root is None:
        pytest.skip(
            f"NOT COMPARED: base {base_sha} is unavailable "
            f"(base C++=missing, candidate C++={len(head_cpp)} bytes)"
        )
    base = transpile_json(pine, root=base_root)
    base_cpp = (base.get("cpp") or "").encode("utf-8")
    assert base["ok"] and base_cpp and head["ok"] and head_cpp, (
        "NOT COMPARED: transpilation failed or produced no C++ "
        f"(base: ok={base['ok']}, bytes={len(base_cpp)}; "
        f"candidate: ok={head['ok']}, bytes={len(head_cpp)})"
    )
    assert head_cpp == base_cpp, f"generated C++ differs from base {base_sha}"


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
