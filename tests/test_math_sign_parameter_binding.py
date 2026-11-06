"""math.sign binds TradingView's required ``number`` parameter.

The excluded-signature-control TV receipt records CE10120 for ``x`` and
CE10165 for missing ``number``. Native expectations below are calculated
from the synthetic input bars; they are not TradingView value evidence.
"""

from __future__ import annotations

import math

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.diagnostic_codes import render_diagnostic
from pineforge_codegen.errors import CompileError, Level, Phase
from tests.test_security_helper_arg_history import _assert_values
from tests.test_security_helper_local_var import _compile_and_run


_HEADER = '//@version=6\nstrategy("sign parameter binding")\n'


def _source(expression: str) -> str:
    return _HEADER + f"pf_value = {expression}\nplot(pf_value)\n"


@pytest.mark.parametrize("argument", [
    "close - open",
    "float(na)",
    "ta.sma(close, 3)",
    "pf_difference(close)",
])
@pytest.mark.parametrize("context", [
    pytest.param("{call}", id="chart"),
    pytest.param(
        'request.security(syminfo.tickerid, "60", {call})',
        id="requested",
    ),
])
def test_number_keyword_retains_the_supplied_expression(argument, context):
    definitions = "pf_difference(float sample) => sample[1] - sample\n"
    positional = context.format(call=f"math.sign({argument})")
    named = context.format(call=f"math.sign(number = {argument})")
    assert transpile(_HEADER + definitions + f"pf_value = {named}\nplot(pf_value)\n") == \
        transpile(_HEADER + definitions + f"pf_value = {positional}\nplot(pf_value)\n")


# Positive prices give negative, positive and zero differences of varied sizes.
# The first bar deliberately passes na to sign.
_BARS = (
    (8.0, 11.0),
    (13.0, 7.0),
    (5.0, 5.0),
    (4.0, 12.0),
    (9.0, 8.75),
    (2.0, 2.5),
    (6.0, 6.0),
)

_CHART_OBSERVER = r"""
#include <iomanip>
#include <iostream>

struct SignObserver : GeneratedStrategy {
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        std::cout << pf_value << '\n';
    }
};

int main() {
    std::cout << std::setprecision(17);
    SignObserver strategy;
    Bar bars[] = {
"""


@pytest.mark.parametrize("argument", [
    pytest.param("pf_sample", id="positional"),
    pytest.param("number = pf_sample", id="named-number"),
])
def test_chart_sign_values_follow_each_input_bar(argument):
    source = _HEADER + (
        "pf_sample = bar_index == 0 ? float(na) : close - open\n"
        f"pf_value = math.sign({argument})\n"
        "plot(pf_value)\n"
    )
    bars = [
        "        Bar{"
        + ", ".join(repr(value) for value in (
            open_, max(open_, close), min(open_, close), close, 1.0,
        ))
        + f", {index * 15 * 60 * 1000}}}"
        for index, (open_, close) in enumerate(_BARS)
    ]
    driver = (
        _CHART_OBSERVER
        + ",\n".join(bars)
        + "\n    };\n"
        + f'    strategy.run(bars, {len(_BARS)}, "15", "15");\n'
        + "    return 0;\n}\n"
    )
    output = _compile_and_run(transpile(source) + driver)
    expected = [
        math.nan if index == 0 else float((close > open_) - (close < open_))
        for index, (open_, close) in enumerate(_BARS)
    ]
    _assert_values([float(line) for line in output.splitlines()], expected)


@pytest.mark.parametrize("body", [
    pytest.param("pf_value = {call}\nplot(pf_value)\n", id="chart"),
    pytest.param(
        'pf_value = request.security(syminfo.tickerid, "60", {call})\nplot(pf_value)\n',
        id="requested",
    ),
    pytest.param(
        "pf_unused() => {call}\npf_value = close\nplot(pf_value)\n",
        id="unused-helper",
    ),
    pytest.param(
        "pf_value = switch\n    close > open => {call}\n    => 0.0\nplot(pf_value)\n",
        id="switch-arm",
    ),
])
@pytest.mark.parametrize("call,code,message,at", [
    pytest.param(
        "math.sign(x = close - open)", "PF-E5090", "x", "x",
        id="invalid-x",
    ),
    pytest.param(
        "math.sign(pf_unknown = open * -2)", "PF-E5090", "pf_unknown", "pf_unknown",
        id="unknown-name",
    ),
    pytest.param(
        "math.sign(close, pf_unknown = open)", "PF-E5090", "pf_unknown", "pf_unknown",
        id="unknown-after-positional",
    ),
    pytest.param(
        "math.sign(close, number = open)", "PF-E1088", "got 'number' twice", "open",
        id="duplicate-position-and-name",
    ),
    pytest.param(
        "math.sign()", "PF-E1088", "is missing its argument 'number'", "math",
        id="missing-number",
    ),
    pytest.param(
        "math.sign(close, open)", "PF-E1088", "takes at most 1 arguments, got 2", "open",
        id="excess-positional",
    ),
])
def test_invalid_bindings_use_the_existing_located_signature_error(body, call, code, message, at):
    source = _HEADER + body.format(call=call)
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="sign_binding.pine")
    errors = [d for d in caught.value.diagnostics if d.level == Level.ERROR]
    assert len(errors) == 1, errors
    diagnostic = errors[0]
    assert diagnostic.code == code
    assert diagnostic.phase == Phase.ANALYZER
    if code == "PF-E5090":
        args = {"function": "math.sign", "name": message, "accepted": "number"}
        assert diagnostic.args == args
        assert (diagnostic.message, diagnostic.hint) == render_diagnostic(code, args)
    else:
        assert f"math.sign {message}" in diagnostic.message
        assert "(TradingView: math.sign(number))." in diagnostic.message
    start = source.index(call) + call.index(at)
    line = source.count("\n", 0, start) + 1
    col = start - source.rfind("\n", 0, start)
    assert diagnostic.location.file == "sign_binding.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)


def test_repeated_number_keyword_keeps_the_parser_diagnostic():
    source = _source("math.sign(number = close, number = open)")
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="sign_binding.pine")
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E0035"
    assert diagnostic.phase == Phase.PARSER
    assert diagnostic.message == "duplicate keyword argument 'number'"
    line = source.splitlines()[2]
    col = line.rindex("number") + 1
    assert diagnostic.location.file == "sign_binding.pine"
    assert (diagnostic.location.line, diagnostic.location.col,
            diagnostic.location.end_col) == (3, col, col + len("number"))


def test_positional_after_number_keeps_the_argument_order_diagnostic():
    source = _source("math.sign(number = close, open)")
    with pytest.raises(CompileError) as caught:
        transpile(source)
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E2074"
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.location.line == 3
    assert diagnostic.location.col == source.splitlines()[2].rindex("open") + 1
