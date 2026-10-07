"""The function overload reads its supplied symbol; the variable reads the chart."""

import re
import subprocess

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.helpers import NamingHelper
from pineforge_codegen.pine_spelling import pine_string_literal
from tests._compile import _COMPILER, compile_cpp

PRELUDE = '//@version=6\nstrategy("prefix probe")\n'
SYMBOLS = [
    ("NASDAQ:AAPL", "NASDAQ", "AAPL"),
    ("BINANCE:BTCUSDT", "BINANCE", "BTCUSDT"),
    ("", "", ""),
    ("AAPL", "", "AAPL"),
    (":AAPL", "", "AAPL"),
    ("NASDAQ:", "", ""),
    ("FIRST:SECOND:THIRD", "FIRST", "SECOND:THIRD"),
    ("交易所:股票", "交易所", "股票"),
]


@pytest.mark.parametrize("function", ["prefix", "ticker"])
@pytest.mark.parametrize("symbol, expected_prefix, expected_ticker", SYMBOLS)
@pytest.mark.parametrize("keyword", [False, True], ids=["positional", "keyword"])
def test_symbol_function_emits_its_symbol_argument(function, symbol, expected_prefix, expected_ticker, keyword):
    argument = pine_string_literal(symbol)
    if keyword:
        argument = "symbol=" + argument
    cpp = transpile(PRELUDE + f"prefixValue = syminfo.{function}({argument})\nplot(str.length(prefixValue))\n")
    escaped = NamingHelper._cpp_string_escape(symbol)
    (expression,) = re.findall(r"^\s+prefixValue = ([^\n]+);", cpp, re.MULTILINE)
    assert expression.startswith("([](const auto& _pf_symbol)")
    assert expression.endswith(f')(std::string("{escaped}"))')


@pytest.mark.parametrize("function", ["prefix", "ticker"])
@pytest.mark.parametrize("argument, emitted", [
    ("syminfo.tickerid", "syminfo_.tickerid"),
    ('input.symbol("NASDAQ:AAPL", "Symbol")', 'get_input_string("Symbol"'),
    ('close > open ? "NASDAQ:AAPL" : "BINANCE:BTCUSDT"', "current_bar_.close"),
])
def test_symbol_function_keeps_runtime_symbol_expressions(function, argument, emitted):
    cpp = transpile(PRELUDE + f"prefixValue = syminfo.{function}({argument})\nplot(str.length(prefixValue))\n")
    (expression,) = re.findall(r"^\s+prefixValue = ([^\n]+);", cpp, re.MULTILINE)
    if argument == "syminfo.tickerid" and function == "ticker":
        assert expression == "syminfo_.ticker"
    else:
        assert emitted in expression
    if argument != "syminfo.tickerid":
        assert expression not in {"_pf_derive_prefix(syminfo_.tickerid)", "syminfo_.ticker"}
        if argument.startswith("input.symbol"):
            assert expression.count('get_input_string("Symbol"') == 1


@pytest.mark.parametrize("function", ["prefix", "ticker"])
def test_symbol_variable_and_chart_symbol_function_are_identical(function):
    variable = transpile(PRELUDE + f"prefixValue = syminfo.{function}\n")
    chart = transpile(PRELUDE + f"prefixValue = syminfo.{function}(syminfo.tickerid)\n")
    legacy = transpile(PRELUDE + f"prefixValue = syminfo.{function}()\n")
    assert variable == chart == legacy


@pytest.mark.parametrize("function", ["prefix", "ticker"])
@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_symbol_literal_expressions_return_the_expected_parts(function, opt, tmp_path):
    if _COMPILER is None:
        pytest.skip("C++ compiler unavailable")
    body = "".join(f"prefixValue{index} = syminfo.{function}({pine_string_literal(symbol)})\n"
                   for index, (symbol, _prefix, _ticker) in enumerate(SYMBOLS))
    body += f"prefixValue{len(SYMBOLS)} = syminfo.{function}(syminfo.tickerid)\n"
    cpp = transpile(PRELUDE + body)
    expressions = re.findall(r"^\s+prefixValue\d+ = ([^\n]+);", cpp, re.MULTILINE)
    assert len(expressions) == len(SYMBOLS) + 1
    helper = re.search(r"static inline std::string _pf_derive_prefix\(.*?\n\}", cpp, re.DOTALL)
    assert helper is not None
    helper_text = helper.group() if function == "prefix" else ""
    driver = ('#include <iostream>\n#include <string>\n#include <type_traits>\n' + helper_text
              + '\nstruct { std::string tickerid; std::string ticker; } syminfo_{"CHART:SYMBOL", "SYMBOL"};\nint main() {\n') + "".join(
        f"std::cout << {expression} << '\\n';\n" for expression in expressions) + "}\n"
    source = tmp_path / "prefix.cpp"
    executable = tmp_path / "prefix"
    source.write_text(driver, encoding="utf-8")
    build = subprocess.run([_COMPILER, "-std=c++17", "-Wall", "-Werror", opt,
                            str(source), "-o", str(executable)],
                           capture_output=True, text=True, timeout=60)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stderr
    expected = ([prefix for _symbol, prefix, _ticker in SYMBOLS] + ["CHART"]
                if function == "prefix" else
                [ticker for _symbol, _prefix, ticker in SYMBOLS] + ["SYMBOL"])
    assert run.stdout.splitlines() == expected


@pytest.mark.parametrize("function", ["prefix", "ticker"])
@pytest.mark.parametrize("declaration, argument", [
    ("", "na"),
    ("string symbol = na\n", "symbol"),
    ("var string symbol = na\nsymbol := syminfo.tickerid\n", "symbol"),
    ("", 'close > open ? "NASDAQ:AAPL" : na'),
])
def test_symbol_missing_value_arguments_still_compile(function, declaration, argument):
    cpp = transpile(PRELUDE + declaration + f"prefixValue = syminfo.{function}({argument})\n")
    compile_cpp(cpp, label=f"syminfo_{function}_missing", standard="c++20")
