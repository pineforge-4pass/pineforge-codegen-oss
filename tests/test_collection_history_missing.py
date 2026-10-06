"""Insufficient collection/object history yields na IDs, never unchecked reads."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.collection_history import NA_ARRAY_MESSAGE, NA_MATRIX_MESSAGE
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests._e2e import Build, chart_feed_head, execute_all, ok, skip_unless_e2e_env, trade_count


HEADER = '//@version=6\nstrategy("missing history", overlay=true)\n'
ORIGINAL = HEADER + """var a = array.new<float>()
b = a[1]
if bar_index == 0 and not na(b)
    strategy.entry("L", strategy.long)
if bar_index == 20
    strategy.close_all()
"""


def witness(body: str) -> str:
    return HEADER + body + """
if bar_index == 3 and valid
    strategy.entry("L", strategy.long)
if bar_index == 5
    strategy.close_all()
// @pf-trace valid=valid
"""


SUCCESS = {
    "probe": (ORIGINAL, 0),
    "empty_array": (witness("""var a = array.new<float>()
b = a[1]
valid = bar_index == 0 ? na(b) : not na(b) and b.size() == 0
"""), 1),
    "array_offsets_aliases": (witness("""a = array.from(float(bar_index))
b = a[1]
c = b
far = a[bar_index + 10]
fixed = a[20]
current = a[0]
valid = na(far) and (bar_index < 20 ? na(fixed) : not na(fixed) and fixed.get(0) == bar_index - 20) and not na(current) and (bar_index == 0 ? na(b) and na(c) : not na(b) and array.get(c, 0) == bar_index - 1 and b.size() == 1)
"""), 1),
    "array_assignment": (witness("""a = array.from(float(bar_index))
b = array.new<float>()
b := a[1]
valid = bar_index == 0 ? na(b) : not na(b) and b.get(0) == bar_index - 1
"""), 1),
    "array_block": (witness("""a = array.from(float(bar_index))
bool valid = false
if bar_index >= 0
    b = a[1]
    valid := bar_index == 0 ? na(b) : not na(b) and b.get(0) == bar_index - 1
"""), 1),
    "array_var": (witness("""a = array.from(float(bar_index))
var b = a[1]
valid = na(b)
"""), 1),
    "array_alias_history": (witness("""a = array.from(float(bar_index))
b = a[1]
c = b[1]
valid = bar_index < 2 ? na(c) : not na(c) and c.get(0) == bar_index - 2
"""), 1),
    "matrix_float": (witness("""a = matrix.new<float>(1, 1, float(bar_index))
b = a[1]
far = a[bar_index + 10]
valid = na(far) and (bar_index == 0 ? na(b) : not na(b) and b.get(0, 0) == bar_index - 1)
"""), 1),
    "matrix_int": (witness("""a = matrix.new<int>(1, 1, bar_index)
b = a[1]
far = a[100]
valid = na(far) and (bar_index == 0 ? na(b) : not na(b) and matrix.get(b, 0, 0) == bar_index - 1)
"""), 1),
    "udt": (witness("""type Cell
    int value
a = Cell.new(bar_index)
b = a[1]
far = a[bar_index + 10]
valid = na(far) and (bar_index == 0 ? na(b) : not na(b) and b.value == bar_index - 1)
"""), 1),
    "drawing": (witness("""a = line.new(bar_index, close, bar_index + 1, close)
b = a[1]
far = a[100]
valid = na(far) and (bar_index == 0 ? na(b) : not na(b) and b.get_x1() == bar_index - 1)
"""), 1),
    "legacy_render": (witness("""var a = array.new<float>()
s = str.tostring(a[20])
valid = s == "NaN"
"""), 1),
    "legacy_selection": (witness("""a = array.new<float>()
b = array.new<float>()
s = str.tostring((close > 0 ? a : b)[bar_index + 10])
valid = s == "NaN"
"""), 1),
    "array_udt": (witness("""type Cell
    int value
a = array.from(Cell.new(bar_index))
b = a[1]
valid = bar_index == 0 ? na(b) : not na(b) and b.get(0).value == bar_index - 1
"""), 1),
    "array_bound_loop": (witness("""a = array.from(float(bar_index))
b = a[1]
float total = 0.0
if not na(b)
    for value in b
        total += value
valid = bar_index == 0 ? na(b) : total == bar_index - 1
"""), 1),
    "array_shadow": (witness("""a = array.from(close)
b = a[1]
f() =>
    b = 7.0
    b
g() =>
    c = array.from(9.0)
    c.get(0)
valid = f() == 7 and g() == 9 and (bar_index == 0 ? na(b) : not na(b))
"""), 1),
    "array_temporary_loop": (witness("""a = array.from(float(bar_index))
b = a[1]
float total = 0.0
if not na(b)
    for value in array.copy(b)
        total += value
valid = bar_index == 0 ? na(b) : total == bar_index - 1
"""), 1),
    "array_ternary": (witness("""a = array.from(float(bar_index))
b = a[1]
float result = 0.0
if bar_index > 0
    c = close > 0 ? b : a
    result := c.get(0)
valid = bar_index == 0 ? na(b) : result == bar_index - 1
"""), 1),
    "array_ternary_na": (witness("""a = array.from(float(bar_index))
b = a[1]
c = bar_index == 0 ? b : a
valid = bar_index == 0 ? na(c) : not na(c) and c.get(0) == bar_index
"""), 1),
    "legacy_assignment": (witness("""a = array.from(1.0, 2.0)
a[1] := 3.0
valid = a.get(1) == 3
"""), 1),
}

for element, value, expected in (
    ("int", "bar_index", "bar_index - 1"),
    ("bool", "true", "true"),
    ("string", '"value"', '"value"'),
    ("color", "color.red", "color.red"),
):
    SUCCESS[f"array_{element}"] = (witness(
        f"a = array.new<{element}>(1, {value})\nb = a[1]\n"
        f"valid = bar_index == 0 ? na(b) : not na(b) and b.get(0) == {expected}\n"
    ), 1)


ERRORS = {
    "array_direct_method": (HEADER + "var a = array.new<float>()\nr = (a[1]).size()\n", NA_ARRAY_MESSAGE),
    "array_bound_method": (HEADER + "var a = array.new<float>()\nb = a[1]\nr = b.size()\n", NA_ARRAY_MESSAGE),
    "array_bound_namespace": (HEADER + "var a = array.new<float>()\nb = a[1]\nr = array.size(b)\n", NA_ARRAY_MESSAGE),
    "array_bound_far": (HEADER + "a = array.from(close)\nb = a[bar_index + 10]\nr = b.get(0)\n", NA_ARRAY_MESSAGE),
    "array_bound_na_branch": (HEADER + "var a = array.new<float>()\nb = a[1]\nr = na(b) ? b.size() : 0\n", NA_ARRAY_MESSAGE),
    "array_helper": (HEADER + "read(array<float> x) => x.size()\na = array.from(close)\nb = a[1]\nr = read(b)\n", NA_ARRAY_MESSAGE),
    "array_direct_helper": (HEADER + "read(array<float> x) => x.size()\na = array.from(close)\nr = read(a[1])\n", NA_ARRAY_MESSAGE),
    "matrix_direct": (HEADER + "a = matrix.new<float>(1, 1, close)\nr = (a[1]).get(0, 0)\n", NA_MATRIX_MESSAGE),
    "matrix_bound": (HEADER + "a = matrix.new<int>(1, 1, bar_index)\nb = a[20]\nr = b.get(0, 0)\n", "matrix operation on na ID"),
    "udt_field": (HEADER + "type Cell\n    int value\na = Cell.new(bar_index)\nb = a[20]\nr = b.value\n", "UDT access on na"),
    "legacy_assignment_missing": (HEADER + "a = array.new<float>()\na[20] := 3.0\n", "Array history element index is out of bounds."),
}


@pytest.mark.parametrize("name", sorted(SUCCESS))
def test_success_shapes_transpile_and_compile(name):
    cpp = transpile(SUCCESS[name][0])
    if name.startswith("array") or name in ("probe", "empty_array"):
        assert "_PFArrayHistoryValue<std::vector<" in cpp
        assert "b = a[1];" not in cpp
    compile_cpp(cpp, label=name)


@pytest.mark.parametrize("name", sorted(ERRORS))
def test_runtime_error_shapes_transpile_and_compile(name):
    compile_cpp(transpile(ERRORS[name][0]), label=name)


def test_success_witnesses_run_without_signals(tmp_path):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 24)
    builds = {name: Build(source, trace=True) for name, (source, _) in SUCCESS.items()}
    runs = execute_all(engine, feed, tmp_path, builds)
    for name, (_, count) in SUCCESS.items():
        run = ok(runs, name)
        assert trade_count(run.trades["default"]) == count, name
        if name != "probe":
            values = [record["value"] for record in run.traces["default"]
                      if record["name"] == "valid"]
            assert values == [1] * 24, name


def test_na_methods_raise_instead_of_signalling(tmp_path):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 4)
    builds = {name: Build(source) for name, (source, _) in ERRORS.items()}
    runs = execute_all(engine, feed, tmp_path, builds)
    for name, (_, message) in ERRORS.items():
        assert runs[name].error is not None and message in runs[name].error, name
        assert "-11" not in runs[name].error, name


def test_map_history_remains_refused_before_codegen():
    with pytest.raises(CompileError, match="History references on map IDs"):
        transpile(HEADER + 'a = map.new<string, float>()\nb = a[1]\nr = na(b)\n')
