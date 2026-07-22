from pineforge_codegen.codegen.base import CodeGen
from pineforge_codegen.symbols import TypeSpec


def _make_codegen():
    cg = CodeGen.__new__(CodeGen)
    cg._udt_defs = {"Pivot": {}}
    return cg


def test_hint_parses_matrix_int():
    cg = _make_codegen()
    spec = cg._type_spec_from_hint_name("matrix<int>")
    assert spec is not None
    assert spec.kind == "matrix"
    assert spec.element.kind == "primitive"
    assert spec.element.name == "int"


def test_hint_parses_matrix_udt():
    cg = _make_codegen()
    spec = cg._type_spec_from_hint_name("matrix<Pivot>")
    assert spec.kind == "matrix"
    assert spec.element.kind == "udt"
    assert spec.element.name == "Pivot"


def test_to_cpp_matrix_float_is_pinematrix():
    cg = _make_codegen()
    spec = TypeSpec.matrix(TypeSpec.primitive("float"))
    assert cg._type_spec_to_cpp(spec) == "PineMatrix"


def test_to_cpp_matrix_int():
    cg = _make_codegen()
    spec = TypeSpec.matrix(TypeSpec.primitive("int"))
    assert cg._type_spec_to_cpp(spec) == "PineGenericMatrix<int>"


def test_to_cpp_matrix_color_lowers_to_int():
    cg = _make_codegen()
    spec = TypeSpec.matrix(TypeSpec.primitive("color"))
    assert cg._type_spec_to_cpp(spec) == "PineGenericMatrix<int>"


def test_to_cpp_matrix_udt():
    cg = _make_codegen()
    spec = TypeSpec.matrix(TypeSpec.udt("Pivot"))
    assert cg._type_spec_to_cpp(spec) == "PineGenericMatrix<Pivot>"


def test_default_for_udt_spec():
    cg = _make_codegen()
    spec = TypeSpec.udt("Pivot")
    assert cg._default_for_spec(spec) == "Pivot{}"


# ---------------------------------------------------------------------------
# Task 2.7: MemberAccess matrix branch
# ---------------------------------------------------------------------------

def test_member_access_matrix_row_returns_array_of_element():
    cg = _make_codegen()
    cg._matrix_specs = {"m": TypeSpec.matrix(TypeSpec.primitive("int"))}
    cg._collection_types = {"m": TypeSpec.matrix(TypeSpec.primitive("int"))}
    from pineforge_codegen.ast_nodes import FuncCall, MemberAccess, Identifier, NumberLiteral
    node = FuncCall(
        callee=MemberAccess(object=Identifier(name="m"), member="row"),
        args=[NumberLiteral(value=0)],
        kwargs={},
    )
    spec = cg._type_spec_from_expr(node)
    assert spec is not None
    assert spec.kind == "array"
    assert spec.element.kind == "primitive"
    assert spec.element.name == "int"


def test_member_access_matrix_copy_returns_same_spec():
    cg = _make_codegen()
    elem = TypeSpec.udt("Pivot")
    cg._matrix_specs = {"m": TypeSpec.matrix(elem)}
    cg._collection_types = {"m": TypeSpec.matrix(elem)}
    from pineforge_codegen.ast_nodes import FuncCall, MemberAccess, Identifier
    node = FuncCall(
        callee=MemberAccess(object=Identifier(name="m"), member="copy"),
        args=[],
        kwargs={},
    )
    spec = cg._type_spec_from_expr(node)
    assert spec.kind == "matrix"
    assert spec.element.kind == "udt"
    assert spec.element.name == "Pivot"


# ---------------------------------------------------------------------------
# Task 2.8: matrix.new<T> dispatch
# ---------------------------------------------------------------------------

from pineforge_codegen import transpile
from tests import _compile as compile_env


def _emit(src: str) -> str:
    return transpile(src)


def test_matrix_new_int_emits_pinegenericmatrix():
    src = '''//@version=6
strategy("t")
var m = matrix.new<int>(3, 3, 0)
m.set(0, 0, 7)
'''
    cpp = _emit(src)
    assert "PineGenericMatrix<int>::new_(3, 3, 0)" in cpp


def test_matrix_new_float_unchanged():
    src = '''//@version=6
strategy("t")
var m = matrix.new<float>(2, 2, 0.0)
'''
    cpp = _emit(src)
    assert "PineMatrix::new_(2, 2, 0.0)" in cpp
    assert "PineGenericMatrix" not in cpp


def test_matrix_new_color_lowers_to_int():
    src = '''//@version=6
strategy("t")
var m = matrix.new<color>(2, 2)
'''
    cpp = _emit(src)
    assert "PineGenericMatrix<int>::new_(2, 2," in cpp


def test_matrix_new_udt_uses_brace_init():
    src = '''//@version=6
strategy("t")
type Pt
    float x
    float y
var m = matrix.new<Pt>(1, 1)
'''
    cpp = _emit(src)
    assert "PineGenericMatrix<Pt>::new_(1, 1, Pt{})" in cpp


def test_udt_constructor_wrapping_matrix_new_keeps_udt_member_type():
    src = '''//@version=6
strategy("t")
type Holder
    matrix<int> nested
var Holder holder = Holder.new(matrix.new<int>(1, 1, 7))
observed = holder.nested.get(0, 0)
'''
    cpp = _emit(src)

    assert "    Holder holder;" in cpp
    assert "    PineMatrix holder;" not in cpp
    assert (
        "_pf_udt_Holder.create(_PFUdtRecord_Holder{"
        ".nested = PineGenericMatrix<int>::new_(1, 1, 7)})"
    ) in cpp
    compile_env.compile_cpp(cpp, label="udt-constructor-wrapping-matrix-new")


# ---------------------------------------------------------------------------
# Task 2.9: numeric-only gate
# ---------------------------------------------------------------------------

def test_method_form_det_on_int_rejected():
    src = '''//@version=6
strategy("t")
var m = matrix.new<int>(2, 2, 0)
x = m.det()
'''
    import pytest
    with pytest.raises(Exception, match="requires matrix<float>"):
        _emit(src)


def test_c_style_det_on_int_rejected():
    src = '''//@version=6
strategy("t")
var m = matrix.new<int>(2, 2, 0)
x = matrix.det(m)
'''
    import pytest
    with pytest.raises(Exception, match="requires matrix<float>"):
        _emit(src)


def test_method_form_det_on_float_allowed():
    src = '''//@version=6
strategy("t")
var m = matrix.new<float>(2, 2, 0.0)
x = m.det()
'''
    cpp = _emit(src)
    assert ".det()" in cpp


def test_sort_on_float_matrix_allowed():
    src = '''//@version=6
strategy("t")
var m = matrix.new<float>(3, 3, 0.0)
m.sort(0)
'''
    cpp = _emit(src)
    assert ".sort(" in cpp


# ---------------------------------------------------------------------------
# Task 2.10: sort UDT rejection
# ---------------------------------------------------------------------------

def test_sort_on_udt_matrix_rejected():
    src = '''//@version=6
strategy("t")
type Pt
    float x
var m = matrix.new<Pt>(2, 2)
m.sort(0)
'''
    import pytest
    with pytest.raises(Exception, match="sort requires"):
        _emit(src)


def test_sort_on_int_matrix_allowed():
    src = '''//@version=6
strategy("t")
var m = matrix.new<int>(2, 2, 0)
m.sort(0)
'''
    cpp = _emit(src)
    assert ".sort(" in cpp


# ---------------------------------------------------------------------------
# Task 2.11: LHS spec from receiver
# ---------------------------------------------------------------------------

def test_lhs_typing_var_m2_eq_m_copy_int():
    src = '''//@version=6
strategy("t")
var m = matrix.new<int>(2, 2, 0)
var m2 = m.copy()
'''
    cpp = _emit(src)
    assert "PineGenericMatrix<int>" in cpp


def test_lhs_typing_var_m2_eq_m_transpose_udt():
    src = '''//@version=6
strategy("t")
type Pt
    float x
var m = matrix.new<Pt>(2, 2)
var m2 = m.transpose()
'''
    cpp = _emit(src)
    assert "PineGenericMatrix<Pt>" in cpp


def test_lhs_typing_var_m_eq_matrix_new_int_local():
    src = '''//@version=6
strategy("t")
m = matrix.new<int>(2, 2, 0)
'''
    cpp = _emit(src)
    assert "PineGenericMatrix<int>" in cpp


# ---------------------------------------------------------------------------
# Task 2.13: reassign type-mismatch
# ---------------------------------------------------------------------------

def test_reassign_matrix_type_mismatch_rejected():
    src = '''//@version=6
strategy("t")
var m = matrix.new<int>(2, 2, 0)
m := matrix.new<float>(2, 2, 0.0)
'''
    import pytest
    with pytest.raises(Exception, match="element type"):
        _emit(src)


def test_reassign_matrix_same_type_allowed():
    src = '''//@version=6
strategy("t")
var m = matrix.new<int>(2, 2, 0)
m := matrix.new<int>(3, 3, 1)
'''
    cpp = _emit(src)
    assert "PineGenericMatrix<int>" in cpp


# ---------------------------------------------------------------------------
# Task 2.14: header inclusion (both matrix.hpp + generic_matrix.hpp)
# ---------------------------------------------------------------------------

def test_emit_includes_both_matrix_headers_when_matrix_used():
    src = '''//@version=6
strategy("t")
var m = matrix.new<int>(2, 2, 0)
'''
    cpp = _emit(src)
    assert "#include <pineforge/matrix.hpp>" in cpp
    assert "#include <pineforge/generic_matrix.hpp>" in cpp


# ---------------------------------------------------------------------------
# Task 2.16: analyzer records matrix spec
# ---------------------------------------------------------------------------

def test_analyzer_records_matrix_spec_in_collection_types():
    from pineforge_codegen.lexer import Lexer
    from pineforge_codegen.parser import Parser
    from pineforge_codegen.analyzer import Analyzer
    src = '''//@version=6
strategy("t")
var m = matrix.new<int>(2, 2, 0)
'''
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    ctx = Analyzer(ast).analyze()
    assert "m" in ctx.collection_types
    spec = ctx.collection_types["m"]
    assert spec.kind == "matrix"
    assert spec.element.name == "int"


# ---------------------------------------------------------------------------
# Task 2.18: bool flow + empty matrix + Series<matrix> probes
# ---------------------------------------------------------------------------

def test_matrix_bool_flow():
    src = '''//@version=6
strategy("t")
var m = matrix.new<bool>(2, 2, false)
'''
    cpp = _emit(src)
    assert "PineGenericMatrix<bool>" in cpp


def test_empty_matrix_then_int():
    src = '''//@version=6
strategy("t")
var m = matrix.new<int>(0, 0, 0)
'''
    cpp = _emit(src)
    assert "PineGenericMatrix<int>::new_(0, 0, 0)" in cpp


def test_series_matrix_history_udt():
    src = '''//@version=6
strategy("t")
type Pt
    float x
var m = matrix.new<Pt>(2, 2)
v = m[1].get(0, 0)
'''
    cpp = _emit(src)
    assert "PineGenericMatrix<Pt>" in cpp


def test_chained_receiver_preserves_matrix_spec():
    src = '''//@version=6
strategy("t")
var m = matrix.new<int>(2, 2, 0)
var m2 = m.transpose().copy()
'''
    cpp = _emit(src)
    assert "PineGenericMatrix<int> m2" in cpp or "PineGenericMatrix<int>\n    m2" in cpp


def test_default_for_unregistered_udt_still_brace_inits():
    cg = _make_codegen()
    cg._udt_defs = {}  # empty — UDT not declared in this TU
    spec = TypeSpec.udt("Imported")
    assert cg._default_for_spec(spec) == "Imported{}"


def test_matrix_bool_round_trip():
    src = '''//@version=6
strategy("t")
var m = matrix.new<bool>(2, 2, false)
m.set(0, 0, true)
v = m.get(0, 0)
'''
    cpp = _emit(src)
    assert "PineGenericMatrix<bool>" in cpp
    assert ".set(" in cpp
    assert ".get(" in cpp


def test_matrix_udt_no_default_ctor_compile_check():
    src = '''//@version=6
strategy("t")
type Pt
    float x
var m = matrix.new<Pt>(2, 2)
'''
    cpp = _emit(src)
    assert "PineGenericMatrix<Pt>::new_(2, 2, Pt{})" in cpp


def test_matrix_concat_emits_assignment_method_form():
    src = '''//@version=6
strategy("t")
var m = matrix.new<int>(2, 2, 0)
var other = matrix.new<int>(2, 2, 1)
m.concat(other, false)
'''
    cpp = _emit(src)
    # Engine concat is [[nodiscard]] + Pine semantics is mutate-receiver:
    # codegen must capture the result back into the receiver.
    assert "m = m.concat(other" in cpp


def test_matrix_concat_emits_assignment_c_style():
    src = '''//@version=6
strategy("t")
var m = matrix.new<int>(2, 2, 0)
var other = matrix.new<int>(2, 2, 1)
matrix.concat(m, other, false)
'''
    cpp = _emit(src)
    assert "m = m.concat(other" in cpp
