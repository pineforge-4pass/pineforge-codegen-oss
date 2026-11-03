"""Bind registered builtin keyword arguments to their parameter positions.

TradingView names the parameters ``nz(source, replacement)`` and
``fixnan(source)``, and evaluates a call's arguments in parameter order
whatever order they are written in (lab tv probes pf-oi-kw-names and
pf-oi-kw-order: ``nz(replacement = r(), source = s())`` runs ``s`` first).
The analyzer and the codegen read these calls by position, so a keyword
form reached them as a call without its source (``nz(source = x)`` and
``fixnan(source = x)`` raised IndexError) or with its replacement dropped
(``nz(x, replacement = y)`` read 0). The support checker refuses a call
whose arguments bind to none of TradingView's signatures; this pass then
rewrites every call that binds with its arguments in parameter order.
Registered math records with complete keyword sets use the same binding,
so their supplied expressions reach analysis and requested-helper emission
positionally, including both overloads of ``math.round``.
"""

from __future__ import annotations

from .ast_nodes import FuncCall, Identifier, MemberAccess, Program
from .intrinsic_binding import bind_intrinsic_arguments
from .limits import iter_ast_nodes
from . import signatures as sigs

POSITIONAL_BUILTINS = {
    "nz": sigs.BUILTIN_FUNCTIONS["nz"],
    "fixnan": sigs.BUILTIN_FUNCTIONS["fixnan"],
}


def positional_builtin(name: str) -> sigs.IntrinsicFunc | None:
    """Resolve the current registry record, including replaced math records."""
    if name in POSITIONAL_BUILTINS:
        return POSITIONAL_BUILTINS[name]
    namespace, separator, short_name = name.partition(".")
    if separator and namespace == "math":
        intrinsic = sigs.get_intrinsic(namespace, short_name)
        if intrinsic is not None and (intrinsic.keyword_names_verified or intrinsic.variadic):
            return intrinsic
    return None


def bind_builtin_keywords(program: Program) -> Program:
    for node, _depth in iter_ast_nodes(program):
        if not isinstance(node, FuncCall) or not node.kwargs:
            continue
        callee = node.callee
        if isinstance(callee, Identifier):
            name = callee.name
        elif isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier):
            name = f"{callee.object.name}.{callee.member}"
        else:
            continue
        func = positional_builtin(name)
        if func is None:
            continue
        bind_intrinsic_arguments(node, func)
    return program
