"""Keep counter evidence separate from collection and local storage inference."""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import ForStmt
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.limits import iter_ast_nodes
from pineforge_codegen.loop_types import LoopTypeSources, _CounterQuery
from pineforge_codegen.parser import Parser
from pineforge_codegen.symbols import PineType, TypeSpec
from tests import _compile as compile_env
from tests._for_counter_header_cases import INT_START_SOURCES
from tests._for_float_step import (
    DIVISION_STARTS, DIVISION_VALUE_STARTS, assert_float_step_refused, loop_step_source,
)
from tests._for_counter_type_source_cases import (
    ARRAY_TYPES, BASELINE, BASELINE_SOURCES, FLOAT_COUNTER_SOURCES,
    INT_COUNTER_SOURCES, MAIN_COMMIT,
)


@pytest.mark.parametrize("name,element", sorted(ARRAY_TYPES.items()))
def test_shared_query_keeps_exact_array_storage(name, element):
    cpp = transpile(BASELINE_SOURCES[name])
    vector = f"std::vector<{element}>"
    assert re.search(rf"\b{re.escape(vector)}\s+a\s*(?:;|=)", cpp), cpp
    assert f"{vector}{{" in cpp, cpp
    scalar = "flag" if element == "bool" else "s"
    assert re.search(rf"\b{re.escape(element)}\s+{scalar}\s*(?:;|=)", cpp), cpp
    assert not re.search(r"\bstd::vector<double>\s+a\s*(?:;|=)", cpp), cpp


def test_function_local_if_with_implicit_na_keeps_double_storage():
    cpp = transpile(BASELINE_SOURCES["if_local"])
    assert re.search(r"\bdouble x\s*=", cpp), cpp
    assert not re.search(r"\bint x\s*=", cpp), cpp


@pytest.mark.parametrize("name", sorted(INT_COUNTER_SOURCES | FLOAT_COUNTER_SOURCES))
def test_counter_storage_uses_only_positive_source_evidence(name):
    # Both 40-level declaration chains must finish transpiling. No wall-time
    # assertion is needed: each shared child is memoised by its declaration.
    cpp = transpile(BASELINE_SOURCES[name])
    family = "double" if name in FLOAT_COUNTER_SOURCES else "int"
    assert re.search(rf"\bfor \({family} walkIndex = ", cpp), cpp
    other = "int" if family == "double" else "double"
    assert not re.search(rf"\bfor \({other} walkIndex = ", cpp), cpp
    for slot in ("start", "end", "step"):
        assert re.search(rf"\b{family} _for_{slot}_\d+ = ", cpp), cpp


@pytest.mark.parametrize("name", sorted(INT_COUNTER_SOURCES | FLOAT_COUNTER_SOURCES))
def test_analyzer_records_the_counter_family_selected_for_emission(name):
    source = BASELINE_SOURCES[name]
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    ctx = Analyzer(ast).analyze()
    loop = next(
        node for node, _depth in iter_ast_nodes(ast)
        if isinstance(node, ForStmt) and node.var == "walkIndex"
    )
    expected = PineType.FLOAT if name in FLOAT_COUNTER_SOURCES else PineType.INT
    assert ctx.for_counter_types[id(loop.start)] == expected


@pytest.mark.parametrize("name", sorted(BASELINE_SOURCES))
def test_type_source_cpp_compiles(name):
    compile_env.compile_cpp(
        transpile(BASELINE_SOURCES[name]), label=name,
        extra_flags=compile_env.STRATEGY_FP_FLAGS,
    )


@pytest.fixture(scope="module")
def main_cpp_hashes():
    assert BASELINE.is_file(), (
        f"Capture the frozen source inventory from {MAIN_COMMIT} into {BASELINE}."
    )
    document = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert document["schema"] == 1
    assert document["revision"] == MAIN_COMMIT
    assert set(document["sources"]) == set(BASELINE_SOURCES)
    return document["sources"]


@pytest.mark.parametrize("name", sorted(BASELINE_SOURCES))
def test_source_inventory_and_unchanged_cpp(name, main_cpp_hashes):
    pine = BASELINE_SOURCES[name]
    baseline = main_cpp_hashes[name]
    assert hashlib.sha256(pine.encode("utf-8")).hexdigest() == baseline["source_sha256"]
    cpp = transpile(pine)
    # This run-stop guard is the only change permitted for int-counter rows.
    cpp = re.sub(
        r"(?m)^ *if \(!is_na\((_for_step_\d+)\) && \1 <= 0\) "
        r"""pine_runtime_error\("'step' in loop must be greater than zero"\);\n""",
        "",
        cpp,
    )
    digest = hashlib.sha256(cpp.encode("utf-8")).hexdigest()
    if name in FLOAT_COUNTER_SOURCES:
        assert digest != baseline["cpp_sha256"], name
    else:
        assert digest == baseline["cpp_sha256"], name


# These are derived engine checks of exact binary steps, without a new
# TradingView value claim: each interval contains three inclusive values,
# except the unknown-parameter control's 0.0 through 1.0 by 0.25 (five).
@pytest.mark.parametrize("name,expected", [
    ("for_local_float", "3"),
    ("array_float_extrema", "3"),
    ("array_float_avg", "3"),
    ("array_method_float_extrema", "3"),
    ("float_tuple_call", "3"),
    ("float_tuple_literal", "3"),
    ("unknown_parameter", "5"),
])
def test_float_evidence_runs_without_a_narrowed_step_stop(name, expected):
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{10.0, 10.0, 10.0, 10.0, 1.0, 60000};
    strategy.run(&bar, 1, "1", "1");
    assert(strategy.last_error().empty());
    std::cout << strategy.acc;
}
'''
    assert compile_env.run_emitted_tu(
        transpile(BASELINE_SOURCES[name]), driver, opt="-O2", label=name,
    ) == expected


def test_positive_int_parameter_start_refuses_a_float_step():
    # A declared int parameter supplies positive int evidence to the same
    # query that keeps an untyped parameter on the float route.
    pine = BASELINE_SOURCES["unknown_parameter"].replace(
        "measure(first) =>", "measure(int first) =>",
    ).replace("measure(0.0)", "measure(0)")
    assert_float_step_refused(pine, counter="walkIndex", location=(5, 37, 41))


def _analyzed_counter_start(pine):
    ast = Parser(Lexer(pine).tokenize(), source=pine).parse()
    ctx = Analyzer(ast).analyze()
    loop = next(node for node, _ in iter_ast_nodes(ast) if isinstance(node, ForStmt))
    query = _CounterQuery(
        LoopTypeSources(ast),
        ctx.identifier_decl_bindings, ctx.identifier_binding_scopes,
    )
    return loop, ctx, query


@pytest.mark.parametrize("name", ["highest", "lowest"])
@pytest.mark.parametrize("arguments", ["bar_index, 3", "source=bar_index, length=3"])
def test_highest_and_lowest_int_sources_accept_a_float_step(name, arguments):
    # TradingView accepts both overloads: the result is float even on int data.
    pine = loop_step_source(f"ta.{name}({arguments})")
    loop, ctx, query = _analyzed_counter_start(pine)
    assert query.infer(loop.start) == TypeSpec.primitive("float")
    assert query.value_spec(loop.start) == TypeSpec.primitive("float")
    assert ctx.for_counter_types[id(loop.start)] == PineType.FLOAT
    cpp = transpile(pine)
    assert re.search(r"\bfor \(double i = ", cpp), cpp
    for slot in ("start", "end", "step"):
        assert re.search(rf"\bdouble _for_{slot}_\d+ = ", cpp), cpp


@pytest.mark.parametrize("start", [
    "ta.change(bar_index)",
    "ta.valuewhen(true, bar_index, 0)",
])
def test_measured_source_typed_ta_calls_still_refuse_an_int_source_float_step(start):
    # TradingView refuses both int-source forms; division value typing must
    # not widen their check result to the registry's default float.
    assert_float_step_refused(loop_step_source(start))
    pine = loop_step_source(start, step=None)
    loop, ctx, query = _analyzed_counter_start(pine)
    assert query.infer(loop.start) == TypeSpec.primitive("int")
    assert ctx.for_counter_types[id(loop.start)] == PineType.INT


@pytest.mark.parametrize("start", [
    "ta.median(bar_index, 3)",  # no TradingView receipt yet
    "ta.mode(bar_index, 3)",  # no TradingView receipt yet
    "ta.max(bar_index)",  # no TradingView receipt yet
    "ta.min(bar_index)",  # no TradingView receipt yet
    "ta.range(bar_index, 3)",  # no TradingView receipt yet
    "ta.percentile_nearest_rank(bar_index, 3, 50)",  # no TradingView receipt yet
])
def test_unmeasured_int_source_ta_check_type_stays_temporarily_unknown(start):
    pine = loop_step_source(start)
    loop, ctx, query = _analyzed_counter_start(pine)
    assert query.infer(loop.start) is None
    assert query.value_spec(loop.start) is None
    assert ctx.for_counter_types[id(loop.start)] == PineType.FLOAT
    cpp = transpile(pine)
    assert re.search(r"\bfor \(double i = ", cpp), cpp
    assert not re.search(r"\bfor \(int i = ", cpp), cpp


def test_requested_expression_helper_division_start_uses_a_double_counter():
    # The expression-only helper runs before the loop; requested helper loops
    # cannot contain a user-function call in their header or body.
    pine = """//@version=6
strategy("Requested fractional start", currency=currency.NONE)
n = input.int(5)
start_value() => n / 2
measure() =>
    first = start_value()
    float acc = 0.0
    for i = first to first + 2
        acc += i
    acc
plot(request.security(syminfo.tickerid, timeframe.period, measure()))
"""
    loop, ctx, query = _analyzed_counter_start(pine)
    assert query.infer(loop.start) == TypeSpec.primitive("int")
    assert query.value_spec(loop.start) == TypeSpec.primitive("float")
    assert ctx.for_counter_types[id(loop.start)] == PineType.FLOAT
    cpp = transpile(pine)
    begin = cpp.index("void _eval_security_0(")
    evaluator = cpp[begin:cpp.index("void evaluate_security(", begin)]
    assert re.search(r"\bfor \(double _sec0_measure_\d+_i = ", evaluator), evaluator
    assert not re.search(r"\bfor \(int _sec0_measure_\d+_i = ", evaluator), evaluator
    for slot in ("start", "end", "step"):
        assert re.search(
            rf"\bdouble _sec0_measure_\d+_for_{slot} = ", evaluator,
        ), evaluator
    assert "::kFloatStepTolerance" in evaluator


@pytest.mark.parametrize("prefix,start,family", [
    *(pytest.param(prefix, start, family, id=name)
      for name, (prefix, start, family) in sorted(DIVISION_STARTS.items())),
    *(pytest.param(prefix, start, family, id=name)
      for name, (prefix, start, _end, _twin, _twin_end, family)
      in sorted(DIVISION_VALUE_STARTS.items())),
])
def test_division_start_has_distinct_check_and_value_specs(prefix, start, family):
    pine = loop_step_source(start, prefix=prefix, step=None)
    ast = Parser(Lexer(pine).tokenize(), source=pine).parse()
    ctx = Analyzer(ast).analyze()
    loop = next(node for node, _ in iter_ast_nodes(ast) if isinstance(node, ForStmt))
    query = _CounterQuery(
        LoopTypeSources(ast),
        ctx.identifier_decl_bindings, ctx.identifier_binding_scopes,
    )
    assert query.infer(loop.start) == TypeSpec.primitive("int")
    expected = "float" if family == "double" else "int"
    assert query.value_spec(loop.start) == TypeSpec.primitive(expected)
    assert ctx.for_counter_types[id(loop.start)] == (
        PineType.FLOAT if family == "double" else PineType.INT
    )


@pytest.mark.parametrize("prefix,start,family", [
    pytest.param("", "(+4) / (-2)", "int", id="parenthesized-signs"),
    pytest.param("const int n = 8\nconst int d = 4\n", "n / d", "int",
                 id="declared-constants"),
    pytest.param("unit = 2\nn = unit * unit\nd = +unit\n", "n / d", "int",
                 id="inferred-constant-aliases"),
    pytest.param("", "(3 * 4 + 6 - 2) / (1 + 1)", "int", id="integer-arithmetic"),
    pytest.param("", "(8 / 2) / (6 / 3)", "int", id="nested-exact-divisions"),
    pytest.param("", "(11 % 4) / 3", "int", id="integer-remainder"),
    pytest.param("", "(-11 % 4) / -3", "int", id="negative-remainder"),
    pytest.param("", "0 / 2", "int", id="zero-result"),
    pytest.param("", "(5 / 2) * 2 / 2", "double", id="fractional-intermediate"),
    pytest.param("", "4 / (2 - 2)", "double", id="zero-constant-divisor"),
    pytest.param("", "(4 % 0) / 2", "double", id="zero-remainder-divisor"),
    pytest.param("n = 4\nn += 1\n", "n / 2", "double", id="reassigned-name"),
    pytest.param("var int n = 4\n", "n / 2", "double", id="persistent-name"),
    pytest.param("series int n = 4\n", "n / 2", "double", id="series-qualifier"),
    pytest.param("simple int n = 4\n", "n / 2", "double", id="simple-qualifier"),
    pytest.param("", "int(4) / 2", "double", id="call-operand"),
    pytest.param("n = int(4)\n", "n / 2", "double", id="call-alias"),
    pytest.param("", "4.0 / 2", "double", id="float-literal"),
    pytest.param("float n = 4\n", "n / 2", "double", id="float-alias"),
])
def test_division_integrality_requires_constant_integer_operands(prefix, start, family):
    pine = loop_step_source(start, prefix=prefix, step=None)
    ast = Parser(Lexer(pine).tokenize(), source=pine).parse()
    ctx = Analyzer(ast).analyze()
    loop = next(node for node, _ in iter_ast_nodes(ast) if isinstance(node, ForStmt))
    query = _CounterQuery(
        LoopTypeSources(ast),
        ctx.identifier_decl_bindings, ctx.identifier_binding_scopes,
    )
    assert query.value_spec(loop.start) == TypeSpec.primitive(
        "int" if family == "int" else "float",
    )
    assert ctx.for_counter_types[id(loop.start)] == (
        PineType.INT if family == "int" else PineType.FLOAT
    )
    cpp = transpile(pine)
    assert re.search(rf"\bfor \({family} i = ", cpp), cpp


@pytest.mark.parametrize("context", ["chart", "security"])
@pytest.mark.parametrize("counter_bound", [False, True])
@pytest.mark.parametrize("prefix,setup,start,step,family", [
    ("", "", "first", "0.25", "double"),
    ("", "", "first", "0.125 + 0.125", "double"),
    ("", "float stride = 0\n", "first", "stride", "double"),
    ("", "", "first", "1", "int"),
    ("", "stride = ta.median(1, 2)\n", "first", "stride", "int"),
    ("", "", "0", "1", "int"),
    ("", "", "0", "0.25", "refused"),
    ("", "int begin = 0\n", "begin", "0.25", "refused"),
    ("", "", "int(first)", "0.25", "refused"),
    ("begin = input.int(0)\n", "", "begin", "0.25", "refused"),
    ("", "", "bar_index", "0.25", "refused"),
    ("", "", "math.floor(first)", "0.25", "refused"),
    ("", "", "math.round(first)", "0.25", "refused"),
    ("", "", "(true ? 0 : na)", "0.25", "refused"),
    ("a = array.from(0.0, 1.0)\n", "", "array.size(a)", "0.25", "refused"),
    ("a = array.from(0.0, 1.0)\n", "", "a.size()", "0.25", "refused"),
    pytest.param("", "", "4 / 2", "0.5", "refused", id="division-start"),
    pytest.param("", "begin = 4 / 2\n", "begin", "0.5", "refused",
                 id="named-division-start"),
    pytest.param("startValue() => 4 / 2\n", "", "startValue()", "0.5", "refused",
                 id="helper-division-start"),
    pytest.param("", "", "bar_index / 2", "0.5", "refused", id="series-division-start"),
    pytest.param("", "", "4 / 2", "1", "int", id="division-int-step"),
    pytest.param("", "begin = 4 / 2\n", "begin", "1", "int",
                 id="named-division-int-step"),
    # Evaluate the expression-only helper before the loop. Requested helper
    # loops cannot contain a user function call in their header or body.
    pytest.param("startValue() => 4 / 2\n", "begin = startValue()\n", "begin", "1", "int",
                 id="helper-division-int-step"),
    pytest.param("n = input.int(5)\n", "", "n / 2", "1", "double",
                 id="input-division-int-step"),
    pytest.param("", "n = 5 + bar_index % 2\n", "n / 2", "1", "double",
                 id="series-division-int-step"),
    # Step checking retains the operand join independently of the constant
    # proof used to choose storage for a division in the start.
    pytest.param("", "", "0", "4 / 2", "int", id="int-division-step"),
    pytest.param("", "", "first", "4 / 2", "int", id="unknown-start-int-division-step"),
    pytest.param("", "", "0", "4 / 2.0", "refused", id="float-division-step"),
    # An int step makes these rows depend on division's result family:
    # an unknown operand must not leave the start on the int fallback.
    pytest.param("", "", "first / 2", "1", "double", id="division-unknown-left"),
    pytest.param("", "", "2 / first", "1", "double", id="division-unknown-right"),
    pytest.param("", "", "na / 2", "1", "double", id="division-na-left"),
    pytest.param("", "", "2 / na", "1", "double", id="division-na-right"),
])
def test_step_evidence_only_promotes_an_unknown_start(
    context, counter_bound, prefix, setup, start, step, family,
):
    # Derive the family from authored evidence, never a signature's FLOAT
    # default or the analyzer's guessed storage for an untyped parameter.
    # The literal int start with float end and step is measured in
    # tradingview_quantities.json: int_start_float_end_float_step.
    # Other start expressions and requested/bound compositions are derived.
    end = "(walkIndex >= 0 ? 1.0 : 1.0)" if counter_bound else "1.0"
    body = (
        setup + "float acc = 0.0\n"
        f"for walkIndex = {start} to {end} by {step}\n"
        "    acc += 1.0\n"
        "acc\n"
    )
    call = "measure(0.0)"
    if context == "security":
        call = f"request.security(syminfo.tickerid, timeframe.period, {call})"
    pine = (
        '//@version=6\nstrategy("Loop step evidence")\n'
        + prefix + "measure(first) =>\n"
        + "".join("    " + line + "\n" for line in body.splitlines())
        + f"plot({call})\n"
    )
    if family == "refused":
        assert_float_step_refused(pine, counter="walkIndex")
        return
    cpp = transpile(pine)
    counter = "walkIndex" if context == "chart" else r"\w+_\d+_walkIndex"
    assert re.search(rf"\b{family} {counter} = ", cpp), cpp
    other = "int" if family == "double" else "double"
    assert not re.search(rf"\b{other} {counter} = ", cpp), cpp
    ast = Parser(Lexer(pine).tokenize(), source=pine).parse()
    ctx = Analyzer(ast).analyze()
    loop = next(node for node, _ in iter_ast_nodes(ast) if isinstance(node, ForStmt))
    expected = PineType.FLOAT if family == "double" else PineType.INT
    assert ctx.for_counter_types[id(loop.start)] == expected


@pytest.mark.parametrize("name,pine", sorted(INT_START_SOURCES.items()))
def test_existing_positive_int_start_evidence_refuses_a_float_step(name, pine):
    # Reuse every existing integer source, including nullable ternaries,
    # array sizes, trade counts and int-returning math/TA calls.
    source = pine.replace(" to 0\n", " to 0 by 0.25\n")
    assert source != pine
    assert_float_step_refused(source, counter="walkIndex")
