"""Collection and numeric domains survive incomplete scalar inference."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.analyzer.operators import join_value_types, resolve_operator
from pineforge_codegen.ast_nodes import VarDecl
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.symbols import TypeSpec


HEADER = '//@version=6\nstrategy("Operand domains", currency = currency.NONE)\n'


@pytest.fixture
def forbid_emission(monkeypatch):
    def unexpected_emission(self):
        pytest.fail("An invalid operand reached C++ generation")

    monkeypatch.setattr(CodeGen, "generate", unexpected_emission)


@pytest.mark.parametrize("body,code,line,col", [
    # TV CE10123 4:11-20: array history cannot fill a numeric + slot.
    ('parts_v = str.split("a,b", ",")\nprobe_v = parts_v[1] + 1.0\n',
     "PF-E2066", 4, 11),
    # TV CE10123 4:11-20: array history cannot fill a string + slot.
    ('parts_v = str.split("a,b", ",")\nprobe_v = parts_v[1] + "x"\n',
     "PF-E2066", 4, 11),
    # TV CE10123 4:11-20: collection equality has no scalar overload.
    ('parts_v = str.split("a,b", ",")\nprobe_v = parts_v[1] == parts_v\n',
     "PF-E2066", 4, 11),
    # TV CE10123 3:11-13: a numeric result cannot concatenate a string.
    ('probe_v = "a" + math.round(close)\n', "PF-E2066", 3, 11),
    # TV CE10123 3:15-31: a numeric result cannot fill not's bool slot.
    ('probe_v = not math.round(close)\n', "PF-E2048", 3, 15),
    # TV CE10123 3:11-27: a numeric result cannot fill and's bool slot.
    ('probe_v = math.round(close) and true\n', "PF-E2066", 3, 11),
    # TV CE10123 3:40-43: bool cannot fill numeric equality's second slot.
    ('probe_v = math.max(close, nz(open)) == true\n', "PF-E2066", 3, 40),
])
def test_measured_incomplete_domains_refuse_at_the_tv_operand(
    body, code, line, col, forbid_emission,
):
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + body)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.code == code
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)


@pytest.mark.parametrize("body", [
    # A declaration preserves the math result's numeric family.
    'rounded_v = math.round(close)\nprobe_v = "a" + rounded_v\n',
    # A selection keeps the collection returned by either arm.
    'parts_v = str.split("a,b", ",")\n'
    'chosen_v = close > open ? parts_v : parts_v\n'
    'probe_v = chosen_v[1] + 1.0\n',
    # Direct builtin results carry structured domains without a binding.
    'probe_v = str.split("a,b", ",") + 1.0\n',
])
def test_binding_and_call_paths_preserve_the_refused_domain(body, forbid_emission):
    # Derived family rows, without an exact-form TV position measurement.
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + body)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E2066"
    assert diagnostic.location.line > 2
    assert diagnostic.location.col > 0


def test_array_history_through_a_call_parameter_is_refused_by_its_own_code(forbid_emission):
    # The history of an array parameter is refused by its own code (PF-E6073,
    # in main's catalog: a collection parameter's history is not supported),
    # so this route is stopped before the operator pair and keeps that code.
    body = ('pick_f(value_v) => value_v[1] + 1.0\n'
            'parts_v = str.split("a,b", ",")\nprobe_v = pick_f(parts_v)\n')
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + body)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E6073"


@pytest.mark.parametrize("expression", [
    "math.round(close) + 1.5",
    "math.max(1, 2.0) * 3",
    "math.abs(nz(close)) + 1.5",
    "math.avg(close, open) + 1",
    "math.round(close) > 1.5",
])
def test_numeric_width_does_not_restrict_numeric_operators(expression):
    # Acceptance controls: both int and float bind every numeric slot.
    assert isinstance(transpile(HEADER + f"probe_v = {expression}\n"), str)


@pytest.mark.parametrize("expression", [
    "math.round(close)", "math.avg(close, open)", "math.abs(nz(close))",
    "math.max(close, nz(open))", "math.min(close, nz(open))",
    "math.sign(nz(close))",
])
def test_math_result_retains_number_domain_when_width_is_open(expression):
    # Each math result is numeric even where no exact int/float proof exists.
    source = HEADER + f"probe_v = {expression}\n"
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    analyzer = Analyzer(ast)
    analyzer.analyze()
    declaration = next(
        node for node in ast.body if isinstance(node, VarDecl) and node.name == "probe_v"
    )
    assert analyzer._operator_operand_type(declaration.value) == TypeSpec.primitive("number")


@pytest.mark.parametrize("operator", ["+", "-", "*", "/", "%", "<", "=="])
@pytest.mark.parametrize("width", ["int", "float", "number"])
def test_number_domain_binds_numeric_slots(operator, width):
    # An open numeric width is compatible with either concrete numeric width.
    assert resolve_operator(operator, (
        TypeSpec.primitive("number"), TypeSpec.primitive(width),
    ))[1] is None


@pytest.mark.parametrize("width,expected", [
    ("int", "number"), ("float", "float"), ("number", "number"),
])
def test_numeric_join_keeps_width_uncertainty(width, expected):
    # Float promotion is definite; int cannot determine an open peer's width.
    assert join_value_types(
        TypeSpec.primitive("number"), TypeSpec.primitive(width),
    ) == TypeSpec.primitive(expected)


@pytest.mark.parametrize("body,code,line,col", [
    # Retained pins, green before and after: TV CE10123 excludes these domains.
    ('probe_v = "a" - "b"\n', "PF-E2066", 3, 11),  # TV 3:11-13.
    ('probe_v = true * 1\n', "PF-E2066", 3, 11),  # TV 3:11-14.
    ('f_v = close\nprobe_v = f_v ? 1 : 0\n', "PF-E2077", 4, 11),  # TV 4:11-13.
    ('probe_v = switch bar_index\n    "a" => 1.0\n    => 2.0\n',
     "PF-E2066", 4, 5),  # TV 4:5-7: string cannot fill numeric ==.
    ('map_v = map.new<string, float>()\nprobe_v = map_v + 1\n',
     "PF-E2066", 4, 11),  # TV 4:11-15: map has no numeric +.
    ('mat_v = matrix.new<float>(1, 1, 1.0)\nprobe_v = mat_v < 1.0\n',
     "PF-E2066", 4, 11),  # TV 4:11-15: matrix has no numeric <.
    ('box_v = box.new(0, high, 1, low)\nprobe_v = box_v + 1\n',
     "PF-E2066", 4, 11),  # TV 4:11-15: box has no numeric +.
    ('tab_v = table.new(position.top_right, 1, 1)\nprobe_v = tab_v + 1\n',
     "PF-E2066", 4, 11),  # TV 4:11-15: table has no numeric +.
])
def test_measured_existing_domain_refusals_keep_their_locations(
    body, code, line, col, forbid_emission,
):
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + body)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.level is Level.ERROR
    assert diagnostic.code == code
    assert (diagnostic.location.line, diagnostic.location.col) == (line, col)


@pytest.mark.parametrize("body", [
    'values_v = array.from(1.0)\nprobe_v = values_v[1] + 1\n',
    'type Cell\n    float value\ncell_v = Cell.new(close)\nprobe_v = cell_v[1] + 1\n',
])
def test_history_operator_user_message_keeps_the_tradingview_code(body, forbid_emission):
    # The common operand refusal replaces history-specific text, keeping CE10123.
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + body)
    (diagnostic,) = caught.value.diagnostics
    assert diagnostic.code == "PF-E2066"
    assert "CE10123" in diagnostic.user_message
