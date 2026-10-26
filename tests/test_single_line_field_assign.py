"""Regression coverage for field assignments in one-line function bodies."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import Assignment, ExprStmt, FuncDef, MemberAccess
from pineforge_codegen.errors import CompileError, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._e2e import (
    Build,
    assert_same_runs,
    chart_feed_head,
    execute_all,
    ok,
    skip_unless_e2e_env,
)


def _parse(source: str):
    return Parser(Lexer(source).tokenize(), source=source).parse()


def _function_source(definition: str) -> str:
    return f"""//@version=6
strategy("single-line field assignment")
type State
    int value = 0
{definition}
var State s = State.new()
result = set(s)
plot(s.value)
"""


def _method_source(definition: str) -> str:
    return f"""//@version=6
strategy("single-line field method")
type State
    int value = 0
{definition}
var State s = State.new()
result = s.set()
plot(s.value)
"""


def test_function_field_assignment_matches_indented_block() -> None:
    inline = _function_source("set(State s) => s.value := 1")
    indented = _function_source("set(State s) =>\n    s.value := 1")

    program = _parse(inline)
    function = next(node for node in program.body if isinstance(node, FuncDef))
    assert function.is_single_expr is False
    assert len(function.body) == 1
    assert isinstance(function.body[0], Assignment)
    assert isinstance(function.body[0].target, MemberAccess)

    assert transpile(inline) == transpile(indented)


def test_method_field_assignment_matches_indented_block() -> None:
    inline = _method_source("method set(State s) => s.value := 1")
    indented = _method_source("method set(State s) =>\n    s.value := 1")

    assert transpile(inline) == transpile(indented)


def test_compound_field_assignment_matches_indented_block() -> None:
    inline = _function_source("set(State s) => s.value += 2")
    indented = _function_source("set(State s) =>\n    s.value += 2")

    assert transpile(inline) == transpile(indented)


def test_comma_joined_field_assignments_match_indented_block() -> None:
    inline = _function_source(
        "set(State s) => s.value := 1, s.value += 2"
    )
    indented = _function_source(
        "set(State s) =>\n"
        "    s.value := 1\n"
        "    s.value += 2"
    )

    assert transpile(inline) == transpile(indented)


def test_existing_single_expression_body_stays_single_expression() -> None:
    source = """//@version=6
strategy("single expression body")
f(int value) => value + 1
"""

    program = _parse(source)
    function = next(node for node in program.body if isinstance(node, FuncDef))
    assert function.is_single_expr is True
    assert len(function.body) == 1
    assert isinstance(function.body[0], ExprStmt)


def test_plain_name_assignment_keeps_parser_rejection() -> None:
    source = """//@version=6
strategy("plain name assignment")
f() => value := 1
"""

    with pytest.raises(CompileError) as error:
        transpile(source)
    assert error.value.diagnostics[0].phase is Phase.PARSER
    assert "COLON_EQUALS" in error.value.diagnostics[0].message


RUNTIME_INLINE = """//@version=6
strategy("single-line field assignment runtime")
type State
    int value = 0
set(State s) => s.value := 1
var State s = State.new()
set(s)
plot(s.value)
// @pf-trace field=s.value
"""

RUNTIME_INDENTED = """//@version=6
strategy("single-line field assignment runtime")
type State
    int value = 0
set(State s) =>
    s.value := 1
var State s = State.new()
set(s)
plot(s.value)
// @pf-trace field=s.value
"""


@pytest.fixture(scope="module")
def runtime_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("single_line_field_assign")
    feed = chart_feed_head(engine, base, 8)
    return execute_all(
        engine,
        feed,
        base,
        {
            "inline": Build(RUNTIME_INLINE, trace=True),
            "indented": Build(RUNTIME_INDENTED, trace=True),
        },
    )


def test_runtime_field_value_matches_indented_form(runtime_runs) -> None:
    inline = ok(runtime_runs, "inline")
    indented = ok(runtime_runs, "indented")
    assert_same_runs(inline, indented)

    records = inline.traces["default"]
    assert records
    assert all(
        record["name"] == "field" and record["value"] == 1
        for record in records
    )
