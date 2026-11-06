"""A typed method that returns an array is emitted returning that array.

``method build(Config self, float v) => array.new<float>(size=self.n,
initial_value=v)`` was emitted as ``double _udt_Config_build(...)`` and the
returned vector failed g++ ("cannot convert 'std::vector<double>' to 'double'
in return"): the analyzer recorded a method's return TypeSpec for a map, a
matrix, a UDT and an array selection with na, but not for an array. A function
that returns an array already carried it.
"""

import pytest

from pineforge_codegen import transpile

HEADER = '//@version=6\nstrategy("method array return", initial_capital = 100000)\n'
TYPE = "type Config\n    int n\n"
ENTRY = 'if s > 0\n    strategy.entry("L", strategy.long)\n'


@pytest.mark.parametrize(
    "element, cpp_element",
    [
        pytest.param("float", "double", id="float"),
        pytest.param("string", "std::string", id="string"),
        pytest.param("bool", "bool", id="bool"),
    ],
)
def test_a_method_returning_an_array_is_emitted_returning_it(element, cpp_element):
    fill = {"float": "1.5", "string": '"x"', "bool": "true"}[element]
    cpp = transpile(
        HEADER + TYPE
        + f"method build(Config self) =>\n    array.new<{element}>(size=self.n, initial_value={fill})\n"
        "cfg = Config.new(3)\n"
        "b = cfg.build()\n"
        "float s = b.size()\n"
        + ENTRY
    )
    signature = [line for line in cpp.splitlines() if "_udt_Config_build(" in line and "{" in line]
    assert signature, cpp
    assert signature[0].lstrip().startswith(f"std::vector<{cpp_element}> _udt_Config_build("), signature


def test_a_method_returning_a_float_is_unchanged():
    cpp = transpile(
        HEADER + TYPE
        + "method scaled(Config self, float v) =>\n    v * self.n\n"
        "cfg = Config.new(3)\n"
        "float s = cfg.scaled(2.0)\n"
        + ENTRY
    )
    signature = [line for line in cpp.splitlines() if "_udt_Config_scaled(" in line and "{" in line]
    assert signature and signature[0].lstrip().startswith("double _udt_Config_scaled("), signature
