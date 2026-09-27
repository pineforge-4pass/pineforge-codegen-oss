"""Bind the keyword arguments of ``nz`` and ``fixnan`` to their positions.

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
"""

from __future__ import annotations

from .ast_nodes import FuncCall, Identifier, Program
from .limits import iter_ast_nodes
from . import signatures as sigs

POSITIONAL_BUILTINS = ("nz", "fixnan")


def bind_builtin_keywords(program: Program) -> Program:
    for node, _depth in iter_ast_nodes(program):
        if (not isinstance(node, FuncCall) or not node.kwargs
                or not isinstance(node.callee, Identifier)
                or node.callee.name not in POSITIONAL_BUILTINS):
            continue
        names = sigs.BUILTIN_FUNCTIONS[node.callee.name].param_names
        if not set(node.kwargs) <= set(names[len(node.args):]):
            continue  # unbound: the support checker refused it
        bound = list(node.args)
        for name in names[len(node.args):]:
            if name not in node.kwargs:
                break
            bound.append(node.kwargs[name])
        if len(bound) != len(node.args) + len(node.kwargs):
            continue  # a hole before a later keyword: left as written
        node.args = bound
        node.kwargs = {}
    return program
