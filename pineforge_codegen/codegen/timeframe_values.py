"""Conservative value sets for computed requested timeframes.

Selections contribute every arm unless a literal-only boolean proves which
arm is taken. Chart and setup-input conditions retain their full union.
Storage contributes its seed and every write until its inventory is closed.
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
from ..security_contexts import ScriptIndex, TIMEFRAME_SOURCES_ANNOTATION
from ..symbols import PineType, TypeSpec
from .constant_fold import _constant_arguments, fold_constant_expression


class OpenTimeframeSet(Exception):
    """No complete, bounded inventory can be proved."""


class TimeframeValues:
    """Inventory scalar values without rendering or running Pine."""

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
        self.writes = {}
        for node, _depth in iter_ast_nodes(emitter.ctx.ast):
            if isinstance(node, Assignment) and isinstance(node.target, Identifier):
                binding = self._binding(node.target)
                self.unstable.add(binding)
                self.writes.setdefault(binding, []).append(node)
        self.work = 0
        self.cache = {}
        self.active = set()
        self.functions = set()
        self.numeric = None

    def mutable(self, node):
        return isinstance(node, Identifier) and self._binding(node) in self.unstable

    def _integer(self, node):
        # Slot inference deliberately reports double for integer builtins
        # such as bar_index. Use the shared numeric value proof, which also
        # retains fractional division and widened reassignment storage.
        if self.numeric is None:
            from ..loop_types import LoopTypeSources, _CounterQuery

            self.numeric = _CounterQuery(
                LoopTypeSources(self.emitter.ctx.ast),
                self.emitter.ctx.identifier_decl_bindings,
                self.emitter.ctx.identifier_binding_scopes)
        return self.numeric.value_spec(node) == TypeSpec.primitive("int")

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

    def _constant_bool(self, node):
        """Prove a boolean from literals, operators and primitive casts only.

        Do not resolve names, inputs, history or helper calls, even when an
        environment happens to give them one value. Those conditions retain
        both arms. Primitive casts of literals include ``bool(2)``.
        """
        pending = [node]
        while pending:
            expr = pending.pop()
            self._tick()
            if isinstance(expr, (StringLiteral, NumberLiteral, BoolLiteral)):
                continue
            if isinstance(expr, UnaryOp):
                pending.append(expr.operand)
            elif isinstance(expr, BinOp):
                pending.extend((expr.left, expr.right))
            elif isinstance(expr, Ternary):
                pending.extend((expr.condition, expr.true_val, expr.false_val))
            elif (isinstance(expr, FuncCall)
                  and isinstance(expr.callee, Identifier)
                  and expr.callee.name in ("int", "float", "bool")
                  and len(expr.args) == 1 and not expr.kwargs):
                pending.extend(expr.args)
            else:
                return None
        value, proven = fold_constant_expression(node, {}, {})
        return value if proven and type(value) is bool else None

    def _evaluate(self, node, env):
        if (node.annotations or {}).get("pine_version") == 5:
            raise OpenTimeframeSet
        sources = (node.annotations or {}).get(TIMEFRAME_SOURCES_ANNOTATION)
        if sources is not None:
            return self._bounded(value for source in sources
                                 for value in self._values(source, env))
        if isinstance(node, (StringLiteral, NumberLiteral, BoolLiteral)):
            return (node.value,)
        if isinstance(node, Identifier):
            binding = self._binding(node)
            if binding in env:
                return env[binding]
            decl = self.declarations.get(binding)
            if decl is None:
                raise OpenTimeframeSet
            if binding in self.unstable:
                return self._storage(binding, decl, env)
            # A caller's locals cannot capture names in a global initializer.
            values = self._values(decl.value, {})
            return self._typed(values, decl.type_hint)
        if isinstance(node, Ternary):
            self._condition(node.condition)
            chosen = self._constant_bool(node.condition)
            if chosen is not None:
                return self._values(node.true_val if chosen else node.false_val, env)
            return (*self._values(node.true_val, env),
                    *self._values(node.false_val, env))
        if isinstance(node, (IfStmt, SwitchStmt)):
            if isinstance(node, IfStmt):
                self._condition(node.condition)
                chosen = self._constant_bool(node.condition)
                bodies = ((node.body if chosen else node.else_body,)
                          if chosen is not None else (node.body, node.else_body))
            else:
                if node.expr is not None:
                    self._condition(node.expr)
                for case, _body in node.cases:
                    self._condition(case)
                bodies = [body for _case, body in node.cases] + [node.default_body]
                chosen = (True if node.expr is None
                          else self._constant_bool(node.expr))
                cases = [self._constant_bool(case) for case, _body in node.cases]
                if chosen is not None and all(case is not None for case in cases):
                    bodies = [next((body for case, (_expr, body)
                                    in zip(cases, node.cases) if case == chosen),
                                   node.default_body)]
            return self._bounded(value for body in bodies
                                 for value in self._block(body, env))
        if isinstance(node, UnaryOp):
            return (self._fold(UnaryOp(op=node.op, operand=self._literal(value)))
                    for value in self._values(node.operand, env))
        if isinstance(node, BinOp):
            if node.op not in ("+", "-", "*", "/", "%", "==", "!=", "<", "<=",
                               ">", ">=", "and", "or"):
                raise OpenTimeframeSet
            try:
                left = self._values(node.left, env)
            except OpenTimeframeSet:
                if node.op != "%" or not self._integer(node.left):
                    raise
                # An integer remainder has a finite range even when its
                # dividend does not. Pine/C++ truncates the quotient toward
                # zero, so a dividend of unknown sign needs both signs.
                nonnegative = (
                    isinstance(node.left, Identifier)
                    and node.left.name == "bar_index"
                    and self._binding(node.left) is None)
                residues = []
                for divisor in self._values(node.right, env):
                    if type(divisor) is not int or divisor == 0:
                        raise OpenTimeframeSet
                    magnitude = abs(divisor)
                    if magnitude > MAX_SECURITY_TIMEFRAME_VALUES:
                        raise OpenTimeframeSet
                    residues.extend(range(0 if nonnegative else 1 - magnitude,
                                          magnitude))
                return self._bounded(residues)
            operands = (left, self._values(node.right, env))
            return (self._fold(BinOp(left=self._literal(left), op=node.op,
                                     right=self._literal(right)))
                    for left, right in product(*operands))
        if isinstance(node, FuncCall):
            return self._call(node, env)
        raise OpenTimeframeSet

    def _storage(self, binding, decl, outer):
        """A closed invariant of the initializer and every write to a cell.

        Iterate from the seed, binding self-reads to the preceding inventory.
        A counter or a growing string fails the ordinary inventory/work
        bounds; a cell assigned a finite choice reaches a fixed point.
        Source order and branch conditions still execute in the chart code.
        """
        values = self._typed(self._values(decl.value, outer), decl.type_hint)
        while True:
            env = {**outer, binding: values}
            candidates = list(values)
            for write in self.writes.get(binding, ()):
                written = self._values(write.value, env)
                if write.op != ":=":
                    operator = write.op.removesuffix("=")
                    written = self._bounded(
                        self._fold(BinOp(left=self._literal(left), op=operator,
                                         right=self._literal(right)))
                        for left, right in product(values, written))
                candidates.extend(self._typed(written, decl.type_hint))
            expanded = self._bounded(candidates)
            if expanded == values:
                return expanded
            values = expanded

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
            if isinstance(stmt, VarDecl):
                binding = (id(stmt), stmt.name)
                if binding in self.unstable:
                    # A condition can inspect an open counter while both
                    # returned string arms are closed. Do not demand that
                    # counter's concrete values until a value arm reads it.
                    result = (self._storage(binding, stmt, env)
                              if stmt is body[-1] else None)
                else:
                    result = self._typed(self._values(stmt.value, env), stmt.type_hint)
                    env[binding] = result
            elif isinstance(stmt, Assignment) and isinstance(stmt.target, Identifier):
                binding = self._binding(stmt.target)
                declaration = self.declarations.get(binding)
                if declaration is None:
                    raise OpenTimeframeSet
                env.pop(binding, None)
                result = (self._storage(binding, declaration, env)
                          if stmt is body[-1] else None)
            elif isinstance(stmt, ExprStmt):
                result = self._values(stmt.expr, env)
            elif isinstance(stmt, (IfStmt, SwitchStmt)):
                # Writes in a prefix branch already belong to each cell's
                # invariant. The branch and its effects still run in the
                # authored helper, never in this inventory.
                result = self._values(stmt, env) if stmt is body[-1] else None
            else:
                # Loops and other statements need a separate value proof.
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
        # Engine 6fa23a06, src/str_utils.cpp:18-25 and :569-600, renders
        # shortest round-trip decimal digits (pinned by the earlier number
        # rendering tape). Bounded integral floats therefore spell as integers.
        # Fractional rounding, mintick, patterns and non-finite values need
        # a separate rendering proof.
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
