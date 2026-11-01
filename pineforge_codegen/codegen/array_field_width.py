"""Find integer array fields whose connected storage receives timestamps."""

from ..ast_nodes import (
    Assignment, ExprStmt, FuncCall, FuncDef, Identifier, IfStmt, MemberAccess,
    MethodDef, Subscript, SwitchStmt, Ternary, VarDecl,
)
from ..symbols import TypeSpec


def timestamp_array_fields(gen):
    """Return resolved (record type, field) pairs requiring wide storage.

    This is a compatibility check, not a collection specialization pass.
    Connections include aliases, constructor fields and callable boundaries:
    changing only one vector in a connected component cannot preserve its
    existing reference bindings.
    """
    fields = {
        (type_name, name)
        for type_name, specs in gen._udt_field_type_specs.items()
        for name, spec in specs.items()
        if _int_array(spec)
    }
    if not fields:
        return set()

    nodes = []
    declarations = {}
    for stmt in gen.ctx.ast.body:
        if not isinstance(stmt, (FuncDef, MethodDef)):
            nodes.extend((child, None) for child in gen._walk_ast(stmt))
    for info in gen.ctx.func_infos:
        if info.node is not None:
            nodes.extend((child, info)
                         for child in gen._walk_ast_list(info.node.body))
    for node, owner in nodes:
        if isinstance(node, VarDecl):
            declarations[(owner.name if owner else "", node.name)] = node

    def binding(name, owner):
        scope = owner.name if owner else ""
        if owner and (name in owner.node.params
                      or (scope, name) in declarations):
            return scope, name
        return "", name

    def spec_of(expr, owner, seen=frozenset()):
        if isinstance(expr, Identifier):
            scope, name = binding(expr.name, owner)
            key = (scope, name)
            if key in seen:
                return None
            if owner and scope and name in owner.node.params:
                index = owner.node.params.index(name)
                specs = owner.param_type_specs
                return specs[index] if index < len(specs) else None
            decl = declarations.get(key)
            if decl is not None:
                hint = gen._type_spec_from_hint_name(decl.type_hint)
                if hint is not None:
                    return hint
                return spec_of(decl.value, owner if scope else None, seen | {key})
        if isinstance(expr, MemberAccess):
            record = spec_of(expr.object, owner, seen)
            if record is not None and record.kind == "udt":
                return gen._udt_field_type_specs.get(record.name, {}).get(expr.member)
            return None
        if isinstance(expr, Subscript):
            return spec_of(expr.object, owner, seen)
        if isinstance(expr, FuncCall):
            fn, ns = gen._resolve_callee(expr.callee)
            if ns in gen._udt_defs and fn == "new":
                return TypeSpec.udt(ns)
            callees = gen._wide_callable_args(expr)
            if callees:
                info = callees[0][0]
                if info.return_type_spec is not None:
                    return info.return_type_spec
                if info.udt_return_type:
                    return TypeSpec.udt(info.udt_return_type)
            if fn in {"copy", "slice", "concat"}:
                receiver = None
                if ns == "array":
                    receiver = expr.args[0] if expr.args else expr.kwargs.get("id")
                elif isinstance(expr.callee, MemberAccess):
                    receiver = expr.callee.object
                receiver_spec = spec_of(receiver, owner, seen)
                if _int_array(receiver_spec, include_wide=True):
                    return receiver_spec
        return gen._type_spec_from_expr(expr) if expr is not None else None

    def slots(expr, owner):
        if isinstance(expr, ExprStmt):
            return slots(expr.expr, owner)
        if isinstance(expr, Assignment):
            return slots(expr.target, owner)
        if isinstance(expr, VarDecl):
            spec = spec_of(Identifier(name=expr.name), owner)
            return ({("name", *binding(expr.name, owner))}
                    if _int_array(spec, include_wide=True) else set())
        if isinstance(expr, Ternary):
            return slots(expr.true_val, owner) | slots(expr.false_val, owner)
        if isinstance(expr, IfStmt):
            return tail(expr.body, owner) | tail(expr.else_body, owner)
        if isinstance(expr, SwitchStmt):
            result = tail(expr.default_body, owner)
            for _condition, body in expr.cases:
                result |= tail(body, owner)
            return result
        if not _int_array(spec_of(expr, owner), include_wide=True):
            return set()
        if isinstance(expr, Identifier):
            return {("name", *binding(expr.name, owner))}
        if isinstance(expr, MemberAccess):
            record = spec_of(expr.object, owner)
            if record is not None and (record.name, expr.member) in fields:
                return {("field", record.name, expr.member)}
        if isinstance(expr, Subscript):
            return slots(expr.object, owner)
        if isinstance(expr, FuncCall):
            callees = gen._wide_callable_args(expr)
            if callees:
                return {("return", info.name) for info, _bound in callees}
            return {("expression", id(expr))}
        return set()

    def tail(body, owner):
        return slots(body[-1], owner) if body else set()

    graph = {}
    wide = set()

    def connect(left, right):
        for a in left:
            graph.setdefault(a, set()).update(right)
        for b in right:
            graph.setdefault(b, set()).update(left)

    with gen._int_width_scan(epoch_only=True):
        for node, owner in nodes:
            if isinstance(node, (VarDecl, Assignment)):
                connect(slots(node, owner), slots(node.value, owner))
            if not isinstance(node, FuncCall):
                continue
            fn, ns = gen._resolve_callee(node.callee)
            callees = gen._wide_callable_args(node)
            for info, bound in callees:
                for index, actual in bound:
                    if index < len(info.param_type_specs) and _int_array(
                            info.param_type_specs[index], include_wide=True):
                        connect({("name", info.name, info.node.params[index])},
                                slots(actual, owner))
            if callees:
                continue
            if ns in gen._udt_defs and fn == "new":
                for index, field in enumerate(gen._udt_defs[ns]):
                    if (ns, field.name) not in fields:
                        continue
                    actual = (node.args[index] if index < len(node.args)
                              else node.kwargs.get(field.name, field.default))
                    connect({("field", ns, field.name)}, slots(actual, owner))
            receiver = None
            if ns == "array":
                receiver = node.args[0] if node.args else node.kwargs.get("id")
            elif isinstance(node.callee, MemberAccess):
                receiver = node.callee.object
            if fn in gen._ARRAY_ELEMENT_WRITERS:
                functional, method = gen._ARRAY_ELEMENT_WRITERS[fn]
                index = functional if ns == "array" else method
                value = (node.args[index] if index < len(node.args)
                         else node.kwargs.get("value"))
                if gen._expr_returns_wide_int(value, owner, set(), None):
                    wide |= slots(receiver, owner)
            if fn in {"copy", "slice", "concat"}:
                connect(slots(node, owner), slots(receiver, owner))
                if fn == "concat":
                    index = 1 if ns == "array" else 0
                    other = (node.args[index] if index < len(node.args)
                             else node.kwargs.get("id2"))
                    connect(slots(node, owner), slots(other, owner))
            if ns == "array" and fn in {"new", "new_int", "from"}:
                values = (node.args if fn == "from" else
                          [node.args[1] if len(node.args) > 1
                           else node.kwargs.get("initial_value")])
                if any(gen._expr_returns_wide_int(v, owner, set(), None) for v in values):
                    wide |= slots(node, owner)
        for info in gen.ctx.func_infos:
            if info.node is not None and _int_array(info.return_type_spec, include_wide=True):
                connect({("return", info.name)}, tail(info.node.body, info))
        for type_name, decl_fields in gen._udt_defs.items():
            for field in decl_fields:
                if (type_name, field.name) in fields:
                    connect({("field", type_name, field.name)}, slots(field.default, None))

    pending = list(wide)
    while pending:
        current = pending.pop()
        for adjacent in graph.get(current, ()):
            if adjacent not in wide:
                wide.add(adjacent)
                pending.append(adjacent)
    return {(type_name, name) for type_name, name in fields
            if ("field", type_name, name) in wide}


def _int_array(spec, include_wide=False):
    return (spec is not None and spec.kind == "array"
            and spec.element is not None and spec.element.kind == "primitive"
            and (spec.element.name == "int"
                 or (include_wide and spec.element.name == "int64")))
