"""Primitive storage checks use Pine types before C++ emission."""

import pytest

from pineforge_codegen import render_diagnostic, transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.symbols import PineType, TypeSpec


HEADER = '//@version=6\nstrategy("Initializer compatibility")\n'


@pytest.mark.parametrize("reassign", [False, True], ids=["initializer", "assignment"])
@pytest.mark.parametrize(
    "hint, name, initial, value, actual, plot_expression",
    [
        ("float", "value", "0.0", '"text"', "string", "value"),
        ("int", "count", "0", '"3"', "string", "count"),
        ("bool", "flag", "false", "1.5", "float", "flag ? 1 : 0"),
        ("string", "s", '""', "1", "int", "str.length(s)"),
    ],
)
def test_primitive_slot_refuses_incompatible_value(
    hint, name, initial, value, actual, plot_expression, reassign,
):
    declaration = f"{hint} {name} = "
    if reassign:
        prefix = f"{name} := "
        body = f"{declaration}{initial}\n{prefix}{value}\n"
        line = 4
        context = "assignment"
    else:
        prefix = declaration
        body = f"{prefix}{value}\n"
        line = 3
        context = f"declaration '{name}'"
    source = HEADER + body + f"plot({plot_expression})\n"

    with pytest.raises(CompileError) as caught:
        transpile(source, filename="compatibility.pine")

    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    args = {"actual": actual, "expected": hint, "context": context}
    message, diagnostic_hint = render_diagnostic("PF-E2093", args)
    assert diagnostic.code == "PF-E2093"
    assert diagnostic.args == args
    assert diagnostic.message == message
    assert diagnostic.hint == diagnostic_hint
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.location.file == "compatibility.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, len(prefix) + 1)


@pytest.mark.parametrize(
    "body",
    [
        "float value = 1\nplot(value)\n",
        "float value = na\nplot(value)\n",
        "int count = 3\nplot(count)\n",
        'string s = "a"\nplot(str.length(s))\n',
        "var float value = 1\nvalue := na\nvalue := close\nplot(value)\n",
        "varip float value = na\nvalue := 1\nvalue := 1.5\nplot(value)\n",
        "float value = 0.0\nvalue := 1\nvalue := na\nvalue := 1.5\nplot(value)\n",
        "float value = input.int(1)\nvalue := 2\nvalue := 2.5\nplot(value)\n",
        "simple float value = 1\nplot(value)\n",
        "series float value = close\nplot(value)\n",
    ],
)
def test_compatible_primitive_values_transpile(body):
    assert transpile(HEADER + body)


@pytest.mark.parametrize("initializer", ["1", "na", "input.int(1)"])
def test_float_declaration_keeps_its_type_after_initialization_and_assignment(initializer):
    source = HEADER + f"float value = {initializer}\nvalue := 2\n"
    tokens = Lexer(source).tokenize()
    ast = Parser(tokens, source=source).parse()
    context = Analyzer(ast).analyze()

    symbol = context.symbols.resolve("value")
    assert symbol is not None
    assert symbol.pine_type is PineType.FLOAT
    assert symbol.type_spec == TypeSpec.primitive("float")
