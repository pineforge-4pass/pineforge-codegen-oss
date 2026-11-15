"""Timeframe argument typing and selection, with opt-in full-TU checks."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.diagnostic_codes import render_diagnostic
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser


def engine_call(function, argument):
    if function == "change":
        return (
            "tf_change(prev_bar_timestamp_, current_bar_.timestamp, "
            + argument + ", syminfo_.timezone, syminfo_.session)"
        )
    return "tf_to_seconds(" + argument + ")"


@pytest.mark.parametrize("function", ["change", "in_seconds"])
@pytest.mark.parametrize("argument,lowered", [
    ('""', 'std::string("")'),
    ('"240"', 'std::string("240")'),
    ("frameChoice", "frameChoice"),
])
def test_empty_plain_argument_selects_chart_and_nonempty_selects_argument(function, argument, lowered):
    cpp = transpile(
        '//@version=6\nstrategy("Chart timeframe selection")\n'
        'frameChoice = input.timeframe("")\n'
        f"value = timeframe.{function}({argument})\n"
    )
    expected = (
        "((" + lowered + ").empty() ? "
        + engine_call(function, "script_tf_") + " : "
        + engine_call(function, lowered) + ")"
    )
    assert expected in cpp


@pytest.mark.parametrize("function", ["change", "in_seconds"])
def test_call_argument_is_evaluated_once_before_chart_selection(function):
    cpp = transpile(
        '//@version=6\nstrategy("Chart timeframe selection")\n'
        'frameChoice = input.timeframe("")\n'
        'chosenFrame() => frameChoice\n'
        f"value = timeframe.{function}(chosenFrame())\n"
    )
    assert "const std::string _pf_timeframe_arg = " in cpp
    selected = "(_pf_timeframe_arg.empty() ? script_tf_ : _pf_timeframe_arg)"
    assert engine_call(function, selected) in cpp
    assert cpp.count("const std::string _pf_timeframe_arg = (chosenFrame());") == 1


@pytest.mark.parametrize("function", ["change", "in_seconds"])
@pytest.mark.parametrize("named", [False, True], ids=["positional", "named"])
def test_na_argument_uses_string_missing_value_before_chart_selection(function, named):
    argument = "timeframe = na" if named else "na"
    cpp = transpile(
        '//@version=6\nstrategy("Missing timeframe")\n'
        f"value = timeframe.{function}({argument})\n"
    )
    missing = "na<std::string>()"
    expected = (
        "((" + missing + ").empty() ? "
        + engine_call(function, "script_tf_") + " : "
        + engine_call(function, missing) + ")"
    )
    assert expected in cpp
    assert "(na<double>()).empty()" not in cpp


@pytest.mark.parametrize("function", ["change", "in_seconds"])
@pytest.mark.parametrize("parameter", ["frameArg", "string frameArg"])
def test_helper_string_parameter_keeps_chart_selection(function, parameter):
    cpp = transpile(
        '//@version=6\nstrategy("Helper timeframe")\n'
        f"withFrame({parameter}) => timeframe.{function}(frameArg)\n"
        'value = withFrame("")\n'
    )
    expected = (
        "((frameArg).empty() ? "
        + engine_call(function, "script_tf_") + " : "
        + engine_call(function, "frameArg") + ")"
    )
    assert expected in cpp


@pytest.mark.parametrize("function", ["change", "in_seconds"])
@pytest.mark.parametrize("named", [False, True], ids=["positional", "named"])
@pytest.mark.parametrize("argument,pine_type", [
    ("0", "int"),
    ("17", "int"),
    ("-3", "int"),
    ("0.0", "float"),
    ("1.25", "float"),
    ("-2.5", "float"),
])
def test_numeric_timeframe_is_refused_during_analysis(function, named, argument, pine_type):
    supplied = f"timeframe = {argument}" if named else argument
    source = (
        '//@version=6\nstrategy("Numeric timeframe")\n'
        f"value = timeframe.{function}({supplied})\n"
    )
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    with pytest.raises(CompileError) as refused:
        Analyzer(ast).analyze()
    diagnostic, = refused.value.diagnostics
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == "PF-E2095"
    assert diagnostic.args == {
        "function_name": f"timeframe.{function}",
        "name": "timeframe",
        "argument_type": pine_type,
        "parameter_type": "string",
    }
    assert diagnostic.message == (
        f"Cannot call 'timeframe.{function}' with a {pine_type} "
        "argument for string parameter 'timeframe'."
    )
    assert diagnostic.hint is None
    assert render_diagnostic(diagnostic.code, diagnostic.args) == (
        diagnostic.message, diagnostic.hint,
    )
    assert diagnostic.location.line == 3
    assert diagnostic.location.col == source.splitlines()[2].index(argument) + 1


@pytest.mark.parametrize("function", ["change", "in_seconds"])
@pytest.mark.parametrize("named", [False, True], ids=["positional", "named"])
@pytest.mark.parametrize("optimization", ["-O0", "-O2"])
def test_na_timeframe_full_translation_unit_compiles(function, named, optimization):
    from tests._compile import compile_cpp

    argument = "timeframe = na" if named else "na"
    cpp = transpile(
        '//@version=6\nstrategy("Missing timeframe compilation")\n'
        f"value = timeframe.{function}({argument})\n"
    )
    compile_cpp(cpp, label=f"timeframe.{function}({argument}) {optimization}",
                extra_flags=[optimization])
