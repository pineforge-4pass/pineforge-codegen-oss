"""Comma-separated callable statements share parameters, locals and a tail value."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import FuncDef, MethodDef
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._compile import (
    compile_cpp, run_emitted_tu, skip_if_no_compile_env, skip_if_no_engine_lib,
)


_HEADER = '//@version=6\nstrategy("body scope")\n'
_TAIL = "if bar_index == 5\n    strategy.entry('L', strategy.long)\n"
_CELL = "type Cell\n    int value\n"


@dataclass(frozen=True)
class BodyCase:
    name: str
    signature: str
    statements: tuple[str, ...]
    setup: str
    call: str
    statement_count: int
    expected: str
    preamble: str = ""
    wrap_after: int | None = None


# Every statement reads the parameter, including through nested calls.
_PARAMETERS = (
    (
        "int", "int", "4",
        (
            "value",
            "math.max(math.abs(value - 1), 2)",
            "math.max(value, 6)",
            "(value + 1) * 2",
        ),
        ("3", "6", "10"),  # max(abs(4-1),2)=3; max(4,6)=6; (4+1)*2=10.
        "",
    ),
    (
        "float", "float", "2.5",
        (
            "value",
            "math.max(math.abs(value - 1.0), 0.0)",
            "math.max(value, 6.5)",
            "(value + 1.0) * 2.0",
        ),
        ("1.5", "6.5", "7"),  # abs(2.5-1)=1.5; max(2.5,6.5)=6.5; 3.5*2=7.
        "",
    ),
    (
        "bool", "bool", "true",
        (
            "value",
            "bool(not value)",
            "bool(value and true)",
            "not bool(not value)",
        ),
        ("0", "1", "1"),  # With true, the final booleans are false, true, true.
        "",
    ),
    (
        "string", "string", "'a,b'",
        (
            "value",
            "str.length(value)",
            "str.length(str.upper(value)) + 1",
            "str.length(value + ',z')",
        ),
        ("3", "4", "5"),  # 'a,b' has length 3; uppercase adds none; ',z' adds 2.
        "",
    ),
    (
        "color", "color", "color.rgb(4, 6, 8)",
        (
            "value",
            "color.r(value)",
            "color.g(color.new(value, 50))",
            "color.b(value)",
        ),
        ("4", "6", "8"),  # RGB channels are 4, 6, 8; transparency keeps them.
        "",
    ),
    (
        "int-array", "array<int>", "array.from(4, 6)",
        (
            "array.size(value)",
            "array.get(value, 0)",
            "math.max(value.get(1), 1)",
            "value.size() + value.get(0)",
        ),
        ("4", "6", "6"),  # [4,6] gives first=4, max(last,1)=6, size+first=6.
        "",
    ),
    (
        "string-array", "array<string>", "array.from('a', 'bb')",
        (
            "value.size()",
            "str.length(array.get(value, 0))",
            "str.length(value.get(1))",
            "str.length(str.upper(value.get(1))) + value.size()",
        ),
        ("1", "2", "4"),  # 'a' has length 1; 'bb' has length 2; 2+size(2)=4.
        "",
    ),
    (
        "record", "Cell", "Cell.new(4)",
        (
            "value.value",
            "math.max(math.abs(value.value - 1), 2)",
            "math.max(value.value, 6)",
            "(value.value + 1) * 2",
        ),
        ("3", "6", "10"),  # The field is 4: max(abs(3),2)=3, max(4,6)=6, 5*2=10.
        _CELL,
    ),
)

_CASES = [
    BodyCase(
        name=f"{kind}-{name}-{count}",
        signature=f"{'method ' if kind == 'method' else ''}evaluate({hint} value)",
        statements=statements[:count],
        setup=f"argument = {initializer}\n",
        call="argument.evaluate()" if kind == "method" else "evaluate(argument)",
        statement_count=count,
        expected=expected,
        preamble=preamble,
    )
    for name, hint, initializer, statements, expectations, preamble in _PARAMETERS
    # Two, three and four statements return the second, third and fourth values.
    for count, expected in zip((2, 3, 4), expectations)
    for kind in ("function", "method")
    # Unannotated color.rgb globals cannot bind user methods in either layout.
    if kind != "method" or hint != "color"
]

_CASES += [
    BodyCase(
        name="local-declaration",
        signature="evaluate(int value)",
        statements=("total = value + 1", "total + 2"),
        setup="",
        call="evaluate(4)",
        statement_count=2,
        expected="7",  # total=4+1=5, then the final expression is 5+2=7.
    ),
    BodyCase(
        name="local-assignment-tail",
        signature="evaluate(int value)",
        statements=("total = value", "total := total + 3"),
        setup="",
        call="evaluate(4)",
        statement_count=2,
        expected="7",  # The final assignment leaves total=4+3=7 and returns it.
    ),
    BodyCase(
        name="local-declaration-tail",
        signature="evaluate(int value)",
        statements=("first = value + 1", "answer = first * 2"),
        setup="",
        call="evaluate(4)",
        statement_count=2,
        expected="10",  # first=5, and the final declaration binds and returns 10.
    ),
    BodyCase(
        name="expression-before-local",
        signature="evaluate(int value)",
        statements=("math.abs(value)", "total = value + 1", "total"),
        setup="",
        call="evaluate(4)",
        statement_count=3,
        expected="5",  # The initial expression declares nothing; total=4+1=5.
    ),
    BodyCase(
        name="chained-declarations",
        signature="evaluate(int value)",
        statements=("left = value + 1", "right = left * 2", "right + value"),
        setup="",
        call="evaluate(4)",
        statement_count=3,
        expected="14",  # left=5, right=10, and the tail is 10+4=14.
    ),
    BodyCase(
        name="local-reassignment",
        signature="evaluate(int value)",
        statements=(
            "total = value + 1", "total := total * 2",
            "other = total + value", "other - 1",
        ),
        setup="",
        call="evaluate(4)",
        statement_count=4,
        expected="13",  # total goes 5 to 10, other=14, and the tail is 13.
    ),
    BodyCase(
        name="typed-locals",
        signature="evaluate(int value)",
        statements=("float first = value + 0.5", "float doubled = first * 2", "doubled"),
        setup="",
        call="evaluate(4)",
        statement_count=3,
        expected="9",  # first=4.5 and doubled=4.5*2=9.
    ),
    BodyCase(
        name="string-local-and-tail",
        signature="evaluate(string value)",
        statements=("text = value + ',b'", "text := str.upper(text)", "text + ',C'"),
        setup="",
        call="evaluate('a')",
        statement_count=3,
        expected="A,B,C",  # 'a,b' becomes 'A,B', then the tail appends ',C'.
    ),
    BodyCase(
        name="method-local-reassignment",
        signature="method evaluate(int value)",
        statements=("local = value + 1", "local := local * 2", "local + value"),
        setup="argument = 4\n",
        call="argument.evaluate()",
        statement_count=3,
        expected="14",  # local goes 5 to 10, then the tail adds 4.
    ),
    BodyCase(
        name="ordered-array-mutations",
        signature="evaluate(array<int> value)",
        statements=(
            "array.push(value, 1)",
            "array.push(value, array.size(value) + 1)",
            "array.set(value, 0, array.get(value, 0) + 2)",
            "value.get(0) * 100 + value.get(1) * 10 + value.size()",
        ),
        setup="argument = array.new<int>()\n",
        call="evaluate(argument)",
        statement_count=4,
        expected="322",  # [] becomes [1], [1,2], [3,2]; the tail is 300+20+2.
    ),
    BodyCase(
        name="multiple-parameters",
        signature="store(Crate item, string text)",
        statements=("array.push(item.contents, text)", "item.contents.size()"),
        setup="var crate = Crate.new(array.new<string>())\n",
        call="store(crate, 'a,b')",
        statement_count=2,
        expected="1",  # One string is pushed; its comma does not add an element.
        preamble="type Crate\n    array<string> contents\n",
    ),
    BodyCase(
        name="parenthesized-call-commas",
        signature="evaluate(int value)",
        statements=(
            "math.max(value, math.min(3, 5))",
            "(math.max(value, 2))",
            "math.min((math.max(value, 2)), 6)",
        ),
        setup="",
        call="evaluate(4)",
        statement_count=3,
        expected="4",  # The final nested max is 4 and min(4,6) is 4.
    ),
    BodyCase(
        name="trailing-comma-continuation",
        signature="evaluate(int value)",
        statements=("value + 1", "value * 2", "value * 3"),
        setup="",
        call="evaluate(4)",
        statement_count=3,
        expected="12",  # A line-ending comma continues the body; its tail is 4*3=12.
        wrap_after=2,
    ),
    BodyCase(
        name="literal-commas",
        signature="evaluate(string value)",
        statements=("str.replace_all(value, ',', ';')", "str.length(value + ',c')"),
        setup="",
        call="evaluate('a,b')",
        statement_count=2,
        expected="5",  # The returned string length is len('a,b,c')=5.
    ),
    BodyCase(
        name="field-and-local",
        signature="update(Cell record, int amount)",
        statements=("prior = record.value", "record.value := prior + amount", "record.value"),
        setup="argument = Cell.new(2)\n",
        call="update(argument, 3)",
        statement_count=3,
        expected="5",  # prior=2, then the shared field becomes 2+3=5.
        preamble=_CELL,
    ),
    BodyCase(
        name="local-after-field-assignment",
        signature="update(Cell record, int amount)",
        statements=(
            "record.value := record.value + amount",
            "prior = record.value",
            "prior * 2",
        ),
        setup="argument = Cell.new(2)\n",
        call="update(argument, 3)",
        statement_count=3,
        expected="10",  # The field becomes 5, prior reads 5, and the tail is 10.
        preamble=_CELL,
    ),
    BodyCase(
        name="tuple-local-declaration",
        signature="evaluate(int value)",
        statements=("[first, successor] = pair(value)", "first + successor"),
        setup="",
        call="evaluate(4)",
        statement_count=2,
        expected="9",  # The tuple declares first=4 and successor=5; their sum is 9.
        preamble="pair(int seed) => [seed, seed + 1]\n",
    ),
    BodyCase(
        name="untyped-parameter",
        signature="evaluate(value)",
        statements=("copy = value + 1", "copy + value"),
        setup="",
        call="evaluate(4)",
        statement_count=2,
        expected="9",  # copy=4+1=5, so the tail is 5+4=9.
    ),
    BodyCase(
        name="parameter-default-and-keyword",
        signature="evaluate(int value, int increment = 2)",
        statements=("value + increment", "math.max(value, increment) + increment"),
        setup="",
        call="evaluate(4, increment = 3)",
        statement_count=2,
        expected="7",  # The keyword binds increment=3, so the tail is max(4,3)+3=7.
    ),
]


def _source(case: BodyCase, *, inline: bool) -> str:
    if inline:
        body = " " + ", ".join(case.statements)
        if case.wrap_after is not None:
            body = (
                " " + ", ".join(case.statements[:case.wrap_after]) + ",\n    "
                + ", ".join(case.statements[case.wrap_after:])
            )
    else:
        body = "\n" + "\n".join("    " + statement for statement in case.statements)
    return (
        _HEADER + case.preamble + case.signature + " =>" + body + "\n"
        + case.setup + f"actual = {case.call}\n" + _TAIL
    )


def _compile_pair(inline: str, indented: str, *, label: str) -> str:
    skip_if_no_compile_env()
    cpp = transpile(inline)
    compile_cpp(cpp, label=label + "-inline")
    control = transpile(indented)
    compile_cpp(control, label=label + "-indented")
    assert cpp == control
    return cpp


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.name)
def test_comma_body_translates_and_compiles_like_indented_body(case: BodyCase) -> None:
    source = _source(case, inline=True)
    _compile_pair(source, _source(case, inline=False), label=case.name)
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    function = next(
        node for node in reversed(program.body) if isinstance(node, (FuncDef, MethodDef))
    )
    assert len(function.body) == case.statement_count
    assert function.is_single_expr is False


_DRIVER = r"""
#include <iomanip>
#include <iostream>

int main() {
    GeneratedStrategy strategy;
    pineforge::Bar bar{100.0, 101.0, 99.0, 100.0, 1.0, 1743466500000LL};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 2;
    }
    std::cout << std::setprecision(17) << strategy.actual << '\n';
}
"""


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.name)
def test_comma_body_returns_the_hand_derived_tail_value(case: BodyCase) -> None:
    skip_if_no_engine_lib()
    output = run_emitted_tu(
        transpile(_source(case, inline=True)), _DRIVER, opt="-O0", label=case.name,
    )
    assert output.strip() == case.expected


_REPRODUCTION = """//@version=6
strategy('repro')
type Box
    array<string> items
var b = Box.new(array.new<string>())
add(Box x, string s) => array.push(x.items, s), x.items.size()
add(b, 'a')
if bar_index == 5
    strategy.entry('L', strategy.long)
"""


def test_record_array_parameter_remains_in_scope_after_comma() -> None:
    indented = _REPRODUCTION.replace(
        "add(Box x, string s) => array.push(x.items, s), x.items.size()",
        "add(Box x, string s) =>\n    array.push(x.items, s)\n    x.items.size()",
    )
    _compile_pair(_REPRODUCTION, indented, label="record-array-parameter")


def test_trailing_comma_at_end_of_source_compiles_and_returns_the_tail() -> None:
    inline = _HEADER + _TAIL + "evaluate(int value) => value + 1, value * 2,\n"
    program = Parser(Lexer(inline).tokenize(), source=inline).parse()
    function = next(node for node in program.body if isinstance(node, FuncDef))
    # Two expressions precede the terminal comma; the delimiter adds no statement.
    assert len(function.body) == 2
    assert function.is_single_expr is False

    # A line-ending comma continues into following code, so terminate the line
    # before adding the script call that registers the function for emission.
    runnable = inline.removesuffix(",\n") + "\nactual = evaluate(4)\n"
    runnable_program = Parser(Lexer(runnable).tokenize(), source=runnable).parse()
    runnable_function = next(
        node for node in runnable_program.body if isinstance(node, FuncDef)
    )
    assert function.body == runnable_function.body
    indented = (
        _HEADER + _TAIL
        + "evaluate(int value) =>\n    value + 1\n    value * 2\n"
        + "actual = evaluate(4)\n"
    )
    cpp = _compile_pair(runnable, indented, label="trailing-comma-at-end")
    output = run_emitted_tu(cpp, _DRIVER, opt="-O0", label="trailing-comma-at-end")
    # The terminal delimiter adds no statement; the function returns 4*2=8.
    assert output.strip() == "8"
