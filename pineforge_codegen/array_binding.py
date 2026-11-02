"""Bind array constructor arguments to their registered formal slots."""

from __future__ import annotations

from typing import NoReturn

from . import signatures as sigs
from .ast_nodes import ASTNode, FuncCall
from .diagnostic_codes import render_diagnostic
from .errors import CompileError, Diagnostic, Level, Phase, SourceLocation
from .pine_spelling import expr_start


def _error(node: ASTNode, message: str) -> NoReturn:
    location = expr_start(node).loc or SourceLocation("<input>", 1, 1, 1)
    raise CompileError([Diagnostic(
        level=Level.ERROR, phase=Phase.CODEGEN, location=location, message=message,
    )])


def bind_array_constructor_arguments(
    node: FuncCall, method: str,
) -> tuple[ASTNode | None, ...]:
    """Return supplied arguments in parameter order, with holes left as None.

    Written order is syntax provenance only: Pine refuses a positional after
    a keyword, but evaluates valid constructor arguments in formal order.
    Defaults and element typing belong to the existing constructor lowering.
    """
    names = sigs.ARRAY_NEW_PARAMETERS
    key = f"array.{method}"
    order = (node.annotations or {}).get(
        "call_arg_order", (*node.args, *node.kwargs.values()))
    keyword_nodes = {id(arg) for arg in node.kwargs.values()}
    seen_keyword = False
    for arg in order:
        if id(arg) in keyword_nodes:
            seen_keyword = True
        elif seen_keyword:
            _error(arg, f"{key}: positional argument after a keyword argument")

    names_count = len(names)
    args_count = len(node.args)
    if args_count > names_count:
        _error(
            node.args[names_count],
            render_diagnostic("PF-E2026", {
                "key": key, "names_count": names_count, "args_count": args_count,
            })[0],
        )
    bound = dict(zip(names, node.args))
    for name, arg in node.kwargs.items():
        if name not in names:
            _error(arg, render_diagnostic("PF-E2027", {"key": key, "name": name})[0])
        if name in bound:
            _error(arg, render_diagnostic("PF-E2024", {"key": key, "name": name})[0])
        bound[name] = arg
    return tuple(bound.get(name) for name in names)
