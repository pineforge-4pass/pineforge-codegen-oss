"""Qualifier evidence for bound call arguments, independent of storage.

Series<T> records history storage, not Pine's type qualifier. Reuse the
library linker's fixed-expression inventories, with declaration identities
from ValueBindings so a shadowed name cannot change another binding's proof.
An unknown expression supplies no evidence for a new refusal.
"""

from __future__ import annotations

from ..ast_nodes import (
    Assignment, BinOp, BoolLiteral, ColorLiteral, EnumDecl, ExprStmt,
    ForInStmt, ForStmt, FuncCall, FuncDef, Identifier, IfStmt, MemberAccess,
    MethodDef, NaLiteral, NumberLiteral, StringLiteral, Subscript, SwitchStmt,
    Ternary, TupleAssign, TupleLiteral, TypeDecl, UnaryOp, VarDecl, WhileStmt,
)
from ..errors import Phase
from ..library_inline import (
    _CONST_MEMBER_NAMESPACES, _PURE_CALL_NAMESPACES, _PURE_CALLS,
    _PURE_TIMEFRAME_CALLS, _SIMPLE_MEMBER_NAMESPACES,
)
from ..limits import iter_ast_nodes
from ..method_binding import MethodBindError, bind_method_call, inventory_method_signatures
from ..pine_spelling import expr_start
from .diagnostics import DiagnosticsHelper
from .tables import BUILTIN_VARS


_CONST, _INPUT, _SIMPLE, _SERIES = range(4)
_RANKS = {"const": _CONST, "input": _INPUT, "simple": _SIMPLE, "series": _SERIES}
_NAMES = tuple(_RANKS)
_LITERALS = (NumberLiteral, StringLiteral, BoolLiteral, ColorLiteral, NaLiteral)

# Parameter order is the positional order; names also bind keyword arguments.
# allow_entry_in's Pine base type is string; this table checks qualifiers only.
_BUILTIN_PARAMETERS = {
    "strategy.risk.allow_entry_in": (("value", "simple"),),
}


def _join(values):
    known = [value for value in values if value is not None]
    return max(known) if known else None


def _declared_qualifiers(definition):
    notes = definition.annotations or {}
    return notes.get("param_qualifiers") or notes.get("method_param_qualifiers", ())


class ParameterQualifiers(DiagnosticsHelper):
    """Read-only qualifier queries shared by user and builtin contracts."""

    def __init__(self, program, bindings, filename, budget=None):
        self._filename = filename
        self.budget = budget
        self.bindings = bindings
        self.active = any(
            qualifier in {"simple", "const"}
            for node in program.body if isinstance(node, (FuncDef, MethodDef))
            for qualifier in _declared_qualifiers(node)
        ) or any(
            isinstance(node, FuncCall)
            and self._builtin_name(node) in _BUILTIN_PARAMETERS
            for node, _ in iter_ast_nodes(program)
        )
        if not self.active:
            return
        self.nodes = {id(node): node for node, _ in iter_ast_nodes(program)}
        self.functions = {}
        self.methods = inventory_method_signatures(program)
        self.types = {}
        self.enums = set()
        self.writes = {}
        self.controls = {}
        self.calls = {}
        self.expressions = {}
        self.returns = {}
        for node in self.nodes.values():
            if isinstance(node, FuncDef):
                self.functions.setdefault(node.name, []).append(node)
            elif isinstance(node, EnumDecl):
                self.enums.add(node.name)
            elif isinstance(node, TypeDecl):
                self.types[node.name] = {field.name: field.type_name for field in node.fields}
            elif isinstance(node, Assignment) and isinstance(node.target, Identifier):
                binding = bindings.get(id(node.target))
                if binding is not None:
                    self.writes.setdefault(binding, []).append(node)
            if isinstance(node, (FuncDef, MethodDef)):
                self.calls[id(node)] = [
                    child for child, _ in iter_ast_nodes(node)
                    if isinstance(child, FuncCall)
                ]
        self._record_controls(program.body, ())

    def _record_controls(self, body, controls):
        for node in body:
            if isinstance(node, Assignment):
                self.controls[id(node)] = controls
            elif isinstance(node, IfStmt):
                self._record_controls(node.body, (*controls, node.condition))
                self._record_controls(node.else_body, (*controls, node.condition))
            elif isinstance(node, SwitchStmt):
                conditions = (node.expr, *(expr for expr, _ in node.cases))
                for _, branch in node.cases:
                    self._record_controls(branch, (*controls, *conditions))
                self._record_controls(node.default_body, (*controls, *conditions))
            elif isinstance(node, (ForStmt, WhileStmt, ForInStmt)):
                conditions = (
                    (node.start, node.end, node.step) if isinstance(node, ForStmt)
                    else (node.condition,) if isinstance(node, WhileStmt)
                    else (node.iterable,)
                )
                self._record_controls(node.body, (*controls, *conditions))
            elif isinstance(node, (FuncDef, MethodDef)):
                self._record_controls(node.body, ())

    def check(self, definition, values, function_name):
        if self.active:
            self._call(definition, values, function_name, {})

    def _builtin_name(self, call):
        parts = []
        root = call.callee
        while isinstance(root, MemberAccess):
            parts.append(root.member)
            root = root.object
        if isinstance(root, Identifier) and self.bindings.get(id(root)) is None:
            return ".".join((root.name, *reversed(parts)))
        return None

    def check_builtin(self, call, environment=None):
        if not self.active:
            return
        function_name = self._builtin_name(call)
        for index, (name, qualifier) in enumerate(
            _BUILTIN_PARAMETERS.get(function_name, ())
        ):
            value = call.args[index] if index < len(call.args) else call.kwargs.get(name)
            self._check_argument(
                function_name, name, qualifier, value,
                self.infer(value, environment if environment is not None else {}),
            )

    def _check_argument(self, function_name, name, qualifier, value, rank):
        required = _RANKS.get(qualifier)
        if required is not None and rank is not None and rank > required:
            argument_qualifier = _NAMES[rank]
            parameter_qualifier = qualifier
            self._error(
                f"Cannot call '{function_name}' with a {argument_qualifier} "
                f"argument for {parameter_qualifier} parameter '{name}'.",
                expr_start(value).loc or value.loc,
            )

    @staticmethod
    def _bound(definition, call):
        defaults = (definition.annotations or {}).get("param_defaults", ())
        values = [
            defaults[index] if index < len(defaults) else None
            for index in range(len(definition.params))
        ]
        for index, arg in enumerate(call.args[:len(values)]):
            values[index] = arg
        for name, value in call.kwargs.items():
            if name in definition.params:
                values[definition.params.index(name)] = value
        return values

    def _call(self, definition, values, function_name, environment):
        notes = definition.annotations or {}
        if notes.get("pine_version") == 5 or notes.get("scalar_overload_fallback"):
            return None
        ranks = [self.infer(value, environment) for value in values]
        qualifiers = list(_declared_qualifiers(definition))
        qualifiers += [None] * (len(definition.params) - len(qualifiers))
        for name, qualifier, value, rank in zip(
            definition.params, qualifiers, values, ranks,
        ):
            self._check_argument(function_name, name, qualifier, value, rank)
        effective = tuple(
            _RANKS.get(qualifier, rank)
            for qualifier, rank in zip(qualifiers, ranks)
        )
        key = (id(definition), effective)
        if key not in self.returns:
            self.returns[key] = None  # Break recursive call paths.
            local = {
                (id(definition), name): rank
                for name, rank in zip(definition.params, effective)
            }
            for call in self.calls.get(id(definition), ()):
                self.infer(call, local)
            result = self.infer(definition.body[-1], local) if definition.body else None
            if isinstance(definition, MethodDef) and notes.get("library"):
                # Linked methods retain the linker's existing qualifier-only
                # overload selection, which cannot prove their authored
                # return qualifier. Their explicit argument contracts above
                # are still checked.
                result = None
            # User functions return simple or series, including on literals.
            self.returns[key] = max(_SIMPLE, result) if result is not None else None
        return self.returns[key]

    def infer(self, node, environment):
        if node is None:
            return None
        key = (id(node), tuple(environment.items()))
        if key not in self.expressions:
            self.expressions[key] = None  # Break cyclic initializers/writes.
            if self.budget is not None:
                self.budget.check(node.loc, Phase.ANALYZER)
            self.expressions[key] = self._infer(node, environment)
        return self.expressions[key]

    def _receiver_hint(self, node, seen=frozenset()):
        """Only source-proven receiver types; unresolved methods stay unknown."""
        if node is None or id(node) in seen:
            return None
        seen = seen | {id(node)}
        if isinstance(node, Identifier):
            binding = self.bindings.get(id(node))
            if binding is None:
                builtin = BUILTIN_VARS.get(node.name)
                return builtin.value if builtin is not None else None
            declaration = self.nodes.get(binding[0])
            if isinstance(declaration, (FuncDef, MethodDef)):
                hints = (declaration.annotations or {}).get("param_type_hints", ())
                index = declaration.params.index(binding[1])
                return hints[index] if index < len(hints) else None
            if isinstance(declaration, VarDecl):
                return declaration.type_hint or self._receiver_hint(declaration.value, seen)
        if isinstance(node, MemberAccess):
            owner = self._receiver_hint(node.object, seen)
            return self.types.get(owner, {}).get(node.member)
        if isinstance(node, NumberLiteral):
            return "float" if isinstance(node.value, float) else "int"
        if isinstance(node, StringLiteral):
            return "string"
        if isinstance(node, BoolLiteral):
            return "bool"
        if isinstance(node, FuncCall):
            callee = node.callee
            if isinstance(callee, Identifier) and callee.name in _PURE_CALLS:
                return callee.name if callee.name in {"int", "float", "bool", "string", "color"} else None
            if isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier):
                namespace = callee.object.name
                if callee.member == "new" and namespace in self.types:
                    return namespace
                if namespace == "array" and callee.member == "from" and node.args:
                    hints = (callee.annotations or {}).get("template_args", ())
                    if hints:
                        return f"array<{hints[0]}>"
                    elements = {self._receiver_hint(arg, seen) for arg in node.args}
                    element = (
                        next(iter(elements)) if len(elements) == 1
                        else "float" if elements <= {"int", "float"} else None
                    )
                    return f"array<{element}>" if element is not None else None
                if namespace in {"array", "matrix", "map"} and callee.member == "new":
                    hints = (callee.annotations or {}).get("template_args", ())
                    return f"{namespace}<{','.join(hints)}>" if hints else None
        return None

    def _infer(self, node, environment):
        rank = lambda value: self.infer(value, environment)
        if isinstance(node, _LITERALS):
            return _CONST
        if isinstance(node, Identifier):
            binding = self.bindings.get(id(node))
            if binding is not None:
                if binding in environment:
                    return environment[binding]
                declaration = self.nodes.get(binding[0])
                if isinstance(declaration, (FuncDef, MethodDef)):
                    qualifiers = _declared_qualifiers(declaration)
                    index = declaration.params.index(binding[1])
                    return _RANKS.get(qualifiers[index]) if index < len(qualifiers) else None
                if isinstance(declaration, ForStmt):
                    return _join(map(rank, (
                        declaration.start, declaration.end, declaration.step,
                    )))
                if isinstance(declaration, ForInStmt):
                    return _SERIES
                if declaration is not None:
                    values = [rank(declaration)]
                    for write in self.writes.get(binding, ()):
                        values.append(rank(write.value))
                        values.extend(rank(control) for control in self.controls.get(id(write), ()))
                    return _join(values)
                return None
            return _SERIES if node.name in BUILTIN_VARS and node.name != "na" else None
        if isinstance(node, VarDecl):
            declared = _RANKS.get((node.annotations or {}).get("qualifier"))
            return _join((declared, rank(node.value)))
        if isinstance(node, TupleAssign):
            return rank(node.value)  # Pine gives all tuple elements one qualifier.
        if isinstance(node, (ExprStmt, Assignment)):
            return rank(node.expr if isinstance(node, ExprStmt) else node.value)
        if isinstance(node, MemberAccess):
            obj = node.object
            if isinstance(obj, Identifier) and self.bindings.get(id(obj)) is None:
                if (
                    obj.name in _CONST_MEMBER_NAMESPACES or obj.name in self.enums
                    or obj.name in {"math", "dayofweek"}
                ):
                    return _CONST
                if obj.name in _SIMPLE_MEMBER_NAMESPACES or obj.name == "chart":
                    return _SIMPLE
                if obj.name == "session":
                    # Session names are constant strings; the flags vary by bar.
                    return _CONST if node.member in {"regular", "extended"} else _SERIES
                if obj.name in {"barstate", "ta"}:
                    return _SERIES
                return None
            # Only a source-proven object field supplies series evidence.
            owner = self._receiver_hint(obj)
            return _SERIES if owner in self.types else None
        if isinstance(node, Subscript):
            return _SERIES
        if isinstance(node, BinOp):
            return _join((rank(node.left), rank(node.right)))
        if isinstance(node, UnaryOp):
            return rank(node.operand)
        if isinstance(node, Ternary):
            return _join(map(rank, (node.condition, node.true_val, node.false_val)))
        if isinstance(node, TupleLiteral):
            return _join(map(rank, node.elements))
        if isinstance(node, IfStmt):
            tails = [body[-1] for body in (node.body, node.else_body) if body]
            return _join(map(rank, [node.condition, *tails]))
        if isinstance(node, SwitchStmt):
            parts = [node.expr]
            for expression, body in node.cases:
                parts.extend((expression, body[-1] if body else None))
            if node.default_body:
                parts.append(node.default_body[-1])
            return _join(map(rank, parts))
        if isinstance(node, (ForStmt, WhileStmt, ForInStmt)):
            controls = (
                (node.start, node.end, node.step) if isinstance(node, ForStmt)
                else (node.condition,) if isinstance(node, WhileStmt)
                else (node.iterable,)
            )
            return _join(map(rank, (*controls, node.body[-1] if node.body else None)))
        if isinstance(node, FuncCall):
            self.check_builtin(node, environment)
            callee = node.callee
            if isinstance(callee, Identifier):
                definitions = self.functions.get(callee.name, ())
                if len(definitions) == 1:
                    definition = definitions[0]
                    return self._call(
                        definition, self._bound(definition, node), callee.name, environment,
                    )
                if callee.name == "input":
                    return _INPUT
                if callee.name in _PURE_CALLS or callee.name == "fixnan":
                    return _join(map(rank, [*node.args, *node.kwargs.values()]))
                if callee.name in BUILTIN_VARS:
                    return _SERIES
                if callee.name == "timestamp":
                    return _join(map(rank, [*node.args, *node.kwargs.values()]))
                return None
            if isinstance(callee, MemberAccess):
                obj, member = callee.object, callee.member
                receiver_hint = self._receiver_hint(obj)
                signature = self.methods.get(f"{receiver_hint}.{member}")
                if signature is not None and signature.declaration is not None:
                    try:
                        arguments = bind_method_call(signature, node).args_by_param
                    except MethodBindError:
                        return None  # The analyzer owns argument-binding diagnostics.
                    return self._call(
                        signature.declaration, [obj, *arguments], signature.key, environment,
                    )
                namespace = (
                    obj.name if isinstance(obj, Identifier)
                    and self.bindings.get(id(obj)) is None else None
                )
                if namespace == "input":
                    return _SERIES if member == "source" else _INPUT
                if namespace in {"array", "matrix", "map", "ta", "request"}:
                    return _SERIES
                if namespace == "math" and member in {"random", "sum"}:
                    return _SERIES
                if (
                    namespace in _PURE_CALL_NAMESPACES
                    or namespace in {"math", "color"}
                    or (namespace == "timeframe" and member in _PURE_TIMEFRAME_CALLS)
                ):
                    return _join(map(rank, [*node.args, *node.kwargs.values()]))
                if namespace in {"time", "time_close"}:
                    return _SERIES
                if namespace is None and receiver_hint is not None and (
                    receiver_hint.startswith(("array<", "matrix<", "map<"))
                    or receiver_hint in {"line", "label", "box", "table", "linefill", "polyline"}
                ):
                    # Builtin collection/drawing receiver calls return series.
                    return _SERIES
        return None
