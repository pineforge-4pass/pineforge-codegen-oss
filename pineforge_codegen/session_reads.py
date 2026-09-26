"""The ``session.*`` flag reads at a history offset that generated C++ holds.

``session.ismarket[1]`` and the other ``session.<flag>[k]`` reads need state:
a Series per flag at the top level, one per call site inside a function
(codegen). A read the codegen never emits needs none: it sits in a call the
codegen skips with all its arguments (``plot``, ``alert``, the ``table.*``
namespace, ...: codegen/tables.py ``SKIP_FUNC_NAMES`` / ``SKIP_NAMESPACES``,
and ``max_bars_back``) or in a ``strategy.*`` parameter it drops
(support_checker.py ``STRATEGY_UNSUPPORTED_PARAMS``, e.g. ``alert_message``).
The analyzer (which functions to emit once per call site) and the codegen
(which reads get a Series) ask this module the same question. A read it
counts that the codegen leaves out some other way (it renders the argument,
then drops it: ``color.from_gradient``, a drawing's xloc) costs an unused
Series, or a second analysis when the C++ reads none of a function's reads
(``pineforge_codegen._generate``); one it leaves out that the codegen does
emit finds no Series and is refused, never given shared state.
"""

from __future__ import annotations

from .ast_nodes import FuncCall, Identifier, MemberAccess, Subscript
from .limits import syntax_children
from .signatures import SESSION_FLAG_MEMBERS, get_param_names


def is_session_history(node) -> bool:
    """Whether ``node`` is a ``session.*`` flag read at an offset
    (``session.ismarket[1]``)."""
    return (isinstance(node, Subscript)
            and isinstance(node.object, MemberAccess)
            and isinstance(node.object.object, Identifier)
            and node.object.object.name == "session"
            and node.object.member in SESSION_FLAG_MEMBERS)


def _dropped_arguments(call: FuncCall) -> list:
    """The arguments of ``call`` the codegen never visits: all of a skipped
    call's, a ``strategy.*`` call's unsupported parameters."""
    # Imported here: the codegen package imports the analyzer, which imports
    # this module.
    from .codegen.tables import SKIP_FUNC_NAMES, SKIP_NAMESPACES, SKIP_VAR_TYPES
    from .support_checker import STRATEGY_UNSUPPORTED_PARAMS

    callee = call.callee
    everything = [*call.args, *call.kwargs.values()]
    if isinstance(callee, Identifier):
        if callee.name in SKIP_FUNC_NAMES or callee.name == "max_bars_back":
            return everything
        return []
    if not (isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier)):
        return []
    namespace, name = callee.object.name, callee.member
    if namespace in SKIP_NAMESPACES or namespace in SKIP_VAR_TYPES:
        return everything
    if namespace == "strategy" and name in STRATEGY_UNSUPPORTED_PARAMS:
        dropped = STRATEGY_UNSUPPORTED_PARAMS[name]
        params = get_param_names("strategy", name) or []
        return ([value for key, value in call.kwargs.items() if key in dropped]
                + [arg for index, arg in enumerate(call.args)
                   if index < len(params) and params[index] in dropped])
    return []


def emitted_session_reads(root, skip: frozenset[int] | set[int] = frozenset()) -> list[Subscript]:
    """Every ``session.<flag>[k]`` under ``root`` the codegen emits, leaving
    out the subtrees whose node id is in ``skip`` (a request.security
    expression, whose reads keep the requested clock's history)."""
    reads: list[Subscript] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node is None or id(node) in skip:
            continue
        if is_session_history(node):
            reads.append(node)
        children = list(syntax_children(node))
        if isinstance(node, FuncCall):
            dropped = {id(arg) for arg in _dropped_arguments(node)}
            if dropped:
                children = [child for child in children if id(child) not in dropped]
        stack.extend(children)
    return reads
