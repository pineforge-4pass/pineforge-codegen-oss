"""Static chart-loop proofs; unproved loops retain their ordinary lowering."""

from __future__ import annotations

from collections import defaultdict
from math import isfinite

from ..ast_nodes import (
    Assignment, BinOp, BreakStmt, ContinueStmt, ForInStmt, ForStmt,
    FuncCall, FuncDef, Identifier, IfStmt, MemberAccess, MethodDef,
    NumberLiteral, Program, SwitchStmt, TupleAssign, UnaryOp, VarDecl, WhileStmt,
)
from ..limits import iter_ast_nodes, syntax_children
from .constant_fold import fold_constant_expression
from .tables import (
    ARRAY_JOURNAL_READ_METHODS, MAP_METHODS, MATH_FUNC_MAP, MATRIX_METHODS,
    STR_FUNC_MAP,
)
from .types import COLLECTION_MUTATING_METHODS


def _affine(node, counter):
    """Return (a, c) for a*counter+c, using only literal arithmetic."""
    if isinstance(node, NumberLiteral):
        result = (0, node.value)
    elif isinstance(node, Identifier) and node.name == counter:
        result = (1, 0)
    elif isinstance(node, UnaryOp) and node.op in ("+", "-"):
        operand = _affine(node.operand, counter)
        if operand is None:
            return None
        result = operand if node.op == "+" else (-operand[0], -operand[1])
    elif isinstance(node, BinOp) and node.op in ("+", "-", "*", "/"):
        left = _affine(node.left, counter)
        right = _affine(node.right, counter)
        if left is None or right is None:
            return None
        a, c = left
        b, d = right
        if node.op == "+":
            result = (a + b, c + d)
        elif node.op == "-":
            result = (a - b, c - d)
        elif node.op == "*" and not (a and b):
            result = (a * d + b * c, c * d)
        elif node.op == "/" and b == 0 and d != 0:
            result = (a / d, c / d)
        else:
            return None
    else:
        return None
    return result if all(isfinite(value) for value in result) else None


def end_cannot_stop(node: ForStmt) -> bool:
    """Prove continuation for both possible initial counter readings.

    Direction is fixed by s versus the end read at counter zero; every
    subsequent end reads the new counter. Also checking the start counter
    is stricter than that measured rule: both directions must continue.
    """
    try:
        start = _affine(node.start, None)
        step = (0, 1) if node.step is None else _affine(node.step, None)
        end = _affine(node.end, node.var)
        if start is None or step is None or end is None or step[1] <= 0:
            return False
        a, c = end
        s = start[1]
        if a < 1:
            return False
        at_start = a * s + c
        if not isfinite(at_start):
            return False
        for initial_counter in (0, s):
            descending = s > a * initial_counter + c
            if not (s >= at_start if descending else s <= at_start):
                return False
        return True
    except (ArithmeticError, ValueError):
        # Optional proof: overflow, division by zero and non-finite constants
        # must never change an existing loop's emission.
        return False


class _Bindings:
    """Lexical bindings and value/control dependencies, with no branch pruning."""

    def __init__(self, program):
        self.writes = {}
        self.chart_loops = []
        self.functions = defaultdict(list)
        self.methods = defaultdict(list)
        self.references = {}
        self.readers = defaultdict(set)
        self.read_roots = {}
        self.statements = {}
        self.top_level = {id(node) for node in program.body}
        self.value_users = defaultdict(set)
        self.call_effects = {}
        self.dependencies = defaultdict(set)
        self.effects = defaultdict(set)
        self.calls = []
        self._block(program.body, (), owner=None)
        self._connect_calls()

    @staticmethod
    def _resolve(name, scopes):
        for scope in reversed(scopes):
            if name in scope:
                return scope[name]
        return (None, name)

    def _read(self, node, scopes, root):
        binding = self._resolve(node.name, scopes)
        self.references[id(node)] = binding
        self.readers[binding].add(id(node))
        self.read_roots[id(node)] = root
        self.dependencies[id(node)].add(binding)
        return binding

    def _inputs(self, key, nodes):
        inputs = {id(node) for node in nodes if node is not None}
        self.dependencies[key].update(inputs)
        for value in inputs:
            self.value_users[value].add(key)
        return inputs

    def _block(self, statements, scopes, *, owner, guards=(), binders=None):
        local = dict(binders or {})
        scopes = (*scopes, local)
        deferred = []
        result = None
        for node in statements:
            self.statements[id(node)] = node
            if isinstance(node, (FuncDef, MethodDef)):
                deferred.append(node)
                registry = self.functions if isinstance(node, FuncDef) else self.methods
                registry[node.name].append(node)
            else:
                self._visit(node, scopes, owner=owner, guards=guards)
                result = node
        # A callable's dependencies become reachable through its calls only.
        # Its parameters/locals still shadow chart bindings in this scope walk.
        for node in deferred:
            # Defaults are stored as annotations, outside syntax_children.
            # Count their reads even when the callable is never reached.
            for default in (node.annotations or {}).get("param_defaults", ()):
                self._visit(default, scopes, owner=id(node))
            params = {name: (id(node), name) for name in node.params}
            returned = self._block(node.body, scopes, owner=id(node), binders=params)
            self._inputs(id(node), [returned])
        return result

    def _visit(self, node, scopes, *, owner, guards=(), effect_root=None):
        if node is None:
            return
        key = id(node)
        if effect_root is None:
            effect_root = key
        self.dependencies[key].update(guards)
        if isinstance(node, Identifier):
            self._read(node, scopes, effect_root)
            return
        if isinstance(node, (VarDecl, TupleAssign)):
            self._visit(
                node.value, scopes, owner=owner, guards=guards,
                effect_root=effect_root,
            )
            self._inputs(key, [node.value])
            names = [node.name] if isinstance(node, VarDecl) else node.names
            bindings = {(id(node), name) for name in names}
            for binding in bindings:
                scopes[-1][binding[1]] = binding
                self.dependencies[binding].add(key)
            if node.value is not None:
                self.writes[key] = bindings
            if isinstance(node, VarDecl) and (node.is_var or node.is_varip):
                self.effects[owner].add(key)
            return
        if isinstance(node, Assignment):
            self._visit(
                node.value, scopes, owner=owner, guards=guards,
                effect_root=effect_root,
            )
            self._inputs(key, [node.value])
            if isinstance(node.target, Identifier):
                binding = self._resolve(node.target.name, scopes)
                self.writes[key] = {binding}
                self.dependencies[binding].add(key)
                if node.op != ":=":
                    self._read(node.target, scopes, key)
                    self._inputs(key, [node.target])
            else:
                self._visit(
                    node.target, scopes, owner=owner, guards=guards,
                    effect_root=effect_root,
                )
                self._inputs(key, [node.target])
                self.effects[owner].add(key)
            return
        if isinstance(node, ForStmt):
            if owner is None and (node.annotations or {}).get("pine_version") != 5:
                self.chart_loops.append(node)
            self._visit(node.start, scopes, owner=owner, guards=guards)
            counter_scope = (*scopes, {node.var: (id(node), node.var)})
            for expr in (node.end, node.step):
                self._visit(expr, counter_scope, owner=owner, guards=guards)
            headers = self._inputs(key, [node.start, node.end, node.step])
            self.dependencies[(key, node.var)].update(headers)
            returned = self._block(
                node.body, counter_scope, owner=owner, guards=(*guards, *headers),
            )
            self._inputs(key, [returned])
            return
        if isinstance(node, ForInStmt):
            self._visit(node.iterable, scopes, owner=owner, guards=guards)
            headers = self._inputs(key, [node.iterable])
            names = [node.var] if node.var else (node.vars or [])
            binders = {name: (id(node), name) for name in names}
            for binding in binders.values():
                self.dependencies[binding].update(headers)
            returned = self._block(
                node.body, scopes, owner=owner, guards=(*guards, *headers),
                binders=binders,
            )
            self._inputs(key, [returned])
            return
        if isinstance(node, IfStmt):
            self._visit(node.condition, scopes, owner=owner, guards=guards)
            headers = self._inputs(key, [node.condition])
            for body in (node.body, node.else_body):
                returned = self._block(
                    body, scopes, owner=owner, guards=(*guards, *headers),
                )
                self._inputs(key, [returned])
            return
        if isinstance(node, SwitchStmt):
            conditions = [node.expr, *(condition for condition, _body in node.cases)]
            for condition in conditions:
                self._visit(condition, scopes, owner=owner, guards=guards)
            headers = self._inputs(key, conditions)
            for body in [*(body for _condition, body in node.cases), node.default_body]:
                returned = self._block(
                    body, scopes, owner=owner, guards=(*guards, *headers),
                )
                self._inputs(key, [returned])
            return
        if isinstance(node, WhileStmt):
            self._visit(node.condition, scopes, owner=owner, guards=guards)
            headers = self._inputs(key, [node.condition])
            returned = self._block(
                node.body, scopes, owner=owner, guards=(*guards, *headers),
            )
            self._inputs(key, [returned])
            return
        children = list(syntax_children(node))
        if isinstance(node, FuncCall):
            self.calls.append((node, owner, effect_root))
            if isinstance(node.callee, Identifier):
                # A direct callable name does not read a scalar binding;
                # a method receiver still does.
                children = [child for child in children if child is not node.callee]
        self._inputs(key, children)
        for child in children:
            self._visit(
                child, scopes, owner=owner, guards=guards,
                effect_root=effect_root,
            )

    def called_helpers(self, call):
        helpers = self.functions.get(_spelling(call.callee), ())
        if not helpers and isinstance(call.callee, MemberAccess):
            receiver = call.callee.object
            if isinstance(receiver, Identifier) and self.references.get(
                id(receiver), (None, receiver.name),
            )[0] is None:
                return ()  # An unbound namespace is not a method receiver.
            # Without a typed method binding, keep every possible overload.
            helpers = self.methods.get(call.callee.member, ())
        return helpers

    def _connect_calls(self):
        for call, owner, effect_root in self.calls:
            if _call_has_effect(call, self):
                # An effectful statement reads its whole expression, even
                # when the call is nested beside an otherwise unused input.
                self.effects[owner].update((id(call), effect_root))
        for call, _owner, _effect_root in self.calls:
            for helper in self.called_helpers(call):
                self.dependencies[id(call)].add(id(helper))
                self.dependencies[id(call)].update(self.effects[id(helper)])

    def live_values(self):
        """Least fixed point from chart effects through value/control inputs.

        The dead_counter, dead_counter_plus_one, dead_nonzero_start,
        dead_counter_with_break and dead_chain fixtures measure unused
        reductions. live_single_bar_read and live_untaken_branch_read stop
        even for late/untaken effect reads. Retain visual, persistent and
        collection effects and every call we cannot prove pure. Self-updates
        and break tests have no root of their own. uncalled_function_loop
        and function_called_in_dead_branch add no chart roots. Absence of a
        path to an effect alone does not establish that omission is measured.
        """
        live = set()
        pending = list(self.effects[None])
        while pending:
            value = pending.pop()
            if value not in live:
                live.add(value)
                pending.extend(self.dependencies.get(value, ()))
        return live


def _spelling(node):
    if isinstance(node, Identifier):
        return node.name
    if isinstance(node, MemberAccess):
        owner = _spelling(node.object)
        return f"{owner}.{node.member}" if owner else None
    return None


def _body_nodes(body):
    for statement in body:
        for node, _depth in iter_ast_nodes(statement):
            yield node


def _expanded_body_nodes(body, bindings):
    """Include called helpers when checking for an existing run stop."""
    pending = [body]
    seen = set()
    while pending:
        for node in _body_nodes(pending.pop()):
            yield node
            if isinstance(node, FuncCall):
                for helper in bindings.called_helpers(node):
                    if id(helper) not in seen:
                        seen.add(id(helper))
                        pending.append(helper.body)


def _call_has_effect(call, bindings, visiting=frozenset()):
    spelling = _spelling(call.callee)
    helpers = bindings.functions.get(spelling, ())
    if helpers:
        if spelling in visiting:
            return True
        if spelling in bindings.call_effects:
            return bindings.call_effects[spelling]
        bindings.call_effects[spelling] = True
        for helper in helpers:
            for node in _body_nodes(helper.body):
                if isinstance(node, VarDecl) and (node.is_var or node.is_varip):
                    return True
                if isinstance(node, Assignment) and not isinstance(node.target, Identifier):
                    return True
                if isinstance(node, FuncCall) and _call_has_effect(
                    node, bindings, visiting | {spelling},
                ):
                    return True
        bindings.call_effects[spelling] = False
        return False
    # Purity bounds effect reachability; it does not authorize omission.
    # Orders, visual output, drawings, alerts, requests and collection
    # mutations keep their inputs live, as do unresolved effects.
    if spelling in {"na", "nz", "fixnan", "int", "float", "bool", "string"}:
        return False
    if isinstance(call.callee, MemberAccess) and isinstance(
        call.callee.object, Identifier,
    ):
        owner = call.callee.object
        if bindings.references.get(id(owner), (None, owner.name))[0] is not None:
            return True
        pure = {
            "array": ARRAY_JOURNAL_READ_METHODS,
            "matrix": MATRIX_METHODS.keys() - COLLECTION_MUTATING_METHODS,
            "map": MAP_METHODS.keys() - COLLECTION_MUTATING_METHODS,
            "math": MATH_FUNC_MAP,
            "str": STR_FUNC_MAP,
        }
        return call.callee.member not in pure.get(owner.name, ())
    return True


def _reachable_nodes(body):
    """Prune only proved constant branches for the break check."""
    for node in body:
        yield node
        if isinstance(node, IfStmt):
            yield from _body_nodes([node.condition])
            constant, proven = fold_constant_expression(node.condition, {}, {})
            if proven and type(constant) is bool:
                yield from _reachable_nodes(node.body if constant else node.else_body)
            else:
                yield from _reachable_nodes(node.body)
                yield from _reachable_nodes(node.else_body)
        elif isinstance(node, (ForStmt, ForInStmt, WhileStmt)):
            if isinstance(node, WhileStmt):
                constant, proven = fold_constant_expression(node.condition, {}, {})
                if proven and constant is False:
                    continue
            yield from _reachable_nodes(node.body)
        elif isinstance(node, SwitchStmt):
            for _condition, statements in node.cases:
                yield from _reachable_nodes(statements)
            yield from _reachable_nodes(node.default_body)
        else:
            for child in syntax_children(node):
                yield from _reachable_nodes([child])
        if isinstance(node, (BreakStmt, ContinueStmt)):
            break


def _measured_unused_reduction(loop, nodes, writes, bindings):
    """Accept unread updates, optionally feeding one unread declaration.

    Self-updates and the dead_counter_with_break fixture's break condition
    are internal reads. Every other read must have the dead_chain fixture's
    single declaration edge, whose destination has no readers or reassignments.
    Callable/default reads count regardless of whether a call is reachable.
    """
    if bindings.value_users.get(id(loop)) or any(
        isinstance(node, FuncCall) for node in nodes
    ):
        return False
    internal = {id(node) for node in nodes}
    break_reads = {
        id(child)
        for node in nodes
        if isinstance(node, IfStmt) and not node.else_body
        and len(node.body) == 1 and isinstance(node.body[0], BreakStmt)
        for child in _body_nodes([node.condition])
    }
    chain = set()
    for binding in writes:
        for reader in bindings.readers[binding]:
            root = bindings.read_roots[reader]
            if reader in internal:
                if reader in break_reads or binding in bindings.writes.get(root, ()):
                    continue
                return False
            statement = bindings.statements.get(root)
            if (
                root not in bindings.top_level
                or not isinstance(statement, VarDecl)
                or statement.is_var or statement.is_varip
            ):
                return False
            expression = list(_body_nodes([statement.value]))
            if any(not isinstance(
                node, (Identifier, NumberLiteral, BinOp, UnaryOp),
            ) for node in expression):
                return False
            if sum(isinstance(node, Identifier) for node in expression) != 1:
                return False
            destination = (root, statement.name)
            if bindings.readers[destination] or any(
                key != root and destination in targets
                for key, targets in bindings.writes.items()
            ):
                return False
            chain.add(root)
            if len(chain) > 1:
                return False
    return True


def plan_endless_for_loops(program: Program) -> dict[int, str]:
    """Return chart loop actions ('omit' or 'stop'), keyed only for lookup."""
    if program.version == 5 or not any(
        isinstance(node, ForStmt) and end_cannot_stop(node)
        for node, _depth in iter_ast_nodes(program)
    ):
        return {}
    bindings = _Bindings(program)
    live_values = bindings.live_values()
    actions = {}
    for loop in bindings.chart_loops:
        if not end_cannot_stop(loop):
            continue
        nodes = list(_body_nodes(loop.body))
        # Do not replace a body-provided error, even through a helper.
        if any(
            isinstance(node, FuncCall) and _spelling(node.callee) == "runtime.error"
            for node in _expanded_body_nodes(loop.body, bindings)
        ):
            continue
        writes = set().union(*(bindings.writes.get(id(node), set()) for node in nodes))
        if (id(loop), loop.var) in writes:
            continue  # A reassigned counter invalidates the monotonic proof.
        live = (
            id(loop) in live_values
            or bool(writes & live_values)
            or any(id(node) in bindings.effects[None] for node in nodes)
        )
        if not live and _measured_unused_reduction(loop, nodes, writes, bindings):
            # TradingView also drops an unused reduction with a break: no
            # result or effect can escape, regardless of when it would end.
            actions[id(loop)] = "omit"
        elif not live or not any(
            isinstance(node, BreakStmt) for node in _reachable_nodes(loop.body)
        ):
            actions[id(loop)] = "stop"
    return actions
