"""Loop binders are visible in their body, never exported from that body."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gate.glue import transpile_json
from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp


HEADER = (
    '//@version=6\n'
    'strategy("loop scope", currency=currency.NONE, initial_capital=1000000)\n'
)
SCHEDULE = (
    'if bar_index > 6 and bar_index % 7 == 3\n'
    '    strategy.entry("L", strategy.long, qty = 1)\n'
    'if bar_index > 6 and bar_index % 7 == 5\n'
    '    strategy.close("L")\n'
)
REAL = (Path(__file__).parent / "fixtures/loop_variable_scope/LOOPS.counter_after.pine").read_text()


def _analyze(source: str, *, filename: str):
    tokens = Lexer(source, filename=filename).tokenize()
    ast = Parser(tokens, source=source, filename=filename).parse()
    return Analyzer(ast, filename=filename).analyze()


def _assert_refusal(action, source, name, line, column, code="PF-E2054"):
    with pytest.raises(CompileError) as caught:
        action(source, filename="loop_scope.pine")
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == code
    assert diagnostic.args == {"name": name}
    assert diagnostic.message == (
        f"Block-local variable '{name}' is read outside its block."
        if code == "PF-E2054" else f"Undefined variable: '{name}'"
    )
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.location.file == "loop_scope.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (line, column)
    assert diagnostic.location.end_col == column + len(name)


@pytest.mark.parametrize("action", [transpile, transpile_full, _analyze])
def test_real_counter_after_is_refused_at_the_pine_identifier(action):
    _assert_refusal(action, REAL, "i", 8, 30)


def test_real_counter_after_json_reports_the_same_span_and_code():
    result = json.loads(transpile_json(REAL))
    assert result["ok"] is False
    assert "cpp" not in result
    diagnostic, = result["diagnostics"]
    assert diagnostic["code"] == "PF-E2054"
    assert diagnostic["args"] == {"name": "i"}
    assert diagnostic["severity"] == "error"
    assert (diagnostic["line"], diagnostic["col"], diagnostic["endCol"]) == (8, 30, 31)


REFUSALS = [
    pytest.param(
        "float total = 0.0\nfor stepIndex = 1 to 4 by 2\n"
        "    total := stepIndex\nresult = stepIndex\n",
        "stepIndex", 6, 10, id="stepped-counter",
    ),
    pytest.param(
        "float total = 0.0\nfor cursor = 0 to 1\n    total := cursor\n"
        "if close > open\n    result = cursor\n",
        "cursor", 7, 14, id="later-sibling-block",
    ),
    pytest.param(
        "float total = 0.0\nfor outer = 0 to 1\n"
        "    for inner = 0 to 1\n        total := inner\n"
        "    result = inner\n",
        "inner", 7, 14, id="inner-scope-ended",
    ),
    pytest.param(
        "float total = 0.0\nfor same = 0 to 1\n"
        "    for same = 0 to 1\n        total += same\n"
        "    total += same\nresult = same\n",
        "same", 8, 10, id="nested-same-name-both-ended",
    ),
    pytest.param(
        "measure() =>\n    float total = 0.0\n"
        "    for ordinal = 0 to 1\n        total += ordinal\n"
        "    total + ordinal\nresult = measure()\n",
        "ordinal", 7, 13, id="function-body",
    ),
    pytest.param(
        "float total = 0.0\nfor turn = 0 to 3\n"
        "    if turn == 1\n        continue\n"
        "    if turn == 2\n        break\n    total += turn\n"
        "result = turn\n",
        "turn", 10, 10, id="break-and-continue",
    ),
    pytest.param(
        "values = array.from(close, open)\nfor element in values\n"
        "    observed = element\nresult = element\n",
        "element", 6, 10, id="single-array-element",
    ),
    pytest.param(
        "values = array.from(close, open)\nfor [offset, element] in values\n"
        "    observed = element + offset\nresult = offset\n",
        "offset", 6, 10, id="array-index",
    ),
    pytest.param(
        "values = array.from(close, open)\nfor [offset, element] in values\n"
        "    observed = element + offset\nresult = element\n",
        "element", 6, 10, id="array-pair-element",
    ),
    pytest.param(
        'values = map.new<string, float>()\nvalues.put("a", close)\n'
        "for [key, value] in values\n    observed = value\nresult = key\n",
        "key", 7, 10, id="map-key",
    ),
    pytest.param(
        'values = map.new<string, float>()\nvalues.put("a", close)\n'
        "for [key, value] in values\n    observed = value\nresult = value\n",
        "value", 7, 10, id="map-value",
    ),
    pytest.param(
        "for sample = 0 to 1\n    observed = sample\n"
        "result = math.abs(number=sample)\n",
        "sample", 5, 26, id="keyword-argument-value",
    ),
    pytest.param(
        "for sample = 0 to 1\n    observed = sample\nresult = sample[1]\n",
        "sample", 5, 10, id="history-root",
    ),
    pytest.param(
        "groups = array.from(array.from(close))\nfor group in groups\n"
        "    observed = group.size()\nresult = group.size()\n",
        "group", 6, 10, id="member-receiver",
    ),
    pytest.param(
        "for hidden = 0 to 1\n    observed = hidden\n"
        "if false\n    result = hidden\n",
        "hidden", 6, 14, id="unreachable-read",
    ),
    pytest.param(
        "if close > open\n    result = laterCounter\n"
        "for laterCounter = 0 to 1\n    observed = laterCounter\n",
        "laterCounter", 4, 14, id="sibling-before-loop",
    ),
    pytest.param(
        "for str = 0 to 1\n    observed = str\nresult = str + 1\n",
        "str", 5, 10, id="namespace-spelling-used-as-value",
    ),
    pytest.param(
        "method measure(float self) =>\n    float total = self\n"
        "    for ordinal = 0 to 1\n        total += ordinal\n"
        "    total + ordinal\nresult = close.measure()\n",
        "ordinal", 7, 13, id="method-body",
    ),
]


@pytest.mark.parametrize("body,name,line,column", REFUSALS)
def test_expired_loop_binding_has_a_located_diagnostic(body, name, line, column):
    _assert_refusal(transpile, HEADER + body, name, line, column)


def test_counter_reassignment_after_loop_keeps_existing_undefined_variable_error():
    _assert_refusal(
        transpile, HEADER + "for target = 0 to 1\n    observed = target\ntarget := 2\n",
        "target", 5, 1, code="PF-E2020",
    )


CAPTURED_REAL = REAL.replace(
    "for i = 0 to 1\n", "int captured = 0\nfor i = 0 to 1\n"
).replace(
    "    signalValue := close - open\n",
    "    signalValue := close - open\n    captured := i\n",
).replace("signalValue + i", "signalValue + captured")

ACCEPTED = [
    pytest.param(CAPTURED_REAL, id="real-explicit-capture"),
    pytest.param(HEADER + (
        "int i = bar_index + 10\nfloat result = 0.0\n"
        "for i = 0 to 1\n    result := i\nresult += i\n"
    ) + SCHEDULE, id="outer-counter-restored"),
    pytest.param(HEADER + (
        "float result = 0.0\nfor i = 0 to 1\n    result += i\n"
        "for i = 2 to 3 by 1\n    result += i\n"
    ) + SCHEDULE, id="sequential-counter-reuse"),
    pytest.param(HEADER + (
        "float result = 0.0\nfor i = 0 to 1\n"
        "    for i = 2 to 3\n        result += i\n    result += i\n"
    ) + SCHEDULE, id="nested-counter-restored"),
    pytest.param(HEADER + (
        "measure(int i) =>\n    float total = 0.0\n"
        "    for i = 0 to 1\n        total += i\n    total + i\n"
        "result = measure(10)\n"
    ) + SCHEDULE, id="function-parameter-restored"),
    pytest.param(HEADER + (
        "float result = 0.0\nfor i = 0 to 4\n"
        "    if i == 1\n        continue\n"
        "    if i == 3\n        break\n    result += i\n"
    ) + SCHEDULE, id="break-continue-local-read"),
    pytest.param(HEADER + (
        "values = array.from(close, open)\nfloat result = 0.0\n"
        "for value in values\n    result += value\n"
        "for [index, value] in values\n    result += index + value\n"
    ) + SCHEDULE, id="array-binder-captures"),
    pytest.param(HEADER + (
        'values = map.new<string, float>()\nvalues.put("a", close)\n'
        'string capturedKey = ""\nfloat result = 0.0\n'
        "for [key, value] in values\n"
        "    capturedKey := key\n    result += value\n"
    ) + SCHEDULE, id="map-binder-captures"),
    pytest.param(HEADER + (
        "float result = 0.0\nfor close = 0 to 1\n    result += close\n"
        "result += close\n"
    ) + SCHEDULE, id="builtin-restored"),
    pytest.param(HEADER + (
        "pair() => [2, 3]\n[i, unused] = pair()\nfloat result = 0.0\n"
        "for i = 0 to 1\n    result += i\nresult += i\n"
    ) + SCHEDULE, id="tuple-outer-binding"),
    pytest.param(HEADER + (
        "float result = 0.0\nfor i = 0 to 1\n"
        "    int count = 0\n    while count < 1\n"
        "        result += i\n        count += 1\n"
    ) + SCHEDULE, id="while-has-no-binder"),
    pytest.param(HEADER + (
        "float result = 0.0\nfor i = 0 to 1\n"
        "    selected = switch i\n"
        "        0 => i + 2\n        => i + 3\n    result += selected\n"
    ) + SCHEDULE, id="switch-descendant"),
    pytest.param(HEADER + (
        "measure() =>\n    for i = 0 to 1\n        close + i\n"
        "result = measure()\n"
    ) + SCHEDULE, id="terminal-loop-value"),
    pytest.param(HEADER + (
        "method measure(float self) =>\n    float total = self\n"
        "    for i = 0 to 1\n        total += i\n    total\n"
        "result = close.measure()\n"
    ) + SCHEDULE, id="method-local-loop"),
    pytest.param(HEADER + (
        'g(_e) => request.security(syminfo.tickerid, "D", _e[1])\n'
        "int i = 4\nfloat captured = 0.0\n"
        "for i = 0 to 1\n    captured += i\nresult = g(i)\n"
    ) + SCHEDULE, id="copied-global-payload"),
    pytest.param(HEADER + (
        "float result = 0.0\nfor str = 0 to 1\n    result += str\n"
        "formatted = str.tostring(result)\n"
    ) + SCHEDULE, id="namespace-member-after-shadow"),
    pytest.param(HEADER + (
        "float result = 0.0\nif close > open\n    int i = 10\n"
        "    for i = 0 to 1\n        result += i\n    result += i\n"
    ) + SCHEDULE, id="branch-outer-binding"),
    pytest.param(HEADER + (
        "values = array.from(close, open)\nint index = 10\nfloat value = 5.0\n"
        "float result = 0.0\nfor [index, value] in values\n"
        "    result += index + value\nresult += index + value\n"
    ) + SCHEDULE, id="for-in-outer-bindings"),
]


@pytest.mark.parametrize("source", ACCEPTED)
def test_legal_loop_bindings_still_transpile(source):
    assert "class GeneratedStrategy" in transpile(source)


@pytest.mark.parametrize("source", ACCEPTED)
def test_legal_loop_bindings_still_compile(source):
    compile_cpp(transpile(source), label="lexical loop binding")


@pytest.mark.parametrize("version", [5, 6])
def test_imported_library_loop_binding_keeps_its_scope(version):
    source = HEADER + "import examples/Scope/1 as scope\nresult = scope.sum(10)\n"
    library = (
        f'//@version={version}\nlibrary("Scope")\n'
        "export sum(int count) =>\n    int result = count\n"
        "    for i = 0 to 1\n        result += i\n    result\n"
    )
    assert "class GeneratedStrategy" in transpile(
        source, libraries={"examples/Scope/1": library},
    )


@pytest.mark.parametrize("header, error_column", [
    ("for i = i to 1", 9),
    ("for i = 0 to 1 - i", None),
    ("for i = 0 to 1 by i", None),
])
def test_measured_counter_header_visibility(header, error_column):
    source = HEADER + header + "\n    observed = i\n" + SCHEDULE
    if error_column is not None:
        # block_scope/open_counter_header_start (CE10272).
        _assert_refusal(transpile, source, "i", 3, error_column)
    else:
        # block_scope/open_counter_header_end and open_counter_header_step accept.
        assert "class GeneratedStrategy" in transpile(source)
