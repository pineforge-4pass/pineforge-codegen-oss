"""Constant input defaults are values before any script statement executes."""

import json
import re

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.codegen.tables import COLOR_CONST_MAP
from pineforge_codegen.errors import CompileError
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.limits import iter_ast_nodes
from pineforge_codegen.parser import Parser


COLOR_CASES = [
    ("member", "const color seed = color.blue", "color.blue"),
    ("other_member", "const color seed = color.red", "color.red"),
    ("hex", "const color seed = #336699", "#336699"),
    ("hex_alpha", "const color seed = #33669980", "#33669980"),
    ("new", "const color seed = color.new(color.blue, 75)", "color.new(color.blue, 75)"),
    ("rgb", "const color seed = color.rgb(10, 20, 30, 40)", "color.rgb(10, 20, 30, 40)"),
    ("alias", "const color origin = color.blue\nconst color seed = origin", "color.blue"),
    ("arithmetic", "const int step = 25\nconst color seed = color.new(color.blue, step + 25)",
     "color.new(color.blue, 50)"),
    ("selection", "const bool pick = true\nconst color seed = pick ? color.blue : color.red",
     "color.blue"),
]

BUILDER_CASES = [
    ("new_base", "const color seed = color.blue",
     "color.new(seed, 50)", "color.new(color.blue, 50)"),
    ("new_keywords", "const color seed = color.blue",
     "color.new(transp=50, color=seed)", "color.new(transp=50, color=color.blue)"),
    ("new_transparency", "const int seed = 50",
     "color.new(color.blue, seed)", "color.new(color.blue, 50)"),
    ("rgb_channel", "const int seed = 10",
     "color.rgb(seed, 20, 30, 40)", "color.rgb(10, 20, 30, 40)"),
    ("nested_builder", "const color seed = color.blue",
     "color.new(color.new(seed, 75), 50)", "color.new(color.new(color.blue, 75), 50)"),
]

DEFAULT_CASES = [(label, declarations, "seed", direct)
                 for label, declarations, direct in COLOR_CASES] + BUILDER_CASES


def source(declarations, default="seed", *, named=False, context="global"):
    call = f'input.color({"defval=" if named else ""}{default}, title="Tint")'
    if context == "function":
        body = f"{declarations}\nchoose() =>\n    {call}\nshade = choose()"
    elif context == "function_local":
        body = ("choose() =>\n"
                + "\n".join(f"    {line}" for line in declarations.splitlines())
                + f"\n    {call}\nshade = choose()")
    elif context == "switch":
        body = ("shade = switch 1\n    1 =>\n"
                + "\n".join(f"        {line}" for line in declarations.splitlines())
                + f"\n        {call}\n    => color.red")
    elif context == "if":
        body = (f"{declarations}\nshade = if true\n    {call}\nelse\n    color.red")
    elif context == "loop":
        body = (f"{declarations}\ncolor shade = color.red\n"
                f"for index = 0 to 0\n    shade := {call}")
    else:
        body = f"{declarations}\nshade = {call}"
    return f'//@version=6\nstrategy("Constant defaults")\n{body}\nplot(close)\n'


def direct_value(expression):
    return transpile_full(source("", expression))["inputs"][0]["default"]


def getter_defaults(cpp):
    values = re.findall(r'get_input_int64\("Tint", (0x[0-9a-fA-F]+|\d+)LL\)', cpp)
    assert values, "the default must be a packed constant before on_bar"
    # Count every call, including variable/builder arguments the literal regex
    # cannot match. A correct settings getter must not hide a wrong body getter.
    assert len(values) == cpp.count('get_input_int64("Tint", '), (
        "every getter, including static input initialization, needs the packed default")
    return [int(value, 0) for value in values]


def test_direct_color_control_keeps_its_packed_engine_constant():
    pine = source("", "color.blue")
    full = transpile_full(pine)
    assert full["cpp"] == transpile(pine)
    assert f'get_input_int64("Tint", {COLOR_CONST_MAP["blue"]})' in full["cpp"]
    assert type(full["inputs"][0]["default"]) is int


@pytest.mark.parametrize("named", [False, True], ids=["positional", "named"])
@pytest.mark.parametrize("label,declarations,direct", COLOR_CASES, ids=[row[0] for row in COLOR_CASES])
def test_explicit_const_defaults_equal_the_direct_expression(label, declarations, direct, named):
    pine = source(declarations, named=named)
    cpp = transpile(pine)
    full = transpile_full(pine)
    expected = direct_value(direct)
    assert getter_defaults(cpp) == [expected] * len(getter_defaults(cpp))
    assert full["cpp"] == cpp
    assert full["inputs"] == [{
        "title": "Tint", "type": "color", "default": expected, "supported": True,
    }]


@pytest.mark.parametrize("named", [False, True], ids=["positional", "named"])
@pytest.mark.parametrize("context", ["global", "function", "function_local", "switch"])
@pytest.mark.parametrize("label,declarations,default,direct", DEFAULT_CASES,
                         ids=[row[0] for row in DEFAULT_CASES])
def test_every_emitted_getter_matches_a_direct_default(
        label, declarations, default, direct, context, named):
    reference = transpile_full(source("", direct))
    packed = reference["inputs"][0]["default"]
    # Emit a direct literal for the reference expression's color so both
    # getters have numeric defaults we can compare without a C++ evaluator.
    literal = f"#{packed & 0xFFFFFF:06X}{packed >> 24:02X}"
    expected = getter_defaults(transpile(source("", literal)))
    assert all(value == packed for value in expected)

    full = transpile_full(source(declarations, default, named=named, context=context))
    actual = getter_defaults(full["cpp"])
    assert actual == [expected[0]] * len(actual)
    if context == "global":
        assert full["inputs"] == reference["inputs"]
        # Check the descriptor's default too, independently of its effective
        # getter and the Python manifest.
        descriptor = next(line for line in full["cpp"].splitlines()
                          if '{"Tint", "int", ' in line)
        value = re.search(r'checked_settings::number\((0x[0-9a-fA-F]+|\d+)LL\)',
                          descriptor)
        assert value is not None
        assert int(value.group(1), 0) == expected[0]


@pytest.mark.parametrize("context", ["function", "switch", "if", "loop"])
def test_const_proof_applies_in_each_statement_context(context):
    pine = source("const color seed = color.blue", context=context)
    full = transpile_full(pine)
    expected = direct_value("color.blue")
    assert all(value == expected for value in getter_defaults(full["cpp"]))
    assert not any(d.code in ("PF-E1006", "PF-W1006") for d in full["diagnostics"])


@pytest.mark.parametrize("name", ["firstShade", "alternativeTint"])
@pytest.mark.parametrize("member", ["blue", "red"])
def test_binding_names_do_not_select_the_constant_value(name, member):
    pine = source(f"const color {name} = color.{member}", name)
    assert all(value == direct_value(f"color.{member}")
               for value in getter_defaults(transpile(pine)))


def test_reassigned_default_never_uses_its_initializer():
    # TradingView accepts this and reads red. A located limitation is allowed
    # until value flow is implemented; it is a compile gap, not a language rule.
    pine = source("color seed = color.blue\nseed := color.red")
    try:
        full = transpile_full(pine)
    except CompileError as exc:
        errors = [d for d in exc.diagnostics if d.code == "PF-E1006"]
        assert len(errors) == 1
        assert errors[0].location == input_call_location(pine)
    else:
        assert all(value == direct_value("color.red")
                   for value in getter_defaults(full["cpp"]))
        assert full["inputs"][0]["default"] == direct_value("color.red")


def input_call_location(pine):
    ast = Parser(Lexer(pine).tokenize()).parse()
    return next(node.loc for node, _ in iter_ast_nodes(ast)
                if getattr(getattr(node, "callee", None), "member", None) == "color")


# These forms were refused by both TradingView and the public transpiler.
# Existing accepted builders with nonconstant arguments are separate defects.
@pytest.mark.parametrize("declarations,default", [
    ("simple color seed = color.blue", "seed"),
    ("series color seed = color.blue", "seed"),
    ('seed = input.color(color.blue, title="Seed")', "seed"),
    ('pick = input.bool(true, title="Pick")', "pick ? color.blue : color.red"),
    ("", "close >= open ? color.blue : color.red"),
    ("const color seed = close >= open ? color.blue : color.red", "seed"),
    ("const int seed = 7", "seed"),
    ('const string seed = "blue"', "seed"),
    ('choose(color arg) =>\n    input.color(arg, title="Tint")\n'
     'const color seed = color.blue', "seed"),
    ('choose(simple color arg) =>\n    input.color(arg, title="Tint")\n'
     'const color seed = color.blue', "seed"),
    ('choose(arg) =>\n    input.color(arg, title="Tint")\n'
     'const color seed = color.blue', "seed"),
    ("type Holder\n    color tint\nconst color seed = color.blue\n"
     "Holder holder = Holder.new(seed)", "holder.tint"),
])
def test_invalid_type_or_qualifier_keeps_a_located_refusal(declarations, default):
    pine = source(declarations, default)
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    assert any(d.code == "PF-E1006" and d.location.line > 0
               and d.location.col > 0 for d in caught.value.diagnostics)


def test_a_forward_reference_does_not_borrow_a_later_constant():
    pine = ('//@version=6\nstrategy("Constant defaults")\n'
            'shade = input.color(seed, title="Tint")\nconst color seed = color.blue\n')
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    assert any(d.code == "PF-E1006" for d in caught.value.diagnostics)


def test_a_parameter_cannot_borrow_a_global_constant_proof():
    pine = ('//@version=6\nstrategy("Constant defaults")\n'
            'const color seed = color.blue\n'
            'choose(color seed) =>\n    input.color(seed, title="Tint")\n'
            'shade = choose(seed)\n')
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    assert any(d.code == "PF-E1006" for d in caught.value.diagnostics)


def test_other_input_types_keep_the_existing_constant_lowering():
    prefix = '//@version=6\nstrategy("Constant defaults")\nconst int seed = 7\n'
    carrier = prefix + 'period = input.int(seed, title="Period")\nplot(period)\n'
    direct = prefix + 'period = input.int(7, title="Period")\nplot(period)\n'
    before, after = transpile_full(direct), transpile_full(carrier)
    assert before["cpp"] == after["cpp"]
    assert json.dumps(before["inputs"]) == json.dumps(after["inputs"])
