"""One ``request.security`` context per symbol and timeframe reaching a helper.

A request's context -- the symbol and the timeframe it aggregates -- is fixed
before the first bar: ``register_security_eval`` runs before ``on_bar``. When
a helper receives them as parameters, the context is the value of those
parameters on each call path, and a path may pass a parameter on through
further helpers::

    g(sym, tf) => request.security(sym, tf, ta.sma(close, 3))
    h(sym, tf) => g(sym, tf)
    a = h(syminfo.tickerid, tfA)
    b = h(syminfo.tickerid, tfB)

The analyzer resolves a helper's timeframe from the arguments of the helper's
own call sites only (``Analyzer._check_mixed_callsite_security_tf``), so the
codegen registered every such request on the chart's timeframe
(``input_tf_``) with no diagnostic, and ``h(ticker.heikinashi(...), tf)``
read plain candles. This pass resolves the symbol and timeframe of every
request whose context depends on a helper's parameters or locals along every
call path from the top level, with each parameter's value read in its
caller's scope, down to expressions of globals, inputs and built-ins, which
registration can evaluate:

* one context on every path: the request is annotated with it
  (``annotations["pf_security_context"]``, read by the analyzer);
* several: each helper on the paths is copied once per distinct context it
  leads to (``h__pfctx1``, ...), each copy calling the copies of its callees
  that lead to its own contexts, and each request copy annotated with its
  context;
* a value no registration can compute (a series, a reassigned local, a user
  call, too many paths) is refused, naming the request, the parameter and the
  path.

A request whose timeframe and symbol are bare parameters, bound at every call
site of its helper to a global expression, stays with the analyzer's call-site
clones (``callsite_idx``), which emit it as every earlier build did. A helper
no top-level statement reaches is marked dead: its requests never run and
keep the chart timeframe.

The helper's parameters a request's payload reads are part of its context
too: the payload is evaluated on the requested bars, where the argument a
parameter is bound to is recomputed::

    nr(_s, _tf, _e) => request.security(_s, _tf, _e[1], lookahead = barmerge.lookahead_on)
    reso(_x, _r) => nr(syminfo.tickerid, _r, _x)
    a = reso(fastMa, "60")
    b = reso(slowMa, "60")

The evaluator never bound such a parameter: ``_e[1]`` read a member nothing
pushed (``na`` on every bar), a bare ``_e`` was refused as an unknown
variable, and ``f(10) + f(20)`` with ``ta.sma(close, len)`` shared one
evaluator built from the first call's length. Each such parameter is
resolved like the symbol and the timeframe, bar series and user calls
allowed, the requests are copied once per distinct value, and each copy's
payload reads the value in the parameter's place (``fastMa[1]``; an untitled
input carries its declaration's key as its title). A length or history index
every top-level call passes one value keeps the analyzer's single evaluator;
its call-site clones for differing timeframes built every clone from the
first call's length. Only a value the request builder lowers on the
requested bars in the read's place is put in (``_Lowered``); any other keeps
the earlier lowering, never a refusal: a reassigned or ``var`` name, a loop
variable, a name the helper declares, a user call, an ``input.source``, a
global declared after the helper (read on the chart's terms there), a
history object other than an OHLCV series, a ``ta.*`` call or an inline
operator expression, a global under a builtin rendered on the chart's terms.

A request of another symbol that reads that symbol's pinned feed
(``external_requests``: the support checker's ``feed`` lowering) is keyed by
its symbol as well, so this pass owns every one whose symbol or timeframe
reaches it through a helper's parameters, the analyzer's call-site clones
included (they tell call sites apart by their timeframe only)::

    f_htfPack(sym, tf) => request.security(sym, tf, f_packConfirmed())
    f_symbolState(sym, tf) => f_htfPack(sym, tf)
    f_tfRegime(sym) => [f_symbolState(sym, mainTf), f_symbolState(sym, confirmTf)]
    [v1, v2] = f_tfRegime(vixSymbol)
    [d1, d2] = f_tfRegime(dxySymbol)

is six contexts. Registration reads a symbol before the first bar, so one
that is not a value registration computes there on some call path (a
series, a reassigned name, a user call: ``ScriptIndex.registration_value``)
does not refuse the script: that request keeps the lowering it had before
it read a feed, a deferred refusal whose first read stops the run
(``external_requests.unpin_requests``), with a warning naming the path.
"""

from __future__ import annotations

import copy
from dataclasses import replace

from .ast_nodes import (
    ArgOrder, ASTNode, Assignment, BinOp, BoolLiteral, ColorLiteral, ForInStmt,
    ForStmt, FuncCall, FuncDef, Identifier, IfStmt, MemberAccess, MethodDef,
    NaLiteral, NumberLiteral, Program, StringLiteral, Subscript, SwitchStmt,
    Ternary, TupleAssign, TupleLiteral, UnaryOp, VarDecl, WhileStmt,
)
from .errors import CompileError, Diagnostic, Level, Phase, SourceLocation
from .pine_spelling import input_binding_names


CONTEXT_ANNOTATION = "pf_security_context"
DEAD_ANNOTATION = "pf_security_dead"
# On a payload's read of a parameter of a helper no top-level statement
# reaches: the codegen does not warn that it reads na.
UNREACHED_ANNOTATION = "pf_security_unreached"
# On the Program: the warnings of the passes that run between the support
# checker and the analyzer, which the analyzer reports as its own.
PASS_WARNINGS_ANNOTATION = "pf_pass_warnings"
# ``external_requests.LOWERING_ANNOTATION`` and its ``feed`` lowering, spelled
# here too: that module imports this one.
_LOWERING_ANNOTATION = "pf_request_lowering"
_FEED_LOWERING = "feed"
_REQUEST_FUNCS = ("security", "security_lower_tf")
# Instances (a helper under one set of parameter values) the pass may build
# before it refuses the script: diamond-shaped helper graphs multiply paths.
_MAX_INSTANCES = 512
# Nodes a payload parameter's value may hold: a value doubling through nested
# helpers (``h(x) => g(x + x)``) keeps the earlier lowering.
_MAX_PAYLOAD_NODES = 256
_LITERALS = (StringLiteral, NumberLiteral, BoolLiteral, NaLiteral, ColorLiteral)
# The chart's own symbol strings, and the inputs, registration reads
# (``ScriptIndex.registration_value``).
_REGISTRATION_MEMBERS = frozenset({
    ("syminfo", "tickerid"), ("syminfo", "ticker"), ("syminfo", "prefix"),
    ("syminfo", "currency"), ("syminfo", "basecurrency"),
})
_REGISTRATION_INPUTS = frozenset({"symbol", "string", "bool"})
# Built-in series a context cannot be computed from before the first bar.
_BAR_SERIES = frozenset({
    "open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4",
    "time", "time_close", "time_tradingday", "timenow", "bar_index", "last_bar_index",
})


class _Unresolvable(Exception):
    """A context value no registration can compute."""


class _Fallback(Exception):
    """Requests whose payload parameters keep the earlier lowering."""

    def __init__(self, requests) -> None:
        super().__init__()
        self.requests = set(requests)


class _FeedFallback(Exception):
    """Requests of another symbol that read no feed: request id -> why."""

    def __init__(self, reasons: dict[int, str]) -> None:
        super().__init__()
        self.reasons = dict(reasons)


def _is_feed(request) -> bool:
    """``request`` reads another symbol's pinned feed."""
    return (request.annotations or {}).get(_LOWERING_ANNOTATION) == _FEED_LOWERING


def reads_bar_series(expr) -> str | None:
    """The built-in bar series ``expr`` reads, if any: a context computed
    from it has no value before the first bar."""
    for node in _walk(expr):
        if isinstance(node, Identifier) and node.name in _BAR_SERIES:
            return node.name
    return None


def _request_name(node) -> str | None:
    if (isinstance(node, FuncCall) and isinstance(node.callee, MemberAccess)
            and isinstance(node.callee.object, Identifier)
            and node.callee.object.name == "request"
            and node.callee.member in _REQUEST_FUNCS):
        return node.callee.member
    return None


def _request_args(node: FuncCall) -> tuple[object, object]:
    symbol = node.args[0] if node.args else node.kwargs.get("symbol")
    tf = node.args[1] if len(node.args) > 1 else node.kwargs.get("timeframe")
    return symbol, tf


def _request_payload(node: FuncCall):
    """The expression a request evaluates on the requested bars."""
    return node.args[2] if len(node.args) > 2 else node.kwargs.get("expression")


def _children(node):
    for key, value in vars(node).items():
        if key in ("loc", "annotations"):
            continue
        yield value


def _walk(value):
    """Every AST node below ``value``, depth first, in source order."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, ASTNode):
            yield item
            stack.extend(reversed(list(_children(item))))
        elif isinstance(item, (list, tuple)):
            stack.extend(reversed(item))
        elif isinstance(item, dict):
            stack.extend(reversed(list(item.values())))


def replace_nodes(root, swaps: dict[int, ASTNode]) -> None:
    """Put ``swaps[id(node)]`` in the place of each such node (a call's
    argument order too)."""
    def fix(value):
        if isinstance(value, ASTNode):
            if id(value) in swaps:
                return swaps[id(value)]
            for key, item in list(vars(value).items()):
                if key == "loc":
                    continue
                if key == "annotations":
                    order = (item or {}).get("call_arg_order")
                    if isinstance(order, ArgOrder) and any(id(n) in swaps for n in order):
                        item["call_arg_order"] = ArgOrder(swaps.get(id(n), n) for n in order)
                    continue
                new = fix(item)
                if new is not item:
                    setattr(value, key, new)
            return value
        if isinstance(value, list):
            for i, item in enumerate(value):
                new = fix(item)
                if new is not item:
                    value[i] = new
            return value
        if isinstance(value, tuple):
            items = tuple(fix(item) for item in value)
            return items if any(a is not b for a, b in zip(items, value)) else value
        if isinstance(value, dict):
            for key, item in list(value.items()):
                new = fix(item)
                if new is not item:
                    value[key] = new
            return value
        return value

    fix(root)


def context_key(value):
    """A hashable spelling of an AST value, blind to locations."""
    if isinstance(value, ASTNode):
        return (type(value).__name__,) + tuple(
            (key, context_key(item)) for key, item in sorted(vars(value).items())
            if key not in ("loc", "annotations"))
    if isinstance(value, (list, tuple)):
        return tuple(context_key(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((key, context_key(item)) for key, item in value.items()))
    return value


def _spell(node) -> str:
    """A short Pine spelling of a resolved context value, for messages."""
    if isinstance(node, StringLiteral):
        return f'"{node.value}"'
    if isinstance(node, Identifier):
        return node.name
    if isinstance(node, MemberAccess):
        return f"{_spell(node.object)}.{node.member}"
    if isinstance(node, FuncCall):
        args = [_spell(a) for a in node.args]
        args += [f"{k}={_spell(v)}" for k, v in node.kwargs.items()]
        return f"{_spell(node.callee)}({', '.join(args)})"
    if isinstance(node, NumberLiteral):
        return str(node.value)
    return type(node).__name__


class ScriptIndex:
    """The lexical bindings, helpers and calls of a program."""

    def __init__(self, program: Program) -> None:
        self.program = program
        self.funcs: dict[str, FuncDef] = {}
        # Names defined more than once: Pine overloads a function by its
        # parameters, which this pass does not tell apart.
        self.overloaded: set[str] = set()
        for stmt in program.body:
            if isinstance(stmt, FuncDef):
                if stmt.name in self.funcs:
                    self.overloaded.add(stmt.name)
                self.funcs[stmt.name] = stmt
        # Identifier id -> its binding: ("global", name) for an unnested
        # top-level declaration, ("param", func, name), ("local", decl id)
        # for a declaration in a callable or a top-level block, ("bound",
        # node id, name) for a loop variable or a tuple element. Built-ins
        # and callables have none.
        self.refs: dict[int, tuple] = {}
        self.decls: dict[tuple, VarDecl] = {}
        # Declaration (VarDecl, or TupleAssign and name) id -> the binding
        # its name's reads resolve to in its block.
        self.decl_binding: dict[tuple, tuple] = {}
        self.unstable: set[tuple] = set()  # reassigned, var or varip
        self.reassigned: set[tuple] = set()
        # FuncCall id -> the callable whose body holds it (None: top level).
        self.owner: dict[int, str | None] = {}
        self.calls: dict[str, list[FuncCall]] = {name: [] for name in self.funcs}
        self.call_ids: set[int] = set()  # the calls in ``calls``
        self.requests: dict[str | None, list[FuncCall]] = {}
        self._index()
        # Global input call id -> the declaration naming it (its key when
        # untitled).
        self.input_names = input_binding_names(program.body)
        # Every name a top-level statement declares.
        self.program_names = {stmt.name for stmt in program.body
                              if isinstance(stmt, VarDecl) and stmt.name}
        self.program_names.update(name for stmt in program.body
                                  if isinstance(stmt, TupleAssign) for name in stmt.names)

    def _index(self) -> None:
        def resolve(name: str, scopes) -> tuple | None:
            for scope in reversed(scopes):
                if name in scope:
                    return scope[name]
            return None

        def block(stmts, scopes, owner, binders=None, top=False) -> None:
            scope = dict(binders or {})
            for stmt in stmts:
                if isinstance(stmt, VarDecl) and stmt.name:
                    key = ("global", stmt.name) if top else ("local", id(stmt))
                    scope.setdefault(stmt.name, key)
                    self.decls.setdefault(key, stmt)
                    self.decl_binding[(id(stmt), stmt.name)] = scope[stmt.name]
                    if stmt.is_var or stmt.is_varip:
                        self.unstable.add(key)
                elif isinstance(stmt, TupleAssign):
                    for name in stmt.names:
                        scope.setdefault(
                            name, ("global", name) if top else ("bound", id(stmt), name))
                        self.decl_binding[(id(stmt), name)] = scope[name]
            nested = (*scopes, scope)
            for stmt in stmts:
                walk(stmt, nested, owner)

        def walk(value, scopes, owner) -> None:
            if isinstance(value, (list, tuple)):
                for item in value:
                    walk(item, scopes, owner)
                return
            if isinstance(value, dict):
                for item in value.values():
                    walk(item, scopes, owner)
                return
            if not isinstance(value, ASTNode):
                return
            node = value
            if isinstance(node, Identifier):
                binding = resolve(node.name, scopes)
                if binding is not None:
                    self.refs[id(node)] = binding
                return
            if isinstance(node, FuncCall):
                self.owner[id(node)] = owner
                if (isinstance(node.callee, Identifier) and node.callee.name in self.funcs
                        and resolve(node.callee.name, scopes) is None):
                    self.calls[node.callee.name].append(node)
                    self.call_ids.add(id(node))
                if _request_name(node):
                    self.requests.setdefault(owner, []).append(node)
            if isinstance(node, Assignment):
                if isinstance(node.target, Identifier):
                    binding = resolve(node.target.name, scopes)
                    if binding is not None:
                        self.unstable.add(binding)
                        self.reassigned.add(binding)
            if isinstance(node, (FuncDef, MethodDef)):
                name = node.name if isinstance(node, FuncDef) else None
                params = {p: ("param", node.name, p) for p in node.params}
                block(node.body, scopes, name if name in self.funcs else f"method {node.name}",
                      params)
                return
            if isinstance(node, IfStmt):
                walk(node.condition, scopes, owner)
                block(node.body, scopes, owner)
                block(node.else_body, scopes, owner)
                return
            if isinstance(node, ForStmt):
                walk([node.start, node.end, node.step], scopes, owner)
                block(node.body, scopes, owner, {node.var: ("bound", id(node), node.var)})
                return
            if isinstance(node, ForInStmt):
                walk(node.iterable, scopes, owner)
                names = [node.var] if node.var else list(node.vars or ())
                block(node.body, scopes, owner,
                      {n: ("bound", id(node), n) for n in names})
                return
            if isinstance(node, WhileStmt):
                walk(node.condition, scopes, owner)
                block(node.body, scopes, owner)
                return
            if isinstance(node, SwitchStmt):
                walk(node.expr, scopes, owner)
                for case_expr, body in node.cases:
                    walk(case_expr, scopes, owner)
                    block(body, scopes, owner)
                block(node.default_body, scopes, owner)
                return
            for child in _children(node):
                walk(child, scopes, owner)

        block(self.program.body, (), None, top=True)

    # -- scopes --------------------------------------------------------------

    def depends_on_scope(self, expr) -> bool:
        """``expr`` reads a parameter, a local or a bound name."""
        return any(isinstance(n, Identifier) and self.refs.get(id(n), ("global",))[0]
                   in ("param", "local", "bound") for n in _walk(expr))

    def param_arg(self, func: str, call: FuncCall, param: str):
        """The argument ``call`` binds to ``param`` of ``func`` (its default
        when omitted), or None."""
        fdef = self.funcs[func]
        if param in call.kwargs:
            return call.kwargs[param]
        index = fdef.params.index(param)
        if index < len(call.args):
            return call.args[index]
        defaults = (fdef.annotations or {}).get("param_defaults") or []
        return defaults[index] if index < len(defaults) else None

    # -- values --------------------------------------------------------------

    def resolve(self, expr, env: dict | None, seen: frozenset = frozenset()):
        """``expr``, read in a scope whose parameters ``env`` binds, as an
        expression of globals, inputs, literals and built-ins."""
        if isinstance(expr, _LITERALS):
            return expr
        if isinstance(expr, Identifier):
            binding = self.refs.get(id(expr))
            if binding is None and expr.name in _BAR_SERIES:
                raise _Unresolvable(f"it reads '{expr.name}', a series")
            if binding is None or binding[0] == "global":
                return expr
            if binding[0] == "param":
                if env is None or binding[2] not in env:
                    raise _Unresolvable(f"parameter '{binding[2]}' has no argument")
                arg, caller_env = env[binding[2]]
                if arg is None:
                    raise _Unresolvable(f"parameter '{binding[2]}' has no argument")
                return self.resolve(arg, caller_env, seen)
            if binding[0] == "local" and binding not in self.unstable and binding not in seen:
                decl = self.decls[binding]
                if decl.value is not None:
                    return self.resolve(decl.value, env, seen | {binding})
            raise _Unresolvable(f"'{expr.name}' is not a value fixed before the first bar")
        if isinstance(expr, MemberAccess):
            if (isinstance(expr.object, Identifier)
                    and self.refs.get(id(expr.object), ("global",))[0] == "global"):
                return expr
            raise _Unresolvable(f"'{_spell(expr)}' is not a value fixed before the first bar")
        if isinstance(expr, FuncCall):
            if isinstance(expr.callee, Identifier) and expr.callee.name in self.funcs:
                raise _Unresolvable(f"the user function call {expr.callee.name}(...)")
            if not (isinstance(expr.callee, Identifier) or (
                    isinstance(expr.callee, MemberAccess)
                    and isinstance(expr.callee.object, Identifier)
                    and id(expr.callee.object) not in self.refs)):
                raise _Unresolvable(f"the method call {_spell(expr)}")
            args = [self.resolve(a, env, seen) for a in expr.args]
            kwargs = {k: self.resolve(v, env, seen) for k, v in expr.kwargs.items()}
            if all(a is b for a, b in zip(args, expr.args)) and all(
                    kwargs[k] is expr.kwargs[k] for k in kwargs):
                return expr
            return replace(expr, args=args, kwargs=kwargs)
        if isinstance(expr, Ternary):
            parts = [self.resolve(p, env, seen)
                     for p in (expr.condition, expr.true_val, expr.false_val)]
            if all(a is b for a, b in zip(parts, (expr.condition, expr.true_val,
                                                  expr.false_val))):
                return expr
            return replace(expr, condition=parts[0], true_val=parts[1], false_val=parts[2])
        if isinstance(expr, BinOp):
            left, right = self.resolve(expr.left, env, seen), self.resolve(expr.right, env, seen)
            if left is expr.left and right is expr.right:
                return expr
            return replace(expr, left=left, right=right)
        if isinstance(expr, UnaryOp):
            operand = self.resolve(expr.operand, env, seen)
            return expr if operand is expr.operand else replace(expr, operand=operand)
        raise _Unresolvable(f"a {type(expr).__name__} is not a value fixed before the first bar")

    def resolve_payload(self, expr, env: dict | None, seen: frozenset = frozenset()):
        """A fresh copy of ``expr``, read in a scope whose parameters ``env``
        binds, with each parameter and local replaced by its value: an
        expression of globals, literals and built-ins, bar series and user
        calls included, which a payload recomputes on the requested bars."""
        swaps = {}
        for node in _walk(expr):
            if isinstance(node, FuncCall) and _request_name(node):
                raise _Unresolvable(f"it holds a request.{_request_name(node)} call")
            if not isinstance(node, Identifier):
                continue
            binding = self.refs.get(id(node))
            if binding is None:
                continue
            if binding in self.unstable:
                raise _Unresolvable(f"'{node.name}' is reassigned or declared var")
            if binding[0] == "global":
                continue
            if binding[0] == "param":
                arg, caller_env = (env or {}).get(binding[2], (None, None))
                if arg is None:
                    raise _Unresolvable(f"parameter '{binding[2]}' has no argument")
                swaps[id(node)] = self.resolve_payload(arg, caller_env, seen)
            elif (binding[0] == "local" and binding not in seen
                    and self.decls[binding].value is not None):
                swaps[id(node)] = self.resolve_payload(
                    self.decls[binding].value, env, seen | {binding})
            else:
                raise _Unresolvable(f"'{node.name}' is not a value the requested bars recompute")
        if id(expr) in swaps:
            return swaps[id(expr)]
        memo: dict = {}
        fresh = copy.deepcopy(expr, memo)
        replace_nodes(fresh, {id(memo[key]): value for key, value in swaps.items()})
        if sum(1 for _ in _walk(fresh)) > _MAX_PAYLOAD_NODES:
            raise _Unresolvable("its value is too large")
        # An untitled input is keyed by the declaration holding it, which its
        # copy leaves: the copy carries the key as its title.
        for node in _walk(expr):
            name = self.input_names.get(id(node))
            if name is None or "title" in node.kwargs or len(node.args) > 1:
                continue
            call = memo[id(node)]
            title = StringLiteral(value=name)
            call.kwargs = {**call.kwargs, "title": title}
            order = (call.annotations or {}).get("call_arg_order")
            if isinstance(order, ArgOrder):
                call.annotations = {**call.annotations,
                                    "call_arg_order": ArgOrder([*order, title])}
        return fresh

    def registration_value(self, expr, seen: frozenset = frozenset()) -> bool:
        """``expr`` is a value registration computes before the first bar:
        literals, inputs (TradingView takes constant arguments), the chart's
        own symbol strings, and operators and ternaries over them, read
        through globals never reassigned. A request of another symbol is
        registered with its symbol string and ``ignore_invalid_symbol`` so."""
        if isinstance(expr, (StringLiteral, NumberLiteral, BoolLiteral)):
            return True
        if isinstance(expr, Identifier):
            binding = self.refs.get(id(expr))
            if (binding is None or binding[0] != "global" or binding in self.unstable
                    or binding in seen):
                return False
            decl = self.decls.get(binding)
            return (decl is not None and decl.value is not None
                    and self.registration_value(decl.value, seen | {binding}))
        if isinstance(expr, MemberAccess):
            return (isinstance(expr.object, Identifier) and id(expr.object) not in self.refs
                    and (expr.object.name, expr.member) in _REGISTRATION_MEMBERS)
        if isinstance(expr, FuncCall):
            callee = expr.callee
            if isinstance(callee, Identifier):
                return callee.name == "input" and id(callee) not in self.refs
            if not (isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier)
                    and id(callee.object) not in self.refs):
                return False
            if callee.object.name == "input":
                return callee.member in _REGISTRATION_INPUTS
            if callee.object.name == "ticker" and callee.member in ("inherit", "standard"):
                first = expr.args[0] if expr.args else expr.kwargs.get("symbol")
                return first is not None and self.registration_value(first, seen)
            return False
        if isinstance(expr, BinOp):
            return (expr.op in ("+", "==", "!=", "and", "or")
                    and self.registration_value(expr.left, seen)
                    and self.registration_value(expr.right, seen))
        if isinstance(expr, UnaryOp):
            return expr.op == "not" and self.registration_value(expr.operand, seen)
        if isinstance(expr, Ternary):
            return all(self.registration_value(part, seen)
                       for part in (expr.condition, expr.true_val, expr.false_val))
        return False


def _error(node, message: str, filename: str) -> Diagnostic:
    loc = getattr(node, "loc", None) or SourceLocation(file=filename, line=1, col=1, end_col=1)
    return Diagnostic(level=Level.ERROR, phase=Phase.ANALYZER, location=loc, message=message,
                      hint=("Pass the symbol and timeframe as literals, inputs or globals "
                            "at the helper's call sites."))


def specialize_security_contexts(program: Program, filename: str = "<input>") -> Program:
    """Resolve the context of every helper request (module docstring)."""
    while True:
        try:
            return _specialize(program, filename)
        except _FeedFallback as exc:
            # Nothing is rewritten before the plan holds: these requests keep
            # their deferred refusal, and the pass starts over without them.
            from .external_requests import unpin_requests
            unpin_requests(program, exc.reasons)


def _specialize(program: Program, filename: str) -> Program:
    prog = ScriptIndex(program)
    _mark_unreached(prog, _reached(prog))
    # The requests whose context this pass owns, by helper; those owned for
    # their symbol or timeframe; the payload parameter reads of those owned
    # for their payload.
    owned: dict[str, list[FuncCall]] = {}
    context_owned: set[int] = set()
    payload_reads: dict[int, list[Identifier]] = {}
    # Another symbol's request whose symbol or timeframe reads a name this
    # pass does not resolve (a method's parameter, a block's local) reads no
    # feed: the support checker keeps those, this is its backstop.
    stray = {id(request): "its symbol or timeframe reads a name of a method or a block"
             for owner, requests in prog.requests.items() if owner not in prog.funcs
             for request in requests if _is_feed(request)
             and any(prog.depends_on_scope(arg) for arg in _request_args(request))}
    if stray:
        raise _FeedFallback(stray)
    for owner, requests in prog.requests.items():
        if owner not in prog.funcs:
            continue
        for request in requests:
            symbol, tf = _request_args(request)
            if (prog.depends_on_scope(symbol) or prog.depends_on_scope(tf)) and (
                    _is_feed(request) or not (
                        _request_name(request) == "security"
                        and _analyzer_resolves(prog, owner, symbol, tf))):
                context_owned.add(id(request))
            reads = _payload_params(prog, owner, request)
            if reads and not _analyzer_binds_payload(prog, owner, request, reads):
                payload_reads[id(request)] = reads
            if id(request) in context_owned or id(request) in payload_reads:
                owned.setdefault(owner, []).append(request)
    while True:
        if not owned:
            return program
        try:
            leads, instances, detail, top = _plan(
                prog, owned, context_owned, payload_reads, filename)
            break
        except _Fallback as exc:
            # These requests keep the earlier lowering of their payload (all
            # of them, should none of these be owned for it).
            dropped = exc.requests & payload_reads.keys()
            for request_id in dropped or list(payload_reads):
                payload_reads.pop(request_id)
            owned = {func: kept for func, requests in owned.items()
                     if (kept := [r for r in requests if id(r) in context_owned
                                  or id(r) in payload_reads])}
    _type_context_params(prog, owned, leads)

    # A request no top-level statement reaches never runs: one owned for its
    # context keeps the chart timeframe, and no read of a parameter of its
    # helper warns.
    for func, requests in owned.items():
        if func not in instances:
            for request in requests:
                if id(request) in context_owned:
                    request.annotations = {**(request.annotations or {}),
                                           DEAD_ANNOTATION: True}

    # Name every instance and build its definition: a helper's first
    # signature keeps its own, every other one is a copy of it.
    taken = {n.name for n in _walk(program) if isinstance(n, Identifier)} | set(prog.funcs)
    names: dict[tuple, str] = {}
    defs: dict[tuple, tuple[FuncDef, dict | None]] = {}
    new_defs: dict[str, list[FuncDef]] = {}
    for func, sigs in instances.items():
        original = prog.funcs[func]
        for index, signature in enumerate(sigs):
            if index == 0:
                names[signature] = func
                defs[signature] = (original, None)
                continue
            n = index
            while f"{func}__pfctx{n}" in taken:
                n += 1
            names[signature] = f"{func}__pfctx{n}"
            taken.add(names[signature])
            memo: dict = {}
            clone = copy.deepcopy(original, memo)
            clone.name = names[signature]
            defs[signature] = (clone, memo)
            new_defs.setdefault(func, []).append(clone)

    for signature, (fdef, memo) in defs.items():
        info = detail[signature]
        mapped = (lambda node: node) if memo is None else (lambda node, m=memo: m[id(node)])
        for request, (symbol, tf), payload in info["contexts"]:
            target = mapped(request)
            target.annotations = {
                **(target.annotations or {}),
                CONTEXT_ANNOTATION: {"symbol": _alias_value(prog, symbol), "timeframe": tf}}
            if payload:
                values = dict(payload)
                replace_nodes(target, {
                    id(mapped(read)): copy.deepcopy(values[prog.refs[id(read)][2]])
                    for read in payload_reads[id(request)]})
        for call, child in info["kids"]:
            target = mapped(call)
            target.callee = replace(target.callee, name=names[child])
    for call, signature in top:
        call.callee = replace(call.callee, name=names[signature])

    body = []
    for stmt in program.body:
        body.append(stmt)
        if isinstance(stmt, FuncDef):
            body.extend(new_defs.get(stmt.name, ()))
    program.body = body
    return program


def _plan(prog: ScriptIndex, owned: dict, context_owned: set[int],
          payload_reads: dict[int, list], filename: str):
    """The helpers leading to ``owned``, their instances, each instance's
    contexts and child calls, and the top-level calls' instances. Refuses a
    symbol or timeframe no registration can compute; raises ``_Fallback``
    for payloads this pass cannot rewrite."""
    # Every helper whose calls lead to an owned request.
    leads = set(owned)
    changed = True
    while changed:
        changed = False
        for name in prog.funcs:
            if name in leads:
                continue
            if any(prog.owner.get(id(c)) == name for g in leads for c in prog.calls[g]):
                leads.add(name)
                changed = True

    errors: dict[int, Diagnostic] = {}

    def refuse(request: FuncCall, message: str) -> None:
        errors.setdefault(id(request), _error(request, message, filename))

    def payload_only(funcs) -> list[int]:
        return [id(r) for g in funcs for r in owned.get(g, ()) if id(r) not in context_owned]

    context_first = {func: first for func, requests in owned.items()
                     if (first := next((r for r in requests if id(r) in context_owned), None))}
    for func in sorted(leads):
        if func in prog.overloaded:
            problem = "is overloaded, and its overloads' call paths are not told apart"
        else:
            problem = next((f"is called from {owner}, whose calls this pass does not follow"
                            for call in prog.calls[func]
                            if (owner := prog.owner.get(id(call))) is not None
                            and owner not in prog.funcs), None)
        if problem is None:
            continue
        reach = {func} | _reachable(prog, func, leads)
        feeds = {id(r): f"helper '{func}' {problem}"
                 for g in reach for r in owned.get(g, ()) if _is_feed(r)}
        if feeds:
            raise _FeedFallback(feeds)
        # The request a helper leads to, for the message.
        target = context_first.get(func) or next(
            (context_first[g] for g in sorted(context_first) if g in reach), None)
        if target is None:
            dropped = payload_only(reach) or list(payload_reads)
            if dropped:
                raise _Fallback(dropped)
            # ``_reachable`` walks an overloaded helper's last definition.
            target = next(r for requests in owned.values() for r in requests)
        refuse(target, f"request.{_request_name(target)} context: helper '{func}' {problem}")
    if errors:
        raise CompileError(list(errors.values()))

    instances: dict[str, list] = {}   # helper -> distinct signatures, first seen first
    detail: dict[tuple, dict] = {}    # signature -> contexts and child calls
    budget = [0]

    def instantiate(func: str, env: dict, path: tuple) -> tuple:
        budget[0] += 1
        if budget[0] > _MAX_INSTANCES or func in {name for name, _ in path}:
            raise _Unresolvable("the helpers reach it through too many call paths")
        here = path + ((func, env),)
        contexts = []
        for request in owned.get(func, []):
            symbol, tf = _request_args(request)
            values = []
            for what, arg in (("symbol", symbol), ("timeframe", tf)):
                chain = " -> ".join(f"{name}()" for name, _ in here)
                try:
                    values.append(prog.resolve(arg, env) if arg is not None else None)
                except _Unresolvable as exc:
                    if id(request) not in context_owned:
                        raise _Fallback([id(request)]) from exc
                    if _is_feed(request):
                        raise _FeedFallback({id(request): (
                            f"its {what} cannot be resolved before the first bar on the "
                            f"call path {chain}: {exc}")}) from exc
                    refuse(request, f"request.{_request_name(request)} {what} cannot be "
                                    f"resolved before the first bar on the call path "
                                    f"{chain}: {exc}")
                    values.append(None)
                    continue
                if (what == "symbol" and _is_feed(request) and values[-1] is not None
                        and not prog.registration_value(values[-1])):
                    raise _FeedFallback({id(request): (
                        f"its symbol {_spell(values[-1])} on the call path {chain} is not "
                        "a value registration computes before the first bar")})
            payload = ()
            if id(request) in payload_reads:
                try:
                    payload = _payload_values(prog, func, request, payload_reads[id(request)],
                                              env)
                except _Unresolvable as exc:
                    raise _Fallback([id(request)]) from exc
            contexts.append((request, tuple(values), payload))
        kids = []
        for call in _body_calls(prog, func, leads):
            callee = call.callee.name
            callee_env = {p: (prog.param_arg(callee, call, p), env)
                          for p in prog.funcs[callee].params}
            kids.append((call, instantiate(callee, callee_env, here)))
        signature = (func, tuple(context_key((v, payload)) for _, v, payload in contexts),
                     tuple(sig for _, sig in kids))
        if signature not in detail:
            detail[signature] = {"contexts": contexts, "kids": kids}
            instances.setdefault(func, []).append(signature)
        return signature

    top: list[tuple[FuncCall, tuple]] = []
    try:
        for name in sorted(leads, key=lambda n: prog.program.body.index(prog.funcs[n])):
            for call in prog.calls[name]:
                if prog.owner.get(id(call)) is not None:
                    continue
                env = {p: (prog.param_arg(name, call, p), None)
                       for p in prog.funcs[name].params}
                top.append((call, instantiate(name, env, ())))
    except _Unresolvable as exc:
        feeds = {id(r): str(exc) for requests in owned.values() for r in requests
                 if _is_feed(r)}
        if feeds:
            raise _FeedFallback(feeds) from exc
        if payload_reads:
            raise _Fallback(list(payload_reads)) from exc
        request = next(iter(owned.values()))[0]
        refuse(request, f"request.{_request_name(request)} context: {exc}")
    if errors:
        raise CompileError(list(errors.values()))
    return leads, instances, detail, top


def _payload_params(prog: ScriptIndex, func: str, request: FuncCall) -> list[Identifier]:
    """The reads of ``func``'s parameters in ``request``'s payload (not a
    tuple's: a helper returning one does not compile)."""
    if _request_name(request) != "security" or isinstance(_request_payload(request),
                                                          TupleLiteral):
        return []
    return [node for node in _walk(_request_payload(request))
            if isinstance(node, Identifier) and prog.refs.get(id(node), ())[:2] == ("param", func)]


def _analyzer_binds_payload(prog: ScriptIndex, func: str, request: FuncCall,
                            reads: list[Identifier]) -> bool:
    """The analyzer's lowering binds these payload reads exactly: each is a
    ``ta.*`` length or a history index, every call of ``func`` is at the top
    level and passes each parameter one value, the same spelling, which holds
    no input call (each untitled one has its own key). The analyzer's
    call-site clones for differing symbols or timeframes give every clone the
    first call's length. A helper nothing calls never runs; an overloaded
    one's calls are not told apart, and keep the analyzer's lowering."""
    calls = prog.calls[func]
    if not calls or func in prog.overloaded:
        return True
    if any(prog.owner.get(id(call)) is not None for call in calls):
        return False
    objects, lengths, _ = _payload_positions(prog, request)
    if any(id(read) not in lengths for read in reads):
        return False
    for param in {prog.refs[id(read)][2] for read in reads}:
        args = [prog.param_arg(func, call, param) for call in calls]
        if len({context_key(arg) for arg in args}) > 1 or any(
                id(node) in prog.input_names for arg in args for node in _walk(arg)):
            return False
    return True


def _payload_positions(prog: ScriptIndex, request: FuncCall) -> tuple[set[int], set[int], set[int]]:
    """The ids of the payload's history objects (``e`` in ``e[1]``), of its
    ``ta.*`` length arguments and history indices -- where every earlier
    build lowered a helper parameter without refusing it -- and of the nodes
    under a builtin call the builder renders on the chart's terms (``nz``,
    ``str.*``, ...) with no ``ta.*`` or user call between them."""
    from .analyzer.tables import TA_PERIOD_ARG

    objects, lengths, rendered = set(), set(), set()

    def visit(value, under: bool) -> None:
        if isinstance(value, (list, tuple)):
            for item in value:
                visit(item, under)
            return
        if isinstance(value, dict):
            for item in value.values():
                visit(item, under)
            return
        if not isinstance(value, ASTNode):
            return
        if under:
            rendered.add(id(value))
        if isinstance(value, Subscript):
            objects.add(id(value.object))
            lengths.add(id(value.index))
        elif isinstance(value, FuncCall):
            callee = value.callee
            space = (callee.object.name if isinstance(callee, MemberAccess)
                     and isinstance(callee.object, Identifier)
                     and id(callee.object) not in prog.refs else None)
            if space == "ta":
                index = TA_PERIOD_ARG.get(callee.member)
                if index is not None and index < len(value.args):
                    lengths.add(id(value.args[index]))
                if "length" in value.kwargs:
                    lengths.add(id(value.kwargs["length"]))
                under = False
            elif isinstance(callee, Identifier) and callee.name in prog.funcs:
                under = False
            elif space != "math":
                under = True
        for child in _children(value):
            visit(child, under)

    visit(_request_payload(request), False)
    return objects, lengths, rendered


# The chart's own series a payload lowers on the requested bars: all of them
# as a value, the ones with requested-bar history as a history object.
_VALUE_SERIES = frozenset({
    "open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4",
})
_HISTORY_SERIES = frozenset({"open", "high", "low", "close", "volume"})
_VALUE_CALLS = frozenset({"nz", "int", "float"})
_CONSTANT_INPUTS = frozenset({"int", "float", "bool", "string"})


class _Lowered:
    """Whether the request builder lowers a payload parameter's value on the
    requested bars in the read's place, never on the chart's terms or into
    C++ that does not compile. It reads a global only when the global is
    declared before the helper (a later one reads the chart's value), not
    reassigned or ``var``, and its value is itself lowered."""

    def __init__(self, prog: ScriptIndex, func: str) -> None:
        from .analyzer.tables import TA_TUPLE_RETURNS

        self.prog = prog
        self.tuple_ta = TA_TUPLE_RETURNS
        # (rule, global name, globals_ok) -> verdict: a global's value is
        # judged once however many reads share it.
        self.memo: dict = {}
        self.before: dict = {}
        for stmt in prog.program.body[:prog.program.body.index(prog.funcs[func])]:
            if isinstance(stmt, VarDecl) and stmt.name:
                self.before.setdefault(stmt.name, stmt.value)
            elif isinstance(stmt, TupleAssign):
                for name in stmt.names:
                    self.before.setdefault(name, None)

    def _global(self, node, seen):
        """A stable global's value declared before the helper, else None."""
        if (node.name in seen or self.before.get(node.name) is None
                or ("global", node.name) in self.prog.unstable):
            return None
        return self.before[node.name]

    def _call(self, node) -> str | None:
        """``ta``, ``math``, ``input`` or ``value`` for a call the builder
        lowers, else None."""
        callee = node.callee
        if (isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier)
                and callee.object.name not in self.before
                and id(callee.object) not in self.prog.refs):
            space, member = callee.object.name, callee.member
            if space == "ta" and member not in self.tuple_ta:
                return "ta"
            if space == "math":
                return "math"
            if space == "input" and member in _CONSTANT_INPUTS:
                return "input"
            return None
        if (isinstance(callee, Identifier) and callee.name in _VALUE_CALLS
                and callee.name not in self.before and callee.name not in self.prog.funcs):
            return "value"
        return None

    def _judged(self, rule: str, node, seen, globals_ok, judge) -> bool:
        key = (rule, node.name, globals_ok)
        if key not in self.memo:
            declared = self._global(node, seen)
            self.memo[key] = declared is not None and judge(declared, seen | {node.name})
        return self.memo[key]

    def value(self, node, seen: frozenset = frozenset(), globals_ok: bool = True) -> bool:
        if isinstance(node, _LITERALS):
            return True
        if isinstance(node, Identifier):
            if node.name in self.prog.program_names:
                return globals_ok and self._judged(
                    "value", node, seen, globals_ok,
                    lambda declared, inner: self.value(declared, inner, globals_ok))
            return node.name in _VALUE_SERIES
        if isinstance(node, FuncCall):
            # An input's arguments are constants (TradingView takes const
            # ones): its value is the same on every bar. ``nz`` and the casts
            # render on the chart's terms: no global below them.
            kind = self._call(node)
            inner_ok = globals_ok and kind != "value"
            return kind == "input" or (kind is not None and all(
                self.value(a, seen, inner_ok) for a in (*node.args, *node.kwargs.values())))
        if isinstance(node, BinOp):
            return self.value(node.left, seen, globals_ok) and self.value(
                node.right, seen, globals_ok)
        if isinstance(node, UnaryOp):
            return self.value(node.operand, seen, globals_ok)
        if isinstance(node, Ternary):
            return all(self.value(part, seen, globals_ok)
                       for part in (node.condition, node.true_val, node.false_val))
        if isinstance(node, Subscript):
            return (self.history(node.object, seen, globals_ok)
                    and self.length(node.index, seen))
        return False

    def history(self, node, seen: frozenset = frozenset(), globals_ok: bool = True) -> bool:
        """``node`` is a history object: a series with requested-bar history,
        a ``ta.*`` call (inline, or a global's value), an inline operator
        expression. Not a literal, ``na``, another history read, ``hl2`` and
        its family, or a global holding an operator expression."""
        if isinstance(node, Identifier):
            if node.name in self.prog.program_names:
                return globals_ok and self._judged(
                    "history", node, seen, globals_ok,
                    lambda declared, inner: isinstance(declared, (Identifier, FuncCall))
                    and self.history(declared, inner, globals_ok))
            return node.name in _HISTORY_SERIES
        if isinstance(node, FuncCall):
            return self._call(node) == "ta" and self.value(node, seen, globals_ok)
        if isinstance(node, (BinOp, UnaryOp, Ternary)):
            return self.value(node, seen, globals_ok)
        return False

    def length(self, node, seen: frozenset = frozenset()) -> bool:
        """``node`` is the same on every bar: literals, constant inputs,
        ``math.*`` and operators over them, globals holding them."""
        if isinstance(node, _LITERALS):
            return True
        if isinstance(node, Identifier):
            return node.name in self.prog.program_names and self._judged(
                "length", node, seen, True, self.length)
        if isinstance(node, FuncCall):
            kind = self._call(node)
            return kind == "input" or (kind in ("math", "value") and all(
                self.length(a, seen) for a in (*node.args, *node.kwargs.values())))
        if isinstance(node, BinOp):
            return self.length(node.left, seen) and self.length(node.right, seen)
        if isinstance(node, UnaryOp):
            return self.length(node.operand, seen)
        if isinstance(node, Ternary):
            return all(self.length(part, seen)
                       for part in (node.condition, node.true_val, node.false_val))
        return False


def _payload_values(prog: ScriptIndex, func: str, request: FuncCall, reads: list[Identifier],
                    env: dict) -> tuple:
    """Each parameter ``reads`` reads, with its value on this call path, when
    the builder lowers that value on the requested bars in the place of every
    read of it (``_Lowered``): a history object, a ``ta.*`` length or history
    index, a value -- naming no global under a builtin rendered on the
    chart's terms (``nz(g)`` would read the chart's ``g``)."""
    objects, lengths, rendered = _payload_positions(prog, request)
    lowered = _Lowered(prog, func)
    fdef = prog.funcs[func]
    # A name the helper declares would capture a global of the same name.
    shadowing = set(fdef.params)
    for node in _walk(fdef.body):
        if isinstance(node, VarDecl):
            shadowing.add(node.name)
        elif isinstance(node, TupleAssign):
            shadowing.update(node.names)
        elif isinstance(node, ForStmt):
            shadowing.add(node.var)
        elif isinstance(node, ForInStmt):
            shadowing.update([node.var] if node.var else list(node.vars or ()))
    values = []
    for param in sorted({prog.refs[id(read)][2] for read in reads}):
        arg, caller_env = env.get(param, (None, None))
        if arg is None:
            raise _Unresolvable(f"parameter '{param}' has no argument")
        value = prog.resolve_payload(arg, caller_env)
        for read in (r for r in reads if prog.refs[id(r)][2] == param):
            if id(read) in objects:
                ok = lowered.history(value, globals_ok=id(read) not in rendered)
            elif id(read) in lengths:
                ok = lowered.length(value)
            else:
                ok = lowered.value(value, globals_ok=id(read) not in rendered)
            if not ok:
                raise _Unresolvable(f"the value of '{param}' is not lowered on the requested bars")
        names = {node.name for node in _walk(value) if isinstance(node, Identifier)}
        if names & shadowing:
            raise _Unresolvable(f"'{func}' declares '{sorted(names & shadowing)[0]}'")
        values.append((param, value))
    return tuple(values)


def _alias_value(prog: ScriptIndex, value):
    """``value`` with each global alias replaced by its declared value, so a
    Heikin-Ashi symbol declared after the helper reads as one."""
    seen: set[str] = set()
    while (isinstance(value, Identifier) and value.name not in seen
           and prog.refs.get(id(value), ("global",))[0] == "global"):
        decl = prog.decls.get(("global", value.name))
        if decl is None or decl.value is None or ("global", value.name) in prog.unstable:
            break
        seen.add(value.name)
        value = decl.value
    return value


def _reached(prog: ScriptIndex) -> set[str]:
    """The helpers a top-level statement, or a method, calls, transitively."""
    out: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, calls in prog.calls.items():
            if name not in out and any(
                    (owner := prog.owner.get(id(call))) is None or owner in out
                    or owner not in prog.funcs for call in calls):
                out.add(name)
                changed = True
    return out


def _mark_unreached(prog: ScriptIndex, reached: set[str]) -> None:
    """Annotate each payload read of a parameter of a helper nothing reaches:
    it never runs, so it does not warn."""
    for owner, requests in prog.requests.items():
        if owner in prog.funcs and owner not in reached:
            for request in requests:
                for read in _payload_params(prog, owner, request):
                    read.annotations = {**(read.annotations or {}),
                                        UNREACHED_ANNOTATION: True}


def _type_context_params(prog: ScriptIndex, owned: dict, leads: set[str]) -> None:
    """Declare ``string`` every untyped helper parameter that carries a
    request's symbol or timeframe as it is -- the request's own argument, or
    passed on whole to such a parameter of another helper. TradingView
    requires a string there; a helper reached only through another helper's
    parameter otherwise defaults the parameter to ``double`` while its
    caller passes a string, which does not compile."""
    carriers: dict[str, set[str]] = {}

    def bare_param(arg, func: str) -> str | None:
        binding = prog.refs.get(id(arg)) if isinstance(arg, Identifier) else None
        if binding is not None and binding[0] == "param" and binding[1] == func:
            return binding[2]
        return None

    for func, requests in owned.items():
        for request in requests:
            for arg in _request_args(request):
                param = bare_param(arg, func)
                if param is not None:
                    carriers.setdefault(func, set()).add(param)
    changed = True
    while changed:
        changed = False
        for func in leads:
            for call in _body_calls(prog, func, leads):
                callee = call.callee.name
                for param in carriers.get(callee, ()):
                    passed = bare_param(prog.param_arg(callee, call, param), func)
                    if passed is not None and passed not in carriers.get(func, set()):
                        carriers.setdefault(func, set()).add(passed)
                        changed = True
    for func, params in carriers.items():
        fdef = prog.funcs[func]
        notes = fdef.annotations = dict(fdef.annotations or {})
        hints = list(notes.get("param_type_hints") or [])
        hints += [None] * (len(fdef.params) - len(hints))
        for index, param in enumerate(fdef.params):
            if param in params and not hints[index]:
                hints[index] = "string"
        notes["param_type_hints"] = hints


def _reachable(prog: ScriptIndex, func: str, leads: set[str]) -> set[str]:
    """The helpers ``func``'s body calls, transitively."""
    seen: set[str] = set()
    stack = [func]
    while stack:
        name = stack.pop()
        for call in _body_calls(prog, name, leads):
            callee = call.callee.name
            if callee not in seen:
                seen.add(callee)
                stack.append(callee)
    return seen


def _body_calls(prog: ScriptIndex, func: str, leads: set[str]) -> list[FuncCall]:
    """The calls in ``func``'s body to helpers leading to owned requests, in
    source order."""
    return [node for node in _walk(prog.funcs[func].body)
            if isinstance(node, FuncCall) and isinstance(node.callee, Identifier)
            and node.callee.name in leads and prog.owner.get(id(node)) == func
            and id(node) in prog.call_ids]


def _analyzer_resolves(prog: ScriptIndex, func: str, symbol, tf) -> bool:
    """The analyzer's call-site clones serve this request: its symbol and
    timeframe are each a global expression or a bare parameter of ``func``,
    and every call of ``func`` passes a global expression for them."""
    params = []
    for arg in (symbol, tf):
        if arg is None or not prog.depends_on_scope(arg):
            continue
        binding = prog.refs.get(id(arg)) if isinstance(arg, Identifier) else None
        if binding is None or binding[0] != "param" or binding[1] != func:
            return False
        params.append(binding[2])
    fdef = prog.funcs[func]
    for call in prog.calls[func]:
        for param in params:
            index = fdef.params.index(param)
            if param in call.kwargs or index >= len(call.args):
                return False
            if prog.depends_on_scope(call.args[index]):
                return False
    return True
