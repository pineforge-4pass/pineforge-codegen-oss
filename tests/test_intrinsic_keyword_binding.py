"""Named intrinsic arguments retain the same expression as positional calls."""

from __future__ import annotations

import csv
from dataclasses import replace
import io
import re

import pytest

from pineforge_codegen import signatures, transpile
from pineforge_codegen.diagnostic_codes import render_diagnostic
from pineforge_codegen.errors import CompileError
from tests._e2e import (
    Build, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)


HEADER = '//@version=6\nstrategy("pf_probe")\n'
POSITIONAL = "math.pow(3.0, 2.0)"
NAMED = (
    "math.pow(base = 3.0, exponent = 2.0)",
    "math.pow(3.0, exponent = 2.0)",
    "math.pow(exponent = 2.0, base = 3.0)",
)


def _source(expression: str) -> str:
    return HEADER + f"pf_value = {expression}\nplot(pf_value)\n"


def _pow_expressions(cpp: str) -> list[str]:
    """Extract calls with their balanced argument lists, including nested calls."""
    calls = []
    for match in re.finditer(r"\bstd::pow\(", cpp):
        depth = 1
        end = match.end()
        while depth and end < len(cpp):
            depth += (cpp[end] == "(") - (cpp[end] == ")")
            end += 1
        assert depth == 0, "unbalanced generated pow call"
        calls.append(cpp[match.start():end])
    assert calls, "no generated pow call"
    return calls


@pytest.mark.parametrize("expression", NAMED)
@pytest.mark.parametrize("context", (
    "{expression}",
    'request.security(syminfo.tickerid, "60", {expression})',
))
def test_named_arguments_emit_the_positional_expression(expression, context):
    expected = transpile(_source(context.format(expression=POSITIONAL)))
    actual = transpile(_source(context.format(expression=expression)))
    assert _pow_expressions(actual) == _pow_expressions(expected)


@pytest.mark.parametrize("body", (
    "pf_value = {expression}\n",
    "pf_unused() => {expression}\npf_value = close\n",
    "if false\n    pf_value = {expression}\n",
    "pf_value = switch\n    close > open => {expression}\n    => 0.0\n",
    'pf_value = request.security(syminfo.tickerid, "60", {expression})\n',
))
@pytest.mark.parametrize("expression", (
    "math.pow(3.0, exp = 2.0)",
    "math.pow(3.0,\n    exp = 1.0 + close)",
))
def test_unknown_keyword_is_refused_at_its_name(body, expression):
    source = HEADER + body.format(expression=expression)
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="keywords.pine")
    diagnostics = [d for d in caught.value.diagnostics if d.code == "PF-E5090"]
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    args = {"function": "math.pow", "name": "exp", "accepted": "base, exponent"}
    assert diagnostic.args == args
    assert (diagnostic.message, diagnostic.hint) == render_diagnostic("PF-E5090", args)
    lines = source.splitlines()
    line = next(index for index, value in enumerate(lines, 1) if "exp =" in value)
    col = lines[line - 1].index("exp =") + 1
    assert diagnostic.location.file == "keywords.pine"
    assert (diagnostic.location.line, diagnostic.location.col,
            diagnostic.location.end_col) == (line, col, col + len("exp"))


def test_positional_after_keyword_keeps_the_argument_order_diagnostic():
    source = _source("math.pow(exponent = 2.0, 3.0)")
    with pytest.raises(CompileError) as caught:
        transpile(source)
    assert [d.code for d in caught.value.diagnostics] == ["PF-E2074"]
    line = source.splitlines()[2]
    assert caught.value.diagnostics[0].location.col == line.index("3.0") + 1


@pytest.mark.parametrize("keyword, positional", (
    ("ta.sma(source = close, length = 5)", "ta.sma(close, 5)"),
    ('input.int(defval = 3, title = "x")', 'input.int(3, "x")'),
    ("math.sqrt(number = 4.0)", "math.sqrt(4.0)"),
))
def test_existing_named_controls_keep_their_positional_emission(keyword, positional):
    assert transpile(_source(keyword)) == transpile(_source(positional))


def test_a_record_without_verified_names_keeps_its_existing_keyword_policy(monkeypatch):
    original = signatures.MATH_FUNCTIONS["sqrt"]
    original_verified = original.keyword_names_verified
    intrinsic = replace(original, keyword_names_verified=False)
    assert intrinsic is not original
    assert intrinsic.keyword_names_verified is False
    with monkeypatch.context() as patch:
        patch.setitem(signatures.MATH_FUNCTIONS, "sqrt", intrinsic)
        assert transpile(_source("math.sqrt(4.0, pf_unused = 2.0)")) == \
            transpile(_source("math.sqrt(4.0)"))
    assert signatures.MATH_FUNCTIONS["sqrt"] is original
    assert original.keyword_names_verified is original_verified


@pytest.mark.parametrize("context", (
    "{expression}",
    'request.security(syminfo.tickerid, "60", {expression})',
))
def test_codegen_also_validates_when_the_support_check_is_disabled(context):
    expression = context.format(expression="math.pow(3.0, exp = 2.0)")
    with pytest.raises(CompileError) as caught:
        transpile(_source(expression), check_support=False)
    assert [d.code for d in caught.value.diagnostics] == ["PF-E5090"]


def test_named_power_values_compile_and_run_like_the_positional_control(tmp_path):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 80)
    tail = (
        "\n// @pf-trace pf_value=pf_value\n"
        "if bar_index % 8 == 0\n"
        '    strategy.entry("pf_long", strategy.long, qty = 1 + pf_value / 100)\n'
        "if bar_index % 8 == 1\n"
        '    strategy.close("pf_long")\n'
    )
    expressions = (POSITIONAL, *NAMED)
    runs = execute_all(engine, feed, tmp_path, {
        str(index): Build(HEADER + f"pf_value = {expression}\n" + tail, trace=True)
        for index, expression in enumerate(expressions)
    })
    reference = ok(runs, "0")
    assert reference.trades["default"]
    assert reference.traces["default"]
    for record in reference.traces["default"]:
        assert record["name"] == "pf_value"
        assert float(record["value"]) == pytest.approx(3.0 ** 2.0)
    rows = list(csv.DictReader(io.StringIO(reference.trades["default"].decode())))
    assert rows
    assert all(float(row["Qty"]) == pytest.approx(1 + (3.0 ** 2.0) / 100)
               for row in rows)
    for index in range(1, len(expressions)):
        actual = ok(runs, str(index))
        assert actual.trades == reference.trades
        assert actual.traces == reference.traces
