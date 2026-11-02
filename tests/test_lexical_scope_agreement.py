"""Measured compile agreement: declarations own a scope and a start position."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from gate.glue import transpile_json
from pineforge_codegen import render_diagnostic, transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.analyzer.value_bindings import BindingInfo, ValueBindings
from pineforge_codegen.ast_nodes import Identifier
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp


FIXTURES = Path(__file__).parent / "fixtures" / "lexical_scope"
VERDICTS = json.loads((FIXTURES / "verdicts.json").read_text())
REFUSED = VERDICTS["refused"]
ACCEPTED = VERDICTS["accepted"]
HEADER = '//@version=6\nstrategy("lexical scope", currency=currency.NONE)\n'


def _analyze(source: str, *, filename: str):
    tokens = Lexer(source, filename=filename).tokenize()
    ast = Parser(tokens, source=source, filename=filename).parse()
    return Analyzer(ast, filename=filename).analyze()


def test_binding_of_reads_annotations_without_side_effects():
    local_context = _analyze(
        HEADER
        + "if true\n"
        "    float localValue = math.abs(close)\n"
        "    outside = localValue\n"
        "readLocal(float argument) =>\n"
        "    if true\n"
        "        float localValue = argument\n"
        "        inside = localValue\n"
        "    argument\n",
        filename="local_bindings.pine",
    )
    outside_block, function = local_context.ast.body[1:]
    outside_decl, outside_read = outside_block.body
    inside_decl, inside_read = function.body[0].body
    local_reads = [
        # Each if body declares its own local; only one is inside the function.
        (outside_read.value, BindingInfo("local", None)),
        (inside_read.value, BindingInfo("local", function.name)),
        # The parameter list declares the argument used in the nested block.
        (inside_decl.value, BindingInfo("parameter", function.name)),
        # close is a Pine builtin, and math is a namespace rather than a value.
        (outside_decl.value.args[0], BindingInfo("builtin", None)),
        (outside_decl.value.callee.object, None),
        # A fresh identifier with a declared name was never part of the walk.
        (Identifier(name=outside_decl.name), None),
    ]

    loop_context = _analyze(
        HEADER
        + "for counter = 0 to 1\n"
        "    outside = counter\n"
        "readCounter(float argument) =>\n"
        "    for counter = 0 to 1\n"
        "        inside = counter\n"
        "    argument\n",
        filename="loop_bindings.pine",
    )
    outside_loop, function = loop_context.ast.body[1:]
    inside_loop = function.body[0]
    loop_reads = [
        # The for headers declare counters outside and inside the function.
        (outside_loop.body[0].value, BindingInfo("loop", None)),
        (inside_loop.body[0].value, BindingInfo("loop", function.name)),
    ]

    global_context = _analyze(
        HEADER
        + "float sharedValue = close\n"
        "outside = sharedValue\n"
        "readShared() => sharedValue\n"
        "method readSharedMethod(float receiver) =>\n"
        "    float localValue = receiver\n"
        "    localValue + sharedValue\n",
        filename="global_bindings.pine",
    )
    global_decl, outside_read, function, method = global_context.ast.body[1:]
    method_decl, method_result = method.body
    global_reads = [
        # The script declares sharedValue, even where a function or method reads it.
        (outside_read.value, BindingInfo("global", None)),
        (function.body[0].expr, BindingInfo("global", None)),
        (method_result.expr.right, BindingInfo("global", None)),
        # The method declares its local and its receiver parameter.
        (method_result.expr.left, BindingInfo("local", method.name)),
        (method_decl.value, BindingInfo("parameter", method.name)),
        (global_decl.value, BindingInfo("builtin", None)),
    ]

    assert [item.name for item in fields(BindingInfo)] == ["kind", "callable"]
    for context, reads in [
        (local_context, local_reads),
        (loop_context, loop_reads),
        (global_context, global_reads),
    ]:
        # A new reader has no resolved scopes and must use the existing annotations.
        bindings = ValueBindings(context.ast, context.filename)
        diagnostics_before = deepcopy(context.diagnostics)
        for identifier, expected in reads:
            assert isinstance(identifier, Identifier)
            annotations = identifier.annotations
            annotations_before = deepcopy(annotations)
            first = bindings.binding_of(identifier)
            assert first == expected
            assert bindings.binding_of(identifier) == first
            if first is not None:
                with pytest.raises(FrozenInstanceError):
                    first.kind = first.kind
            assert identifier.annotations is annotations
            assert identifier.annotations == annotations_before
            assert context.diagnostics == diagnostics_before


def _without_support(source: str, *, filename: str):
    return transpile(source, filename=filename, check_support=False)


def _assert_refusal(action, source, filename, name, code, line, col):
    with pytest.raises(CompileError) as caught:
        action(source, filename=filename)
    diagnostic, = caught.value.diagnostics
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.code == code
    assert diagnostic.args == {"name": name}
    location = diagnostic.location
    assert location.file == filename
    assert (location.line, location.col, location.end_col) == (line, col, col + len(name))
    assert source.splitlines()[line - 1][col - 1:location.end_col - 1] == name
    assert render_diagnostic(diagnostic.code, diagnostic.args) == (
        diagnostic.message, diagnostic.hint,
    )


@pytest.mark.parametrize("case", REFUSED, ids=lambda case: case["probe"])
@pytest.mark.parametrize("action", [transpile, _without_support, _analyze])
def test_measured_refusal_points_to_first_inaccessible_use(case, action):
    filename = case["probe"] + ".pine"
    source = (FIXTURES / filename).read_text()
    _assert_refusal(
        action, source, filename, case["name"], case["code"], case["line"], case["col"],
    )


@pytest.fixture(scope="module")
def accepted_cpp_baseline():
    path = FIXTURES / "accepted_cpp.json"
    assert path.is_file(), (
        "Missing fixed-main C++ hashes. They were captured by transpiling the "
        "accepted sources with archived main (never with the code under test); "
        "recapture them the same way."
    )
    baseline = json.loads(path.read_text())
    assert baseline["schema"] == "pineforge-scope-cpp-baseline/v1"
    assert baseline["base_commit"] == VERDICTS["base_commit"]
    assert set(baseline["probes"]) == set(ACCEPTED)
    return baseline["probes"]


# Main's C++ for these two accepted scripts (a loop counter read inside its own
# end or step expression) does not compile, and the counted-loop lowering that
# declares the counter before its end and step fixes it on purpose, so their
# C++ differs from main's. Their native compile is checked below and they run
# in test_e2e_for_counter_headers.py; every other accepted script keeps main's C++.
CPP_CHANGED_BY_HEADER_COUNTER_LOWERING = frozenset({
    "block_scope/open_counter_header_end",
    "block_scope/open_counter_header_step",
})


@pytest.mark.parametrize(
    "probe", [probe for probe in ACCEPTED if probe not in CPP_CHANGED_BY_HEADER_COUNTER_LOWERING])
def test_measured_acceptance_keeps_fixed_main_cpp(probe, accepted_cpp_baseline):
    source = (FIXTURES / (probe + ".pine")).read_text()
    expected = accepted_cpp_baseline[probe]
    assert hashlib.sha256(source.encode()).hexdigest() == expected["source_sha256"]
    cpp = transpile(source, filename=probe + ".pine")
    assert hashlib.sha256(cpp.encode()).hexdigest() == expected["cpp_sha256"], probe


@pytest.mark.parametrize("probe", ACCEPTED)
def test_measured_acceptance_still_compiles(probe):
    source = (FIXTURES / (probe + ".pine")).read_text()
    compile_cpp(transpile(source, filename=probe + ".pine"), label=probe)


def test_missing_binding_in_json_has_the_authored_span():
    source = (FIXTURES / "name_resolution/ref_self_initializer.pine").read_text()
    result = json.loads(transpile_json(source))
    assert result["ok"] is False
    assert "cpp" not in result
    diagnostic, = result["diagnostics"]
    assert diagnostic["code"] == "PF-E2075"
    assert diagnostic["args"] == {"name": "lateValue"}
    assert (diagnostic["line"], diagnostic["col"], diagnostic["endCol"]) == (5, 23, 32)


@pytest.mark.parametrize("body,name,line,col", [
    ("if false\n    result = unseen\n    float unseen = close\n", "unseen", 4, 14),
    ("unused() =>\n    result = unseen\n    float unseen = close\n    result\n",
     "unseen", 4, 14),
    ("value = value + 1\n", "value", 3, 9),
    ("[left, right] = if true\n    [left, open]\nelse\n    [close, open]\n",
     "left", 4, 6),
    ("float total = 0\nwhile unseen > 0\n    int unseen = 0\n",
     "unseen", 4, 7),
])
def test_visibility_also_checks_dead_code_and_initializers(body, name, line, col):
    _assert_refusal(
        transpile, HEADER + body, "extra.pine", name, "PF-E2075", line, col,
    )


def test_iterable_is_resolved_before_its_binder():
    _assert_refusal(
        transpile, HEADER + "for item in array.from(item)\n    observed = item\n",
        "iterable.pine", "item", "PF-E2054", 3, 24,
    )


@pytest.mark.parametrize("body", [
    "read(float value = available) => value\nfloat available = close\nout = read()\n",
    "type Reading\n    float value = available\nfloat available = close\n",
    "read(float earlier, float value = earlier) => value\nout = read(close)\n",
    "read(float value = value) => value\nout = read()\n",
    "type Reading\n    float earlier = 1\n    float value = earlier\n",
    "type Reading\n    float value = value\n",
    "method read(float self, float value = self) => value\nout = close.read()\n",
])
def test_defaults_do_not_bind_later_globals_parameters_or_fields(body):
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + body)
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E2075"
    assert diagnostic.location is not None


@pytest.mark.parametrize("body", [
    "read(color value = color.red) => value\nout = read()\n",
    "type Reading\n    color value = color.red\nreading = Reading.new()\n",
    "float total = 0\nint cursor = 2\nfor cursor = cursor to cursor + 1 by 1\n"
    "    total += cursor\n",
    "float total = 0\nfor cursor = 0 to 1\n    for cursor = cursor to cursor + 1\n"
    "        total += cursor\n    total += cursor\n",
])
def test_builtin_defaults_and_visible_outer_header_bindings_stay_accepted(body):
    assert "class GeneratedStrategy" in transpile(HEADER + body)


@pytest.mark.parametrize("target", ["absent", "absent.value", "absent[1]"])
def test_an_expired_assignment_target_is_checked_before_its_value(target):
    source = (
        HEADER + "if false\n    float absent = close\n"
        + target + " := otherMissing\n"
    )
    with pytest.raises(CompileError) as caught:
        transpile(source)
    diagnostic, = caught.value.diagnostics
    assert diagnostic.args == {"name": "absent"}
    assert (diagnostic.location.line, diagnostic.location.col) == (5, 1)
    assert diagnostic.code == ("PF-E2020" if target == "absent" else "PF-E2054")


def test_expired_receiver_is_a_value_read_at_its_own_span():
    _assert_refusal(
        transpile, HEADER + "if true\n    missingReceiver = array.from(close)\n"
        "result = missingReceiver.size()\n", "receiver.pine",
        "missingReceiver", "PF-E2054", 5, 10,
    )
