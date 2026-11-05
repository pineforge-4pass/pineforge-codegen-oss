"""Each v6 call writes a positional prefix followed by a keyword suffix."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

from gate.glue import transpile_json
from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.diagnostic_codes import classify, render_diagnostic
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.library_modules import parse_library_module
from pineforge_codegen.limits import iter_ast_nodes
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp


ROOT = Path(__file__).resolve().parents[1]
REAL = (ROOT / "tests/fixtures/call_argument_order/"
        "LEX.positional_after_keyword.pine").read_text(encoding="utf-8")
HEADER = '//@version=6\nstrategy("Argument order")\n'
CODE = "PF-E2074"
MESSAGE = "Positional arguments cannot follow keyword arguments."
LIBRARY_PATH = "pftest/ArgumentOrder/1"
LIBRARY = '''//@version=6
library("ArgumentOrder")
export combine(float left, float right = 1.0) => left + right
'''


# Each input tests one argument list; the suffix identifies the first late
# positional expression without relying on the name of the function being called.
REFUSALS = [
    pytest.param('value = str.substring(source="ribbon", 2, 5)\n',
                 "2, 5", id="substring-keyword-first"),
    pytest.param('value = str.substring("ribbon", end_pos=5, 2)\n',
                 "2)", id="substring-positional-prefix"),
    pytest.param("combine(float left, float right = 1.0) => left + right\n"
                 "value = combine(right=3.0, 7.0)\n",
                 "7.0", id="user-function-default"),
    pytest.param("method combine(float self, float left, float right = 1.0) => self + left + right\n"
                 "base = close\nvalue = base.combine(right=3.0, 7.0)\n",
                 "7.0", id="user-method"),
    pytest.param("type Pair\n    float left = 0.0\n    float right = 1.0\n"
                 "value = Pair.new(right=3.0, 7.0)\n",
                 "7.0", id="udt-constructor"),
    pytest.param("value = array.new<float>(initial_value=3.0, 7)\n",
                 "7)", id="generic-array-constructor"),
    pytest.param("value = array.new_float(initial_value=3.0, 7)\n",
                 "7)", id="typed-array-constructor"),
    pytest.param("items = array.new_float(3, 1.0)\n"
                 "value = array.get(index=1, items)\n",
                 "items)", id="array-namespace"),
    pytest.param("items = array.new_float(3, 1.0)\n"
                 "items.set(value=3.0, 1)\n",
                 "1)", id="array-method"),
    pytest.param("items = matrix.new<float>(2, 2, 1.0)\n"
                 "value = items.get(column=1, 0)\n",
                 "0)", id="matrix-method"),
    pytest.param("items = map.new<string, float>()\n"
                 'map.put(items, value=3.0, "key")\n',
                 '"key"', id="map-namespace"),
    pytest.param('strategy.entry("Order", qty=2, strategy.long)\n',
                 "strategy.long)", id="strategy-action"),
    pytest.param('strategy(overlay=true, "Ordered title")\n',
                 '"Ordered title"', id="strategy-declaration"),
    pytest.param("value = nz(replacement=0, close)\n",
                 "close)", id="rewritten-builtin"),
    pytest.param("value = ta.sma(length=3, close)\n",
                 "close)", id="ta"),
    pytest.param("value = math.pow(exp=3, 7)\n",
                 "7)", id="math"),
    pytest.param("value = color.new(transp=25, color.red)\n",
                 "color.red)", id="color"),
    pytest.param('value = input.int(title="Amount", 7)\n',
                 "7)", id="input"),
    pytest.param('value = request.security(syminfo.tickerid, expression=close, "60")\n',
                 '"60"', id="request"),
    pytest.param('value = request.security(syminfo.tickerid, "60", math.pow(exp=3, close))\n',
                 "close))", id="nested-request-payload"),
    pytest.param('value = str.format(str="{0}", 7)\n',
                 "7)", id="variadic"),
    pytest.param("value = timestamp(year=2024, 7, 1, 0, 0)\n",
                 "7, 1", id="calendar"),
    pytest.param('plot(title="Witness", close)\n',
                 "close)", id="discarded-visual"),
    pytest.param("unused() => math.pow(exp=3, close)\nvalue = close\n",
                 "close)", id="unused-function"),
    pytest.param("if false\n    value = math.pow(exp=3, close)\n",
                 "close)", id="dead-branch"),
    pytest.param("value = switch\n    close > open => math.pow(exp=3, close)\n    => 0.0\n",
                 "close)", id="switch-arm"),
    pytest.param("unused(float x = math.pow(exp=3, 7)) => x\n",
                 "7))", id="parameter-default"),
    pytest.param('import pftest/ArgumentOrder/1 as lib\n'
                 'value = lib.combine(right=3.0, 7.0)\n',
                 "7.0", id="library-function"),
    pytest.param('value = str.substring(\n    "ribbon",\n    end_pos=5,\n'
                 '    int(math.max(1, 2)) + 1\n)\n',
                 "int(math.max", id="multiline-compound"),
]


ACCEPTED = [
    pytest.param('value = str.substring("ribbon", 2, 5)\n', id="all-positional"),
    pytest.param('value = str.substring(end_pos=5, begin_pos=2, source="ribbon")\n',
                 id="all-named-reversed"),
    pytest.param('value = str.substring("ribbon", end_pos=5, begin_pos=2)\n',
                 id="positional-then-named"),
    pytest.param('value = str.substring("ribbon", begin_pos=2)\n', id="optional-end"),
    pytest.param('value = str.substring(source=str.tostring(math.max(12, 23)), '
                 'end_pos=2, begin_pos=0)\n', id="nested-positional-in-keyword"),
    pytest.param('value = str.substring(\n    "ribbon",\n'
                 '    end_pos=5,\n    begin_pos=int(math.max(1, 2))\n)\n',
                 id="multiline"),
    pytest.param("combine(float left, float right = 1.0) => left + right\n"
                 "value = combine(right=3.0, left=7.0)\n",
                 id="function-all-named"),
    pytest.param("combine(float left, float right = 1.0) => left + right\n"
                 "value = combine(7.0)\n", id="function-default"),
    pytest.param("method combine(float self, float left, float right = 1.0) => self + left + right\n"
                 "base = close\nvalue = base.combine(7.0, right=3.0)\n",
                 id="method-receiver"),
    pytest.param("type Pair\n    float left = 0.0\n    float right = 1.0\n"
                 "value = Pair.new(right=3.0, left=7.0)\n",
                 id="constructor-reversed"),
    pytest.param("value = array.new<float>(3, initial_value=7.0)\n",
                 id="generic-constructor"),
    pytest.param("items = array.new_float(3, 1.0)\n"
                 "items.set(1, value=3.0)\nvalue = array.get(items, index=1)\n",
                 id="collection-method-and-function"),
    pytest.param("value = nz(replacement=0, source=close)\n", id="rewritten-builtin"),
    pytest.param("value = ta.sma(length=3, source=close)\n", id="ta-reversed"),
    pytest.param('strategy.entry(direction=strategy.long, id="Order", qty=2)\n',
                 id="action-reversed"),
    pytest.param('strategy(initial_capital=1000000, title="Named", overlay=true)\n',
                 id="declaration-reversed"),
    pytest.param('value = request.security(syminfo.tickerid, "60", '
                 'expression=math.pow(close, 2))\n', id="request-nested"),
    pytest.param("unused() => ta.sma(length=3, source=close)\nvalue = close\n",
                 id="unused-function"),
    pytest.param("if false\n    value = ta.sma(close, length=3)\n",
                 id="dead-branch"),
    pytest.param("var items = array.new<float>()\nstrategy.close_all()\n",
                 id="empty-calls"),
    pytest.param("left = close\ncombine(float left, float right = 1.0) => left + right\n"
                 "if close > open\n    left = open\n"
                 "    value = combine(left, right=math.max(1, 2))\n",
                 id="shadowed-argument"),
    pytest.param('import pftest/ArgumentOrder/1 as lib\n'
                 'value = lib.combine(right=3.0, left=7.0)\n',
                 id="library-reversed"),
]


def _parse(source: str, filename: str = "arguments.pine"):
    return Parser(Lexer(source, filename=filename).tokenize(),
                  source=source, filename=filename).parse()


def _source(body: str) -> str:
    if body.startswith("strategy("):
        return "//@version=6\n" + body
    return HEADER + body


def _assert_diagnostic(error: CompileError, source: str, offending: str,
                       filename: str = "arguments.pine"):
    diagnostic, = error.diagnostics
    offset = source.rindex(offending)
    line = source.count("\n", 0, offset) + 1
    col = offset - source.rfind("\n", 0, offset)
    token = re.match(r'"[^"]*"|[A-Za-z_]\w*|\d+(?:\.\d+)?', offending).group()
    assert diagnostic.code == CODE
    assert diagnostic.message == MESSAGE
    assert diagnostic.args == {}
    assert diagnostic.hint is None
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.location.file == filename
    assert (diagnostic.location.line, diagnostic.location.col,
            diagnostic.location.end_col) == (line, col, col + len(token))
    return diagnostic


@pytest.mark.parametrize("api", [transpile, transpile_full])
@pytest.mark.parametrize("check_support", [True, False])
def test_real_script_refuses_before_emission(api, check_support):
    with pytest.raises(CompileError) as caught:
        api(REAL, filename="real.pine", check_support=check_support)
    diagnostic = _assert_diagnostic(caught.value, REAL, "1, 4", "real.pine")
    assert (diagnostic.location.line, diagnostic.location.col,
            diagnostic.location.end_col) == (5, 61, 62)


@pytest.mark.parametrize("body,offending", REFUSALS)
@pytest.mark.parametrize("check_support", [True, False])
def test_every_call_argument_list_obeys_order(body, offending, check_support):
    source = _source(body)
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="arguments.pine", check_support=check_support,
                  libraries={LIBRARY_PATH: LIBRARY})
    _assert_diagnostic(caught.value, source, offending)


@pytest.mark.parametrize("body,offending", REFUSALS)
def test_direct_analyzer_obeys_order(body, offending):
    source = _source(body)
    with pytest.raises(CompileError) as caught:
        Analyzer(_parse(source), filename="arguments.pine").analyze()
    _assert_diagnostic(caught.value, source, offending)


def test_glue_returns_a_located_error_without_cpp():
    result = json.loads(transpile_json(REAL))
    assert result["ok"] is False
    assert "cpp" not in result
    diagnostic, = result["diagnostics"]
    assert diagnostic["code"] == CODE
    assert diagnostic["severity"] == "error"
    assert diagnostic["message"] == MESSAGE
    assert diagnostic["args"] == {}
    assert (diagnostic["line"], diagnostic["col"], diagnostic["endCol"]) == (5, 61, 62)


@pytest.mark.parametrize("body", ACCEPTED)
def test_accepted_argument_order_still_transpiles(body):
    source = _source(body)
    cpp = transpile(source, libraries={LIBRARY_PATH: LIBRARY})
    full = transpile_full(source, libraries={LIBRARY_PATH: LIBRARY})
    assert cpp == full["cpp"]
    assert "class GeneratedStrategy" in cpp


@pytest.mark.parametrize("body", ACCEPTED)
def test_accepted_argument_order_still_compiles(body):
    compile_cpp(transpile(_source(body), libraries={LIBRARY_PATH: LIBRARY}),
                label="legal argument order")


def test_corrected_real_script_still_transpiles_and_compiles():
    corrected = REAL.replace('source = "quantity", 1, 4',
                             '"quantity", end_pos = 4, begin_pos = 1')
    cpp = transpile(corrected)
    assert cpp == transpile_full(corrected)["cpp"]
    assert json.loads(transpile_json(corrected))["ok"] is True
    compile_cpp(cpp, label="corrected real argument order")


def test_direct_analyzer_recovers_legacy_location_order():
    ast = _parse(REAL)
    for node, _depth in iter_ast_nodes(ast):
        if node.annotations:
            node.annotations.pop("call_arg_order", None)
    with pytest.raises(CompileError) as caught:
        Analyzer(ast, filename="arguments.pine").analyze()
    _assert_diagnostic(caught.value, REAL, "1, 4")


def test_unlocated_ast_without_recorded_order_uses_canonical_order():
    source = HEADER + 'value = str.substring("ribbon", end_pos=5, begin_pos=2)\n'
    ast = _parse(source)
    for node, _depth in iter_ast_nodes(ast):
        node.loc = None
        if node.annotations:
            node.annotations.pop("call_arg_order", None)
    Analyzer(ast).analyze()


@pytest.mark.parametrize("library_body,offending", [
    ('unused() => math.pow(exp=3, 7)\n', "7)"),
    ('library(overlay=true, "Late title")\n', '"Late title"'),
])
def test_v6_library_validates_before_unused_code_is_filtered(library_body, offending):
    source = (LIBRARY.replace('library("ArgumentOrder")\n', library_body)
              if library_body.startswith("library(") else LIBRARY + library_body)
    with pytest.raises(CompileError) as caught:
        parse_library_module(LIBRARY_PATH, source)
    _assert_diagnostic(caught.value, source, offending, LIBRARY_PATH)


def test_imported_unused_v6_library_body_is_validated():
    library = LIBRARY + "unused() => math.pow(exp=3, 7)\n"
    source = HEADER + 'import pftest/ArgumentOrder/1 as lib\nvalue = lib.combine(close)\n'
    with pytest.raises(CompileError) as caught:
        transpile(source, libraries={LIBRARY_PATH: library})
    _assert_diagnostic(caught.value, library, "7)", LIBRARY_PATH)


def test_v5_library_policy_stays_unchanged():
    # This task establishes a v6 rule; a v5 policy change needs its own evidence.
    library = LIBRARY.replace("//@version=6", "//@version=5")
    library += "export average(float x) => ta.sma(length=3, x)\n"
    assert parse_library_module(LIBRARY_PATH, library).pine_version == 5
    source = HEADER + 'import pftest/ArgumentOrder/1 as lib\nvalue = lib.average(close)\n'
    assert "class GeneratedStrategy" in transpile(source, libraries={LIBRARY_PATH: library})


def test_first_violation_uses_source_order_across_nested_lists():
    source = HEADER + "value = math.pow(base=math.pow(exp=3, close), 7)\n"
    with pytest.raises(CompileError) as caught:
        transpile(source)
    _assert_diagnostic(caught.value, source, "close)", "<input>")


def test_diagnostic_template_round_trips():
    assert classify("error", MESSAGE) == (CODE, {})
    assert render_diagnostic(CODE, {}) == (MESSAGE, None)


def test_diagnostic_template_is_extracted():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/gen_diagnostics_catalog.py"), "--list"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"error {MESSAGE}" in {
        line.split(" || ", 1)[0] for line in result.stdout.splitlines()
    }
