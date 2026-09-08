"""Numeric folding must never expose Python objects to Pine source."""

import builtins
import re

import pytest

from pineforge_codegen import Analyzer, CodeGen, Lexer, Parser, transpile
from pineforge_codegen.codegen import constant_fold
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp


def _generator() -> CodeGen:
    source = '//@version=6\nstrategy("numeric folding")\n'
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    return CodeGen(Analyzer(ast).analyze())


@pytest.mark.parametrize("expression", [
    "round(abs.__class__.__base__.__subclasses__().__len__())",
    "abs.__class__",
    "math.__dict__",
    "math.sin.__call__(0)",
    "math.pi.__class__",
    "round.__call__(7)",
    "math.sin(0).__class__",
    "abs(abs)",
    "(lambda: 7)()",
    "(1, 7)[1]",
    "[7][0]",
    "([x for x in (1, 2)])",
    "({'value': 7})",
    "('7')",
    "round(*(7,))",
    "round(number=7)",
    "(saved := 7)",
    "sum([1, 2])",
])
def test_python_object_grammar_is_not_constant_folded(expression: str) -> None:
    assert _generator()._resolve_known(expression) == expression


def test_attribute_escape_is_rejected_through_public_transpile() -> None:
    source = '''//@version=6
strategy("reject object introspection")
value = ta.sma(close, round(abs.__class__.__base__.__subclasses__().__len__()))
'''
    with pytest.raises(CompileError) as error:
        transpile(source, filename="object-introspection.pine")
    assert "object-introspection.pine:3:" in str(error.value)


def test_folding_does_not_invoke_python_expression_execution(monkeypatch) -> None:
    gen = _generator()
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Python expression execution must not be used")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "eval", forbidden)
        folded = gen._resolve_known("math.abs(-7) + round(2.5)")
        refused = gen._resolve_known("round(abs.__class__.__name__)")
    assert calls == []
    assert folded == "9"
    assert refused == "round(abs.__class__.__name__)"


@pytest.mark.parametrize(("expression", "expected"), [
    ("length / 2", "8"),
    ("(length - 4) / (2 + 1)", "4"),
    ("+length + -2", "14"),
    ("(length % 3)", "1"),
    ("abs(-length)", "16"),
    ("math.abs(-length)", "16"),
    ("round(length / 3)", "5"),
    ("math.round(length / 3)", "5"),
    ("round(2.5)", "2"),
    ("math.pow(2, 3)", "8"),
    ("math.sin(math.pi / 2)", "1"),
    ("math.log(1) + math.exp(0)", "1"),
])
def test_existing_numeric_folds_are_preserved(expression: str, expected: str) -> None:
    gen = _generator()
    gen._known_vars["length"] = 16
    assert gen._resolve_known(expression) == expected


@pytest.mark.parametrize("expression", [
    "math.round(math.sqrt(length))",
    "math.ceil(length / 3)",
    "math.floor(length / 3)",
])
def test_previously_unfolded_math_keeps_its_existing_fallback(expression: str) -> None:
    gen = _generator()
    gen._known_vars["length"] = 16
    assert gen._resolve_known(expression) == expression


def test_new_math_domain_does_not_disagree_with_runtime_rounding() -> None:
    # The old folder did not admit sqrt: its import rewrite failed. Adding it
    # here would newly fold round(sqrt(6.25)) to Python's 2, while runtime C++
    # std::round computes 3. Retain the old rejection until semantics are pinned.
    source = '''//@version=6
strategy("keep existing math domain")
value = ta.sma(close, math.round(math.sqrt(6.25)))
'''
    with pytest.raises(CompileError):
        transpile(source)


@pytest.mark.parametrize("name", ["round", "math.round"])
@pytest.mark.parametrize("precision", [-1000000000, 1000000000])
def test_round_precision_is_bounded_before_callable_dispatch(monkeypatch, name, precision) -> None:
    calls = []

    def sentinel(*args):
        calls.append(args)
        return 0

    # A harmless sentinel proves that rejection happens before the integer
    # round implementation could allocate a power of ten for huge ndigits.
    monkeypatch.setitem(constant_fold._FUNCTIONS, name, sentinel)
    expression = f"{name}(1, {precision})"
    assert _generator()._resolve_known(expression) == expression
    assert calls == []


@pytest.mark.parametrize(("expression", "expected"), [
    ("round(125, -1)", "120"),
    ("round(135, -1)", "140"),
    ("math.round(1.25, 1)", "1.2"),
    ("round(1, -1024)", "0"),
    ("round(1, 1024)", "1"),
])
def test_bounded_round_precision_preserves_existing_values(expression: str, expected: str) -> None:
    assert _generator()._resolve_known(expression) == expected


@pytest.mark.parametrize("expression", [
    "unknown + 1",
    "1 / 0",
    "math.sqrt(-1)",
    "math.exp(10000)",
    "math.pow(1e308, 2)",
    "math.factorial(1000000000)",
    "(1e309)",
    "2 ** 1000000000",
    "1 << 1000000000",
    "9" * 1000 + " + 1",
    "1 + " * 2000 + "1",
    "abs(" * 80 + "1" + ")" * 80,
    " + ".join(["1"] * 200),
])
def test_invalid_or_unbounded_fold_preserves_original_expression(expression: str) -> None:
    assert _generator()._resolve_known(expression) == expression


def test_known_names_are_bound_as_numeric_values_not_rewritten(monkeypatch) -> None:
    gen = _generator()
    gen._known_vars.update({"length": 16, "length2": 4, "e": 9, "abs": 8})
    assert gen._resolve_known("length / length2") == "4"
    assert gen._resolve_known("math.abs(-length)") == "16"
    assert gen._resolve_known("math.log(math.e)") == "1"
    assert gen._resolve_known("abs(7)") == "abs(7)"
    monkeypatch.setattr(gen, "_known_var_is_lexically_shadowed", lambda name: name == "length")
    assert gen._resolve_known("length / length2") == "length / length2"


def test_known_non_numeric_objects_are_never_coerced() -> None:
    class NonNumeric:
        def __add__(self, other):
            pytest.fail("constant folding called an object-supplied operator")

    gen = _generator()
    gen._known_vars["value"] = NonNumeric()
    assert gen._resolve_known("value + 1") == "value + 1"
    gen._known_vars["value"] = "16"
    assert gen._resolve_known("value + 1") == "value + 1"


def test_folded_math_ta_lengths_compile_and_keep_runtime_input_reads() -> None:
    cpp = transpile('''//@version=6
strategy("bounded numeric constructors")
length = input.int(16, "Length")
rounded = math.round(length / 3)
first = ta.sma(close, rounded)
second = ta.sma(close, math.abs(-6))
third = ta.sma(close, math.pow(5, 1))
fourth = ta.sma(close, math.abs(-8) / 2)
fifth = ta.sma(close, math.pow(2, 3))
''')
    constructor = next(line for line in cpp.splitlines() if "explicit GeneratedStrategy()" in line)
    assert re.findall(r"_ta_sma_\d+\((\d+)\)", constructor) == ["5", "6", "5", "4", "8"]
    reset = next(line for line in cpp.splitlines() if "_ta_sma_1 = ta::SMA" in line)
    assert 'get_input_int("Length", 16)' in reset
    assert "std::round" in reset
    compile_cpp(cpp, label="safe-numeric-constructors")


def test_unfolded_math_keeps_existing_input_reset_and_compiles() -> None:
    cpp = transpile('''//@version=6
strategy("existing runtime math domain")
length = input.int(16, "Length")
root = math.round(math.sqrt(length))
first = ta.sma(close, root)
second = ta.sma(close, math.ceil(length / 3))
third = ta.sma(close, math.floor(length / 3))
''')
    constructor = next(line for line in cpp.splitlines() if "explicit GeneratedStrategy()" in line)
    assert re.findall(r"_ta_sma_\d+\((\d+)\)", constructor) == ["1", "1", "1"]
    resets = [line for line in cpp.splitlines() if "= ta::SMA" in line]
    assert any('get_input_int("Length", 16)' in line and "std::sqrt" in line for line in resets)
    assert any('get_input_int("Length", 16)' in line and "std::ceil" in line for line in resets)
    assert any('get_input_int("Length", 16)' in line and "std::floor" in line for line in resets)
    compile_cpp(cpp, label="existing-unfolded-math-input-resets")
