"""Guards for declaration-value writes and semantic static classification.

Every fixture must pass public transpilation with support checks enabled.
CompileError propagates as a fixture-support boundary, not a regression
assertion result. These guards are authored here for separate execution.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import sys
from typing import Any

import pytest


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the package sits one level above tests/
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pineforge_codegen  # noqa: E402
from pineforge_codegen.ast_nodes import (  # noqa: E402
    Assignment,
    FuncCall,
    Identifier,
    IfStmt,
    MemberAccess,
    Program,
    SwitchStmt,
    TupleAssign,
    VarDecl,
)


@dataclass(frozen=True)
class _Compiled:
    ast: Program
    gen: Any
    ctx: Any


def _compile(body: str) -> _Compiled:
    source = '//@version=6\nstrategy("CONST semantic guards")\n' + body
    filename = "const-guard-semantics.pine"
    public_cpp = pineforge_codegen.transpile(
        source, check_support=True, filename=filename, libraries={}
    )
    ast, _pragmas, _budget = pineforge_codegen._parse_bounded(source, filename)
    assert (ast.annotations or {}).get("parse_recovery_count", 0) == 0
    gen, ctx, cpp, _support, _sites = pineforge_codegen._generate(
        source, True, filename, {}
    )
    assert cpp == public_cpp
    assert "class GeneratedStrategy" in public_cpp
    return _Compiled(ast, gen, ctx)


def _decl(run: _Compiled, name: str) -> VarDecl:
    matches = [
        node for node in run.ast.body
        if isinstance(node, VarDecl) and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def _symbol(run: _Compiled, name: str) -> Any:
    symbol = run.ctx.symbols.global_scope.resolve(name)
    assert symbol is not None, name
    return symbol


def _sma_sites(run: _Compiled) -> list[Any]:
    sites = [site for site in run.ctx.ta_call_sites if site.owner_func is None]
    assert all(site.class_name == "ta::SMA" for site in sites)
    return sites


_INPUTS = """horizon = input.int(7, "Horizon")
untouched = input.int(11, "Untouched")
anchor = 3
"""


def _assert_other_bindings_retained(run: _Compiled) -> None:
    assert run.gen._known_vars.get("untouched") == 11
    assert "untouched" in run.gen._input_backed_vars
    assert run.gen._known_vars.get("anchor") == 3
    assert "anchor" not in run.gen._input_backed_vars


def _assert_runtime_write(run: _Compiled, targets: set[str]) -> None:
    assert run.gen._find_reassigned_vars() == targets
    assert "horizon" not in run.gen._known_vars
    assert "horizon" not in run.gen._input_backed_vars
    _assert_other_bindings_retained(run)


@pytest.mark.parametrize(
    ("body", "value_type"),
    (
        pytest.param("choice = horizon\n", Identifier, id="plain-alias"),
        pytest.param(
            """choice = if close > open
    branchCopy = horizon
    branchCopy
else
    horizon
""",
            IfStmt,
            id="read-only-if",
        ),
        pytest.param(
            """choice = switch
    close > open =>
        branchCopy = horizon
        branchCopy
    =>
        horizon
""",
            SwitchStmt,
            id="read-only-switch",
        ),
    ),
)
def test_read_only_declarations_retain_input_collection(body: str, value_type: type):
    run = _compile(_INPUTS + body + "plot(choice + untouched + anchor)\n")
    assert isinstance(_decl(run, "choice").value, value_type)
    assert isinstance(_decl(run, "horizon").value, FuncCall)
    assert run.gen._find_reassigned_vars() == set()
    assert run.gen._known_vars.get("horizon") == 7
    assert "horizon" in run.gen._input_backed_vars
    _assert_other_bindings_retained(run)


def test_top_level_conditional_write_still_excludes_input():
    run = _compile(_INPUTS + """if close > open
    horizon += 2
plot(horizon + untouched + anchor)
""")
    selections = [node for node in run.ast.body if isinstance(node, IfStmt)]
    assert len(selections) == 1
    assert isinstance(selections[0].body[0], Assignment)
    assert selections[0].body[0].op == "+="
    _assert_runtime_write(run, {"horizon"})


@pytest.mark.parametrize(
    ("body", "selection_type"),
    (
        pytest.param(
            """choice = if close > open
    horizon
else
    horizon += 2
    horizon
""",
            IfStmt,
            id="if-else-compound-write",
        ),
        pytest.param(
            """choice = switch
    close > open =>
        horizon
    =>
        horizon := horizon + 3
        horizon
""",
            SwitchStmt,
            id="switch-default-write",
        ),
    ),
)
def test_selection_fallback_write_excludes_only_mutated_input(
    body: str, selection_type: type
):
    run = _compile(_INPUTS + body + "plot(choice + untouched + anchor)\n")
    selection = _decl(run, "choice").value
    assert isinstance(selection, selection_type)
    branch = (
        selection.else_body if isinstance(selection, IfStmt)
        else selection.default_body
    )
    assert isinstance(branch[0], Assignment)
    assert isinstance(branch[0].target, Identifier)
    assert branch[0].target.name == "horizon"
    _assert_runtime_write(run, {"horizon"})


def test_repeated_declaration_value_edges_reach_inner_switch_write():
    run = _compile(_INPUTS + """choice = if close > open
    innerChoice = switch
        high > close =>
            horizon += 2
            horizon
        =>
            horizon
    innerChoice
else
    horizon
plot(choice + untouched + anchor)
""")
    outer = _decl(run, "choice").value
    assert isinstance(outer, IfStmt)
    inner = outer.body[0]
    assert isinstance(inner, VarDecl)
    assert isinstance(inner.value, SwitchStmt)
    assert isinstance(inner.value.cases[0][1][0], Assignment)
    _assert_runtime_write(run, {"horizon"})


def test_tuple_selection_declaration_names_are_not_reassignment_targets():
    run = _compile(_INPUTS + """[firstValue, secondValue] = switch
    close > open =>
        horizon += 2
        [horizon, anchor]
    =>
        [horizon, untouched]
plot(firstValue + secondValue)
""")
    bindings = [node for node in run.ast.body if isinstance(node, TupleAssign)]
    assert len(bindings) == 1
    assert bindings[0].names == ["firstValue", "secondValue"]
    assert isinstance(bindings[0].value, SwitchStmt)
    assert isinstance(bindings[0].value.cases[0][1][0], Assignment)
    _assert_runtime_write(run, {"horizon"})


def test_assignment_selection_keeps_outer_and_inner_write_targets():
    run = _compile(_INPUTS + """choice = 0
choice := if close > open
    horizon += 2
    horizon
else
    horizon
plot(choice + untouched + anchor)
""")
    writes = [node for node in run.ast.body if isinstance(node, Assignment)]
    assert len(writes) == 1
    assert isinstance(writes[0].target, Identifier)
    assert writes[0].target.name == "choice"
    assert isinstance(writes[0].value, IfStmt)
    assert isinstance(writes[0].value.body[0], Assignment)
    _assert_runtime_write(run, {"horizon", "choice"})


def test_real_inputs_constants_enums_and_chart_static_series_are_retained():
    run = _compile("""enum Mode
    a
    b
sample = input.float(3.5, "Sample")
inputOffset = input(2.0, "Offset")
horizon = input.int(4, "Horizon")
const float literalOffset = 1.0
stableSource = math.abs(sample) + inputOffset + literalOffset
enumChoice = input.enum(Mode.a, "Mode")
enumMember = Mode.b
enumDecision = enumChoice == enumMember
chartSource = close
plot(ta.sma(sample, horizon))
plot(ta.sma(stableSource, horizon))
plot(ta.sma(chartSource, horizon))
plot(enumDecision ? 1 : 0)
""")
    sample = _decl(run, "sample").value
    assert isinstance(sample, FuncCall)
    assert isinstance(sample.callee, MemberAccess)
    assert isinstance(sample.callee.object, Identifier)
    assert sample.callee.object.name == "input"
    for name in ("sample", "inputOffset", "horizon", "enumChoice"):
        assert _symbol(run, name).is_const is True
    assert _symbol(run, "enumChoice").enum_type_name == "Mode"
    assert run.ctx.enum_defs["Mode"] == ["a", "b"]
    for name in (
        "literalOffset", "stableSource", "enumMember", "enumDecision", "chartSource"
    ):
        assert getattr(_symbol(run, name), "is_static_series", False) is True, name
    assert run.gen._known_vars.get("literalOffset") == 1.0
    assert run.gen._known_vars.get("horizon") == 4
    assert "horizon" in run.gen._input_backed_vars
    sites = _sma_sites(run)
    assert len(sites) == 3
    assert [site.compute_args[0].name for site in sites] == [
        "sample", "stableSource", "chartSource"
    ]
    assert all(site.is_static for site in sites)


@pytest.mark.parametrize(
    "name", ("positionValue", "inputValue", "input_value", "inputsValue")
)
def test_runtime_symbol_and_derived_alias_do_not_gain_staticness_from_name(name: str):
    run = _compile(f"""horizon = input.int(4, "Horizon")
{name} = strategy.position_size
derivedValue = {name} + 1
plot(ta.sma({name}, horizon))
plot(ta.sma(derivedValue, horizon))
""")
    source = _decl(run, name).value
    assert isinstance(source, MemberAccess)
    assert isinstance(source.object, Identifier)
    assert source.object.name == "strategy"
    assert source.member == "position_size"
    for symbol_name in (name, "derivedValue"):
        symbol = _symbol(run, symbol_name)
        assert symbol.is_const is False
        assert getattr(symbol, "is_static_series", False) is False
    sites = _sma_sites(run)
    assert len(sites) == 2
    assert [site.compute_args[0].name for site in sites] == [name, "derivedValue"]
    assert all(not site.is_static for site in sites)
    assert run.gen._known_vars.get("horizon") == 4
    assert "horizon" in run.gen._input_backed_vars


@pytest.mark.parametrize("name", ("positionBox", "inputBox", "inputsBox"))
def test_runtime_member_receiver_prefix_is_not_the_input_namespace(name: str):
    run = _compile(f"""type Snapshot
    float value = 0.0
horizon = input.int(4, "Horizon")
{name} = Snapshot.new(strategy.position_size)
fieldValue = {name}.value
plot(ta.sma({name}.value, horizon))
plot(ta.sma(fieldValue, horizon))
""")
    field = _decl(run, "fieldValue").value
    assert isinstance(field, MemberAccess)
    assert isinstance(field.object, Identifier)
    assert field.object.name == name
    assert field.member == "value"
    assert _symbol(run, name).udt_type_name == "Snapshot"
    for symbol_name in (name, "fieldValue"):
        symbol = _symbol(run, symbol_name)
        assert symbol.is_const is False
        assert getattr(symbol, "is_static_series", False) is False
    sites = _sma_sites(run)
    assert len(sites) == 2
    assert isinstance(sites[0].compute_args[0], MemberAccess)
    assert isinstance(sites[0].compute_args[0].object, Identifier)
    assert sites[0].compute_args[0].object.name == name
    assert isinstance(sites[1].compute_args[0], Identifier)
    assert sites[1].compute_args[0].name == "fieldValue"
    assert all(not site.is_static for site in sites)
