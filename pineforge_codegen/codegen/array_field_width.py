"""Keep connected integer arrays at one width, including callable boundaries."""

from dataclasses import dataclass, field

from ..ast_nodes import (
    Assignment, BinOp, ExprStmt, ForInStmt, ForStmt, FuncCall, FuncDef,
    Identifier, IfStmt, MemberAccess, MethodDef, Subscript, SwitchStmt,
    Ternary, TupleAssign, UnaryOp, VarDecl, WhileStmt,
)
from ..symbols import PineType, TypeSpec


@dataclass
class ArrayWidths:
    wide: set = field(default_factory=set)
    nodes: dict = field(default_factory=dict)
    names: dict = field(default_factory=dict)
    specs: dict = field(default_factory=dict)
    name_specs: dict = field(default_factory=dict)
    bindings: dict = field(default_factory=dict)
    elements: dict = field(default_factory=dict)

    def widen(self, spec, slots):
        if _int_array(spec) and not self.wide.isdisjoint(slots):
            return TypeSpec.array(TypeSpec.primitive("int64"))
        return spec


def prepare_array_widths(gen):
    """Specialize an entire connected component, never convert its references.

    A field is keyed by its resolved record type, not its spelling. All
    instances share that layout. Copies need the same element type too, but
    this pass does not change their ownership or lifetime. Unconnected arrays
    containing only small integers retain their original C++. Declarations
    and their authored reads share a storage identity; spelling alone never
    connects two lexical arrays. Declaration, constructor, parameter, result,
    tuple and history types all consume this plan.
    """
    result = ArrayWidths()
    # Type queries made by scalar provenance can see the growing answer.
    gen._array_widths = result
    fields = {
        (type_name, name)
        for type_name, specs in gen._udt_field_type_specs.items()
        for name, spec in specs.items()
        if _int_array(spec)
    }
    nodes = []
    declarations = {}
    declarations_by_id = {}
    declaration_keys = {}
    block_declarations = {}
    parameter_keys = {
        (info.name, name)
        for info in gen.ctx.func_infos if info.node is not None
        for name in info.node.params
    }
    for stmt in gen.ctx.ast.body:
        if not isinstance(stmt, (FuncDef, MethodDef)):
            nodes.extend((child, None) for child in gen._walk_ast(stmt))
    for info in gen.ctx.func_infos:
        if info.node is not None:
            nodes.extend((child, info)
                         for child in gen._walk_ast_list(info.node.body))
    if (not fields
            and not any(_int_array(spec, include_wide=True)
                        for spec in gen._collection_types.values())
            and not any(isinstance(node, FuncCall)
                        and gen._resolve_callee(node.callee)[1] == "array"
                        for node, _owner in nodes)):
        return result
    for node, owner in nodes:
        if isinstance(node, VarDecl):
            key = (owner.name if owner else "", node.name)
            declarations[key] = node
            declaration_keys.setdefault(key, set()).add(
                ("declaration", id(node), node.name))
            declarations_by_id[id(node)] = (node, owner)
        elif isinstance(node, TupleAssign):
            declarations_by_id[id(node)] = (node, owner)
            for index, name in enumerate(node.names):
                element = gen._tuple_element_value(node.value, index, owner)
                if name and name != "_" and element is not None:
                    key = (owner.name if owner else "", name)
                    declarations[key] = element
                    declaration_keys.setdefault(key, set()).add(
                        ("declaration", id(node), name))
        elif isinstance(node, (ForStmt, ForInStmt)):
            declarations_by_id[id(node)] = (node, owner)
        bodies = []
        if isinstance(node, (ForStmt, ForInStmt, WhileStmt)):
            bodies.append((id(node), node.body))
        elif isinstance(node, IfStmt):
            bodies.extend((id(body), body) for body in (node.body, node.else_body))
        elif isinstance(node, SwitchStmt):
            bodies.extend((id(body), body) for _condition, body in node.cases)
            bodies.append((id(node.default_body), node.default_body))
        for block_id, body in bodies:
            for child in body:
                if isinstance(child, VarDecl):
                    block_declarations[(block_id, child.name)] = child

    def binding(name, owner, expr=None):
        scope = owner.name if owner else ""
        resolved = gen.ctx.identifier_decl_bindings.get(id(expr))
        if resolved is not None and resolved[0] in declarations_by_id:
            return ("declaration", *resolved)
        if owner and name in owner.node.params:
            return "name", scope, name
        candidates = declaration_keys.get((scope, name), ())
        if len(candidates) == 1:
            return next(iter(candidates))
        return "name", "", name

    def declaration(expr, owner):
        resolved = gen.ctx.identifier_decl_bindings.get(id(expr))
        if resolved is not None and resolved[0] in declarations_by_id:
            decl, lexical_owner = declarations_by_id[resolved[0]]
            if isinstance(decl, TupleAssign):
                index = decl.names.index(expr.name)
                element = gen._tuple_element_value(decl.value, index, lexical_owner)
                return element, lexical_owner
            return decl, lexical_owner
        scope = owner.name if owner else ""
        if owner and expr.name in owner.node.params:
            return None, owner
        if (scope, expr.name) in declarations:
            return declarations[(scope, expr.name)], owner
        return declarations.get(("", expr.name)), None

    def spec_of(expr, owner, seen=frozenset()):
        if isinstance(expr, Identifier):
            key = binding(expr.name, owner, expr)
            if key in seen:
                return None
            decl, lexical_owner = declaration(expr, owner)
            if owner and decl is None and expr.name in owner.node.params:
                index = owner.node.params.index(expr.name)
                specs = owner.param_type_specs
                return specs[index] if index < len(specs) else None
            if isinstance(decl, tuple):
                value, lexical_owner = decl
                return spec_of(value, lexical_owner, seen | {key})
            if isinstance(decl, ForStmt):
                family = gen.ctx.for_counter_types.get(id(decl.start))
                return TypeSpec.primitive("float" if family == PineType.FLOAT else "int")
            if isinstance(decl, ForInStmt):
                iterable = spec_of(decl.iterable, lexical_owner, seen | {key})
                if iterable is not None and iterable.kind == "array":
                    return (TypeSpec.primitive("int")
                            if decl.vars and expr.name == decl.vars[0]
                            else iterable.element)
                if iterable is not None and iterable.kind == "map" and decl.vars:
                    return iterable.key if expr.name == decl.vars[0] else iterable.value
                return None
            if decl is not None:
                hint = gen._type_spec_from_hint_name(decl.type_hint)
                if hint is not None:
                    return hint
                return spec_of(decl.value, lexical_owner, seen | {key})
        if isinstance(expr, UnaryOp):
            return (TypeSpec.primitive("bool") if expr.op == "not"
                    else spec_of(expr.operand, owner, seen))
        if isinstance(expr, BinOp):
            if expr.op in {"==", "!=", "<", ">", "<=", ">=", "and", "or"}:
                return TypeSpec.primitive("bool")
            operands = [spec_of(arg, owner, seen) for arg in (expr.left, expr.right)]
            if all(spec is not None and spec.kind == "primitive" for spec in operands):
                names = {spec.name for spec in operands}
                if names <= {"int", "int64", "float"}:
                    return TypeSpec.primitive(
                        "float" if expr.op == "/" or "float" in names
                        else "int64" if "int64" in names else "int")
                if expr.op == "+" and names == {"string"}:
                    return TypeSpec.primitive("string")
            return None
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
            if ns is None and fn in {"int", "float", "bool", "string"}:
                return TypeSpec.primitive(fn)
            if gen._expr_is_int64_builtin(expr):
                return TypeSpec.primitive("int")
            if ns == "array" and fn == "from" and expr.args:
                elements = [spec_of(arg, owner, seen)
                            or gen._array_from_element_spec(arg) for arg in expr.args]
                known = [spec for spec in elements if spec is not None]
                if known and all(spec.kind == "primitive" and spec.name in {
                        "int", "int64", "float"} for spec in known):
                    name = ("float" if any(spec.name == "float" for spec in known)
                            else "int64" if any(spec.name == "int64" for spec in known)
                            else "int")
                    return TypeSpec.array(TypeSpec.primitive(name))
                if known:
                    return TypeSpec.array(known[0])
            callees = gen._wide_callable_args(expr)
            if callees:
                info = callees[0][0]
                if info.return_type_spec is not None:
                    return info.return_type_spec
                if info.udt_return_type:
                    return TypeSpec.udt(info.udt_return_type)
            if fn in {"copy", "slice", "concat"} | gen._WIDE_ARRAY_ELEMENT_READS:
                receiver = None
                if ns == "array":
                    receiver = expr.args[0] if expr.args else expr.kwargs.get("id")
                elif isinstance(expr.callee, MemberAccess):
                    receiver = expr.callee.object
                receiver_spec = spec_of(receiver, owner, seen)
                if _int_array(receiver_spec, include_wide=True):
                    return (receiver_spec.element if fn in gen._WIDE_ARRAY_ELEMENT_READS
                            else receiver_spec)
        return gen._type_spec_from_expr(expr) if expr is not None else None

    def slots(expr, owner):
        if isinstance(expr, ExprStmt):
            return slots(expr.expr, owner)
        if isinstance(expr, Assignment):
            return slots(expr.target, owner)
        if isinstance(expr, VarDecl):
            spec = (gen._type_spec_from_hint_name(expr.type_hint)
                    or spec_of(expr.value, owner))
            return ({("declaration", id(expr), expr.name)}
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
            return {binding(expr.name, owner, expr)}
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
    seeds = []
    wide = result.wide

    def connect(left, right):
        for a in left:
            graph.setdefault(a, set()).update(right)
        for b in right:
            graph.setdefault(b, set()).update(left)

    with gen._int_width_scan(epoch_only=True):
        for node, owner in nodes:
            node_slots = slots(node, owner)
            result.nodes[id(node)] = node_slots
            if node_slots:
                spec = (gen._type_spec_from_hint_name(node.type_hint)
                        or spec_of(node.value, owner)
                        if isinstance(node, VarDecl) else spec_of(node, owner))
                if _int_array(spec, include_wide=True):
                    result.specs[id(node)] = spec
            if isinstance(node, VarDecl):
                scope = owner.name if owner else ""
                key = (scope, node.name)
                if (key not in parameter_keys
                        and len(declaration_keys.get(key, ())) == 1):
                    result.names[key] = node_slots
                    if id(node) in result.specs:
                        result.name_specs[key] = result.specs[id(node)]
                # Persistent callable/block variables have a separate member
                # spelling; their class layout must use the same width.
                meta = gen.ctx.var_member_metadata_by_node.get(id(node))
                if meta is not None:
                    result.names.setdefault(("", meta[1]), set()).update(node_slots)
                    if id(node) in result.specs:
                        result.name_specs[("", meta[1])] = result.specs[id(node)]
            if isinstance(node, (VarDecl, Assignment)):
                connect(slots(node, owner), slots(node.value, owner))
            elif isinstance(node, TupleAssign):
                for index, name in enumerate(node.names):
                    element = gen._tuple_element_value(node.value, index, owner)
                    if name and name != "_" and element is not None:
                        value, lexical_owner = element
                        target = {("declaration", id(node), name)}
                        connect(target, slots(value, lexical_owner))
                        spec = spec_of(value, lexical_owner)
                        if _int_array(spec, include_wide=True):
                            result.bindings[(id(node), name)] = (spec, target)
                            key = (owner.name if owner else "", name)
                            if (key not in parameter_keys
                                    and len(declaration_keys.get(key, ())) == 1):
                                result.names[key] = target
                                result.name_specs[key] = spec
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
                        key = (info.name, info.node.params[index])
                        if key not in declaration_keys:
                            result.names.setdefault(key, set()).add(
                                ("name", *key))
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
            if fn in gen._WIDE_ARRAY_ELEMENT_READS:
                result.elements[id(node)] = slots(receiver, owner)
            if fn in gen._ARRAY_ELEMENT_WRITERS:
                functional, method = gen._ARRAY_ELEMENT_WRITERS[fn]
                index = functional if ns == "array" else method
                value = (node.args[index] if index < len(node.args)
                         else node.kwargs.get("value"))
                seeds.append((slots(receiver, owner), value, owner))
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
                seeds.extend((slots(node, owner), value, owner) for value in values)
        for info in gen.ctx.func_infos:
            if info.node is not None and _int_array(info.return_type_spec, include_wide=True):
                connect({("return", info.name)}, tail(info.node.body, info))
        for type_name, decl_fields in gen._udt_defs.items():
            for field in decl_fields:
                if (type_name, field.name) in fields:
                    connect({("field", type_name, field.name)}, slots(field.default, None))

    with gen._int_width_scan(epoch_only=True):
        while True:
            before = len(wide)
            for storage, value, owner in seeds:
                if gen._expr_returns_wide_int(value, owner, set(), None):
                    wide.update(storage)
            pending = list(wide)
            while pending:
                current = pending.pop()
                for adjacent in graph.get(current, ()):
                    if adjacent not in wide:
                        wide.add(adjacent)
                        pending.append(adjacent)
            if len(wide) == before:
                break
            # Scalar reads/returns can now obtain width from an array element.
            for attr in ("_wide_int_provenance_cache",
                         "_wide_int_epoch_provenance_cache",
                         "_wide_udt_int_field_cache"):
                gen.__dict__.pop(attr, None)
    # Captured declarations are used directly for persistent member layout,
    # tuple inference and lexical overlays; specialize these private copies.
    result.bindings = {
        key: result.widen(spec, storage)
        for key, (spec, storage) in result.bindings.items()
        if not wide.isdisjoint(storage)
    }
    for node_id, spec in gen._callable_collection_bindings.items():
        storage = result.nodes.get(node_id, ())
        if not wide.isdisjoint(storage):
            spec = result.specs.get(node_id, spec)
        gen._callable_collection_bindings[node_id] = result.widen(spec, storage)
    for owner, specs in gen._func_collection_types.items():
        for name, spec in specs.items():
            storage = result.names.get((owner, name), ())
            if not wide.isdisjoint(storage):
                spec = result.name_specs.get((owner, name), spec)
            specs[name] = result.widen(spec, storage)
    for block_id, specs in gen._block_collection_types.items():
        for name, spec in specs.items():
            decl = block_declarations.get((block_id, name))
            storage = result.nodes.get(id(decl), ())
            if not wide.isdisjoint(storage):
                spec = result.specs.get(id(decl), spec)
            specs[name] = result.widen(spec, storage)
    # Chart blocks lack callable collection metadata. Open the same lexical
    # overlay for a widened local so its registry cannot escape into a later
    # sibling block that happens to reuse the spelling.
    for (block_id, name), decl in block_declarations.items():
        storage = result.nodes.get(id(decl), ())
        if not wide.isdisjoint(storage):
            gen._block_collection_types.setdefault(block_id, {})[name] = result.widen(
                result.specs[id(decl)], storage)
    for attr in ("_wide_int_provenance_cache",
                 "_wide_int_epoch_provenance_cache",
                 "_wide_udt_int_field_cache"):
        gen.__dict__.pop(attr, None)
    return result


def timestamp_array_fields(gen):
    """Resolved field layouts specialized by the shared storage plan."""
    return {(slot[1], slot[2]) for slot in gen._array_widths.wide
            if slot[0] == "field"}


def _int_array(spec, include_wide=False):
    return (spec is not None and spec.kind == "array"
            and spec.element is not None and spec.element.kind == "primitive"
            and (spec.element.name == "int"
                 or (include_wide and spec.element.name == "int64")))
