"""Operator constraints with incomplete type facts and lexical free names."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.analyzer.operators import resolve_operator
from pineforge_codegen.ast_nodes import VarDecl
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.symbols import TypeSpec
from tests._compile import run_emitted_tu


HEADER = (
    '//@version=6\n'
    'strategy("Operator constraints", currency = currency.NONE, initial_capital = 1000000)\n'
    '// Operand types determine operator applicability.\n'
)
ORDERS = (
    'if bar_index > 6 and bar_index % 7 == 3\n'
    '    strategy.entry("L", strategy.long, qty = 1)\n'
    'if bar_index > 6 and bar_index % 7 == 5\n'
    '    strategy.close("L")\n'
)


@pytest.fixture
def forbid_emission(monkeypatch):
    def unexpected_emission(self):
        pytest.fail("An invalid operand reached C++ generation")

    monkeypatch.setattr(CodeGen, "generate", unexpected_emission)


@pytest.mark.parametrize(
    "body,code,line,col",
    [
        # TV CE10123 5:23: array history cannot bind either + overload.
        pytest.param(
            "arr_h = array.from(1.0)\nprobe_v = nz(close) + arr_h[1]\nplot(probe_v)\n",
            "PF-E2066", 5, 23, id="builtin-and-array-history",
        ),
        # TV CE10123 7:22: the unresolved call does not erase the array domain.
        pytest.param(
            "arr_h = array.from(1.0)\npick_f() =>\n    close * 2\n"
            "probe_v = pick_f() + arr_h[1]\nplot(probe_v)\n",
            "PF-E2066", 7, 22, id="call-and-array-history",
        ),
        # TV CE10123 5:11: bool cannot bind the numeric slot of <.
        pytest.param(
            "flag_v = close > open\nprobe_v = flag_v < 1\nplot(probe_v ? 1 : 0)\n",
            "PF-E2066", 5, 11, id="bool-ordering",
        ),
        # TV CE10123 7:11: > returns bool; our equality points at numeric num_v.
        # Retained refusal pin: both inner operands are already proven numeric.
        pytest.param(
            "lhs_v = close\nrhs_v = open\nnum_v = 1.0\n"
            "probe_v = lhs_v > rhs_v == num_v\nplot(probe_v ? 1 : 0)\n",
            "PF-E2049", 7, 28, id="comparison-chain",
        ),
        # TV CE10123 6:5: switch compares the string discriminator to an int.
        pytest.param(
            'disc_v = "a"\nprobe_v = switch disc_v\n    1 => 1.0\n'
            "    => 2.0\nplot(probe_v)\n",
            "PF-E2066", 6, 5, id="switch-case-equality",
        ),
        # TV CE10123 5:26: bool and int have no common arm type.
        # Retained refusal pin, green before and after.
        pytest.param(
            "sel_v = close > open\nprobe_v = sel_v ? true : 1\nplot(probe_v ? 1 : 0)\n",
            "PF-E2066", 5, 26, id="bool-int-selection",
        ),
        # TV CE10101 4:7: na cannot fill a condition's bool slot.
        pytest.param(
            "while na\n    break\nplot(close)\n",
            "PF-E2062", 4, 7, id="missing-while-condition",
        ),
    ],
)
def test_measured_operator_constraints_stop_before_emission(
    body, code, line, col, forbid_emission,
):
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + body + ORDERS)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.level is Level.ERROR
    assert diagnostic.code == code
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)


@pytest.mark.parametrize("constructor", [
    pytest.param("array.from(1.0)", id="array"),
    pytest.param("matrix.new<float>(1, 1, 1.0)", id="matrix"),
])
@pytest.mark.parametrize("expression", ["nz(close)", "pick_f()"])
@pytest.mark.parametrize("history_first", [False, True])
def test_history_operand_rejects_addition_with_an_unresolved_peer(
    constructor, expression, history_first, forbid_emission,
):
    # TV's array-history CE10123 establishes the nonnumeric operand rule;
    # matrices have the same absence of a + overload in either position.
    left, right = ("values[1]", expression) if history_first else (expression, "values[1]")
    source = (
        HEADER + f"values = {constructor}\npick_f() => close * 2\n"
        f"probe_v = {left} + {right}\nplot(probe_v)\n"
    )
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E2066"
    assert diagnostic.args["op"] == "+"
    # The history starts at 11 on the left, or after the call and three chars.
    assert (diagnostic.location.line, diagnostic.location.col) == (
        6, 11 if history_first else 11 + len(expression) + 3,
    )


@pytest.mark.parametrize("constructor", [
    "array.from(1.0)", "matrix.new<float>(1, 1, 1.0)",
])
def test_compound_addition_retains_the_history_operand_domain(constructor, forbid_emission):
    # += uses the same numeric/string overloads as +; values starts at 8.
    source = HEADER + f"values = {constructor}\nacc = nz(close)\nacc += values[1]\n"
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E2066"
    assert (diagnostic.location.line, diagnostic.location.col) == (6, 8)


def test_object_operand_rejects_addition_with_an_unresolved_call(forbid_emission):
    # A nominal object, like a collection, supplies neither number nor string.
    source = (
        HEADER + "type Cell\n    float value\nudt_obj = Cell.new(close)\n"
        "pick_f() => close\nprobe_v = pick_f() + udt_obj\n"
    )
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E2066"
    assert (diagnostic.location.line, diagnostic.location.col) == (8, 22)


@pytest.mark.parametrize("operator", ["+", "-", "*", "/", "%", "<", "<=", ">", ">="])
@pytest.mark.parametrize("domain", [
    TypeSpec.array(TypeSpec.primitive("float")),
    TypeSpec.matrix(TypeSpec.primitive("float")),
    TypeSpec.map(TypeSpec.primitive("string"), TypeSpec.primitive("float")),
    TypeSpec.udt("Cell"), TypeSpec.udt("box"), TypeSpec.udt("table"),
    TypeSpec.primitive("bool"),
])
@pytest.mark.parametrize("position", [0, 1])
def test_impossible_operand_position_refuses_without_a_peer_type(operator, domain, position):
    # No signature in these families admits the proven nonnumeric domain.
    operands = (domain, None) if position == 0 else (None, domain)
    assert resolve_operator(operator, operands) == (None, position)


@pytest.mark.parametrize("operator", ["-", "*", "/", "%", "<", "<=", ">", ">="])
@pytest.mark.parametrize("position", [0, 1])
def test_string_cannot_fill_a_numeric_slot_with_an_unresolved_peer(operator, position):
    # The string overload belongs only to +, not to numeric-only families.
    domain = TypeSpec.primitive("string")
    operands = (domain, None) if position == 0 else (None, domain)
    assert resolve_operator(operator, operands) == (None, position)


@pytest.mark.parametrize("operator", ["+", "-", "*", "/", "%", "==", "!=", "<", "<=", ">", ">="])
@pytest.mark.parametrize("operands", [
    (None, None), (TypeSpec.primitive("int"), None), (None, TypeSpec.primitive("float")),
])
def test_possible_operand_domains_leave_unresolved_constraints_open(operator, operands):
    # Acceptance control, green before and after: a numeric overload survives.
    assert resolve_operator(operator, operands)[1] is None


@pytest.mark.parametrize("operator,domain", [
    ("+", "string"), ("==", "string"), ("!=", "bool"), ("and", "bool"),
])
@pytest.mark.parametrize("position", [0, 1])
def test_a_possible_nonnumeric_domain_does_not_refuse_an_unresolved_peer(operator, domain, position):
    # Acceptance control: each proven slot fits at least one family overload.
    spec = TypeSpec.primitive(domain)
    operands = (spec, None) if position == 0 else (None, spec)
    assert resolve_operator(operator, operands)[1] is None


@pytest.mark.parametrize("operator", ["==", "!=", "<", "<=", ">", ">="])
def test_comparison_result_is_bool_even_when_operand_types_are_unresolved(operator):
    # A comparison's result family is fixed independently of its open inputs.
    source = HEADER + f"probe_v = nz(close) {operator} nz(open)\n"
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    analyzer = Analyzer(ast)
    analyzer.analyze()
    result = next(node for node in ast.body if isinstance(node, VarDecl) and node.name == "probe_v")
    assert analyzer._operator_operand_type(result.value) == TypeSpec.primitive("bool")


def test_invalid_comparison_still_has_a_bool_result_family():
    # Result typing is independent of operand validation: bool > unknown
    # has bool result even though its bool operand cannot bind numeric >.
    source = HEADER + "probe_v = true > nz(close)\n"
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    analyzer = Analyzer(ast)
    result = next(node for node in ast.body if isinstance(node, VarDecl) and node.name == "probe_v")
    assert analyzer._operator_operand_type(result.value) == TypeSpec.primitive("bool")


def test_unresolved_comparison_cannot_hide_a_bool_numeric_equality(forbid_emission):
    # The inner numeric call types stay open, but > still returns bool.
    source = HEADER + "probe_v = nz(close) > nz(open) == 1.0\n"
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E2049"
    assert diagnostic.args == {"op": "=="}


def test_a_switch_case_is_checked_with_the_written_parameter_type(forbid_emission):
    # The untyped definition is open; the string argument makes case 1
    # incompatible with ==, just as a string discriminator written globally.
    source = (
        HEADER + "choose_v(disc_v) =>\n    switch disc_v\n"
        "        1 => 1.0\n        => 2.0\nprobe_v = choose_v(\"a\")\n"
    )
    with pytest.raises(CompileError) as caught:
        transpile(source)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E2066"
    assert diagnostic.args == {"op": "==", "left": "string", "right": "int"}
    assert (diagnostic.location.line, diagnostic.location.col) == (6, 9)


@pytest.mark.parametrize("local", ['string x_v = "s"', "bool x_v = true"])
def test_callee_free_names_resolve_outside_the_callers_local_scope(local):
    # TV accepts the string-local source: read_f sees global 1.0, hence 2.0;
    # a bool local is the same lexical shadow, also illegal as a numeric + input.
    source = (
        HEADER + "x_v = 1.0\nread_f() =>\n    x_v + 1\ncall_g() =>\n"
        f"    {local}\n    read_f()\nprobe_v = call_g()\nplot(probe_v)\n" + ORDERS
    )
    assert isinstance(transpile(source), str)


def test_callee_free_name_shadow_preserves_the_global_value():
    # Global x_v is 1.0, so read_f returns 2.0 regardless of call_g's string.
    source = (
        HEADER + "x_v = 1.0\nread_f() =>\n    x_v + 1\ncall_g() =>\n"
        '    string x_v = "s"\n    read_f()\nprobe_v = call_g()\nplot(probe_v)\n'
    )
    driver = r"""
#include <cstdio>
int main() {
    GeneratedStrategy s;
    pineforge::Bar bar{100.0, 101.0, 99.0, 100.0, 1.0, 1743466500000LL};
    s.run(&bar, 1);
    std::printf("%.1f\n", s.probe_v);
}
"""
    assert run_emitted_tu(
        transpile(source), driver, opt="-O0", label="lexical free name",
    ).strip() == "2.0"


@pytest.mark.parametrize("branch", ["if close > open", "while close > open"])
def test_callee_free_names_ignore_branch_local_shadows(branch):
    # Lexical acceptance control: a caller block cannot change read_f's x_v.
    # The block sits in a function body: a top-level block shadow of a script
    # binding of another type is a separate, existing refusal.
    source = (
        HEADER + "x_v = 1.0\nread_f() => x_v + 1\ncall_g() =>\n    r = 0.0\n"
        f'    {branch}\n        string x_v = "s"\n        r := read_f()\n    r\n'
        "probe_v = call_g()\nplot(probe_v)\n"
    )
    assert isinstance(transpile(source), str)
