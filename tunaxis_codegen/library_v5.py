"""Pine v5 rules inside the bodies of an inlined v5 library.

A library keeps its own ``//@version``: TradingView compiles it on its own,
and a v6 script can import a v5 one (``jdehorty/MLExtensions/2`` and
``jdehorty/KernelFunctions/2`` are v5). Where v5 and v6 read the same code
differently, a v5 library body must be lowered with v5's rules.
``V5_RULES`` enumerates every difference TradingView's migration guide to v6
lists ("Here are the changes that affect v5 scripts") and what PineForge does
with it inside a v5 library body: lower it with v5's rule here or in the
codegen (``annotations["pine_version"] == 5`` on the inlined function or
method), refuse the construct by the library's name, or nothing because the
difference cannot reach a library body's lowering.

Each implemented rule is pinned by TradingView's tape of a synthetic v5
script (``tests/fixtures/xsym_lib_tv``), replayed through a synthetic v5
library holding the same code (``tests/test_e2e_library_v5.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

from .ast_nodes import (
    ArgOrder, ASTNode, Assignment, BinOp, BoolLiteral, ColorLiteral, ExprStmt,
    ForInStmt, ForStmt, FuncCall, FuncDef, Identifier, IfStmt, MemberAccess,
    MethodDef, NaLiteral, NumberLiteral, StringLiteral, Subscript, SwitchStmt,
    Ternary, TupleAssign, TupleLiteral, TypeDecl, UnaryOp, VarDecl, WhileStmt,
)
from .errors import CompileError, Diagnostic, Level, Phase, SourceLocation
from .library_modules import LibraryModule
from . import signatures as sigs


@dataclass(frozen=True)
class V5Rule:
    change: str        # the migration guide's wording
    disposition: str   # "implemented" | "refused" | "not applicable"
    how: str


# The migration guide's list, in its order (pine-script-docs
# migration-guides/to-pine-version-6, read 2026-09-28).
V5_RULES: dict[str, V5Rule] = {
    "implicit-bool-cast": V5Rule(
        "Values of the int and float types are no longer implicitly cast to bool.",
        "implemented",
        "the codegen's truthiness helper reads na and 0 as false in every "
        "bool context, which is v5's implicit cast"),
    "bool-na": V5Rule(
        "Boolean values can no longer be na, and the na(), nz(), and fixnan() "
        "functions no longer accept bool arguments.",
        "refused",
        "a v5 bool na (a comparison with na, an na literal, a bool's history "
        "before the first bar, an if without else) reads as false wherever v5 "
        "casts it (conditions, not, and, or, ?:) and across the library "
        "boundary (TradingView's v6 caller reads it false); na(), nz(), "
        "fixnan() and str.tostring/str.format of a bool, and == / != with a "
        "bool operand, observe the third state and are refused"),
    "lazy-and-or": V5Rule(
        "The and and or operators now evaluate conditions lazily.",
        "implemented",
        "the codegen evaluates both operands of and/or, left then right, "
        "every time the expression is evaluated; ?: stays lazy (v5 too)"),
    "dynamic-requests": V5Rule(
        "All request.*() functions can now execute dynamically.",
        "refused",
        "a request.*() call in a v5 library body"),
    "const-int-division": V5Rule(
        "Division of two const int values can now return a fractional value.",
        "implemented",
        "a division of two const ints is truncated toward zero, int(a / b); "
        "a const is a literal, a const declaration or a variable never "
        "reassigned with a const value, arithmetic, ?:, int(), math.abs/"
        "max/min/round/floor/ceil and str.length of consts; a typed "
        "parameter, a reassigned variable, an input, nz() or any series is "
        "not, and a division whose constness depends on an untyped "
        "parameter (v5 types it per call) is refused"),
    "when-parameter": V5Rule(
        "The when parameter is removed from all applicable strategy.*() functions.",
        "not applicable", "a library cannot call strategy.*()"),
    "default-margin": V5Rule(
        "The default long and short margin percentage for strategies is now 100.",
        "not applicable", "a strategy() declaration, never a library's"),
    "excess-orders": V5Rule(
        "Strategies now trim the oldest orders in their results instead of "
        "raising an error when they exceed the 9000 trade limit.",
        "not applicable", "the importing strategy's order book"),
    "exit-parameter-pairs": V5Rule(
        "The strategy.exit() command no longer ignores relative parameters "
        "... when the call also includes arguments for the related absolute "
        "parameters.",
        "not applicable", "a library cannot call strategy.exit()"),
    "literal-and-field-history": V5Rule(
        "The history-referencing operator [] can no longer reference the "
        "history of literal values or fields of user-defined types directly.",
        "refused",
        "[] on a literal, a built-in constant or a user-defined type's field "
        "in a v5 library body"),
    "repeated-parameters": V5Rule(
        "Function calls can no longer include more than one argument for the "
        "same parameter.",
        "refused",
        "the parser refuses a keyword argument given twice (v5 kept the "
        "first), located in the library"),
    "series-offset": V5Rule(
        "The offset parameter of plot() and other functions no longer accepts "
        "series values.",
        "not applicable", "plot() and its kin run at the global scope; a "
        "library's global code is never inlined"),
    "unique-type-na": V5Rule(
        "na values are no longer allowed in place of built-in constants of "
        "unique types.",
        "not applicable", "those parameters style plots and drawings, which "
        "the codegen drops"),
    "timeframe-period-multiplier": V5Rule(
        'The value of timeframe.period now always includes a multiplier '
        '(e.g., "1D" instead of "D").',
        "implemented",
        'timeframe.period reads "D", "W" and "M" for a 1D, 1W and 1M chart'),
    "negative-array-index": V5Rule(
        "Some array.*() functions now accept negative index arguments.",
        "implemented",
        "array get/set/insert/remove stop the run on a negative index "
        "(TradingView's v5 runtime error), where v6 counts from the end"),
    "mutable-const": V5Rule(
        "Some mutable variables are no longer erroneously marked as const.",
        "refused",
        "a reassigned variable passed where a ta.*() or math.sum() length is "
        "(v5 runs it at its first value); a reassigned variable is not const "
        "in a division (TradingView's tape: fractional)"),
    "transp-parameter": V5Rule(
        "The transp parameter is removed from all applicable functions.",
        "not applicable", "plot-style functions, global scope only"),
    "default-colors": V5Rule(
        "Some default colors and color constants have updated values.",
        "implemented",
        "color.red, color.teal and color.yellow keep their v5 values "
        "(#FF5252, #00897B, #FFEB3B); label.new's default text color is a "
        "drawing style the codegen drops"),
    "dynamic-for-boundary": V5Rule(
        "The for loop statement now evaluates its end boundary dynamically "
        "before every iteration.",
        "implemented",
        "the codegen evaluates a for loop's `to` once, before the first "
        "iteration"),
}

_V5_COLORS = {"red": "#FF5252", "teal": "#00897B", "yellow": "#FFEB3B"}

# Built-in namespaces whose members are constants: ``[]`` on one is history
# of a constant (refused under v5).
_CONSTANT_NAMESPACES = frozenset({
    "color", "shape", "location", "size", "position", "text", "xloc", "yloc",
    "extend", "plot", "hline", "display", "format", "order", "currency",
    "dayofweek", "barmerge", "font", "scale", "alert", "adjustment",
    "backadjustment", "settlement_as_close", "label", "line", "strategy",
})
_NAMESPACES = _CONSTANT_NAMESPACES | frozenset({
    "ta", "math", "str", "array", "map", "matrix", "input", "request",
    "syminfo", "timeframe", "barstate", "session", "chart", "table",
    "linefill", "polyline", "log", "runtime", "ticker", "dividends",
    "earnings", "splits", "footprint", "volume_row", "box",
})

# What the codegen lowers by v5's rules only inside a v5 function or method
# (``CodeGen._pine_v5_body``): a top-level declaration or a type's field
# default, emitted with the script's code, refuses it.
_BODY_ONLY_OPS = frozenset({"and", "or", "==", "!="})
_BODY_ONLY_CALLS = frozenset({"na", "nz", "fixnan", "string"})
_BODY_ONLY_MEMBER_CALLS = frozenset({
    ("str", "tostring"), ("str", "format"),
    ("array", "get"), ("array", "set"), ("array", "insert"), ("array", "remove"),
})
_BODY_ONLY_METHODS = frozenset({"get", "set", "insert", "remove"})

_INT_BUILTIN_VARS = frozenset({
    "bar_index", "last_bar_index", "time", "time_close", "timenow",
    "time_tradingday", "year", "month", "weekofyear", "dayofmonth",
    "dayofweek", "hour", "minute", "second",
})

# Qualifier lattice of the v5 const-int analysis.
C, N, U = "const", "nonconst", "unknown"


def _combine(*qs: str) -> str:
    if N in qs:
        return N
    if U in qs:
        return U
    return C


def _kind_of_hint(hint) -> str:
    if not isinstance(hint, str):
        return "?"
    return {"int": "int", "float": "float", "bool": "bool",
            "string": "string"}.get(hint, "?")


class _Refusal(Exception):
    def __init__(self, node: ASTNode | None, message: str) -> None:
        super().__init__(message)
        self.node = node


class _Callable:
    """What the const-int analysis knows of one inlined body."""

    def __init__(self, node, module: "_V5Module") -> None:
        self.module = module
        hints = list((node.annotations or {}).get("param_type_hints") or ())
        self.params = {p: (hints[i] if i < len(hints) else None)
                       for i, p in enumerate(node.params)}
        self.decls: dict[str, list[VarDecl]] = {}
        self.bound: set[str] = set()          # tuple, loop and other bindings
        self.reassigned: set[str] = set()
        for stmt in _statements(node.body):
            if isinstance(stmt, VarDecl):
                self.decls.setdefault(stmt.name, []).append(stmt)
            elif isinstance(stmt, TupleAssign):
                self.bound.update(n for n in stmt.names if n != "_")
            elif isinstance(stmt, Assignment) and isinstance(stmt.target, Identifier):
                self.reassigned.add(stmt.target.name)
            elif isinstance(stmt, ForStmt) and stmt.var:
                self.bound.add(stmt.var)
            elif isinstance(stmt, ForInStmt):
                self.bound.update(v for v in [stmt.var, *(stmt.vars or [])] if v)


class _V5Module:
    def __init__(self, lib: LibraryModule, definitions: list, names: dict[str, str]) -> None:
        self.lib = lib
        self.definitions = definitions
        self.globals: dict[str, VarDecl] = {}
        self.reassigned: set[str] = set()
        for stmt in definitions:
            if isinstance(stmt, VarDecl):
                self.globals[stmt.name] = stmt
        for stmt in lib.program.body:
            if isinstance(stmt, Assignment) and isinstance(stmt.target, Identifier):
                original = stmt.target.name
                self.reassigned.add(names.get(original, original))


def _statements(body):
    """Every statement of a body, nested blocks included."""
    stack = list(reversed(body or []))
    while stack:
        stmt = stack.pop()
        yield stmt
        blocks = []
        if isinstance(stmt, IfStmt):
            blocks = [stmt.body, stmt.else_body]
        elif isinstance(stmt, (ForStmt, ForInStmt, WhileStmt)):
            blocks = [stmt.body]
        elif isinstance(stmt, SwitchStmt):
            blocks = [b for _v, b in stmt.cases] + [stmt.default_body]
        for value in (getattr(stmt, "value", None), getattr(stmt, "expr", None)):
            if isinstance(value, (IfStmt, SwitchStmt, ForStmt, WhileStmt)):
                stack.append(value)
        for block in reversed(blocks):
            stack.extend(reversed(block or []))


def _walk(expr):
    """``expr`` and every AST node below it."""
    stack = [expr]
    while stack:
        node = stack.pop()
        if not isinstance(node, ASTNode):
            continue
        yield node
        for key, value in vars(node).items():
            if key in ("loc", "annotations"):
                continue
            if isinstance(value, ASTNode):
                stack.append(value)
            elif isinstance(value, list):
                stack.extend(v for v in value if isinstance(v, ASTNode))
            elif isinstance(value, dict):
                stack.extend(v for v in value.values() if isinstance(v, ASTNode))


class _Lowering:
    """The v5 lowering of one inlined definition."""

    def __init__(self, module: _V5Module, node) -> None:
        self.module = module
        self.node = node
        self.fn = _Callable(node, module) if isinstance(node, (FuncDef, MethodDef)) else None

    # -- the const-int analysis ------------------------------------------

    def kind(self, expr) -> tuple[str, str]:
        """(kind, qualifier) of ``expr``: kind int/float/bool/string/na/?,
        qualifier const/nonconst/unknown under v5's rules (TradingView's tape
        of xc_v5_lib, ``tests/fixtures/xsym_lib_tv``)."""
        if isinstance(expr, NumberLiteral):
            return ("int" if isinstance(expr.value, int) else "float"), C
        if isinstance(expr, NaLiteral):
            return "na", C
        if isinstance(expr, BoolLiteral):
            return "bool", C
        if isinstance(expr, StringLiteral):
            return "string", C
        if isinstance(expr, ColorLiteral):
            return "color", C
        if isinstance(expr, Identifier):
            return self._identifier(expr.name)
        if isinstance(expr, UnaryOp):
            k, q = self.kind(expr.operand)
            return ("bool", q) if expr.op == "not" else (k, q)
        if isinstance(expr, BinOp):
            if expr.op in ("and", "or", "==", "!=", "<", ">", "<=", ">="):
                return "bool", _combine(self.kind(expr.left)[1], self.kind(expr.right)[1])
            (kl, ql), (kr, qr) = self.kind(expr.left), self.kind(expr.right)
            q = _combine(ql, qr)
            if expr.op == "/":
                if kl == kr == "int":
                    return ("int", C) if q == C else ("float", q)
                return ("float" if "float" in (kl, kr) else "?"), q
            if kl == kr and kl in ("int", "float", "string"):
                return kl, q
            if {kl, kr} <= {"int", "float"}:
                return "float", q
            return "?", q
        if isinstance(expr, Ternary):
            (_kc, qc), (ka, qa), (kb, qb) = (self.kind(expr.condition),
                                             self.kind(expr.true_val),
                                             self.kind(expr.false_val))
            q = _combine(qc, qa, qb)
            if ka == kb:
                return ka, q
            if {ka, kb} <= {"int", "float"}:
                return "float", q
            return "?", q
        if isinstance(expr, FuncCall):
            return self._call_kind(expr)
        if isinstance(expr, Subscript):
            return self.kind(expr.object)[0], N
        if isinstance(expr, MemberAccess):
            if isinstance(expr.object, Identifier) and expr.object.name == "color":
                return "color", C
            return "?", N
        return "?", U

    def _identifier(self, name: str) -> tuple[str, str]:
        fn = self.fn
        if fn is not None and name in fn.params:
            hint = fn.params[name]
            # A typed parameter is series (or simple) whatever the argument
            # (TradingView: xc_v5_lib's typedDiv(5, 5, 5) divides its int,
            # simple int and series int parameters by 2 as 2.5); an untyped
            # one takes its call's qualifier (a probe outside this
            # repository: 2 for the argument 5, 0.5 for bar_index on bar 1).
            return (_kind_of_hint(hint), N) if hint else ("?", U)
        if fn is not None and (name in fn.decls or name in fn.bound):
            if name in fn.bound or name in fn.reassigned:
                return self._decl_kind(fn.decls.get(name)), N
            if len(fn.decls[name]) != 1:
                # Declared in sibling blocks: each is its own variable.
                return self._decl_kind(fn.decls[name]), U
            decl = fn.decls[name][0]
            k, q = self.kind(decl.value)
            return (_kind_of_hint(decl.type_hint) if decl.type_hint else k), q
        glob = self.module.globals.get(name)
        if glob is not None:
            if name in self.module.reassigned:
                return self._decl_kind([glob]), N
            k, q = self.kind(glob.value)
            return (_kind_of_hint(glob.type_hint) if glob.type_hint else k), q
        if name in _INT_BUILTIN_VARS:
            return "int", N
        if name == "na":
            return "na", C
        return "?", N

    def _decl_kind(self, decls) -> str:
        for decl in decls or ():
            if decl.type_hint:
                return _kind_of_hint(decl.type_hint)
        return "?"

    def _call_kind(self, call: FuncCall) -> tuple[str, str]:
        callee = call.callee
        args = list(call.args) + list(call.kwargs.values())
        kinds = [self.kind(a) for a in args]
        q = _combine(*(k[1] for k in kinds)) if kinds else C
        if isinstance(callee, Identifier):
            name = callee.name
            if name == "int":
                return "int", q
            if name == "float":
                return "float", q
            if name == "bool":
                return "bool", q
            if name == "string":
                return "string", q
            if name == "nz":
                # TradingView: nz(7) / 2 divides as 3.5.
                return (kinds[0][0] if kinds else "?"), N
            if name == "na":
                return "bool", q
            # A user function's result: series unless every argument is a
            # const, where v5's per-call typing is unknown.
            return "?", (U if q == C else N)
        if isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier):
            ns, member = callee.object.name, callee.member
            if ns == "math":
                if member in ("abs",) and kinds:
                    return kinds[0][0], q
                if member in ("max", "min"):
                    kk = {k for k, _ in kinds}
                    return ("int" if kk == {"int"} else "float"), q
                if member in ("round", "floor", "ceil") and len(args) == 1:
                    return "int", q
                return "float", q
            if ns == "str" and member == "length":
                return "int", q
            if ns in _NAMESPACES:
                return "?", N
        return "?", (U if q == C else N)

    # -- rewriting --------------------------------------------------------

    def lower(self) -> None:
        node = self.node
        if isinstance(node, (FuncDef, MethodDef)):
            self._block(node.body)
            notes = node.annotations or {}
            node.annotations = dict(notes, param_defaults=[
                None if d is None else self.expr(d)
                for d in notes.get("param_defaults") or ()])
        elif isinstance(node, (VarDecl, TupleAssign)):
            node.value = self.expr(node.value)
            self._check_outside_body(node.value, "a top-level declaration")
        elif isinstance(node, TypeDecl):
            for fld in node.fields:
                if fld.default is not None:
                    fld.default = self.expr(fld.default)
                    self._check_outside_body(fld.default, "a field's default")

    def _check_outside_body(self, expr, where: str) -> None:
        """Refuse in ``expr``, emitted outside the library's functions, what
        the codegen lowers by v5's rules only inside them."""
        for node in _walk(expr):
            what = None
            if isinstance(node, BinOp) and node.op in _BODY_ONLY_OPS:
                what = f"'{node.op}'"
            elif isinstance(node, FuncCall):
                callee = node.callee
                if isinstance(callee, Identifier) and callee.name in _BODY_ONLY_CALLS:
                    what = f"{callee.name}()"
                elif isinstance(callee, MemberAccess):
                    ns = callee.object.name if isinstance(callee.object, Identifier) else None
                    if (ns, callee.member) in _BODY_ONLY_MEMBER_CALLS:
                        what = f"{ns}.{callee.member}()"
                    elif ns not in _NAMESPACES and callee.member in _BODY_ONLY_METHODS:
                        what = f"the method {callee.member}()"
            if what is not None:
                raise _Refusal(node, (
                    f"{what} in {where}: v5 reads it differently from v6, and "
                    "PineForge applies v5's rule only inside the library's "
                    "functions and methods"))

    def _block(self, stmts) -> None:
        for i, stmt in enumerate(stmts or []):
            stmts[i] = self.stmt(stmt)

    def stmt(self, stmt):
        if isinstance(stmt, VarDecl):
            stmt.value = self.expr(stmt.value)
        elif isinstance(stmt, TupleAssign):
            stmt.value = self.expr(stmt.value)
        elif isinstance(stmt, Assignment):
            stmt.target = self.expr(stmt.target)
            stmt.value = self.expr(stmt.value)
        elif isinstance(stmt, ExprStmt):
            stmt.expr = self.expr(stmt.expr)
        elif isinstance(stmt, (IfStmt, ForStmt, ForInStmt, WhileStmt, SwitchStmt)):
            self._block_node(stmt)
        return stmt

    def _block_node(self, node):
        if isinstance(node, IfStmt):
            node.condition = self.expr(node.condition)
            self._block(node.body)
            self._block(node.else_body)
        elif isinstance(node, ForStmt):
            node.start, node.end = self.expr(node.start), self.expr(node.end)
            if node.step is not None:
                node.step = self.expr(node.step)
            self._block(node.body)
        elif isinstance(node, ForInStmt):
            node.iterable = self.expr(node.iterable)
            self._block(node.body)
        elif isinstance(node, WhileStmt):
            node.condition = self.expr(node.condition)
            self._block(node.body)
        elif isinstance(node, SwitchStmt):
            if node.expr is not None:
                node.expr = self.expr(node.expr)
            node.cases = [(None if v is None else self.expr(v), b) for v, b in node.cases]
            for _v, body in node.cases:
                self._block(body)
            self._block(node.default_body)
        return node

    def expr(self, node):
        if node is None or not isinstance(node, ASTNode):
            return node
        if isinstance(node, (IfStmt, ForStmt, ForInStmt, WhileStmt, SwitchStmt)):
            return self._block_node(node)
        if isinstance(node, MemberAccess):
            if isinstance(node.object, Identifier):
                ns, member = node.object.name, node.member
                if ns == "color" and member in _V5_COLORS:
                    return self._at(ColorLiteral(value=_V5_COLORS[member]), node)
                if ns == "timeframe" and member == "period":
                    return self._v5_timeframe_period(node)
            node.object = self.expr(node.object)
            return node
        if isinstance(node, Subscript):
            self._check_history(node)
            node.object = self.expr(node.object)
            node.index = self.expr(node.index)
            return node
        if isinstance(node, FuncCall):
            self._check_call(node)
            node.callee = self.expr(node.callee) if not isinstance(node.callee, Identifier) else node.callee
            replaced = {}
            args = []
            for a in node.args:
                new = self.expr(a)
                if new is not a:
                    replaced[id(a)] = new
                args.append(new)
            kwargs = {}
            for k, a in node.kwargs.items():
                new = self.expr(a)
                if new is not a:
                    replaced[id(a)] = new
                kwargs[k] = new
            node.args, node.kwargs = args, kwargs
            if replaced:
                notes = node.annotations or {}
                order = notes.get("call_arg_order")
                if isinstance(order, ArgOrder):
                    node.annotations = dict(notes, call_arg_order=ArgOrder(
                        replaced.get(id(n), n) for n in order))
            return node
        if isinstance(node, BinOp):
            node.left = self.expr(node.left)
            node.right = self.expr(node.right)
            if node.op == "/":
                return self._division(node)
            return node
        if isinstance(node, UnaryOp):
            node.operand = self.expr(node.operand)
            return node
        if isinstance(node, Ternary):
            node.condition = self.expr(node.condition)
            node.true_val = self.expr(node.true_val)
            node.false_val = self.expr(node.false_val)
            return node
        if isinstance(node, TupleLiteral):
            node.elements = [self.expr(e) for e in node.elements]
            return node
        return node

    @staticmethod
    def _at(new: ASTNode, like: ASTNode) -> ASTNode:
        new.loc = like.loc
        return new

    def _division(self, node: BinOp):
        (kl, ql), (kr, qr) = self.kind(node.left), self.kind(node.right)
        if "float" in (kl, kr) or "na" in (kl, kr) or N in (ql, qr):
            return node
        if kl == kr == "int" and ql == qr == C:
            # v5 divides two const ints as integers, truncating toward zero
            # (TradingView: -7 / 2 = -3, 7 / -2 = -3); int() of v6's
            # fractional quotient is that.
            callee = self._at(Identifier(name="int"), node)
            call = self._at(FuncCall(callee=callee, args=[node], kwargs={}), node)
            return call
        raise _Refusal(node, (
            "v5 divides two const ints as integers, and whether both operands "
            "of this division are const depends on its call (an untyped "
            "parameter takes its argument's qualifier in v5); PineForge does "
            "not specialize a v5 function per call"))

    def _v5_timeframe_period(self, node: MemberAccess):
        """``timeframe.period`` as v5 spells it: no multiplier of 1 for a
        day, week or month (TradingView: "D" on a 1D chart)."""
        def period():
            return self._at(MemberAccess(
                object=self._at(Identifier(name="timeframe"), node), member="period"), node)

        result = period()
        for v6, v5 in (("1M", "M"), ("1W", "W"), ("1D", "D")):
            cond = self._at(BinOp(left=period(), op="==",
                                  right=self._at(StringLiteral(value=v6), node)), node)
            result = self._at(Ternary(condition=cond,
                                      true_val=self._at(StringLiteral(value=v5), node),
                                      false_val=result), node)
        return result

    def _check_history(self, node: Subscript) -> None:
        obj = node.object
        if isinstance(obj, (NumberLiteral, StringLiteral, BoolLiteral, ColorLiteral, NaLiteral)):
            raise _Refusal(node, "[] on a literal reads its history, which v5 "
                                 "allows and PineForge does not implement")
        if isinstance(obj, MemberAccess) and isinstance(obj.object, Identifier):
            if obj.object.name in _CONSTANT_NAMESPACES:
                raise _Refusal(node, "[] on a built-in constant reads its history, "
                                     "which v5 allows and PineForge does not implement")
            if obj.object.name not in _NAMESPACES:
                raise _Refusal(node, "[] on a user-defined type's field reads its "
                                     "history directly, which v5 allows and "
                                     "PineForge does not implement")
        elif isinstance(obj, MemberAccess):
            raise _Refusal(node, "[] on a user-defined type's field reads its "
                                 "history directly, which v5 allows and PineForge "
                                 "does not implement")

    def _check_call(self, call: FuncCall) -> None:
        callee = call.callee
        if not (isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier)):
            return
        ns, member = callee.object.name, callee.member
        if ns == "request":
            raise _Refusal(call, f"request.{member}() in a v5 library runs under "
                                 "v5's non-dynamic request rules, which PineForge "
                                 "does not implement")
        length_params: set[int | str] = set()
        if ns == "ta" and member in sigs.TA_FUNCTIONS:
            for sig in sigs.TA_FUNCTIONS[member].signatures:
                for i, p in enumerate(sig.params):
                    if p.pine_type == sigs.I:
                        length_params.update((i, p.name))
        elif ns == "math" and member == "sum":
            length_params.update((1, "length"))
        if not length_params:
            return
        bound = [(i, a) for i, a in enumerate(call.args)] + list(call.kwargs.items())
        for key, arg in bound:
            if key in length_params and self._reads_reassigned(arg):
                raise _Refusal(arg, (
                    f"a reassigned variable passed as {ns}.{member}()'s length: "
                    "v5 marks it const and runs the call at its first value, "
                    "which PineForge does not implement"))

    def _reads_reassigned(self, expr) -> bool:
        fn = self.fn
        return any(
            isinstance(node, Identifier)
            and ((fn is not None and node.name in fn.reassigned)
                 or node.name in self.module.reassigned)
            for node in _walk(expr))


def lower_v5_modules(modules: list[tuple[LibraryModule, list, dict[str, str]]],
                     filename: str) -> None:
    """Lower the reachable definitions of every v5 module in ``modules``
    (library, its inlined definitions in source order, its top-level renames)
    with v5's rules, refusing by the library's name what PineForge does not
    implement."""
    for lib, definitions, names in modules:
        if lib.pine_version != 5:
            continue
        module = _V5Module(lib, definitions, names)
        for node in definitions:
            try:
                _Lowering(module, node).lower()
            except _Refusal as exc:
                loc = getattr(exc.node, "loc", None) or SourceLocation(
                    file=lib.path, line=1, col=1, end_col=1)
                raise CompileError([Diagnostic(
                    level=Level.ERROR, phase=Phase.PARSER, location=loc,
                    message=f"library '{lib.path}' is //@version=5: {exc}",
                )]) from None
