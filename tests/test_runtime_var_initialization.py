"""Regression coverage for runtime scalar ``var`` initializers.

Pine evaluates a ``var`` / ``varip`` initializer once, when execution first
reaches its declaration. A generated C++ constructor cannot evaluate
bar-dependent expressions, and a global first-bar preamble cannot preserve
dependencies or conditional first-entry semantics, so primitive runtime
initializers use declaration-site once guards.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests import _compile as compile_env


def _var_init_block(cpp: str) -> str:
    start = cpp.index("        if (!_var_initialized) {")
    end = cpp.index("            _var_initialized = true;", start)
    return cpp[start:end]


def test_runtime_scalar_var_and_varip_initialize_at_declaration_once():
    cpp = transpile("""//@version=6
strategy("runtime scalar var init")
var float seededLow = low
varip float seededHigh = high
var float literalSeed = 7.5
seededLow += 1.0
    seededHigh += 1.0
""", check_support=False)

    assert "double seededLow = na<double>();" in cpp
    assert "double seededHigh = na<double>();" in cpp
    assert "if (!_pf_var_init_seededLow) {" in cpp
    assert "if (!_pf_var_init_seededHigh) {" in cpp
    assert cpp.count("seededLow = current_bar_.low;") == 1
    assert cpp.count("seededHigh = current_bar_.high;") == 1
    assert "decltype(GeneratedStrategy::_pf_var_init_seededLow)" in cpp
    assert "this->_pf_var_init_seededLow =" in cpp

    # The literal retains its existing constructor route and receives no
    # redundant declaration-site guard.
    assert "literalSeed(7.5)" in cpp
    assert "_pf_var_init_literalSeed" not in cpp


def test_runtime_scalar_dependencies_follow_source_order():
    cpp = transpile("""//@version=6
strategy("runtime scalar dependency order")
length = input.int(3, "Length")
emaValue = ta.ema(close, length)
plainValue = emaValue + 2.0
var int fromInput = length
var float fromTA = emaValue
var float fromPlain = plainValue
var float directInput = input.float(4.5, "Direct Seed")
""")

    # Input-backed aliases are runtime values even though their defaults can
    # be constant-folded for other constructor decisions.
    assert "fromInput(3)" not in cpp
    assert "if (!_pf_var_init_fromInput)" in cpp
    assert "fromInput = length;" in cpp
    assert "directInput = get_input_double(\"Direct Seed\", 4.5);" in cpp

    on_bar = cpp[cpp.index("    void on_bar("):]
    input_pos = on_bar.index('length = get_input_int("Length", 3);')
    ema_pos = on_bar.index("emaValue =")
    plain_pos = on_bar.index("plainValue =")
    from_input_pos = on_bar.index("fromInput = length;")
    from_ta_pos = on_bar.index("fromTA = emaValue;")
    from_plain_pos = on_bar.index("fromPlain = plainValue;")
    assert input_pos < ema_pos < plain_pos
    assert plain_pos < from_input_pos < from_ta_pos < from_plain_pos


def test_conditional_sibling_vars_get_distinct_lazy_members_and_flags():
    cpp = transpile("""//@version=6
strategy("conditional runtime scalar vars")
if close > 50
    var float pending = low
    pending += 1.0
if close < 0
    var float pending = high
    pending += 2.0
""")

    assert "double pending = na<double>();" in cpp
    assert "double pending__blk1 = na<double>();" in cpp
    assert "bool _pf_var_init_pending = false;" in cpp
    assert "bool _pf_var_init_pending__blk1 = false;" in cpp
    assert "pending = current_bar_.low;" in cpp
    assert "pending__blk1 = current_bar_.high;" in cpp
    on_bar = cpp[cpp.index("    void on_bar("):]
    assert on_bar.index("if (!_pf_var_init_pending)") > on_bar.index("if (([&]")


def test_if_else_same_name_mixed_runtime_and_literal_vars_are_independent():
    cpp = transpile("""//@version=6
strategy("if else mixed var init")
length = input.float(4.0, "Length")
if close > 0
    var float branchValue = length
    branchValue += 1.0
else
    var float branchValue = 7.0
    branchValue += 2.0
""")

    assert "double branchValue = na<double>();" in cpp
    assert "double branchValue__blk1;" in cpp
    assert "branchValue__blk1(7)" in cpp
    assert "bool _pf_var_init_branchValue = false;" in cpp
    assert "_pf_var_init_branchValue__blk1" not in cpp
    assert "branchValue = length;" in cpp
    assert "branchValue__blk1 += 2.0;" in cpp


def test_switch_cases_same_name_get_per_case_storage_and_init_routes():
    cpp = transpile("""//@version=6
strategy("switch mixed var init")
switch
    close > 10 =>
        var float caseValue = low
        caseValue += 1.0
    close > 0 =>
        var float caseValue = 2.0
        caseValue += 2.0
    =>
        var float caseValue = high
        caseValue += 3.0
""")

    assert "double caseValue = na<double>();" in cpp
    assert "double caseValue__blk1;" in cpp
    assert "double caseValue__blk2 = na<double>();" in cpp
    assert "caseValue__blk1(2)" in cpp
    assert "bool _pf_var_init_caseValue = false;" in cpp
    assert "bool _pf_var_init_caseValue__blk2 = false;" in cpp
    assert "_pf_var_init_caseValue__blk1" not in cpp
    assert "caseValue = current_bar_.low;" in cpp
    assert "caseValue__blk1 += 2.0;" in cpp
    assert "caseValue__blk2 = current_bar_.high;" in cpp


def test_if_expression_branch_vars_receive_runtime_init_routes():
    cpp = transpile("""//@version=6
strategy("if expression runtime var init")
float result = if close > 0
    var float branchState = low
    branchState
else
    var float branchState = high
    branchState
""")

    assert "double branchState = na<double>();" in cpp
    assert "double branchState__blk1 = na<double>();" in cpp
    assert "bool _pf_var_init_branchState = false;" in cpp
    assert "bool _pf_var_init_branchState__blk1 = false;" in cpp
    assert "branchState = current_bar_.low;" in cpp
    assert "branchState__blk1 = current_bar_.high;" in cpp
    assert "result = branchState;" in cpp
    assert "result = branchState__blk1;" in cpp


def test_switch_expression_case_vars_receive_runtime_init_routes():
    cpp = transpile("""//@version=6
strategy("switch expression runtime var init")
float result = switch
    close > 10 =>
        var float caseState = low
        caseState
    =>
        var float caseState = high
        caseState
""")

    assert "double caseState = na<double>();" in cpp
    assert "double caseState__blk1 = na<double>();" in cpp
    assert "bool _pf_var_init_caseState = false;" in cpp
    assert "bool _pf_var_init_caseState__blk1 = false;" in cpp
    assert "caseState = current_bar_.low;" in cpp
    assert "caseState__blk1 = current_bar_.high;" in cpp
    assert "result = caseState;" in cpp
    assert "result = caseState__blk1;" in cpp


def test_function_vars_use_exact_declaration_site_initialization_route():
    cpp = transpile("""//@version=6
strategy("function var init isolation")
helper() =>
    var float functionSeed = high
    functionSeed += 1.0
    functionSeed
b = helper()
""")

    assert "if (!this->_pf_var_init_functionSeed)" in cpp
    # Legacy per-variant members remain part of the checkpoint shape, but the
    # callable body must no longer consult them for initialization timing.
    assert "bool _fvinit_helper_cs0 = false;" in cpp
    assert "if (!_fvinit_helper_cs0" not in cpp
    assert "functionSeed = current_bar_.high;" in cpp


def test_runtime_var_flag_name_avoids_user_member_collision():
    cpp = transpile("""//@version=6
strategy("runtime var flag collision")
var float _pf_var_init_seeded = 1.0
_pf_var_init_otherSeed = 2.0
var float seeded = low
var float otherSeed = high
seeded += _pf_var_init_seeded
otherSeed += _pf_var_init_otherSeed
""")

    assert "double _pf_var_init_seeded;" in cpp
    assert "bool _pf_var_init_seeded_2 = false;" in cpp
    assert "if (!_pf_var_init_seeded_2)" in cpp
    assert "double _pf_var_init_otherSeed = 0.0;" in cpp
    assert "bool _pf_var_init_otherSeed_2 = false;" in cpp
    assert "if (!_pf_var_init_otherSeed_2)" in cpp


def test_runtime_scalar_route_does_not_duplicate_specialized_var_initializers():
    cpp = transpile("""//@version=6
strategy("specialized var init")
type Point
    float x
var array<float> values = array.new<float>()
var matrix<float> grid = matrix.new<float>(1, 1, low)
var map<string, float> lookup = map.new<string, float>()
var Point point = Point.new(low)
var line marker = na
""")

    init_block = _var_init_block(cpp)
    assert init_block.count("values = std::vector<double>();") == 1
    assert init_block.count(
        "grid = PineMatrix::new_(1, 1, current_bar_.low);"
    ) == 1
    assert init_block.count(
        "lookup = PineMap<std::string, double>::new_();"
    ) == 1
    assert init_block.count(
        "point = Point{.x = current_bar_.low, .__pf_na = false};"
    ) == 1
    assert "marker =" not in init_block


_RUNTIME_PINE = """//@version=6
strategy("runtime var init execution")
var float seeded = low
var float directSeed = input.float(5.0, "Direct Seed")
aliasSeed = input.float(6.0, "Alias Seed")
var float aliasedSeed = aliasSeed
seeded += 1.0
directSeed += 1.0
aliasedSeed += 1.0
"""


_CPP_DRIVER = r"""
#include <iomanip>
#include <iostream>

int main() {
    Bar bars[] = {
        Bar{11.0, 12.0, 10.0, 11.5, 100.0, 1000},
        Bar{101.0, 102.0, 100.0, 101.5, 200.0, 2000},
    };

    GeneratedStrategy first;
    GeneratedStrategy second;
    first.set_input("Direct Seed", "20.0");
    first.set_input("Alias Seed", "30.0");
    second.set_input("Direct Seed", "20.0");
    second.set_input("Alias Seed", "30.0");
    first.run(bars, 2);
    second.run(bars, 2);

    std::cout << std::fixed << std::setprecision(1)
              << first.seeded << "\t"
              << first.directSeed << "\t"
              << first.aliasedSeed << "\t"
              << second.seeded << "\t"
              << second.directSeed << "\t"
              << second.aliasedSeed << "\n";
    return 0;
}
"""


def _find_engine_library() -> Path | None:
    explicit = os.environ.get("PINEFORGE_ENGINE_LIB")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if path.is_file() else None

    engine_inc = compile_env._ENGINE_INC
    if engine_inc is None:
        return None
    candidates: list[Path] = []
    for pattern in ("build*/lib/libpineforge.a", "build*/lib/libpineforge.dylib"):
        candidates.extend(sorted(engine_inc.parent.glob(pattern)))
    return candidates[0].resolve() if candidates else None


def _compile_and_run(cpp_source: str) -> str:
    compile_env.skip_if_no_compile_env()
    engine_lib = _find_engine_library()
    if engine_lib is None:
        pytest.skip(
            "built libpineforge not found; set PINEFORGE_ENGINE_LIB or build "
            "the engine beside PINEFORGE_ENGINE_INCLUDE"
        )

    compiler = compile_env._COMPILER
    engine_inc = compile_env._ENGINE_INC
    eigen_inc = compile_env._EIGEN_INC
    assert compiler is not None and engine_inc is not None and eigen_inc is not None

    with tempfile.TemporaryDirectory(prefix="pineforge-runtime-var-init-") as tmp:
        cpp_path = Path(tmp) / "runtime_var_init.cpp"
        exe_path = Path(tmp) / "runtime_var_init"
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
                "runtime var initializer probe failed to link\n"
                + "\n".join((built.stderr or built.stdout).splitlines()[:80])
            )

        ran = subprocess.run(
            [str(exe_path)], capture_output=True, text=True, timeout=30
        )
        if ran.returncode != 0:
            raise AssertionError(
                f"runtime var initializer probe exited {ran.returncode}\n"
                f"stdout:\n{ran.stdout}\nstderr:\n{ran.stderr}"
            )
        return ran.stdout


def test_runtime_var_initializer_uses_first_bar_once_and_is_deterministic():
    cpp = transpile(_RUNTIME_PINE)
    stdout = _compile_and_run(cpp + _CPP_DRIVER)

    # ``seeded`` starts at the first bar's low (10), then increments once on
    # each of two bars. Reinitializing from the second bar's low would be 101;
    # dropping the initializer cannot deterministically produce 12.
    assert stdout == "12.0\t22.0\t32.0\t12.0\t22.0\t32.0\n"


_CONDITIONAL_RUNTIME_PINE = """//@version=6
strategy("conditional runtime var execution")
if close > 50
    var float pending = low
    pending += 1.0
if close < 0
    var float pending = high
    pending += 2.0
"""


_CONDITIONAL_CPP_DRIVER = r"""
#include <iomanip>
#include <iostream>

int main() {
    Bar bars[] = {
        Bar{9.0, 12.0, 8.0, 10.0, 100.0, 1000},
        Bar{55.0, 65.0, 50.0, 60.0, 100.0, 2000},
        Bar{0.0, 5.0, -2.0, -1.0, 100.0, 3000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    std::cout << std::fixed << std::setprecision(1)
              << strategy.pending << "\t" << strategy.pending__blk1 << "\n";
    return 0;
}
"""


def test_conditional_vars_initialize_on_first_block_entry_at_runtime():
    cpp = transpile(_CONDITIONAL_RUNTIME_PINE)
    stdout = _compile_and_run(cpp + _CONDITIONAL_CPP_DRIVER)

    # Neither block executes on bar zero. The first declaration therefore
    # seeds from bar one's low (50), and the same-named sibling independently
    # seeds from bar two's high (5).
    assert stdout == "51.0\t7.0\n"
