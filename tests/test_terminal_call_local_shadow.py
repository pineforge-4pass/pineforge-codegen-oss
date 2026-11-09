"""A function's last call is typed from the function's own locals before the globals they shadow.

A function that ends in a call on a collection (``slot.get(0, 0)``,
``slot.size()``) is typed again at each call from the callee's parameter specs.
A local the callee declares (``slot = matrix.new<int>(...)``) is not a
parameter, and the scope that query reads holds the globals, so a global of
the same name decided the result: ``f()`` was declared ``std::string`` for an
integer element read from the local matrix, and the program did not build.

Each row names the function, the C++ type the call must return and a source
whose generated program must also pass the compile-only check. The three
controls keep the neighbouring behaviour: no global of that name, a global of
the same value type, and a parameter whose collection type comes from the
argument of the call.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp


HEADER = '//@version=6\nstrategy("terminal call local shadow")\n'

CASES = {
    "matrix-over-string-map": (
        "f", "int",
        'var slot = map.new<string, string>()\n'
        "f() =>\n"
        "    slot = matrix.new<int>(1, 1, 5)\n"
        "    slot.get(0, 0)\n"
        "result = f()\n",
    ),
    "matrix-after-reading-the-global": (
        "f", "int",
        'var slot = map.new<string, string>()\n'
        "f() =>\n"
        '    slot.put("k", "v")\n'
        '    slot = matrix.new<int>(slot.contains("k"), 1, 0)\n'
        "    slot.get(0, 0)\n"
        "result = f()\n",
    ),
    "matrix-with-an-untyped-parameter": (
        "f", "int",
        'var slot = map.new<string, string>()\n'
        "f(seed) =>\n"
        '    slot = matrix.new<int>(slot.contains("k"), 1, seed)\n'
        "    slot.get(0, 0)\n"
        "result = f(7)\n",
    ),
    "array-over-string-map": (
        "f", "int",
        'var slot = map.new<string, string>()\n'
        "f() =>\n"
        "    slot = array.from(1)\n"
        "    slot.get(0)\n"
        "result = f()\n",
    ),
}

CONTROLS = {
    "no-global-of-that-name": (
        "f", "int",
        "f() =>\n"
        "    slot = matrix.new<int>(1, 1, 5)\n"
        "    slot.get(0, 0)\n"
        "result = f()\n",
    ),
    "global-of-the-same-value-type": (
        "f", "int",
        'var slot = map.new<string, int>()\n'
        "f() =>\n"
        "    slot = matrix.new<int>(1, 1, 5)\n"
        "    slot.get(0, 0)\n"
        "result = f()\n",
    ),
    "untyped-parameter-takes-the-argument-type": (
        "pick", "int",
        "pick(m) =>\n"
        '    m.get("key")\n'
        "observed = pick(map.new<string, int>())\n",
    ),
}


def _return_type(cpp: str, name: str) -> str:
    match = re.search(rf"^    ([A-Za-z_][\w:<>, ]*?) {re.escape(name)}\(", cpp, re.M)
    assert match, f"no definition of {name}() in the generated program"
    return match.group(1)


def _check(name: str, expected: str, body: str, label: str) -> None:
    cpp = transpile(HEADER + body)
    assert _return_type(cpp, name) == expected, (
        f"{name}() is declared {_return_type(cpp, name)}, the element read is {expected}")
    compile_cpp(cpp, label=label)


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_local_collection_types_the_result_not_the_global_it_shadows(case):
    name, expected, body = CASES[case]
    _check(name, expected, body, "terminal_local_shadow_" + case.replace("-", "_"))


@pytest.mark.parametrize("case", sorted(CONTROLS))
def test_neighbouring_terminal_calls_keep_their_type(case):
    name, expected, body = CONTROLS[case]
    _check(name, expected, body, "terminal_local_control_" + case.replace("-", "_"))
