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
from pineforge_codegen.parser import Parser
from pineforge_codegen.symbols import PineType
from tests import _compile as compile_env
from tests._for_counter_header_cases import INT_START_SOURCES
from tests._for_float_step import assert_float_step_refused
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
