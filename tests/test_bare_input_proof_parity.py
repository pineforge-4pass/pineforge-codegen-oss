"""Check a bare input proof against a separate expression in the strategy."""

import json

import pytest

from pineforge_codegen import transpile_full
from pineforge_codegen.codegen import constant_fold
from tests._compile import run_emitted_tu
from tests.test_bare_input_alias_depth import DRIVER


def _compared_input(declarations, condition):
    return transpile_full(
        f'//@version=6\nstrategy("String comparison")\n{declarations}\n'
        f'const string seed = {condition} ? "A" : "B"\n'
        'mode = input(seed, "Mode")\n'
        f'runtime_mode = {condition} ? "A" : "B"\n'
        'plot(str.length(mode) + str.length(runtime_mode))\n'
    )


def _run_string_comparison(full, expected):
    driver = DRIVER.replace(
        "<< generated->mode << '\\n';",
        "<< generated->mode << '\\n' << generated->runtime_mode << '\\n';",
    )
    receipt, effective, computed = run_emitted_tu(
        full["cpp"], driver, opt="-O0", label="string default and independent expression"
    ).splitlines()
    setting, = json.loads(receipt)["inputs"]
    assert full["inputs"][0]["default"] == expected
    assert setting["type"] == "string"
    assert setting["default"] == setting["effective_value"] == effective == computed == expected


@pytest.mark.parametrize("declarations,operand", [
    ("x = true ? 9007199254740993 : 0.0", "x"),
    ("float x = true ? 9007199254740993 : 0.0", "x"),
    ("", "(true ? 9007199254740993 : 0.0)"),
], ids=["inferred-alias", "typed-control", "inline-control"])
def test_mixed_selection_keeps_its_float_result_across_a_binding(declarations, operand):
    # The float arm makes the selection float. Binary64 rounds the selected
    # odd integer to the even neighbor 9007199254740992, so equality is true.
    _run_string_comparison(_compared_input(declarations, f"{operand} == 9007199254740992"), "A")


@pytest.mark.parametrize("op", ["==", "!=", "<", "<=", ">", ">="])
def test_wide_missing_integer_binding_is_unordered(op):
    # The wide cast produces the engine's missing int. Every relation,
    # including !=, is false. Equality and the greater-than rows are controls.
    _run_string_comparison(
        _compared_input("const int m = int(-9223372036854775808.0)", f"m {op} 0.0"),
        "B",
    )


def test_wide_cast_does_not_treat_the_narrow_minimum_as_missing():
    # Control: -2**31 is a present value in the wide cast and its alias.
    _run_string_comparison(
        _compared_input("const int m = int(-2147483648.0)", "m != 0.0"),
        "A",
    )


def test_negative_half_uses_half_away_from_zero():
    # Control: -1.5 is halfway and the away-from-zero result is -2.
    _run_string_comparison(_compared_input("", "math.round(-1.5) == -2.0"), "A")


@pytest.mark.parametrize("declarations,condition", [
    ("", "(9007199254740992 + 1) == 9007199254740992"),
    ("", "math.toradians(1e308) < 1e308"),
    ("", "math.todegrees(1.1e306) < 1e308"),
    ("", "math.avg(1e16, 1.0, -1e16) > 0.1"),
    ("", "math.round(1.005, 2) > 1.005"),
    ("float x = 9007199254740993", "x == 9007199254740992"),
], ids=["computed-wide-int", "radians", "degrees", "average", "round-precision",
        "inlined-float-annotation"])
def test_unproved_string_keeps_the_historical_numeric_getter(declarations, condition):
    # Exact integer arithmetic gives B in the first row, but the native
    # float wrapper gives A. No literal is safe to substitute in that row.
    # Each unproved string uses the same numeric descriptor as older releases.
    full = _compared_input(declarations, condition)
    assert full["inputs"][0]["default"] is None
    with pytest.raises(AssertionError, match=r"error:[^\n]*number"):
        run_emitted_tu(full["cpp"], DRIVER, opt="-O0", label="unproved string input")


def test_unproved_math_in_a_numeric_default_keeps_working():
    # Control: the double getter accepts a numeric expression. With all
    # operands finite, the emitted radians expression for zero is zero.
    full = transpile_full('''//@version=6
strategy("Numeric default")
mode = input(math.toradians(0.0), "Mode")
plot(mode)
''')
    assert full["inputs"][0]["default"] is None
    receipt, effective = run_emitted_tu(
        full["cpp"], DRIVER, opt="-O0", label="numeric radians input"
    ).splitlines()
    setting, = json.loads(receipt)["inputs"]
    assert setting["type"] == "float"
    assert float(setting["default"]) == float(setting["effective_value"]) == float(effective) == 0.0


def test_alias_with_embedded_nul_keeps_the_unproven_default():
    full = transpile_full(
        '//@version=6\nstrategy("String default")\n'
        'const string seed = "A\0B"\nmode = input(seed, "Mode")\n'
        'plot(str.length(mode))\n'
    )
    assert full["inputs"][0]["default"] is None
    with pytest.raises(AssertionError, match=r"error:[^\n]*number"):
        run_emitted_tu(full["cpp"], DRIVER, opt="-O0", label="unproved string input")


def test_fold_and_emission_read_the_same_comparison_band(monkeypatch):
    # Widen the band for this test only. The operands are equal under that
    # band but unequal under the historical one. A hardcoded copy on either
    # side fails against A, even if the published default and receipt agree.
    monkeypatch.setattr(constant_fold, "PINE_FLOAT_COMPARISON_BAND", 2e-10, raising=False)
    _run_string_comparison(_compared_input("", "0.00000000015 == 0.0"), "A")
