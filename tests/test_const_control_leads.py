"""Input-dependent controls for the two delimited CONST leads.

These are evidence contracts for a later Spark2 run.  They deliberately drive
the real parser, public ``transpile`` entrypoint, analyzer context, and
codegen object.  The author lane does not execute them.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import sys
from typing import Any

import pytest


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the package sits one level above tests/
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pineforge_codegen  # noqa: E402
from pineforge_codegen.ast_nodes import (  # noqa: E402
    Assignment,
    ExprStmt,
    FuncCall,
    Identifier,
    IfStmt,
    MemberAccess,
    NumberLiteral,
    SwitchStmt,
    VarDecl,
)
from pineforge_codegen.errors import CompileError  # noqa: E402


_MISSING = object()
_RENAMED_USER_SYMBOL = "__renamed_user_symbol__"


@dataclass(frozen=True)
class _PipelineRun:
    source: str
    label: str
    parsed_ast: Any | None = None
    public_cpp: str | None = None
    gen: Any | None = None
    ctx: Any | None = None
    cpp: str | None = None
    # (exception type, stable diagnostic code, phase, file, line, column,
    # message, hint)
    refusal: tuple[tuple[str, str, str, str, int, int, str, str], ...] | None = None


def _refusal_details(error: CompileError) -> tuple[
    tuple[str, str, str, str, int, int, str, str], ...
]:
    """Keep the exact typed public boundary for a later run."""

    details = []
    for diagnostic in error.diagnostics:
        location = diagnostic.location
        details.append(
            (
                "CompileError",
                diagnostic.code,
                diagnostic.phase.value,
                location.file if location is not None else "<no-location>",
                location.line if location is not None else 0,
                location.col if location is not None else 0,
                diagnostic.message,
                diagnostic.hint or "",
            )
        )
    return tuple(details)


def _run_pipeline(source: str, label: str) -> _PipelineRun:
    """Run the supported public route, then expose its real internal state.

    ``CompileError`` is the only boundary captured.  Other exceptions remain
    test failures so an unsupported syntax shape or an untyped limit escape
    cannot be relabeled as a product refusal.
    """

    filename = f"{label}.pine"
    try:
        public_cpp = pineforge_codegen.transpile(
            source,
            check_support=True,
            filename=filename,
            libraries={},
        )
    except CompileError as error:
        return _PipelineRun(
            source=source,
            label=label,
            refusal=_refusal_details(error),
        )

    # This is the actual parser path used by transpile, repeated only so the
    # raw declaration.value shape is an explicit evidence item.
    parsed_ast, _pragmas, _budget = pineforge_codegen._parse_bounded(
        source, filename
    )
    gen, ctx, cpp, _support_diagnostics, _sites = pineforge_codegen._generate(
        source,
        True,
        filename,
        {},
    )
    assert cpp == public_cpp, (
        f"{label}: public transpile and _generate must produce the same emitted C++"
    )
    return _PipelineRun(
        source=source,
        label=label,
        parsed_ast=parsed_ast,
        public_cpp=public_cpp,
        gen=gen,
        ctx=ctx,
        cpp=cpp,
    )


def _format_refusal(run: _PipelineRun) -> str:
    rows = []
    for _error_type, code, phase, file, line, col, message, hint in run.refusal or ():
        suffix = f" hint={hint!r}" if hint else ""
        rows.append(f"{code}@{phase} {file}:{line}:{col}: {message}{suffix}")
    return (
        f"{run.label}: typed CompileError refusal is a real boundary; "
        f"preserve exact diagnostics: {'; '.join(rows)}"
    )


def _require_public_success(run: _PipelineRun) -> _PipelineRun:
    """Fail when the supported public route refuses a TradingView-valid source."""

    if run.refusal is not None:
        pytest.fail(_format_refusal(run))
    assert run.gen is not None
    assert run.ctx is not None
    assert run.parsed_ast is not None
    assert run.public_cpp is not None
    assert run.cpp is not None
    assert "class GeneratedStrategy" in run.public_cpp, (
        f"{run.label}: public route did not return a strategy translation"
    )
    return run


def _top_level_decl(ast: Any, name: str) -> VarDecl:
    matches = [
        statement
        for statement in ast.body
        if isinstance(statement, VarDecl) and statement.name == name
    ]
    assert len(matches) == 1, f"expected one top-level declaration for {name!r}"
    return matches[0]


def _reassignment_state(run: _PipelineRun) -> dict[str, Any]:
    """Read the actual helper result and its constructor-time consumer state."""

    assert run.gen is not None
    return {
        "reassigned": frozenset(run.gen._find_reassigned_vars()),
        "known_length": run.gen._known_vars.get("length", _MISSING),
        "input_backed_length": "length" in run.gen._input_backed_vars,
    }


_CONST_HEADER = """//@version=6
strategy("CONST controls")
length = input.int(10, "Length")
"""

_NO_REASSIGN_SOURCE = _CONST_HEADER + """plot(length)
"""

_TOP_LEVEL_REASSIGN_SOURCE = _CONST_HEADER + """if close > open
    length := 20
plot(length)
"""

_HIDDEN_IF_SOURCE = _CONST_HEADER + """selected = if close > open
    length := 20
    length
else
    length
plot(selected)
"""

_HIDDEN_SWITCH_SOURCE = _CONST_HEADER + """selected = switch
    close > open =>
        length := 20
        length
    =>
        length
plot(selected)
"""


def _assert_nested_assignment_shape(run: _PipelineRun, selection_type: type) -> None:
    """Prove the assignment is in the declaration's parsed RHS selection."""

    assert run.parsed_ast is not None
    selected = _top_level_decl(run.parsed_ast, "selected")
    assert isinstance(selected.value, selection_type), (
        f"{run.label}: parser did not retain the selected {selection_type.__name__} "
        "as declaration.value"
    )

    if isinstance(selected.value, IfStmt):
        selection = selected.value
        assert len(selection.body) >= 2
        branch = selection.body
        assert len(selection.else_body) == 1
        assert isinstance(selection.else_body[0], ExprStmt)
    else:
        assert isinstance(selected.value, SwitchStmt)
        selection = selected.value
        assert len(selection.cases) == 1
        assert selection.default_body
        assert isinstance(selection.default_body[0], ExprStmt)
        branch = selection.cases[0][1]
        assert len(branch) >= 2

    assignment = branch[0]
    assert isinstance(assignment, Assignment), (
        f"{run.label}: declaration.value branch lost the actual := assignment"
    )
    assert assignment.op == ":="
    assert isinstance(assignment.target, Identifier)
    assert assignment.target.name == "length"
    assert isinstance(assignment.value, NumberLiteral)
    assert assignment.value.value == 20
    assert isinstance(branch[-1], ExprStmt)
    assert isinstance(branch[-1].expr, Identifier)
    assert branch[-1].expr.name == "length"


def test_const_input_and_top_level_reassignment_controls_keep_legitimate_state():
    """Healthy input and top-level controls establish the expected state."""

    ordinary = _require_public_success(
        _run_pipeline(_NO_REASSIGN_SOURCE, "const-ordinary-input")
    )
    ordinary_decl = _top_level_decl(ordinary.parsed_ast, "length")
    assert isinstance(ordinary_decl.value, FuncCall)
    ordinary_state = _reassignment_state(ordinary)
    assert "length" not in ordinary_state["reassigned"]
    assert ordinary_state["known_length"] == 10
    assert ordinary_state["input_backed_length"] is True

    top_level = _require_public_success(
        _run_pipeline(_TOP_LEVEL_REASSIGN_SOURCE, "const-top-level-reassignment")
    )
    top_level_state = _reassignment_state(top_level)
    assert "length" in top_level_state["reassigned"], (
        "P1-1 healthy control: top-level := must reach "
        "CodeGen._find_reassigned_vars"
    )
    assert top_level_state["known_length"] is _MISSING
    assert top_level_state["input_backed_length"] is False


@pytest.mark.parametrize(
    ("label", "source", "selection_type"),
    (
        ("const-hidden-if", _HIDDEN_IF_SOURCE, IfStmt),
        ("const-hidden-switch", _HIDDEN_SWITCH_SOURCE, SwitchStmt),
    ),
)
def test_const_reassignment_in_declaration_value_reaches_codegen(
    label: str,
    source: str,
    selection_type: type,
):
    """A declaration RHS must not hide its nested runtime reassignment."""

    # The healthy counterpart is selected and checked before the lead case.
    healthy = _require_public_success(
        _run_pipeline(_TOP_LEVEL_REASSIGN_SOURCE, f"{label}-healthy")
    )
    healthy_state = _reassignment_state(healthy)
    assert "length" in healthy_state["reassigned"]
    assert healthy_state["known_length"] is _MISSING
    assert healthy_state["input_backed_length"] is False

    candidate = _require_public_success(_run_pipeline(source, label))
    _assert_nested_assignment_shape(candidate, selection_type)

    # The public output and _generate output have already been compared above.
    # This state assertion is the cause-specific regression contract: the
    # nested assignment must reach the real walker and the real
    # constructor-time _collect_known_vars consumer.
    candidate_state = _reassignment_state(candidate)
    assert candidate_state == healthy_state, (
        "P1-1: declaration.value reassignment must reach "
        "CodeGen._find_reassigned_vars and prevent input-backed constant "
        f"collection; observed hidden={candidate_state!r}, "
        f"top_level={healthy_state!r}"
    )


def _prefix_source(source_name: str) -> str:
    return f"""//@version=6
strategy("CONST prefix")
window = input.int(5, "Window")
{source_name} = strategy.position_size
plot(ta.sma({source_name}, window))
"""


def _normalize_symbol_set(values: set[str], source_name: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            _RENAMED_USER_SYMBOL if value == source_name else value
            for value in values
        )
    )


def _normalize_cpp(cpp: str, source_name: str) -> str:
    return re.sub(
        rf"\b{re.escape(source_name)}\b",
        _RENAMED_USER_SYMBOL,
        cpp,
    )


def _prefix_ta_site(run: _PipelineRun, source_name: str) -> Any:
    assert run.ctx is not None
    assert run.parsed_ast is not None
    source_decl = _top_level_decl(run.parsed_ast, source_name)
    assert isinstance(source_decl.value, MemberAccess)
    assert isinstance(source_decl.value.object, Identifier)
    assert source_decl.value.object.name == "strategy"
    assert source_decl.value.member == "position_size"

    sites = [
        site for site in run.ctx.ta_call_sites if site.owner_func is None
    ]
    assert len(sites) == 1, (
        f"{run.label}: expected the one top-level ta.sma caller to be recorded"
    )
    site = sites[0]
    assert isinstance(site.node, FuncCall)
    assert isinstance(site.node.callee, MemberAccess)
    assert isinstance(site.node.callee.object, Identifier)
    assert site.node.callee.object.name == "ta"
    assert site.node.callee.member == "sma"
    assert len(site.node.args) == 2
    assert isinstance(site.node.args[0], Identifier)
    assert site.node.args[0].name == source_name
    assert isinstance(site.node.args[1], Identifier)
    assert site.node.args[1].name == "window"
    assert len(site.compute_args) == 1
    assert isinstance(site.compute_args[0], Identifier)
    assert site.compute_args[0].name == source_name
    assert site.ctor_args == ["window"]
    return site


def _prefix_facts(run: _PipelineRun, source_name: str) -> dict[str, Any]:
    assert run.gen is not None
    assert run.ctx is not None
    assert run.cpp is not None
    site = _prefix_ta_site(run, source_name)
    symbol = run.ctx.symbols.global_scope.resolve(source_name)
    dynamic_plan = run.gen._ta_dynamic_plan(site)
    dynamic_refusal = run.gen._ta_dynamic_refusal(site)
    return {
        "analyzer": (
            site.class_name,
            site.returns_tuple,
            bool(site.is_static),
            bool(getattr(symbol, "is_series", False)),
            _normalize_symbol_set(run.ctx.series_vars, source_name),
        ),
        "schedule": (
            bool(run.gen._ta_site_uses_precalc(site)),
            bool(run.gen._has_precalculated_ta()),
            None if dynamic_plan is None else dynamic_plan["kind"],
            None
            if dynamic_refusal is None
            else tuple(dynamic_refusal.get("refused", ())),
        ),
        "cpp": _normalize_cpp(run.cpp, source_name),
    }


def test_const_user_symbol_prefix_does_not_change_static_or_schedule_facts():
    """A user rename must preserve analyzer and downstream TA behavior."""

    # posVal is the healthy, non-prefix selector and is run first.
    healthy = _require_public_success(
        _run_pipeline(_prefix_source("posVal"), "const-prefix-control")
    )
    healthy_facts = _prefix_facts(healthy, "posVal")
    assert healthy.gen is not None
    assert healthy.gen._known_vars.get("window") == 5
    assert "window" in healthy.gen._input_backed_vars
    assert healthy_facts["analyzer"][2] is False, (
        "P1-2 fixture: strategy.position_size must reach the real TA caller "
        "as a runtime-dependent source"
    )

    candidate = _require_public_success(
        _run_pipeline(_prefix_source("inputPos"), "const-prefix-inputPos")
    )
    candidate_facts = _prefix_facts(candidate, "inputPos")
    assert candidate.gen is not None
    assert candidate.gen._known_vars.get("window") == 5
    assert "window" in candidate.gen._input_backed_vars

    # Collect the normalized emitted and downstream scheduling facts before
    # the cause-specific analyzer equality assertion.  Only the known renamed
    # source symbol is normalized; TA/member identifiers and all other text
    # remain part of the comparison.
    assert candidate_facts["cpp"] == healthy_facts["cpp"], (
        "P1-2: normalized public emissions diverged after a user-symbol rename"
    )
    assert candidate_facts["schedule"] == healthy_facts["schedule"], (
        "P1-2: downstream TA precompute/dynamic scheduling changed after "
        "a user-symbol rename"
    )
    assert candidate_facts["analyzer"] == healthy_facts["analyzer"], (
        "P1-2: renaming a non-input user symbol changed analyzer static "
        "classification or series facts"
    )
