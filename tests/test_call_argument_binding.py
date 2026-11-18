"""Builtin call spellings preserve argument binding and declared value types."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._compile import compile_cpp, run_emitted_tu


HEADER = '//@version=6\nstrategy("Call arguments")\n'


@pytest.mark.parametrize(
    "call",
    [
        "a.push(value = close)",
        "a.push(close)",
        "array.push(a, value = close)",
    ],
)
def test_array_push_binds_value_in_method_and_namespace_calls(call):
    source = HEADER + f"a = array.new<float>()\n{call}\nplot(a.size())\n"
    cpp = transpile(source)
    compile_cpp(cpp, label="array_push_arguments")
    assert cpp == transpile(source.replace(call, "a.push(close)"))
    output = run_emitted_tu(
        cpp,
        r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bars[] = {
        {10.0, 10.0, 10.0, 10.0, 1.0, 0},
        {20.0, 20.0, 20.0, 20.0, 1.0, 60000},
        {30.0, 30.0, 30.0, 30.0, 1.0, 120000},
    };
    strategy.run(bars, 3);
    std::cout << strategy.last_error() << "\n"
              << strategy.a.size() << " " << strategy.a.at(0) << "\n";
}
""",
        opt="-O0",
        label="array_push_arguments",
    )
    # Each bar creates an empty array and pushes that bar's close once.
    assert output.splitlines() == ["", "1 30"]


@pytest.mark.parametrize(
    "call,expected",
    [
        ('m.put("k", na)', '1 1 ""'),
        ('map.put(m, "k", na)', '1 1 ""'),
        ('m.put("k", "v")', '1 0 "v"'),
    ],
)
def test_string_map_put_uses_declared_value_type(call, expected):
    source = (
        HEADER
        + f'm = map.new<string, string>()\n{call}\n'
        + 'plot(na(m.get("k")) ? 1 : 0)\n'
        + 'missing = na(m.get("k"))\n'
        + 'entries = m.size()\n'
        + 'stored = m.get("k")\n'
    )
    cpp = transpile(source)
    compile_cpp(cpp, label="string_map_put_arguments")
    output = run_emitted_tu(
        cpp,
        r"""
#include <iomanip>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bars[] = {
        {10.0, 10.0, 10.0, 10.0, 1.0, 0},
        {20.0, 20.0, 20.0, 20.0, 1.0, 60000},
        {30.0, 30.0, 30.0, 30.0, 1.0, 120000},
    };
    strategy.run(bars, 3);
    std::cout << strategy.last_error() << "\n"
              << strategy.entries << " " << strategy.missing << " "
              << std::quoted(strategy.stored) << "\n";
}
""",
        opt="-O0",
        label="string_map_put_arguments",
    )
    # A put creates one entry; na reads back as missing, while "v" does not.
    assert output.splitlines() == ["", expected]


def test_na_keyword_bool_argument_keeps_its_located_refusal():
    source = HEADER + "bool b = false\nx = na(x = b)\nplot(x ? 1 : 0)\n"
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="calls.pine")
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.code == "PF-E2072"
    assert diagnostic.message == (
        "Cannot call 'na' with a bool argument: "
        "Pine v6 bool values cannot be na."
    )
    assert diagnostic.location.file == "calls.pine"
    # The bool argument in "x = na(x = b)" occupies column 12.
    assert (diagnostic.location.line, diagnostic.location.col) == (4, 12)
    assert diagnostic.location.end_col == 13


def test_na_keyword_numeric_argument_matches_positional_emission():
    source = HEADER + "x = na(x = close)\nplot(x ? 1 : 0)\n"
    cpp = transpile(source)
    compile_cpp(cpp, label="na_keyword_argument")
    assert cpp == transpile(source.replace("na(x = close)", "na(close)"))
