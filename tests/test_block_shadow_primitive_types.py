"""Only the proven float-outer/string-inner shadow gains distinct storage."""

from __future__ import annotations

import re
from itertools import product

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError


PROBE = """//@version=6
strategy("r3 top-level block shadow of another type", currency = currency.NONE, initial_capital = 1000000)
x_v = 1.0
read_f() => x_v + 1
if close > open
    string x_v = "s"
    probe_v = read_f()
if bar_index == 10
    strategy.entry("L", strategy.long, qty = 1)
if bar_index == 12
    strategy.close("L")
"""

HEADER = '//@version=6\nstrategy("primitive block shadow", currency=currency.NONE)\n'

SHADOW_MESSAGE = (
    "A top-level block binding named '{name}' shadows a direct script binding "
    "with history, aggregate, or incompatible typed state; declaration-exact "
    "block storage is not supported yet."
)


def _function_body(cpp: str, name: str) -> str:
    match = re.search(
        rf"^    [^\n]+ \b{re.escape(name)}\(\) \{{\n(.*?)^    \}}",
        cpp,
        re.M | re.S,
    )
    assert match is not None, name
    return match.group(1)


def _driver(outputs: list[str]) -> str:
    streamed = ' << " " << '.join(f"strategy.{name}" for name in outputs)
    return f"""
#include <iomanip>
#include <iostream>
int main() {{
    Bar bars[] = {{
        Bar{{1.0, 3.0, 0.5, 2.0, 100.0, 0}},
        Bar{{2.0, 4.0, 1.5, 3.0, 100.0, 60000}}
    }};
    GeneratedStrategy strategy;
    strategy.run(bars, 2);
    std::cout << std::fixed << std::setprecision(1)
              << {streamed} << "\\n";
    return 0;
}}
"""


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        ("x_v = 1.0\n", "x_v = close * 2\n"),
        ('    string x_v = "s"\n', "    x_v = str.tostring(close)\n"),
        ("x_v = 1.0\n", 'x_v = input.float(1.0, "x")\n'),
    ],
    ids=["inferred-float-outer", "inferred-string-local", "input-float-outer"],
)
def test_probe_requires_declared_or_literal_types(original: str, replacement: str):
    with pytest.raises(CompileError) as caught:
        transpile(PROBE.replace(original, replacement, 1))

    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E2010"
    assert diagnostic.message == SHADOW_MESSAGE.format(name="x_v")


FAMILIES = {
    "int": ("int", "3", "7"),
    "float": ("double", "3.5", "7.5"),
    "bool": ("bool", "true", "false"),
    "string": ("std::string", '"outer"', '"inner"'),
    "color": ("int64_t", "#112233", "#445566"),
}

# TradingView has proven only this pair. Float-outer/int-inner keeps its
# existing widening route; every other different primitive pair is refused.
NEW_SHADOWS = [
    pytest.param("float", "string", id="float-over-string"),
]
REFUSED_SHADOWS = [
    pytest.param(outer, inner, id=f"refused-{outer}-over-{inner}")
    for outer, inner in product(FAMILIES, repeat=2)
    if outer != inner and (outer, inner) not in {("float", "int"), ("float", "string")}
]


def _primitive_shadow_source(outer: str, inner: str) -> str:
    _, outer_value, _ = FAMILIES[outer]
    _, inner_value, replacement = FAMILIES[inner]
    return (
        HEADER
        + f"{outer} value = {outer_value}\n"
        + "local_ok = false\ncall_ok = false\n"
        + "read_outer() => value\n"
        + "if close > open\n"
        + f"    value = {inner_value}\n"
        + f"    value := {replacement}\n"
        + f"    local_ok := value == {replacement}\n"
        + f"    call_ok := read_outer() == {outer_value}\n"
        + f"value := {outer_value}\n"
        + f"after_ok = value == {outer_value}\n"
        + f"after_call_ok = read_outer() == {outer_value}\n"
    )


@pytest.mark.parametrize(("outer", "inner"), REFUSED_SHADOWS)
def test_unproven_primitive_shadow_pairs_keep_their_diagnostic(outer: str, inner: str):
    with pytest.raises(CompileError) as caught:
        transpile(_primitive_shadow_source(outer, inner))

    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E2010"
    assert diagnostic.message == SHADOW_MESSAGE.format(name="value")


def test_script_input_with_unproven_shadow_fails_closed():
    source = (
        HEADER
        + 'length = input.int(2, "Length")\nseen = ""\n'
        + 'if true\n    length = "inner"\n    seen := length\n'
        + "average = ta.sma(close, length)\n"
        + "outer_ok = length == 2\n"
    )

    with pytest.raises(CompileError) as caught:
        transpile(source)

    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E2010"
    assert diagnostic.message == SHADOW_MESSAGE.format(name="length")


@pytest.mark.parametrize(
    "initializer",
    ['input.string("inner", "Local value")', 'if true\n        "inner"\n    else\n        "other"'],
    ids=["input", "selection"],
)
def test_local_initializer_with_unproven_shadow_fails_closed(initializer: str):
    source = (
        HEADER
        + 'value = 1\nseen = ""\n'
        + "if close > open\n"
        + f"    value = {initializer}\n"
        + '    value := value + "!"\n'
        + "    seen := value\n"
        + "outer_ok = value == 1\n"
    )

    with pytest.raises(CompileError) as caught:
        transpile(source)

    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E2010"
    assert diagnostic.message == SHADOW_MESSAGE.format(name="value")


@pytest.mark.parametrize(
    "call",
    [
        "ta.sma(close, value)",
        "ta.sma(source=close, length=value)",
        "ta.highest(value)",
        'ta.sma(close, value + "t")',
        "ta.sar(0.02, value, 0.2)",
    ],
    ids=["sma-length", "keyword-length", "length-only", "length-expression", "sar-increment"],
)
def test_shadowed_string_cannot_supply_a_ta_constructor_argument(call: str):
    source = (
        HEADER
        + "value = 2.0\navg = 0.0\n"
        + "if close > open\n"
        + '    string value = "s"\n'
        + f"    avg := {call}\n"
    )

    with pytest.raises(CompileError) as caught:
        transpile(source)

    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E2010"
    assert diagnostic.message == SHADOW_MESSAGE.format(name="value")


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (
            "value = 1.0\nif true\n    value = 2.0\n    prior = value[1]\n",
            "PF-E2010",
        ),
        (
            'value = 1.0\nprior = value[1]\nif true\n    string value = "inner"\n',
            "PF-E2010",
        ),
        (
            "value = array.new_float(1)\nif true\n    value = array.new_float(2)\n",
            "PF-E2010",
        ),
        (
            'value = 1.0\nif true\n    var string value = "inner"\n',
            "PF-E2010",
        ),
        (
            'var float value = 1.0\nif true\n    string value = "inner"\n',
            "PF-E2010",
        ),
        (
            "type Holder\n    float field\nvalue = 1.0\n"
            "if true\n    value = Holder.new(2.0)\n",
            "PF-E2010",
        ),
        (
            "value = 1.0\nif true\n    value = map.new<string, float>()\n",
            "PF-E2010",
        ),
        (
            "value = 1.0\nif true\n    value = matrix.new<float>(1, 1, 2.0)\n",
            "PF-E2010",
        ),
        (
            "value = 1.0\nif true\n    value = line.new(0, 1.0, 1, 2.0)\n",
            "PF-E2010",
        ),
        (
            'enum Direction\n    upward\nvalue = "outer"\n'
            "if true\n    value = Direction.upward\n",
            "PF-E2010",
        ),
        (
            "if true\n    value = 2.0\n    prior = value[1]\n"
            "read_local() =>\n    var float value = 1.0\n    value\n"
            "seen = read_local()\n",
            "PF-E2007",
        ),
    ],
    ids=[
        "block-history", "outer-history", "array", "block-var", "outer-var",
        "udt", "map", "matrix", "drawing", "enum", "block-callable-history",
    ],
)
def test_unsupported_shadow_storage_keeps_its_diagnostic(body: str, code: str):
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + body)

    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == code
    if code == "PF-E2010":
        assert diagnostic.message == SHADOW_MESSAGE.format(name="value")
    else:
        assert diagnostic.message == (
            "A top-level block history binding named 'value' shares raw generated "
            "Series state with another lexical declaration; declaration-exact "
            "block storage is not supported yet."
        )


