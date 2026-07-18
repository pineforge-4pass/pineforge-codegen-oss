"""Atomic PineMap codegen, handle identity, and rollback regressions."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import subprocess
import tempfile

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests import _compile as compile_env


_HANDLE_SOURCE = '''//@version=6
strategy("PineMap handles")
identity(map<string, int> value) => value
mutate_then_rebind(map<string, int> value) =>
    value.put("caller", 10)
    value := map.new<string, int>()
    value.put("private", 20)
    value
local_alias_then_rebind(map<string, int> value) =>
    alias = value
    alias.put("local", 11)
    alias := map.new<string, int>()
    alias.put("local_private", 12)
    value.get("local")
var map<string, int> original = map.new<string, int>()
var map<string, int> alias = identity(original)
var map<string, int> rebound = mutate_then_rebind(alias)
var map<string, int> copied = original.copy()
var map<string, int> temporary = identity(map.new<string, int>())
local_seen = local_alias_then_rebind(original)
original.put("after_copy", 30)
var map<string, int> ordered = map.new<string, int>()
var map<string, int> merge_source = map.new<string, int>()
ordered.put("b", 2)
ordered.put("a", 1)
ordered.put("c", 3)
ordered.put("a", 10)
ordered.remove("b")
ordered.put("b", 20)
merge_source.put("a", 100)
merge_source.put("d", 4)
ordered.put_all(merge_source)
'''


_ORDER_SOURCE = '''//@version=6
strategy("PineMap evaluation order")
var order = array.new<int>()
var target = map.new<string, int>()
receiver() =>
    order.push(1)
    target
next_key() =>
    order.push(2)
    "k"
next_value() =>
    order.push(3)
    7
choose() =>
    order.push(1)
    true
p1 = receiver().put(next_key(), next_value())
p2 = map.put(receiver(), next_key(), next_value())
p3 = (choose() ? target : target).put(next_key(), next_value())
'''


_COOF_SOURCE = '''//@version=6
strategy("PineMap COOF", calc_on_order_fills=true)
type Holder
    map<string, int> data
    bool active
type Nested
    Holder inner
var map<string, int> root = map.new<string, int>()
var map<string, int> alias = root
var map<string, int> independent = root.copy()
var map<string, int> nullable = na
var Holder holder = Holder.new(root, true)
var Nested nested = Nested.new(Holder.new(root, true))
var array<Holder> holders = array.from(Holder.new(root, true))
var array<bool> flags = array.from(true, false)
if barstate.isfirst
    root.put("seed", 1)
'''


_NULL_SOURCE = '''//@version=6
strategy("PineMap null IDs")
local_null_roundtrip() =>
    var map<string, int> local = na
    started_null = na(local)
    local := map.new<string, int>()
    local.put("temporary", 1)
    local := na
    started_null and na(local)
var map<string, int> global_null = map.new<string, int>()
global_null.put("temporary", 1)
global_null := na
global_null_ok = na(global_null)
local_null_ok = local_null_roundtrip()
'''


_MAP_LOOP_SOURCE = '''//@version=6
strategy("PineMap pair loop")
var pairs = map.new<string, int>()
pairs.put("b", 2)
pairs.put("a", 1)
var string observed_keys = ""
var int observed_values = 0
for [key, value] in pairs
    observed_keys += key
    observed_values := observed_values * 10 + value
'''


def _find_engine_library() -> Path | None:
    explicit = os.environ.get("PINEFORGE_ENGINE_LIB")
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        return candidate if candidate.is_file() else None
    if compile_env._ENGINE_INC is None:
        return None
    candidates: list[Path] = []
    for pattern in ("build*/lib/libpineforge.a", "build*/lib/libpineforge.dylib"):
        candidates.extend(sorted(compile_env._ENGINE_INC.parent.glob(pattern)))
    return candidates[0].resolve() if candidates else None


def _compile_and_run(cpp_source: str, *, label: str) -> str:
    compile_env.skip_if_no_compile_env()
    engine_library = _find_engine_library()
    if engine_library is None:
        pytest.skip("built libpineforge not found; set PINEFORGE_ENGINE_LIB")
    assert compile_env._COMPILER is not None
    assert compile_env._ENGINE_INC is not None
    assert compile_env._EIGEN_INC is not None

    with tempfile.TemporaryDirectory(prefix=f"pineforge-{label}-") as tmp:
        source_path = Path(tmp) / "probe.cpp"
        executable = Path(tmp) / "probe"
        source_path.write_text(cpp_source)
        command = [
            compile_env._COMPILER,
            "-std=c++17",
            "-O0",
            "-I",
            str(compile_env._ENGINE_INC),
            "-I",
            str(compile_env._EIGEN_INC),
        ]
        if compile_env._GENERATED_INC is not None:
            command += ["-I", str(compile_env._GENERATED_INC)]
        command += [str(source_path), str(engine_library), "-pthread", "-o", str(executable)]
        built = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if built.returncode != 0:
            raise AssertionError(
                f"{label} failed to link\n"
                + "\n".join((built.stderr or built.stdout).splitlines()[:120])
            )
        ran = subprocess.run(
            [str(executable)], capture_output=True, text=True, timeout=30
        )
        if ran.returncode != 0:
            raise AssertionError(
                f"{label} exited {ran.returncode}\n"
                f"stdout:\n{ran.stdout}\nstderr:\n{ran.stderr}"
            )
        return ran.stdout


@pytest.mark.parametrize(
    ("source", "label"),
    [
        (_HANDLE_SOURCE, "pinemap_handles"),
        (_ORDER_SOURCE, "pinemap_order"),
        (_COOF_SOURCE, "pinemap_coof"),
        (_NULL_SOURCE, "pinemap_null"),
        (_MAP_LOOP_SOURCE, "pinemap_pair_loop"),
    ],
)
def test_pinemap_generated_sources_compile(source: str, label: str) -> None:
    compile_env.compile_cpp(transpile(source), label=label)


def test_pinemap_emission_uses_handle_identity_and_recursive_checkpoints() -> None:
    handles = transpile(_HANDLE_SOURCE)
    assert '#include <pineforge/map.hpp>' in handles
    assert "PineMap<std::string, int> identity(PineMap<std::string, int> value)" in handles
    assert "PineMap<std::string, int>& value" not in handles
    assert "PineMap<std::string, int>::new_()" in handles
    assert ".copy()" in handles
    assert "std::unordered_map<std::string, int>" not in handles

    checkpoint = transpile(_COOF_SOURCE)
    assert "Holder holder;" in checkpoint
    assert "PineMap<std::string, int> data" in checkpoint
    assert "_PFCheckpointTraits<PineMap<_PFKey, _PFValue>>" in checkpoint
    assert "std::optional<typename map_type::Snapshot>" in checkpoint
    assert "struct _PFCheckpointTraits<Holder>" in checkpoint
    assert "decltype(Holder::__pf_na)" in checkpoint
    assert "struct _PFCheckpointTraits<Nested>" in checkpoint
    assert "_PFCheckpointTraits<std::vector<_PFElement, _PFAllocator>>" in checkpoint


def test_non_map_cpp_remains_exact_baseline_bytes() -> None:
    source = '''//@version=6
strategy("No map checkpoint", calc_on_order_fills=true)
var float scalar = 1.0
scalar += close
observed = scalar
'''
    cpp = transpile(source)
    # Directly captured from clean base commit 741465c before this integration.
    assert sha256(cpp.encode()).hexdigest() == (
        "a2dda39086b904f0bd526a9696a7135e2526254d42667ae1e58354303d453fa6"
    )
    assert '#include <pineforge/map.hpp>' not in cpp
    assert "_PFCheckpointTraits" not in cpp


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            '''//@version=6
strategy("map history")
var map<string, int> values = map.new<string, int>()
previous = values[1]
''',
            "History references on map IDs",
        ),
        (
            '''//@version=6
strategy("map parameter history")
previous(map<string, int> values) => values[1]
observed = previous(map.new<string, int>())
''',
            "History references on map IDs",
        ),
        (
            '''//@version=6
strategy("inferred map parameter history")
previous(values) => values[1]
observed = previous(map.new<string, int>())
''',
            "History references on map IDs",
        ),
        (
            '''//@version=6
strategy("inferred map-bearing UDT parameter history")
type Holder
    map<string, int> values
previous(holder) => holder[1]
var Holder holder = Holder.new(map.new<string, int>())
observed = previous(holder)
''',
            "History references on map-bearing UDTs",
        ),
        (
            '''//@version=6
strategy("map alias history")
var root = map.new<string, int>()
alias = root
previous = alias[1]
''',
            "History references on map IDs",
        ),
        (
            '''//@version=6
strategy("map ternary history")
var left = map.new<string, int>()
var right = map.new<string, int>()
condition = true
previous = (condition ? left : right)[1]
''',
            "History references on map IDs",
        ),
        (
            '''//@version=6
strategy("map current history")
var root = map.new<string, int>()
current = root[0]
''',
            "History references on map IDs",
        ),
        (
            '''//@version=6
strategy("map UDT history")
type Holder
    map<string, int> values
var Holder holder = Holder.new(map.new<string, int>())
previous = holder[1]
''',
            "History references on map-bearing UDTs",
        ),
        (
            '''//@version=6
strategy("map UDT matrix")
type Holder
    map<string, int> values
var matrix<Holder> holders = matrix.new<Holder>(1, 1, Holder.new(map.new<string, int>()))
''',
            "matrix<Holder> is not supported when the UDT contains a map field",
        ),
        (
            '''//@version=6
strategy("UDT map value")
type Holder
    int value
var map<string, Holder> values = map.new<string, Holder>()
''',
            "map values must be primitive",
        ),
    ],
)
def test_unsupported_shallow_checkpoint_shapes_fail_closed(
    source: str, message: str
) -> None:
    with pytest.raises(CompileError, match=message):
        transpile(source)


def test_scalar_parameter_lexically_shadows_same_named_global_map() -> None:
    source = '''//@version=6
strategy("map history lexical shadow")
var map<string, int> values = map.new<string, int>()
previous(float values) => values[1]
observed = previous(close)
'''
    cpp = transpile(source)
    assert "double previous_cs0(const Series<double>& values)" in cpp
    compile_env.compile_cpp(cpp, label="pinemap-history-scalar-shadow")


@pytest.mark.parametrize(
    "body",
    [
        '''probe() =>
    float slot = close
    slot[1]
''',
        '''probe() =>
    if close > 0
        float slot = close
        previous = slot[1]
    0.0
''',
        '''probe() =>
    for slot = 0 to 1
        previous = slot[1]
    0.0
''',
        '''probe() =>
    for slot in array.from(0, 1)
        previous = slot[1]
    0.0
''',
    ],
    ids=["local", "block-local", "range-loop-binder", "for-in-binder"],
)
def test_scalar_bindings_shadowing_map_history_fail_closed(body: str) -> None:
    source = f'''//@version=6
strategy("map history scalar binding shadow")
var slot = map.new<string, int>()
{body}observed = probe()
'''
    with pytest.raises(
        CompileError,
        match="scalar local or loop bindings that shadow a map ID",
    ):
        transpile(source)


def test_handle_alias_copy_rebind_and_order_runtime() -> None:
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.original.get("caller") != 10) return 3;
    if (strategy.original.get("local") != 11) return 4;
    if (strategy.original.get("after_copy") != 30) return 5;
    if (strategy.alias.get("after_copy") != 30) return 6;
    if (strategy.rebound.get("private") != 20) return 7;
    if (strategy.rebound.contains("caller")) return 8;
    if (strategy.copied.get("caller") != 10) return 9;
    if (strategy.copied.contains("after_copy")) return 10;
    if (strategy.temporary.size() != 0) return 11;
    if (strategy.local_seen != 11) return 12;
    const std::vector<std::string> expected_keys{"a", "c", "b", "d"};
    const std::vector<int> expected_values{100, 3, 20, 4};
    if (strategy.ordered.keys() != expected_keys) return 13;
    if (strategy.ordered.values() != expected_values) return 14;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        transpile(_HANDLE_SOURCE) + driver, label="pinemap-handle-runtime"
    ) == "ok\n"


def test_pine_level_null_declaration_and_rebind_runtime() -> None:
    cpp = transpile(_NULL_SOURCE)
    assert "PineMap<std::string, int>{}" in cpp
    assert "na<double>()" not in next(
        line for line in cpp.splitlines() if "global_null =" in line
    )
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (!strategy.global_null_ok || !strategy.local_null_ok) return 3;
    if (!strategy.global_null.is_na()) return 4;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        cpp + driver, label="pinemap-null-runtime"
    ) == "ok\n"


def test_receiver_key_value_evaluation_order_runtime() -> None:
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    const std::vector<int> expected{1, 2, 3, 1, 2, 3, 1, 2, 3};
    if (strategy.order != expected) return 3;
    if (!is_na(strategy.p1)) return 4;
    if (strategy.p2 != 7 || strategy.p3 != 7) return 5;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        transpile(_ORDER_SOURCE) + driver, label="pinemap-order-runtime"
    ) == "ok\n"


def test_map_pair_loop_uses_insertion_order_and_public_api_runtime() -> None:
    cpp = transpile(_MAP_LOOP_SOURCE)
    assert "auto __pf_map_iter_" in cpp
    assert ".keys())" in cpp
    assert ".get(key)" in cpp
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.observed_keys != "ba") return 3;
    if (strategy.observed_values != 21) return 4;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        cpp + driver, label="pinemap-pair-loop-runtime"
    ) == "ok\n"


def test_map_pair_loop_temporary_avoids_user_binding_collision() -> None:
    source = '''//@version=6
strategy("PineMap pair loop collision")
var int __pf_map_iter_0 = 99
var pairs = map.new<string, int>()
pairs.put("a", 1)
for [key, value] in pairs
    observed = value
'''
    cpp = transpile(source)
    assert "auto __pf_map_iter_1 = pairs;" in cpp
    assert "auto __pf_map_iter_0 = pairs;" not in cpp
    compile_env.compile_cpp(cpp, label="pinemap-pair-loop-collision")


@pytest.mark.parametrize(
    ("as_method", "parameter", "key_name"),
    [
        (False, "__pf_map_iter_0", "key"),
        (False, "__pf_map_key_0", "_"),
        (True, "__pf_map_iter_0", "key"),
        (True, "__pf_map_key_0", "_"),
    ],
    ids=["udf-iter", "udf-key", "method-iter", "method-key"],
)
def test_map_pair_loop_temporary_avoids_parameter_collisions(
    as_method: bool, parameter: str, key_name: str
) -> None:
    if as_method:
        prelude = '''type Accumulator
    int seed
'''
        declaration = (
            "method probe(Accumulator self, int "
            f"{parameter}, map<string, int> pairs) =>"
        )
        setup = '''var Accumulator accumulator = Accumulator.new(0)
observed = accumulator.probe(10, pairs)
'''
    else:
        prelude = ""
        declaration = (
            f"probe(int {parameter}, map<string, int> pairs) =>"
        )
        setup = "observed = probe(10, pairs)\n"
    source = f'''//@version=6
strategy("PineMap pair loop parameter collision")
{prelude}{declaration}
    total = {parameter}
    for [{key_name}, value] in pairs
        total += value
    total + {parameter}
var pairs = map.new<string, int>()
pairs.put("a", 1)
pairs.put("b", 2)
{setup}'''
    cpp = transpile(source)
    if parameter == "__pf_map_iter_0":
        assert "auto __pf_map_iter_0 = pairs;" not in cpp
    else:
        assert "for (auto __pf_map_key_0 :" not in cpp
    assert f"return (total + {parameter});" in cpp
    compile_env.compile_cpp(
        cpp,
        label=(
            "pinemap-pair-loop-method-param-collision"
            if as_method
            else "pinemap-pair-loop-udf-param-collision"
        ),
    )

    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.observed != 23) return 3;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        cpp + driver,
        label=(
            "pinemap-pair-loop-method-param-runtime"
            if as_method
            else "pinemap-pair-loop-udf-param-runtime"
        ),
    ) == "ok\n"


def test_coof_recursive_snapshot_restore_runtime() -> None:
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.root.get("seed") != 1) return 3;
    if (!strategy.nullable.is_na()) return 4;
    strategy.snapshot_script_state();

    strategy.root.put("after", 2);
    strategy.alias = PineMap<std::string, int>::new_();
    strategy.alias.put("wrong", 9);
    strategy.holder.data = PineMap<std::string, int>::new_();
    strategy.nested.inner.data = PineMap<std::string, int>::new_();
    strategy.holders[0].data = PineMap<std::string, int>::new_();
    strategy.holders.push_back(Holder{PineMap<std::string, int>::new_(), false, false});
    strategy.flags[0] = false;
    strategy.flags.push_back(true);
    strategy.holder.active = false;
    strategy.holder.__pf_na = true;
    strategy.nested.__pf_na = true;
    strategy.nested.inner.__pf_na = true;
    strategy.independent.put("copy_after", 3);
    strategy.nullable = PineMap<std::string, int>::new_();
    strategy.nullable.put("not_null", 4);

    strategy.restore_script_state();
    if (strategy.root.contains("after")) return 5;
    if (strategy.root.get("seed") != 1) return 6;
    if (strategy.alias.get("seed") != 1) return 7;
    if (strategy.holder.data.get("seed") != 1) return 8;
    if (strategy.nested.inner.data.get("seed") != 1) return 9;
    if (strategy.holders.size() != 1) return 10;
    if (strategy.holders[0].data.get("seed") != 1) return 11;
    if (!strategy.holder.active || strategy.holder.__pf_na) return 12;
    if (strategy.nested.__pf_na || strategy.nested.inner.__pf_na) return 13;
    if (strategy.independent.contains("copy_after")) return 14;
    if (!strategy.nullable.is_na()) return 15;
    if (strategy.flags.size() != 2 || !strategy.flags[0] || strategy.flags[1]) return 23;

    strategy.holder.data.put("shared", 5);
    if (strategy.root.get("shared") != 5) return 16;
    if (strategy.alias.get("shared") != 5) return 17;
    if (strategy.nested.inner.data.get("shared") != 5) return 18;
    if (strategy.holders[0].data.get("shared") != 5) return 19;

    strategy.restore_script_state();
    if (strategy.root.contains("shared")) return 20;
    if (strategy.root.get("seed") != 1) return 21;
    if (!strategy.nullable.is_na()) return 22;
    std::cout << "ok\n";
}
'''
    assert _compile_and_run(
        transpile(_COOF_SOURCE) + driver, label="pinemap-coof-runtime"
    ) == "ok\n"
