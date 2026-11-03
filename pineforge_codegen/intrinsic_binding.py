"""Keyword validation and positional binding from intrinsic records."""

from __future__ import annotations

from .ast_nodes import ASTNode, FuncCall
from .diagnostic_codes import render_diagnostic
from .errors import CompileError, Diagnostic, Level, Phase, SourceLocation
from .pine_spelling import expr_start
from .signatures import IntrinsicFunc


def intrinsic_keyword_diagnostics(
    intrinsic: IntrinsicFunc | None,
    kwargs: dict[str, ASTNode],
    *,
    filename: str | None = None,
) -> list[Diagnostic]:
    """Check record-driven keyword prohibitions and verified keyword names.

    Other records retain their existing binding, including ignored names.
    This checks names, leaving argument order and other binding policies to
    their existing validators. A caller's filename fills the parser's default
    source label without replacing a location from an explicitly named source.
    """
    if intrinsic is None or not kwargs:
        return []
    is_math = intrinsic.name.startswith("math.")
    if intrinsic.variadic:
        value = next(iter(kwargs.values()))
        location = ((value.annotations or {}).get("keyword_location")
                    or expr_start(value).loc or value.loc
                    or SourceLocation("<input>", 1, 1, 2))
        if filename is not None and location.file == "<input>":
            location = SourceLocation(
                filename, location.line, location.col, location.end_col,
            )
        if is_math:
            message, hint = render_diagnostic(
                "PF-E5089", {"function": intrinsic.name},
            )
        else:
            message, hint = render_diagnostic(
                "PF-E1088", {
                    "name": intrinsic.name,
                    "message": "cannot accept keyword arguments because it accepts "
                               "a variable number of arguments",
                    "spelled": "CE10119: Functions that accept a variable number "
                               "of arguments cannot accept keyword arguments",
                },
            )
        return [Diagnostic(
            level=Level.ERROR, phase=Phase.ANALYZER, location=location,
            message=message, hint=hint,
        )]
    if not intrinsic.keyword_names_verified:
        return []
    names = tuple(dict.fromkeys(
        param.name for signature in intrinsic.signatures
        for param in signature.params
    ))
    diagnostics = []
    for name, value in kwargs.items():
        if name in names:
            continue
        location = ((value.annotations or {}).get("keyword_location")
                    or expr_start(value).loc or value.loc
                    or SourceLocation("<input>", 1, 1, 2))
        if filename is not None and location.file == "<input>":
            location = SourceLocation(
                filename, location.line, location.col, location.end_col,
            )
        if is_math:
            message, hint = render_diagnostic(
                "PF-E5090", {"function": intrinsic.name, "name": name,
                             "accepted": ", ".join(names)},
            )
        else:
            message, hint = render_diagnostic(
                "PF-E2027", {"key": intrinsic.name, "name": name},
            )
        diagnostics.append(Diagnostic(
            level=Level.ERROR, phase=Phase.ANALYZER, location=location,
            message=message, hint=hint,
        ))
    return diagnostics


def validate_intrinsic_keywords(
    intrinsic: IntrinsicFunc | None,
    kwargs: dict[str, ASTNode],
    *,
    filename: str | None = None,
) -> None:
    """Validate the record's keyword policy for direct codegen clients."""
    diagnostics = intrinsic_keyword_diagnostics(intrinsic, kwargs, filename=filename)
    if diagnostics:
        raise CompileError(diagnostics)


def bind_intrinsic_arguments(
    node: FuncCall, intrinsic: IntrinsicFunc, *, filename: str | None = None,
) -> None:
    """Move supplied keyword expressions to their canonical argument slots.

    Keep the original AST nodes, including their locations and annotations.
    Argument-count, duplicate-slot and required-slot diagnostics remain with
    the existing signature validators; a call with a gap is left as written.
    """
    validate_intrinsic_keywords(intrinsic, node.kwargs, filename=filename)
    if not node.kwargs:
        return
    names = intrinsic.param_names
    remaining = names[len(node.args):]
    if not set(node.kwargs) <= set(remaining):
        return
    bound = list(node.args)
    for name in remaining:
        if name not in node.kwargs:
            break
        bound.append(node.kwargs[name])
    if len(bound) != len(node.args) + len(node.kwargs):
        return
    node.args = bound
    node.kwargs = {}
