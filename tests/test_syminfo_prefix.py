"""The function overload reads its supplied symbol; the variable reads the chart."""

import re
import subprocess

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.helpers import NamingHelper
from pineforge_codegen.pine_spelling import pine_string_literal
from tests._compile import _COMPILER

PRELUDE = '//@version=6\nstrategy("prefix probe")\n'
SYMBOLS = [
    ("NASDAQ:AAPL", "NASDAQ"),
    ("BINANCE:BTCUSDT", "BINANCE"),
    ("", ""),
    ("AAPL", ""),
    (":AAPL", ""),
    ("NASDAQ:", ""),
    ("FIRST:SECOND:THIRD", "FIRST"),
    ("交易所:股票", "交易所"),
]


@pytest.mark.parametrize("symbol, expected", SYMBOLS)
@pytest.mark.parametrize("keyword", [False, True], ids=["positional", "keyword"])
def test_prefix_function_emits_its_symbol_argument(symbol, expected, keyword):
    argument = pine_string_literal(symbol)
    if keyword:
        argument = "symbol=" + argument
    cpp = transpile(PRELUDE + f"prefixValue = syminfo.prefix({argument})\nplot(str.length(prefixValue))\n")
    escaped = NamingHelper._cpp_string_escape(symbol)
    (expression,) = re.findall(r"^\s+prefixValue = ([^\n]+);", cpp, re.MULTILINE)
    assert expression.startswith("([](const std::string& _pf_symbol)")
    assert expression.endswith(f')(std::string("{escaped}"))')


@pytest.mark.parametrize("argument, emitted", [
    ("syminfo.tickerid", "syminfo_.tickerid"),
    ('input.symbol("NASDAQ:AAPL", "Symbol")', 'get_input_string("Symbol"'),
    ('close > open ? "NASDAQ:AAPL" : "BINANCE:BTCUSDT"', "current_bar_.close"),
])
def test_prefix_function_keeps_runtime_symbol_expressions(argument, emitted):
    cpp = transpile(PRELUDE + f"prefixValue = syminfo.prefix({argument})\nplot(str.length(prefixValue))\n")
    (expression,) = re.findall(r"^\s+prefixValue = ([^\n]+);", cpp, re.MULTILINE)
    assert emitted in expression
    if argument != "syminfo.tickerid":
        assert expression != "_pf_derive_prefix(syminfo_.tickerid)"


def test_prefix_variable_and_chart_symbol_function_are_identical():
    variable = transpile(PRELUDE + "prefixValue = syminfo.prefix\n")
    function = transpile(PRELUDE + "prefixValue = syminfo.prefix(syminfo.tickerid)\n")
    assert variable == function


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_prefix_literal_expressions_return_the_expected_prefixes(opt, tmp_path):
    if _COMPILER is None:
        pytest.skip("C++ compiler unavailable")
    body = "".join(f"prefixValue{index} = syminfo.prefix({pine_string_literal(symbol)})\n"
                   for index, (symbol, _expected) in enumerate(SYMBOLS))
    cpp = transpile(PRELUDE + body)
    expressions = re.findall(r"^\s+prefixValue\d+ = ([^\n]+);", cpp, re.MULTILINE)
    assert len(expressions) == len(SYMBOLS)
    helper = re.search(r"static inline std::string _pf_derive_prefix\(.*?\n\}", cpp, re.DOTALL)
    assert helper is not None
    driver = ('#include <iostream>\n#include <string>\n' + helper.group()
              + '\nstruct { std::string tickerid; } syminfo_{"CHART:SYMBOL"};\nint main() {\n') + "".join(
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
    assert run.stdout.splitlines() == [expected for _symbol, expected in SYMBOLS]
