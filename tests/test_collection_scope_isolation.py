"""Lexical isolation for callable-local array/map/matrix TypeSpecs."""

from __future__ import annotations

from hashlib import sha256

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp


def _ordered_source(
    title: str,
    left: str,
    right: str,
    calls: str,
    reverse: bool,
) -> str:
    definitions = (right, left) if reverse else (left, right)
    return (
        f'//@version=6\nstrategy("{title}")\n'
        + "\n".join(definitions)
        + "\n"
        + calls
    )


def _body(cpp: str, signature: str) -> str:
    start = cpp.index(signature)
    end = cpp.index("\n    }", start) + len("\n    }")
    return cpp[start:end]


def _pair_source(family: str, reverse: bool) -> str:
    if family == "map-elements":
        return _ordered_source(
            "scope map elements",
            '''map_text() =>
    slot = map.new<string, string>()
    slot.put("k", "text")
    slot.remove("k")
''',
            '''map_float() =>
    slot = map.new<string, float>()
    slot.put("k", 2.5)
    slot.remove("k")
''',
            "text_result = map_text()\nfloat_result = map_float()\n",
            reverse,
        )
    if family == "array-elements":
        return _ordered_source(
            "scope array elements",
            '''array_text() =>
    slot = array.from("text")
    copied = slot.copy()
    copied.size()
''',
            '''array_int() =>
    slot = array.from(7)
    copied = slot.copy()
    copied.size()
''',
            "text_result = array_text()\nint_result = array_int()\n",
            reverse,
        )
    if family == "map-array":
        return _ordered_source(
            "scope map array",
            '''kind_map() =>
    slot = map.new<string, string>()
    slot.put("k", "v")
    slot.remove("k")
''',
            '''kind_array() =>
    slot = array.from(7)
    copied = slot.copy()
    copied.size()
''',
            "map_result = kind_map()\narray_result = kind_array()\n",
            reverse,
        )
    if family == "map-matrix":
        return _ordered_source(
            "scope map matrix",
            '''kind_map() =>
    slot = map.new<string, float>()
    slot.put("k", 2.5)
    slot.get("k")
''',
            '''kind_matrix() =>
    slot = matrix.new<int>(1, 1, 7)
    slot.get(0, 0)
''',
            "map_result = kind_map()\nmatrix_result = kind_matrix()\n",
            reverse,
        )
    if family == "matrix-elements":
        return _ordered_source(
            "scope matrix elements",
            '''matrix_int() =>
    slot = matrix.new<int>(1, 1, 7)
    slot.get(0, 0)
''',
            '''matrix_bool() =>
    slot = matrix.new<bool>(1, 1, true)
    slot.get(0, 0)
''',
            "int_result = matrix_int()\nbool_result = matrix_bool()\n",
            reverse,
        )
    raise AssertionError(f"unknown family: {family}")


_FAMILIES = (
    "map-elements",
    "array-elements",
    "map-array",
    "map-matrix",
    "matrix-elements",
)


@pytest.mark.parametrize("reverse", [False, True], ids=["left-first", "right-first"])
@pytest.mark.parametrize("family", _FAMILIES)
def test_callable_local_collection_specs_are_lexically_isolated(
    family: str, reverse: bool
) -> None:
    cpp = transpile(_pair_source(family, reverse))

    if family == "map-elements":
        text = _body(cpp, "std::string map_text(")
        numeric = _body(cpp, "double map_float(")
        assert "std::unordered_map<std::string, std::string> slot" in text
        assert 'return std::string("");' in text
        assert "std::unordered_map<std::string, double> slot" in numeric
        assert "return 0.0;" in numeric
    elif family == "array-elements":
        text = _body(cpp, "double array_text(")
        numeric = _body(cpp, "double array_int(")
        assert "std::vector<std::string> copied = std::vector<std::string>(slot);" in text
        assert "std::vector<int> copied = std::vector<int>(slot);" in numeric
    elif family == "map-array":
        mapped = _body(cpp, "std::string kind_map(")
        arrayed = _body(cpp, "double kind_array(")
        assert '.find(std::string("k"))' in mapped
        assert "std::vector<int> copied = std::vector<int>(slot);" in arrayed
        assert ".count(" not in arrayed
    elif family == "map-matrix":
        mapped = _body(cpp, "double kind_map(")
        matrixed = _body(cpp, "int kind_matrix(")
        assert 'slot.count(std::string("k"))' in mapped
        assert "PineGenericMatrix<int> slot" in matrixed
        assert "slot.get((int)(0), (int)(0))" in matrixed
        assert "slot.count(" not in matrixed
    else:
        ints = _body(cpp, "int matrix_int(")
        bools = _body(cpp, "bool matrix_bool(")
        assert "PineGenericMatrix<int> slot" in ints
        assert "PineGenericMatrix<bool> slot" in bools
        assert "#include <pineforge/generic_matrix.hpp>" in cpp


@pytest.mark.parametrize("reverse", [False, True], ids=["left-first", "right-first"])
@pytest.mark.parametrize("family", _FAMILIES)
def test_callable_local_collection_collision_pairs_compile(
    family: str, reverse: bool
) -> None:
    compile_cpp(
        transpile(_pair_source(family, reverse)),
        label=f"collection_scope_{family}_{int(reverse)}",
    )


def _global_local_source(reverse: bool) -> str:
    left = '''global_read() =>
    slot.put("k", "global")
    slot.remove("k")
'''
    right = '''local_copy() =>
    slot = array.from(7)
    copied = slot.copy()
    copied.size()
'''
    definitions = (right, left) if reverse else (left, right)
    return (
        '//@version=6\nstrategy("scope global local")\n'
        "var slot = map.new<string, string>()\n"
        + "\n".join(definitions)
        + "\nglobal_result = global_read()\nlocal_result = local_copy()\n"
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_callable_local_collection_shadows_same_named_global(reverse: bool) -> None:
    cpp = transpile(_global_local_source(reverse))
    assert "std::unordered_map<std::string, std::string> slot;" in cpp
    global_body = _body(cpp, "std::string global_read(")
    local_body = _body(cpp, "double local_copy(")
    assert '.find(std::string("k"))' in global_body
    assert "std::vector<int> slot" in local_body
    assert "std::vector<int> copied = std::vector<int>(slot);" in local_body
    assert ".count(" not in local_body
    compile_cpp(cpp, label=f"collection_scope_global_local_{int(reverse)}")


def _udf_method_source(reverse: bool) -> str:
    udf = '''plain_matrix() =>
    slot = matrix.new<int>(1, 1, 7)
    slot.get(0, 0)
'''
    method = '''method read_float(Holder self) =>
    slot = map.new<string, float>()
    slot.put("k", 2.5)
    slot.get("k")
'''
    definitions = (method, udf) if reverse else (udf, method)
    return (
        '//@version=6\nstrategy("scope udf method")\n'
        "type Holder\n    int seed\n"
        + "\n".join(definitions)
        + "\nvar Holder h = Holder.new(1)\n"
        "plain_result = plain_matrix()\nmethod_result = h.read_float()\n"
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_udf_and_udt_method_collection_locals_are_isolated(reverse: bool) -> None:
    cpp = transpile(_udf_method_source(reverse))
    matrixed = _body(cpp, "int plain_matrix(")
    mapped = _body(cpp, "double _udt_Holder_read_float(")
    assert "PineGenericMatrix<int> slot" in matrixed
    assert "slot.get((int)(0), (int)(0))" in matrixed
    assert "slot.count(" not in matrixed
    assert "std::unordered_map<std::string, double> slot" in mapped
    assert 'slot.count(std::string("k"))' in mapped
    compile_cpp(cpp, label=f"collection_scope_udf_method_{int(reverse)}")


_PERSISTENT_SOURCE = '''//@version=6
strategy("scope persistent collections")
array_state() =>
    var ints = array.from(1)
    ints.size()
map_state() =>
    var weights = map.new<string, int>()
    weights.put("k", 1)
    weights.size()
matrix_state() =>
    var grid = matrix.new<int>(1, 1, 7)
    grid.get(0, 0)
array_a = array_state()
array_b = array_state()
map_a = map_state()
map_b = map_state()
matrix_a = matrix_state()
matrix_b = matrix_state()
'''


def test_function_var_collection_members_and_clones_keep_exact_types() -> None:
    cpp = transpile(_PERSISTENT_SOURCE)
    for name in ("ints", "ints_cs1"):
        assert f"std::vector<int> {name};" in cpp
    for name in ("weights", "weights_cs1"):
        assert f"std::unordered_map<std::string, int> {name};" in cpp
    for name in ("grid", "grid_cs1"):
        assert f"PineGenericMatrix<int> {name};" in cpp
    compile_cpp(cpp, label="collection_scope_persistent_clones")


_SCALAR_SHADOW_SOURCE = '''//@version=6
strategy("scope scalar shadow")
var slot = map.new<string, string>()
local_scalar() =>
    slot = "local"
    slot
global_read() =>
    slot.put("k", "global")
    slot.remove("k")
local_result = local_scalar()
global_result = global_read()
'''


def test_scalar_local_does_not_erase_same_named_global_collection() -> None:
    cpp = transpile(_SCALAR_SHADOW_SOURCE)
    assert "std::unordered_map<std::string, std::string> slot;" in cpp
    assert "std::string local_scalar(" in cpp
    assert "std::string slot = std::string(\"local\")" in _body(
        cpp, "std::string local_scalar("
    )
    assert '.find(std::string("k"))' in _body(cpp, "std::string global_read(")
    compile_cpp(cpp, label="collection_scope_scalar_shadow")


_TOP_LEVEL_ALIAS_SOURCE = '''//@version=6
strategy("scope top-level alias")
var seed = array.from(1)
alias = seed
alias.push(2)
observed = alias.size()
'''


def test_top_level_collection_alias_keeps_live_registry_inference() -> None:
    cpp = transpile(_TOP_LEVEL_ALIAS_SOURCE)
    assert "std::vector<int> seed;" in cpp
    assert "std::vector<int> alias;" in cpp
    assert "alias = seed;" in cpp
    assert "alias.push_back(2);" in cpp
    compile_cpp(cpp, label="collection_scope_top_level_alias")


def _sibling_branch_source(reverse: bool, matrix: bool = False) -> str:
    if matrix:
        left = '''        slot = matrix.new<int>(1, 1, 7)
        slot.set(0, 0, 8)
'''
    else:
        left = '''        slot = array.from(1)
        slot.push(2)
'''
    right = '''        slot = map.new<string, int>()
        slot.put("k", 1)
'''
    first, second = (right, left) if reverse else (left, right)
    return f'''//@version=6
strategy("scope sibling branches")
mixed_branch(cond) =>
    if cond
{first}    else
{second}    0
result = mixed_branch(close > open)
'''


@pytest.mark.parametrize("reverse", [False, True], ids=["left-first", "right-first"])
@pytest.mark.parametrize("matrix", [False, True], ids=["array-map", "matrix-map"])
def test_sibling_blocks_keep_exact_collection_bindings(
    reverse: bool, matrix: bool
) -> None:
    cpp = transpile(_sibling_branch_source(reverse, matrix))
    body = _body(cpp, "int mixed_branch(")
    assert "std::unordered_map<std::string, int> slot" in body
    assert '(slot[std::string("k")] = 1)' in body
    if matrix:
        assert "PineGenericMatrix<int> slot" in body
        assert "slot.set((int)(0), (int)(0), 8)" in body
        assert "#include <pineforge/generic_matrix.hpp>" in cpp
    else:
        assert "std::vector<int> slot" in body
        assert "slot.push_back(2)" in body
    compile_cpp(
        cpp,
        label=f"collection_scope_sibling_{int(matrix)}_{int(reverse)}",
    )


_TEMPORAL_SOURCE = '''//@version=6
strategy("scope temporal activation")
var slot = map.new<string, string>()
temporal_array() =>
    slot.put("before", "global")
    slot.remove("before")
    slot = array.from(1)
    slot.push(2)
    slot.size()
temporal_matrix() =>
    slot.put("k", "v")
    slot = matrix.new<int>(slot.contains("k"), 1, 0)
    slot.get(0, 0)
array_result = temporal_array()
matrix_result = temporal_matrix()
'''


def test_callable_binding_activates_after_declaration_rhs() -> None:
    cpp = transpile(_TEMPORAL_SOURCE)
    arrayed = _body(cpp, "double temporal_array(")
    matrixed = _body(cpp, "int temporal_matrix(")
    assert '(slot[std::string("before")] = std::string("global"))' in arrayed
    assert '.find(std::string("before"))' in arrayed
    assert "std::vector<int> slot" in arrayed
    assert "slot.push_back(2)" in arrayed
    assert '(slot[std::string("k")] = std::string("v"))' in matrixed
    assert "auto& _pf_outer_slot_0 = this->slot" in matrixed
    assert '_pf_outer_slot_0.count(std::string("k"))' in matrixed
    assert "PineGenericMatrix<int> slot" in matrixed
    compile_cpp(cpp, label="collection_scope_temporal_activation")


_GLOBAL_REASSIGN_SOURCE = '''//@version=6
strategy("scope global reassignment")
var slot = map.new<string, string>()
method_reset() =>
    slot := map.new<string, string>()
    slot.put("method", "ok")
    slot.remove("method")
functional_reset() =>
    slot := map.new<string, string>()
    map.put(slot, "functional", "ok")
    map.remove(slot, "functional")
method_result = method_reset()
functional_result = functional_reset()
'''


def test_global_collection_reassignment_is_not_a_local_shadow() -> None:
    cpp = transpile(_GLOBAL_REASSIGN_SOURCE)
    method = _body(cpp, "std::string method_reset(")
    functional = _body(cpp, "std::string functional_reset(")
    assert "slot = std::unordered_map<std::string, std::string>()" in method
    assert '(slot[std::string("method")] = std::string("ok"))' in method
    assert '.find(std::string("method"))' in method
    assert '(slot[std::string("functional")] = std::string("ok"))' in functional
    assert '.find(std::string("functional"))' in functional
    compile_cpp(cpp, label="collection_scope_global_reassignment")


_NESTED_SHADOW_SOURCE = '''//@version=6
strategy("scope nested restore")
var shared = map.new<string, string>()
restore_local(cond) =>
    slot = array.from(1)
    if cond
        slot = map.new<string, int>()
        slot.put("k", 1)
    else
        slot = "scalar"
        copied = slot
    slot.push(2)
    slot.size()
restore_global(cond) =>
    if cond
        shared = "scalar"
        copied = shared
    else
        marker = 0
    shared.put("after", "global")
    shared.remove("after")
local_result = restore_local(close > open)
global_result = restore_global(close > open)
'''


def test_nested_scalar_and_collection_shadows_restore_outer_binding() -> None:
    cpp = transpile(_NESTED_SHADOW_SOURCE)
    local = _body(cpp, "double restore_local(")
    globally = _body(cpp, "std::string restore_global(")
    assert "std::unordered_map<std::string, int> slot" in local
    assert 'std::string slot = std::string("scalar")' in local
    assert "std::string copied = slot" in local
    assert "slot.push_back(2)" in local
    assert 'std::string shared = std::string("scalar")' in globally
    assert "std::string copied = shared" in globally
    assert '(shared[std::string("after")] = std::string("global"))' in globally
    assert '.find(std::string("after"))' in globally
    compile_cpp(cpp, label="collection_scope_nested_restore")


_BLOCK_PERSISTENT_SOURCE = '''//@version=6
strategy("scope block persistent")
array_state(cond) =>
    if cond
        var ints = array.from(1)
        ints.push(2)
    0
map_state(cond) =>
    if cond
        var weights = map.new<string, int>()
        weights.put("k", 1)
    0
matrix_state(cond) =>
    if cond
        var grid = matrix.new<int>(1, 1, 7)
        grid.set(0, 0, 8)
    0
array_a = array_state(true)
array_b = array_state(false)
map_a = map_state(true)
map_b = map_state(false)
matrix_a = matrix_state(true)
matrix_b = matrix_state(false)
'''


def test_block_scoped_persistent_collections_keep_exact_clone_types() -> None:
    cpp = transpile(_BLOCK_PERSISTENT_SOURCE)
    for name in ("ints", "ints_cs1"):
        assert f"std::vector<int> {name};" in cpp
    for name in ("weights", "weights_cs1"):
        assert f"std::unordered_map<std::string, int> {name};" in cpp
    for name in ("grid", "grid_cs1"):
        assert f"PineGenericMatrix<int> {name};" in cpp
    assert "#include <pineforge/generic_matrix.hpp>" in cpp
    compile_cpp(cpp, label="collection_scope_block_persistent")


_PERSISTENT_ALIAS_SOURCE = '''//@version=6
strategy("scope persistent alias initializer")
copy_state() =>
    var seed = array.from(1)
    var copied = seed.copy()
    copied.size()
first = copy_state()
second = copy_state()
'''


def test_persistent_collection_initializer_sees_prior_binding() -> None:
    cpp = transpile(_PERSISTENT_ALIAS_SOURCE)
    for name in ("seed", "copied", "seed_cs1", "copied_cs1"):
        assert f"std::vector<int> {name};" in cpp
    assert "copied = std::vector<int>(seed);" in cpp
    compile_cpp(cpp, label="collection_scope_persistent_alias_initializer")


_CALLABLE_DRAWING_COLLECTION_SOURCE = '''//@version=6
strategy("scope callable drawing collection")
direct_lines() =>
    xs = array.new<line>()
    xs.size()
block_lines(cond) =>
    if cond
        xs = array.new<line>()
        xs.size()
    0
direct_result = direct_lines()
block_result = block_lines(true)
'''


def test_callable_drawing_collection_specs_enable_runtime_header() -> None:
    cpp = transpile(_CALLABLE_DRAWING_COLLECTION_SOURCE)
    assert "#include <pineforge/drawing.hpp>" in cpp
    assert "std::vector<Line> xs" in _body(cpp, "double direct_lines(")
    assert "std::vector<Line> xs" in _body(cpp, "int block_lines(")
    compile_cpp(cpp, label="collection_scope_callable_drawing_header")


_PARAM_ALIAS_COLLISION_SOURCE = '''//@version=6
strategy("scope param alias collision")
var slot = map.new<string, string>()
param_alias(_pf_outer_slot_0) =>
    slot = matrix.new<int>(slot.contains("k"), 1, _pf_outer_slot_0)
    slot.get(0, 0)
result = param_alias(7)
'''


def test_temporal_outer_alias_avoids_callable_parameter_name() -> None:
    cpp = transpile(_PARAM_ALIAS_COLLISION_SOURCE)
    body = _body(cpp, "int param_alias(")
    assert "int _pf_outer_slot_0" in body
    assert "auto& _pf_outer_slot_1 = this->slot" in body
    assert '_pf_outer_slot_1.count(std::string("k"))' in body
    compile_cpp(cpp, label="collection_scope_param_alias_collision")


_NESTED_EXACT_ARRAY_SOURCE = '''//@version=6
strategy("scope nested exact array")
nested_exact(cond) =>
    slot = array.from(1)
    if cond
        slot = array.from(slot.get(0) + 1)
        slot.push(2)
    slot.size()
result = nested_exact(true)
'''


def test_nested_same_name_array_keeps_analyzer_element_spec() -> None:
    cpp = transpile(_NESTED_EXACT_ARRAY_SOURCE)
    body = _body(cpp, "double nested_exact(")
    assert "auto& _pf_outer_slot_0 = slot" in body
    assert "std::vector<int> slot = std::vector<int>" in body
    assert "std::vector<double> slot" not in body
    assert "slot.push_back(2)" in body
    compile_cpp(cpp, label="collection_scope_nested_exact_array")


_PARAM_BLOCK_SHADOW_SOURCE = '''//@version=6
strategy("scope parameter block shadow")
param_block(array<int> slot, bool cond) =>
    if cond
        slot = map.new<string, int>()
        slot.put("k", 1)
        slot.get("k")
    0
var param_arg = array.from(1)
result = param_block(param_arg, true)
'''


def test_block_local_collection_shadows_collection_parameter() -> None:
    cpp = transpile(_PARAM_BLOCK_SHADOW_SOURCE)
    body = _body(cpp, "int param_block(")
    assert "std::unordered_map<std::string, int> slot" in body
    assert '(slot[std::string("k")] = 1)' in body
    assert 'slot.count(std::string("k"))' in body
    compile_cpp(cpp, label="collection_scope_parameter_block_shadow")


_SECURITY_LINEAR_COLLECTION_SOURCE = '''//@version=6
strategy("scope security linear collection")
helper() =>
    xs = array.from(7)
    xs.get(0)
result = request.security(syminfo.tickerid, "D", helper())
'''


def test_security_linear_helper_activates_local_collection_binding() -> None:
    cpp = transpile(_SECURITY_LINEAR_COLLECTION_SOURCE)
    assert "std::vector<int> _sec0_helper_" in cpp
    assert "((_sec0_helper_" in cpp
    compile_cpp(cpp, label="collection_scope_security_linear_helper")


_TUPLE_COLLECTION_SOURCE = '''//@version=6
strategy("scope tuple collection")
tuple_collection() =>
    slot = array.from(1)
    copied = slot.copy()
    [copied, 1]
[arr, n] = tuple_collection()
'''


def test_tuple_return_prepass_uses_callable_collection_specs() -> None:
    cpp = transpile(_TUPLE_COLLECTION_SOURCE)
    assert "std::tuple<std::vector<int>, int> tuple_collection(" in cpp
    assert "std::tuple<double, int> tuple_collection(" not in cpp
    compile_cpp(cpp, label="collection_scope_tuple_return")


_METHOD_PERSISTENT_COLLECTION_SOURCE = '''//@version=6
strategy("scope method persistent collection")
type Holder
    int seed
method use(Holder self) =>
    var slot = array.from("x")
    slot.push("y")
    slot.size()
var Holder h = Holder.new(1)
result = h.use()
'''


def test_method_persistent_collection_member_uses_scoped_spec() -> None:
    cpp = transpile(_METHOD_PERSISTENT_COLLECTION_SOURCE)
    assert "std::vector<std::string> slot;" in cpp
    assert 'slot.push_back(std::string("y"))' in cpp
    compile_cpp(cpp, label="collection_scope_method_persistent")


_LOOP_PERSISTENT_COLLECTION_SOURCE = '''//@version=6
strategy("scope loop persistent collection")
loop_persistent() =>
    for i = 0 to 0
        var slot = array.from("x")
        slot.push("y")
        slot.size()
    0
result = loop_persistent()
'''


def test_loop_persistent_collection_member_uses_block_owner_spec() -> None:
    cpp = transpile(_LOOP_PERSISTENT_COLLECTION_SOURCE)
    assert "std::vector<std::string> slot;" in cpp
    assert 'slot.push_back(std::string("y"))' in cpp
    compile_cpp(cpp, label="collection_scope_loop_persistent")


_GLOBAL_SCALAR_LOCAL_COLLECTION_SOURCE = '''//@version=6
strategy("scope scalar persistent vs local")
var slot = close
helper() =>
    slot = array.from("x")
    slot.size()
result = helper()
'''


def test_nonpersistent_local_collection_cannot_retype_global_scalar_member() -> None:
    cpp = transpile(_GLOBAL_SCALAR_LOCAL_COLLECTION_SOURCE)
    assert "double slot = na<double>();" in cpp
    assert "std::vector<std::string> slot;" not in cpp
    assert 'std::vector<std::string> slot = std::vector<std::string>' in cpp
    assert "slot = current_bar_.close;" in cpp
    compile_cpp(cpp, label="collection_scope_global_scalar_local_collection")


_LOOP_SHADOW_SOURCE = '''//@version=6
strategy("scope loop shadow")
var slot = map.new<string, string>()
loop_shadow() =>
    slot.put("before", "global")
    for slot = 0 to 1
        local_copy = slot
    slot.put("after", "global")
    slot.remove("after")
result = loop_shadow()
'''


def test_loop_binder_shadows_collection_only_inside_loop() -> None:
    cpp = transpile(_LOOP_SHADOW_SOURCE)
    body = _body(cpp, "std::string loop_shadow(")
    assert "for (int slot = " in body
    # Keep general scalar lowering byte-compatible; exact loop TypeSpecs are
    # consumed only by collection inference/dispatch in this patch.
    assert "double local_copy = slot" in body
    assert '(slot[std::string("before")] = std::string("global"))' in body
    assert '(slot[std::string("after")] = std::string("global"))' in body
    assert '.find(std::string("after"))' in body
    compile_cpp(cpp, label="collection_scope_loop_shadow")


_LOOP_BINDER_EXACT_SOURCES = {
    "range-int": '''//@version=6
strategy("scope range binder exact")
var slot = array.from(4)
loop_binding() =>
    for i = 0 to 0
        slot = array.from(slot.get(0) + i)
        slot.size()
    0
result = loop_binding()
''',
    "for-in-int-direct": '''//@version=6
strategy("scope for-in int direct")
var values = array.from(1)
loop_binding() =>
    for value in values
        slot = array.from(value)
        slot.size()
    0
result = loop_binding()
''',
    "for-in-int-outer": '''//@version=6
strategy("scope for-in int outer")
var values = array.from(1)
var slot = array.from(4)
loop_binding() =>
    for value in values
        slot = array.from(slot.get(0) + value)
        slot.size()
    0
result = loop_binding()
''',
    "for-in-string": '''//@version=6
strategy("scope for-in string")
var values = array.from("x")
loop_binding() =>
    for value in values
        slot = array.from(value)
        slot.push("y")
    0
result = loop_binding()
''',
    "map-pair": '''//@version=6
strategy("scope map pair binder")
var pairs = map.new<string, int>()
var slot = array.from(4)
loop_binding() =>
    for [key, value] in pairs
        slot = array.from(slot.get(0) + value)
        slot.size()
    0
result = loop_binding()
''',
}


@pytest.mark.parametrize(
    "case", _LOOP_BINDER_EXACT_SOURCES, ids=_LOOP_BINDER_EXACT_SOURCES
)
def test_loop_binder_specs_match_array_from_declaration(case: str) -> None:
    cpp = transpile(_LOOP_BINDER_EXACT_SOURCES[case])
    if case == "for-in-string":
        assert (
            "std::vector<std::string> slot = "
            "std::vector<std::string>{value};"
        ) in cpp
        assert 'slot.push_back(std::string("y"))' in cpp
    else:
        assert "std::vector<int> slot = std::vector<int>" in cpp
    if case == "map-pair":
        assert "for (auto [key, value] : pairs)" in cpp
    compile_cpp(cpp, label=f"collection_scope_loop_binder_{case}")


_OUTER_ALIAS_COLLISION_SOURCE = '''//@version=6
strategy("scope outer alias collision")
var slot = array.from(4.0)
outer_subscript() =>
    _pf_outer_slot_0 = 99
    slot = array.from(slot[0])
    slot.size()
result = outer_subscript()
'''


def test_temporal_outer_alias_avoids_user_name_and_routes_subscript() -> None:
    cpp = transpile(_OUTER_ALIAS_COLLISION_SOURCE)
    body = _body(cpp, "double outer_subscript(")
    assert "int _pf_outer_slot_0 = 99" in body
    assert "auto& _pf_outer_slot_1 = this->slot" in body
    assert "std::vector<double>{_pf_outer_slot_1[0]}" in body
    compile_cpp(cpp, label="collection_scope_outer_alias_collision")


_IDENTITY_SOURCE = '''//@version=6
strategy("Collection scope identity")
map_probe() =>
    unique_map = map.new<string, float>()
    unique_map.put("k", 2.5)
    unique_map.size()
array_probe() =>
    unique_array = array.from(1, 2)
    unique_array.size()
matrix_probe() =>
    unique_matrix = matrix.new<float>(1, 1, 7.0)
    unique_matrix.get(0, 0)
map_result = map_probe()
array_result = array_probe()
matrix_result = matrix_probe()
'''


def test_unique_local_collection_output_stays_byte_identical() -> None:
    cpp = transpile(_IDENTITY_SOURCE)
    assert len(cpp) == 8892
    assert sha256(cpp.encode()).hexdigest() == (
        "b3cea2eab92d45874cfddb94f2a624e0d7c463cf2dbb3d27edbe06a0a5fbb8d0"
    )
