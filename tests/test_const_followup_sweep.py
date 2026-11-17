"""Source-driven controls for the two remaining KEYCUT defect shapes.

Runtime UDT fields already produce non-static TA sites after KEYCUT H, so
the analyzer blocks their path to the precalc expression check. The TA
controls below preserve reachable static cases; they are not a red proof
for the receiver-prefix change. CompileError is never caught or skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import sys
from textwrap import indent
from typing import Any

import pytest


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    cpp: str


def _compile(body: str) -> _Compiled:
    source = '//@version=6\nstrategy("CONST follow-up sweep")\n' + body
    filename = "const-followup-sweep.pine"
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
    return _Compiled(ast, gen, ctx, cpp)


_SCALARS = """horizon = 7
horizon := 11
untouched = input.int(5, "Untouched")
anchor = 3
"""


def _selection_binding(
    binding_kind: type, selection_kind: type, write_arm: str | None
) -> str:
    """Spell a real binding, with the optional write in either selection arm."""
    binding = {
        VarDecl: "choice = ",
        TupleAssign: "[firstValue, secondValue] = ",
        Assignment: "choice := ",
    }[binding_kind]
    result = "[horizon, anchor]" if binding_kind is TupleAssign else "horizon"

    def arm(name: str) -> str:
        write = "horizon := 13\n" if write_arm == name else ""
        return write + result + "\n"

    if selection_kind is IfStmt:
        selection = (
            "if high > low\n"
            + indent(arm("body"), "    ")
            + "else\n"
            + indent(arm("fallback"), "    ")
        )
    else:
        selection = (
            "switch\n    high > low =>\n"
            + indent(arm("body"), "        ")
            + "    =>\n"
            + indent(arm("fallback"), "        ")
        )
    initial = "choice = 0\n" if binding_kind is Assignment else ""
    return initial + binding + selection


@pytest.mark.parametrize(
    "binding_kind", (VarDecl, TupleAssign, Assignment),
    ids=("declaration", "tuple", "assignment"),
)
@pytest.mark.parametrize(
    "selection_kind", (IfStmt, SwitchStmt), ids=("if", "switch"),
)
@pytest.mark.parametrize("write_arm", ("body", "fallback"))
def test_selection_value_write_invalidates_reassigned_stable_scalar(
    binding_kind: type, selection_kind: type, write_arm: str
):
    """A hidden write under a series guard must invalidate an earlier stable fold."""
    run = _compile(
        _SCALARS
        + "if close > open\n"
        + indent(_selection_binding(binding_kind, selection_kind, write_arm), "    ")
        + "plot(horizon + untouched + anchor)\n"
    )
    outer = [node for node in run.ast.body if isinstance(node, IfStmt)]
    assert len(outer) == 1
    binding = outer[0].body[-1]
    assert isinstance(binding, binding_kind)
    selection = binding.value
    assert isinstance(selection, selection_kind)
    if isinstance(selection, IfStmt):
        branch = selection.body if write_arm == "body" else selection.else_body
    else:
        branch = (
            selection.cases[0][1] if write_arm == "body"
            else selection.default_body
        )
    assert isinstance(branch[0], Assignment)
    assert isinstance(branch[0].target, Identifier)
    assert branch[0].target.name == "horizon"
    assert branch[0].op == ":="
    # H's global walker already sees this write; the remaining defect is
    # the second pass resurrecting horizon as a stable scalar.
    assert "horizon" in run.gen._find_reassigned_vars()
    assert "horizon" not in run.gen._stable_runtime_vars, (
        "_reassigns must visit binding.value: the series-guarded write "
        "invalidates the earlier horizon := 11 fold"
    )
    assert "horizon" not in run.gen._derived_input_expr
    assert "horizon" not in run.gen._known_vars
    assert "horizon" not in run.gen._input_backed_vars
    assert run.gen._known_vars.get("untouched") == 5
    assert "untouched" in run.gen._input_backed_vars
    assert run.gen._known_vars.get("anchor") == 3


@pytest.mark.parametrize(
    "binding_kind", (VarDecl, TupleAssign, Assignment),
    ids=("declaration", "tuple", "assignment"),
)
@pytest.mark.parametrize(
    "selection_kind", (IfStmt, SwitchStmt), ids=("if", "switch"),
)
def test_read_only_selection_value_preserves_reassigned_stable_scalar(
    binding_kind: type, selection_kind: type
):
    """Reading horizon in a selection value must not invalidate its stable fold."""
    run = _compile(
        _SCALARS
        + "if close > open\n"
        + indent(_selection_binding(binding_kind, selection_kind, None), "    ")
        + "plot(horizon)\n"
    )
    assert "horizon" in run.gen._stable_runtime_vars
    assert run.gen._derived_input_expr.get("horizon") == "11"
    assert run.gen._known_vars.get("horizon") == 11


def test_direct_top_level_assignment_preserves_reassigned_stable_scalar():
    """A direct stable := must still be folded rather than losing scalar tracking."""
    run = _compile(_SCALARS + "plot(horizon)\n")
    assert "horizon" in run.gen._stable_runtime_vars
    assert run.gen._derived_input_expr.get("horizon") == "11"
    assert run.gen._known_vars.get("horizon") == 11


def test_direct_write_in_top_level_if_invalidates_reassigned_stable_scalar():
    """_reassigns must still see a direct := in a top-level series-dependent if."""
    run = _compile(_SCALARS + """if close > open
    horizon := 13
plot(horizon)
""")
    assert "horizon" not in run.gen._stable_runtime_vars
    assert "horizon" not in run.gen._derived_input_expr
    assert "horizon" not in run.gen._known_vars


def test_real_inputs_enums_constants_and_chart_sources_keep_static_precalc():
    """Real inputs, enum members, literals and chart series must keep static TA."""
    run = _compile("""enum Mode
    a
    b
sample = input.source(high, "Sample")
horizon = input.int(4, "Horizon")
plot(ta.sma(sample, horizon))
plot(ta.sma(Mode.a == Mode.b ? close : open, horizon))
plot(ta.sma(3.5, horizon))
plot(ta.sma(close, horizon))
""")
    sample = [
        node for node in run.ast.body
        if isinstance(node, VarDecl) and node.name == "sample"
    ]
    assert len(sample) == 1
    assert isinstance(sample[0].value, FuncCall)
    callee = sample[0].value.callee
    assert isinstance(callee, MemberAccess)
    assert isinstance(callee.object, Identifier)
    assert callee.object.name == "input"
    assert callee.member == "source"
    assert run.ctx.symbols.global_scope.resolve("sample").is_const is True
    assert run.ctx.enum_defs["Mode"] == ["a", "b"]
    sites = [site for site in run.ctx.ta_call_sites if site.owner_func is None]
    assert len(sites) == 4
    for site in sites:
        assert site.class_name == "ta::SMA"
        assert site.is_static is True
        assert run.gen._ta_site_uses_precalc(site) is True
        assert f"std::vector<double> _precalc_{site.member_name}" in run.cpp
    assert run.gen._has_precalculated_ta() is True
