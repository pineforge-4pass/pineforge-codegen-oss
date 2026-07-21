"""Regression controls for a void matrix call as a method's sole expression.

The parser and analyzer already preserve the authored method and identify its
terminal ``matrix.set`` call as void.  Codegen must therefore emit the call as
a statement, not try to return the C++ ``void`` expression from its numeric
fallback method wrapper.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import ExprStmt, FuncCall, MemberAccess, MethodDef
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.symbols import PineType
from tests._compile import compile_cpp


SOURCE = '''//@version=6
strategy("void matrix method")
method set(matrix<int> self, int row, int col, int value) => matrix.set(self, row, col, value)
'''


MATRIX_VOID_CASES = (
    ("set", "matrix.set(self, 0, 0, 1)", "self.set(0, 0, 1)"),
    ("fill", "matrix.fill(self, 1)", "self.fill(1)"),
    ("add_row", "matrix.add_row(self, values)", "self.add_row(values)"),
    ("add_col", "matrix.add_col(self, values)", "self.add_col(values)"),
    ("swap_rows", "matrix.swap_rows(self, 0, 0)", "self.swap_rows(0, 0)"),
    (
        "swap_columns",
        "matrix.swap_columns(self, 0, 0)",
        "self.swap_columns(0, 0)",
    ),
    ("reshape", "matrix.reshape(self, 1, 1)", "self.reshape(1, 1)"),
    ("reverse", "matrix.reverse(self)", "self.reverse()"),
    ("sort", "matrix.sort(self, 0)", "self.sort(0)"),
)


def _void_case_source(method: str, expression: str, form: str) -> str:
    values_param = ", array<int> values" if method in {"add_row", "add_col"} else ""
    return f'''//@version=6
strategy("matrix {method} {form}")
method mutate_{method}_{form}(matrix<int> self{values_param}) => {expression}
'''


def _parse():
    return Parser(Lexer(SOURCE).tokenize(), source=SOURCE).parse()


def test_single_expression_matrix_void_method_parses_without_recovery() -> None:
    program = _parse()

    assert (program.annotations or {}).get("parse_recovery_count", 0) == 0
    methods = [node for node in program.body if isinstance(node, MethodDef)]
    assert len(methods) == 1
    method = methods[0]
    assert method.type_name == "matrix<int>"
    assert method.params == ["self", "row", "col", "value"]
    assert method.is_single_expr
    assert len(method.body) == 1
    assert isinstance(method.body[0], ExprStmt)
    assert isinstance(method.body[0].expr, FuncCall)
    assert isinstance(method.body[0].expr.callee, MemberAccess)
    assert method.body[0].expr.callee.member == "set"


def test_single_expression_matrix_void_method_analyzes_as_void() -> None:
    ctx = Analyzer(_parse()).analyze()

    method_info = next(
        info for info in ctx.func_infos if info.name == "matrix<int>.set"
    )
    assert method_info.return_type == PineType.VOID
    assert method_info.return_type_spec is None


def test_single_expression_matrix_void_method_does_not_return_void_call() -> None:
    cpp = transpile(SOURCE)

    assert re.search(
        r"double _udt_matrix_int_set\(PineGenericMatrix<int>& self, "
        r"int row, int col, int value\)",
        cpp,
    )
    assert "return self.set((int)(row), (int)(col), value);" not in cpp
    assert "self.set((int)(row), (int)(col), value);" in cpp
    assert "return 0.0;" in cpp


def test_single_expression_matrix_void_method_native_compiles() -> None:
    compile_cpp(
        transpile(SOURCE),
        label="matrix-single-expression-void-method",
    )


@pytest.mark.parametrize("method,namespace_expr,member_expr", MATRIX_VOID_CASES)
@pytest.mark.parametrize("form", ("namespace", "member"))
def test_every_void_matrix_mutator_is_a_statement_with_default_return(
    method: str,
    namespace_expr: str,
    member_expr: str,
    form: str,
) -> None:
    expression = namespace_expr if form == "namespace" else member_expr
    cpp = transpile(_void_case_source(method, expression, form))
    function_name = f"_udt_matrix_int_mutate_{method}_{form}"
    body = cpp.split(f" {function_name}(", 1)[1].split("\n    }", 1)[0]

    assert f"self.{method}(" in body
    assert f"return self.{method}(" not in body
    assert "return 0.0;" in body


def test_all_void_matrix_mutator_forms_native_compile() -> None:
    sources = [
        _void_case_source(
            method,
            namespace_expr if form == "namespace" else member_expr,
            form,
        )
        for method, namespace_expr, member_expr in MATRIX_VOID_CASES
        for form in ("namespace", "member")
    ]
    for index, source in enumerate(sources):
        compile_cpp(
            transpile(source),
            label=f"matrix-void-mutator-{index}",
        )


def test_typed_user_matrix_method_still_precedes_same_named_builtin() -> None:
    source = '''//@version=6
strategy("typed user matrix method precedence")
method fill(matrix<int> self) => 42
method delegate(matrix<int> self) => self.fill()
'''

    cpp = transpile(source)
    body = cpp.split(" _udt_matrix_int_delegate(", 1)[1].split("\n    }", 1)[0]

    assert "return _udt_matrix_int_fill(self);" in body
    assert "return 0.0;" not in body
    compile_cpp(cpp, label="matrix-void-mutator-user-method-precedence")


def test_typed_user_method_precedes_shadowed_matrix_namespace() -> None:
    source = '''//@version=6
strategy("typed user method shadows matrix namespace")
method fill(int self, matrix<int> other) => 42
method delegate(int matrix, matrix<int> other) => matrix.fill(other)
'''

    cpp = transpile(source)
    body = cpp.split(" _udt_int_delegate(", 1)[1].split("\n    }", 1)[0]

    assert "return _udt_int_fill(matrix, other);" in body
    assert "return 0;" not in body
    compile_cpp(cpp, label="matrix-void-mutator-shadowed-namespace")
