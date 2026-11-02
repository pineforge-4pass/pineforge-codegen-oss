"""Fractional storage, measured quantities, and unchanged integer controls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from textwrap import dedent

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer.value_bindings import ValueBindings
from pineforge_codegen.ast_nodes import ForStmt, FuncDef
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.limits import iter_ast_nodes
from pineforge_codegen.loop_types import LoopTypeSources, _CounterQuery
from pineforge_codegen.parser import Parser
from pineforge_codegen.symbols import TypeSpec
from tests import _compile as compile_env
from tests._e2e import reference_codegen, transpile_json
from tests._for_float_step import assert_float_step_refused


FIXTURES = Path(__file__).parent / "fixtures" / "for_counter_headers"
TV = json.loads((FIXTURES / "tradingview_quantities.json").read_text(encoding="utf-8"))
CPP_REFERENCE = "daa5102558b9b4c1ca764f3a770cbd206003e91a"
# The explicit-int quantity row is first, independently of loop lowering.
MEASURED_FORMS = (
    "reassign_int_hint", "reassign_plain_qty", "reassign_literal_qty",
    "reassign_series_qty", "reassign_untyped", "reassign_typed",
)


def _source(body: str) -> str:
    return (
        '//@version=6\nstrategy("Fractional storage", currency=currency.NONE)\n'
        'n = input.int(5, "Numerator")\n'
        + dedent(body).lstrip()
    )


def _assert_fractional_storage(cpp: str) -> None:
    assert re.search(r"\b(?:double|Series<double>)\s+s(?:\b|_cs)", cpp), cpp
    assert not re.search(r"\b(?:int|int64_t|Series<int>)\s+s\b", cpp), cpp
    writes = [
        line for line in cpp.splitlines()
        if re.search(r"\bs(?:_cs\d+)?(?: = |\.update\()", line) and " / " in line
    ]
    assert writes, cpp
    assert all("_pf_v" not in line and "na<int>()" not in line for line in writes), writes


@pytest.mark.parametrize("name", MEASURED_FORMS)
def test_measured_reassignment_forms_use_double_storage(name):
    _assert_fractional_storage(
        transpile((FIXTURES / f"{name}.pine").read_text(encoding="utf-8")),
    )


@pytest.mark.parametrize("name", (
    "widened_float_step", "widened_int_hint_float_step",
))
def test_float_step_loop_from_a_widened_variable_is_refused_like_int(name):
    source = (FIXTURES / f"{name}.pine").read_bytes()
    pine = source.decode("utf-8")
    refusal = TV["compile_refusals"][name]
    assert refusal["verdict"] == "REFUSED"
    assert refusal["code"] == "CE10106"
    assert hashlib.sha256(source).hexdigest() == refusal["source_sha256"]
    # TradingView's recorded span "line:first-last" (last inclusive) is the
    # step literal; the engine's location is the same columns, end exclusive.
    start, end = refusal["span"].split("-")
    line, first = (int(part) for part in start.split(":"))
    _, last = (int(part) for part in end.split(":"))
    assert_float_step_refused(pine, location=(line, first, last + 1))


# These controls derive from the measured plain-quantity row. They exercise
# where the quotient is stored, without claiming additional TradingView tapes.
# Persistent cases reset before dividing so every bar has the measured value.
SCOPE_SOURCES = {
    "global": _source("""
        int s = n
        s := s / 2
        observed = s
    """),
    "global_var": _source("""
        var int s = n
        s := n
        s := s / 2
        observed = s
    """),
    "global_varip": _source("""
        varip int s = n
        s := n
        s := s / 2
        observed = s
    """),
    "callable_local": _source("""
        measure(int seed) =>
            int s = seed
            s := s / 2
            s
        observed = measure(n)
    """),
    "callable_inferred": _source("""
        measure(int seed) =>
            s = seed
            s := s / 2
            s
        observed = measure(n)
    """),
    "callable_var": _source("""
        measure(int seed) =>
            var int s = seed
            s := seed
            s := s / 2
            s
        observed = measure(n)
    """),
    "block_local": _source("""
        float observed = 0.0
        if n > 0
            int s = n
            s := s / 2
            observed := s
    """),
    "loop_body": _source("""
        float observed = 0.0
        for turn = 0 to 0
            int s = n
            s := s / 2
            observed := s
    """),
    "write_in_loop": _source("""
        int s = n
        for turn = 0 to 0
            s := s / 2
        observed = s
    """),
    "history": _source("""
        int s = n
        s := s / 2
        observed = nz(s[1], s)
    """),
    "write_after_read": _source("""
        int s = n
        before = s
        if n > 0
            s := s / 2
        observed = s + (before - n)
    """),
    "write_after_loop_read": _source("""
        int s = n
        float total = 0.0
        for i = s to s + 2
            total += i
        s := s / 2
        observed = s
    """),
    "transitive_writes": _source("""
        int first = n
        int s = n
        s := first
        first := first / 2
        s := first
        observed = s
    """),
}


@pytest.mark.parametrize("name", tuple(SCOPE_SOURCES))
def test_fractional_storage_reaches_each_unambiguous_scope(name):
    cpp = transpile(SCOPE_SOURCES[name])
    if name == "transitive_writes":
        # The division writes first; s receives the value through another :=.
        assert re.search(r"\bdouble first\b", cpp), cpp
        assert re.search(r"\bdouble s\b", cpp), cpp
    else:
        _assert_fractional_storage(cpp)
    if name == "history":
        assert re.search(r"\bSeries<double>\s+s\b", cpp), cpp
    if name == "write_after_loop_read":
        assert re.search(r"\bfor \(double i = ", cpp), cpp


VALUE_DRIVER = r'''
#include <cassert>
#include <iostream>
int main() {
    Bar bars[] = {
        {10.0, 10.0, 10.0, 10.0, 1.0, 60000},
        {10.0, 10.0, 10.0, 10.0, 1.0, 120000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 2, "1", "1");
    assert(strategy.last_error().empty());
    std::cout << strategy.observed;
}
'''


@pytest.mark.parametrize("name", tuple(SCOPE_SOURCES))
def test_scope_storage_keeps_the_measured_quotient(name):
    # The existing helper owns the native-prerequisite skip.
    actual = compile_env.run_emitted_tu(
        transpile(SCOPE_SOURCES[name]), VALUE_DRIVER, opt="-O2", label=name,
    )
    assert float(actual) == TV["entry_quantities"]["reassign_int_hint"]


def test_explicit_int_loop_is_consistent_with_the_untyped_loop_measurement():
    """CONSISTENCY row: the same loop shape as the measured untyped source.

    TradingView was not run on this explicit-int loop source; it is not a tape row.
    """
    pine = _source("""
        int s = n
        s := s / 2
        float observed = 0.0
        for i = s to s + 2
            observed += i
    """)
    cpp = transpile(pine)
    _assert_fractional_storage(cpp)
    assert re.search(r"\bfor \(double i = ", cpp), cpp
    actual = compile_env.run_emitted_tu(cpp, VALUE_DRIVER, opt="-O2")
    assert float(actual) == TV["entry_quantities"]["reassign_untyped"]


@pytest.mark.parametrize("hint", ("", "int "))
@pytest.mark.parametrize("step", ("", " by 1"))
def test_widened_loop_start_keeps_double_storage_with_an_integer_step(hint, step):
    cpp = transpile(_source(f"""
        {hint}s = n
        s := s / 2
        float observed = 0.0
        for i = s to s + 2{step}
            observed += i
    """))
    _assert_fractional_storage(cpp)
    assert re.search(r"\bfor \(double i = ", cpp), cpp


@pytest.mark.parametrize("rhs", ("math.sqrt(n)", "n * 0.5", "float(n)"))
def test_positive_float_expression_writes_use_double_storage(rhs):
    """engine-vs-engine control, not TradingView evidence."""
    cpp = transpile(_source(f"int s = n\ns := {rhs}\nplot(s)\n"))
    assert re.search(r"\bdouble s\b", cpp), cpp
    writes = [line for line in cpp.splitlines() if re.search(r"\bs = ", line)]
    # The narrowing helper's own text: an int cast of the written value. The
    # state checkpoint lines carry a member named _pf_value_N, which is not it.
    assert all("(int)_pf_v" not in line and "na<int>" not in line
               for line in writes), writes


FROZEN_CONTROLS = {
    "inferred_integer_write": "s = n\ns := s + 1\nplot(s)\n",
    "hinted_integer_write": "int s = n\ns := s + 1\nplot(s)\n",
    "literal_integer_write": "s = 5\ns := s + 1\nplot(s)\n",
    "persistent_integer_write": "var int s = n\ns := s + 1\nplot(s)\n",
    "direct_quotient_initializer": "int s = n / 2\nplot(s)\n",
    "proved_integral_quotient_write": "int s = n\ns := 4 / 2\nplot(s)\n",
    # Direct, non-quotient float-literal writes are unmeasured and retain
    # their existing narrowing for every declaration spelling.
    "float_literal_write": "int s = n\ns := 5.7\nplot(s)\n",
    "signed_float_literal_write": "int s = n\ns := -5.7\nplot(s)\n",
    "local_integer_write": """
        measure(int seed) =>
            int s = seed
            s := s + 1
            s
        plot(measure(n))
    """,
    "loop_body_integer_write": """
        float total = 0.0
        for turn = 0 to 0
            int s = n
            s := s + 1
            total += s
        plot(total)
    """,
    # Name-based emitter lookups still prevent safe widening of these two
    # distinct bindings. Preserve this known gap without refusing the source.
    "shadowed_storage": """
        int s = n
        measure() =>
            int s = n
            s := s / 2
            s
        plot(s + measure())
    """,
}


@pytest.mark.parametrize("name", tuple(FROZEN_CONTROLS))
def test_integer_and_unmeasured_controls_keep_complete_frozen_cpp(name, tmp_path):
    pine = _source(FROZEN_CONTROLS[name])
    reference = reference_codegen(CPP_REFERENCE)
    assert reference is not None, "Required C++ reference unavailable; restore git history."
    path = tmp_path / "strategy.pine"
    path.write_text(pine, encoding="utf-8")
    before = transpile_json(path, root=reference)
    assert before["ok"], before["diagnostics"]
    assert transpile(pine) == before["cpp"]


def _parameter_loop_source(parameter: str) -> str:
    return _source(f"""
        f({parameter}) =>
            float total = 0.0
            for i = p to 6 by 0.5
                total += i
            total
        plot(f(2))
    """)


# Known gap: TradingView refuses the registry probe
# f(float p) => p; f(2) with CE10106, while PineForge translates it
# (accepting what TradingView refuses is a disagreement too).
# These parameter-loop rows make no TradingView agreement claim.
@pytest.mark.parametrize("parameter,expected", [
    ("int p", TypeSpec.primitive("int")),
    ("float p", TypeSpec.primitive("float")),
    ("p", None),
])
def test_declaration_bound_parameter_check_uses_its_own_hint(parameter, expected):
    pine = _parameter_loop_source(parameter)
    ast = Parser(Lexer(pine).tokenize(), source=pine).parse()
    bindings = ValueBindings(ast, "parameter.pine")
    bindings.resolve()
    function = next(node for node, _ in iter_ast_nodes(ast) if isinstance(node, FuncDef))
    loop = next(node for node, _ in iter_ast_nodes(ast) if isinstance(node, ForStmt))
    assert bindings.declaration_bindings[id(loop.start)] == (id(function), "p")
    query = _CounterQuery(LoopTypeSources(ast), bindings.declaration_bindings, {})
    # The function returns a float total regardless of its parameter hint.
    # Following that return instead makes the int and untyped rows fail.
    assert query.infer(loop.start) == expected


# Analyzer parameter symbols have no declaration identity in the counter map.
# The frozen query already reads their hints: int refuses the float step;
# float and untyped starts translate with double counters.
@pytest.mark.parametrize("parameter,accepted", [
    pytest.param("int p", False, id="int_parameter"),
    pytest.param("float p", True, id="float_parameter"),
    pytest.param("p", True, id="untyped_parameter"),
])
def test_parameter_loop_start_is_unchanged_by_the_storage_fix(
    parameter, accepted, tmp_path,
):
    reference = reference_codegen(CPP_REFERENCE)
    assert reference is not None, "Required C++ reference unavailable; restore git history."
    pine = _parameter_loop_source(parameter)
    path = tmp_path / "strategy.pine"
    path.write_text(pine, encoding="utf-8")
    before = transpile_json(path, root=reference)
    assert before["ok"] is accepted, before["diagnostics"]
    if not accepted:
        assert "PF-E2079" in {diagnostic["code"] for diagnostic in before["diagnostics"]}
        assert_float_step_refused(pine)
    else:
        cpp = transpile(pine)
        assert re.search(r"\bfor \(double i = ", cpp), cpp
        assert cpp == before["cpp"]
