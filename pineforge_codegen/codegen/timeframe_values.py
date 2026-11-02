"""Conservative value sets for computed requested timeframes.

Selections contribute every arm, including setup-only selections: their union
is a safe superset without evaluating a chart condition during registration.
Unknown values and unsupported semantics invalidate the entire proof.
"""

from __future__ import annotations

import math
import re
from itertools import product

from ..ast_nodes import (
    Assignment, BinOp, BoolLiteral, ExprStmt, FuncCall, Identifier, IfStmt,
    MemberAccess, NumberLiteral, StringLiteral, SwitchStmt, Ternary, UnaryOp, VarDecl,
)
from ..external_requests import _Taint
from ..limits import (
    MAX_NESTING_DEPTH, MAX_SECURITY_TIMEFRAME_VALUES,
    MAX_SECURITY_TIMEFRAME_WORK, MAX_SOURCE_CHARS, iter_ast_nodes,
)
from ..security_contexts import ScriptIndex
from ..symbols import PineType
from .constant_fold import _constant_arguments, fold_constant_expression


class OpenTimeframeSet(Exception):
    """No complete, bounded inventory can be proved."""


class TimeframeValues:
    """Interpret scalar pure expressions without rendering or running Pine."""

    # Stay within every emitted scalar integer route, excluding its na sentinel.
    _INTEGER_MAX = (1 << 31) - 1
    _PLACEHOLDER = re.compile(r"\{([0-9]+)\}")

    def __init__(self, emitter):
        self.emitter = emitter
        self.index = ScriptIndex(emitter.ctx.ast)
        self.declarations = {(id(decl), decl.name): decl
                             for decl in self.index.decls.values()}
        self.unstable = {key for key, decl in self.declarations.items()
                         if decl.is_var or decl.is_varip}
        for node, _depth in iter_ast_nodes(emitter.ctx.ast):
            if isinstance(node, Assignment) and isinstance(node.target, Identifier):
                self.unstable.add(self._binding(node.target))
        self.work = 0
        self.cache = {}
        self.active = set()
        self.functions = set()

    def _binding(self, node):
        # Prefer declaration-position-aware analyzer provenance. ScriptIndex's
        # block inventory alone can capture a later same-named declaration.
        bindings = self.emitter.ctx.identifier_decl_bindings
        if id(node) in bindings and bindings[id(node)] is not None:
            return bindings[id(node)]
        declarations = self.emitter.ctx.identifier_binding_nodes
        declaration = declarations.get(id(node))
        if isinstance(declaration, VarDecl):
            return (id(declaration), node.name)
        indexed = self.index.refs.get(id(node))
        if indexed is not None and indexed[0] == "param":
            return indexed
        if id(node) in declarations:
            return None
        # Normalization preserves original identifier nodes whenever possible.
        # An unannotated copy must still have an indexed, exact declaration.
        declaration = self.index.decls.get(indexed)
        return ((id(declaration), node.name)
                if declaration is not None else None)

    def _tick(self):
        self.work += 1
        if self.work > MAX_SECURITY_TIMEFRAME_WORK:
            raise OpenTimeframeSet

    def _bounded(self, values):
        result = []
        seen = set()
        for value in values:
            self._tick()
            if type(value) is str:
                if len(value) > MAX_SOURCE_CHARS:
                    raise OpenTimeframeSet
            elif type(value) in (int, float):
                if not math.isfinite(value) or abs(value) > self._INTEGER_MAX:
                    raise OpenTimeframeSet
            elif type(value) is not bool:
                raise OpenTimeframeSet
            key = (type(value), value)
            if key not in seen:
                seen.add(key)
                result.append(value)
                if len(result) > MAX_SECURITY_TIMEFRAME_VALUES:
                    raise OpenTimeframeSet
        if not result:
            raise OpenTimeframeSet
        return tuple(result)

    def strings(self, node):
        values = self._values(node, {})
        if any(type(value) is not str for value in values):
            raise OpenTimeframeSet
        return [StringLiteral(value=value, loc=node.loc) for value in values]

    def _values(self, node, env):
        self._tick()
        key = (id(node), tuple((binding, tuple((type(value), value) for value in values))
                               for binding, values in env.items()))
        if key in self.cache:
            return self.cache[key]
        if key in self.active or len(self.active) >= MAX_NESTING_DEPTH:
            raise OpenTimeframeSet
        self.active.add(key)
        try:
            result = self._bounded(self._evaluate(node, env))
            self.cache[key] = result
            return result
        finally:
            self.active.remove(key)

    @staticmethod
    def _literal(value):
        kind = (StringLiteral if type(value) is str else
                BoolLiteral if type(value) is bool else NumberLiteral)
        return kind(value=value)

    def _fold(self, node):
        value, proven = fold_constant_expression(node, {}, {})
        if not proven:
            raise OpenTimeframeSet
        return value

    def _evaluate(self, node, env):
        if (node.annotations or {}).get("pine_version") == 5:
            raise OpenTimeframeSet
        if isinstance(node, (StringLiteral, NumberLiteral, BoolLiteral)):
            return (node.value,)
        if isinstance(node, Identifier):
            binding = self._binding(node)
            if binding in env:
                return env[binding]
            decl = self.declarations.get(binding)
            if decl is None or binding in self.unstable:
                raise OpenTimeframeSet
            # A caller's locals cannot capture names in a global initializer.
            values = self._values(decl.value, {})
            return self._typed(values, decl.type_hint)
        if isinstance(node, Ternary):
            self._condition(node.condition)
            return (*self._values(node.true_val, env),
                    *self._values(node.false_val, env))
        if isinstance(node, (IfStmt, SwitchStmt)):
            if isinstance(node, IfStmt):
                self._condition(node.condition)
                bodies = (node.body, node.else_body)
            else:
                if node.expr is not None:
                    self._condition(node.expr)
                for case, _body in node.cases:
                    self._condition(case)
                bodies = [body for _case, body in node.cases] + [node.default_body]
            return self._bounded(value for body in bodies
                                 for value in self._block(body, env))
        if isinstance(node, UnaryOp):
            return (self._fold(UnaryOp(op=node.op, operand=self._literal(value)))
                    for value in self._values(node.operand, env))
        if isinstance(node, BinOp):
            if node.op not in ("+", "-", "*", "/", "%", "==", "!=", "<", "<=",
                               ">", ">=", "and", "or"):
                raise OpenTimeframeSet
            operands = (self._values(node.left, env), self._values(node.right, env))
            return (self._fold(BinOp(left=self._literal(left), op=node.op,
                                     right=self._literal(right)))
                    for left, right in product(*operands))
        if isinstance(node, FuncCall):
            return self._call(node, env)
        raise OpenTimeframeSet

    def _typed(self, values, hint):
        if not hint:
            return values
        hint = str(hint).split()[-1]
        expected = {"int": int, "float": float, "bool": bool, "string": str}.get(hint)
        if expected is None:
            raise OpenTimeframeSet
        if expected is float and all(type(value) in (int, float) for value in values):
            return self._bounded(float(value) for value in values)
        if any(type(value) is not expected for value in values):
            # In particular, do not guess at inferred helper-parameter narrowing.
            raise OpenTimeframeSet
        return values

    def _block(self, body, outer):
        if not body:
            # Missing scalar string arms denote the chart timeframe, whose
            # value is not a compile-time string. The legacy path handles it.
            raise OpenTimeframeSet
        env = dict(outer)
        result = None
        for stmt in body:
            if isinstance(stmt, VarDecl) and not (stmt.is_var or stmt.is_varip):
                result = self._typed(self._values(stmt.value, env), stmt.type_hint)
                env[(id(stmt), stmt.name)] = result
            elif isinstance(stmt, ExprStmt):
                result = self._values(stmt.expr, env)
            elif isinstance(stmt, (IfStmt, SwitchStmt)) and stmt is body[-1]:
                result = self._values(stmt, env)
            else:
                # Loops, persistent storage and writes are not pure lets.
                raise OpenTimeframeSet
        if result is None:
            raise OpenTimeframeSet
        return result

    def _condition(self, node, active=frozenset()):
        """A free condition may read chart state, but cannot hide effects."""
        for child, _depth in iter_ast_nodes(node):
            self._tick()
            if isinstance(child, Assignment) or (
                    isinstance(child, VarDecl) and (child.is_var or child.is_varip)):
                raise OpenTimeframeSet
            if isinstance(child, FuncCall):
                name, namespace = self.emitter._resolve_callee(child.callee)
                if namespace is None and name in self.index.funcs:
                    if name in active or name in self.index.overloaded:
                        raise OpenTimeframeSet
                    self._condition(self.index.funcs[name], active | {name})
                elif not _Taint._is_pure(namespace, name):
                    raise OpenTimeframeSet

    def _call(self, node, env):
        if (isinstance(node.callee, MemberAccess)
                and isinstance(node.callee.object, Identifier)
                and self._binding(node.callee.object) is not None):
            # A value receiver is not a builtin namespace, even with its name.
            raise OpenTimeframeSet
        name, namespace = self.emitter._resolve_callee(node.callee)
        if namespace is None and name in self.index.funcs:
            if name in self.functions or name in self.index.overloaded:
                raise OpenTimeframeSet
            definition = self.index.funcs[name]
            if (definition.annotations or {}).get("pine_version") == 5:
                raise OpenTimeframeSet
            infos = [info for info in self.emitter.ctx.func_infos if info.name == name]
            if len(infos) != 1 or len(node.args) > len(definition.params):
                raise OpenTimeframeSet
            supplied = dict(zip(definition.params, node.args))
            for param, value in node.kwargs.items():
                if param not in definition.params or param in supplied:
                    raise OpenTimeframeSet
                supplied[param] = value
            authored = set(supplied)
            if set(supplied) != set(definition.params):
                defaulted = self.emitter._user_call_args_with_defaults(name, node)
                if defaulted is None:
                    raise OpenTimeframeSet
                supplied = dict(zip(definition.params, defaulted))
            bound = {}
            hints = {PineType.INT: "int", PineType.FLOAT: "float",
                     PineType.BOOL: "bool", PineType.STRING: "string"}
            for param, kind in zip(definition.params, infos[0].param_types):
                # Defaults belong to their declaration, not the caller's lets.
                values = self._values(supplied[param], env if param in authored else {})
                bound[("param", name, param)] = self._typed(values, hints.get(kind, "?"))
            if len(bound) != len(definition.params):
                raise OpenTimeframeSet
            self.functions.add(name)
            try:
                return self._typed(self._block(definition.body, bound),
                                   hints.get(infos[0].return_type, "?"))
            finally:
                self.functions.remove(name)
        if not _Taint._is_pure(namespace, name):
            raise OpenTimeframeSet
        # Purity is necessary, not sufficient: only operations with a proven
        # scalar interpretation below are evaluated, never arbitrary callables.
        arguments = _constant_arguments(node) if node.kwargs else node.args
        args = [self._values(argument, env) for argument in arguments]
        return self._bounded(self._builtin(namespace, name, values)
                             for values in product(*args))

    @staticmethod
    def _text(value, grouping=False):
        if type(value) is str:
            return value
        if type(value) is bool:
            return "true" if value else "false"
        # Integral values in the bounded scalar range have an exact decimal
        # spelling under both Pine formatters. Fractional rounding, mintick,
        # patterns and non-finite values require a separate rendering proof.
        if type(value) not in (int, float) or not float(value).is_integer():
            raise OpenTimeframeSet
        return format(int(value), ",d" if grouping else "d")

    def _builtin(self, namespace, name, args):
        self._tick()
        if namespace == "str" and name == "tostring" and len(args) == 1:
            return self._text(args[0])
        if namespace == "str" and name == "format" and args and type(args[0]) is str:
            template, *values = args
            # This is the plain indexed-placeholder subset of MessageFormat.
            # Quoting, styles and unmatched braces must not use Python rules.
            plain = self._PLACEHOLDER.sub("", template)
            if any(char in plain for char in "{}'"):
                raise OpenTimeframeSet
            output_length = len(plain)

            def replace(match):
                nonlocal output_length
                index = int(match.group(1))
                if index >= len(values):
                    raise OpenTimeframeSet
                text = self._text(values[index], grouping=True)
                output_length += len(text)
                if output_length > MAX_SOURCE_CHARS:
                    raise OpenTimeframeSet
                return text

            return self._PLACEHOLDER.sub(replace, template)
        if namespace is None and name in ("int", "float", "bool") and len(args) == 1:
            return self._fold(FuncCall(callee=Identifier(name=name),
                                       args=[self._literal(args[0])]))
        raise OpenTimeframeSet
