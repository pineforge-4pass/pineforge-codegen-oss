"""Timeframe string parameters use semantic types before C++ conversion."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.diagnostic_codes import render_diagnostic
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser


HEADER = '//@version=6\nstrategy("Timeframe argument types")\n'
NON_STRINGS = [
    pytest.param("frames = input.int(14)\n", "frames", "int", id="integer-input"),
    pytest.param("", "input.int(7)", "int", id="inline-integer-input"),
    pytest.param("counter = 0\ncounter := counter + 1\n", "counter", "int",
                 id="reassigned-counter"),
    pytest.param("", "syminfo.mintick", "float", id="member-value"),
    pytest.param("", "timeframe.multiplier", "int", id="integer-timeframe-member"),
    pytest.param("", "timeframe.isdaily", "bool", id="boolean-timeframe-member"),
    pytest.param("", "strategy.closedtrades", "int", id="integer-strategy-member"),
    pytest.param("", "barstate.isfirst", "bool", id="boolean-barstate-member"),
    pytest.param("", "ta.sma(close, 3)", "float", id="indicator-result"),
    pytest.param("", "input.float(2.25)", "float", id="float-input"),
    pytest.param("", "true", "bool", id="boolean-literal"),
    pytest.param("", "close > open", "bool", id="boolean-series"),
    pytest.param("flag = input.bool(true)\n", "flag", "bool", id="boolean-input"),
    pytest.param("flag = false\nflag := close > open\n", "flag", "bool",
                 id="reassigned-boolean"),
    pytest.param("", "color.red", "color", id="color"),
    pytest.param("frames = array.new<int>(2, 1)\n", "frames", "array<int>",
                 id="array"),
    pytest.param("frames = matrix.new<float>(1, 1, 2.0)\n", "frames",
                 "matrix<float>", id="matrix"),
    pytest.param("frames = map.new<string, int>()\n", "frames",
                 "map<string,int>", id="map"),
    pytest.param('type FrameBox\n    string frame\n'
                 'boxValue = FrameBox.new("60")\n', "boxValue", "FrameBox",
                 id="object"),
]


def _assert_type_diagnostic(diagnostic, function, pine_type):
    assert diagnostic.level == Level.ERROR
    assert diagnostic.code == "PF-E2095"
    assert diagnostic.args == {
        "function_name": f"timeframe.{function}",
        "name": "timeframe",
        "argument_type": pine_type,
        "parameter_type": "string",
    }
    assert diagnostic.hint is None
    assert render_diagnostic(diagnostic.code, diagnostic.args) == (
        diagnostic.message, diagnostic.hint,
    )


@pytest.mark.parametrize("function", ["change", "in_seconds"])
@pytest.mark.parametrize("named", [False, True], ids=["positional", "named"])
@pytest.mark.parametrize("scope", [
    "direct", "helper", "helper-local", "named-helper", "typed-helper",
])
@pytest.mark.parametrize("conversion", [False, True], ids=["alone", "bool-to-text"])
@pytest.mark.parametrize("declarations,argument,pine_type", NON_STRINGS)
def test_non_string_timeframes_are_refused_by_semantic_type(
    function, named, scope, conversion, declarations, argument, pine_type,
):
    source = HEADER + declarations
    if conversion:
        source += "unrelatedString = str.tostring(close > open)\n"
    value = argument if scope == "direct" else "frameArg"
    if scope == "helper-local":
        value = "localFrame"
    supplied = f"timeframe = {value}" if named else value
    call = f"timeframe.{function}({supplied})"
    if scope == "direct":
        source += f"value = {call}\n"
    elif scope in ("helper", "named-helper", "typed-helper"):
        parameter = f"{pine_type} frameArg" if scope == "typed-helper" else "frameArg"
        actual = f"frameArg = {argument}" if scope == "named-helper" else argument
        source += f"withFrame({parameter}) => {call}\nvalue = withFrame({actual})\n"
    else:
        source += (
            "withFrame(frameArg) =>\n"
            "    localFrame = frameArg\n"
            f"    {call}\n"
            f"value = withFrame({argument})\n"
        )
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    with pytest.raises(CompileError) as refused:
        Analyzer(ast).analyze()
    diagnostic, = refused.value.diagnostics
    _assert_type_diagnostic(diagnostic, function, pine_type)
    assert diagnostic.phase == Phase.ANALYZER
    line, = [(index, line) for index, line in enumerate(source.splitlines(), 1)
             if call in line]
    assert diagnostic.location.line == line[0]
    assert diagnostic.location.col == line[1].index(supplied, line[1].index(call)) + (
        len("timeframe = ") if named else 0
    ) + 1


@pytest.mark.parametrize("function", ["change", "in_seconds"])
@pytest.mark.parametrize("named", [False, True], ids=["positional", "named"])
@pytest.mark.parametrize("conversion", [False, True], ids=["alone", "bool-to-text"])
@pytest.mark.parametrize("body,pine_type", [
    ("input.int(14)", "int"),
    ("syminfo.mintick", "float"),
    ("close > open", "bool"),
])
def test_helper_return_cannot_be_converted_to_a_timeframe_string(
    function, named, conversion, body, pine_type,
):
    source = HEADER
    if conversion:
        source += "unrelatedString = str.tostring(close > open)\n"
    source += f"chosenFrame() => {body}\n"
    supplied = "timeframe = chosenFrame()" if named else "chosenFrame()"
    source += f"value = timeframe.{function}({supplied})\n"
    with pytest.raises(CompileError) as refused:
        transpile(source)
    diagnostic, = refused.value.diagnostics
    _assert_type_diagnostic(diagnostic, function, pine_type)
    assert diagnostic.phase in (Phase.ANALYZER, Phase.CODEGEN)
    assert diagnostic.location.line == len(source.splitlines())
    assert diagnostic.location.col == source.splitlines()[-1].index("chosenFrame()") + 1


@pytest.mark.parametrize("function", ["change", "in_seconds"])
@pytest.mark.parametrize("named", [False, True], ids=["positional", "named"])
@pytest.mark.parametrize("argument,pine_type", [
    ("input.int(14)", "int"),
    ("syminfo.mintick", "float"),
    ("true", "bool"),
])
def test_emitter_checks_type_before_implicit_string_conversion(
    monkeypatch, function, named, argument, pine_type,
):
    # An internal caller can reach emission after bypassing an analyzer guard.
    # The emitter must still refuse the semantic type before bool text lowering.
    monkeypatch.setattr(Analyzer, "_check_timeframe_arguments",
                        lambda *args, **kwargs: None, raising=False)
    supplied = f"timeframe = {argument}" if named else argument
    source = (
        HEADER + "unrelatedString = str.tostring(close > open)\n"
        f"value = timeframe.{function}({supplied})\n"
    )
    with pytest.raises(CompileError) as refused:
        transpile(source)
    diagnostic, = refused.value.diagnostics
    _assert_type_diagnostic(diagnostic, function, pine_type)
    assert diagnostic.phase == Phase.CODEGEN
    assert diagnostic.location.line == 4
    assert diagnostic.location.col == source.splitlines()[3].index(argument) + 1
