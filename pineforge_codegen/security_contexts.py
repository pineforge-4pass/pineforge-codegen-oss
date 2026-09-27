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
"""

from __future__ import annotations

import copy
from dataclasses import replace

from .ast_nodes import (
    ASTNode, Assignment, BinOp, BoolLiteral, ColorLiteral, ForInStmt, ForStmt,
    FuncCall, FuncDef, Identifier, IfStmt, MemberAccess, MethodDef, NaLiteral,
    NumberLiteral, Program, StringLiteral, SwitchStmt, Ternary, TupleAssign,
    UnaryOp, VarDecl, WhileStmt,
)
from .errors import CompileError, Diagnostic, Level, Phase, SourceLocation


CONTEXT_ANNOTATION = "pf_security_context"
DEAD_ANNOTATION = "pf_security_dead"
_REQUEST_FUNCS = ("security", "security_lower_tf")
# Instances (a helper under one set of parameter values) the pass may build
# before it refuses the script: diamond-shaped helper graphs multiply paths.
_MAX_INSTANCES = 512
_LITERALS = (StringLiteral, NumberLiteral, BoolLiteral, NaLiteral, ColorLiteral)
# Built-in series a context cannot be computed from before the first bar.
_BAR_SERIES = frozenset({
    "open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4",
    "time", "time_close", "time_tradingday", "timenow", "bar_index", "last_bar_index",
})


class _Unresolvable(Exception):
    """A context value no registration can compute."""


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


class _Program:
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
        self.unstable: set[tuple] = set()  # reassigned, var or varip
        # FuncCall id -> the callable whose body holds it (None: top level).
        self.owner: dict[int, str | None] = {}
        self.calls: dict[str, list[FuncCall]] = {name: [] for name in self.funcs}
        self.call_ids: set[int] = set()  # the calls in ``calls``
        self.requests: dict[str | None, list[FuncCall]] = {}
        self._index()

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
                    if stmt.is_var or stmt.is_varip:
                        self.unstable.add(key)
                elif isinstance(stmt, TupleAssign):
                    for name in stmt.names:
                        scope.setdefault(
                            name, ("global", name) if top else ("bound", id(stmt), name))
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


def _error(node, message: str, filename: str) -> Diagnostic:
    loc = getattr(node, "loc", None) or SourceLocation(file=filename, line=1, col=1, end_col=1)
    return Diagnostic(level=Level.ERROR, phase=Phase.ANALYZER, location=loc, message=message,
                      hint=("Pass the symbol and timeframe as literals, inputs or globals "
                            "at the helper's call sites."))


def specialize_security_contexts(program: Program, filename: str = "<input>") -> Program:
    """Resolve the context of every helper request (module docstring)."""
    prog = _Program(program)
    # The requests whose context this pass owns, by helper.
    owned: dict[str, list[FuncCall]] = {}
    for owner, requests in prog.requests.items():
        if owner not in prog.funcs:
            continue
        for request in requests:
            symbol, tf = _request_args(request)
            if not (prog.depends_on_scope(symbol) or prog.depends_on_scope(tf)):
                continue
            if _request_name(request) == "security" and _analyzer_resolves(
                    prog, owner, symbol, tf):
                continue
            owned.setdefault(owner, []).append(request)
    if not owned:
        return program

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

    first_request = {func: requests[0] for func, requests in owned.items()}
    for func in sorted(leads):
        # The request a helper leads to, for the message.
        target = first_request.get(func) or next(
            first_request[g] for g in sorted(owned)
            if g in _reachable(prog, func, leads))
        if func in prog.overloaded:
            refuse(target, f"request.{_request_name(target)} context: helper '{func}' "
                           "is overloaded, and its overloads' call paths are not told apart")
        for call in prog.calls[func]:
            owner = prog.owner.get(id(call))
            if owner is not None and owner not in prog.funcs:
                refuse(target, f"request.{_request_name(target)} context: helper "
                               f"'{func}' is called from {owner}, whose calls this "
                               "pass does not follow")
    if errors:
        raise CompileError(list(errors.values()))
    _type_context_params(prog, owned, leads)

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
                try:
                    values.append(prog.resolve(arg, env) if arg is not None else None)
                except _Unresolvable as exc:
                    chain = " -> ".join(f"{name}()" for name, _ in here)
                    refuse(request, f"request.{_request_name(request)} {what} cannot be "
                                    f"resolved before the first bar on the call path "
                                    f"{chain}: {exc}")
                    values.append(None)
            contexts.append((request, tuple(values)))
        kids = []
        for call in _body_calls(prog, func, leads):
            callee = call.callee.name
            callee_env = {p: (prog.param_arg(callee, call, p), env)
                          for p in prog.funcs[callee].params}
            kids.append((call, instantiate(callee, callee_env, here)))
        signature = (func, tuple(context_key(v) for _, v in contexts),
                     tuple(sig for _, sig in kids))
        if signature not in detail:
            detail[signature] = {"contexts": contexts, "kids": kids}
            instances.setdefault(func, []).append(signature)
        return signature

    top: list[tuple[FuncCall, tuple]] = []
    try:
        for name in sorted(leads, key=lambda n: program.body.index(prog.funcs[n])):
            for call in prog.calls[name]:
                if prog.owner.get(id(call)) is not None:
                    continue
                env = {p: (prog.param_arg(name, call, p), None)
                       for p in prog.funcs[name].params}
                top.append((call, instantiate(name, env, ())))
    except _Unresolvable as exc:
        request = next(iter(owned.values()))[0]
        refuse(request, f"request.{_request_name(request)} context: {exc}")
    if errors:
        raise CompileError(list(errors.values()))

    # A request no top-level statement reaches never runs.
    for func, requests in owned.items():
        if func not in instances:
            for request in requests:
                request.annotations = {**(request.annotations or {}), DEAD_ANNOTATION: True}

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
        for request, (symbol, tf) in info["contexts"]:
            target = mapped(request)
            target.annotations = {**(target.annotations or {}),
                                  CONTEXT_ANNOTATION: {"symbol": symbol, "timeframe": tf}}
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


def _type_context_params(prog: _Program, owned: dict, leads: set[str]) -> None:
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


def _reachable(prog: _Program, func: str, leads: set[str]) -> set[str]:
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


def _body_calls(prog: _Program, func: str, leads: set[str]) -> list[FuncCall]:
    """The calls in ``func``'s body to helpers leading to owned requests, in
    source order."""
    return [node for node in _walk(prog.funcs[func].body)
            if isinstance(node, FuncCall) and isinstance(node.callee, Identifier)
            and node.callee.name in leads and prog.owner.get(id(node)) == func
            and id(node) in prog.call_ids]


def _analyzer_resolves(prog: _Program, func: str, symbol, tf) -> bool:
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
