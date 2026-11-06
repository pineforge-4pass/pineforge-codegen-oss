"""Conditional ``var`` arrays initialize on their first reached bar.

The array declaration runs once when its enclosing block is first reached.
Its values therefore come from that bar and remain available on later bars,
including when the initializer reads time or the bar index.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

import pytest

from pineforge_codegen import transpile
from tests import _compile as compile_env
from tests._compile import run_emitted_tu


class PairedRuntimeSetupError(AssertionError):
    """The paired runtime inputs required by these value checks are absent."""


_HEADER = (
    "//@version=6\n"
    'strategy("conditional array first reach")\n'
)


@dataclass(frozen=True)
class _Row:
    name: str
    array_name: str
    declaration: str
    constructor_marker: str
    expected: str


_ROWS = (
    _Row(
        "inferred_from",
        "values",
        "var values = array.from(close, open)",
        "std::vector<",
        "2 20 19\n",
    ),
    _Row(
        "typed_from",
        "values",
        "var float[] values = array.from(close, open)",
        "std::vector<",
        "2 20 19\n",
    ),
    _Row(
        "short_constructor",
        "values",
        "var values = array.new_float(2, close)",
        "_pf_array_new_size",
        "2 20 20\n",
    ),
    _Row(
        "generic_constructor",
        "values",
        "var float[] values = array.new<float>(size = 2, initial_value = close)",
        "_pf_array_new_size",
        "2 20 20\n",
    ),
    _Row(
        "timestamp_history",
        "stamps",
        "var int[] stamps = array.from(time, time[1])",
        "std::vector<",
        "2 2000 1000\n",
    ),
    _Row(
        "bar_index_literal",
        "values",
        "var int[] values = array.from(bar_index, 41)",
        "std::vector<",
        "2 1 41\n",
    ),
)


def _source(row: _Row) -> str:
    return (
        _HEADER
        + "float observed_size = 0.0\n"
        + "float observed_first = -1.0\n"
        + "float observed_second = -1.0\n"
        + "if bar_index > 0\n"
        + f"    {row.declaration}\n"
        + f"    observed_size := {row.array_name}.size()\n"
        + (
            f"    observed_first := {row.array_name}.size() > 0 "
            f"? {row.array_name}.get(0) : -1.0\n"
        )
        + (
            f"    observed_second := {row.array_name}.size() > 1 "
            f"? {row.array_name}.get(1) : -1.0\n"
        )
    )


def _missing_paired_runtime_inputs() -> list[str]:
    generated = os.environ.get("PINEFORGE_GENERATED_INCLUDE", "")
    generated_ok = bool(generated) and (
        Path(generated) / "pineforge" / "version.h"
    ).is_file()
    available = {
        "CXX": compile_env._COMPILER is not None,
        "PINEFORGE_ENGINE_INCLUDE": compile_env._ENGINE_INC is not None,
        "PINEFORGE_EIGEN_INCLUDE": compile_env._EIGEN_INC is not None,
        "PINEFORGE_GENERATED_INCLUDE": generated_ok,
        "PINEFORGE_ENGINE_LIB": compile_env._ENGINE_LIB is not None,
    }
    return [name for name, present in available.items() if not present]


def _require_paired_runtime() -> None:
    missing = _missing_paired_runtime_inputs()
    if not missing:
        return
    message = (
        "paired runtime setup error: missing paired runtime inputs: "
        + ", ".join(missing)
    )
    if os.environ.get("PINEFORGE_REQUIRE_PAIRED_RUNTIME") == "1":
        raise PairedRuntimeSetupError(message)
    pytest.skip(message)


def _matching_brace(text: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise AssertionError("unterminated emitted conditional block")


def _conditional_block_for_guard(cpp: str, guard_start: int) -> str:
    enclosing: list[tuple[int, int, str]] = []
    for match in re.finditer(r"\bif\s*\(", cpp):
        paren = cpp.find("(", match.start(), match.end())
        depth = 0
        closing_paren = None
        for index in range(paren, len(cpp)):
            if cpp[index] == "(":
                depth += 1
            elif cpp[index] == ")":
                depth -= 1
                if depth == 0:
                    closing_paren = index
                    break
        if closing_paren is None:
            continue
        condition = cpp[paren + 1:closing_paren]
        if "pine_bar_index()" not in condition:
            continue
        opening = closing_paren + 1
        while opening < len(cpp) and cpp[opening].isspace():
            opening += 1
        if opening >= len(cpp) or cpp[opening] != "{":
            continue
        closing = _matching_brace(cpp, opening)
        if opening < guard_start < closing:
            enclosing.append((opening, closing, condition))
    assert enclosing, cpp
    opening, closing, condition = max(enclosing, key=lambda item: item[0])
    assert "pine_bar_index()" in condition
    return cpp[opening:closing + 1]


def _assert_declaration_site_guard(cpp: str, row: _Row) -> None:
    flag = f"_pf_var_init_{row.array_name}"
    guard = f"if (!{flag}) {{"
    assert cpp.count(guard) == 1, cpp
    start = cpp.index(guard)
    block = _conditional_block_for_guard(cpp, start)
    assert guard in block, cpp
    assert f"{flag} = true;" in block, block
    assignment = re.search(
        rf"\b{re.escape(row.array_name)}\s*=\s*", block
    )
    assert assignment is not None, block
    rhs = block[assignment.end():]
    assert row.constructor_marker in rhs, block
    assert "/* unknown */" not in rhs, block

    preamble_start = cpp.index("if (!_var_initialized) {")
    preamble_end = cpp.index("_var_initialized = true;", preamble_start)
    empty_preamble = cpp[preamble_start:preamble_end]
    assert not re.search(
        rf"\b{re.escape(row.array_name)}\s*=", empty_preamble
    ), cpp


_DRIVER = r"""
#include <iostream>

int main() {
    GeneratedStrategy strategy;
    Bar bars[] = {
        {9.0, 11.0, 8.0, 10.0, 100.0, 1000},
        {19.0, 21.0, 18.0, 20.0, 101.0, 2000},
        {29.0, 31.0, 28.0, 30.0, 102.0, 3000},
    };
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << "\n";
        return 2;
    }
    std::cout << strategy.observed_size << " "
              << strategy.observed_first << " "
              << strategy.observed_second << "\n";
}
"""


@pytest.mark.parametrize("row", _ROWS, ids=lambda row: row.name)
def test_conditional_array_uses_a_first_reach_guard(row: _Row) -> None:
    _assert_declaration_site_guard(transpile(_source(row)), row)


@pytest.mark.parametrize("row", _ROWS, ids=lambda row: row.name)
def test_conditional_array_values_remain_from_the_first_reached_bar(
    row: _Row,
) -> None:
    _require_paired_runtime()
    output = run_emitted_tu(
        transpile(_source(row)),
        _DRIVER,
        opt="-O0",
        label=f"conditional-array-{row.name}",
    )
    assert output == row.expected
