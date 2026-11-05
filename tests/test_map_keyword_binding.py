"""TradingView-accepted keyword suffixes and map named-slot lowering.

Each accepted keyword form is compared with its positional twin: the same
named slots must produce the same result types and the same generated C++.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import FuncCall, Identifier, MemberAccess, Program
from pineforge_codegen.errors import CompileError, Phase
from pineforge_codegen.symbols import TypeSpec
from tests._compile import STRATEGY_FP_FLAGS, compile_cpp
from tests.test_map_param_methods import _compile_and_run


PROBE_ROOT = Path(__file__).parent / "fixtures" / "call_keyword_binding"
REAL_PROBE = PROBE_ROOT / "call_order" / "map_accept.pine"
SECTION_B_CONTROLS = sorted(
    path for path in (PROBE_ROOT / "call_order").glob("*.pine") if path != REAL_PROBE
)
SECTION_C_PROBES = sorted((PROBE_ROOT / "map_keywords").glob("*.pine"))

# Only the map calls differ in these positional twins; the fixture sources
# retain the supplied strategy declarations, comments, and trading carrier.
_PROBE_TWIN_REPLACEMENTS = {
    "call_order/map_accept.pine": (
        'map.put(values, "a", value = 4.0)', 'map.put(values, "a", 4.0)',
    ),
    "map_keywords/mk1_get_named_all.pine": (
        'map.get(id = values, key = "a")', 'map.get(values, "a")',
    ),
    "map_keywords/mk2_get_positional_then_named.pine": (
        'map.get(values, key = "a")', 'map.get(values, "a")',
    ),
    "map_keywords/mk3_get_method_named.pine": (
        'values.get(key = "a")', 'values.get("a")',
    ),
    "map_keywords/mk4_put_all_named.pine": (
        'map.put(id = values, key = "a", value = 4.0)',
        'map.put(values, "a", 4.0)',
    ),
    "map_keywords/mk5_put_key_value_named.pine": (
        'map.put(values, key = "a", value = 4.0)', 'map.put(values, "a", 4.0)',
    ),
    "map_keywords/mk6_put_method_named.pine": (
        'values.put(key = "a", value = 4.0)', 'values.put("a", 4.0)',
    ),
}


def positional_probe_source(path: Path) -> str:
    source = path.read_text()
    old, new = _PROBE_TWIN_REPLACEMENTS[path.relative_to(PROBE_ROOT).as_posix()]
    assert source.count(old) == 1
    return source.replace(old, new)


def test_real_map_keyword_prefix_transpiles():
    transpile(REAL_PROBE.read_text(), filename="map-prefix.pine")


@pytest.mark.parametrize("path", SECTION_B_CONTROLS, ids=lambda p: p.stem)
def test_section_b_controls_keep_transpiling(path: Path):
    transpile(path.read_text(), filename=path.name)


@pytest.mark.parametrize("path", SECTION_C_PROBES, ids=lambda p: p.stem)
def test_section_c_map_keywords_transpile(path: Path):
    transpile(path.read_text(), filename=path.name)


@pytest.mark.parametrize(
    "path", [REAL_PROBE, *SECTION_C_PROBES], ids=lambda p: p.stem,
)
def test_measured_map_keywords_match_positional_twins(path: Path):
    assert transpile(path.read_text(), filename=path.name) == transpile(
        positional_probe_source(path), filename=path.name,
    )


# Independent call spellings exercise every map operation and every receiver
# dispatch route, including suffixes and reversed all-keyword arguments.
MAP_CALL_TWINS = [
    ('map.put({target}, "birch", value=17.5)',
     'map.put({target}, "birch", 17.5)'),
    ('map.put(value=17.5, key="birch", id={target})',
     'map.put({target}, "birch", 17.5)'),
    ('map.get({target}, key="birch")', 'map.get({target}, "birch")'),
    ('map.get(key="birch", id={target})', 'map.get({target}, "birch")'),
    ('map.remove(id={target}, key="birch")', 'map.remove({target}, "birch")'),
    ('map.contains({target}, key="birch")', 'map.contains({target}, "birch")'),
    ('map.put_all({target}, id2=source)', 'map.put_all({target}, source)'),
    ('map.put_all(id2=source, id={target})', 'map.put_all({target}, source)'),
    ('map.size(id={target})', 'map.size({target})'),
    ('map.clear(id={target})', 'map.clear({target})'),
    ('map.keys(id={target})', 'map.keys({target})'),
    ('map.values(id={target})', 'map.values({target})'),
    ('map.copy(id={target})', 'map.copy({target})'),
    ('{target}.put("birch", value=17.5)', '{target}.put("birch", 17.5)'),
    ('{target}.put(value=17.5, key="birch")', '{target}.put("birch", 17.5)'),
    ('{target}.get(key="birch")', '{target}.get("birch")'),
    ('{target}.remove(key="birch")', '{target}.remove("birch")'),
    ('{target}.contains(key="birch")', '{target}.contains("birch")'),
    ('{target}.put_all(id2=source)', '{target}.put_all(source)'),
]
MAP_RECEIVER_ROUTES = ("global", "local", "parameter", "expression", "field")


def map_call_source(call: str, route: str) -> str:
    header = '//@version=6\nstrategy("Map argument slots")\n'
    declarations = (
        "target = map.new<string, float>()\n"
        "source = map.new<string, float>()\n"
    )
    if route == "local":
        body = declarations + call.format(target="target") + "\nmap.size(target)"
        return header + "probe() =>\n" + "".join(
            f"    {line}\n" for line in body.splitlines()
        ) + "metric = probe()\n"
    if route == "parameter":
        return (
            header
            + "probe(map<string, float> target, map<string, float> source) =>\n"
            + f"    {call.format(target='target')}\n"
            + "    target.size()\n"
            + declarations
            + "metric = probe(target, source)\n"
        )
    if route == "expression":
        receiver = "identity(target)"
        extra = "identity(map<string, float> item) => item\n"
    elif route == "field":
        receiver = "holder.values"
        extra = (
            "type Holder\n"
            "    map<string, float> values\n"
            "holder = Holder.new(target)\n"
        )
    else:
        receiver = "target"
        extra = ""
    return (
        header + declarations + extra + call.format(target=receiver)
        + "\nmetric = map.size(target)\n"
    )


@pytest.mark.parametrize("route", MAP_RECEIVER_ROUTES)
@pytest.mark.parametrize(("named", "positional"), MAP_CALL_TWINS)
def test_map_keyword_slots_match_positional_calls(named, positional, route):
    assert transpile(map_call_source(named, route)) == transpile(
        map_call_source(positional, route)
    )


@pytest.mark.parametrize(
    ("method", "parameter_names"),
    [
        ("copy", ()),
        ("put", ("key", "value")),
        ("get", ("key",)),
        ("remove", ("key",)),
        ("keys", ()),
        ("values", ()),
    ],
)
@pytest.mark.parametrize(
    ("key_type", "value_type"), [("string", "int"), ("int", "string")],
)
def test_map_history_keyword_receiver_matches_positional_type_spec(
    method, parameter_names, key_type, value_type,
):
    analyzer = Analyzer(Program(version=6))
    receiver = Identifier(name="target")
    arguments = {
        name: Identifier(name=f"entry_{name}") for name in parameter_names
    }
    callee = MemberAccess(object=Identifier(name="map"), member=method)
    overrides = {
        id(receiver): TypeSpec.map(
            TypeSpec.primitive(key_type), TypeSpec.primitive(value_type)
        ),
    }
    positional_spec = analyzer._history_receiver_type_spec(
        FuncCall(callee=callee, args=[receiver, *arguments.values()]), overrides
    )
    keyword_spec = analyzer._history_receiver_type_spec(
        FuncCall(callee=callee, kwargs={"id": receiver, **arguments}), overrides
    )
    assert positional_spec is not None
    assert keyword_spec == positional_spec


INVALID_MAP_CALLS = [
    ('map.get(target, other="birch")', "PF-E5016",
     "map.get: unknown keyword argument 'other'"),
    ('target.get(other="birch")', "PF-E5016",
     "map.get: unknown keyword argument 'other'"),
    ('map.get(target, "birch", key="oak")', "PF-E5019",
     "map.get: argument 'key' passed both positionally and by keyword"),
    ('map.get(target, key="birch", id=target)', "PF-E5019",
     "map.get: argument 'id' passed both positionally and by keyword"),
    ('target.get("birch", key="oak")', "PF-E5019",
     "map.get: argument 'key' passed both positionally and by keyword"),
    ('map.put(target, value=17.5)', "PF-E5020",
     "map.put: missing required argument 'key'"),
    ('target.put(value=17.5)', "PF-E5020",
     "map.put: missing required argument 'key'"),
    ('map.get(key="birch")', "PF-E5020",
     "map.get: missing required argument 'id'"),
    ('map.get(target, "birch", "oak")', "PF-E5018",
     "map.get: too many positional arguments (expected 2, got 3)"),
    ('map.put_all(id=target, from=source)', "PF-E5017",
     "map.put_all: unknown keyword argument 'from'"),
]


@pytest.mark.parametrize(("call", "code", "message"), INVALID_MAP_CALLS)
def test_invalid_map_bindings_keep_current_diagnostics(call, code, message):
    source = (
        '//@version=6\nstrategy("Invalid map binding")\n'
        "target = map.new<string, float>()\n"
        "source = map.new<string, float>()\n" + call + "\n"
    )
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="map-binding-error.pine")
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.phase is Phase.CODEGEN
    assert diagnostic.code == code
    assert diagnostic.message == message
    assert diagnostic.location.file == "map-binding-error.pine"


# A positional argument after a keyword argument is refused by the separate
# call-order rule (PF-E2074). These tests cover legal prefixes and invalid bindings.


TYPE_CASES = [
    ("float", "13.25", "double"),
    ("int", "37", "int"),
    ("bool", "true", "bool"),
    ("string", '"leaf"', "std::string"),
]


def typed_map_source(pine_type: str, seed: str, *, named: bool) -> str:
    def call(method, args=""):
        if named:
            return f"map.{method}(id=target{', ' + args if args else ''})"
        positional = {
            'key="birch"': '"birch"',
            f'key="birch", value={seed}': f'"birch", {seed}',
            "id2=source": "source",
        }.get(args, args)
        return f"map.{method}(target{', ' + positional if positional else ''})"

    get = call("get", 'key="birch"')
    put = call("put", f'key="birch", value={seed}')
    remove = call("remove", 'key="birch"')
    return f'''//@version=6
strategy("Map named receiver result types")
var target = map.new<string, {pine_type}>()
var source = map.new<string, {pine_type}>()
read_value(map<string, {pine_type}> target) => {get}
put_value(map<string, {pine_type}> target) => {put}
remove_value(map<string, {pine_type}> target) => {remove}
copy_values(map<string, {pine_type}> target) => {call("copy")}
read_keys(map<string, {pine_type}> target) => {call("keys")}
read_values(map<string, {pine_type}> target) => {call("values")}
clear_values(map<string, {pine_type}> target) => {call("clear")}
merge_values(map<string, {pine_type}> target, map<string, {pine_type}> source) => {call("put_all", "id2=source")}
{put}
direct = {get}
copied = {call("copy")}
key_list = {call("keys")}
value_list = {call("values")}
via_helper = read_value(target)
helper_copy = copy_values(target)
helper_keys = read_keys(target)
helper_values = read_values(target)
put_result = put_value(target)
removed = remove_value(target)
cleared = clear_values(target)
merged = merge_values(target, source)
'''


@pytest.mark.parametrize(("pine_type", "seed", "cpp_type"), TYPE_CASES)
def test_named_receivers_preserve_result_types(pine_type, seed, cpp_type):
    source = typed_map_source(pine_type, seed, named=True)
    cpp = transpile(source)
    assert cpp == transpile(typed_map_source(pine_type, seed, named=False))
    for name in ("copied", "helper_copy"):
        assert f"PineMap<std::string, {cpp_type}> {name};" in cpp
    for name in ("key_list", "helper_keys"):
        assert f"std::vector<std::string> {name};" in cpp
    for name in ("value_list", "helper_values"):
        assert f"std::vector<{cpp_type}> {name};" in cpp
    if pine_type == "string":
        for name in ("read_value", "put_value", "remove_value"):
            assert f"std::string {name}(" in cpp


TERMINAL_CALL_TWINS = [
    ("map.clear(id=target)", "map.clear(target)"),
    ('map.get(target, key="birch")', 'map.get(target, "birch")'),
    ('target.get(key="birch")', 'target.get("birch")'),
    ("target.put_all(id2=source)", "target.put_all(source)"),
]


def terminal_map_source(call: str, route: str) -> str:
    header = (
        '//@version=6\nstrategy("Terminal map keyword binding")\n'
        "var source = map.new<string, string>()\n"
    )
    declaration = "target = map.new<string, string>()\n"
    if route == "local":
        return (
            header + "probe() =>\n    " + declaration
            + f"    {call}\nobserved = probe()\n"
        )
    if route == "parameter":
        return (
            header + declaration
            + f"probe(map<string, string> target) => {call}\n"
            + "observed = probe(target)\n"
        )
    return header + declaration + f"probe() => {call}\nobserved = probe()\n"


@pytest.mark.parametrize("route", ("global", "local", "parameter"))
@pytest.mark.parametrize(("named", "positional"), TERMINAL_CALL_TWINS)
def test_terminal_keywords_match_positional_returns(named, positional, route):
    cpp = transpile(terminal_map_source(named, route))
    assert cpp == transpile(terminal_map_source(positional, route))
    if ".get(" in named:
        assert "std::string probe(" in cpp


def test_real_map_keyword_prefix_native():
    cpp = transpile(REAL_PROBE.read_text())
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) return 2;
    std::cout << strategy.metric << "\n";
}
'''
    assert float(_compile_and_run(cpp + driver)) == 4.0


@pytest.mark.parametrize(("pine_type", "seed", "cpp_type"), TYPE_CASES)
def test_named_receiver_results_compile(pine_type, seed, cpp_type):
    compile_cpp(
        transpile(typed_map_source(pine_type, seed, named=True)),
        label=f"map_named_receiver_{pine_type}",
        extra_flags=STRATEGY_FP_FLAGS,
    )


def test_global_string_method_keyword_return_compiles():
    compile_cpp(
        transpile(terminal_map_source('target.get(key="birch")', "global")),
        label="map_global_string_keyword_return",
        extra_flags=STRATEGY_FP_FLAGS,
    )


@pytest.mark.parametrize("route", ("global", "local", "parameter"))
@pytest.mark.parametrize(("named", "positional"), TERMINAL_CALL_TWINS)
def test_terminal_keyword_returns_compile(named, positional, route):
    compile_cpp(
        transpile(terminal_map_source(named, route)),
        label=f"map_terminal_keyword_{route}",
        extra_flags=STRATEGY_FP_FLAGS,
    )
