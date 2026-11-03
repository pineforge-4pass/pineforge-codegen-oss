"""TV-backed math keyword contracts and independent native value checks.

Per-function TV probes accept the canonical unary names and refuse ``x``.
Round's positive receipt covers ``number, precision`` together; positional
overloads remain controls. All six max/min/avg probes, including the candidate
``number0, number1`` forms, refuse every keyword with CE10119. Native values
below use independent bar oracles and are separate from those TV receipts.
"""

from __future__ import annotations

import math

import pytest

from pineforge_codegen import signatures, transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import FuncCall, VarDecl
from pineforge_codegen.builtin_keywords import bind_builtin_keywords
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.diagnostic_codes import render_diagnostic
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests.test_security_helper_arg_history import _assert_values
from tests.test_security_helper_local_var import _compile_and_run


_HEADER = '//@version=6\nstrategy("math keyword parameters")\n'

# Independent of the product registry: changing a record cannot change these
# expectations. None of the positive domains relies on a domain-error result.
_UNARY_CONTRACTS = (
    ("abs", "number", "pf_sample", abs),
    ("ceil", "number", "pf_sample", math.ceil),
    ("floor", "number", "pf_sample", math.floor),
    ("round", "number", "pf_sample",
     lambda value: math.copysign(math.floor(abs(value) + 0.5), value)),
    ("round_to_mintick", "number", "pf_sample",
     lambda value: math.copysign(math.floor(abs(value) / 0.25 + 0.5), value) * 0.25),
    ("sign", "number", "pf_sample",
     lambda value: float((value > 0) - (value < 0))),
    ("sqrt", "number", "pf_sample * pf_sample + 0.25",
     lambda value: math.sqrt(value * value + 0.25)),
    ("exp", "number", "pf_sample", math.exp),
    ("log", "number", "math.abs(pf_sample) + 0.25",
     lambda value: math.log(abs(value) + 0.25)),
    ("log10", "number", "math.abs(pf_sample) + 0.25",
     lambda value: math.log10(abs(value) + 0.25)),
    ("sin", "angle", "pf_sample / 2.0", lambda value: math.sin(value / 2.0)),
    ("cos", "angle", "pf_sample / 2.0", lambda value: math.cos(value / 2.0)),
    ("tan", "angle", "pf_sample / 2.0", lambda value: math.tan(value / 2.0)),
    ("asin", "angle", "pf_sample / 2.0", lambda value: math.asin(value / 2.0)),
    ("acos", "angle", "pf_sample / 2.0", lambda value: math.acos(value / 2.0)),
    ("atan", "angle", "pf_sample", math.atan),
    ("todegrees", "radians", "pf_sample / 2.0",
     lambda value: math.degrees(value / 2.0)),
    ("toradians", "degrees", "pf_sample * 90.0",
     lambda value: math.radians(value * 90.0)),
)
_CONTRACT_IDS = [row[0] for row in _UNARY_CONTRACTS]


def _parse(source: str):
    return Parser(Lexer(source, filename="math_keywords.pine").tokenize(),
                  source=source).parse()


def _generate(source: str, path: str) -> str:
    if path == "direct":
        return CodeGen(Analyzer(_parse(source), filename="math_keywords.pine").analyze()).generate()
    return transpile(source, filename="math_keywords.pine",
                     check_support=path == "public")


def _source(expression: str) -> str:
    return _HEADER + f"pf_value = {expression}\nplot(pf_value)\n"


@pytest.mark.parametrize("short_name,keyword,argument,oracle",
                         _UNARY_CONTRACTS, ids=_CONTRACT_IDS)
def test_registry_declares_the_complete_unary_keyword_set(
    short_name, keyword, argument, oracle,
):
    record = signatures.MATH_FUNCTIONS[short_name]
    expected = [[keyword]]
    if short_name == "round":
        expected.append([keyword, "precision"])
    assert [[param.name for param in sig.params] for sig in record.signatures] == expected
    assert record.keyword_names_verified is True


@pytest.mark.parametrize("short_name,keyword,argument,oracle",
                         _UNARY_CONTRACTS, ids=_CONTRACT_IDS)
@pytest.mark.parametrize("path", ("public", "unchecked", "direct"))
@pytest.mark.parametrize("context", (
    pytest.param("{call}", id="chart"),
    pytest.param('request.security(syminfo.tickerid, "60", {call})', id="requested"),
))
def test_canonical_keyword_keeps_the_positional_expression(
    short_name, keyword, argument, oracle, path, context,
):
    prefix = _HEADER + "pf_sample = close - open\n"
    positional = context.format(call=f"math.{short_name}({argument})")
    named = context.format(call=f"math.{short_name}({keyword} = {argument})")
    assert _generate(prefix + f"pf_value = {named}\nplot(pf_value)\n", path) == \
        _generate(prefix + f"pf_value = {positional}\nplot(pf_value)\n", path)


@pytest.mark.parametrize("short_name,keyword,argument,oracle",
                         _UNARY_CONTRACTS, ids=_CONTRACT_IDS)
@pytest.mark.parametrize("path", ("public", "unchecked", "direct"))
@pytest.mark.parametrize("context", (
    pytest.param("{call}", id="chart"),
    pytest.param('request.security(syminfo.tickerid, "60", {call})', id="requested"),
))
@pytest.mark.parametrize("wrong_name", ("x", "pf_unknown"))
def test_wrong_keyword_is_refused_at_its_name(
    short_name, keyword, argument, oracle, path, context, wrong_name,
):
    call = f"math.{short_name}(\n    {wrong_name} = close - open)"
    source = _source(context.format(call=call))
    start = source.index(f"{wrong_name} =")
    line = source.count("\n", 0, start) + 1
    col = start - source.rfind("\n", 0, start)
    with pytest.raises(CompileError) as caught:
        _generate(source, path)
    diagnostic, = [d for d in caught.value.diagnostics if d.level == Level.ERROR]
    args = {"function": f"math.{short_name}", "name": wrong_name,
            "accepted": "number, precision" if short_name == "round" else keyword}
    assert diagnostic.code == "PF-E5090"
    assert diagnostic.args == args
    assert diagnostic.phase == Phase.ANALYZER
    assert (diagnostic.message, diagnostic.hint) == render_diagnostic("PF-E5090", args)
    assert diagnostic.location.file == "math_keywords.pine"
    assert (diagnostic.location.line, diagnostic.location.col,
            diagnostic.location.end_col) == (line, col, col + len(wrong_name))


@pytest.mark.parametrize("short_name,keyword,argument,oracle",
                         _UNARY_CONTRACTS, ids=_CONTRACT_IDS)
@pytest.mark.parametrize("body", (
    "pf_unused() => {call}\npf_value = close\n",
    "pf_value = switch\n    close > open => {call}\n    => 0.0\n",
))
def test_unused_and_conditional_calls_still_refuse_wrong_names(
    short_name, keyword, argument, oracle, body,
):
    source = _HEADER + body.format(call=f"math.{short_name}(x = close)")
    with pytest.raises(CompileError) as caught:
        transpile(source)
    diagnostic, = [d for d in caught.value.diagnostics if d.level == Level.ERROR]
    assert diagnostic.code == "PF-E5090"
    assert diagnostic.args == {
        "function": f"math.{short_name}", "name": "x",
        "accepted": "number, precision" if short_name == "round" else keyword,
    }
    assert (diagnostic.message, diagnostic.hint) == render_diagnostic(
        "PF-E5090", diagnostic.args,
    )


_ROUND_FORMS = (
    "pf_sample, pf_precision",
    "number = pf_sample, precision = pf_precision",
    "precision = pf_precision, number = pf_sample",
    "pf_sample, precision = pf_precision",
)


@pytest.mark.parametrize("arguments", _ROUND_FORMS)
@pytest.mark.parametrize("path", ("public", "unchecked", "direct"))
def test_round_precision_keeps_both_arguments(arguments, path):
    prefix = _HEADER + "pf_sample = close - open\npf_precision = bar_index % 3 + 1\n"
    expected = prefix + "pf_value = math.round(pf_sample, pf_precision)\nplot(pf_value)\n"
    actual = prefix + f"pf_value = math.round({arguments})\nplot(pf_value)\n"
    assert _generate(actual, path) == _generate(expected, path)


@pytest.mark.parametrize("arguments,wrong_name", (
    ("x = close - open, precision = 2", "x"),
    ("number = close - open, digits = 2", "digits"),
    ("precision = 2, number0 = close - open", "number0"),
))
@pytest.mark.parametrize("path", ("public", "unchecked", "direct"))
@pytest.mark.parametrize("context", (
    pytest.param("{call}", id="chart"),
    pytest.param('request.security(syminfo.tickerid, "60", {call})', id="requested"),
))
def test_round_precision_refuses_wrong_parameter_names(arguments, wrong_name, path, context):
    source = _source(context.format(call=f"math.round({arguments})"))
    with pytest.raises(CompileError) as caught:
        _generate(source, path)
    diagnostic, = [d for d in caught.value.diagnostics if d.level == Level.ERROR]
    assert diagnostic.code == "PF-E5090"
    assert diagnostic.args == {"function": "math.round", "name": wrong_name,
                               "accepted": "number, precision"}
    assert (diagnostic.message, diagnostic.hint) == render_diagnostic(
        "PF-E5090", diagnostic.args,
    )
    start = source.index(f"{wrong_name} =")
    line = source.count("\n", 0, start) + 1
    col = start - source.rfind("\n", 0, start)
    assert diagnostic.location.file == "math_keywords.pine"
    assert (diagnostic.location.line, diagnostic.location.col,
            diagnostic.location.end_col) == (line, col, col + len(wrong_name))


def test_normalization_keeps_the_actual_nodes_in_parameter_order():
    program = _parse(_source("math.round(precision = bar_index % 3, number = close - open)"))
    declaration, = [node for node in program.body
                    if isinstance(node, VarDecl) and node.name == "pf_value"]
    call = declaration.value
    assert isinstance(call, FuncCall)
    number = call.kwargs["number"]
    precision = call.kwargs["precision"]
    bind_builtin_keywords(program)
    assert len(call.args) == 2
    assert call.args[0] is number
    assert call.args[1] is precision
    assert call.kwargs == {}


# All prices are positive; differences cover na, negative, zero and positive.
# No finite sample is a rounding tie at any exercised precision or tick size.
_BARS = ((8.0, 8.31), (13.0, 12.27), (5.0, 5.0), (4.0, 4.29),
         (9.0, 9.84), (2.0, 1.79), (6.0, 6.17))


def _round_at(value: float, precision: int) -> float:
    scale = 10 ** precision
    return math.copysign(math.floor(abs(value) * scale + 0.5), value) / scale


def _native_values(source: str, members: list[str], path: str):
    prints = " << ' ' << ".join(
        f"(is_na({member}) ? na<double>() : static_cast<double>({member}))"
        for member in members
    )
    bars = ",\n".join(
        "        Bar{" + ", ".join(repr(value) for value in (
            open_, max(open_, close), min(open_, close), close, 1.0,
        )) + f", {index * 15 * 60 * 1000}}}"
        for index, (open_, close) in enumerate(_BARS)
    )
    driver = (
        "#include <iomanip>\n#include <iostream>\n"
        "struct MathObserver : GeneratedStrategy {\n"
        "    void on_source_bar(const Bar& bar) override {\n"
        "        syminfo_.mintick = 0.25;\n"
        "        GeneratedStrategy::on_source_bar(bar);\n"
        f"        std::cout << {prints} << '\\n';\n"
        "    }\n};\n"
        "int main() {\n"
        "    std::cout << std::setprecision(17);\n"
        "    MathObserver strategy;\n"
        f"    Bar bars[] = {{\n{bars}\n    }};\n"
        f'    strategy.run(bars, {len(_BARS)}, "15", "15");\n'
        "    return 0;\n}\n"
    )
    return [[float(value) for value in line.split()] for line in
            _compile_and_run(_generate(source, path) + driver).splitlines()]


@pytest.mark.parametrize("path", ("public", "unchecked", "direct"))
def test_native_unary_and_round_values_follow_independent_bar_oracles(path):
    source = _HEADER + (
        "pf_sample = bar_index == 0 ? float(na) : close - open\n"
        "pf_precision = bar_index % 3 + 1\n"
    )
    members = []
    oracles = []
    for short_name, keyword, argument, oracle in _UNARY_CONTRACTS:
        for suffix, arguments in (("pos", argument), ("named", f"{keyword} = {argument}")):
            member = f"pf_{short_name}_{suffix}"
            members.append(member)
            oracles.append(lambda value, index, oracle=oracle: oracle(value))
            source += f"{member} = math.{short_name}({arguments})\n"
    for index, arguments in enumerate(_ROUND_FORMS):
        member = f"pf_round_precision_{index}"
        members.append(member)
        oracles.append(lambda value, index: _round_at(value, index % 3 + 1))
        source += f"{member} = math.round({arguments})\n"
    source += f"plot({members[-1]})\n"
    rows = _native_values(source, members, path)
    assert len(rows) == len(_BARS)
    for index, ((open_, close), row) in enumerate(zip(_BARS, rows)):
        assert len(row) == len(members)
        expected = [math.nan if index == 0 else oracle(close - open_, index)
                    for oracle in oracles]
        _assert_values(row, expected)


@pytest.mark.parametrize("path", ("public", "unchecked", "direct"))
def test_native_unary_arguments_are_evaluated_once(path):
    source = _HEADER + (
        "pf_mark(array<float> events, float value) =>\n"
        "    array.push(events, value)\n"
        "    value\n"
        "pf_events = array.new_float()\n"
        "pf_sample = close - open\n"
    )
    members = []
    for short_name, keyword, argument, oracle in _UNARY_CONTRACTS:
        member = f"pf_{short_name}_once"
        members.append(member)
        source += f"{member} = math.{short_name}({keyword} = pf_mark(pf_events, {argument}))\n"
    source += "pf_count = array.size(pf_events)\nplot(pf_count)\n"
    rows = _native_values(source, [*members, "pf_count"], path)
    assert len(rows) == len(_BARS)
    for (open_, close), row in zip(_BARS, rows):
        _assert_values(row, [oracle(close - open_) for *_, oracle in _UNARY_CONTRACTS]
                       + [float(len(_UNARY_CONTRACTS))])


@pytest.mark.parametrize("arguments", _ROUND_FORMS)
@pytest.mark.parametrize("path", ("public", "unchecked", "direct"))
def test_native_round_evaluates_number_and_precision_once(arguments, path):
    source = _HEADER + (
        "pf_number_arg(array<int> events, float sample) =>\n"
        "    array.push(events, 1)\n"
        "    sample\n"
        "pf_precision_arg(array<int> events, int requested) =>\n"
        "    array.push(events, 2)\n"
        "    requested\n"
        "pf_events = array.new_int()\n"
        "pf_sample = close - open\n"
        "pf_precision = bar_index % 3 + 1\n"
    )
    arguments = arguments.replace("pf_sample", "pf_number_arg(pf_events, pf_sample)")
    arguments = arguments.replace("pf_precision", "pf_precision_arg(pf_events, pf_precision)")
    source += (
        f"pf_value = math.round({arguments})\n"
        "pf_marker_sum = array.sum(pf_events)\n"
        "pf_total_count = array.size(pf_events)\n"
        "plot(pf_value)\n"
    )
    rows = _native_values(source, ["pf_value", "pf_marker_sum", "pf_total_count"], path)
    assert len(rows) == len(_BARS)
    for index, ((open_, close), row) in enumerate(zip(_BARS, rows)):
        _assert_values(row, [_round_at(close - open_, index % 3 + 1), 3.0, 2.0])


_VARIADIC_KEYWORDS = (
    pytest.param("number0 = close, number1 = open", "number0", id="tv-number0-number1"),
    pytest.param("x = close, y = open", "x", id="tv-x-y"),
    pytest.param("number1 = open, number0 = close", "number1", id="reversed-candidates"),
    pytest.param("number0 = close", "number0", id="single-candidate"),
    pytest.param("number = close", "number", id="unary-name"),
    pytest.param("pf_unknown = close", "pf_unknown", id="unknown-name"),
    pytest.param("close, number1 = open", "number1", id="mixed-candidate"),
    pytest.param("close, y = open", "y", id="mixed-old-name"),
    pytest.param("close, open, pf_unknown = close - open", "pf_unknown", id="extra-keyword"),
    pytest.param("close, open, z = bar_index", "z", id="third-old-name"),
)


def _assert_variadic_refusal(source: str, short_name: str, keyword: str, path: str):
    with pytest.raises(CompileError) as caught:
        _generate(source, path)
    diagnostic, = [d for d in caught.value.diagnostics if d.level == Level.ERROR]
    args = {"function": f"math.{short_name}"}
    assert diagnostic.code == "PF-E5089"
    assert diagnostic.args == args
    assert diagnostic.phase == Phase.ANALYZER
    assert (diagnostic.message, diagnostic.hint) == render_diagnostic("PF-E5089", args)
    assert "TradingView refuses keyword arguments" in diagnostic.message
    assert "variable number of arguments" in diagnostic.message
    start = source.index(f"{keyword} =")
    line = source.count("\n", 0, start) + 1
    col = start - source.rfind("\n", 0, start)
    assert diagnostic.location.file == "math_keywords.pine"
    assert (diagnostic.location.line, diagnostic.location.col,
            diagnostic.location.end_col) == (line, col, col + len(keyword))


@pytest.mark.parametrize("short_name", ("max", "min", "avg"))
@pytest.mark.parametrize("arguments,keyword", _VARIADIC_KEYWORDS)
@pytest.mark.parametrize("path", ("public", "unchecked", "direct"))
@pytest.mark.parametrize("context", (
    pytest.param("{call}", id="chart"),
    pytest.param('request.security(syminfo.tickerid, "60", {call})', id="requested"),
))
def test_variadic_calls_refuse_every_keyword(short_name, arguments, keyword, path, context):
    call = f"math.{short_name}(\n    {arguments})"
    _assert_variadic_refusal(_source(context.format(call=call)), short_name, keyword, path)


@pytest.mark.parametrize("short_name", ("max", "min", "avg"))
@pytest.mark.parametrize("body", (
    "pf_unused() => {call}\npf_value = close\n",
    "if false\n    pf_unused = {call}\npf_value = close\n",
    "pf_value = switch\n    close > open => {call}\n    => 0.0\n",
))
@pytest.mark.parametrize("arguments,keyword", _VARIADIC_KEYWORDS[:2])
def test_unused_and_conditional_variadic_calls_refuse_keywords(
    short_name, body, arguments, keyword,
):
    source = _HEADER + body.format(call=f"math.{short_name}({arguments})")
    _assert_variadic_refusal(source, short_name, keyword, "public")


@pytest.mark.parametrize("path", ("public", "unchecked", "direct"))
def test_variadic_keyword_policy_follows_the_record(monkeypatch, path):
    # Exercise an existing, otherwise fixed-arity dispatch with this policy.
    # No literal list in validation can satisfy this control.
    from dataclasses import replace

    record = replace(signatures.MATH_FUNCTIONS["atan2"], keyword_names_verified=True)
    monkeypatch.setattr(record, "variadic", True, raising=False)
    monkeypatch.setitem(signatures.MATH_FUNCTIONS, "atan2", record)
    source = _source("math.atan2(y = close, x = open)")
    _assert_variadic_refusal(source, "atan2", "y", path)
    assert _generate(_source("math.atan2(close, open)"), path)


# Signed slopes let different arguments win as the bar changes. This inventory
# deliberately exceeds every variadic overload example in the registry.
_VARIADIC_TERMS = (
    (1.0, 0.0), (-2.0, 0.125), (0.5, -0.25), (-0.75, 0.5),
    (3.0, -0.375), (-4.0, 0.75), (0.25, -0.875), (-0.125, 1.0),
    (5.0, -1.125),
)
_VARIADIC_COUNTS = (2, 3, 4, 6, 9)


@pytest.mark.parametrize("short_name", ("max", "min", "avg"))
@pytest.mark.parametrize("count", _VARIADIC_COUNTS)
@pytest.mark.parametrize("path", ("public", "unchecked", "direct"))
def test_variadic_positional_calls_keep_all_arguments(short_name, count, path):
    arguments = [f"close * {slope} + {offset}"
                 for slope, offset in _VARIADIC_TERMS[:count]]
    program = _parse(_source(f"math.{short_name}({', '.join(arguments)})"))
    declaration, = [node for node in program.body
                    if isinstance(node, VarDecl) and node.name == "pf_value"]
    call = declaration.value
    original = list(call.args)
    bind_builtin_keywords(program)
    assert len(call.args) == count
    assert all(actual is expected for actual, expected in zip(call.args, original))
    assert _generate(_source(f"math.{short_name}({', '.join(arguments)})"), path)


@pytest.mark.parametrize("path", ("public", "unchecked", "direct"))
def test_native_variadic_positional_values_follow_independent_bar_oracles(path):
    source = _HEADER + "pf_sample = bar_index == 0 ? float(na) : close - open\n"
    members = []
    expectations = []
    for short_name, oracle in (("max", max), ("min", min),
                               ("avg", lambda values: sum(values) / len(values))):
        for count in _VARIADIC_COUNTS:
            member = f"pf_{short_name}_{count}"
            members.append(member)
            expectations.append((count, oracle))
            arguments = ", ".join(f"pf_sample * {slope} + {offset}"
                                  for slope, offset in _VARIADIC_TERMS[:count])
            source += f"{member} = math.{short_name}({arguments})\n"
    # Preserve the existing one-positional-argument min/max routes too.
    for short_name in ("max", "min"):
        member = f"pf_{short_name}_one"
        members.append(member)
        expectations.append((1, max if short_name == "max" else min))
        source += f"{member} = math.{short_name}(pf_sample)\n"
    source += f"plot({members[-1]})\n"
    rows = _native_values(source, members, path)
    assert len(rows) == len(_BARS)
    for index, ((open_, close), row) in enumerate(zip(_BARS, rows)):
        assert len(row) == len(members)
        expected = [
            math.nan if index == 0 else
            oracle([(close - open_) * slope + offset
                    for slope, offset in _VARIADIC_TERMS[:count]])
            for count, oracle in expectations
        ]
        _assert_values(row, expected)
