"""Authored names survive C++ parameter shadowing and libc macros."""

import re

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.codegen.helpers import cpp_code_only


@pytest.mark.parametrize("name,value", [("bar", "close - open"), ("HUGE", "1e10")])
def test_header_and_parameter_names_escape_without_changing_input_titles(name, value):
    def source(binding):
        return (
            '//@version=6\nstrategy("Names")\n'
            f'{binding} = {value}\nx = math.min(close, {binding})\nplot(x)\n'
        )

    cpp = cpp_code_only(transpile(source(name)))
    control = cpp_code_only(transpile(source("ceiling")))
    escaped = f"pf_safe_{name}"
    assert re.search(rf"\bdouble {escaped}\b", cpp)
    assert re.search(rf"\b{escaped} =", cpp)
    # The math expression must read the escaped member, just as the control
    # reads its member, while the host's bar parameter retains its spelling.
    reads = lambda text: next(line.strip() for line in text.splitlines()
                              if line.strip().startswith("x ="))
    assert reads(cpp).replace(escaped, "ceiling") == reads(control)

    settings = transpile_full(
        '//@version=6\nstrategy("Names")\n'
        f'{name} = input.float(3.0)\nplot({name})\n')
    assert settings["inputs"][0]["title"] == name
    assert f'get_input_double("{name}", ' in settings["cpp"]


@pytest.mark.parametrize("name", [
    "MB_LEN_MAX", "INT_WIDTH", "BOOL_MAX", "SNAN", "HUGE_VAL_F128",
    "FP_ILOGB0", "M_PIf64x", "math_errhandling", "_GLIBCXX_CMATH",
    "PINEFORGE_HAS_TA_SERIES_LENGTH", "PF_NATIVE_OK",
])
def test_audited_header_macros_escape_and_keep_public_input_keys(name):
    result = transpile_full(
        '//@version=6\nstrategy("Names")\n'
        f'{name} = input.float(3.0)\nplot({name})\n')
    cpp = cpp_code_only(result["cpp"])
    escaped = f"pf_safe_{name}"
    assert re.search(rf"\bdouble {escaped}\b", cpp)
    assert f'{escaped} = get_input_double("{name}", ' in result["cpp"]
    assert result["inputs"][0]["title"] == name


@pytest.mark.parametrize("name", ["issignaling", "iszero", "iseqsig"])
def test_function_like_header_macros_escape_authored_calls(name):
    cpp = cpp_code_only(transpile(
        '//@version=6\nstrategy("Names")\n'
        f'{name}(float value) => value + 1.0\nplot({name}(close))\n'))
    assert f"pf_safe_{name}(" in cpp
    assert not re.search(rf"\b{name}\s*\(", cpp)
