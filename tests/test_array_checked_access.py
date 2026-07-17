"""Pine v6 checked array access and negative-index regression coverage."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests import _compile as compile_env


def _generate(body: str) -> str:
    return transpile(f'//@version=6\nstrategy("T")\n{body}\n')


@pytest.mark.parametrize(
    "body",
    [
        "a = array.from(1, 2, 3)\nx = array.get(a, -1)",
        "a = array.from(1, 2, 3)\nx = a.get(-1)",
        "a = array.from(1, 2, 3)\nx = array.get(id=a, index=-1)",
        "a = array.from(1, 2, 3)\nx = a.get(index=-1)",
        "a = array.from(1, 2, 3)\narray.set(a, -2, 9)",
        "a = array.from(1, 2, 3)\na.set(-2, 9)",
        "a = array.from(1, 2, 3)\narray.set(id=a, index=-2, value=9)",
        "a = array.from(1, 2, 3)\na.set(index=-2, value=9)",
        "a = array.from(1, 2, 3)\narray.set(a, index=-2, value=9)",
        "a = array.from(1, 2, 3)\na.set(-2, value=9)",
        "a = array.from(1, 2, 3)\nx = array.remove(a, -3)",
        "a = array.from(1, 2, 3)\nx = a.remove(-3)",
        "a = array.from(1, 2, 3)\nx = array.remove(id=a, index=-3)",
        "a = array.from(1, 2, 3)\nx = a.remove(index=-3)",
        "a = array.from(1)\nx = array.first(a)",
        "a = array.from(1)\nx = array.first(id=a)",
        "a = array.from(1)\nx = a.last()",
        "a = array.from(1)\nx = array.last(id=a)",
        "a = array.from(1)\nx = array.pop(a)",
        "a = array.from(1)\nx = array.pop(id=a)",
        "a = array.from(1)\nx = a.shift()",
        "a = array.from(1)\nx = array.shift(id=a)",
    ],
)
def test_checked_methods_route_through_runtime_error_path(body: str):
    cpp = _generate(body)
    assert "pine_runtime_error" in cpp


def test_checked_get_binds_temporary_receiver_and_index_once():
    cpp = _generate(
        "values = array.from(10, 20, 30)\n"
        "idx() =>\n"
        "    -1\n"
        "x = array.get(array.slice(values, 0, 3), idx())"
    )
    assignment = next(
        line for line in cpp.splitlines() if line.startswith("        x =")
    )
    assert assignment.count("std::vector<int>(values.begin()") == 1
    assert assignment.count("idx()") == 1
    assert "pine_runtime_error" in assignment


@pytest.mark.parametrize(
    ("setup", "receiver"),
    [
        ("make_values() =>\n    array.from(10, 20, 30)\n", "make_values()"),
        ("", "array.from(10, 20, 30)"),
        ("m = matrix.new<float>(1, 3, 10)\n", "m.row(0)"),
        (
            "a = array.from(10, 20)\nb = array.from(30, 40)\n",
            "(close > open ? a : b)",
        ),
    ],
)
def test_checked_get_supports_temporary_method_receiver(setup: str, receiver: str):
    cpp = _generate(f"{setup}x = {receiver}.get(-1)")
    assignment = next(
        line for line in cpp.splitlines() if line.startswith("        x =")
    )
    assert "None(" not in assignment
    assert "pine_runtime_error" in assignment


def test_checked_set_emission_preserves_receiver_index_value_order():
    cpp = _generate(
        "values = array.from(10, 20, 30)\n"
        "recv() =>\n"
        "    array.copy(values)\n"
        "idx() =>\n"
        "    -1\n"
        "val() =>\n"
        "    99\n"
        "array.set(recv(), idx(), val())"
    )
    statement = next(
        line for line in cpp.splitlines() if "recv()" in line and "idx()" in line
    )
    assert statement.count("recv()") == 1
    assert statement.count("idx()") == 1
    assert statement.count("val()") == 1
    assert statement.index("[&](auto&& __pf_array)") < statement.index(
        "[&](auto&& __pf_raw_index_value)"
    ) < statement.index("[&](auto&& __pf_array_value)")
    # The calls close in reverse textual order because the outermost receiver
    # lambda executes first, then invokes the index lambda, which invokes the
    # value lambda.  The executable order probe below locks the semantics.
    assert statement.index("}((val()))") < statement.index("}((idx()))")
    assert statement.index("}((idx()))") < statement.index("}((recv()))")


def test_checked_index_rejects_na_nonfinite_and_range_before_integer_cast():
    cpp = _generate("a = array.from(1, 2, 3)\nx = a.get(close)")
    assignment = next(
        line for line in cpp.splitlines() if line.startswith("        x =")
    )
    integer_cast = assignment.index(
        "int64_t __pf_raw_index=(int64_t)__pf_raw_index_value"
    )
    assert assignment.index("is_na(__pf_raw_index_value)") < integer_cast
    assert assignment.index("std::isfinite(__pf_raw_index_value)") < integer_cast
    assert assignment.index("std::numeric_limits<int64_t>::min()") < integer_cast
    assert assignment.index("std::numeric_limits<int64_t>::max()") < integer_cast


def test_percentrank_checks_bounds_without_normalizing_negative_indices():
    cpp = _generate(
        "values = array.from(1.0, 2.0, 3.0)\n"
        "idx() =>\n"
        "    -1\n"
        "rank = array.percentrank(values, idx())"
    )
    assignment = next(
        line for line in cpp.splitlines() if line.startswith("        rank =")
    )
    assert assignment.count("idx()") == 1
    assert "[&](auto&& __pf_array)" in assignment
    assert "return [&](auto&& __pf_raw_index_value)" in assignment
    assert "__pf_array.size()<=1" in assignment
    assert "pine_runtime_error" in assignment
    assert "int64_t __pf_array_index=__pf_raw_index;" in assignment
    assert "__pf_raw_index<0?" not in assignment


def test_percentrank_keeps_temporary_receiver_before_index_once():
    cpp = _generate(
        "values = array.from(1.0, 2.0, 3.0)\n"
        "receiver() =>\n"
        "    array.copy(values)\n"
        "idx() =>\n"
        "    2\n"
        "rank = array.percentrank(receiver(), idx())"
    )
    assignment = next(
        line for line in cpp.splitlines() if line.startswith("        rank =")
    )
    assert assignment.count("receiver()") == 1
    assert assignment.count("idx()") == 1
    assert assignment.index("[&](auto&& __pf_array)") < assignment.index(
        "[&](auto&& __pf_raw_index_value)"
    )
    assert assignment.index("}((idx()))") < assignment.index("}((receiver()))")


@pytest.mark.parametrize(
    "access",
    ["array.get(pivots, i)", "array.first(pivots)", "pivots.last()"],
)
def test_checked_udt_lvalue_access_preserves_alias(access: str):
    cpp = _generate(
        "type Pivot\n"
        "    float level\n"
        "var array<Pivot> pivots = array.new<Pivot>()\n"
        "update(int i) =>\n"
        f"    Pivot p = {access}\n"
        "    p.level := close\n"
        "    0\n"
        "if array.size(pivots) == 0\n"
        "    array.push(pivots, Pivot.new(na))\n"
        "update(0)"
    )
    assert re.search(r"Pivot& p = .*pine_runtime_error", cpp)
    assert "Pivot p =" not in cpp


_VALID_NEGATIVE_SOURCE = """//@version=6
strategy("Checked array valid negative indices")
make_values() =>
    array.from(7, 8)
values = array.from(10, 20, 30)
get_last = values.get(-1)
array.set(values, -2, 99)
removed = array.remove(values, -3)
first_after = values.first()
last_after = array.last(values)
popped = values.pop()
array.push(values, 40)
shifted = array.shift(values)
remaining = values.get(0)
temporary = array.get(array.slice(values, 0, 1), -1)
keyword_function = array.get(id=values, index=-1)
keyword_method = values.get(index=-1)
temporary_method = make_values().get(-1)
keyword_values = array.from(1, 2, 3)
array.set(id=keyword_values, index=-1, value=77)
array.set(keyword_values, index=-2, value=66)
keyword_values.set(index=-3, value=55)
keyword_values.set(-1, value=88)
keyword_set_first = keyword_values.get(0)
keyword_set_middle = keyword_values.get(1)
keyword_set_last = keyword_values.get(2)
keyword_removed_function = array.remove(id=keyword_values, index=-2)
keyword_removed_method = keyword_values.remove(index=-1)
keyword_set_remaining = keyword_values.get(0)
"""


_ORDER_SOURCE = """//@version=6
strategy("Checked array evaluation order")
var values = array.from(10, 20, 30)
var order = array.new<int>()

receiver() =>
    array.push(order, 1)
    array.copy(values)

index() =>
    array.push(order, 2)
    -1

value() =>
    array.push(order, 3)
    99

array.set(receiver(), index(), value())
order_code = array.get(order, 0) * 100 + array.get(order, 1) * 10 + array.get(order, 2)
order_size = array.size(order)
"""


_ERROR_ORDER_SOURCE = """//@version=6
strategy("Checked array error evaluation order")
var values = array.from(10, 20, 30)
var order = array.new<int>()
var after = 0

receiver() =>
    array.push(order, 1)
    array.copy(values)

bad_index() =>
    array.push(order, 2)
    3

value() =>
    array.push(order, 3)
    99

array.set(receiver(), bad_index(), value())
after := 1
"""


_BOOL_ACCESS_SOURCE = """//@version=6
strategy("Checked bool array access")
values = array.from(true, false, true)
got = values.get(-1)
array.set(values, -2, true)
removed = array.remove(values, -3)
first_value = values.first()
last_value = values.last()
popped = values.pop()
array.push(values, false)
shifted = values.shift()
remaining = values.get(0)
"""


_ERROR_MATRIX_SOURCE = """//@version=6
strategy("Checked array error matrix")
local_na_get(array<int> source) =>
    int missing = na
    array.get(source, missing)
selector = close
values = array.from(1, 2, 3)
empty = array.new<int>(0)
int global_missing = na
sink = 0

if selector == 1
    sink := array.get(values, 3)
else if selector == 2
    sink := values.get(-4)
else if selector == 3
    array.set(values, 3, 9)
else if selector == 4
    values.set(-4, 9)
else if selector == 5
    sink := array.remove(values, 3)
else if selector == 6
    sink := values.remove(-4)
else if selector == 7
    sink := array.first(empty)
else if selector == 8
    sink := empty.last()
else if selector == 9
    sink := array.pop(empty)
else if selector == 10
    sink := empty.shift()
else if selector == 11
    sink := local_na_get(values)
else if selector == 12
    sink := array.get(values, global_missing)
else if selector == 13
    sink := array.get(values, math.pow(10, 400))
"""


_PERCENTRANK_BOUNDS_ERROR_SOURCE = """//@version=6
strategy("PercentRank bounds errors")
selector = close
values = array.from(1.0, 2.0, 3.0)
int missing = na
rank = 0.0

if selector == 1
    rank := array.percentrank(values, -1)
else if selector == 2
    rank := array.percentrank(values, 3)
else if selector == 3
    rank := array.percentrank(values, missing)
else if selector == 4
    rank := array.percentrank(values, math.pow(10, 400))
"""


_PERCENTRANK_VALID_AND_DEGENERATE_SOURCE = """//@version=6
strategy("PercentRank valid and degenerate indices")
values = array.from(1.0, 2.0, 3.0)
empty = array.new<float>(0)
singleton = array.from(7.0)
var calls = array.new<int>()

empty_index() =>
    array.push(calls, 1)
    999

singleton_index() =>
    array.push(calls, 2)
    -999

low = array.percentrank(values, 0)
high = array.percentrank(values, 2)
empty_rank = array.percentrank(empty, empty_index())
singleton_rank = array.percentrank(singleton, singleton_index())
call_count = array.size(calls)
call_code = array.get(calls, 0) * 10 + array.get(calls, 1)
"""


_PERCENTRANK_INTERNAL_NAME_SOURCE = """//@version=6
strategy("PercentRank internal-name collision")
__pf_array = array.from(1.0, 2.0, 3.0)
__pf_raw_index_value = 2
rank = array.percentrank(__pf_array, __pf_raw_index_value)
"""


_PERCENTRANK_ERROR_ORDER_SOURCE = """//@version=6
strategy("PercentRank error evaluation order")
var values = array.from(1.0, 2.0, 3.0)
var order = array.new<int>()
var after = 0

receiver() =>
    array.push(order, 1)
    array.copy(values)

bad_index() =>
    array.push(order, 2)
    -1

rank = array.percentrank(receiver(), bad_index())
after := 1
"""


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

    with tempfile.TemporaryDirectory(prefix="pineforge-array-access-") as tmp:
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
                "checked-array runtime probe failed to link\n"
                + "\n".join((built.stderr or built.stdout).splitlines()[:100])
            )
        ran = subprocess.run(
            [str(exe_path)], capture_output=True, text=True, timeout=30
        )
        if ran.returncode != 0:
            raise AssertionError(
                f"checked-array runtime probe exited {ran.returncode}\n"
                f"stdout:\n{ran.stdout}\nstderr:\n{ran.stderr}"
            )
        return ran.stdout


def test_percentrank_valid_and_degenerate_indices_runtime():
    driver = r"""
#include <cmath>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << "\n";
        return 2;
    }
    std::cout << strategy.low << " " << strategy.high << " "
              << std::isnan(strategy.empty_rank) << " "
              << std::isnan(strategy.singleton_rank) << " "
              << strategy.call_count << " " << strategy.call_code << "\n";
}
"""
    output = _compile_and_run(
        transpile(_PERCENTRANK_VALID_AND_DEGENERATE_SOURCE) + driver
    )
    assert tuple(int(float(value)) for value in output.split()) == (
        0,
        100,
        1,
        1,
        2,
        12,
    )


def test_percentrank_internal_names_compile_without_self_initialization():
    cpp = transpile(_PERCENTRANK_INTERNAL_NAME_SOURCE)
    assert "auto&& __pf_array=(__pf_array)" not in cpp
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
    std::cout << strategy.rank << "\n";
}
"""
    assert float(_compile_and_run(cpp + driver)) == 100.0


def test_percentrank_oob_indices_surface_deterministic_last_error():
    driver = r"""
#include <iostream>
int main() {
    for (int selector = 1; selector <= 4; ++selector) {
        GeneratedStrategy strategy;
        double value = static_cast<double>(selector);
        Bar bar{value, value, value, value, 1.0, selector};
        strategy.run(&bar, 1);
        std::cout << selector << "\t" << strategy.last_error() << "\n";
    }
}
"""
    output = _compile_and_run(transpile(_PERCENTRANK_BOUNDS_ERROR_SOURCE) + driver)
    assert output.splitlines() == [
        "1\tIndex -1 is out of bounds. Array size is 3",
        "2\tIndex 3 is out of bounds. Array size is 3",
        "3\tIndex na is out of bounds. Array size is 3",
        "4\tIndex inf is out of bounds. Array size is 3",
    ]


def test_percentrank_error_preserves_receiver_index_order_and_halts():
    driver = r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    std::cout << strategy.last_error() << "\n";
    for (auto value : strategy.order) std::cout << value;
    std::cout << " " << strategy.after << "\n";
}
"""
    output = _compile_and_run(
        transpile(_PERCENTRANK_ERROR_ORDER_SOURCE) + driver
    ).splitlines()
    assert output == ["Index -1 is out of bounds. Array size is 3", "12 0"]


@pytest.mark.parametrize(
    "access",
    [
        "array.get(array.from(Pivot.new(1), Pivot.new(2)), -1)",
        "array.first(array.from(Pivot.new(1), Pivot.new(2)))",
        "array.from(Pivot.new(1), Pivot.new(2)).last()",
    ],
)
def test_temporary_udt_access_returns_safe_value(access: str):
    source = f'''//@version=6
strategy("Temporary UDT checked access")
type Pivot
    float level
probe() =>
    Pivot p = {access}
    p.level := 7
    p.level
observed = probe()
'''
    cpp = transpile(source)
    assert "Pivot& p =" not in cpp
    assert re.search(r"Pivot p = .*pine_runtime_error", cpp)
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
    std::cout << strategy.observed << "\n";
}
"""
    assert float(_compile_and_run(cpp + driver)) == 7.0


def test_nested_array_udt_access_keeps_write_through_alias():
    source = '''//@version=6
strategy("Nested UDT checked access")
type Pivot
    float level
var inner = array.from(Pivot.new(1))
var outer = array.from(inner)
mutate() =>
    Pivot p = array.get(array.get(outer, 0), 0)
    p.level := 9
    0
mutate()
observed = array.get(array.get(outer, 0), 0).level
'''
    cpp = transpile(source)
    assert re.search(r"Pivot& p = .*pine_runtime_error", cpp)
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
    std::cout << strategy.observed << "\n";
}
"""
    assert float(_compile_and_run(cpp + driver)) == 9.0


def test_valid_negative_indices_and_end_operations_runtime():
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
    std::cout << strategy.get_last << " " << strategy.removed << " "
              << strategy.first_after << " " << strategy.last_after << " "
              << strategy.popped << " " << strategy.shifted << " "
              << strategy.remaining << " " << strategy.temporary << " "
              << strategy.keyword_function << " " << strategy.keyword_method << " "
              << strategy.temporary_method << " "
              << strategy.keyword_set_first << " "
              << strategy.keyword_set_middle << " "
              << strategy.keyword_set_last << " "
              << strategy.keyword_removed_function << " "
              << strategy.keyword_removed_method << " "
              << strategy.keyword_set_remaining << "\n";
}
"""
    output = _compile_and_run(transpile(_VALID_NEGATIVE_SOURCE) + driver)
    assert tuple(int(value) for value in output.split()) == (
        30,
        10,
        99,
        30,
        30,
        99,
        40,
        40,
        40,
        40,
        8,
        55,
        66,
        88,
        66,
        88,
        55,
    )


def test_receiver_index_value_runtime_order_and_one_evaluation():
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
    std::cout << strategy.order_code << " " << strategy.order_size << "\n";
}
"""
    output = _compile_and_run(transpile(_ORDER_SOURCE) + driver)
    order_code, order_size = (int(float(value)) for value in output.split())
    assert (order_code, order_size) == (123, 3)


def test_oob_set_evaluates_receiver_index_and_value_once_before_error():
    driver = r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    std::cout << strategy.last_error() << "\n";
    for (auto value : strategy.order) std::cout << value;
    std::cout << " " << strategy.after << "\n";
}
"""
    lines = _compile_and_run(transpile(_ERROR_ORDER_SOURCE) + driver).splitlines()
    assert lines == ["Index 3 is out of bounds. Array size is 3", "123 0"]


def test_bool_array_checked_operations_do_not_dangle_vector_bool_proxies():
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
    std::cout << strategy.got << " " << strategy.removed << " "
              << strategy.first_value << " " << strategy.last_value << " "
              << strategy.popped << " " << strategy.shifted << " "
              << strategy.remaining << "\n";
}
"""
    output = _compile_and_run(transpile(_BOOL_ACCESS_SOURCE) + driver)
    assert tuple(int(value) for value in output.split()) == (1, 1, 1, 1, 1, 1, 0)


def test_oob_and_empty_methods_surface_deterministic_last_error():
    driver = r"""
#include <iostream>
int main() {
    for (int selector = 1; selector <= 13; ++selector) {
        GeneratedStrategy strategy;
        double value = static_cast<double>(selector);
        Bar bar{value, value, value, value, 1.0, selector};
        strategy.run(&bar, 1);
        std::cout << selector << "\t" << strategy.last_error() << "\n";
    }
}
"""
    output = _compile_and_run(transpile(_ERROR_MATRIX_SOURCE) + driver)
    observed = {
        int(selector): message
        for selector, message in (
            line.split("\t", 1) for line in output.splitlines()
        )
    }
    positive = "Index 3 is out of bounds. Array size is 3"
    negative = "Index -4 is out of bounds. Array size is 3"
    assert observed == {
        1: positive,
        2: negative,
        3: positive,
        4: negative,
        5: positive,
        6: negative,
        7: "Cannot use first() if array is empty.",
        8: "Cannot use last() if array is empty.",
        9: "Cannot use pop() if array is empty.",
        10: "Cannot use shift() if array is empty.",
        11: "Index na is out of bounds. Array size is 3",
        12: "Index na is out of bounds. Array size is 3",
        13: "Index inf is out of bounds. Array size is 3",
    }
