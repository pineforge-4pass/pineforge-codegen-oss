"""Typed-map UDF parameter method dispatch regression coverage."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from pineforge_codegen.errors import CompileError
from pineforge_codegen import transpile
from tests import _compile as compile_env


_SOURCE = """//@version=6
strategy("Typed map parameter methods")
var order = array.new<int>()
var array<float> shadow_array = array.from(99.0)
var map<string, float> shadow_map = map.new<string, float>()
var matrix<float> shadow_matrix = matrix.new<float>(1, 1, 99.0)

next_key() =>
    order.push(1)
    "ordered"

next_value() =>
    order.push(2)
    7

read_key() =>
    order.push(3)
    "ordered"

mutate_int(map<string, int> target) =>
    target.put(value=next_value(), key=next_key())
    target.get(key=read_key())

mutate_float(map<string, float> target) =>
    target.put("float", 2.5)
    target.get("float")

probe_bool(map<string, bool> target) =>
    target.put(key="bool", value=true)
    target.contains(key="bool") and target.get(key="bool")

probe_string(map<string, string> target) =>
    target.put(value="ok", key="string")
    target.get(key="string") == "ok"

missing_float(map<string, float> target) =>
    target.get(key="missing") == 0.0

missing_int(map<string, int> target) =>
    target.get(key="missing") == 0

missing_bool(map<string, bool> target) =>
    not target.get(key="missing")

missing_string(map<string, string> target) =>
    target.get(key="missing") == ""

inspect_int(map<string, int> target) =>
    array<string> key_list = target.keys()
    array<int> value_list = target.values()
    map<string, int> copied = target.copy()
    key_list.size() + value_list.size() + copied.size() + target.size()

remove_int(map<string, int> target) =>
    target.remove(key="ordered")

clear_int(map<string, int> target) =>
    target.clear()
    target.size()

merge_int(map<string, int> target, map<string, int> source) =>
    target.put_all(id2=source)
    target.get(key="other")

shadow_array_probe(map<string, int> shadow_array) =>
    shadow_array.put(key="a", value=11)
    shadow_array.get(key="a")

shadow_map_probe(map<string, string> shadow_map) =>
    shadow_map.put(key="m", value="map")
    shadow_map.get(key="m") == "map"

shadow_matrix_probe(map<string, bool> shadow_matrix) =>
    shadow_matrix.put(key="mx", value=true)
    shadow_matrix.get(key="mx")

int_values = map.new<string, int>()
float_values = map.new<string, float>()
bool_values = map.new<string, bool>()
string_values = map.new<string, string>()
empty_float_values = map.new<string, float>()
empty_int_values = map.new<string, int>()
empty_bool_values = map.new<string, bool>()
empty_string_values = map.new<string, string>()
clear_values = map.new<string, int>()
merge_target = map.new<string, int>()
merge_source = map.new<string, int>()
array_shadow_values = map.new<string, int>()
map_shadow_values = map.new<string, string>()
matrix_shadow_values = map.new<string, bool>()
map.put(clear_values, "clear", 1)
map.put(merge_source, "other", 8)
int_result = mutate_int(int_values)
int_size_after_put = map.size(int_values)
inspect_result = inspect_int(int_values)
float_result = mutate_float(float_values)
bool_result = probe_bool(bool_values)
string_result = probe_string(string_values)
missing_float_result = missing_float(empty_float_values)
missing_int_result = missing_int(empty_int_values)
missing_bool_result = missing_bool(empty_bool_values)
missing_string_result = missing_string(empty_string_values)
merge_result = merge_int(merge_target, merge_source)
merge_caller_size = map.size(merge_target)
shadow_array_result = shadow_array_probe(array_shadow_values)
shadow_map_result = shadow_map_probe(map_shadow_values)
shadow_matrix_result = shadow_matrix_probe(matrix_shadow_values)
removed_result = remove_int(int_values)
int_size_after_remove = map.size(int_values)
clear_result = clear_int(clear_values)
clear_caller_size = map.size(clear_values)
order_code = order.get(0) * 100 + order.get(1) * 10 + order.get(2)
order_size = order.size()
"""


def _function_body(cpp: str, signature: str) -> str:
    start = cpp.index(signature)
    end = cpp.index("\n    }", start) + len("\n    }")
    return cpp[start:end]


def test_typed_map_parameter_methods_route_by_typespec_and_keywords():
    cpp = transpile(_SOURCE)

    assert "double mutate_int(PineMap<std::string, int> target)" in cpp
    assert "double mutate_float(PineMap<std::string, double> target)" in cpp
    assert "bool probe_bool(PineMap<std::string, bool> target)" in cpp
    assert (
        "bool probe_string(PineMap<std::string, std::string> target)"
        in cpp
    )

    for raw_call in (
        "target.put(",
        "target.get(",
        "target.contains(",
        "target.remove(",
        "target.put_all(",
        "shadow_array.put(",
        "shadow_array.get(",
        "shadow_map.put(",
        "shadow_map.get(",
        "shadow_matrix.put(",
        "shadow_matrix.get(",
    ):
        assert raw_call not in cpp

    # Reversed keyword spelling still binds key before value. Both expressions
    # execute once, and get's duplicated template key is also bound once.
    put_line = next(
        line for line in cpp.splitlines()
        if "next_key()" in line and "next_value()" in line
    )
    assert put_line.count("next_key()") == 1
    assert put_line.count("next_value()") == 1
    assert put_line.index("__pf_map_param_arg_0") < put_line.index(
        "__pf_map_param_arg_1"
    )
    assert put_line.index("}((next_value()))") < put_line.index(
        "}((next_key()))"
    )
    get_line = next(
        line for line in cpp.splitlines()
        if "read_key()" in line and "__pf_map_param_arg" in line
    )
    assert get_line.count("read_key()") == 1

    # Missing values are supplied by PineMap's typed Pine-na runtime contract.
    for signature in (
        "bool missing_float(", "bool missing_int(", "bool missing_bool(",
        "bool missing_string(",
    ):
        body = _function_body(cpp, signature)
        assert ".get(" in body
        assert ".count(" not in body

    # Zero-argument methods and typed collection results route through the
    # helper without synthetic arguments.
    inspect = _function_body(cpp, "double inspect_int(")
    assert ".keys()" in inspect
    assert ".values()" in inspect
    assert "PineMap<std::string, int> copied" in inspect
    assert ".copy()" in inspect
    assert ".size()" in inspect
    assert ".clear()" in _function_body(cpp, "double clear_int(")

    # Parameter TypeSpecs win over same-named global array/map/matrix entries.
    assert ".get(" in _function_body(cpp, "double shadow_array_probe(")
    assert ".get(" in _function_body(cpp, "bool shadow_map_probe(")
    assert ".get(" in _function_body(cpp, "double shadow_matrix_probe(")

    merge = _function_body(cpp, "double merge_int(")
    assert ".put_all(__pf_map_param_arg_" in merge
    assert merge.count("}((source))") == 1


def test_udt_valued_map_parameter_remains_outside_supported_subset():
    source = '''//@version=6
strategy("T")
type Pivot
    float level
probe(map<string, Pivot> values) =>
    values.get("x").level
values = map.new<string, Pivot>()
observed = probe(values)
'''
    with pytest.raises(CompileError, match="map values must be primitive"):
        transpile(source)


def test_typed_map_parameter_methods_compile():
    compile_env.compile_cpp(transpile(_SOURCE), label="typed_map_parameter_methods")


def _find_engine_library() -> Path | None:
    explicit = os.environ.get("PINEFORGE_ENGINE_LIB")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if path.is_file() else None
    if compile_env._ENGINE_INC is None:
        return None
    candidates: list[Path] = []
    for pattern in ("build*/lib/libpineforge.a", "build*/lib/libpineforge.dylib"):
        candidates.extend(sorted(compile_env._ENGINE_INC.parent.glob(pattern)))
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

    with tempfile.TemporaryDirectory(prefix="pineforge-map-param-") as tmp:
        cpp_path = Path(tmp) / "probe.cpp"
        exe_path = Path(tmp) / "probe"
        cpp_path.write_text(cpp_source)
        command = [
            compiler,
            "-std=c++17",
            "-O0",
            "-I",
            str(engine_inc),
            "-I",
            str(eigen_inc),
        ]
        if compile_env._GENERATED_INC is not None:
            command += ["-I", str(compile_env._GENERATED_INC)]
        command += [str(cpp_path), str(engine_lib), "-pthread", "-o", str(exe_path)]
        built = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if built.returncode != 0:
            raise AssertionError(
                "typed-map runtime probe failed to link\n"
                + "\n".join((built.stderr or built.stdout).splitlines()[:100])
            )
        ran = subprocess.run(
            [str(exe_path)], capture_output=True, text=True, timeout=30
        )
        if ran.returncode != 0:
            raise AssertionError(
                f"typed-map runtime probe exited {ran.returncode}\n"
                f"stdout:\n{ran.stdout}\nstderr:\n{ran.stderr}"
            )
        return ran.stdout


def test_typed_map_parameter_methods_preserve_runtime_aliases_and_order():
    driver = r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << "\n";
        return 2;
    }
    std::cout << strategy.int_result << " "
              << strategy.int_size_after_put << " "
              << strategy.inspect_result << " "
              << strategy.float_result << " "
              << strategy.bool_result << " "
              << strategy.string_result << " "
              << strategy.missing_float_result << " "
              << strategy.missing_int_result << " "
              << strategy.missing_bool_result << " "
              << strategy.missing_string_result << " "
              << strategy.merge_result << " "
              << strategy.merge_caller_size << " "
              << strategy.shadow_array_result << " "
              << strategy.shadow_map_result << " "
              << strategy.shadow_matrix_result << " "
              << strategy.removed_result << " "
              << strategy.int_size_after_remove << " "
              << strategy.clear_result << " "
              << strategy.clear_caller_size << " "
              << strategy.order_code << " "
              << strategy.order_size << "\n";
}
"""
    output = _compile_and_run(transpile(_SOURCE) + driver)
    assert tuple(float(value) for value in output.split()) == (
        7.0,
        1.0,
        4.0,
        2.5,
        1.0,
        1.0,
        0.0,
        0.0,
        1.0,
        1.0,
        8.0,
        1.0,
        11.0,
        1.0,
        1.0,
        7.0,
        0.0,
        0.0,
        0.0,
        123.0,
        3.0,
    )
