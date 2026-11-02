"""Known empty timeframe selection arms stop translation pending value evidence."""

import json
from pathlib import Path

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.ast_nodes import StringLiteral
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.limits import iter_ast_nodes
from pineforge_codegen.parser import Parser


SOURCES = (
    ('//@version=6\nstrategy("Conditional request target")\n'
     'empty = input.bool(true, "Empty")\n'
     'tf = if empty\n'
     '    ""\n'
     'else\n'
     '    "240"\n'
     'value = request.security(syminfo.tickerid, tf, ta.sma(close, 3))\n'
     'plot(value)\n', 5, 5),
    ('//@version=6\nstrategy("Block switch request target")\n'
     'empty = input.bool(true, "Empty")\n'
     'tf = switch empty\n'
     '    true =>\n'
     '        selectedTarget = ""\n'
     '        selectedTarget\n'
     '    =>\n'
     '        selectedTarget = "240"\n'
     '        selectedTarget\n'
     'value = request.security(syminfo.tickerid, tf, ta.sma(close, 3))\n'
     'plot(value)\n', 6, 26),
    ('//@version=6\nstrategy("String selector block request target")\n'
     'routeChoice = input.string("chart", "Route", options = ["chart", "explicit"])\n'
     'chosenInterval = switch routeChoice\n'
     '    "chart" =>\n'
     '        armInterval = ""\n'
     '        armInterval\n'
     '    =>\n'
     '        armInterval = "240"\n'
     '        armInterval\n'
     'observedMean = request.security(syminfo.tickerid, chosenInterval, ta.sma(close, 3))\n'
     'plot(observedMean)\n', 6, 23),
)


@pytest.fixture
def protocol():
    root = Path(__file__).resolve().parent.parent
    namespace = {}
    glue = (root / "gate" / "glue.py").read_text(encoding="utf-8")
    exec(compile(glue.replace('"/codegen"', repr(str(root))), "glue.py", "exec"),
         namespace)
    return lambda source: json.loads(namespace["transpile_json"](source))


@pytest.mark.parametrize("pine,line,col", SOURCES)
def test_empty_timeframe_selection_arm_refuses_at_literal(protocol, pine, line, col):
    # Red before the guard: an empty selected timeframe has no measured TV value.
    # Columns count indentation plus the local declaration prefix, if present.
    ast = Parser(Lexer(pine).tokenize(), source=pine,
                 filename="empty_timeframe.pine").parse()
    literal, = (node for node, _ in iter_ast_nodes(ast)
                if isinstance(node, StringLiteral) and node.value == "")
    assert (literal.loc.line, literal.loc.col) == (line, col)
    for translate in (transpile, transpile_full):
        with pytest.raises(CompileError) as caught:
            translate(pine, filename="empty_timeframe.pine")
        diagnostic, = caught.value.diagnostics
        assert diagnostic.code == "PF-E3105"
        assert diagnostic.level is Level.ERROR
        assert diagnostic.phase is Phase.CODEGEN
        assert diagnostic.location.file == "empty_timeframe.pine"
        assert (diagnostic.location.line, diagnostic.location.col) == (line, col)
        assert diagnostic.location == literal.loc
    envelope = protocol(pine)
    assert not envelope["ok"] and "cpp" not in envelope
    diagnostic, = envelope["diagnostics"]
    assert (diagnostic["code"], diagnostic["line"], diagnostic["col"]) == (
        "PF-E3105", line, col,
    )


@pytest.mark.parametrize("pine,line,col", SOURCES)
def test_nonempty_timeframe_selection_arm_keeps_translating(pine, line, col):
    # Preservation control: replacing only the empty arm by "60" avoids the guard.
    assert isinstance(transpile(pine.replace('""', '"60"')), str)


def test_empty_scalar_ternary_timeframe_keeps_translating():
    pine = ('//@version=6\nstrategy("Scalar ternary target")\n'
            'pick = input.bool(true, "Pick")\n'
            'tf = pick ? "" : "240"\n'
            'value = request.security(syminfo.tickerid, tf, ta.sma(close, 3))\n'
            'plot(value)\n')
    # Preservation control: a scalar ternary never enters an if/switch arm.
    assert isinstance(transpile(pine), str)


@pytest.mark.parametrize("selection", (
    'if pick\n    ""\nelse\n    "240"',
    'switch pick\n    true => ""\n    => "240"',
    'if pick\n    "60"\nelse\n    ""',
    'switch pick\n    true => "60"\n    => ""',
    'if pick\n    localTf = ""\n    localTf\nelse\n    "240"',
    'if pick\n    string localTf = ""\n    localTf\nelse\n    "240"',
    'if pick\n    localTf = ""\n    switch pick\n        true => localTf\n        => "240"\nelse\n    "60"',
))
def test_bound_and_aliased_selections_share_empty_arm_guard(selection):
    setup = '//@version=6\nstrategy("Selection aliases")\npick = input.bool(true)\n'
    for prefix, timeframe in (
        (f"chosen = {selection}\n", "chosen"),
        (f"chosen = {selection}\naliasTf = chosen\n", "aliasTf"),
    ):
        pine = (setup + prefix +
                f'value = request.security(syminfo.tickerid, {timeframe}, close)\n'
                'plot(value)\n')
        # Red before the guard: binding aliases and local lets keep the empty value.
        with pytest.raises(CompileError) as caught:
            transpile(pine)
        assert caught.value.diagnostics[0].code == "PF-E3105"


def test_runtime_timeframe_arm_is_not_assumed_empty():
    pine = ('//@version=6\nstrategy("Runtime timeframe arm")\n'
            'pick = input.bool(true)\nrequestedTf = input.timeframe("")\n'
            'tf = if pick\n    chosenTf = requestedTf\n    chosenTf\n'
            'else\n    "240"\n'
            'value = request.security(syminfo.tickerid, tf, close)\nplot(value)\n')
    # Preservation control: an input read is not a proven empty constant.
    assert isinstance(transpile(pine), str)


def test_nonempty_typed_local_timeframe_arm_keeps_translating():
    pine = ('//@version=6\nstrategy("Typed timeframe arm")\npick = input.bool(true)\n'
            'tf = if pick\n    string localTf = "60"\n    localTf\n'
            'else\n    "240"\n'
            'value = request.security(syminfo.tickerid, tf, close)\nplot(value)\n')
    # Preservation control: a string identity cast does not make "60" empty.
    assert isinstance(transpile(pine), str)


@pytest.mark.parametrize("selection", (
    'if pick\n    inheritedTf\nelse\n    "240"',
    'switch pick\n    true => inheritedTf\n    => "240"',
))
def test_constant_timeframe_arm_is_followed(selection):
    pine = ('//@version=6\nstrategy("Constant timeframe arm")\n'
            'pick = input.bool(true)\nbaseTf = ""\ninheritedTf = baseTf\n'
            f'tf = {selection}\n'
            'value = request.security(syminfo.tickerid, tf, close)\nplot(value)\n')
    # Red before the guard: constant aliases still select the empty timeframe.
    with pytest.raises(CompileError) as caught:
        transpile(pine)
    assert caught.value.diagnostics[0].code == "PF-E3105"
