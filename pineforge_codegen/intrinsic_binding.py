"""Keyword validation and positional binding from intrinsic records."""

from __future__ import annotations

from .ast_nodes import ASTNode, BoolLiteral, FuncCall, NumberLiteral, StringLiteral
from .diagnostic_codes import render_diagnostic
from .errors import CompileError, Diagnostic, Level, Phase, SourceLocation
from .pine_spelling import expr_start
from .signatures import IntrinsicFunc, Param


_STRATEGY_ORDER_FUNCTIONS = frozenset({
    "strategy.entry", "strategy.order", "strategy.exit", "strategy.close",
    "strategy.close_all", "strategy.cancel", "strategy.cancel_all",
})


def intrinsic_keyword_diagnostics(
    intrinsic: IntrinsicFunc | None,
    kwargs: dict[str, ASTNode],
    *,
    filename: str | None = None,
    pine_version: int | None = None,
) -> list[Diagnostic]:
    """Check record-driven keyword prohibitions and verified keyword names.

    Other records retain their existing binding, including ignored names.
    This checks names, leaving argument order and other binding policies to
    their existing validators. A caller's filename fills the parser's default
    source label without replacing a location from an explicitly named source.
    Version-specific migration wording requires an explicit source version.
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
        elif (pine_version == 6 and name == "when"
              and intrinsic.name in _STRATEGY_ORDER_FUNCTIONS):
            message, hint = render_diagnostic(
                "PF-E2081", {"key": intrinsic.name, "name": name},
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
    pine_version: int | None = None,
) -> None:
    """Validate the record's keyword policy for direct codegen clients."""
    diagnostics = intrinsic_keyword_diagnostics(
        intrinsic, kwargs, filename=filename, pine_version=pine_version,
    )
    if diagnostics:
        raise CompileError(diagnostics)


def _default_literal(param: Param, anchor: ASTNode) -> ASTNode | None:
    """The literal a parameter's declared default stands for, else None.

    A parameter that declares no default (``None``) or a default that is not a
    plain number, string or bool has none to stand for."""
    default = param.default
    location = expr_start(anchor).loc or anchor.loc
    if isinstance(default, bool):
        return BoolLiteral(value=default, loc=location)
    if isinstance(default, (int, float)):
        return NumberLiteral(value=default, loc=location)
    if isinstance(default, str):
        return StringLiteral(value=default, loc=location)
    return None


def bind_intrinsic_arguments(
    node: FuncCall, intrinsic: IntrinsicFunc, *, filename: str | None = None,
) -> None:
    """Move supplied keyword expressions to their canonical argument slots.

    Keep the original AST nodes, including their locations and annotations.
    A keyword that skips parameters takes its place after them, and each
    skipped parameter becomes the literal of its declared default
    (``matrix.new<float>(columns = 4)`` is ``matrix.new<float>(0, 4)``,
    ``math.random(seed = 3)`` is ``math.random(0.0, 1.0, 3)``). A skipped
    parameter with no declared default leaves the call as written.
    Argument-count, duplicate-slot and required-slot diagnostics remain with
    the existing signature validators.
    """
    validate_intrinsic_keywords(intrinsic, node.kwargs, filename=filename)
    if not node.kwargs:
        return
    widest = max(intrinsic.signatures, key=lambda signature: len(signature.params))
    remaining = widest.params[len(node.args):]
    names = [param.name for param in remaining]
    if not set(node.kwargs) <= set(names):
        return
    last = max(names.index(name) for name in node.kwargs)
    anchor = next(iter(node.kwargs.values()))
    bound = list(node.args)
    for param in remaining[:last + 1]:
        if param.name in node.kwargs:
            bound.append(node.kwargs[param.name])
            continue
        literal = _default_literal(param, anchor)
        if literal is None:
            return
        bound.append(literal)
    node.args = bound
    node.kwargs = {}
