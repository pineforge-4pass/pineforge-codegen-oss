"""Generated-code regressions for Pine-compatible ``math.sign`` lowering."""

from __future__ import annotations

import re

from pineforge_codegen import transpile
from pineforge_codegen.codegen import CodeGen
from tests._compile import compile_cpp


_PRELUDE = '//@version=6\nstrategy("math.sign regression")\n'


def _gen(body: str) -> str:
    return transpile(_PRELUDE + body + "\n")


def _assignment(cpp: str, name: str) -> str:
    match = re.search(rf"^\s*{re.escape(name)}\s*=.*;$", cpp, re.MULTILINE)
    assert match is not None, cpp
    return match.group(0)


def _assert_na_safe_sign(expr: str) -> None:
    assert "[](const auto _pf_sign_v) -> double" in expr
    assert "is_na(_pf_sign_v) ? na<double>()" in expr
    assert "(double)((_pf_sign_v > 0) - (_pf_sign_v < 0))" in expr


def test_math_sign_kwarg_propagates_float_na_and_evaluates_argument_once():
    cpp = _gen("float source = na\nresult = math.sign(x=source)\nplot(result)")
    assignment = _assignment(cpp, "result")
    _assert_na_safe_sign(assignment)
    assert assignment.count("(source)") == 1
    assert "double result = 0.0;" in cpp


def test_math_sign_propagates_int_na_where_int_sentinel_is_representable():
    cpp = _gen("int source = na\nresult = math.sign(source)\nplot(result)")
    assignment = _assignment(cpp, "result")
    _assert_na_safe_sign(assignment)
    assert assignment.count("(source)") == 1
    assert "double result = 0.0;" in cpp


def test_math_sign_series_history_expression_is_evaluated_once():
    cpp = _gen("result = math.sign(close[1])\nplot(result)")
    assignment = _assignment(cpp, "result")
    _assert_na_safe_sign(assignment)
    assert assignment.count("_s_close[1]") == 1


def test_math_sign_stateful_argument_has_single_evaluation_evidence():
    cpp = _gen(
        "bump() =>\n"
        "    var float calls = 0.0\n"
        "    calls += 1.0\n"
        "    calls\n"
        "result = math.sign(bump())\n"
        "plot(result)"
    )
    assignment = _assignment(cpp, "result")
    _assert_na_safe_sign(assignment)
    assert len(re.findall(r"\bbump(?:_cs\d+)?\(\)", assignment)) == 1


def test_math_sign_security_lowering_uses_same_na_safe_callable():
    cpp = _gen(
        'result = request.security(syminfo.tickerid, "D", '
        "math.sign(close[1]))\n"
        "plot(result)"
    )
    assignment = next(
        line for line in cpp.splitlines()
        if re.search(r"_req_sec_\d+\s*=", line) and "_pf_sign_v" in line
    )
    _assert_na_safe_sign(assignment)
    assert len(re.findall(r"_sec\d+_hist_close\[0\]", assignment)) == 1


def test_math_sign_stable_runtime_reset_fallback_is_na_safe(monkeypatch):
    # Force the legacy token-substitution lane so this pins the separate
    # stable-runtime math-member mapping, not only the ordinary visitor that
    # the preferred reparse lane normally reuses.
    monkeypatch.setattr(
        CodeGen,
        "_lower_reset_expr_via_visitor",
        lambda self, expanded: None,
    )
    cpp = _gen(
        'length = input.int(5, "Length")\n'
        "result = ta.sma(close, int(math.sign(length)) + 2)\n"
        "plot(result)"
    )
    reset = next(
        line for line in cpp.splitlines()
        if "_ta_sma_" in line and "get_input_int" in line
    )
    _assert_na_safe_sign(reset)
    assert reset.count('get_input_int("Length", 5)') == 1
    assert "math.sign" not in reset


def test_math_sign_generated_cpp_compiles_for_float_int_history_and_kwarg():
    cpp = _gen(
        "float float_na = na\n"
        "int int_na = na\n"
        "from_float = math.sign(x=float_na)\n"
        "from_int = math.sign(int_na)\n"
        "from_history = math.sign(close[1])\n"
        "plot(from_float + from_int + from_history)"
    )
    compile_cpp(cpp, label="math-sign-na-single-evaluation")
