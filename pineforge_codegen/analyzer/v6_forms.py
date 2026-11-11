"""Validate version-specific forms before analysis discards authored syntax."""

from __future__ import annotations

from ..ast_nodes import (
    ASTNode, FuncCall, FuncDef, Identifier, MemberAccess, MethodDef,
    NumberLiteral, Program, StringLiteral, TupleAssign, UnaryOp, VarDecl,
)
from ..diagnostic_codes import render_diagnostic
from ..errors import CompileError, Diagnostic, Level, Phase, SourceLocation
from ..limits import TimeBudget, syntax_children
from ..pine_spelling import expr_start


def _refuse(code: str, node: ASTNode, filename: str,
            location: SourceLocation | None = None) -> None:
    message, hint = render_diagnostic(code, {})
    raise CompileError([Diagnostic(
        level=Level.ERROR,
        phase=Phase.ANALYZER,
        location=location or node.loc or SourceLocation(filename, 1, 1, 1),
        message=message,
        hint=hint,
    )])


def _callee_name(node: FuncCall) -> str | None:
    callee = node.callee
    if isinstance(callee, Identifier):
        return callee.name
    if isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier):
        return f"{callee.object.name}.{callee.member}"
    return None


def _declared_callable_names(program: Program, budget: TimeBudget | None) -> set[str]:
    names: set[str] = set()
    stack = [program]
    while stack:
        node = stack.pop()
        if budget is not None:
            budget.check(node.loc, Phase.ANALYZER)
        notes = node.annotations or {}
        if notes.get("pine_version") == 5:
            continue
        if isinstance(node, (FuncDef, MethodDef)):
            names.add(node.name)
            stack.extend(
                value for value in notes.get("param_defaults", ())
                if isinstance(value, ASTNode)
            )
        stack.extend(syntax_children(node))
    return names


def _check_call(node: FuncCall, filename: str, declared_callables: set[str]) -> None:
    name = _callee_name(node)
    if (name == "color.new"
            and len(node.args) + len(node.kwargs) == 1
            and "transp" not in node.kwargs):
        _refuse("PF-E2088", expr_start(node), filename)
    if name == "fill" and name not in declared_callables and "transp" in node.kwargs:
        argument = node.kwargs["transp"]
        _refuse(
            "PF-E2089", argument, filename,
            (argument.annotations or {}).get("keyword_location"),
        )
    if name == "input.time":
        default = node.kwargs.get("defval", node.args[0] if node.args else None)
        if (isinstance(default, FuncCall)
                and _callee_name(default) == "timestamp"
                and "timestamp" not in declared_callables
                and len(default.args) + len(default.kwargs) > 1
                and ("timezone" in default.kwargs
                     or (default.args and isinstance(default.args[0], StringLiteral)))):
            _refuse("PF-E2090", expr_start(default), filename)
    if name == "plot" and name not in declared_callables:
        width = node.kwargs.get("linewidth", node.args[3] if len(node.args) > 3 else None)
        literal = width
        sign = 1
        if isinstance(width, UnaryOp) and width.op == "-":
            literal = width.operand
            sign = -1
        if (isinstance(literal, NumberLiteral)
                and isinstance(literal.value, int)
                and sign * literal.value < 1):
            _refuse("PF-E2091", width, filename)


def validate_v6_forms(program: Program, filename: str,
                      budget: TimeBudget | None = None) -> None:
    """Inspect every written value, including ignored visual arguments."""
    if program.version != 6:
        return
    declared_callables = _declared_callable_names(program, budget)
    stack = [program]
    while stack:
        node = stack.pop()
        if budget is not None:
            budget.check(node.loc, Phase.ANALYZER)
        notes = node.annotations or {}
        if notes.get("pine_version") == 5:
            continue
        if isinstance(node, (VarDecl, TupleAssign, FuncDef, MethodDef)):
            for name, location in notes.get("declaration_names", ()):
                if name == "range":
                    _refuse("PF-E2092", node, filename, location)
        if isinstance(node, FuncCall):
            _check_call(node, filename, declared_callables)
        stack.extend(syntax_children(node))
        if isinstance(node, (FuncDef, MethodDef)):
            # Parameter defaults live in annotations rather than syntax fields.
            stack.extend(
                value for value in notes.get("param_defaults", ())
                if isinstance(value, ASTNode)
            )
