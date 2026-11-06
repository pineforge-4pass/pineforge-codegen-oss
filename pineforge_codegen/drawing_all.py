"""Drawing collection types and the evidenced read-only snapshot surface."""

from dataclasses import replace

from .ast_nodes import FuncCall, Identifier, MemberAccess
from .symbols import TypeSpec


DRAWING_ALL_KINDS = frozenset({"line", "box", "label", "linefill", "polyline"})
DRAWING_ALL_READS = frozenset({"size", "first", "last", "get"})
DRAWING_ALL_SNAPSHOT = "pf_drawing_all_snapshot"


def drawing_all_kind(node):
    if (isinstance(node, MemberAccess) and node.member == "all"
            and isinstance(node.object, Identifier)
            and node.object.name in DRAWING_ALL_KINDS):
        return node.object.name
    return None


def drawing_all_call(node):
    """Return (kind, method, receiver, call without the functional receiver)."""
    if not isinstance(node, FuncCall) or not isinstance(node.callee, MemberAccess):
        return None
    callee = node.callee
    receiver = callee.object
    kind = drawing_all_kind(receiver)
    if kind is not None:
        return kind, callee.member, receiver, node
    if isinstance(receiver, Identifier) and receiver.name == "array":
        receiver = node.args[0] if node.args else node.kwargs.get("id")
        kind = drawing_all_kind(receiver)
        if kind is not None:
            return kind, callee.member, receiver, replace(
                node, args=node.args[1:] if node.args else [],
                kwargs={k: v for k, v in node.kwargs.items()
                        if k != "id" or node.args},
            )
    return None


def drawing_all_result_spec(node):
    kind = (getattr(node, "annotations", None) or {}).get(DRAWING_ALL_SNAPSHOT)
    kind = kind or drawing_all_kind(node)
    if kind is not None and kind != "polyline":
        return TypeSpec.array(TypeSpec.udt(kind))
    call = drawing_all_call(node)
    if call is None or call[0] == "polyline" or call[1] not in DRAWING_ALL_READS:
        return None
    return (TypeSpec.primitive("int") if call[1] == "size"
            else TypeSpec.udt(call[0]))


def drawing_all_uses(program):
    """Return allowed direct reads and refused alias uses, with lexical owners.

    Collection identity cannot escape through a helper, field, history or
    reassignment until its read-only provenance is carried through that path.
    Handle elements have no such restriction: setters/delete affect the arena.
    Annotate only snapshot bindings/reads so copied lowering subtrees retain
    the exact array type even after the analyzer has left a lexical scope.
    """
    from .ast_nodes import (
        Assignment, ExprStmt, ForInStmt, ForStmt, FuncDef, IfStmt, MethodDef,
        SwitchStmt, TupleAssign, VarDecl, WhileStmt,
    )
    from .limits import iter_ast_nodes, syntax_children

    # Parameter defaults are syntax stored in annotations, so the ordinary
    # AST iterator does not see them.
    roots = [program]
    roots.extend(
        default for node, _ in iter_ast_nodes(program)
        if isinstance(node, (FuncDef, MethodDef))
        for default in (node.annotations or {}).get("param_defaults") or ()
        if default is not None
    )
    if not any(drawing_all_kind(node) is not None
               for root in roots for node, _ in iter_ast_nodes(root)):
        return set(), []
    user_methods = {
        (node.name, node.type_name)
        for node, _ in iter_ast_nodes(program) if isinstance(node, MethodDef)
    }

    allowed = set()
    refused = []
    scopes = [{}]

    def annotate(node, kind):
        node.annotations = {
            **(node.annotations or {}), DRAWING_ALL_SNAPSHOT: kind,
        }

    def kind_of(node):
        kind = drawing_all_kind(node)
        if kind is not None:
            return kind
        if isinstance(node, Identifier):
            for scope in reversed(scopes):
                if node.name in scope:
                    return scope[node.name]
        return None

    def block(body, binders=(), value=False):
        scopes.append(dict.fromkeys(binders))
        try:
            for index, statement in enumerate(body):
                walk(statement, value=value and index == len(body) - 1)
        finally:
            scopes.pop()

    def walk(node, collection=False, value=False):
        if node is None:
            return
        kind = kind_of(node)
        if kind is not None:
            if drawing_all_kind(node) is not None:
                if collection and kind != "polyline":
                    allowed.add(id(node))
                # Other direct uses retain the existing MemberAccess refusal.
            elif not collection:
                refused.append((node, kind))
            else:
                annotate(node, kind)
            return
        if isinstance(node, VarDecl):
            rhs_kind = kind_of(node.value)
            permitted = rhs_kind is not None and node.type_hint in {
                None, f"array<{rhs_kind}>",
            }
            if permitted and (node.is_var or node.is_varip or value):
                # Persistent alias semantics are not yet measured against
                # TradingView (live view or snapshot). A value-producing
                # declaration would return the collection via a synthesized
                # Identifier that this authored-tree walk cannot inspect.
                refused.append((node, rhs_kind))
                permitted = False
            walk(node.value, collection=permitted, value=True)
            # The initializer sees the outer binding; only now shadow it.
            scopes[-1][node.name] = rhs_kind
            if permitted and rhs_kind != "polyline":
                annotate(node, rhs_kind)
            return
        if isinstance(node, FuncCall) and isinstance(node.callee, MemberAccess):
            callee = node.callee
            receiver = callee.object
            functional = (
                isinstance(receiver, Identifier) and receiver.name == "array"
                and kind_of(receiver) is None
            )
            if functional:
                receiver = node.args[0] if node.args else node.kwargs.get("id")
            receiver_kind = kind_of(receiver)
            if receiver_kind is not None:
                # A user method can override a builtin read name and mutate
                # its receiver. Do not let a read spelling escape provenance.
                overridden = (
                    not functional and drawing_all_kind(receiver) is None
                    and any((callee.member, hint) in user_methods for hint in (
                        f"array<{receiver_kind}>", f"{receiver_kind}[]",
                    ))
                )
                walk(receiver, collection=(
                    callee.member in DRAWING_ALL_READS and not overridden
                ))
                for argument in node.args[1:] if functional and node.args else node.args:
                    walk(argument, value=True)
                for name, argument in node.kwargs.items():
                    if not (functional and not node.args and name == "id"):
                        walk(argument, value=True)
                return
        if isinstance(node, (FuncDef, MethodDef)):
            for default in (node.annotations or {}).get("param_defaults") or ():
                walk(default, value=True)
                if default is not None:
                    for child, _ in iter_ast_nodes(default):
                        kind = drawing_all_kind(child)
                        if kind is not None and id(child) not in allowed:
                            refused.append((child, kind))
            block(node.body, node.params, value=True)
            return
        if isinstance(node, TupleAssign):
            walk(node.value, value=True)
            scopes[-1].update(dict.fromkeys(node.names))
            return
        if isinstance(node, ForInStmt):
            walk(node.iterable, collection=node.var is not None, value=True)
            block(node.body, node.vars or [node.var], value=value)
            return
        if isinstance(node, ForStmt):
            walk(node.start, value=True)
            scopes.append({node.var: None})
            try:
                walk(node.end, value=True)
                walk(node.step, value=True)
                block(node.body, value=value)
            finally:
                scopes.pop()
            return
        if isinstance(node, IfStmt):
            walk(node.condition, value=True)
            block(node.body, value=value)
            block(node.else_body, value=value)
            return
        if isinstance(node, WhileStmt):
            walk(node.condition, value=True)
            block(node.body, value=value)
            return
        if isinstance(node, SwitchStmt):
            walk(node.expr, value=True)
            for expression, body in node.cases:
                walk(expression, value=True)
                block(body, value=value)
            block(node.default_body, value=value)
            return
        if isinstance(node, Assignment):
            # Neither replacing a snapshot nor writing it into another owner
            # is in the evidenced subset.
            walk(node.target)
            walk(node.value, value=True)
            return
        if isinstance(node, ExprStmt):
            walk(node.expr, value=value)
            return
        for child in syntax_children(node):
            walk(child, value=True)

    for statement in program.body:
        walk(statement)
    return allowed, refused


def drawing_all_refusal(kind):
    if kind == "polyline":
        return "polyline.all requires an engine polyline handle and live drawing arena."
    return (f"Drawing collection '{kind}.all' only supports direct "
            "size(), first(), last() and get() reads.")
