"""Keyword-name validation for intrinsic records with verified keyword names."""

from __future__ import annotations

from .ast_nodes import ASTNode
from .diagnostic_codes import render_diagnostic
from .errors import CompileError, Diagnostic, Level, Phase, SourceLocation
from .pine_spelling import expr_start
from .signatures import IntrinsicFunc


def intrinsic_keyword_diagnostics(
    intrinsic: IntrinsicFunc | None,
    kwargs: dict[str, ASTNode],
) -> list[Diagnostic]:
    """Check the complete keyword set of records with verified keyword names.

    Other records retain their existing binding, including ignored names.
    This checks names, leaving argument order and other binding policies to
    their existing validators.
    """
    if intrinsic is None or not intrinsic.keyword_names_verified or not kwargs:
        return []
    names = {param.name for signature in intrinsic.signatures
             for param in signature.params}
    diagnostics = []
    for name, value in kwargs.items():
        if name in names:
            continue
        location = ((value.annotations or {}).get("keyword_location")
                    or expr_start(value).loc or value.loc
                    or SourceLocation("<input>", 1, 1, 2))
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
) -> None:
    """Validate verified keyword names for direct codegen clients."""
    diagnostics = intrinsic_keyword_diagnostics(intrinsic, kwargs)
    if diagnostics:
        raise CompileError(diagnostics)
