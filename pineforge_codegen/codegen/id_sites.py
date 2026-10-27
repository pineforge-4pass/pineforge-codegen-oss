"""Immutable count of the order sites that create a computed id on every bar.

A ``strategy.entry``, ``strategy.order`` or ``strategy.exit`` site whose ``id``
``order_shapes.parameter_class`` calls "dynamic" can create a new order id each
time it runs; a host sizes its per-bar id bookkeeping from how often one bar can
run such sites. The count follows the program, not the emitted C++: a user
function counts once per static call of it (transitively), a ``for`` loop whose
bounds are known before the script runs counts once per iteration unless its
body assigns the loop's own counter, and anything without a static bound (a
``while`` or ``for ... in`` loop, a ``for`` loop with a computed bound or a
body that assigns its counter, a recursion) makes the document say ``unbounded``.
"""

import json
import math

from ..ast_nodes import (
    Assignment, BinOp, ForInStmt, ForStmt, FuncCall, FuncDef, Identifier, MemberAccess,
    MethodDef, TupleAssign, VarDecl, WhileStmt,
)
from ..limits import iter_ast_nodes, syntax_children
from .order_shapes import _literal, _site_facts, parameter_class


# The lowered calls that can create an order id.
FRESH_ID_CALLS = frozenset(("entry", "order", "exit"))
UNBOUNDED = math.inf
# The largest count an unsigned 32-bit reader holds; a larger one is reported unbounded.
MAX_FRESH_PER_BAR = 2 ** 32 - 1
# ``strategy.entry(...)`` beside a user ``method entry(...)`` is the built-in call, unless
# the script binds the namespace's name itself.
BUILTIN_NAMESPACES = frozenset({
    "ta", "math", "str", "array", "map", "matrix", "color", "input",
    "request", "strategy", "syminfo", "timeframe", "barstate", "session",
    "chart", "label", "line", "box", "table", "linefill", "polyline", "log",
    "runtime", "ticker", "dividends", "earnings", "splits", "currency",
    "display", "extend", "format", "hline", "location", "order", "plot",
    "position", "scale", "shape", "size", "text", "xloc", "yloc", "alert",
    "barmerge", "font", "adjustment", "backadjustment", "settlement_as_close",
    "dayofweek", "footprint", "volume_row", "timenow", "time",
})


def _bounded(count):
    return UNBOUNDED if count > MAX_FRESH_PER_BAR else count


def _plus(left, right):
    return _bounded(left + right)


def _times(count, cost):
    if cost == 0:
        return 0
    return UNBOUNDED if cost == UNBOUNDED else _bounded(count * cost)


def _static_int(node, bindings):
    """The whole number a loop bound is worth before the script runs, else None."""
    if isinstance(node, BinOp) and node.op in ("+", "-", "*"):
        left = _static_int(node.left, bindings)
        right = _static_int(node.right, bindings)
        if left is None or right is None:
            return None
        return left + right if node.op == "+" else left - right if node.op == "-" else left * right
    value = _literal(node, bindings)
    if type(value) is int:
        return value
    if type(value) is float and math.isfinite(value) and value == int(value):
        return int(value)
    return None


def _trip_count(loop, bindings):
    """Iterations of a ``for`` loop with static bounds: the codegen's direction and step magnitude."""
    start = _static_int(loop.start, bindings)
    end = _static_int(loop.end, bindings)
    step = 1 if loop.step is None else _static_int(loop.step, bindings)
    if start is None or end is None or step is None:
        return None
    return abs(end - start) // (abs(step) or 1) + 1


def _assigns_counter(loop):
    """Whether the body of a ``for`` loop assigns the loop's own counter.

    The counter is a variable the body can set (``i := 0``, ``i += 1``), so the loop
    then runs a number of iterations its bounds do not give. An assignment to the name
    counts anywhere in the body, a nested block or loop included, even where an inner
    loop declares the same name for itself: that errs high, and the count must never be low.
    """
    for statement in loop.body:
        for node, _depth in iter_ast_nodes(statement):
            if (isinstance(node, Assignment) and isinstance(node.target, Identifier)
                    and node.target.name == loop.var):
                return True
    return False


class _Index:
    """What the execution count needs from one pass over the program."""

    def __init__(self, ast, sites):
        self.definitions = []
        self.functions = {}
        self.methods = {}
        self.bound = set()
        self.loops = set()
        self.placed = set()
        for node, _depth in iter_ast_nodes(ast):
            if id(node) in sites:
                self.placed.add(id(node))
            if isinstance(node, (FuncDef, MethodDef)):
                self.definitions.append(node)
                table = self.functions if isinstance(node, FuncDef) else self.methods
                table.setdefault(node.name, []).append(node)
                self.bound.update(name if isinstance(name, str) else name[0] for name in node.params)
            elif isinstance(node, ForStmt):
                self.loops.add(id(node))
                self.bound.add(node.var)
            elif isinstance(node, ForInStmt):
                self.bound.update(node.vars or ([node.var] if node.var else ()))
            elif isinstance(node, VarDecl):
                self.bound.add(node.name)
            elif isinstance(node, TupleAssign):
                self.bound.update(node.names)


class _Executions:
    """How often one bar runs the counted sites, from the program's structure.

    ``counted`` maps a site's node id to its weight: what one run of the site
    costs, a whole number or ``UNBOUNDED``.
    """

    def __init__(self, ast, index, counted, facts):
        self.ast = ast
        self.index = index
        self.counted = counted
        self.facts = facts
        edges = {}
        direct = set()
        for definition in index.definitions:
            calls = set()
            for root in self._roots(definition):
                for node, _depth in iter_ast_nodes(root):
                    if id(node) in counted:
                        direct.add(id(definition))
                    if isinstance(node, FuncCall):
                        calls.update(id(target) for target in self._targets(node))
            edges[id(definition)] = calls
        callers = {}
        for owner, calls in edges.items():
            for callee in calls:
                callers.setdefault(callee, set()).add(owner)
        # A definition that reaches no counted site costs nothing and is never walked.
        self.bearing = set(direct)
        pending = list(direct)
        while pending:
            for owner in callers.get(pending.pop(), ()):
                if owner not in self.bearing:
                    self.bearing.add(owner)
                    pending.append(owner)
        # Callees first. A definition on a call cycle, or that reaches one, never
        # becomes ready: it has no static bound.
        below = {key: {callee for callee in edges[key] if callee in self.bearing}
                 for key in self.bearing}
        above = {}
        for key, callees in below.items():
            for callee in callees:
                above.setdefault(callee, set()).add(key)
        waiting = {key: len(callees) for key, callees in below.items()}
        ready = [key for key, count in waiting.items() if count == 0]
        by_key = {id(definition): definition for definition in index.definitions}
        self.cost = {}
        while ready:
            key = ready.pop()
            self.cost[key] = self._body_cost(by_key[key])
            for owner in above.get(key, ()):
                waiting[owner] -= 1
                if waiting[owner] == 0:
                    ready.append(owner)

    @staticmethod
    def _roots(definition):
        """The statements a call runs: the body and the defaults of omitted arguments."""
        defaults = (definition.annotations or {}).get("param_defaults", ())
        return [*definition.body, *(default for default in defaults if default is not None)]

    def _targets(self, call):
        """The definitions a call can reach: a user function by name, a user method by its name."""
        callee = call.callee
        if isinstance(callee, Identifier):
            return self.index.functions.get(callee.name, ())
        if isinstance(callee, MemberAccess) and callee.member in self.index.methods:
            root = callee.object
            while isinstance(root, MemberAccess):
                root = root.object
            if not (isinstance(root, Identifier) and root.name in BUILTIN_NAMESPACES
                    and root.name not in self.index.bound):
                return self.index.methods[callee.member]
        return ()

    def _call_cost(self, call):
        cost = 0
        for target in self._targets(call):
            key = id(target)
            if key in self.bearing:
                cost = max(cost, self.cost.get(key, UNBOUNDED))
        return cost

    def _block_cost(self, statements):
        total = 0
        for statement in statements:
            total = _plus(total, self._cost(statement))
        return total

    def _body_cost(self, definition):
        return self._block_cost(self._roots(definition))

    def _cost(self, node):
        if node is None or isinstance(node, (FuncDef, MethodDef)):
            return 0
        if isinstance(node, ForStmt):
            return self._for_cost(node)
        if isinstance(node, (ForInStmt, WhileStmt)):
            return self._open_loop_cost(node)
        total = self.counted.get(id(node), 0)
        if isinstance(node, FuncCall):
            total = _plus(total, self._call_cost(node))
        for child in syntax_children(node):
            total = _plus(total, self._cost(child))
        return total

    def _for_cost(self, loop):
        trips = _trip_count(loop, self.facts.get(id(loop), (0, "", {}))[2])
        if trips is not None and _assigns_counter(loop):
            # The body moves the counter itself, so the bounds do not say how often it runs.
            trips = None
        body = self._block_cost(loop.body)
        if trips is not None:
            # Static bounds hold no call, so only the body repeats.
            return _times(trips, body)
        repeated = _plus(body, self._cost(loop.end))
        return _plus(_plus(self._cost(loop.start), self._cost(loop.step)),
                     UNBOUNDED if repeated else 0)

    def _open_loop_cost(self, loop):
        """A ``for ... in`` or ``while`` loop: nothing says how often it repeats."""
        once = self._cost(loop.iterable) if isinstance(loop, ForInStmt) else 0
        repeated = self._block_cost(loop.body)
        if isinstance(loop, WhileStmt):
            repeated = _plus(repeated, self._cost(loop.condition))
        return _plus(once, UNBOUNDED if repeated else 0)

    def total(self):
        if set(self.counted) - self.index.placed:
            # A lowered site the program does not hold has no position to count from.
            return UNBOUNDED
        return self._block_cost(self.ast.body)


def fresh_ids_per_bar(emitter):
    """Computed-id order sites one bar can run; ``UNBOUNDED`` when no static bound holds."""
    sites = {id(record.node): record for record in emitter._order_shape_calls.values()
             if record.call in FRESH_ID_CALLS}
    if not sites:
        return 0
    ast = emitter.ctx.ast
    index = _Index(ast, sites)
    facts = _site_facts(ast, emitter.ctx.global_mutable_infos, set(sites) | index.loops)
    counted = {key: 1 for key, record in sites.items()
               if parameter_class("id", record.parameters["id"],
                                  facts.get(key, (0, "", {}))[2]) == "dynamic"}
    if not counted:
        return 0
    return _Executions(ast, index, counted, facts).total()


def id_sites_document(emitter) -> str:
    fresh = fresh_ids_per_bar(emitter)
    ids = {"freshPerBarStatus": "unbounded"} if fresh == UNBOUNDED else {"freshPerBar": int(fresh)}
    return json.dumps({"strategy": {"ids": ids}, "version": 1},
                      sort_keys=True, separators=(",", ":"), allow_nan=False)
