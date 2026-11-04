"""F1-1 parameter-default actuals, authored on d910 before seeing a fix.

Source tracing: parser._parse_func_def stores expression roots in
annotations["param_defaults"]; method_binding and CallVisitor materialize
them at omitted calls. ValueBindings._default sees preceding globals, not
the callable's formal parameters. Ordinary annotations remain non-syntax.
No located refusal was found for these shapes by source tracing; admission
and native values have NOT been executed by this author.

The oracle is independently missing + 1 = missing, finite + 1 arithmetic,
and the authored explicit override. Never generate a golden with codegen.
The exact review script is retained separately from input-driven variants.

Later ONE fresh genuine C2 kit must retain this whole module's collection,
raw results and exact identities: defaults d910 RED / candidate GREEN, plus
the original 21 source and 14 native rows 01919c40 RED / candidate GREEN.
The candidate requires 0 errors and 0 skips, including every O0/O2 node.
Intended collection, NOT observed: 8 inventory + 7 guard + 8 binding +
2 overridden-root + 1 annotation = 26 source rows; 8 * 2 = 16 native rows.
Finite/default and explicit-binding controls need not fail on the parent.
Collection alongside the untouched original 35 rows should be 77, subject
to actual remote collection and the genuine verdict, never source labels.
Do not label a build/setup/abort as a numeric counterexample. Native evidence
is scoped to the recorded engine/runtime (the planned old9fa kit), not an
unobserved current engine. No executor is launched by this source change.

Commands for a later, admitted executor, with explicit real native inputs:
    python -m pytest --collect-only -q tests/test_numeric_parameter_default_actuals.py
    python -m pytest -vv -ra --junitxml=defaults.xml --basetemp=defaults-artifacts \
        tests/test_numeric_parameter_default_actuals.py
The combined collection must also name tests/test_numeric_actual_inventory.py.
Full engine-enabled and corpus gates remain the supervisor's separate gates.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import (
    FuncCall, FuncDef, Identifier, MethodDef, NaLiteral, NumberLiteral, VarDecl,
)
from tests._numeric_expected import integer_arithmetic
from tests.test_numeric_actual_inventory import _generator, _inc_body, _native_values
from tests.test_numeric_missing_value_rows import _json, _sha


_ROOT = Path(__file__).resolve().parents[1]
_PARENT = "d9103e8cca57a3709ffb2e7c7c675dcb33ab2846"
_REVIEW_SHA = "21f07db144e0065d1ff820da82cb36200319c8c995bc7c22928dee2c3d3812d5"
_GRAPH_SHA = "8478ad47035c2c2113b27820c1300aa9bbb0da38e9a22d80794173d4ec8bcd6e"
_REVIEW = """//@version=6
strategy("T")
inc(int a) => a + 1
var int k = na
g(int y = inc(k)) => y
x = g()
r = inc(7)
plot(x)
"""
_LITERAL = """//@version=6
strategy("literal default")
inc(int a) => a + 1
g(int y = inc(na)) => y
x = g()
r = inc(7)
plot(x)
"""
_CONTROLS = """positional = g(int(volume))
keyword = g(y = int(close))
control = int(volume)
plot(positional)
plot(keyword)
plot(control)
"""
_METHOD = """//@version=6
strategy("method default")
inc(int a) => a + 1
var int k = na
type Reading
    int offset = 0
method g(Reading self, int y = inc(k)) => y + self.offset
reading = Reading.new()
x = reading.g()
r = inc(7)
positional = reading.g(int(volume))
keyword = reading.g(y = int(close))
control = int(volume)
plot(x)
plot(positional)
plot(keyword)
plot(control)
"""
_INPUT = """//@version=6
strategy("input driven default")
inc(int a) => a + 1
int k = int(volume > 0 ? volume : na)
g(int y = inc(k)) => y
x = g()
r = inc(7)
plot(x)
""" + _CONTROLS
_SHADOW = """//@version=6
strategy("default global over formal")
inc(int a) => a + 1
var int k = na
g(int k, int y = inc(k)) => y
x = g(5)
r = inc(7)
positional = g(9, int(volume))
keyword = g(11, y = int(close))
control = int(volume)
plot(x)
plot(positional)
plot(keyword)
plot(control)
"""


@dataclass(frozen=True)
class Case:
    source: str
    actual_kind: str
    mode: str = "missing"
    controls: bool = False
    method: bool = False


# No extra nonliteral inc actual is added as a finite control: it would
# independently retain the parent's guard and hide the missing-default bug.
# Input-sensitive controls use explicit g overrides and direct int casts.
_CASES = {
    "review-global": Case(_REVIEW, "k"),
    "literal-na": Case(_LITERAL, "na"),
    "keyword-na": Case(_LITERAL.replace("inc(na)", "inc(a = na)"), "na"),
    "method-default": Case(_METHOD, "k", controls=True, method=True),
    "explicit-overrides": Case(_REVIEW + _CONTROLS, "k", controls=True),
    "finite-default": Case(
        _LITERAL.replace("inc(na)", "inc(5)") + _CONTROLS,
        "five", mode="finite", controls=True,
    ),
    "input-driven": Case(_INPUT, "k", mode="input", controls=True),
    "formal-shadow": Case(_SHADOW, "k", controls=True),
}


@pytest.mark.parametrize("case_id", _CASES)
def test_default_call_is_a_nonempty_integer_actual_root(case_id):
    case = _CASES[case_id]
    generator = _generator(case.source)
    parameters, arrays = generator._int_boundary_inputs()
    actuals = parameters.get(("inc", 0), ())
    assert actuals, "The visible inc(7) and declaration default must be inventoried"
    visible = [
        (value, owner, written) for value, owner, written in actuals
        if isinstance(value, NumberLiteral) and value.value == 7
    ]
    assert len(visible) == 1 and visible[0][1:] == (None, True)
    hidden = [entry for entry in actuals if entry not in visible]
    assert hidden, "A visible finite call alone cannot prove the default present"
    for value, owner, written in hidden:
        assert owner is None, "A default must not borrow g's or a caller's formals"
        assert written is True  # inc's argument is written inside the default.
        if value is None:
            # A conservative unknown-owner/unknown-actual receipt is sound.
            # Separate guard and native rows prevent dropping its NA guard.
            continue
        if case.actual_kind == "k":
            assert isinstance(value, Identifier) and value.name == "k"
        elif case.actual_kind == "na":
            assert isinstance(value, NaLiteral)
        else:
            assert isinstance(value, NumberLiteral) and value.value == 5
    cached_parameters, cached_arrays = generator._int_boundary_inputs()
    assert cached_parameters is parameters and cached_arrays is arrays


@pytest.mark.parametrize(
    "case_id", [name for name, case in _CASES.items() if case.mode != "finite"],
)
def test_nullable_default_retains_the_original_integer_operand_guard(case_id):
    body = _inc_body(transpile(_CASES[case_id].source))
    assert body and integer_arithmetic("a", "+", "1") in body


@pytest.mark.parametrize("case_id", _CASES)
def test_omitted_and_explicit_calls_use_current_default_binding(case_id):
    case = _CASES[case_id]
    generator = _generator(case.source)
    definitions = [
        node for node in generator.ctx.ast.body
        if isinstance(node, (FuncDef, MethodDef)) and node.name == "g"
    ]
    assert len(definitions) == 1
    definition = definitions[0]
    slot = definition.params.index("y")
    default = definition.annotations["param_defaults"][slot]
    assert isinstance(default, FuncCall)
    assert isinstance(default.callee, Identifier) and default.callee.name == "inc"
    calls = {
        node.name: node.value for node in generator.ctx.ast.body
        if isinstance(node, VarDecl) and node.name in ("x", "positional", "keyword")
    }
    assert "x" in calls
    for name, call in calls.items():
        assert isinstance(call, FuncCall)
        bindings = generator._wide_callable_args(call)
        assert len(bindings) == 1
        info, bound = bindings[0]
        assert info.name == ("Reading.g" if case.method else "g")
        value = dict(bound)[slot]
        if name == "x":
            assert value is default, "Observe the actual omitted default expression"
        else:
            written = call.kwargs["y"] if name == "keyword" else call.args[-1]
            assert value is written and value is not default
    assert set(calls) == (
        {"x", "positional", "keyword"} if case.controls else {"x"}
    )


@pytest.mark.parametrize("case_id", ("review-global", "method-default"))
def test_overriding_every_call_does_not_drop_the_declared_default_root(case_id):
    case = _CASES[case_id]
    before = "x = reading.g()\n" if case.method else "x = g()\n"
    after = "x = reading.g(11)\n" if case.method else "x = g(11)\n"
    assert case.source.count(before) == 1
    source = case.source.replace(before, after, 1)
    actuals = _generator(source)._int_boundary_inputs()[0].get(("inc", 0), ())
    assert len(actuals) >= 2, "Defaults remain explicit roots even when overridden"
    hidden = [
        (value, owner, written) for value, owner, written in actuals
        if value is None or isinstance(value, Identifier)
    ]
    assert hidden
    assert all(
        owner is None and written is True
        and (value is None or value.name == "k")
        for value, owner, written in hidden
    )
    assert integer_arithmetic("a", "+", "1") in _inc_body(transpile(source))


def test_unrelated_annotations_do_not_become_default_syntax():
    source = (
        '//@version=6\nstrategy("annotation control")\n'
        "inc(int a) => a + 1\nr = inc(7)\nplot(r)\n"
    )
    generator = _generator(source)
    generator.ctx.ast.annotations = {
        **(generator.ctx.ast.annotations or {}),
        "unrelated_analysis_receipt": FuncCall(
            callee=Identifier(name="inc"), args=[NaLiteral()],
        ),
    }
    # Make this assertion inspect the newly attached metadata even if a
    # constructor query happened to populate the inventory before attachment.
    generator.__dict__.pop("_int_boundary_inputs_cache", None)
    actuals = generator._int_boundary_inputs()[0][("inc", 0)]
    assert len(actuals) == 1
    value, owner, written = actuals[0]
    assert isinstance(value, NumberLiteral) and value.value == 7
    assert owner is None and written is True


_DRIVER = r"""
#include <cmath>
#include <iomanip>
#include <iostream>
#include <type_traits>
#include <vector>

template<class T> static void raw_number(T value) {
    if constexpr (std::is_floating_point_v<T>) {
        if (std::isnan(value)) { std::cout << "null"; return; }
    }
    std::cout << value;
}

template<class T> static void member(const char* name, T value) {
    std::cout << ",\"" << name << "\":";
    if (is_na(value)) std::cout << "null";
    else raw_number(value);
    std::cout << ",\"" << name << "_na\":"
              << (is_na(value) ? "true" : "false")
              << ",\"" << name << "_raw\":";
    raw_number(value);
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
        if (!(std::cin >> prefix) || prefix == 0 || prefix > size) return 13;
        GeneratedStrategy strategy;
        strategy.run(bars.data(), prefix, "1", "1");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 14;
        }
        std::cout << "{\"prefix\":" << prefix;
        member("x", strategy.x);
        member("r", strategy.r);
        // CONTROL_MEMBERS
        std::cout << "}\n";
    }
    return 0;
}
"""
_CONTROL_MEMBERS = r"""
        member("positional", strategy.positional);
        member("keyword", strategy.keyword);
        member("control", strategy.control);
"""
_SCHEDULES = {
    "missing-first": (0, 5, 13, 0, 19, 31),
    "finite-first": (23, 0, 7, 47, 0, 59),
}


def _inputs(schedule, opt):
    second = schedule == "finite-first"
    origin = 1743465600000 if second else 1704067200000
    base = 200 if second else 100
    bars = [
        {
            "open": base + index, "high": base + index + 5,
            "low": base + index - 5,
            "close": base + index + (3 if index % 2 == 0 else -3),
            "volume": volume, "timestamp": origin + index * 60000,
        }
        for index, volume in enumerate(_SCHEDULES[schedule])
    ]
    return {
        "bars": bars, "prefixes": list(range(1, len(bars) + 1)),
        "schedule": schedule, "opt": opt,
        "observer": "stored x/r and authored controls after real strategy.run",
    }


def _reference(case, payload):
    """No producer, emitted spelling, runtime sentinel or captured output."""
    rows = []
    for prefix, bar in enumerate(payload["bars"], 1):
        if case.mode == "finite":
            actual = 5
        elif case.mode == "input":
            actual = int(bar["volume"]) if bar["volume"] > 0 else None
        else:
            actual = None
        row = {"prefix": prefix, "x": None if actual is None else actual + 1, "r": 8}
        if case.controls:
            row.update(
                positional=int(bar["volume"]), keyword=int(bar["close"]),
                control=int(bar["volume"]),
            )
        rows.append(row)
    return rows


def _assert_values(rows, expected, directory):
    assert len(rows) == len(expected) == 6, (rows, directory)
    for observed, wanted in zip(rows, expected):
        members = set(wanted) - {"prefix"}
        keys = {"prefix"} | members | {
            name + suffix for name in members for suffix in ("_na", "_raw")
        }
        assert set(observed) == keys, (observed, wanted, directory)
        assert type(observed["prefix"]) is int
        assert observed["prefix"] == wanted["prefix"]
        for name in members:
            value = observed[name]
            assert value is None or (
                type(value) in (int, float) and math.isfinite(value)
            ), (name, observed, directory)
            assert value == wanted[name], (name, observed, wanted, directory)
            assert observed[name + "_na"] is (wanted[name] is None)
            if value is not None:
                assert observed[name + "_raw"] == value
    return [row["x"] for row in rows]


@pytest.mark.parametrize("opt", ("-O0", "-O2"), ids=("O0", "O2"))
@pytest.mark.parametrize("case_id", _CASES)
def test_default_result_native_after_real_strategy_run(tmp_path, case_id, opt):
    case = _CASES[case_id]
    # Fix both complete inputs and independent references before transpilation.
    payloads = {name: _inputs(name, opt) for name in _SCHEDULES}
    references = {name: _reference(case, payload) for name, payload in payloads.items()}
    for name in _SCHEDULES:
        directory = tmp_path / name
        directory.mkdir()
        _json(directory / "inputs.json", payloads[name])
        _json(directory / "expected.json", references[name])
        (directory / "source.pine").write_text(case.source, encoding="utf-8")
        _json(directory / "source_binding.json", {
            "authoring_parent": _PARENT, "case_id": case_id,
            "review_record_sha256": _REVIEW_SHA, "source_graph_sha256": _GRAPH_SHA,
            "source_sha256": _sha(case.source.encode()),
            "reference_sha256": _sha((directory / "expected.json").read_bytes()),
            "reference_rule": "missing+1=missing; finite+1; explicit override actual",
            "observer_sha256": _sha(Path(__file__).read_bytes()),
            "runner_sha256": _sha(
                (_ROOT / "tests/test_numeric_actual_inventory.py").read_bytes(),
            ),
            "process_helper_sha256": _sha(
                (_ROOT / "tests/test_numeric_missing_value_rows.py").read_bytes(),
            ),
            "runtime_scope": "actual identity.json only; planned old9fa, no current-engine claim",
        })
    cpp = transpile(case.source)
    for name in _SCHEDULES:
        (tmp_path / name / "generated.cpp").write_text(cpp, encoding="utf-8")
    # Do not require a candidate guard here: a real parent's wrong value must
    # reach the retained raw native output before the independent assertion.
    assert _inc_body(cpp)
    driver = _DRIVER.replace(
        "// CONTROL_MEMBERS", _CONTROL_MEMBERS if case.controls else "",
    )
    observed = {}
    for name, payload in payloads.items():
        directory = tmp_path / name
        (directory / "driver.cpp").write_text(
            '#include "generated.cpp"\n' + driver, encoding="utf-8",
        )
        (directory / "input.txt").write_text(
            str(len(payload["bars"])) + "\n" + "".join(
                f'{bar["open"]} {bar["high"]} {bar["low"]} {bar["close"]} '
                f'{bar["volume"]} {bar["timestamp"]}\n' for bar in payload["bars"]
            ) + str(len(payload["prefixes"])) + "\n"
            + " ".join(map(str, payload["prefixes"])) + "\n", encoding="ascii",
        )
        # Read-only existing runner: asserts real compiler/includes/generated
        # version/runtime, records full commands/exits/raw streams, HEAD/tree,
        # runtime/version hashes and flags; never skips or synthesizes inputs.
        rows = _native_values(directory, case.source, cpp, opt)
        _json(directory / "artifact_hashes.json", {
            path.name: _sha(path.read_bytes())
            for path in sorted(directory.iterdir()) if path.is_file()
        })
        observed[name] = rows
    # Both raw schedules survive even when parent x is the wrong finite number.
    for name, rows in observed.items():
        _assert_values(rows, references[name], tmp_path / name)
    if case.mode == "input":
        first, second = (observed[name] for name in _SCHEDULES)
        assert any(row["x"] is None for row in first + second)
        assert len({row["x"] for row in first + second if row["x"] is not None}) >= 4
        assert any(a["x"] != b["x"] for a, b in zip(first, second))
    if case.controls:
        assert all(
            any(a[member] != b[member] for a, b in zip(
                observed["missing-first"], observed["finite-first"],
            ))
            for member in ("positional", "keyword", "control")
        ), (observed, tmp_path)
