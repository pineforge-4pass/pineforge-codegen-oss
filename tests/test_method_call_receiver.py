"""Scalar expression receivers use the same typed method lowering as names."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp, run_emitted_tu
from tests.test_compile_corpus import (
    KNOWN_TRANSPILE_FAILURES,
    _STRATEGIES,
    _strategy_id,
)


_HEADER = '''//@version=6
strategy("method receiver expressions")
method f1_step(float x) => x + 0.5
method f2_step(float x, float amount) => x + amount
'''


@dataclass(frozen=True)
class ReceiverCase:
    name: str
    statement: str
    expected: str


_RECEIVER_CASES = [
    ReceiverCase(
        "one_line",
        "float leaf = (close - open).f1_step()",
        "3.5",
    ),  # close 103.0 - open 100.0 + 0.5 = 3.5.
    ReceiverCase(
        "leading_dot",
        "float leaf = (close - open)\n  .f1_step()",
        "3.5",
    ),  # The joined expression is 103.0 - 100.0 + 0.5 = 3.5.
    ReceiverCase(
        "nested_call",
        "float leaf = math.abs(close - open).f1_step()",
        "3.5",
    ),  # abs(103.0 - 100.0) + 0.5 = 3.5.
    ReceiverCase(
        "two_argument",
        "float leaf = (close - open).f2_step(2.0)",
        "5.0",
    ),  # 103.0 - 100.0 + 2.0 = 5.0.
    ReceiverCase(
        "chained",
        "float leaf = (close - open).f1_step().f1_step()",
        "4.0",
    ),  # 103.0 - 100.0 + 0.5 + 0.5 = 4.0.
    ReceiverCase(
        "identifier",
        "float leaf = close.f1_step()",
        "103.5",
    ),  # close 103.0 + 0.5 = 103.5.
]


_DRIVER = r'''
#include <iomanip>
#include <iostream>

int main() {
    GeneratedStrategy strategy;
    pineforge::Bar bar{100.0, 104.0, 99.0, 103.0, 10.0, 1743466500000LL};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty())
        return 2;
    std::cout << std::fixed << std::setprecision(1) << strategy.leaf << '\n';
}
'''


def _source(statement: str) -> str:
    return _HEADER + statement + "\n"


def _contains_cpp_word(source: str, word: str) -> bool:
    """Find one C++ identifier outside comments and quoted literals."""
    i = 0
    while i < len(source):
        if source.startswith("//", i):
            end = source.find("\n", i + 2)
            i = len(source) if end < 0 else end + 1
            continue
        if source.startswith("/*", i):
            end = source.find("*/", i + 2)
            i = len(source) if end < 0 else end + 2
            continue
        if source[i] in ('"', "'"):
            quote = source[i]
            i += 1
            while i < len(source):
                if source[i] == "\\":
                    i += 2
                elif source[i] == quote:
                    i += 1
                    break
                else:
                    i += 1
            continue
        if source[i].isalpha() or source[i] == "_":
            start = i
            i += 1
            while i < len(source) and (
                source[i].isalnum() or source[i] == "_"
            ):
                i += 1
            if source[start:i] == word:
                return True
            continue
        i += 1
    return False


@pytest.mark.parametrize(
    "case", _RECEIVER_CASES, ids=lambda case: case.name
)
def test_expression_receiver_translates_compiles_and_runs(
    case: ReceiverCase,
) -> None:
    cpp = transpile(_source(case.statement))
    assert not _contains_cpp_word(cpp, "None")
    compile_cpp(cpp, label=case.name)
    output = run_emitted_tu(cpp, _DRIVER, opt="-O0", label=case.name)
    assert output.strip() == case.expected


_PROBE_HEADER = '''//@version=6
strategy("literal receiver probes")
method f1_step(float x) => x + 0.5
'''


_PROBE_SOURCES = {
    "body": _PROBE_HEADER + '''float leaf = (close - open)
  .f1_step()
''',
    "bar_index": _PROBE_HEADER + '''float leaf = float(bar_index)
  .f1_step()
''',
    "gap": _PROBE_HEADER + '''float leaf = (open - nz(close[1], open))
  .f1_step()
''',
    "lag3": _PROBE_HEADER + '''float leaf = nz(close[3], close)
  .f1_step()
''',
    "range_pct": _PROBE_HEADER + '''float leaf = (100.0 * (high - low) / close)
  .f1_step()
''',
    "return": _PROBE_HEADER + '''float leaf = (100.0 * (close / nz(close[1], close) - 1.0))
  .f1_step()
''',
    "sma": _PROBE_HEADER + '''float leaf = ta.sma(close, 5)
  .f1_step()
''',
    "tick_body": _PROBE_HEADER + '''float leaf = ((close - open) / syminfo.mintick)
  .f1_step()
''',
    "weighted": _PROBE_HEADER + '''float leaf = ((high + low + close * 2.0) / 4.0)
  .f1_step()
''',
    "range": _PROBE_HEADER + '''float leaf = high - low
  .f1_step()
''',
    "volume": _PROBE_HEADER + '''float leaf = volume
  .f1_step()
''',
    "time": _PROBE_HEADER + '''float leaf = float(time / 900000 % 3) + 1.0
  .f1_step()
''',
}


def test_literal_receiver_probes_emit_no_cpp_none() -> None:
    for name, source in _PROBE_SOURCES.items():
        cpp = transpile(source)
        assert not _contains_cpp_word(cpp, "None"), name


def test_corpus_emission_has_no_cpp_none() -> None:
    if not _STRATEGIES:
        pytest.skip("the engine corpus is unavailable")
    for pine_path in _STRATEGIES:
        strategy_id = _strategy_id(pine_path)
        try:
            cpp = transpile(pine_path.read_text())
        except CompileError:
            if strategy_id in KNOWN_TRANSPILE_FAILURES:
                continue
            raise
        assert not _contains_cpp_word(cpp, "None"), strategy_id
