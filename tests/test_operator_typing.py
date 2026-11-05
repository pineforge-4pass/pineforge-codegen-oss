"""Measured operator overloads and their located analysis diagnostics."""

import json
from pathlib import Path
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.analyzer.operators import (
    fixed_builtin_result, join_value_types, resolve_operator, source_builtin_result,
)
from pineforge_codegen.ast_nodes import BinOp, VarDecl
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.symbols import TypeSpec
from tests._compile import compile_cpp


FIXTURES = Path(__file__).parent / "fixtures"
TAPES = json.loads((FIXTURES / "operator_typing_tv.json").read_text())
FOLLOWUPS = json.loads((FIXTURES / "operator_typing_followups.json").read_text())
TAPES += FOLLOWUPS
STRATEGIES = json.loads(
    (FIXTURES / "operator_typing_strategies.json").read_text()
)
ACCEPTED = [row for row in TAPES if row["tv"].get("accepted")]
EMITTABLE = [row for row in ACCEPTED if row.get("validation", "compile") == "compile"]
ANALYZER_ACCEPTED = [row for row in ACCEPTED if row.get("validation") == "analyzer"]
REFUSED = [row for row in TAPES if "expected" in row]


@pytest.fixture
def forbid_emission(monkeypatch):
    def unexpected_emission(self):
        pytest.fail("An incompatible operator reached C++ generation")

    monkeypatch.setattr(CodeGen, "generate", unexpected_emission)


@pytest.mark.parametrize("row", STRATEGIES, ids=lambda row: row["name"])
def test_string_number_strategy_is_refused_before_emission(row, forbid_emission):
    # The 22-character prefix 'bool f1_bool = "a" == ' puts the int at 3:23.
    with pytest.raises(CompileError) as caught:
        transpile(row["source"], filename=f'{row["name"]}.pine')
    (diag,) = caught.value.diagnostics
    assert diag.phase is Phase.ANALYZER
    assert diag.level is Level.ERROR
    assert diag.code == "PF-E2066"
    assert diag.args == {"op": "==", "left": "string", "right": "int"}
    assert (diag.location.line, diag.location.col, diag.location.end_col) == (
        3, 23, 24,
    )
    assert diag.location.file == f'{row["name"]}.pine'


@pytest.mark.parametrize("row", REFUSED, ids=lambda row: row["probe"])
def test_refused_operator_tape_has_an_operand_diagnostic(row, forbid_emission):
    # Each fixture's derivation binds the domains and counts to the first
    # token of the incompatible operand, independently of TV's column.
    with pytest.raises(CompileError) as caught:
        transpile(row["source"], filename=f'{row["probe"]}.pine')
    (diag,) = caught.value.diagnostics
    expected = row["expected"]
    assert diag.phase is Phase.ANALYZER, row["derivation"]
    assert diag.level is Level.ERROR
    assert diag.code == expected["code"], row["derivation"]
    assert (diag.location.line, diag.location.col) == (
        expected["line"], expected["col"],
    ), row["derivation"]
    assert diag.location.file == f'{row["probe"]}.pine'
    assert diag.location.end_col > diag.location.col
    line = row["source"].splitlines()[expected["line"] - 1]
    assert line[expected["col"] - 1:].startswith(row["operand"])


@pytest.mark.parametrize("row", EMITTABLE, ids=lambda row: row["probe"])
def test_accepted_operator_tape_compiles(row):
    # These are existing acceptance pins: the applicable overload preserves
    # the emitter's numeric wrappers, strings, handles and selected IDs.
    cpp = transpile(row["source"])
    compile_cpp(cpp, label=row["probe"])


@pytest.mark.parametrize("row", EMITTABLE, ids=lambda row: row["probe"])
def test_accepted_operators_emit_values_without_placeholders(row):
    # ExprVisitor's fallbacks spell missing expressions as these comments.
    cpp = transpile(row["source"])
    assert "/* unknown */" not in cpp
    assert "/* null */" not in cpp


@pytest.mark.parametrize(
    "namespace,name,arity",
    [
        # Each result depends on a source family or an overload, so the
        # registry's primary result cannot constrain an operator.
        ("ta", "change", 1),
        ("ta", "valuewhen", 3),
        ("ta", "highest", 2),
        ("ta", "lowest", 2),
        ("math", "max", 2),
        ("math", "abs", 1),
        ("math", "round", 2),
        ("input", "enum", 1),
    ],
)
def test_polymorphic_registry_results_remain_unresolved(namespace, name, arity):
    assert fixed_builtin_result(namespace, name, arity) is None


@pytest.mark.parametrize(
    "namespace,name,arguments",
    [
        # An unresolved source cannot prove which overload returns its value.
        ("ta", "change", (None,)),
        ("ta", "valuewhen", (TypeSpec.primitive("bool"), None)),
        ("ta", "highest", (None, TypeSpec.primitive("int"))),
    ],
)
def test_source_dependent_overloads_require_proven_sources(namespace, name, arguments):
    assert source_builtin_result(namespace, name, arguments) is None


def test_math_result_with_an_unproven_source_is_a_number_of_open_width():
    # Every math result is numeric whatever its sources; only int against
    # float stays unresolved (it fits every numeric slot, no string or bool).
    assert source_builtin_result(
        "math", "max", (TypeSpec.primitive("int"), None),
    ) == TypeSpec.primitive("number")


@pytest.mark.parametrize("sentinel", ["unknown", "void"])
@pytest.mark.parametrize("operator,peer", [
    # A non-value inference sentinel supplies no numeric or boolean constraint.
    (">=", "float"), ("*", "float"), ("and", "bool"),
])
def test_callable_nonvalue_facts_do_not_refuse_value_slots(sentinel, operator, peer):
    assert resolve_operator(
        operator, (TypeSpec.primitive(peer), TypeSpec.primitive(sentinel)),
    ) == (None, None)


@pytest.mark.parametrize("variant,domain", [
    # Input variants determine their semantic domain, independently of storage.
    ("color", "color"), ("bool", "bool"), ("string", "string"),
    ("int", "int"), ("float", "float"),
])
def test_input_variants_supply_their_own_operator_domain(variant, domain):
    assert fixed_builtin_result("input", variant, 1) == TypeSpec.primitive(domain)


@pytest.mark.parametrize("left,right", [
    # Population regression, source private: these coarse arm pairs do not
    # prove an incompatible Pine selection. Each was refused before the fix.
    ("int", "color"), ("color", "int"), ("color", "float"),
    ("float", "bool"), ("bool", "float"),
])
def test_coarse_selection_pairs_do_not_prove_a_refusal(left, right):
    assert resolve_operator("?:", (
        TypeSpec.primitive("bool"),
        TypeSpec.primitive(left), TypeSpec.primitive(right),
    )) == (None, None)


@pytest.mark.parametrize("body", [
    # Population regression, source private: input.color has the same Pine
    # domain as the declared color arm, in either order (previously int/color).
    pytest.param(
        "ink = input.color(color.red)\ncolor prior = color.blue\n"
        "selected = flag ? ink : prior\nplot(close, color = selected)\n",
        id="input-color-first",
    ),
    pytest.param(
        "ink = input.color(color.red)\ncolor prior = color.blue\n"
        "selected = flag ? prior : ink\nplot(close, color = selected)\n",
        id="input-color-second",
    ),
    # Population regression, source private: valuewhen preserves a color
    # source; the primary float registry slot cannot constrain the selection.
    pytest.param(
        "selected = flag ? color.red : ta.valuewhen(flag, color.blue, 0)\n"
        "plot(close, color = selected)\n",
        id="color-source-selection",
    ),
    # Population regression, source private: change(bool) and valuewhen(bool)
    # supply bool arms and logical operands, rather than coarse float slots.
    pytest.param(
        "selected = flag ? ta.change(flag) : false\nplot(selected ? 1 : 0)\n",
        id="bool-source-selection",
    ),
    pytest.param(
        "selected = flag or ta.change(flag)\nplot(selected ? 1 : 0)\n",
        id="bool-source-disjunction",
    ),
    pytest.param(
        "selected = ta.valuewhen(flag, flag, 0) and flag\nplot(selected ? 1 : 0)\n",
        id="bool-source-left-conjunction",
    ),
    pytest.param(
        "selected = flag and ta.valuewhen(flag, flag, 0)\nplot(selected ? 1 : 0)\n",
        id="bool-source-right-conjunction",
    ),
    # Population regression, source private: a callable's legacy return
    # cache supplies no operator proof. These are acceptance controls, green
    # before and after; the non-value-domain tests above pin the red sentinel
    # failure independently of how a private program reached that cache.
    pytest.param(
        "method pick(float self, bool choose) =>\n"
        "    if choose\n        self\n    else\n        open\n"
        "selected = close >= close.pick(flag)\nplot(selected ? 1 : 0)\n",
        id="relational-call-result",
    ),
    pytest.param(
        "method pick(float self, bool choose) =>\n"
        "    if choose\n        self\n    else\n        open\n"
        "selected = close * close.pick(flag)\nplot(selected)\n",
        id="arithmetic-call-result",
    ),
])
def test_population_operator_families_keep_their_accepted_lowering(body):
    # Reduced public constructs only; no private source is reproduced.
    cpp = transpile('//@version=6\nstrategy("Proven operator domains")\n'
                    'flag = close > open\n' + body)
    compile_cpp(cpp, label="proven operator domains")


@pytest.mark.parametrize("row", ANALYZER_ACCEPTED, ids=lambda row: row["probe"])
def test_typed_missing_string_retains_its_analyzer_domain(row):
    # Acceptance pin: the declared string domain permits equality; storage
    # inferred from bare na is a separate emission defect.
    source = row["source"]
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    analyzer = Analyzer(ast)
    analyzer.analyze()
    probe = next(stmt for stmt in ast.body if isinstance(stmt, VarDecl) and stmt.name == "probe")
    assert analyzer._operator_operand_type(probe.value.right) == TypeSpec.primitive("string")


def test_operator_proofs_preserve_legacy_boolean_storage_and_truth_cast():
    cpp = transpile('//@version=6\nstrategy("Boolean selection storage")\n'
                    'flag = close > open\n'
                    'selected = flag ? ta.change(flag) : false\n'
                    'plot(selected ? 1 : 0)\n')
    assert "double selected = " in cpp


def test_operator_proofs_do_not_narrow_callable_local_return_storage():
    # Legacy primitive inference leaves a builtin-call alias unresolved and
    # falls back to double; an int operator proof must not change that return.
    cpp = transpile('//@version=6\nstrategy("Callable result storage")\n'
                    'maximum(x, y) =>\n    result = math.max(x, y)\n    result + 0\n'
                    'selected = maximum(2, 3)\nplot(selected)\n')
    assert re.search(r"\bdouble maximum(?:_cs\d+)?\(", cpp)
    assert "double selected = " in cpp


@pytest.mark.parametrize("float_first", [False, True])
def test_callable_numeric_return_types_remain_per_written_call(float_first):
    # Acceptance pin, green before and after: the float arguments require a
    # floating return and result member in either source order.
    calls = ["whole = choose(flag, 2, 3)\n", "fraction = choose(flag, 2.5, 3.5)\n"]
    if float_first:
        calls.reverse()
    source = ('//@version=6\nstrategy("Independent numeric calls")\n'
              'choose(cond, x, y) =>\n    cond ? x : y\n'
              'flag = close > open\n' + "".join(calls) + 'plot(fraction)\n')
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    ctx = Analyzer(ast).analyze()
    cpp = CodeGen(ctx).generate()
    # The actual assignment identifies the emitted variant in either order.
    call = re.search(r"\bfraction = (choose(?:_cs\d+)?)\(", cpp)
    assert call is not None
    assert re.search(rf"\bdouble {re.escape(call.group(1))}\(", cpp)
    assert "double fraction = " in cpp


def test_declaration_error_precedes_a_deferred_operator_condition_error(forbid_emission):
    # Priority pin, green before and after: a queued ?: constraint waits for
    # declarations, so the later bool-na declaration supplies the first error.
    source = ('//@version=6\nstrategy("Diagnostic priority")\n'
              'selected = 1 ? 2 : 3\nbool invalid = na\n')
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diag,) = caught.value.diagnostics
    assert (diag.code, diag.args, diag.phase) == ("PF-E2042", {}, Phase.ANALYZER)
    assert (diag.location.line, diag.location.col) == (4, 1)


HISTORY_EXPECTATIONS = [
    # A history value retains its collection domain; unary and condition
    # constraints name their own role, while compound addition has a float LHS.
    (f"hist_{kind}_{operation}.pine", "CE10123", code, arguments)
    for kind, domain in [
        ("array", "array<float>"), ("matrix", "matrix<float>"),
        ("map", "map<string,float>"),
    ]
    for operation, code, arguments in [
        ("binary", "PF-E2066", {"op": "+", "left": domain, "right": "int"}),
        ("unary", "PF-E2076", {"op": "-", "operand_type": domain}),
        ("cond", "PF-E2077", {"condition_type": domain}),
        ("compound", "PF-E2066", {"op": "+", "left": "float", "right": domain}),
    ]
] + [
    # The field-history restriction precedes every operator constraint.
    (f"hist_udtfield_{operation}.pine", "CE10290", "PF-E2032",
     {"receiver": "hold_h", "member": "vals"})
    for operation in ("binary", "unary", "cond", "compound")
] + [
    # Nested collection subscripts stop in the history checker, independently
    # of the operator consuming them (TV rejects the second bracket as syntax).
    (f"hist_nested_{operation}.pine", "CE10156", "PF-E6012",
     {"label": "arr_h[1]", "kind": "array"})
    for operation in ("binary", "unary", "cond", "compound")
]


@pytest.mark.parametrize(
    "filename,tv_code,code,arguments", HISTORY_EXPECTATIONS,
    ids=[row[0] for row in HISTORY_EXPECTATIONS],
)
def test_collection_history_operand_refusals(filename, tv_code, code, arguments, forbid_emission):
    source = (FIXTURES / "operator_history_tv" / filename).read_text()
    with pytest.raises(CompileError) as caught:
        transpile(source, filename=filename)
    (diag,) = caught.value.diagnostics
    assert (diag.code, diag.args, diag.phase) == (code, arguments, Phase.ANALYZER), tv_code
    assert diag.level is Level.ERROR
    assert diag.location.file == filename


def test_unsupported_parameter_history_keeps_its_collection_operand_domain(forbid_emission):
    # A typed parameter's history formerly took LEGACY, so checking only
    # SUPPORTED histories lost the array domain and accepted this addition.
    source = ('//@version=6\nstrategy("Historical parameter operand")\n'
              'addPast(array<float> items) =>\n    items[1] + 1\n'
              'values = array.from(close)\nplot(addPast(values))\n')
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diag,) = caught.value.diagnostics
    assert (diag.code, diag.args) == (
        "PF-E2066", {"op": "+", "left": "array<float>", "right": "int"},
    )
    assert (diag.location.line, diag.location.col) == (4, 5)


@pytest.mark.parametrize(
    "left,right,expected",
    [
        # Numeric value joins promote, while collection arguments stay invariant.
        (TypeSpec.primitive("int"), TypeSpec.primitive("float"), TypeSpec.primitive("float")),
        (TypeSpec.primitive("na"), TypeSpec.primitive("string"), TypeSpec.primitive("string")),
        (TypeSpec.primitive("na"), TypeSpec.primitive("bool"), None),
        (TypeSpec.array(TypeSpec.primitive("int")), TypeSpec.array(TypeSpec.primitive("float")), None),
        # Nominal identity is retained for enums, just as for other named types.
        (TypeSpec("enum", name="Mode"), TypeSpec("enum", name="Mode"), TypeSpec("enum", name="Mode")),
        (TypeSpec("enum", name="Mode"), TypeSpec("enum", name="Side"), None),
    ],
)
def test_value_type_join_preserves_nullable_and_nominal_constraints(left, right, expected):
    assert join_value_types(left, right) == expected


@pytest.mark.parametrize(
    "body,expected",
    [
        # Source-based overloads preserve bool and int through an alias;
        # nz has no proven operator result and must retain that uncertainty.
        ("flag = close > open\nr = ta.change(flag) == true\n", "bool"),
        ("flag = close > open\nv = ta.change(flag)\nr = v == true\n", "bool"),
        ("v = ta.valuewhen(close > open, close > open, 0)\nr = v == true\n", "bool"),
        ("v = nz(close)\nr = v == close\n", None),
        ("v = math.max(1, 2)\nr = v == 2\n", "int"),
    ],
)
def test_polymorphic_call_facts_survive_aliases_without_a_scalar_default(body, expected):
    source = '//@version=6\nstrategy("Open result types")\n' + body
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    analyzer = Analyzer(ast)
    analyzer.analyze()
    result = next(stmt for stmt in ast.body if isinstance(stmt, VarDecl) and stmt.name == "r")
    assert isinstance(result.value, BinOp)
    assert analyzer._operator_operand_type(result.value.left) == (
        None if expected is None else TypeSpec.primitive(expected)
    )
    assert resolve_operator(
        "==", (None, TypeSpec.primitive("bool")),
    ) == (None, None)


@pytest.mark.parametrize(
    "expression,code,message,arguments,col",
    [
        # The prefix plus sign puts true at column 10; its domain is bool.
        ("+true", "PF-E2076",
         "Operator '+' requires a numeric operand; found bool.",
         {"op": "+", "operand_type": "bool"}, 10),
        # A unary minus has the same numeric requirement for a string operand.
        ('-"s"', "PF-E2076",
         "Operator '-' requires a numeric operand; found string.",
         {"op": "-", "operand_type": "string"}, 10),
        # The conditional's first operand starts after the eight-character prefix.
        ("na ? 1 : 2", "PF-E2077",
         "The ?: operator requires a bool condition; found na.",
         {"condition_type": "na"}, 9),
    ],
)
def test_operator_requirements_name_the_operand_role(
    expression, code, message, arguments, col, forbid_emission,
):
    source = '//@version=6\nstrategy("Operand roles")\n' + f"value = {expression}\n"
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diag,) = caught.value.diagnostics
    assert (diag.code, diag.message, diag.args) == (code, message, arguments)
    assert (diag.location.line, diag.location.col) == (3, col)


@pytest.mark.parametrize(
    "probe,fragment",
    [
        # String equality remains std::string equality, without numeric casts.
        ("accept-real-close", '(std::string("a") == std::string("a"))'),
    ],
)
def test_nullable_and_string_operator_emission_pins(probe, fragment):
    row = next(row for row in ACCEPTED if row["probe"] == probe)
    assert fragment in transpile(row["source"])


@pytest.mark.parametrize(
    "expression",
    [
        '"a" + na',
        'na + "a"',
    ],
)
def test_string_concatenation_with_na_is_accepted(expression):
    # Acceptance pin, green before and after: na binds the nullable string
    # operand domain of + in either position; this asserts translation only.
    source = ('//@version=6\nstrategy("Nullable string operands")\n'
              f'string result = {expression}\n')
    assert isinstance(transpile(source), str)
