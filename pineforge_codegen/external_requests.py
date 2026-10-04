"""Requests PineForge has no data for, and the reads of requests lowered
onto data a run is given.

``request.security`` on another symbol and ``request.financial`` /
``earnings`` / ``dividends`` / ``splits`` / ``footprint`` read data the
engine does not load itself: a probe's requests manifest pins it
(``PINEFORGE_REQUESTS_ROOT``). A request of another symbol the support checker
lowers onto that symbol's pinned feed (``FEED_LOWERING``), and a fundamentals
request lowered onto the series recorded under its key (``RECORDED_LOWERING``,
``recorded_key``), stay; their reads are marked like a deferred refusal's
below, and stop the run only when the run began with no data for them.
``TradeSlice`` follows such a request's value forward
through the script: when it reaches display and alert sinks only -- the
backward slice of every trade sink holds no part of it -- the request is
lowered to ``na`` with a warning (``lower_no_data_requests``). Every other one
is a deferred refusal: it lowers to a value whose first READ stops the run
with an error naming the request, since no data is pinned for it. Binding it
is no read: ``e = request.earnings(...)`` stops the run only where ``e`` is
read, so a script reading it only in a branch it never takes runs.

The slice is conservative. Trade sinks are the order calls (``strategy.*``
that places, closes or cancels, ``strategy.risk.*``) and ``runtime.error``. A
value the request taints, and every statement under a condition it taints,
may reach operators, built-ins that return ``na`` for an ``na`` argument
(``math.*``, ``color.*``, ``ta.*``, ``nz``, ``str.tostring``, ...), user
functions (followed through their parameters and results), bindings (a
``var`` too: bindings are flow-insensitive, so a value that feeds a later
bar's state is followed), and the display and alert sinks (``alert``,
``plot*``, ``table.*``, ``log.*``). Anything else -- an order, a collection
or drawing, a method, ``break``, a field write, a built-in this module does
not list -- keeps the refusal: the lowered ``na`` could change it where
TradingView's value would not.
"""

from __future__ import annotations

from .ast_nodes import (
    ASTNode, Assignment, BinOp, BoolLiteral, BreakStmt, ContinueStmt,
    ExprStmt, ForInStmt, ForStmt, FuncCall, FuncDef, Identifier, IfStmt,
    MemberAccess, MethodDef, NaLiteral, NumberLiteral, Program, StringLiteral, Subscript,
    SwitchStmt, Ternary, TupleAssign, TupleLiteral, UnaryOp, VarDecl, WhileStmt,
)
from .errors import SourceLocation
from .security_contexts import ScriptIndex, replace_nodes


LOWERING_ANNOTATION = "pf_request_lowering"
# The lowering of a request of another symbol that reads the feed a requests
# manifest pins for it (``request.security``; the engine's instrument feeds).
FEED_LOWERING = "feed"
# The lowering of ``request.earnings`` / ``dividends`` / ``splits`` /
# ``financial``: the series TradingView returned per chart bar, recorded under
# its key (``recorded_key``) by the requests manifest.
RECORDED_LOWERING = "recorded"
# Lowerings whose data is looked up at run time: the request stays, and its
# reads stop the run only where the data is missing.
DATA_LOWERINGS = frozenset({FEED_LOWERING, RECORDED_LOWERING})
# The lowering of a ``request.security_lower_tf`` of another symbol whose
# value can reach a trade: the engine reads no other symbol's intrabars, and
# the request read the chart's. It stays (its array type flows on), and
# evaluating it stops the run with the request named.
ABSENT_LOWERING = "absent"
# On a recorded request once lowered: its key's parts but the symbol, which
# stays the call's only argument (``lower_no_data_requests``).
RECORDED_KEY_ANNOTATION = "pf_recorded_key"
# Pine's field constants per recorded function, and each default; the
# financial periods (the requests manifest's key grammar, workflow
# campaign/src/probe-requests.mjs formatRecordedKey).
RECORDED_FIELDS = {
    "earnings": (("actual", "estimate", "standardized"), "actual"),
    "dividends": (("gross", "net"), "gross"),
    "splits": (("denominator", "numerator"), "denominator"),
}
FINANCIAL_PERIODS = ("FQ", "FY", "FH", "TTM")
# Each recorded function's parameters, in order.
_RECORDED_PARAMS = {
    "earnings": ("ticker", "field", "gaps", "lookahead", "ignore_invalid_symbol", "currency"),
    "dividends": ("ticker", "field", "gaps", "lookahead", "ignore_invalid_symbol", "currency"),
    "splits": ("ticker", "field", "gaps", "lookahead", "ignore_invalid_symbol", "currency"),
    "financial": ("symbol", "financial_id", "period", "gaps", "ignore_invalid_symbol",
                  "currency"),
}
# On a node whose evaluation stops the run: the message it stops with, or,
# for a request lowered onto pinned data, ``{"message": ..., "ref":
# RequestRef}``: the run stops there only when no data is installed for the
# request that carries the same ref (``REQUEST_REF_ANNOTATION``).
UNPINNED_ANNOTATION = "pf_request_unpinned"
CAPABILITY_UNPINNED_ANNOTATION = "pf_capability_unpinned_requests"


def _record_unpinned_capability(program, request):
    notes = program.annotations = dict(program.annotations or {})
    sites = notes.setdefault(CAPABILITY_UNPINNED_ANNOTATION, [])
    sites.append({
        "function": f"request.{request.callee.member}",
        "symbol_node": request.args[0] if request.args else request.kwargs.get("symbol"),
        "tf_node": request.args[1] if len(request.args) > 1 else request.kwargs.get("timeframe"),
        "lookahead_node": request.kwargs.get("lookahead"),
        "gaps_node": request.kwargs.get("gaps"),
    })
REQUEST_REF_ANNOTATION = "pf_request_ref"
# On a request whose symbol can select the chart's or another symbol's: it
# read the chart before it read a feed, and keeps that lowering where no feed
# can be keyed (``unpin_requests``).
CHART_FALLBACK_ANNOTATION = "pf_request_chart_fallback"
# On a request of another symbol whose symbol or timeframe reaches it through
# a helper's parameters: the support checker's warning, which
# ``security_contexts`` reports once every call path keys a feed (else it
# reports why the request keeps its earlier lowering).
FEED_WARNING_ANNOTATION = "pf_request_feed_warning"
# On a ``request.footprint`` that is the whole expression of a request of
# another symbol reading its feed: the feed column its delta is read from.
FOOTPRINT_COLUMN_ANNOTATION = "pf_footprint_column"


class RequestRef:
    """Names a request lowered onto pinned data from the reads marked for
    it. A plain object, not a node, so no AST walk follows it; a helper copy
    (``security_contexts``) copies it once for the request and its reads."""
# request.* calls whose data PineForge never has.
NO_DATA_REQUEST_FUNCS = frozenset({"financial", "earnings", "dividends", "splits", "footprint"})

_ORDER_CALLS = frozenset({
    "entry", "order", "exit", "close", "close_all", "cancel", "cancel_all",
})
_DISPLAY_CALLS = frozenset({
    "alert", "alertcondition", "plot", "plotshape", "plotchar", "plotarrow",
    "plotcandle", "plotbar", "bgcolor", "barcolor", "fill", "hline",
})
_DISPLAY_NAMESPACES = frozenset({"table", "log"})
# Built-ins that return na (or a value) for an na argument, never stop the
# script, and change nothing but their own result.
_NA_SAFE_CALLS = frozenset({"na", "nz", "fixnan", "int", "float", "bool", "string"})
_NA_SAFE_NAMESPACES = frozenset({"math", "color", "ta"})
_NA_SAFE_STR = frozenset({
    "tostring", "format", "length", "upper", "lower", "contains", "startswith",
    "endswith", "trim", "replace", "replace_all",
})
_BOOL_TA = frozenset({"cross", "crossover", "crossunder", "rising", "falling"})


def _call_name(node: FuncCall) -> tuple[str | None, str | None]:
    callee = node.callee
    if isinstance(callee, Identifier):
        return None, callee.name
    if isinstance(callee, MemberAccess):
        parts = []
        obj = callee.object
        while isinstance(obj, MemberAccess):
            parts.append(obj.member)
            obj = obj.object
        if isinstance(obj, Identifier):
            parts.append(obj.name)
            return ".".join(reversed(parts)), callee.member
    return None, None


def recorded_key(request: FuncCall) -> tuple[dict | None, str | None]:
    """``(parts, None)`` of a recorded request's key but its symbol --
    ``{"fn", "symbol", "field", "period", "gaps", "lookahead"}``, the symbol
    the argument node -- or ``(None, why)`` for a spelling no key names: a
    field constant of another namespace or none, a financial id or period
    that is not a literal of the grammar, a ``gaps``/``lookahead`` that is
    not a ``barmerge`` constant, a ``currency`` (the tape records no
    conversion)."""
    ns, fn = _call_name(request)
    if ns != "request" or fn not in _RECORDED_PARAMS:
        return None, "it is no recorded request"
    params = _RECORDED_PARAMS[fn]
    if len(request.args) > len(params):
        return None, "it has more arguments than the request takes"
    args = dict(zip(params, request.args))
    for name, value in request.kwargs.items():
        if name not in params or name in args:
            return None, f"its argument {name} is not one the key names"
        args[name] = value
    if "currency" in args:
        return None, "it converts to a currency, which no recorded key names"
    symbol = args.get(params[0])
    if symbol is None:
        return None, "it names no symbol"

    def flag(name: str, kind: str) -> str | None:
        value = args.get(name)
        if value is None:
            return "off"
        if (isinstance(value, MemberAccess) and isinstance(value.object, Identifier)
                and value.object.name == "barmerge"
                and value.member in (f"{kind}_on", f"{kind}_off")):
            return value.member.rsplit("_", 1)[1]
        return None

    gaps = flag("gaps", "gaps")
    lookahead = flag("lookahead", "lookahead") if fn != "financial" else "off"
    if gaps is None or lookahead is None:
        return None, "its gaps and lookahead are barmerge constants"
    if fn == "financial":
        fid, period = args.get("financial_id"), args.get("period")
        if not (isinstance(fid, StringLiteral) and fid.value[:1].isalpha()
                and fid.value.replace("_", "").isalnum() and fid.value == fid.value.upper()):
            return None, "its financial_id is a literal TradingView financial id"
        if not (isinstance(period, StringLiteral) and period.value in FINANCIAL_PERIODS):
            return None, f"its period is one of the literals {', '.join(FINANCIAL_PERIODS)}"
        field_text, period_text = fid.value, period.value
    else:
        fields, default = RECORDED_FIELDS[fn]
        value = args.get("field")
        if value is None:
            field_text = default
        elif (isinstance(value, MemberAccess) and isinstance(value.object, Identifier)
                and value.object.name == fn and value.member in fields):
            field_text = value.member
        else:
            return None, f"its field is one of the {fn}.* constants"
        period_text = "-"
    return {"fn": fn, "symbol": symbol, "field": field_text, "period": period_text,
            "gaps": gaps, "lookahead": lookahead}, None


def footprint_column(request) -> str | None:
    """``fp_delta_<ticks>_<va>``: the feed column a ``request.footprint``
    call with literal ticks per row and value-area percent reads its delta
    from (the requests manifest's column names), else None."""
    if not (isinstance(request, FuncCall) and _call_name(request) == ("request", "footprint")):
        return None
    ticks = request.args[0] if request.args else request.kwargs.get("ticks_per_row")
    va = (request.args[1] if len(request.args) > 1 else request.kwargs.get("va_percent",
                                                                            NumberLiteral(value=70)))
    values = []
    for arg in (ticks, va):
        if not (isinstance(arg, NumberLiteral) and float(arg.value).is_integer()
                and int(arg.value) > 0):
            return None
        values.append(int(arg.value))
    return f"fp_delta_{values[0]}_{values[1]}"


def _footprint_payload(request) -> FuncCall | None:
    """The ``request.footprint`` call that is ``request``'s whole
    expression (``request.security(sym, tf, request.footprint(100, 70))``)."""
    if isinstance(request, FuncCall) and _call_name(request) == ("request", "security"):
        payload = request.args[2] if len(request.args) > 2 else request.kwargs.get("expression")
        if isinstance(payload, FuncCall) and _call_name(payload) == ("request", "footprint"):
            return payload
    return None


class FootprintValues:
    """The script's footprint values, as ``ScriptIndex`` bindings:
    declarations and helper parameters typed ``footprint``, declarations
    holding a ``request.footprint`` (inside a ``request.security`` or not) or
    a helper's call returning one, and the parameters a helper's calls pass
    one to. PineForge reads a footprint's ``delta()`` only, so a footprint
    value is its delta."""

    def __init__(self, index: ScriptIndex) -> None:
        self.index = index
        self.bindings: set[tuple] = {
            ("param", name, param) for name, fdef in index.funcs.items()
            for param, hint in zip(fdef.params, _param_type_hints(fdef))
            if str(hint or "").strip() == "footprint"}
        self.returns: set[str] = set()  # helpers returning a footprint
        decls = [(binding, decl) for binding, decl in index.decls.items()
                 if isinstance(decl, VarDecl)]
        changed = True
        while changed:
            changed = False
            for binding, decl in decls:
                if binding not in self.bindings and (
                        str(decl.type_hint or "").strip() == "footprint"
                        or self.holds(decl.value)):
                    self.bindings.add(binding)
                    changed = True
            for name, fdef in index.funcs.items():
                last = fdef.body[-1] if fdef.body else None
                if (name not in self.returns and isinstance(last, ExprStmt)
                        and self.holds(last.expr)):
                    self.returns.add(name)
                    changed = True
                for param in fdef.params:
                    if ("param", name, param) not in self.bindings and any(
                            self.holds(index.param_arg(name, call, param))
                            for call in index.calls.get(name, ())):
                        self.bindings.add(("param", name, param))
                        changed = True

    @staticmethod
    def is_request(expr) -> bool:
        return (isinstance(expr, FuncCall) and _call_name(expr) == ("request", "footprint")
                or _footprint_payload(expr) is not None)

    def holds(self, expr) -> bool:
        """``expr`` is a footprint value."""
        if isinstance(expr, Identifier):
            return self.index.refs.get(id(expr)) in self.bindings
        if (isinstance(expr, FuncCall) and id(expr) in self.index.call_ids
                and expr.callee.name in self.returns):
            return True
        return self.is_request(expr)

    def member(self, call: FuncCall) -> tuple[str, ASTNode | None, bool] | None:
        """``(member, footprint, only argument)`` of ``fp.member(...)`` or
        ``footprint.member(fp, ...)``, else None; the flag says the footprint
        is the call's only argument."""
        callee = call.callee
        if not isinstance(callee, MemberAccess):
            return None
        arity = len(call.args) + len(call.kwargs)
        if (isinstance(callee.object, Identifier) and callee.object.name == "footprint"
                and id(callee.object) not in self.index.refs):
            first = call.args[0] if call.args else call.kwargs.get("id")
            if first is None or not self.holds(first):
                return None
            return callee.member, first, arity == 1
        if self.holds(callee.object):
            return callee.member, callee.object, arity == 0
        return None


def _param_type_hints(fdef: FuncDef) -> list:
    return list((fdef.annotations or {}).get("param_type_hints") or ())


def read_footprint_deltas(program: Program) -> None:
    """``fp.delta()`` and ``footprint.delta(fp)`` read the footprint's
    delta, which is the value a footprint lowers to: each becomes ``fp``, and
    a declaration typed ``footprint`` a float. The support checker refuses
    every other footprint member."""
    if not any((isinstance(n, Identifier) and n.name == "footprint")
               or (isinstance(n, MemberAccess) and n.member == "footprint")
               or (isinstance(n, VarDecl) and str(n.type_hint or "").strip() == "footprint")
               for n in _nodes(program)):
        return  # most scripts name no footprint: index none for them
    values = FootprintValues(ScriptIndex(program))
    swaps: dict[int, ASTNode] = {}
    for node in _nodes(program):
        if isinstance(node, FuncCall):
            read = values.member(node)
            if read is not None and read[0] == "delta" and read[2]:
                swaps[id(node)] = read[1]
        elif isinstance(node, VarDecl) and str(node.type_hint or "").strip() == "footprint":
            node.type_hint = "float"
        elif isinstance(node, FuncDef) and any(
                str(hint or "").strip() == "footprint" for hint in _param_type_hints(node)):
            node.annotations = {**node.annotations, "param_type_hints": [
                "float" if str(hint or "").strip() == "footprint" else hint
                for hint in _param_type_hints(node)]}
    if swaps:
        replace_nodes(program, swaps)


def no_data_request(node) -> str | None:
    """``financial`` ... ``footprint`` for a request PineForge has no data
    for by its function alone, else None."""
    if isinstance(node, FuncCall):
        ns, name = _call_name(node)
        if ns == "request" and name in NO_DATA_REQUEST_FUNCS:
            return name
    return None


def spell_call(node: FuncCall) -> str:
    """The call as a message names it: callee and its first arguments."""
    ns, name = _call_name(node)
    head = f"{ns}.{name}" if ns else str(name)

    def arg(value) -> str:
        if isinstance(value, StringLiteral):
            return f'"{value.value}"'
        if isinstance(value, Identifier):
            return value.name
        if isinstance(value, MemberAccess):
            inner = arg(value.object)
            return f"{inner}.{value.member}" if inner != "..." else "..."
        if isinstance(value, FuncCall):
            return spell_call(value)
        return "..."

    shown = [arg(a) for a in node.args[:2]]
    if len(node.args) > 2 or node.kwargs:
        shown.append("...")
    line = f" at line {node.loc.line}" if node.loc is not None else ""
    return f"{head}({', '.join(shown)}){line}"


class TradeSlice:
    """Whether a request's value can reach a trade (module docstring)."""

    def __init__(self, program: Program) -> None:
        self.index = ScriptIndex(program)
        self.program = program
        self.methods = {s.name: s for s in program.body if isinstance(s, MethodDef)}

    def reason(self, request: FuncCall) -> str | None:
        """None when ``request``'s value reaches no trade sink, else the use
        that may."""
        return _Taint(self, request).run()


class _Unsafe(Exception):
    pass


class _Taint:
    def __init__(self, slice_: TradeSlice, request: FuncCall) -> None:
        self.s = slice_
        self.idx = slice_.index
        self.request = request
        self.tainted: set[tuple] = set()
        self.returns: set[str] = set()      # user functions / methods returning taint
        self.guarded: set[str] = set()      # functions called under tainted control
        self.changed = True

    def run(self) -> str | None:
        try:
            self._payload_is_pure()
            while self.changed:
                self.changed = False
                self._block(self.s.program.body, False)
                for name, fdef in self.idx.funcs.items():
                    self._callable(name, fdef, name in self.guarded)
                for name, mdef in self.s.methods.items():
                    self._callable(name, mdef, False)
        except _Unsafe as exc:
            return str(exc)
        return None

    # -- the value ------------------------------------------------------------

    def _add(self, binding) -> None:
        if binding is not None and binding not in self.tainted:
            self.tainted.add(binding)
            self.changed = True

    def _reads_taint(self, expr) -> bool:
        for node in _nodes(expr):
            if node is self.request:
                return True
            if isinstance(node, Identifier) and self.idx.refs.get(id(node)) in self.tainted:
                return True
            if isinstance(node, FuncCall):
                ns, name = _call_name(node)
                if ns is None and name in self.returns and name in self.idx.funcs:
                    return True
                if (isinstance(node.callee, MemberAccess) and name in self.returns
                        and name in self.s.methods):
                    return True
        return False

    def _payload_is_pure(self) -> None:
        """A dropped request runs nothing: its arguments, the payload's too,
        may only compute."""
        seen: set[str] = set()
        stack = [list(self.request.args) + list(self.request.kwargs.values())]
        while stack:
            for node in _nodes(stack.pop()):
                if not isinstance(node, FuncCall):
                    continue
                ns, name = _call_name(node)
                if ns is None and name in self.idx.funcs:
                    if name not in seen:
                        seen.add(name)
                        stack.append(self.idx.funcs[name].body)
                    continue
                if self._is_order(ns, name):
                    raise _Unsafe(f"its expression calls {ns}.{name}")
                if not (self._is_display(node, ns, name) or self._is_pure(ns, name)):
                    raise _Unsafe(f"its expression calls {_label(ns, name)}")

    # -- statements -------------------------------------------------------------

    def _callable(self, name: str, fdef, guarded: bool) -> None:
        self._block(fdef.body, guarded)
        if fdef.body and name not in self.returns:
            last = fdef.body[-1]
            value = last.expr if isinstance(last, ExprStmt) else last
            if self._reads_taint(value):
                self.returns.add(name)
                self.changed = True

    def _block(self, stmts, guarded: bool) -> None:
        for stmt in stmts:
            self._stmt(stmt, guarded)

    def _stmt(self, stmt, guarded: bool) -> None:
        if isinstance(stmt, (FuncDef, MethodDef)):
            return
        if isinstance(stmt, VarDecl):
            self._expr(stmt.value, guarded)
            if guarded or self._reads_taint(stmt.value):
                self._add(self.idx.decl_binding.get((id(stmt), stmt.name)))
            return
        if isinstance(stmt, TupleAssign):
            self._expr(stmt.value, guarded)
            if guarded or self._reads_taint(stmt.value):
                for name in stmt.names:
                    self._add(self.idx.decl_binding.get((id(stmt), name)))
            return
        if isinstance(stmt, Assignment):
            self._expr(stmt.value, guarded)
            tainted = guarded or self._reads_taint(stmt.value)
            if isinstance(stmt.target, Identifier):
                if tainted:
                    self._add(self.idx.refs.get(id(stmt.target)))
            elif tainted or self._reads_taint(stmt.target):
                raise _Unsafe("it is written to a field or element")
            else:
                self._expr(stmt.target, guarded)
            return
        if isinstance(stmt, IfStmt):
            self._expr(stmt.condition, guarded)
            inner = guarded or self._reads_taint(stmt.condition)
            self._block(stmt.body, inner)
            self._block(stmt.else_body, inner)
            return
        if isinstance(stmt, SwitchStmt):
            parts = [stmt.expr] + [case for case, _ in stmt.cases]
            for part in parts:
                self._expr(part, guarded)
            inner = guarded or any(self._reads_taint(p) for p in parts)
            for _, body in stmt.cases:
                self._block(body, inner)
            self._block(stmt.default_body, inner)
            return
        if isinstance(stmt, (ForStmt, ForInStmt, WhileStmt)):
            heads = ([stmt.start, stmt.end, stmt.step] if isinstance(stmt, ForStmt)
                     else [stmt.iterable] if isinstance(stmt, ForInStmt) else [stmt.condition])
            for head in heads:
                self._expr(head, guarded)
            inner = guarded or any(self._reads_taint(h) for h in heads)
            if inner:
                names = ([stmt.var] if isinstance(stmt, ForStmt)
                         else [stmt.var] if isinstance(stmt, ForInStmt) and stmt.var
                         else list(getattr(stmt, "vars", None) or ()))
                for node in _nodes(stmt.body):
                    if isinstance(node, Identifier) and node.name in names:
                        self._add(self.idx.refs.get(id(node)))
            self._block(stmt.body, inner)
            return
        if isinstance(stmt, (BreakStmt, ContinueStmt)):
            if guarded:
                raise _Unsafe("it decides a loop's break or continue")
            return
        if isinstance(stmt, ExprStmt):
            self._expr(stmt.expr, guarded)
            return
        self._expr(stmt, guarded)

    # -- expressions ------------------------------------------------------------

    def _expr(self, expr, guarded: bool) -> None:
        """Check every call ``expr`` makes, under ``guarded`` control and the
        lazy operands' own conditions."""
        if expr is None or expr is self.request:
            return
        if isinstance(expr, (list, tuple)):
            for item in expr:
                self._expr(item, guarded)
            return
        if isinstance(expr, (IfStmt, SwitchStmt)):
            self._stmt(expr, guarded)
            return
        if not isinstance(expr, ASTNode):
            return
        if isinstance(expr, Ternary):
            self._expr(expr.condition, guarded)
            inner = guarded or self._reads_taint(expr.condition)
            self._expr(expr.true_val, inner)
            self._expr(expr.false_val, inner)
            return
        if isinstance(expr, BinOp) and expr.op in ("and", "or"):
            self._expr(expr.left, guarded)
            self._expr(expr.right, guarded or self._reads_taint(expr.left))
            return
        if isinstance(expr, FuncCall):
            self._call(expr, guarded)
        if isinstance(expr, Subscript) and self._reads_taint(expr.index):
            # A history offset of na can stop the script where TradingView's
            # value would not.
            raise _Unsafe("it is a history offset")
        for key, value in vars(expr).items():
            if key not in ("loc", "annotations", "callee"):
                self._expr(value, guarded)

    def _call(self, call: FuncCall, guarded: bool) -> None:
        ns, name = _call_name(call)
        args = list(call.args) + list(call.kwargs.values())
        receiver = call.callee.object if isinstance(call.callee, MemberAccess) else None
        if receiver is not None and not (
                isinstance(receiver, Identifier) and id(receiver) not in self.idx.refs):
            # A method call's receiver is an argument (``arr.push(x)``).
            args.append(receiver)
        tainted_args = [a for a in args if self._reads_taint(a)]
        if ns is None and name in self.idx.funcs:
            fdef = self.idx.funcs[name]
            for param in fdef.params:
                arg = self.idx.param_arg(name, call, param)
                if arg is not None and self._reads_taint(arg):
                    self._add(("param", name, param))
            if guarded and name not in self.guarded:
                self.guarded.add(name)
                self.changed = True
            return
        if not (guarded or tainted_args):
            return
        if self._is_order(ns, name):
            raise _Unsafe(f"it reaches {_label(ns, name)}")
        if self._is_display(call, ns, name):
            return
        if tainted_args and self._is_na_safe(ns, name):
            return
        if not tainted_args and self._is_pure(ns, name):
            return
        raise _Unsafe(f"it reaches {_label(ns, name)}")

    # -- call families ------------------------------------------------------------

    @staticmethod
    def _is_order(ns, name) -> bool:
        return ((ns == "strategy" and name in _ORDER_CALLS) or ns == "strategy.risk"
                or (ns, name) == ("runtime", "error"))

    def _is_display(self, call: FuncCall, ns, name) -> bool:
        if ns is None and name in _DISPLAY_CALLS:
            return True
        if ns in _DISPLAY_NAMESPACES:
            return True
        # A method on a table: ``board.cell(...)``.
        receiver = call.callee.object if isinstance(call.callee, MemberAccess) else None
        if isinstance(receiver, Identifier):
            binding = self.idx.refs.get(id(receiver))
            decl = self.idx.decls.get(binding) if binding is not None else None
            if decl is not None and (str(decl.type_hint or "").strip() == "table" or (
                    isinstance(decl.value, FuncCall)
                    and _call_name(decl.value) == ("table", "new"))):
                return True
        return False

    @staticmethod
    def _is_na_safe(ns, name) -> bool:
        return ((ns is None and name in _NA_SAFE_CALLS) or ns in _NA_SAFE_NAMESPACES
                or (ns == "str" and name in _NA_SAFE_STR))

    def _is_pure(self, ns, name) -> bool:
        """A built-in that changes nothing but its own result."""
        if self._is_na_safe(ns, name):
            return True
        if ns in ("str", "timeframe", "syminfo", "ticker", "input", "chart.point"):
            return True
        if ns == "request" and name in ("security", "security_lower_tf"):
            return True
        return ns is None and name in (
            "time", "time_close", "timestamp", "hour", "minute", "second",
            "dayofmonth", "dayofweek", "month", "year", "weekofyear",
        )


def _label(ns, name) -> str:
    return f"{ns}.{name}(...)" if ns else f"{name}(...)"


def _nodes(value):
    """Every AST node below ``value`` (annotations and locations aside)."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, ASTNode):
            yield item
            stack.extend(v for k, v in vars(item).items() if k not in ("loc", "annotations"))
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
        elif isinstance(item, dict):
            stack.extend(item.values())


# ---------------------------------------------------------------------------
# Lowering
# ---------------------------------------------------------------------------

def _na_like(expr, funcs: dict[str, FuncDef], depth: int = 0):
    """An ``na`` of ``expr``'s shape: a tuple of them for a tuple, ``false``
    for a bool (TradingView reads an na bool as false), an empty string for
    a string, else a float ``na``."""
    if depth > 8:
        return NaLiteral(loc=getattr(expr, "loc", None))
    loc = getattr(expr, "loc", None)
    if isinstance(expr, TupleLiteral):
        return TupleLiteral(elements=[_na_like(e, funcs, depth + 1) for e in expr.elements], loc=loc)
    if isinstance(expr, FuncCall):
        ns, name = _call_name(expr)
        if ns is None and name in funcs and funcs[name].body:
            last = funcs[name].body[-1]
            value = last.expr if isinstance(last, ExprStmt) else None
            if isinstance(value, TupleLiteral):
                local = {s.name: s.value for s in funcs[name].body if isinstance(s, VarDecl)}
                return TupleLiteral(elements=[
                    _na_scalar(e, local, loc) for e in value.elements], loc=loc)
            if value is not None:
                return _na_like(value, funcs, depth + 1)
    return _na_scalar(expr, {}, loc)


def _na_scalar(expr, local: dict, loc, depth: int = 0):
    kind = _kind(expr, local, depth)
    if kind == "bool":
        return BoolLiteral(value=False, loc=loc)
    if kind == "string":
        return StringLiteral(value="", loc=loc)
    return NaLiteral(loc=loc)


def _kind(expr, local: dict, depth: int = 0) -> str:
    if depth > 8:
        return "float"
    if isinstance(expr, BoolLiteral):
        return "bool"
    if isinstance(expr, StringLiteral):
        return "string"
    if isinstance(expr, UnaryOp) and expr.op == "not":
        return "bool"
    if isinstance(expr, BinOp):
        if expr.op in ("==", "!=", "<", "<=", ">", ">=", "and", "or"):
            return "bool"
        if expr.op == "+" and "string" in (_kind(expr.left, local, depth + 1),
                                           _kind(expr.right, local, depth + 1)):
            return "string"
        return "float"
    if isinstance(expr, Ternary):
        return _kind(expr.true_val, local, depth + 1)
    if isinstance(expr, Identifier) and expr.name in local:
        return _kind(local[expr.name], local, depth + 1)
    if isinstance(expr, Subscript):
        return _kind(expr.object, local, depth + 1)
    if isinstance(expr, FuncCall):
        ns, name = _call_name(expr)
        if ns == "ta" and name in _BOOL_TA or (ns, name) == (None, "na"):
            return "bool"
        if ns == "str" and name not in ("length", "pos", "tonumber"):
            return "string"
    return "float"


def unpinned_message(node: FuncCall) -> str:
    """What the run stops with when it reads a request with no data."""
    return f"{spell_call(node)}: no data is pinned for this request, and its value was read"


def _na_of(request: FuncCall, funcs: dict[str, FuncDef]) -> ASTNode:
    """The ``na`` a request lowers to when no data is read for it."""
    payload = None if no_data_request(request) else (
        request.args[2] if len(request.args) > 2 else request.kwargs.get("expression"))
    return _na_like(payload, funcs) if payload is not None else NaLiteral(loc=request.loc)


def _mark_reads(program: Program, index: ScriptIndex, declarations: dict,
                request: FuncCall, evaluated: ASTNode, marker) -> None:
    """Mark where ``request``'s value is read with ``marker``: the reads of
    the names of a declaration holding the whole request, those names never
    reassigned (binding it is no read); else ``evaluated``, the node the
    request is evaluated as."""
    stmt = declarations.get(id(request))
    names = ([stmt.name] if isinstance(stmt, VarDecl) else
             list(stmt.names) if isinstance(stmt, TupleAssign) else [])
    bindings = {index.decl_binding.get((id(stmt), name)) for name in names} - {None}
    if stmt is None or not bindings or bindings & index.reassigned:
        evaluated.annotations = {**(evaluated.annotations or {}), UNPINNED_ANNOTATION: marker}
        return
    for node in _nodes(program):
        if isinstance(node, Subscript) and isinstance(node.object, Identifier):
            reads = node.object
        elif isinstance(node, Identifier):
            reads = node
        else:
            continue
        if index.refs.get(id(reads)) in bindings:
            node.annotations = {**(node.annotations or {}), UNPINNED_ANNOTATION: marker}


def lower_no_data_requests(program: Program) -> Program:
    """Replace each request the support checker lowered
    (``annotations[LOWERING_ANNOTATION]``) by its ``na``: an inert one's
    plainly, and an unpinned one's so that its first read stops the run.
    When the request is the whole value of a declaration whose names are
    never reassigned, those names' reads stop it; otherwise evaluating the
    request does. A request lowered onto pinned data (``DATA_LOWERINGS``)
    stays, and the same reads stop the run only when its data is missing
    when the run begins."""
    read_footprint_deltas(program)
    funcs = {s.name: s for s in program.body if isinstance(s, FuncDef)}
    swaps: dict[int, ASTNode] = {}
    unpinned: list[FuncCall] = []
    backed: list[FuncCall] = []
    for node in _nodes(program):
        lowering = (node.annotations or {}).get(LOWERING_ANNOTATION)
        if lowering is None or not isinstance(node, FuncCall):
            continue
        if lowering in DATA_LOWERINGS:
            backed.append(node)
            continue
        if lowering == ABSENT_LOWERING:
            _record_unpinned_capability(program, node)
            node.annotations = {**node.annotations, UNPINNED_ANNOTATION: unpinned_message(node)}
            continue
        swaps[id(node)] = _na_of(node, funcs)
        if lowering == "unpinned":
            _record_unpinned_capability(program, node)
            unpinned.append(node)
    if unpinned or backed:
        index = ScriptIndex(program)
        declarations = {id(stmt.value): stmt for stmt in _nodes(program)
                        if isinstance(stmt, (VarDecl, TupleAssign))}
        for request in unpinned:
            _mark_reads(program, index, declarations, request, swaps[id(request)],
                        unpinned_message(request))
        for request in backed:
            ref = RequestRef()
            request.annotations = {**(request.annotations or {}), REQUEST_REF_ANNOTATION: ref}
            _mark_reads(program, index, declarations, request, request,
                        {"message": unpinned_message(request), "ref": ref})
            if request.annotations.get(LOWERING_ANNOTATION) == RECORDED_LOWERING:
                # The key's other parts are constants: the call keeps its
                # symbol, the one part the run computes.
                parts, _ = recorded_key(request)
                symbol = parts.pop("symbol")
                request.args, request.kwargs = [symbol], {}
                notes = {k: v for k, v in request.annotations.items() if k != "call_arg_order"}
                request.annotations = {**notes, RECORDED_KEY_ANNOTATION: parts}
    if swaps:
        replace_nodes(program, swaps)
    return program


def pass_warning(node: ASTNode, message: str, hint: str) -> tuple:
    """A warning of a pass between the support checker and the analyzer,
    held on the AST as plain data (the analyzer makes it a ``Diagnostic``:
    AST walkers recurse into annotation values, and an enum cycles)."""
    return (message, hint, node.loc or SourceLocation(file="<input>", line=1, col=1, end_col=1))


def unpin_requests(program: Program, reasons: dict[int, str]) -> None:
    """Give each request ``reasons`` names (by id: a request of another
    symbol whose symbol registration cannot compute before the first bar)
    the lowering it had before it read a feed: its ``na``, whose reads stop
    the run with the request named; or, for one whose symbol can select the
    chart's (``CHART_FALLBACK_ANNOTATION``), the chart's bars, with the
    warning it had. Each is reported with its reason."""
    from .security_contexts import PASS_WARNINGS_ANNOTATION

    funcs = {s.name: s for s in program.body if isinstance(s, FuncDef)}
    requests = {id(node): node for node in _nodes(program) if id(node) in reasons}
    refs = {id((node.annotations or {}).get(REQUEST_REF_ANNOTATION)) for node in requests.values()}
    chart_refs = {id(node.annotations.get(REQUEST_REF_ANNOTATION)) for node in requests.values()
                  if (node.annotations or {}).get(CHART_FALLBACK_ANNOTATION)}
    swaps: dict[int, ASTNode] = {}
    warnings = []
    for request_id, request in list(requests.items()):
        if (request.annotations or {}).get(CHART_FALLBACK_ANNOTATION):
            request.annotations = {
                k: v for k, v in request.annotations.items()
                if k not in (LOWERING_ANNOTATION, REQUEST_REF_ANNOTATION, UNPINNED_ANNOTATION,
                             CHART_FALLBACK_ANNOTATION, FEED_WARNING_ANNOTATION)}
            warnings.append(pass_warning(
                request,
                f"{spell_call(request)}: request.security symbol can select an alternate "
                "symbol, but PineForge always loads the current chart symbol.",
                f"It reads no feed of another symbol: {reasons[request_id]}. Every reachable "
                "symbol value must resolve to syminfo.tickerid or syminfo.ticker for exact "
                "results."))
            continue
        lowered = _na_of(request, funcs)
        _record_unpinned_capability(program, request)
        marker = (request.annotations or {}).get(UNPINNED_ANNOTATION)
        if isinstance(marker, dict):
            lowered.annotations = {**(lowered.annotations or {}),
                                   UNPINNED_ANNOTATION: marker["message"]}
        swaps[request_id] = lowered
        # The request leaves the program; its lowering is the deferred
        # refusal's now (``request_discovery`` lists it so).
        request.annotations = {**(request.annotations or {}), LOWERING_ANNOTATION: "unpinned"}
        warnings.append(pass_warning(
            request,
            f"{spell_call(request)}: no data is pinned for this request; the run stops with "
            "an error where its value is read.",
            f"PineForge reads another symbol's feed only for a symbol and timeframe "
            f"registration computes before the first bar; {reasons[request_id]}."))
    for node in _nodes(program):
        marker = (node.annotations or {}).get(UNPINNED_ANNOTATION)
        if isinstance(marker, dict) and id(marker["ref"]) in chart_refs:
            node.annotations = {k: v for k, v in node.annotations.items()
                                if k != UNPINNED_ANNOTATION}
        elif isinstance(marker, dict) and id(marker["ref"]) in refs:
            node.annotations = {**node.annotations, UNPINNED_ANNOTATION: marker["message"]}
    if swaps:
        replace_nodes(program, swaps)
    notes = program.annotations = dict(program.annotations or {})
    notes[PASS_WARNINGS_ANNOTATION] = [*notes.get(PASS_WARNINGS_ANNOTATION, ()), *warnings]
