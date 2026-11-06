"""A string result of a requested helper is read as a string, never converted to a number.

The query that decides whether a requested helper's result can be a missing
integer (``SecurityEmitter._security_integer_na_operand``) answers for the
helper's final expression over the helper's own locals. A string local, a
string state and a string call result are not integers: wrapping their read
in the integer-na conversion (``(double)_pf_w`` of a ``std::string``) is not
valid C++, and the script does not build.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests import _compile as compile_env


HEADER = '//@version=6\nstrategy("string helper result")\n'
TAIL = (
    'v = request.security(syminfo.tickerid, "60", f())\n'
    'if v == "up"\n'
    '    strategy.entry("L", strategy.long)\n'
)

# Each helper returns a string by a different route to its final expression.
HELPERS = {
    "local": (
        "f() =>\n"
        '    string s = close > open ? "up" : "down"\n'
        "    s\n"
    ),
    "state": (
        "f() =>\n"
        '    var string s = "down"\n'
        "    if close > open\n"
        '        s := "up"\n'
        "    s\n"
    ),
    "call": 'f() => close > open ? "up" : "down"\n',
    "tostring": 'f() => str.tostring(close > open ? 1 : 0)\n',
}


@pytest.mark.parametrize("name", sorted(HELPERS))
def test_string_helper_result_is_not_converted_to_a_number(name):
    cpp = transpile(HEADER + HELPERS[name] + TAIL)
    assert "std::string _req_sec_0 = na<std::string>();" in cpp
    assert "(double)_pf_w" not in cpp
    compile_env.compile_cpp(cpp, label=f"security-helper-string-result-{name}")
