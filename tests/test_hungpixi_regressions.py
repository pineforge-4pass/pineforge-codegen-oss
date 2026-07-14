"""Regressions isolated from the Hungpixi MACD campaign strategy."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp, skip_if_no_compile_env


PRELUDE = '//@version=6\nstrategy("t")\n'


def _registration_line(source: str) -> str:
    return next(
        line.strip()
        for line in transpile(source).splitlines()
        if "register_security_eval(" in line
    )


def test_security_timeframe_alias_from_switch_is_registration_expression():
    source = (
        PRELUDE
        + "HTF = switch timeframe.period\n"
        + '    "1" => "5"\n'
        + '    "5" => "15"\n'
        + '    => "60"\n'
        + "value = request.security(syminfo.tickerid, HTF, close)\n"
        + "plot(value)\n"
    )

    line = _registration_line(source)

    assert "/* unknown */" not in line
    assert "script_tf_" in line
    assert 'std::string("5")' in line
    assert 'std::string("15")' in line
    assert 'std::string("60")' in line


def test_security_timeframe_switch_without_default_is_rejected():
    source = (
        PRELUDE
        + "HTF = switch timeframe.period\n"
        + '    "1" => "5"\n'
        + '    "5" => "15"\n'
        + "value = request.security(syminfo.tickerid, HTF, close)\n"
        + "plot(value)\n"
    )

    with pytest.raises(CompileError, match="timeframe switch requires a default arm"):
        transpile(source)


def test_security_timeframe_switch_with_multistatement_arm_is_rejected():
    source = (
        PRELUDE
        + "HTF = switch timeframe.period\n"
        + '    "1" =>\n'
        + '        selected = "5"\n'
        + "        selected\n"
        + '    => "60"\n'
        + "value = request.security(syminfo.tickerid, HTF, close)\n"
        + "plot(value)\n"
    )

    with pytest.raises(CompileError, match="switch arms must contain one expression"):
        transpile(source)


def test_closed_trade_entry_id_local_is_string_typed():
    source = (
        PRELUDE
        + "longCount = 0\n"
        + "if strategy.closedtrades > 0\n"
        + "    for i = 0 to strategy.closedtrades - 1\n"
        + "        entryId = strategy.closedtrades.entry_id(i)\n"
        + '        if entryId == "Long"\n'
        + "            longCount += 1\n"
        + "plot(longCount)\n"
    )

    cpp = transpile(source)

    assert "std::string entryId = closed_trade_entry_id(i);" in cpp
    assert "double entryId = closed_trade_entry_id(i);" not in cpp


def test_trade_text_accessors_are_string_typed_without_retyping_numeric_accessors():
    source = (
        PRELUDE
        + "if strategy.closedtrades > 0\n"
        + "    for i = 0 to strategy.closedtrades - 1\n"
        + "        closedEntryId = strategy.closedtrades.entry_id(i)\n"
        + "        closedExitId = strategy.closedtrades.exit_id(i)\n"
        + "        closedEntryComment = strategy.closedtrades.entry_comment(i)\n"
        + "        closedExitComment = strategy.closedtrades.exit_comment(i)\n"
        + "        closedEntryBar = strategy.closedtrades.entry_bar_index(i)\n"
        + "        closedProfit = strategy.closedtrades.profit(i)\n"
        + "if strategy.opentrades > 0\n"
        + "    for i = 0 to strategy.opentrades - 1\n"
        + "        openEntryId = strategy.opentrades.entry_id(i)\n"
        + "        openEntryComment = strategy.opentrades.entry_comment(i)\n"
        + "plot(close)\n"
    )

    cpp = transpile(source)

    expected_strings = (
        "std::string closedEntryId = closed_trade_entry_id(i);",
        "std::string closedExitId = closed_trade_exit_id(i);",
        "std::string closedEntryComment = closed_trade_entry_comment(i);",
        "std::string closedExitComment = closed_trade_exit_comment(i);",
        "std::string openEntryId = open_trade_entry_id(i);",
        "std::string openEntryComment = open_trade_entry_comment(i);",
    )
    for declaration in expected_strings:
        assert declaration in cpp

    # Numeric accessor declarations are intentionally outside this fix.
    assert "double closedEntryBar = closed_trade_entry_bar_index(i);" in cpp
    assert "double closedProfit = closed_trade_profit(i);" in cpp


def test_hungpixi_shapes_compile_against_engine_headers():
    skip_if_no_compile_env()
    source = (
        PRELUDE
        + "HTF = switch timeframe.period\n"
        + '    "1" => "5"\n'
        + '    "5" => "15"\n'
        + '    => "60"\n'
        + "value = request.security(syminfo.tickerid, HTF, close)\n"
        + "if strategy.closedtrades > 0\n"
        + "    for i = 0 to strategy.closedtrades - 1\n"
        + "        entryId = strategy.closedtrades.entry_id(i)\n"
        + '        if entryId == "Long"\n'
        + "            value += 1\n"
        + "plot(value)\n"
    )

    compile_cpp(transpile(source), label="hungpixi-switch-tf-trade-id")
