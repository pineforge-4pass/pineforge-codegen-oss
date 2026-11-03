"""Pine value types for text conversion, independent of C++ storage types."""

from __future__ import annotations

from collections import defaultdict

from .. import signatures
from ..analyzer.value_bindings import ValueBindings
from ..ast_nodes import (
    Assignment, BinOp, BoolLiteral, ExprStmt, FuncCall, FuncDef, Identifier,
    IfStmt, MemberAccess, MethodDef, NumberLiteral, StringLiteral, Subscript,
    SwitchStmt, Ternary, UnaryOp, VarDecl,
)
from ..errors import Phase
from ..limits import iter_ast_nodes, syntax_children
from ..symbols import PineType, TypeSpec


_BOOL = TypeSpec.primitive("bool")
_FLOAT = TypeSpec.primitive("float")
_PRIMITIVES = {
    PineType.BOOL: _BOOL,
    PineType.INT: TypeSpec.primitive("int"),
    PineType.FLOAT: _FLOAT,
    PineType.STRING: TypeSpec.primitive("string"),
    PineType.COLOR: TypeSpec.primitive("color"),
}


def _bindings_key(bindings):
    """A hashable, name-ordered copy of (function, parameter) -> type bindings."""
    return tuple(sorted(bindings.items()))


class BoolTextTypes:
    """Follow exact bindings and written arguments without re-running analysis.

    This query never supplies a storage, parameter, history or condition type.
    In particular a Pine bool may occupy a legacy numeric slot; changing that
    slot would also change code emitted for scripts which never produce text.
    """

    def __init__(self, ctx, hint_spec, budget=None):
        self.ctx = ctx
        self.hint_spec = hint_spec
        self.budget = budget
        self.functions = {info.name: info for info in ctx.func_infos}
        self.nodes = {}
        self.owners = {}
        self.writes = defaultdict(list)
        self.calls = defaultdict(list)
        self.user_calls = {}
        self.steps = 0

        def collect(root, owner):
            for node, _depth in iter_ast_nodes(root):
                self.steps += 1
                if self.budget is not None and self.steps % 128 == 0:
                    self.budget.check(getattr(node, "loc", None), Phase.CODEGEN)
                self.nodes[id(node)] = node
                self.owners[id(node)] = owner
                if isinstance(node, Assignment) and isinstance(node.target, Identifier):
                    binding = self._binding_of(node.target)
                    if binding is not None:
                        self.writes[binding].append(node.value)

        for stmt in ctx.ast.body:
            if not isinstance(stmt, (FuncDef, MethodDef)):
                collect(stmt, None)
        for name, info in self.functions.items():
            if info.node is not None:
                for stmt in info.node.body:
                    collect(stmt, name)

        for node in self.nodes.values():
            if not isinstance(node, FuncCall):
                continue
            if isinstance(node.callee, Identifier) and node.callee.name in self.functions:
                info = self.functions[node.callee.name]
                args = self._arguments(info.node.params, node)
                defaults = (info.node.annotations or {}).get("param_defaults", ())
                for index, default in enumerate(defaults):
                    if index < len(args) and args[index] is None:
                        args[index] = default
                self.user_calls[id(node)] = (info, args)
                self.calls[info.name].append((node, args))
        for (call_id, name), binding in ctx.method_call_bindings.items():
            if call_id in self.nodes and name in self.functions:
                info = self.functions[name]
                args = list(binding.args_by_param)
                self.user_calls[call_id] = (info, args)
                self.calls[name].append((self.nodes[call_id], args))

    def _binding_of(self, node: Identifier) -> tuple[int, str] | None:
        # Some analyzer dispatchers leave keyword values unvisited. The lexical
        # pass still bound those reads; use that identity only in this query.
        # A visited read's snapshot remains authoritative, including None.
        if id(node) in self.ctx.identifier_decl_bindings:
            return self.ctx.identifier_decl_bindings[id(node)]
        return ValueBindings.declaration_of(node)

    @staticmethod
    def _arguments(names, call, receiver=None):
        positional = ([receiver] if receiver is not None else []) + list(call.args)
        return [
            positional[index] if index < len(positional) else call.kwargs.get(name)
            for index, name in enumerate(names)
        ]

    @staticmethod
    def _terminal(body):
        if not body:
            return None
        node = body[-1]
        return node.expr if isinstance(node, ExprStmt) else node

    def is_bool(self, node, owner=None, call_site=None):
        cache = {}
        visiting = set()

        def join(values):
            values = list(values)
            return values[0] if values and all(v == values[0] for v in values) else None

        def parameter(name, param, env):
            key = (name, param)
            if key in env:
                return env[key]
            info = self.functions[name]
            index = info.node.params.index(param)
            declared = self.ctx.func_declared_param_type_specs.get(name, ())
            if index < len(declared) and declared[index] is not None:
                return declared[index]
            guard = ("parameter", name, param)
            if guard in visiting:
                return None
            visiting.add(guard)
            try:
                actuals = self.calls.get(name, ())
                if name == owner and call_site is not None:
                    actuals = [
                        (call, args) for call, args in actuals
                        if self.ctx.func_call_cs_map.get(id(call)) == (name, call_site)
                    ]
                return join(
                    spec(args[index], env)
                    for _, args in actuals if index < len(args)
                )
            finally:
                visiting.remove(guard)

        def identifier(value, env):
            binding = self._binding_of(value)
            if binding is not None:
                declaration = self.nodes.get(binding[0])
                if not isinstance(declaration, VarDecl):
                    return None
                if declaration.type_hint:
                    return self.hint_spec(declaration.type_hint)
                return join(
                    spec(rhs, env) for rhs in
                    [declaration.value, *self.writes.get(binding, ())]
                )
            name = self.owners.get(id(value))
            scope = self.ctx.identifier_binding_scopes.get(id(value))
            info = self.functions.get(name)
            if (info is not None and value.name in info.node.params
                    and scope != "global"):
                return parameter(name, value.name, env)
            if scope == "global" or name is None:
                symbol = self.ctx.symbols.resolve(value.name)
                if symbol is not None:
                    return symbol.type_spec or _PRIMITIVES.get(symbol.pine_type)
            return None

        def call_type(value, env):
            user = self.user_calls.get(id(value))
            if user is not None:
                info, args = user
                bound = dict(env)
                for param, arg in zip(info.node.params, args):
                    bound[(info.name, param)] = spec(arg, env)
                return spec(self._terminal(info.node.body), bound)

            callee = value.callee
            namespace = None
            receiver = None
            receiver_spec = None
            if isinstance(callee, Identifier):
                member = callee.name
            elif isinstance(callee, MemberAccess):
                member = callee.member
                receiver_spec = spec(callee.object, env)
                if receiver_spec is not None and receiver_spec.kind in ("array", "map", "matrix"):
                    namespace = receiver_spec.kind
                    receiver = callee.object
                elif isinstance(callee.object, Identifier):
                    namespace = callee.object.name
            else:
                return None
            method_receiver = receiver
            full_name = f"{namespace}.{member}" if namespace else member
            template = (callee.annotations or {}).get("template_args", ())
            if namespace in ("array", "matrix") and (
                    member == "new" or (namespace == "array" and member.startswith("new_"))):
                element = self.hint_spec(str(template[0])) if template else (
                    self.hint_spec(member[4:]) if member.startswith("new_") else _FLOAT
                )
                return TypeSpec(kind=namespace, element=element)
            if namespace == "array" and member == "from":
                return TypeSpec.array(join(spec(arg, env) for arg in value.args) or _FLOAT)
            if namespace == "map" and member == "new" and len(template) == 2:
                return TypeSpec.map(*(self.hint_spec(str(t)) for t in template))
            if namespace == "request" and member == "security_lower_tf":
                payload = value.args[2] if len(value.args) > 2 else value.kwargs.get("expression")
                return TypeSpec.array(spec(payload, env) or _FLOAT)
            if namespace in ("array", "matrix", "map"):
                receiver = receiver if receiver is not None else (
                    value.args[0] if value.args else value.kwargs.get("id")
                )
                receiver_spec = spec(receiver, env)
                if receiver_spec is not None:
                    if namespace == "array" and member in ("get", "first", "last", "pop", "shift", "remove"):
                        return receiver_spec.element
                    if namespace == "matrix" and member == "get":
                        return receiver_spec.element
                    if namespace == "map" and member in ("put", "get", "remove"):
                        return receiver_spec.value
                    if member in ("copy", "slice"):
                        return receiver_spec
            function = signatures.get_value_function(full_name)
            if function is not None:
                bool_overloads = [s for s in function.signatures if s.return_type == PineType.BOOL]
                if len(bool_overloads) == len(function.signatures):
                    return _BOOL
                for signature in bool_overloads:
                    args = self._arguments(
                        [p.name for p in signature.params], value,
                        method_receiver,
                    )
                    # Select source/payload-dependent bool overloads using Pine
                    # argument types, rather than the legacy arity-only choice.
                    if all(spec(arg, env) == _BOOL for p, arg in zip(signature.params, args)
                           if p.pine_type == PineType.BOOL):
                        return _BOOL
                return _PRIMITIVES.get(function.primary.return_type)
            if namespace in self.ctx.udt_defs and member == "new":
                return TypeSpec.udt(namespace)
            return None

        def infer(value, env):
            if isinstance(value, BoolLiteral):
                return _BOOL
            if isinstance(value, NumberLiteral):
                return _FLOAT if isinstance(value.value, float) else TypeSpec.primitive("int")
            if isinstance(value, StringLiteral):
                return TypeSpec.primitive("string")
            if isinstance(value, (ExprStmt, VarDecl, Assignment)):
                return spec(value.expr if isinstance(value, ExprStmt) else value.value, env)
            if isinstance(value, Identifier):
                return identifier(value, env)
            if isinstance(value, Subscript):
                return spec(value.object, env)
            if isinstance(value, UnaryOp):
                return _BOOL if value.op == "not" else spec(value.operand, env)
            if isinstance(value, BinOp):
                return _BOOL if value.op in ("==", "!=", "<", ">", "<=", ">=", "and", "or") else _FLOAT
            if isinstance(value, Ternary):
                return join(spec(arm, env) for arm in (value.true_val, value.false_val))
            if isinstance(value, IfStmt):
                return join(spec(self._terminal(body), env) for body in (value.body, value.else_body))
            if isinstance(value, SwitchStmt):
                bodies = [body for _, body in value.cases]
                bodies.append(value.default_body)
                return join(spec(self._terminal(body), env) for body in bodies)
            if isinstance(value, MemberAccess):
                receiver_spec = spec(value.object, env)
                if receiver_spec is not None and receiver_spec.kind == "udt":
                    return self.ctx.udt_field_type_specs.get(receiver_spec.name, {}).get(value.member)
                if isinstance(value.object, Identifier) and receiver_spec is None:
                    return _PRIMITIVES.get(signatures.get_variable_type(value.object.name, value.member))
            if isinstance(value, FuncCall):
                return call_type(value, env)
            return None

        def spec(value, env):
            if value is None:
                return None
            key = (id(value), _bindings_key(env))
            if key in cache:
                return cache[key]
            if key in visiting:
                return None
            self.steps += 1
            if self.budget is not None and self.steps % 128 == 0:
                self.budget.check(getattr(value, "loc", None), Phase.CODEGEN)
            visiting.add(key)
            try:
                result = infer(value, env)
                cache[key] = result
                return result
            finally:
                visiting.remove(key)

        return spec(node, {}) == _BOOL

    def contains_conversion(self, node, owner=None, call_site=None):
        """Whether a text value reaches an explicit bool conversion."""
        seen = set()

        def visit(value):
            if value is None or id(value) in seen:
                return False
            seen.add(id(value))
            if isinstance(value, Identifier):
                binding = self._binding_of(value)
                declaration = self.nodes.get(binding[0]) if binding else None
                if isinstance(declaration, VarDecl):
                    return (visit(declaration.value)
                            or any(visit(rhs) for rhs in self.writes.get(binding, ())))
            if isinstance(value, FuncCall):
                callee = value.callee
                conversion = (
                    isinstance(callee, Identifier) and callee.name == "string"
                ) or (
                    isinstance(callee, MemberAccess) and callee.member == "tostring"
                    and isinstance(callee.object, Identifier) and callee.object.name == "str"
                )
                argument = value.args[0] if value.args else value.kwargs.get("value")
                if conversion and self.is_bool(argument, owner, call_site):
                    return True
                user = self.user_calls.get(id(value))
                if user is not None and visit(self._terminal(user[0].node.body)):
                    return True
            return any(visit(child) for child in syntax_children(value))

        return visit(node)
