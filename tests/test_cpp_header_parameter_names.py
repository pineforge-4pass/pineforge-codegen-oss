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
