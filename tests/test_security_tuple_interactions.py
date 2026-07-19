"""Bounded interaction matrices for typed ``request.security`` tuples.

The first matrix crosses four genuinely orthogonal helper/codegen factors.
Direct tuple literals cannot also possess a helper wrapper or helper parameter
scope, so requested-bar completion is covered by a separate N=2 matrix instead
of padding an N=5 matrix with meaningless duplicate cells.  Both matrices run
every cell and aggregate failures; neither stops after an early success.
"""

from __future__ import annotations

from itertools import product
import math
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests import _compile as compile_env
from tests.test_security_helper_local_var import _compile_and_run


def _helper_matrix_source(
    wrapper: bool,
    colliding_params: bool,
    udf_global_read: bool,
    compatible_shadow: bool,
) -> tuple[str, bool]:
    """Return one N=4 cell and whether its wrapper is forward-defined."""
    up_param = "x" if colliding_params else "probe"
    plus_param = "x" if colliding_params else "seed"
    four_param = "x" if colliding_params else "src"
    outer_param = "x" if colliding_params else "outerSrc"

    definitions = [
        f"up(float {up_param}) =>\n    {up_param} > 1.0",
        f"plus(float {plus_param}) =>\n    {plus_param} + 1.0",
    ]
    four = (
        f"four(float {four_param}) =>\n"
        f"    [up(plus({four_param})), {four_param} > 2.0, "
        f"{four_param} > 3.0, {four_param} > 4.0]"
    )
    outer = (
        f"outer(float {outer_param}) =>\n"
        f"    four({outer_param})"
    )

    # Fold both wrapper definition orders into the wrapper-on cells without
    # inventing a fifth factor: opposite collision/read parity is forward.
    forward_wrapper = wrapper and (colliding_params != udf_global_read)
    if wrapper and forward_wrapper:
        definitions.extend([outer, four])
    else:
        definitions.append(four)
        if wrapper:
            definitions.append(outer)

    request_expr = "outer(close)" if wrapper else "four(close)"
    readout = (
        "readGlobal() =>\n    a\n"
        "out = readGlobal() ? 1 : 0"
        if udf_global_read
        else "out = a ? 1 : 0"
    )
    shadow_type = "bool" if compatible_shadow else "float"
    shadow_value = "false" if compatible_shadow else "1.5"
    source = f'''//@version=6
strategy("typed security tuple N4")
{chr(10).join(definitions)}
[a, b, c, d] = request.security(syminfo.tickerid, "1", {request_expr}, barmerge.gaps_off, barmerge.lookahead_off)
if close < 0.0
    {shadow_type} a = {shadow_value}
{readout}
plot(out)
'''
    return source, forward_wrapper


_BOOL_MATRIX_DRIVER = r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bars[] = {
        Bar{1.0, 1.0, 1.0, 1.0, 1.0, 0},
        Bar{4.0, 4.0, 4.0, 4.0, 1.0, 60000},
    };
    strategy.run(bars, 2, "1", "1");
    std::cout << strategy.out << "\n";
    return 0;
}
"""


def test_security_bool_tuple_n4_interaction_matrix_runs_all_16_cells():
    failures: list[str] = []
    visited: list[str] = []

    for bits in product((False, True), repeat=4):
        wrapper, colliding_params, udf_global_read, compatible_shadow = bits
        case = "".join("1" if bit else "0" for bit in bits)
        visited.append(case)
        source, forward_wrapper = _helper_matrix_source(*bits)

        try:
            cpp = transpile(source)
        except CompileError as exc:
            message = str(exc)
            if forward_wrapper:
                if "forward tuple-wrapper calls are not supported yet" not in message:
                    failures.append(f"{case}: wrong forward-order error: {message}")
            elif not compatible_shadow:
                if "shadows a direct script binding" not in message:
                    failures.append(f"{case}: wrong shadow-boundary error: {message}")
            else:
                failures.append(f"{case}: unexpected CompileError: {message}")
            continue
        except Exception as exc:  # keep the harness exhaustive
            failures.append(f"{case}: unexpected {type(exc).__name__}: {exc}")
            continue

        if forward_wrapper:
            failures.append(f"{case}: forward wrapper unexpectedly transpiled")
            continue
        if not compatible_shadow:
            failures.append(f"{case}: incompatible float shadow unexpectedly transpiled")
            continue

        try:
            if "auto [a, b, c, d] = _req_sec_0;" in cpp:
                raise AssertionError("security tuple remained a shadowing local")
            temp_match = re.search(r"auto (_tuple_result_\d+) = _req_sec_0;", cpp)
            if temp_match is None:
                raise AssertionError("security tuple member materialization is missing")
            temp = temp_match.group(1)
            for index, name in enumerate(("a", "b", "c", "d")):
                expected = f"{name} = std::get<{index}>({temp});"
                if expected not in cpp:
                    raise AssertionError(f"missing member assignment: {expected}")
            value = int(_compile_and_run(cpp + _BOOL_MATRIX_DRIVER).strip())
            if value != 1:
                raise AssertionError(f"native output was {value}, expected 1")
        except Exception as exc:  # keep evaluating later cells
            failures.append(f"{case}: {type(exc).__name__}: {exc}")

    assert len(visited) == 16
    assert len(set(visited)) == 16
    assert not failures, "N=4 tuple matrix failures:\n" + "\n".join(failures)


def _gap_matrix_source(helper_tuple: bool) -> str:
    helper = "pair() =>\n    [false, true]\n" if helper_tuple else ""
    expression = "pair()" if helper_tuple else "[false, true]"
    return f'''//@version=6
strategy("typed security tuple gap N2")
{helper}[a, b] = request.security(syminfo.tickerid, "2", {expression}, barmerge.gaps_off, barmerge.lookahead_off)
readA() =>
    a
readB() =>
    b
outA = readA() ? 1 : 0
outB = readB() ? 1 : 0
plot(outA + outB)
'''


def _gap_matrix_driver(complete_requested_bar: bool) -> str:
    second_bar = (
        "        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 60000},\n"
        if complete_requested_bar
        else ""
    )
    count = 2 if complete_requested_bar else 1
    return f'''
#include <iostream>
int main() {{
    GeneratedStrategy strategy;
    Bar bars[] = {{
        Bar{{1.0, 1.0, 1.0, 1.0, 1.0, 0}},
{second_bar}    }};
    strategy.run(bars, {count}, "1", "1");
    std::cout << strategy.outA << " " << strategy.outB << "\\n";
    return 0;
}}
'''


def test_security_bool_tuple_initial_gap_n2_matrix_runs_all_4_cells():
    failures: list[str] = []
    visited: list[str] = []

    for helper_tuple, complete_requested_bar in product((False, True), repeat=2):
        case = f"helper={int(helper_tuple)},complete={int(complete_requested_bar)}"
        visited.append(case)
        try:
            cpp = transpile(_gap_matrix_source(helper_tuple))
            if helper_tuple:
                expected_decl = (
                    "std::tuple<bool, bool> _req_sec_0 = "
                    "std::tuple<bool, bool>{false, false};"
                )
                if expected_decl not in cpp:
                    raise AssertionError("helper tuple lacks typed false defaults")
            else:
                for index in range(2):
                    expected_decl = f"bool _req_sec_0_{index} = false;"
                    if expected_decl not in cpp:
                        raise AssertionError(
                            f"direct tuple lacks typed false default: {index}"
                        )
            values = tuple(
                int(value)
                for value in _compile_and_run(
                    cpp + _gap_matrix_driver(complete_requested_bar)
                ).split()
            )
            expected = (0, 1) if complete_requested_bar else (0, 0)
            if values != expected:
                raise AssertionError(f"native output {values}, expected {expected}")
        except Exception as exc:  # keep evaluating later cells
            failures.append(f"{case}: {type(exc).__name__}: {exc}")

    assert len(visited) == 4
    assert len(set(visited)) == 4
    assert not failures, "N=2 initial-gap matrix failures:\n" + "\n".join(failures)


def test_ordinary_heterogeneous_tuple_keeps_lexical_structured_binding():
    source = '''//@version=6
strategy("ordinary heterogeneous tuple")
pair() =>
    [close, "tag"]
[value, tag] = pair()
plot(value)
'''
    cpp = transpile(source)
    assert "auto [value, tag] = pair();" in cpp
    assert not re.search(r"value = std::get<0>\(", cpp)
    assert not re.search(r"tag = std::get<1>\(", cpp)
    compile_env.compile_cpp(cpp, label="ordinary-heterogeneous-tuple-routing")


_FOUR_EMA_VARIANTS = '''//@version=6
strategy("four requested EMA variants")
e(float src, int len) =>
    ta.ema(src, len)
four(float src) =>
    [e(src, 2), e(src, 3), e(src, 4), e(src, 5)]
[a, b, c, d] = request.security(syminfo.tickerid, "1", four(close))
outA = a
outB = b
outC = c
outD = d
plot(outA + outB + outC + outD)
'''


def test_security_tuple_preserves_four_distinct_ema_variants_structurally():
    cpp = transpile(_FOUR_EMA_VARIANTS)
    members = set(re.findall(r"ta::EMA (_sec0__ta_ema_1_v\d+);", cpp))
    assert len(members) == 4

    constructor = next(
        line for line in cpp.splitlines()
        if line.strip().startswith("explicit GeneratedStrategy()")
    )
    ctor_pairs = dict(
        re.findall(r"(_sec0__ta_ema_1_v\d+)\((\d+)\)", constructor)
    )
    assert set(ctor_pairs) == members
    assert sorted(ctor_pairs.values()) == ["2", "3", "4", "5"]

    for member in members:
        assert f"{member}.compute(bar.close)" in cpp
        assert f"{member}.recompute(bar.close)" in cpp
    compile_env.compile_cpp(cpp, label="security-four-distinct-ema-variants")


def test_security_tuple_four_ema_variants_are_distinct_natively():
    driver = r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bars[] = {
        Bar{1.0, 1.0, 1.0, 1.0, 1.0, 0},
        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 60000},
        Bar{4.0, 4.0, 4.0, 4.0, 1.0, 120000},
        Bar{8.0, 8.0, 8.0, 8.0, 1.0, 180000},
    };
    strategy.run(bars, 4, "1", "1");
    std::cout << strategy.outA << " " << strategy.outB << " "
              << strategy.outC << " " << strategy.outD << "\n";
    return 0;
}
"""
    values = tuple(
        float(value)
        for value in _compile_and_run(
            transpile(_FOUR_EMA_VARIANTS) + driver
        ).split()
    )
    assert len(values) == 4
    assert all(math.isfinite(value) for value in values)
    assert len(set(values)) == 4
    assert values[0] > values[1] > values[2] > values[3]


_FOUR_INPUT_EMA_VARIANTS = '''//@version=6
strategy("four requested input EMA variants")
l2 = input.int(2, "L2")
l3 = input.int(3, "L3")
l4 = input.int(4, "L4")
l5 = input.int(5, "L5")
e(float src, int len) =>
    ta.ema(src, len)
four(float src, int p2, int p3, int p4, int p5) =>
    [e(src, p2), e(src, p3), e(src, p4), e(src, p5)]
[a, b, c, d] = request.security(syminfo.tickerid, "1", four(close, l2, l3, l4, l5))
outA = a
outB = b
outC = c
outD = d
plot(outA + outB + outC + outD)
'''


def test_security_tuple_input_ema_variants_get_distinct_runtime_resets():
    cpp = transpile(_FOUR_INPUT_EMA_VARIANTS)
    members = set(re.findall(r"ta::EMA (_sec0__ta_ema_1_v\d+);", cpp))
    assert len(members) == 4

    constructor = next(
        line for line in cpp.splitlines()
        if line.strip().startswith("explicit GeneratedStrategy()")
    )
    ctor_pairs = dict(
        re.findall(r"(_sec0__ta_ema_1_v\d+)\((\d+)\)", constructor)
    )
    assert set(ctor_pairs) == members
    assert set(ctor_pairs.values()) == {"1"}

    resets = set(
        re.findall(
            r'(_sec0__ta_ema_1_v\d+) = ta::EMA\(get_input_int\("(L[2-5])", ([2-5])\)\);',
            cpp,
        )
    )
    assert {member for member, _title, _default in resets} == members
    assert {(title, default) for _member, title, default in resets} == {
        ("L2", "2"),
        ("L3", "3"),
        ("L4", "4"),
        ("L5", "5"),
    }
    compile_env.compile_cpp(cpp, label="security-four-input-ema-runtime-resets")


def test_security_tuple_input_ema_override_changes_only_matching_variant():
    driver = r"""
#include <iostream>

void run_and_print(GeneratedStrategy& strategy) {
    Bar bars[] = {
        Bar{1.0, 1.0, 1.0, 1.0, 1.0, 0},
        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 60000},
        Bar{4.0, 4.0, 4.0, 4.0, 1.0, 120000},
        Bar{8.0, 8.0, 8.0, 8.0, 1.0, 180000},
    };
    strategy.run(bars, 4, "1", "1");
    std::cout << strategy.outA << " " << strategy.outB << " "
              << strategy.outC << " " << strategy.outD << "\n";
}

int main() {
    GeneratedStrategy defaults;
    run_and_print(defaults);

    GeneratedStrategy overridden;
    overridden.set_input("L2", "5");
    run_and_print(overridden);
    return 0;
}
"""
    values = tuple(
        float(value)
        for value in _compile_and_run(
            transpile(_FOUR_INPUT_EMA_VARIANTS) + driver
        ).split()
    )
    assert len(values) == 8
    defaults = values[:4]
    overridden = values[4:]
    assert len(set(defaults)) == 4
    assert defaults[0] > defaults[1] > defaults[2] > defaults[3]
    assert math.isclose(overridden[0], defaults[3], rel_tol=1e-5)
    for actual, expected in zip(overridden[1:], defaults[1:]):
        assert math.isclose(actual, expected, rel_tol=1e-5)


_FOUR_TIMEFRAME_EMA_VARIANTS = '''//@version=6
strategy("four requested timeframe EMA variants")
e(float src, int len) =>
    ta.ema(src, len)
four(float src) =>
    [e(src, timeframe.multiplier), e(src, timeframe.multiplier + 1), e(src, timeframe.multiplier + 2), e(src, timeframe.multiplier + 3)]
[a, b, c, d] = request.security(syminfo.tickerid, "1", four(close))
outA = a
outB = b
outC = c
outD = d
plot(outA + outB + outC + outD)
'''


def test_security_tuple_timeframe_ema_variants_reset_and_run_distinctly():
    cpp = transpile(_FOUR_TIMEFRAME_EMA_VARIANTS)
    members = set(re.findall(r"ta::EMA (_sec0__ta_ema_1_v\d+);", cpp))
    assert len(members) == 4
    resets = set(
        re.findall(
            r'(_sec0__ta_ema_1_v\d+) = ta::EMA\((\(?tf_multiplier\("1"\)(?: \+ [123])?\)?)\);',
            cpp,
        )
    )
    assert {member for member, _length in resets} == members
    assert {length for _member, length in resets} == {
        'tf_multiplier("1")',
        '(tf_multiplier("1") + 1)',
        '(tf_multiplier("1") + 2)',
        '(tf_multiplier("1") + 3)',
    }

    driver = r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bars[] = {
        Bar{1.0, 1.0, 1.0, 1.0, 1.0, 0},
        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 60000},
        Bar{4.0, 4.0, 4.0, 4.0, 1.0, 120000},
        Bar{8.0, 8.0, 8.0, 8.0, 1.0, 180000},
    };
    strategy.run(bars, 4, "1", "1");
    std::cout << strategy.outA << " " << strategy.outB << " "
              << strategy.outC << " " << strategy.outD << "\n";
    return 0;
}
"""
    values = tuple(
        float(value)
        for value in _compile_and_run(cpp + driver).split()
    )
    assert len(values) == 4
    assert all(math.isfinite(value) for value in values)
    assert len(set(values)) == 4
    assert values[0] > values[1] > values[2] > values[3]
    assert math.isclose(values[0], 8.0, rel_tol=1e-9)


def test_security_tuple_rejects_mixed_input_and_series_ema_length():
    source = '''//@version=6
strategy("requested EMA mixed stable and series length")
length = input.int(2, "L")
e(float src, int len) =>
    ta.ema(src, len)
four(float src, int p) =>
    [e(src, p), e(src, p + int(close)), e(src, 4), e(src, 5)]
[a, b, c, d] = request.security(syminfo.tickerid, "1", four(close, length))
plot(a + b + c + d)
'''
    with pytest.raises(CompileError) as exc_info:
        transpile(source)
    message = str(exc_info.value)
    assert "requested-context TA constructor length" in message
    assert "not a stable per-run scalar" in message


_DIRECT_HETEROGENEOUS_SECURITY_TUPLE = '''//@version=6
strategy("direct heterogeneous requested tuple")
[a, b] = request.security(syminfo.tickerid, "2", [close, close > open])
readB() =>
    b
direct = b ? 1 : 0
viaUdf = readB() ? 1 : 0
plot(direct + viaUdf)
'''


def test_direct_heterogeneous_security_tuple_materializes_program_members():
    cpp = transpile(_DIRECT_HETEROGENEOUS_SECURITY_TUPLE)
    assert "auto [a, b] = std::make_tuple(" not in cpp
    temp_match = re.search(r"auto (_tuple_result_\d+) = std::make_tuple\(", cpp)
    assert temp_match is not None
    temp = temp_match.group(1)
    assert f"a = std::get<0>({temp});" in cpp
    assert f"b = std::get<1>({temp});" in cpp
    compile_env.compile_cpp(cpp, label="security-direct-heterogeneous-members")


def test_direct_heterogeneous_security_tuple_udf_reads_fresh_member_natively():
    driver = r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bars[] = {
        Bar{1.0, 2.0, 0.5, 1.0, 1.0, 0},
        Bar{1.0, 4.0, 0.5, 4.0, 1.0, 60000},
    };
    strategy.run(bars, 2, "1", "1");
    std::cout << strategy.direct << " " << strategy.viaUdf << " "
              << strategy.b << "\n";
    return 0;
}
"""
    values = tuple(
        int(value)
        for value in _compile_and_run(
            transpile(_DIRECT_HETEROGENEOUS_SECURITY_TUPLE) + driver
        ).split()
    )
    assert values == (1, 1, 1)


_SECURITY_MACD_TUPLE = '''//@version=6
strategy("requested MACD tuple program members")
[macdLine, signalLine, histogram] = request.security(syminfo.tickerid, "1", ta.macd(close, 2, 3, 2))
readMacd() =>
    macdLine
direct = macdLine
viaUdf = readMacd()
plot(direct + viaUdf + signalLine + histogram)
'''


def test_security_ta_tuple_materializes_program_members_for_udf_reads():
    cpp = transpile(_SECURITY_MACD_TUPLE)
    assert "auto [macdLine, signalLine, histogram] = _req_sec_0;" not in cpp
    temp_match = re.search(r"auto (_tuple_result_\d+) = _req_sec_0;", cpp)
    assert temp_match is not None
    temp = temp_match.group(1)
    for name, field in zip(
        ("macdLine", "signalLine", "histogram"),
        ("macd_line", "signal_line", "histogram"),
    ):
        assert f"{name} = {temp}.{field};" in cpp

    driver = r"""
#include <cmath>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bars[] = {
        Bar{1.0, 1.0, 1.0, 1.0, 1.0, 0},
        Bar{2.0, 2.0, 2.0, 2.0, 1.0, 60000},
        Bar{4.0, 4.0, 4.0, 4.0, 1.0, 120000},
        Bar{8.0, 8.0, 8.0, 8.0, 1.0, 180000},
    };
    strategy.run(bars, 4, "1", "1");
    std::cout << strategy.direct << " " << strategy.viaUdf << " "
              << strategy.macdLine << "\n";
    return 0;
}
"""
    values = tuple(
        float(value)
        for value in _compile_and_run(cpp + driver).split()
    )
    assert len(values) == 3
    assert all(math.isfinite(value) for value in values)
    assert not math.isclose(values[0], 0.0, abs_tol=1e-12)
    assert math.isclose(values[0], values[1], rel_tol=1e-9)
    assert math.isclose(values[0], values[2], rel_tol=1e-9)
