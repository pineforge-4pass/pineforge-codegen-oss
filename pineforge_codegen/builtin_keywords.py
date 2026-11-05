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

The calls whose single parameter is a timeframe or a trade number
(``timeframe.change``, ``timeframe.in_seconds``, the accessors of
``strategy.closedtrades`` and ``strategy.opentrades``) take the same route: the
lowering reads their argument by position, so a keyword spelling was read as a
call without its argument (the chart's timeframe, trade 0) and raised no
diagnostic. A keyword none of them declares is refused with the unknown-keyword
code, as TradingView refuses it. These records are bound here only
(``keyword_bound_builtin``): the support checker's arity policy for them is
unchanged.
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

# Calls with one parameter that the lowering reads by position (see above).
KEYWORD_ONLY_BOUND_BUILTINS = {
    "timeframe.change": sigs.BUILTIN_FUNCTIONS["timeframe.change"],
    "timeframe.in_seconds": sigs.BUILTIN_FUNCTIONS["timeframe.in_seconds"],
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


def keyword_bound_builtin(name: str) -> sigs.IntrinsicFunc | None:
    """The record whose keyword arguments this pass binds: ``positional_builtin``'s
    and the one-parameter calls whose arity the support checker does not judge."""
    return (positional_builtin(name)
            or KEYWORD_ONLY_BOUND_BUILTINS.get(name)
            or sigs.TRADE_ACCESSOR_FUNCTIONS.get(name))


def _dotted_name(callee) -> str | None:
    """``a``, ``a.b`` or ``a.b.c`` for a callee written as plain names, else None."""
    members: list[str] = []
    while isinstance(callee, MemberAccess):
        members.append(callee.member)
        callee = callee.object
    if not isinstance(callee, Identifier):
        return None
    return ".".join([callee.name, *reversed(members)])


def bind_builtin_keywords(program: Program, *, filename: str | None = None) -> Program:
    for node, _depth in iter_ast_nodes(program):
        if not isinstance(node, FuncCall) or not node.kwargs:
            continue
        name = _dotted_name(node.callee)
        if name is None:
            continue
        func = keyword_bound_builtin(name)
        if func is None:
            continue
        bind_intrinsic_arguments(node, func, filename=filename)
    return program
