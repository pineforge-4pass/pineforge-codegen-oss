"""Micro-contract for helper-local ``var`` inside ``request.security``.

The N=2 matrix crosses the two independent semantics that previously hid one
another: persistent declaration (plain/``var``) and history use (none/``[1]``).
All four cells use a two-minute requested context over one-minute input so the
same requested bar is evaluated once and then recomputed.  This makes a
missing rollback visible instead of testing only the easier new-bar path.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests import _compile as compile_env


_N2_MATRIX = """//@version=6
strategy("security helper var N2")

f00(float src) =>
    float state = src
    state := state + 1.0
    state

f10(float src) =>
    var float state = src
    state += 1.0
    state

f01(float src) =>
    float state = src
    state := nz(state[1], 0.0) + 1.0
    state

f11(float src) =>
    var float state = src
    state := nz(state[1], 0.0) + 1.0
    state

out00 = request.security(syminfo.tickerid, "2", f00(close), lookahead=barmerge.lookahead_on)
out10 = request.security(syminfo.tickerid, "2", f10(close), lookahead=barmerge.lookahead_on)
out01 = request.security(syminfo.tickerid, "2", f01(close), lookahead=barmerge.lookahead_on)
out11 = request.security(syminfo.tickerid, "2", f11(close), lookahead=barmerge.lookahead_on)
"""


_CONCORDANCE_SHAPE = """//@version=6
strategy("Concordance helper-local var probe")
int almaLen = 9
int sdLen = 9
float almaFactor = 1.5

f_alma_sig(float src) =>
    float alma = ta.alma(src, almaLen, 0.85, 4.0)
    float dev = ta.stdev(src, sdLen)
    float upper = alma + almaFactor * dev
    float lower = alma - almaFactor * dev
    float prevUpper = nz(upper[1], upper)
    float prevLower = nz(lower[1], lower)
    upper := upper < prevUpper or src[1] > prevUpper ? upper : prevUpper
    lower := lower > prevLower or src[1] < prevLower ? lower : prevLower
    var int dir = 0
    dir := src > upper ? 1 : src < lower ? -1 : nz(dir[1], 0)
    [dir, alma]

[sig15, alma15] = request.security(syminfo.tickerid, "15", f_alma_sig(close[1]), lookahead=barmerge.lookahead_off)
[sig60, alma60] = request.security(syminfo.tickerid, "60", f_alma_sig(close[1]), lookahead=barmerge.lookahead_off)
[sig240, alma240] = request.security(syminfo.tickerid, "240", f_alma_sig(close[1]), lookahead=barmerge.lookahead_off)
[sigD, almaD] = request.security(syminfo.tickerid, "D", f_alma_sig(close[1]), lookahead=barmerge.lookahead_off)
plot(sig15 + sig60 + sig240 + sigD + alma15 + alma60 + alma240 + almaD)
"""


_FOUR_BOOL_TUPLE_SHAPE = """//@version=6
strategy("generic four bool tuple")
classify(float src) =>
    [src > 1.0, src < 0.0, src >= 2.0, src == 3.0]

[isPositive, isNegative, isAtLeastTwo, isThree] = request.security(syminfo.tickerid, "1", classify(close), barmerge.gaps_off, barmerge.lookahead_off)
outPositive = isPositive ? 1 : 0
outNegative = isNegative ? 1 : 0
outAtLeastTwo = isAtLeastTwo ? 1 : 0
outThree = isThree ? 1 : 0
plot(outPositive + outNegative + outAtLeastTwo + outThree)
"""


def _eval_body(cpp: str, sec_id: int, next_sec_id: int | None) -> str:
    start = cpp.index(f"void _eval_security_{sec_id}(")
    marker = (
        f"void _eval_security_{next_sec_id}("
        if next_sec_id is not None
        else "void evaluate_security("
    )
    return cpp[start:cpp.index(marker, start)]


def _series_key(body: str) -> str:
    match = re.search(r'_security_helper_series_\["([^"@]+_state)"\]\.size\(\)', body)
    assert match is not None, body
    return match.group(1)


def test_n2_matrix_emits_distinct_plain_var_history_and_rollback_cells():
    cpp = transpile(_N2_MATRIX)
    cell00 = _eval_body(cpp, 0, 1)
    cell10 = _eval_body(cpp, 1, 2)
    cell01 = _eval_body(cpp, 2, 3)
    cell11 = _eval_body(cpp, 3, None)

    # 00: an ordinary non-history temporary remains a scalar local.
    assert "_security_helper_series_" not in cell00

    # 10: var forces requested-context persistence even without an explicit
    # history read. Its initializer is captured once, new bars carry state,
    # and same-bar recomputation rolls back to the prior bar (or first seed).
    key10 = _series_key(cell10)
    seed10 = f"{key10}@var_seed"
    assert f'_security_helper_series_["{seed10}"].push(bar.close);' in cell10
    assert (
        f'_security_helper_series_["{key10}"].push('
        f'_security_helper_series_["{key10}"][0]);'
    ) in cell10
    assert (
        f'_security_helper_series_["{key10}"].size() > 1 '
        f'? _security_helper_series_["{key10}"][1] '
        f': _security_helper_series_["{seed10}"][0]'
    ) in cell10

    # 01: a plain history-bearing local keeps its established per-slot
    # declaration recomputation behavior and never gains var seed state.
    key01 = _series_key(cell01)
    assert "@var_seed" not in cell01
    assert f'_security_helper_series_["{key01}"].push(bar.close);' in cell01
    assert f'_security_helper_series_["{key01}"].update(bar.close);' in cell01
    assert f'_security_helper_series_["{key01}"][1]' in cell01

    # 11: the target shape composes persistence, rollback, and [1] recurrence.
    key11 = _series_key(cell11)
    assert f"{key11}@var_seed" in cell11
    assert f'_security_helper_series_["{key11}"][1]' in cell11


def _find_engine_library() -> Path | None:
    explicit = os.environ.get("PINEFORGE_ENGINE_LIB")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if path.is_file() else None
    if compile_env._ENGINE_INC is None:
        return None
    root = compile_env._ENGINE_INC.parent
    candidates: list[Path] = []
    for pattern in ("build*/lib/libpineforge.a", "build*/lib/libpineforge.dylib"):
        candidates.extend(sorted(root.glob(pattern)))
    return candidates[0].resolve() if candidates else None


def _compile_and_run(cpp_source: str) -> str:
    compile_env.skip_if_no_compile_env()
    engine_lib = _find_engine_library()
    if engine_lib is None:
        pytest.skip("built libpineforge not found; set PINEFORGE_ENGINE_LIB")

    compiler = compile_env._COMPILER
    engine_inc = compile_env._ENGINE_INC
    eigen_inc = compile_env._EIGEN_INC
    assert compiler is not None and engine_inc is not None and eigen_inc is not None

    with tempfile.TemporaryDirectory(prefix="pineforge-security-var-") as tmp:
        cpp_path = Path(tmp) / "matrix.cpp"
        exe_path = Path(tmp) / "matrix"
        cpp_path.write_text(cpp_source)
        cmd = [
            compiler,
            "-std=c++17",
            "-O0",
            "-I", str(engine_inc),
            "-I", str(eigen_inc),
        ]
        if compile_env._GENERATED_INC is not None:
            cmd += ["-I", str(compile_env._GENERATED_INC)]
        cmd += [str(cpp_path), str(engine_lib), "-pthread", "-o", str(exe_path)]
        built = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if built.returncode != 0:
            raise AssertionError(
                "security helper-var runtime probe failed to link\n"
                + "\n".join((built.stderr or built.stdout).splitlines()[:80])
            )
        ran = subprocess.run([str(exe_path)], capture_output=True, text=True, timeout=30)
        if ran.returncode != 0:
            raise AssertionError(
                f"security helper-var runtime probe exited {ran.returncode}\n"
                f"stdout:\n{ran.stdout}\nstderr:\n{ran.stderr}"
            )
        return ran.stdout


def test_n2_matrix_runtime_recomputes_without_double_mutating_var_state():
    driver = r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bars[] = {
        Bar{1.0, 1.0, 1.0, 1.0, 1.0, 0},
        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 60000},
        Bar{3.0, 3.0, 3.0, 3.0, 1.0, 120000},
        Bar{4.0, 4.0, 4.0, 4.0, 1.0, 180000},
    };
    strategy.run(bars, 4, "1", "1");
    std::cout << strategy.out00 << " " << strategy.out10 << " "
              << strategy.out01 << " " << strategy.out11 << "\n";
    return 0;
}
"""
    values = tuple(float(value) for value in _compile_and_run(transpile(_N2_MATRIX) + driver).split())
    # f10's initializer captures close=1 only once. Each two-minute requested
    # bar then increments once: 1 -> 2 -> 3. Without same-slot rollback the
    # four child evaluations would incorrectly produce 5.
    assert values == (5.0, 3.0, 2.0, 2.0)


def test_concordance_tuple_helper_shape_composes_offsets_and_compiles():
    cpp = transpile(_CONCORDANCE_SHAPE)
    for sec_id in range(4):
        assert re.search(
            rf"std::tuple<double, double> _req_sec_{sec_id}\s*=",
            cpp,
        )
        assert f'_sec{sec_id}_f_alma_sig_' in cpp
    # f(close[1]) plus src[1] must become close[2], represented by the HTF
    # history series at offset 1. It must never subscript the scalar read.
    assert re.search(r'_sec0_hist_close\[1\]', cpp)
    assert not re.search(r'_sec\d+_hist_close\[\d+\]\[', cpp)
    compile_env.compile_cpp(cpp, label="concordance-security-helper-var")


def test_helper_parameter_history_declares_direct_and_composed_bar_series():
    src = """//@version=6
strategy("security helper parameter history")
f(float src) =>
    src[1]
direct = request.security(syminfo.tickerid, "2", f(close))
composed = request.security(syminfo.tickerid, "2", f(close[0]))
plot(direct + composed)
"""
    cpp = transpile(src)
    assert "Series<double> _sec0_hist_close" in cpp
    assert "Series<double> _sec1_hist_close" in cpp
    assert "_req_sec_0 = _sec0_hist_close[0];" in cpp
    assert "_req_sec_1 = _sec1_hist_close[0];" in cpp
    assert "_sec0_hist_close.push(bar.close);" in cpp
    assert "_sec1_hist_close.push(bar.close);" in cpp
    compile_env.compile_cpp(cpp, label="security-helper-parameter-history")


def test_helper_parameter_history_rejects_non_bar_series_binding():
    src = """//@version=6
strategy("security helper expression history reject")
f(float src) =>
    src[1]
out = request.security(syminfo.tickerid, "2", f(close + 1.0))
"""
    with pytest.raises(CompileError, match="direct OHLC/time bar-series binding"):
        transpile(src)


def test_helper_parameter_history_index_binding_matches_declaration_prepass():
    src = """//@version=6
strategy("security helper bound history index")
f(float src, int offset) =>
    src[offset]
current = request.security(syminfo.tickerid, "2", f(close, 0))
previous = request.security(syminfo.tickerid, "2", f(close, 1))
plot(current + previous)
"""
    cpp = transpile(src)
    assert "Series<double> _sec0_hist_close" not in cpp
    assert "_req_sec_0 = bar.close;" in cpp
    assert "Series<double> _sec1_hist_close" in cpp
    assert "_req_sec_1 = _sec1_hist_close[0];" in cpp
    compile_env.compile_cpp(cpp, label="security-helper-bound-history-index")


def test_helper_local_varip_remains_rejected():
    src = """//@version=6
strategy("varip reject")
f(float src) =>
    varip float state = src
    state += 1.0
    state
out = request.security(syminfo.tickerid, "2", f(close))
"""
    with pytest.raises(CompileError, match="varip"):
        transpile(src)


def test_non_scalar_helper_local_var_state_is_not_silently_numericized():
    src = """//@version=6
strategy("string var reject")
f() =>
    var string state = "seed"
    state
out = request.security(syminfo.tickerid, "2", f())
"""
    with pytest.raises(CompileError, match="supports only int, float, and bool"):
        transpile(src)


def test_conditional_helper_local_var_state_is_rejected():
    src = """//@version=6
strategy("conditional helper var reject")
f(float src) =>
    float out = 0.0
    if src > 0.0
        var float state = 0.0
        state += 1.0
        out := state
    out
value = request.security(syminfo.tickerid, "2", f(close))
"""
    with pytest.raises(CompileError, match="inside conditional control flow"):
        transpile(src)


def test_nested_conditional_persistent_helper_call_is_rejected():
    src = """//@version=6
strategy("conditional nested helper var reject")
stateful(float src) =>
    var float state = src
    state += 1.0
    state
outer(float src) =>
    float out = 0.0
    if src > 0.0
        out := stateful(src)
    out
value = request.security(syminfo.tickerid, "2", outer(close))
"""
    with pytest.raises(CompileError, match="inside conditional control flow"):
        transpile(src)


def test_nested_persistent_helper_in_var_initializer_is_rejected():
    src = """//@version=6
strategy("persistent initializer nested helper reject")
stateful() =>
    var float state = 0.0
    state += 1.0
    state
outer() =>
    var float captured = stateful()
    captured
value = request.security(syminfo.tickerid, "2", outer())
"""
    with pytest.raises(CompileError, match="inside conditional control flow"):
        transpile(src)


def test_security_tuple_helpers_support_arbitrary_numeric_arity_and_bindings():
    src = """//@version=6
strategy("numeric tuple arity")
pair(float src) =>
    [src, 2]
quad(float src) =>
    float shifted = src + 1.0
    [src, shifted, 3, src + 3.0]
[p0, p1] = request.security(syminfo.tickerid, "2", pair(close))
[q0, q1, q2, q3] = request.security(syminfo.tickerid, "2", quad(close))
plot(p0 + p1 + q0 + q1 + q2 + q3)
"""
    cpp = transpile(src)

    # Preserve the established two-element representation while extending the
    # same double-coercing storage contract to larger numeric tuples.
    assert re.search(r"std::tuple<double, double> _req_sec_0\s*=", cpp)
    assert re.search(
        r"std::tuple<double, double, double, double> _req_sec_1\s*=",
        cpp,
    )
    # Expression-only helper parameters and multi-statement helper locals are
    # both lowered in the requested context; neither source identifier may
    # leak into the generated evaluator as an undeclared C++ name.
    body0 = _eval_body(cpp, 0, 1)
    body1 = _eval_body(cpp, 1, None)
    assert "std::make_tuple(bar.close, 2)" in body0
    assert re.search(
        r"double _sec1_quad_\d+_shifted = \(bar\.close \+ 1\.0\);",
        body1,
    )
    assert re.search(
        r"_req_sec_1 = std::make_tuple\(bar\.close, "
        r"_sec1_quad_\d+_shifted, 3, \(bar\.close \+ 3\.0\)\);",
        body1,
    )
    compile_env.compile_cpp(cpp, label="security-helper-numeric-tuple-arity")


def test_security_tuple_helper_numeric_arity_executes_natively():
    src = """//@version=6
strategy("numeric tuple runtime")
quad(float src) =>
    float shifted = src + 1.0
    [src, shifted, 3, src + 3.0]
[a, b, c, d] = request.security(syminfo.tickerid, "1", quad(close))
outA = a
outB = b
outC = c
outD = d
plot(outA + outB + outC + outD)
"""
    driver = r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bars[] = {
        Bar{1.0, 1.0, 1.0, 1.0, 1.0, 0},
        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 60000},
        Bar{3.0, 3.0, 3.0, 3.0, 1.0, 120000},
    };
    strategy.run(bars, 3, "1", "1");
    std::cout << strategy.outA << " " << strategy.outB << " "
              << strategy.outC << " " << strategy.outD << "\n";
    return 0;
}
"""
    values = tuple(
        float(value)
        for value in _compile_and_run(transpile(src) + driver).split()
    )
    assert values == (3.0, 4.0, 3.0, 6.0)


def test_security_tuple_helper_rejects_mixed_elements_before_codegen():
    src = """//@version=6
strategy("mixed tuple reject")
f(float src) =>
    string tag = "value"
    [src, tag]
[value, tag] = request.security(syminfo.tickerid, "2", f(close))
"""
    with pytest.raises(
        CompileError,
        match=(
            r"two or more numeric int/float elements or homogeneous bool "
            r"elements; "
            r"inferred 2 element\(s\) \[float, string\]"
        ),
    ):
        transpile(src)


def test_security_tuple_helper_rejects_mixed_numeric_bool_shape_honestly():
    src = """//@version=6
strategy("mixed tuple reject")
f(float src) =>
    [src, src > 1.0, src + 2.0]
[a, b, c] = request.security(syminfo.tickerid, "2", f(close))
"""
    with pytest.raises(
        CompileError,
        match=(
            r"two or more numeric int/float elements or homogeneous bool "
            r"elements; inferred 3 element\(s\) \[float, bool, float\]"
        ),
    ):
        transpile(src)


def test_security_tuple_helper_supports_generic_four_bool_shape():
    cpp = transpile(_FOUR_BOOL_TUPLE_SHAPE)
    assert re.search(
        r"std::tuple<bool, bool, bool, bool> _req_sec_0\s*=\s*"
        r"std::tuple<bool, bool, bool, bool>\{false, false, false, false\};",
        cpp,
    )
    assert "_req_sec_0 = std::make_tuple(" in _eval_body(cpp, 0, None)
    assert re.search(r"bool isPositive\s*=", cpp)
    assert re.search(r"bool isNegative\s*=", cpp)
    assert re.search(r"bool isAtLeastTwo\s*=", cpp)
    assert re.search(r"bool isThree\s*=", cpp)
    compile_env.compile_cpp(cpp, label="security-helper-generic-bool-tuple")


def test_security_tuple_helper_generic_bool_values_execute_natively():
    driver = r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bars[] = {
        Bar{1.0, 1.0, 1.0, 1.0, 1.0, 0},
        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 60000},
    };
    strategy.run(bars, 2, "1", "1");
    std::cout << strategy.outPositive << " " << strategy.outNegative << " "
              << strategy.outAtLeastTwo << " " << strategy.outThree << "\n";
    return 0;
}
"""
    values = tuple(
        int(value)
        for value in _compile_and_run(
            transpile(_FOUR_BOOL_TUPLE_SHAPE) + driver
        ).split()
    )
    assert values == (1, 0, 1, 0)


def test_program_security_tuple_assign_writes_members_for_udf_reads():
    src = """//@version=6
strategy("program tuple member routing")
pair() =>
    [true, false, true, false]
[b0, b1, b2, b3] = request.security(syminfo.tickerid, "1", pair())
read() =>
    b0
out = read() ? 1 : 0
plot(out)
"""
    cpp = transpile(src)
    assert "auto [b0, b1, b2, b3] = _req_sec_0;" not in cpp
    match = re.search(r"auto (_tuple_result_\d+) = _req_sec_0;", cpp)
    assert match is not None
    temp = match.group(1)
    assert f"b0 = std::get<0>({temp});" in cpp
    assert f"b1 = std::get<1>({temp});" in cpp
    assert f"b2 = std::get<2>({temp});" in cpp
    assert f"b3 = std::get<3>({temp});" in cpp
    assert "return b0;" in cpp
    compile_env.compile_cpp(cpp, label="program-security-tuple-member-routing")


def test_program_security_tuple_assign_member_value_executes_natively():
    src = """//@version=6
strategy("program tuple member runtime")
pair() =>
    [true, false, true, false]
[b0, b1, b2, b3] = request.security(syminfo.tickerid, "1", pair())
read() =>
    b0
out = read() ? 1 : 0
plot(out)
"""
    driver = r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bars[] = {
        Bar{1.0, 1.0, 1.0, 1.0, 1.0, 0},
        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 60000},
    };
    strategy.run(bars, 2, "1", "1");
    std::cout << strategy.out << "\n";
    return 0;
}
"""
    value = int(
        _compile_and_run(transpile(src) + driver).strip()
    )
    assert value == 1


def test_callable_tuple_assign_keeps_lexical_structured_binding():
    src = """//@version=6
strategy("callable tuple lexical routing")
pair() =>
    [1.0, 2.0]
readPair() =>
    [left, right] = pair()
    left + right
out = readPair()
plot(out)
"""
    cpp = transpile(src)
    assert "auto [left, right] = pair();" in cpp
    assert not re.search(r"left = std::get<0>\(", cpp)
    assert not re.search(r"right = std::get<1>\(", cpp)
    compile_env.compile_cpp(cpp, label="callable-tuple-lexical-routing")
