"""Sizing constants keep their Pine values through live emitted consumers."""

import re

import pytest

from pineforge_codegen import signatures as sigs, transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.codegen.helpers import CPP_TEMPORARY_NAMES
from pineforge_codegen.errors import CompileError
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.symbols import PineType, TypeSpec


SIZING_MEMBERS = ("fixed", "cash", "percent_of_equity")
HEADER = '//@version=6\nstrategy("Sizing consumers")\n'


def _source_body(cpp):
    match = re.search(
        r"void on_source_bar\([^)]*\) override \{(.*?)\n    \}",
        cpp, re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def _engine_call(function, argument):
    if function == "change":
        return (
            "tf_change(prev_bar_timestamp_, current_bar_.timestamp, "
            f"{argument}, syminfo_.timezone, syminfo_.session)"
        )
    return f"tf_to_seconds({argument})"


def _live_timeframe_source(member, function, form):
    argument = f"strategy.{member}"
    declarations = ""
    supplied = f"timeframe = {argument}" if form == "named" else argument
    call = f"timeframe.{function}({supplied})"
    if form in ("helper", "named-helper"):
        supplied = "timeframe = frameArg" if form == "named-helper" else "frameArg"
        declarations = f"withFrame(frameArg) => timeframe.{function}({supplied})\n"
        call = f"withFrame({argument})"
    elif form == "alias":
        declarations = f"frameMode = {argument}\n"
        call = f"timeframe.{function}(frameMode)"
    elif form == "return":
        declarations = f"chosenFrame() => {argument}\n"
        call = f"timeframe.{function}(chosenFrame())"
    value = f"{call} ? 1 : 0" if function == "change" else call
    return (
        HEADER + declarations + f"sampleValue = {value}\n"
        "if sampleValue > 0\n"
        '    strategy.entry("L", strategy.long)\n'
    )


@pytest.mark.parametrize("member", SIZING_MEMBERS)
@pytest.mark.parametrize("function", ("change", "in_seconds"))
@pytest.mark.parametrize(
    "form", ("direct", "named", "helper", "named-helper", "alias", "return"),
)
def test_sizing_timeframe_arguments_reach_live_string_lowering(member, function, form):
    cpp = transpile(_live_timeframe_source(member, function, form))
    body = _source_body(cpp)
    assert re.search(r"\bsampleValue\s*=", body)
    assert re.search(r"if \([^;]*\bsampleValue\b", body)
    assert 'strategy_entry(std::string("L"), true' in body
    literal = f'std::string("{member}")'
    if form in ("helper", "named-helper"):
        assert f"withFrame({literal})" in body
        assert re.search(r"\bwithFrame\(std::string frameArg\)", cpp)
        assert _engine_call(function, "frameArg") in cpp
        assert "(frameArg).empty()" in cpp
    elif form == "alias":
        assert f"frameMode = {literal};" in body
        assert _engine_call(function, "frameMode") in body
        assert "(frameMode).empty()" in body
    elif form == "return":
        assert re.search(r"\bstd::string chosenFrame\(", cpp)
        assert f"return {literal};" in cpp
        assert "const std::string _pf_timeframe_arg = (chosenFrame());" in body
        assert _engine_call(
            function, "(_pf_timeframe_arg.empty() ? script_tf_ : _pf_timeframe_arg)",
        ) in body
    else:
        assert _engine_call(function, literal) in body
        assert f"({literal}).empty()" in body
    assert not re.search(r"\(\s*(?:[+-]?\d+(?:\.\d*)?|true|false)\s*\)\.empty\(", cpp)


@pytest.mark.parametrize("member", SIZING_MEMBERS)
@pytest.mark.parametrize("consumer", ("concat", "equality", "selection", "collection"))
def test_sizing_strings_keep_their_type_in_other_live_consumers(member, consumer):
    if consumer == "concat":
        declaration = f'sampleName = "Sizing: " + strategy.{member}\n'
    elif consumer == "equality":
        declaration = (
            f'rightMode = input.string("{member}")\n'
            f'sampleName = strategy.{member} == rightMode ? "equal" : "different"\n'
        )
    elif consumer == "selection":
        declaration = (
            "chooseMode = input.bool(true)\n"
            f'sampleName = chooseMode ? strategy.{member} : "other"\n'
        )
    else:
        declaration = (
            f'modes = array.from(strategy.{member}, "other")\n'
            "sampleName = array.get(modes, input.int(0))\n"
        )
    cpp = transpile(
        HEADER + declaration + "if barstate.islast\n"
        "    strategy.entry(sampleName, strategy.long)\n"
    )
    body = _source_body(cpp)
    assert f'std::string("{member}")' in body
    assert "strategy_entry(sampleName, true" in body
    assert re.search(r"\bstd::string sampleName\b", cpp)
    if consumer == "collection":
        assert "std::vector<std::string>" in cpp


@pytest.mark.parametrize("member,host_value", (("fixed", 0), ("percent_of_equity", 1), ("cash", 2)))
def test_host_qty_boundary_preserves_codes_without_changing_pine_values(member, host_value):
    cpp = transpile(
        HEADER + f"modeName = strategy.{member}\n"
        f'strategy.entry(modeName, strategy.long, qty=1, qty_type=strategy.{member})\n'
    )
    body = _source_body(cpp)
    assert f'modeName = std::string("{member}");' in body
    assert f", {host_value})" in body
    assert sigs.get_variable_type("strategy", member) == PineType.STRING
    assert sigs.STRATEGY_VARIABLES[f"strategy.{member}"] == PineType.STRING
    assert sigs.get_variable_type_spec("strategy", member) == TypeSpec.primitive("string")


@pytest.mark.parametrize("consumer", ("order", "risk"))
def test_computed_sizing_strings_convert_once_at_numeric_host_boundaries(consumer):
    source = (
        HEADER + "chooseMode = input.bool(true)\n"
        "chosenMode() => chooseMode ? strategy.cash : strategy.percent_of_equity\n"
    )
    if consumer == "order":
        source += 'strategy.entry("L", strategy.long, qty=1, qty_type=chosenMode())\n'
    else:
        source += (
            "strategy.risk.max_drawdown(10, chosenMode())\n"
            'strategy.entry("L", strategy.long)\n'
        )
    body = _source_body(transpile(source))
    assert body.count("const std::string _pf_qty_type = (chosenMode());") == 1
    assert '_pf_qty_type == std::string("cash")' in body
    assert '_pf_qty_type == std::string("percent_of_equity")' in body
    assert "strategy_entry(" in body
    if consumer == "risk":
        assert "set_pine_risk_max_drawdown(" in body


@pytest.mark.parametrize("namespace,member", (
    ("strategy", "long"), ("strategy", "short"),
    ("strategy.commission", "percent"),
    ("strategy.commission", "cash_per_contract"),
    ("strategy.commission", "cash_per_order"),
    ("strategy.oca", "none"), ("strategy.oca", "cancel"), ("strategy.oca", "reduce"),
    *(("display", member) for member in (
        "all", "none", "pane", "data_window", "status_line", "price_scale",
    )),
))
def test_legacy_constant_storage_is_not_a_semantic_operator_proof(namespace, member):
    assert sigs.get_variable_type(namespace, member) != PineType.UNKNOWN
    assert sigs.get_variable_type_spec(namespace, member) is None


@pytest.mark.parametrize("version", (5, 6))
def test_authored_namespace_receiver_keeps_its_field_type(version):
    if version == 5:
        libraries = {"pftest/SizingReceiver/1": (
            '//@version=5\nlibrary("SizingReceiver")\n'
            "export type Mode\n    int cash\n"
            "export readMode(Mode strategy) => strategy.cash\n"
        )}
        source = (
            HEADER + "import pftest/SizingReceiver/1 as L\n"
            "sampleValue = L.readMode(L.Mode.new(3))\n"
            'sizingText = "Sizing: " + strategy.cash\n'
            "if sampleValue > 0\n"
            '    strategy.entry("L", strategy.long)\n'
        )
        cpp = transpile(source, libraries=libraries)
    else:
        cpp = transpile(
            f'//@version={version}\nstrategy("Sizing receiver")\n'
            "type Mode\n    int cash\n"
            "readMode(Mode strategy) => strategy.cash\n"
            "sampleValue = readMode(Mode.new(3))\n"
            'sizingText = "Sizing: " + strategy.cash\n'
            "if sampleValue > 0\n"
            '    strategy.entry("L", strategy.long)\n'
        )
    body = _source_body(cpp)
    assert ".cash" in cpp
    assert re.search(r"sizingText\s*=.*cash", body)
    assert "strategy_entry(" in body


def test_computed_qty_type_temporary_is_reserved():
    source = (
        HEADER +
        'qtyMode = input.string("cash", options = ["cash", "percent_of_equity"])\n'
        'strategy.entry("L", strategy.long, qty = 1, qty_type = qtyMode)\n'
    )
    cpp = transpile(source)
    assert "_pf_qty_type" in cpp
    assert "_pf_qty_type" in CPP_TEMPORARY_NAMES


def test_ordinary_live_numeric_control_keeps_its_operands():
    body = _source_body(transpile(
        HEADER + "sampleValue = close + open\n"
        "if sampleValue > 0\n"
        '    strategy.entry("L", strategy.long)\n'
    ))
    assert "(current_bar_.close + current_bar_.open)" in body
    assert "sampleValue" in body
    assert "strategy_entry(" in body


@pytest.mark.parametrize("function", ("change", "in_seconds"))
def test_open_numeric_domain_is_not_reported_as_a_guessed_float(function):
    source = HEADER + f"value = timeframe.{function}(math.round(close))\n"
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    with pytest.raises(CompileError) as refused:
        Analyzer(ast).analyze()
    diagnostic, = refused.value.diagnostics
    assert diagnostic.code == "PF-E2095"
    assert diagnostic.args["argument_type"] == "number"
    assert str(TypeSpec.primitive("number")) == "number"
