"""P1-1: every written integer actual participates in the NA guard proof.

Authored against 01919c40674bf2bd94e7b06cd036eb5536314678. Expectations follow
Pine's na + 1 = na and finite a + 1 arithmetic, not captured codegen output.
The review fixture is source-traced evidence, not a new TradingView capture.
Run parent RED and candidate GREEN together on the approved C2 executor.
"""

from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import (
    BoolLiteral, ExprStmt, FuncCall, FuncDef, Identifier, IfStmt, NaLiteral,
    NumberLiteral,
)
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._numeric_expected import integer_arithmetic
from tests.test_numeric_missing_value_rows import _git_identity, _json, _process, _sha


_ROOT = Path(__file__).resolve().parents[1]
_REVIEW_TUPLE = """//@version=6
strategy("T")
inc(int a) => a + 1
var int k = na
if bar_index > 2
    k := 5
pair() => [inc(k), 0]
[p, q] = pair()
r = inc(7)
plot(p)
"""
_PREFIX = """//@version=6
strategy("T")
inc(int a) => a + 1
var int k = na
if bar_index > 2
    k := 5
"""
_SUFFIX = "r = inc(7)\nplot(k)\n"

# The explicit breaks bound even the parent's wrong finite INT_MIN + 1 end.
# Native loop rows observe the original pure inc member, without running loops.
_ROWS = [
    pytest.param(_REVIEW_TUPLE, "pair", True, id="review-tuple"),
    pytest.param(
        _PREFIX + "for i = 0 to inc(k)\n    break\n" + _SUFFIX,
        None, False, id="review-for-end",
    ),
    pytest.param(
        _PREFIX + "for i = inc(k) to 0\n    break\n" + _SUFFIX,
        None, False, id="for-start",
    ),
    pytest.param(
        _PREFIX + "for i = 0 to 1 by inc(k)\n    break\n" + _SUFFIX,
        None, False, id="for-step",
    ),
    pytest.param(
        _PREFIX + "for v in array.from(inc(a = k))\n    break\n" + _SUFFIX,
        None, False, id="for-in-iterable-keyword",
    ),
    pytest.param(
        _PREFIX + "pair() => [0, inc(k)]\n[q, p] = pair()\n"
        "r = inc(7)\nplot(p)\n",
        "pair", True, id="tuple-second-element",
    ),
    pytest.param(
        _PREFIX + "scan(int k) =>\n"
        "    for i = 0 to inc(k)\n        break\n    0\n"
        "s = scan(k)\n" + _SUFFIX,
        "scan", False, id="helper-for-end-owner",
    ),
]


def _generator(source):
    tree = Parser(Lexer(source).tokenize(), source=source).parse()
    return CodeGen(Analyzer(tree).analyze())


def _inc_body(cpp):
    # This seam accepts either return family; integer storage must not be
    # widened just to hide a missing guard. The parameter remains a C++ int.
    bodies = re.findall(
        r"^    (?:int|int64_t|double) inc\(int a\) \{\n"
        r"(        return [^\n]+;\n)    \}",
        cpp, re.MULTILINE,
    )
    assert len(bodies) == 1, "Expected the original, pure one-return inc member"
    return bodies[0]


@pytest.mark.parametrize("source, owner_name, tuple_result", _ROWS)
def test_hidden_actual_keeps_integer_na_guard(source, owner_name, tuple_result):
    cpp = transpile(source)
    # Check the original integer before arithmetic: INT_MIN + 1 is a number
    # in C++, whereas Pine's missing integer plus one must remain missing.
    assert integer_arithmetic("a", "+", "1") in _inc_body(cpp)


@pytest.mark.parametrize("source, owner_name, tuple_result", _ROWS)
def test_actual_inventory_keeps_lexical_owner_and_written_binding(
    source, owner_name, tuple_result,
):
    generator = _generator(source)
    parameters, arrays = generator._int_boundary_inputs()
    actuals = parameters[("inc", 0)]
    observed = Counter(
        (value.name if isinstance(value, Identifier) else value.value,
         None if owner is None else owner.name, written)
        for value, owner, written in actuals
    )
    assert observed == Counter({("k", owner_name, True): 1, (7, None, True): 1})
    cached_parameters, cached_arrays = generator._int_boundary_inputs()
    assert cached_parameters is parameters and cached_arrays is arrays


@pytest.mark.parametrize("actual, guarded", [("7", False), ("na", True)])
def test_visible_finite_and_missing_actual_controls(actual, guarded):
    source = (
        '//@version=6\nstrategy("control")\n'
        "inc(int a) => a + 1\n" + f"r = inc({actual})\nplot(r)\n"
    )
    body = _inc_body(transpile(source))
    if guarded:
        assert integer_arithmetic("a", "+", "1") in body
    else:
        assert "(a + 1)" in body and "_pna_l" not in body


def test_unknown_nested_callable_does_not_borrow_the_enclosing_owner():
    hidden = FuncCall(callee=Identifier(name="inc"), args=[Identifier(name="a")])
    nested = FuncDef(name="unknown_owner", params=["a"], body=[ExprStmt(expr=hidden)])
    # Synthetic syntax tests the conservative fallback; this is not a claim
    # that Pine v6 accepts nested declarations.
    source = (
        '//@version=6\nstrategy("owner receipt")\n'
        "inc(int a) => a + 1\nr = inc(7)\nplot(r)\n"
    )
    tree = Parser(Lexer(source).tokenize(), source=source).parse()
    context = Analyzer(tree).analyze()
    visible = FuncCall(callee=Identifier(name="inc"), args=[NumberLiteral(value=11)])
    context.ast.body.append(IfStmt(
        condition=BoolLiteral(value=True), body=[nested, ExprStmt(expr=visible)],
    ))
    actuals = CodeGen(context)._int_boundary_inputs()[0][("inc", 0)]
    assert len(actuals) == 3
    assert (None, None, True) in actuals
    finite = [(value.value, owner, written) for value, owner, written in actuals
              if value is not None]
    assert finite == [(7, None, True), (11, None, True)]


def test_method_receiver_keyword_and_omitted_na_actual_keep_their_slots():
    source = """//@version=6
strategy("method actual slots")
type Reading
    int value = 0
method add(Reading self, int delta = na) => delta + 1
reading = Reading.new()
x = reading.add()
y = reading.add(delta = 7)
plot(x)
plot(y)
"""
    parameters, _arrays = _generator(source)._int_boundary_inputs()
    receivers = parameters[("Reading.add", 0)]
    assert len(receivers) == 2
    assert all(isinstance(value, Identifier) and value.name == "reading"
               and owner is None and written
               for value, owner, written in receivers)
    actuals = parameters[("Reading.add", 1)]
    assert len(actuals) == 2
    missing, finite = actuals
    assert isinstance(missing[0], NaLiteral) and missing[1:] == (None, False)
    assert finite[0].value == 7 and finite[1:] == (None, True)


def test_array_writes_keep_declaration_identity_and_lexical_owner():
    source = """//@version=6
strategy("array write receipts")
values = array.new_int()
write(int value) =>
    values.push(value)
    0
values.push(7)
x = write(5)
plot(x)
"""
    generator = _generator(source)
    _parameters, arrays = generator._int_boundary_inputs()
    declaration = next(stmt for stmt in generator.ctx.ast.body
                       if getattr(stmt, "name", None) == "values")
    writes = arrays[(id(declaration), "values")]
    assert Counter(
        (value.name if isinstance(value, Identifier) else value.value,
         None if owner is None else owner.name)
        for value, owner in writes
    ) == Counter({(7, None): 1, ("value", "write"): 1})


_DRIVER = r"""
#include <iomanip>
#include <iostream>
#include <vector>

template<class T> static void number(T value) {
    if (is_na(value)) std::cout << "null";
    else std::cout << value;
}

int main() {
    std::cout << std::setprecision(17);
    unsigned size = 0;
    if (!(std::cin >> size) || size == 0) return 10;
    std::vector<pineforge::Bar> bars(size);
    for (auto& bar : bars) {
        if (!(std::cin >> bar.open >> bar.high >> bar.low >> bar.close
                      >> bar.volume >> bar.timestamp)) return 11;
    }
    unsigned samples = 0;
    if (!(std::cin >> samples) || samples == 0) return 12;
    for (unsigned sample = 0; sample < samples; ++sample) {
        unsigned prefix = 0;
        int missing = 0, finite = 0;
        if (!(std::cin >> prefix >> missing >> finite)
                || prefix == 0 || prefix > size) return 13;
        GeneratedStrategy strategy;
        // inc is the unchanged emitted pure member of the complete TU.
        // In loop rows we never execute on_bar or infer a loop's value.
        auto value = strategy.inc(missing ? na<int>() : finite);
        std::cout << "{\"prefix\":" << prefix << ",\"value\":";
        number(value);
        // TUPLE_OBSERVER
        std::cout << "}\n";
    }
    return 0;
}
"""
_TUPLE_OBSERVER = r"""
        strategy.run(bars.data(), prefix, "1", "1");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 14;
        }
        std::cout << ",\"p\":";
        number(strategy.p);
        std::cout << ",\"r\":";
        number(strategy.r);
"""


def _native_values(directory, source, cpp, opt):
    """Single real TU, retaining the same inputs/flags as the native helper."""
    for variable in (
        "PINEFORGE_ENGINE_INCLUDE", "PINEFORGE_EIGEN_INCLUDE",
        "PINEFORGE_GENERATED_INCLUDE", "PINEFORGE_ENGINE_LIB",
    ):
        assert os.environ.get(variable), f"Explicit native input required: {variable}"
    generated_header = (
        Path(os.environ["PINEFORGE_GENERATED_INCLUDE"]) / "pineforge/version.h"
    )
    assert generated_header.is_file(), "A synthesized syntax-only header is insufficient"
    from tests import _compile as native

    assert native._COMPILER is not None
    assert native._ENGINE_INC is not None and native._EIGEN_INC is not None
    assert native._ENGINE_LIB is not None
    header = native._ENGINE_INC / "pineforge/version.h"
    if not header.is_file():
        assert native._GENERATED_INC is not None
        header = native._GENERATED_INC / "pineforge/version.h"
    assert header.is_file()
    compiler = _process([native._COMPILER, "--version"], directory, "compiler_identity")
    assert compiler.returncode == 0
    _json(directory / "identity.json", {
        "source_sha256": _sha(source.encode()),
        "cpp_sha256": _sha(cpp.encode()),
        "driver_sha256": _sha((directory / "driver.cpp").read_bytes()),
        "input_sha256": _sha((directory / "input.txt").read_bytes()),
        "observer_sha256": _sha(Path(__file__).read_bytes()),
        "types_sha256": _sha(
            (_ROOT / "pineforge_codegen/codegen/types.py").read_bytes(),
        ),
        "opt": opt, "standard": "c++17", "LC_ALL": "C",
        "fp_flags": list(native.STRATEGY_FP_FLAGS),
        "include_flags": native._include_flags(isolate_headers=True),
        "codegen": _git_identity(_ROOT, directory, "codegen_git"),
        "engine": _git_identity(native._ENGINE_INC.parent, directory, "engine_git"),
        "runtime_path": str(native._ENGINE_LIB),
        "runtime_sha256": _sha(native._ENGINE_LIB.read_bytes()),
        "version_path": str(header), "version_sha256": _sha(header.read_bytes()),
    })
    executable = directory / "driver"
    built = _process([
        native._COMPILER, "-std=c++17", opt, *native.STRATEGY_FP_FLAGS,
        *native._include_flags(isolate_headers=True), "-I", directory,
        directory / "driver.cpp", native._ENGINE_LIB, "-o", executable,
    ], directory, "compile")
    assert built.returncode == 0, f"Compile failure; raw artifacts: {directory}"
    _json(directory / "executable.json", {"sha256": _sha(executable.read_bytes())})
    result = _process(
        [executable], directory, "run", stdin=(directory / "input.txt").read_bytes(),
    )
    assert result.returncode == 0, f"Native failure; raw artifacts: {directory}"
    rows = [json.loads(line) for line in result.stdout.decode("ascii").splitlines()]
    _json(directory / "values.json", rows)
    return rows


@pytest.mark.parametrize("opt", ("-O0", "-O2"), ids=("O0", "O2"))
@pytest.mark.parametrize("source, owner_name, tuple_result", _ROWS)
def test_hidden_actual_native_na_and_finite_values(
    tmp_path, source, owner_name, tuple_result, opt,
):
    # Literal inputs and expected values are fixed before transpilation.
    # Missing + 1 is missing; 5 + 1 = 6, 13 + 1 = 14, 0 + 1 = 1.
    samples = [(1, 1, 0), (2, 1, 13), (3, 0, 5), (4, 0, 13), (5, 0, 0)]
    expected = [
        {"prefix": prefix, "value": value}
        for prefix, value in enumerate((None, None, 6, 14, 1), 1)
    ]
    if tuple_result:
        # The exact review program keeps k missing on bars 0-2, then k = 5.
        for row, p in zip(expected, (None, None, None, 6, 6)):
            row.update(p=p, r=8)
    bars = [
        {"open": 100, "high": 102, "low": 99, "close": 101, "volume": 10,
         "timestamp": 1704067200000 + index * 60000}
        for index in range(5)
    ]
    payload = {"bars": bars, "samples": samples, "opt": opt,
               "observer": "tuple members after run" if tuple_result else "pure inc"}
    _json(tmp_path / "inputs.json", payload)
    _json(tmp_path / "expected.json", expected)
    (tmp_path / "input.txt").write_text(
        str(len(bars)) + "\n" + "".join(
            f'{b["open"]} {b["high"]} {b["low"]} {b["close"]} '
            f'{b["volume"]} {b["timestamp"]}\n' for b in bars
        ) + str(len(samples)) + "\n"
        + "".join(f"{prefix} {missing} {finite}\n"
                  for prefix, missing, finite in samples), encoding="ascii",
    )
    (tmp_path / "source.pine").write_text(source, encoding="utf-8")
    cpp = transpile(source)
    (tmp_path / "generated.cpp").write_text(cpp, encoding="utf-8")
    # A nonempty pure-member receipt is required on both parent and candidate;
    # guard spelling is checked separately so parent values can be observed.
    _inc_body(cpp)
    driver = _DRIVER.replace(
        "// TUPLE_OBSERVER", _TUPLE_OBSERVER if tuple_result else "",
    )
    (tmp_path / "driver.cpp").write_text(
        '#include "generated.cpp"\n' + driver, encoding="utf-8",
    )
    rows = _native_values(tmp_path, source, cpp, opt)
    assert len(rows) == len(expected) == 5, (rows, tmp_path)
    assert rows == expected, (rows, expected, tmp_path)
