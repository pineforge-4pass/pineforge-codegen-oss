"""PineScript v6 support checker for PineForge.

Walks a parsed AST and rejects scripts that use language constructs, built-in
functions, variables, or `request.security` parameters that PineForge cannot
faithfully execute. Source-of-truth tables are imported from the transpiler's
own analyzer/codegen/signatures modules so the checker cannot drift.

Buckets:

* HARD_REJECT_FUNC / HARD_REJECT_NAMESPACE - calls that have no PineForge
  semantics at all (e.g. ``request.financial``, ``ticker.*``).
* DIVERGENT_VARS - built-in variables whose PineForge value diverges from
  TradingView (e.g. ``bar_index`` depends on data window, ``last_bar_index``
  is wrongly aliased in codegen). Reported as WARNING — these often appear
  in visual or logging code that does not affect trade outcomes.
* NOT_YET - calls the runtime could support but the transpiler does not yet
  emit (e.g. ``max_bars_back``, bare ``barssince``).
* request.security - only ``symbol`` / ``timeframe`` / ``expression`` allowed,
  symbol must be the current chart symbol.
* Declarations - only ``strategy(...)`` accepted; ``indicator(...)`` and
  ``library(...)`` rejected.
* Unknown ``ta.X`` / ``math.X`` / ``str.X`` / ``input.X`` calls (codegen would
  silently emit ``na`` / ``0`` / ``""`` stubs).
"""

from __future__ import annotations

from typing import Callable

from .ast_nodes import (
    ASTNode,
    Program, StrategyDecl,
    VarDecl, Assignment, TupleAssign,
    IfStmt, ForStmt, ForInStmt, WhileStmt, SwitchStmt,
    FuncDef, ExprStmt,
    BinOp, UnaryOp, Ternary, FuncCall, Subscript,
    Identifier, MemberAccess,
    NumberLiteral, StringLiteral, BoolLiteral, NaLiteral, ColorLiteral,
    TupleLiteral,
    TypeDecl, EnumDecl, MethodDef,
)
from .errors import SourceLocation, Diagnostic, CompileError, Level, Phase
from . import signatures as sigs
from .tv_input_choices import INPUT_SOURCE_SERIES_IDS
from .analyzer import TA_CLASS_MAP
from .codegen import (
    MATH_FUNC_MAP, STR_FUNC_MAP,
    ARRAY_METHODS, MAP_METHODS, MATRIX_METHODS,
    SYMINFO_MEMBER_MAP, COLOR_CONST_MAP,
    SKIP_FUNC_NAMES, SKIP_NAMESPACES,
    BAR_BUILTINS, BAR_FIELDS,
)


# ---------------------------------------------------------------------------
# Rule tables
# ---------------------------------------------------------------------------

# `ta.sum` is exposed by TA_CLASS_MAP only as a backing implementation for
# `math.sum`; it is not a real Pine identifier. Strip from supported set.
TA_PROPERTY_VARIABLES: frozenset[str] = frozenset(
    {"obv", "accdist", "nvi", "pvi", "pvt", "wad", "wvad", "iii"}
)
SUPPORTED_TA: frozenset[str] = frozenset(
    set(TA_CLASS_MAP) - {"sum", "vwap_bands"} - set(TA_PROPERTY_VARIABLES) | {"tr", "pivot_point_levels"}
)
SUPPORTED_MATH: frozenset[str] = frozenset(
    set(MATH_FUNC_MAP) | set(sigs.MATH_CONSTANTS) | set(sigs.MATH_FUNCTIONS)
)
SUPPORTED_STR: frozenset[str] = frozenset(set(STR_FUNC_MAP) | {"format_time"})
SUPPORTED_INPUT: frozenset[str] = frozenset(sigs.INPUT_FUNCTIONS)
SUPPORTED_ARRAY: frozenset[str] = frozenset(set(ARRAY_METHODS) | {"new", "new_float", "new_int", "new_bool", "new_string", "from"})
SUPPORTED_MAP: frozenset[str] = frozenset(set(MAP_METHODS) | {"new"})
SUPPORTED_MATRIX: frozenset[str] = frozenset(set(MATRIX_METHODS) | {"new"})
SUPPORTED_SYMINFO: frozenset[str] = frozenset(SYMINFO_MEMBER_MAP)
SUPPORTED_COLOR_CONST: frozenset[str] = frozenset(COLOR_CONST_MAP)
SUPPORTED_COLOR_FUNC: frozenset[str] = frozenset({"new", "rgb", "r", "g", "b", "t"})
SUPPORTED_TIMEFRAME_FUNC: frozenset[str] = frozenset({"change", "in_seconds"})
SUPPORTED_RUNTIME_FUNC: frozenset[str] = frozenset({"error"})
# log.* helpers wired into pine_log_{info,warning,error} by codegen/visit_call.
# Without this whitelist, codegen silently emits an empty-string literal
# (``"" /* unsupported log */``) for unknown log names. Valid C++, but a dead
# string statement that hides the typo from the strategy author.
SUPPORTED_LOG: frozenset[str] = frozenset({"info", "warning", "error"})

HARD_REJECT_FUNC: dict[str, str] = {
    "request.financial":         "External fundamentals data not available in PineForge.",
    "request.dividends":         "External corporate-action data not available in PineForge.",
    "request.earnings":          "External corporate-action data not available in PineForge.",
    "request.splits":            "External corporate-action data not available in PineForge.",
    "request.seed":              "External seed data feeds not available in PineForge.",
    "request.quandl":            "External Quandl data not available in PineForge.",
    "request.currency_rate":     "Currency conversion data not available in PineForge.",
    "color.from_gradient":       "Charting helpers not available in PineForge backtests.",
    # ticker.* chart-type modifiers and cross-symbol constructors — hard reject
    "ticker.heikinashi":         (
        "ticker.heikinashi() chart-type modifier / cross-symbol construction not supported — "
        "PineForge runs same-symbol MTF only via request.security with the chart's own tickerid."
    ),
    "ticker.renko":              (
        "ticker.renko() chart-type modifier / cross-symbol construction not supported — "
        "PineForge runs same-symbol MTF only via request.security with the chart's own tickerid."
    ),
    "ticker.kagi":               (
        "ticker.kagi() chart-type modifier / cross-symbol construction not supported — "
        "PineForge runs same-symbol MTF only via request.security with the chart's own tickerid."
    ),
    "ticker.linebreak":          (
        "ticker.linebreak() chart-type modifier / cross-symbol construction not supported — "
        "PineForge runs same-symbol MTF only via request.security with the chart's own tickerid."
    ),
    "ticker.pointfigure":        (
        "ticker.pointfigure() chart-type modifier / cross-symbol construction not supported — "
        "PineForge runs same-symbol MTF only via request.security with the chart's own tickerid."
    ),
    "ticker.new":                (
        "ticker.new() chart-type modifier / cross-symbol construction not supported — "
        "PineForge runs same-symbol MTF only via request.security with the chart's own tickerid."
    ),
    "ticker.modify":             (
        "ticker.modify() chart-type modifier / cross-symbol construction not supported — "
        "PineForge runs same-symbol MTF only via request.security with the chart's own tickerid."
    ),
}

# ticker.inherit and ticker.standard are NOT in HARD_REJECT_NAMESPACE;
# they pass through in codegen (emit the symbol argument unchanged).
# The remaining ticker.* chart-type modifiers are in HARD_REJECT_FUNC above.
HARD_REJECT_NAMESPACE: dict[str, str] = {
    # (ticker namespace blanket-reject removed; per-function entries above)
}

# Built-in variables whose PineForge value diverges from TradingView semantics.
# Demoted to WARNING — many real strategies use bar_index / time_close in
# logging or visual logic that does not affect trade outcomes. The checker
# still flags divergence so users see the risk.
DIVERGENT_VARS: dict[str, str] = {
    "bar_index":      "bar_index depends on the data window; PineForge and TradingView produce different values for the same script.",
    "last_bar_index": "last_bar_index is incorrectly aliased to the current bar index in PineForge codegen.",
    "timenow":        "timenow is aliased to the current bar timestamp in PineForge; it is not real wall-clock time.",
    "time_close":     "time_close is aliased to the bar open timestamp in PineForge; it does not represent the bar close time.",
}

BARSTATE_APPROX_VARS: dict[str, str] = {
    "barstate.islast": "barstate.islast is always false in PineForge batch backtests.",
    "barstate.ishistory": "barstate.ishistory is always true in PineForge batch backtests.",
    "barstate.isrealtime": "barstate.isrealtime is always false in PineForge batch backtests.",
    "barstate.isnew": "barstate.isnew follows PineForge first-tick execution state.",
    "barstate.isconfirmed": "barstate.isconfirmed follows PineForge last-tick execution state.",
    "barstate.islastconfirmedhistory": "barstate.islastconfirmedhistory is always false in PineForge batch backtests.",
}

STRATEGY_UNSUPPORTED_PARAMS: dict[str, set[str]] = {
    "entry": {"alert_message", "disable_alert"},
    "order": {"comment", "alert_message", "disable_alert", "qty_type"},
    "exit": {"comment_profit", "comment_loss", "comment_trailing", "alert_message", "alert_profit", "alert_loss", "alert_trailing", "disable_alert"},
    "close": {"alert_message", "disable_alert"},
    "close_all": {"comment", "alert_message", "disable_alert", "immediately"},
}

# strategy.closedtrades / strategy.opentrades accessor surfaces are NOT
# symmetric in Pine v6. opentrades has no exit_* fields (a trade has not
# closed yet). Both lack ``direction`` (Pine has ``size`` whose sign carries
# the direction). Splitting the whitelist makes the support checker reject
# typos like ``strategy.opentrades.exit_price(0)`` that previously slipped
# through into codegen and produced trade-accessor calls the runtime does
# not implement on the open-trades side.
CLOSED_TRADE_ACCESSOR_METHODS: frozenset[str] = frozenset({
    "profit", "profit_percent", "commission",
    "entry_bar_index", "exit_bar_index", "entry_comment", "exit_comment",
    "entry_id", "exit_id", "entry_price", "exit_price", "entry_time",
    "exit_time", "size", "max_runup", "max_runup_percent",
    "max_drawdown", "max_drawdown_percent",
})
OPEN_TRADE_ACCESSOR_METHODS: frozenset[str] = frozenset({
    "profit", "profit_percent", "commission",
    "entry_bar_index", "entry_comment", "entry_id",
    "entry_price", "entry_time",
    "size", "max_runup", "max_runup_percent",
    "max_drawdown", "max_drawdown_percent",
})
# Back-compat alias for any external consumer still importing the union set
# (kept as the union of the two new sets so a stale import never silently
# narrows). New code should prefer the side-specific constant above.
TRADE_ACCESSOR_METHODS: frozenset[str] = (
    CLOSED_TRADE_ACCESSOR_METHODS | OPEN_TRADE_ACCESSOR_METHODS
)

STRATEGY_EXIT_PRICE_PARAMS: frozenset[str] = frozenset({
    "profit", "loss", "limit", "stop", "trail_price", "trail_points", "trail_offset",
})

# Implementable but currently silent in codegen -> reject loudly.
NOT_YET_FUNC: dict[str, str] = {
    "max_bars_back":            "max_bars_back is silently dropped by the codegen.",
    "timeframe.from_seconds":   "timeframe.from_seconds is not yet implemented; codegen would emit 'false' and silently produce wrong TF strings.",
}

# Bare (no-namespace) function names that codegen has no handler for.
# Without a handler, the generic emitter at visit_call.py:912 would
# produce e.g. `color(arg)` — an undeclared C++ symbol. Reject loudly.
UNSUPPORTED_BARE_FUNCS: dict[str, str] = {
    "color": "Bare color(...) cast is not supported. Use color.new(c, alpha) or color.rgb(r, g, b, transp).",
}

# Whole namespaces with NO codegen support. Any call into one of these
# fails to compile downstream. Reject loudly with a precise message so
# users don't see a cryptic C++ error referencing an undeclared
# namespace.
UNSUPPORTED_NAMESPACES: dict[str, str] = {
    "footprint":  "footprint.* (bid/ask volume rows) is not supported in PineForge batch backtests; it requires tick-level data the engine does not consume.",
    "volume_row": "volume_row.* is not supported in PineForge batch backtests; same reason as footprint.*.",
}

# Member-access references with no batch-mode equivalent. Codegen would
# silently emit "false" (visit_expr.py chart.* fallthrough) which
# becomes epoch 0 in time arithmetic. Reject loudly.
UNSUPPORTED_MEMBERS: dict[tuple[str, str], str] = {
    ("chart", "left_visible_bar_time"):  "chart.left_visible_bar_time has no meaning in a batch backtest (no viewport).",
    ("chart", "right_visible_bar_time"): "chart.right_visible_bar_time has no meaning in a batch backtest (no viewport).",
    ("chart", "bg_color"): "chart.bg_color has no meaning in a batch backtest (no chart theme).",
    ("chart", "fg_color"): "chart.fg_color has no meaning in a batch backtest (no chart theme).",
}

# Constant-only namespaces whose members are drawing/visual style constants
# (or call-scoped option constants). They are legitimately consumed as
# ARGUMENTS to parse-and-skip visual calls (plot, plotshape, hline,
# label.new, table.cell, ...), inside the strategy() declaration, and — for
# barmerge.* — as request.security gaps/lookahead values. Outside those
# contexts codegen falls through to ``std::string("<member>")`` while the
# analyzer types the read INT: a silent type mismatch that at best surfaces
# as a cryptic C++ error. Reject loudly when the member access survives into
# non-visual code paths (see ``_const_arg_ctx_depth``).
_CONST_NS_VISUAL_MSG = (
    "is a visual/style constant with no runtime value in PineForge "
    "backtests; it is only accepted as an argument to visual calls "
    "(plot, plotshape, hline, label.new, table.cell, ...), which "
    "PineForge parses and skips."
)
UNSUPPORTED_CONST_NAMESPACES: dict[str, str] = {
    "extend":   _CONST_NS_VISUAL_MSG,
    "font":     _CONST_NS_VISUAL_MSG,
    "hline":    _CONST_NS_VISUAL_MSG,
    "location": _CONST_NS_VISUAL_MSG,
    "plot":     _CONST_NS_VISUAL_MSG,
    "scale":    _CONST_NS_VISUAL_MSG,
    "shape":    _CONST_NS_VISUAL_MSG,
    "text":     _CONST_NS_VISUAL_MSG,
    "xloc":     _CONST_NS_VISUAL_MSG,
    "yloc":     _CONST_NS_VISUAL_MSG,
    "barmerge": (
        "is only valid as the gaps/lookahead argument of "
        "request.security(...); it has no runtime value as a free "
        "expression in PineForge."
    ),
    "alert": (
        "is only valid as the freq argument of alert(...); it has no "
        "runtime value as a free expression in PineForge."
    ),
}

# Namespaces whose variable members have no batch-mode data source.
# These reads currently emit `std::string("<member>")` via the
# unknown-identifier fallthrough — which the analyzer-typed `double`
# context then rejects at C++ compile time. Catch it earlier.
UNSUPPORTED_NAMESPACE_VARS: dict[str, str] = {
    "dividends": "dividends.* is not available in PineForge — fundamental dividend data is not loaded.",
    "earnings":  "earnings.* is not available in PineForge — fundamental earnings data is not loaded.",
    "splits":    "splits.* is not available in PineForge — corporate-action split data is not loaded.",
}

# request.security parameter rules.
# Codegen supports symbol/timeframe/expression plus gaps/lookahead (read in
# _eval_security_* emission and forwarded to register_security_eval).
# ignore_invalid_symbol/currency are accepted by the parser but silently
# dropped by codegen — reject loudly to surface unsupported behavior.
SECURITY_ALLOWED_PARAMS: frozenset[str] = frozenset(
    {"symbol", "timeframe", "expression", "gaps", "lookahead",
     # Data-adjustment constants — silently accepted and ignored by codegen;
     # the underlying engine uses a fixed unadjusted data source.
     "backadjustment", "settlement_as_close", "adjustment"}
)
SECURITY_PARAM_ORDER: tuple[str, ...] = (
    "symbol", "timeframe", "expression",
    "gaps", "lookahead", "ignore_invalid_symbol", "currency",
)
SECURITY_MAX_POSITIONAL: int = 5  # symbol, timeframe, expression, gaps, lookahead

# Per-kwarg allowed values for request.security data-adjustment params.
# Values NOT in this list cause silent wrong-result backtests: codegen
# emits a numeric constant which the engine ignores entirely, producing
# a different price series from TradingView with no warning. Reject
# loudly when the script passes anything outside the no-op set.
#
# (off / none = the engine's de-facto behavior; inherit = "follow chart
# settings" which, in batch mode with a single data source, is also a
# no-op.)
SECURITY_ADJUSTMENT_ALLOWED_VALUES: dict[str, frozenset[str]] = {
    "backadjustment":      frozenset({"off", "inherit"}),
    "settlement_as_close": frozenset({"off", "inherit"}),
    "adjustment":          frozenset({"none", "inherit"}),
}
# Identifiers/expressions that resolve to "this script's symbol".
SECURITY_CURRENT_SYMBOL_NAMES: frozenset[str] = frozenset({"syminfo.tickerid", "syminfo.ticker"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _loc(node: ASTNode | None, fallback_file: str) -> SourceLocation:
    if node is not None and getattr(node, "loc", None) is not None:
        return node.loc
    return SourceLocation(file=fallback_file, line=1, col=1, end_col=1)


def _qualified_name(callee: ASTNode) -> tuple[str | None, str | None]:
    """Return (namespace, function_name) for a call's callee, or (None, None)."""
    if isinstance(callee, Identifier):
        return None, callee.name
    if isinstance(callee, MemberAccess):
        obj = callee.object
        # Single-level: ns.func
        if isinstance(obj, Identifier):
            return obj.name, callee.member
        # Multi-level: foo.bar.baz - flatten left side
        if isinstance(obj, MemberAccess):
            left_ns, left_name = _qualified_name(obj)
            if left_ns is None and left_name is not None:
                return left_name, callee.member
            if left_ns is not None and left_name is not None:
                return f"{left_ns}.{left_name}", callee.member
    return None, None


def _resolve_member_chain(node: ASTNode) -> str | None:
    """Flatten a MemberAccess/Identifier chain into a dotted name."""
    if isinstance(node, Identifier):
        return node.name
    if isinstance(node, MemberAccess):
        left = _resolve_member_chain(node.object)
        if left is None:
            return None
        return f"{left}.{node.member}"
    return None


# ---------------------------------------------------------------------------
# Checker
# ---------------------------------------------------------------------------

class SupportChecker:
    """AST walker that produces Diagnostic objects for unsupported features."""

    # syminfo fields that emit na<T>() (or route through the runtime metadata
    # map, which returns na until a feed injects a value) due to missing data;
    # warn when used in conditional context (if-condition or ternary
    # condition) because the condition will always evaluate to false/na.
    # Derived from the emission table so new na-accept fields cannot drift
    # out of the warning: every SYMINFO_MEMBER_MAP entry whose emission is an
    # ``na<T>()`` literal or a ``get_syminfo_metadata(...)`` lookup is a
    # silent gap (sector/industry/isin/expiration_date/current_contract/
    # mincontract/root/pricescale/minmove + employees/shareholders/
    # shares_outstanding_*/recommendations_*/target_price_*).
    _SYMINFO_SILENT_GAP_FIELDS: frozenset[str] = frozenset(
        member
        for member, emission in SYMINFO_MEMBER_MAP.items()
        if "na<" in emission or "get_syminfo_metadata" in emission
    )

    def __init__(self, ast: Program, filename: str = "<input>") -> None:
        self._ast = ast
        self._filename = filename
        self._diagnostics: list[Diagnostic] = []
        # Names defined locally (UDTs, enums, user functions, methods) so we
        # don't flag user-defined identifiers as unknown built-ins.
        self._user_types: set[str] = set()
        self._user_enums: set[str] = set()
        self._user_funcs: set[str] = set()
        self._user_methods: set[str] = set()
        # Track whether we are inside an if/ternary condition expression.
        self._in_conditional_depth: int = 0
        # Track whether we are inside an argument subtree that legitimately
        # consumes constant-namespace members: parse-and-skip visual calls
        # (plot, label.new, ...), the strategy() declaration, and
        # request.security (barmerge.* gaps/lookahead values). While > 0 the
        # UNSUPPORTED_CONST_NAMESPACES rejection is suppressed.
        self._const_arg_ctx_depth: int = 0

    # -- Public API --

    def check(self) -> list[Diagnostic]:
        self._collect_user_definitions(self._ast)
        for stmt in self._ast.body:
            self._visit(stmt)
        return self._diagnostics

    def check_or_raise(self) -> None:
        diags = self.check()
        errors = [d for d in diags if d.level == Level.ERROR]
        if errors:
            raise CompileError(diags)

    # -- Setup --

    def _collect_user_definitions(self, ast: Program) -> None:
        for stmt in ast.body:
            if isinstance(stmt, TypeDecl):
                self._user_types.add(stmt.name)
            elif isinstance(stmt, EnumDecl):
                self._user_enums.add(stmt.name)
            elif isinstance(stmt, FuncDef):
                self._user_funcs.add(stmt.name)
            elif isinstance(stmt, MethodDef):
                self._user_methods.add(stmt.name)

    # -- Diagnostic emission --

    def _err(self, node: ASTNode | None, message: str, hint: str | None = None) -> None:
        self._diagnostics.append(Diagnostic(
            level=Level.ERROR,
            phase=Phase.ANALYZER,
            location=_loc(node, self._filename),
            message=message,
            hint=hint,
        ))

    def _warn(self, node: ASTNode | None, message: str, hint: str | None = None) -> None:
        self._diagnostics.append(Diagnostic(
            level=Level.WARNING,
            phase=Phase.ANALYZER,
            location=_loc(node, self._filename),
            message=message,
            hint=hint,
        ))

    def _reject_if_in(
        self,
        table: dict,
        key,
        node: ASTNode,
        msg_fmt: Callable[[object, str], str],
        hint: str | None = None,
    ) -> bool:
        """Look ``key`` up in ``table``; if present, emit an error built from
        ``msg_fmt(key, table[key])``, visit children, and return True. Otherwise
        return False so the caller can fall through to later checks.

        Consolidates the near-identical lookup+emit blocks that dispatch the
        UNSUPPORTED_* tables in _visit_FuncCall / _visit_MemberAccess.
        """
        if key not in table:
            return False
        self._err(node, msg_fmt(key, table[key]), hint=hint)
        self._visit_children(node)
        return True

    # -- Visitor dispatch --

    def _visit(self, node: ASTNode | None) -> None:
        if node is None:
            return
        method = getattr(self, f"_visit_{type(node).__name__}", None)
        if method is not None:
            method(node)
            return
        self._visit_children(node)

    def _visit_children_const_ok(self, node: ASTNode) -> None:
        """Visit children with constant-namespace member reads allowed.

        Used for the argument subtrees of parse-and-skip visual calls,
        the strategy() declaration, and request.security — the contexts
        where ``plot.style_*`` / ``text.align_*`` / ``barmerge.*`` /
        ``alert.freq_*`` constants are legitimate.
        """
        self._const_arg_ctx_depth += 1
        try:
            self._visit_children(node)
        finally:
            self._const_arg_ctx_depth -= 1

    def _visit_children(self, node: ASTNode) -> None:
        for value in vars(node).values():
            if isinstance(value, ASTNode):
                self._visit(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, ASTNode):
                        self._visit(item)
            elif isinstance(value, dict):
                for item in value.values():
                    if isinstance(item, ASTNode):
                        self._visit(item)

    # -- Specific visitors --

    @staticmethod
    def _param_is_provided(node: FuncCall, namespace: str | None, func_name: str, param_name: str) -> bool:
        if param_name in node.kwargs:
            return True
        param_names = sigs.get_param_names(namespace, func_name) or []
        try:
            idx = param_names.index(param_name)
        except ValueError:
            return False
        return idx < len(node.args)

    @staticmethod
    def _is_color_constant_or_builder(expr: ASTNode) -> bool:
        """True when ``expr`` is a Pine color literal/builder safe to feed
        into the codegen's packed-int input route.

        Accepts:
          * ``color.<const>``  (e.g. ``color.red``)
          * ``color.new(...)`` / ``color.rgb(...)`` builder calls
          * ``ColorLiteral``   (#rrggbb syntax)
        """
        if isinstance(expr, ColorLiteral):
            return True
        if isinstance(expr, MemberAccess) and isinstance(expr.object, Identifier):
            if expr.object.name == "color":
                return True
        if isinstance(expr, FuncCall):
            ns, name = _qualified_name(expr.callee)
            if ns == "color" and name in ("new", "rgb"):
                return True
        return False

    def _check_input_color_defval(self, node: FuncCall) -> None:
        """input.color: reject defvals that aren't a color constant/builder."""
        defval: ASTNode | None = None
        if node.args:
            defval = node.args[0]
        elif "defval" in node.kwargs:
            defval = node.kwargs["defval"]
        if defval is None:
            self._err(
                node,
                "input.color(...) requires a color defval "
                "(e.g. color.red or color.new(color.red, 50)).",
            )
            return
        if not self._is_color_constant_or_builder(defval):
            self._err(
                node,
                "input.color(...) defval must be a color constant "
                "(color.red, ...), a color literal (#rrggbb), or a "
                "color.new(...) / color.rgb(...) builder. Arbitrary "
                "expressions are not supported: the engine has no color "
                "helper, so codegen routes input.color through the int "
                "getter — any non-color defval would silently store a "
                "numeric value with no color encoding.",
                hint="Replace the defval with color.<name> or color.new(...).",
            )

    def _check_input_source_defval(self, node: FuncCall) -> None:
        """input.source: reject defvals that aren't a native chart series.

        PineForge restricts input.source strictly to native OHLCV series
        (open/high/low/close/volume/hl2/hlc3/ohlc4/hlcc4) — the engine's
        runtime override (get_input_source) can only resolve to those base
        series. User series, computed expressions, and indicator outputs
        have no resolvable backing series; without this guard codegen would
        bind to the close fallback, silently using the wrong series."""
        defval: ASTNode | None = None
        if node.args:
            defval = node.args[0]
        elif "defval" in node.kwargs:
            defval = node.kwargs["defval"]
        if defval is None:
            self._err(
                node,
                "input.source(...) requires a native series defval "
                "(close, open, high, low, hl2, hlc3, ohlc4, ...).",
            )
            return
        if not (isinstance(defval, Identifier)
                and defval.name in INPUT_SOURCE_SERIES_IDS):
            self._err(
                node,
                "input.source(...) defval must be a native chart series "
                "(open, high, low, close, volume, hl2, hlc3, ohlc4, hlcc4). "
                "User series, computed expressions, and indicator outputs "
                "are not supported: input.source is restricted to native "
                "OHLCV series.",
                hint="Use one of: close, open, high, low, hl2, hlc3, ohlc4.",
            )

    def _visit_StrategyDecl(self, node: StrategyDecl) -> None:
        kind = (node.annotations or {}).get("decl_kind", "strategy")
        if kind != "strategy":
            self._err(
                node,
                f"{kind}() declarations are not supported; PineForge runs strategies only.",
                hint="Replace the declaration with strategy(...) and add explicit entry/exit calls.",
            )
        # strategy(...) kwargs legitimately carry constant-namespace members
        # (e.g. scale=scale.right, format=format.price).
        self._visit_children_const_ok(node)

    def _visit_VarDecl(self, node: VarDecl) -> None:
        if node.is_varip:
            self._err(
                node,
                "varip is not supported in PineForge batch backtests — there "
                "are no intrabar ticks. Codegen would silently demote varip "
                "to var, producing incorrect state accumulation for any "
                "script that relies on tick-level updates.",
                hint=(
                    "Replace 'varip' with 'var' if the strategy logic does "
                    "not depend on tick-level updates, or run the strategy "
                    "in hosted TradingView Studio."
                ),
            )
        self._visit_children(node)

    def _visit_TupleAssign(self, node: TupleAssign) -> None:
        if isinstance(node.value, FuncCall):
            ns, name = _qualified_name(node.value.callee)
            if ns == "ta" and name == "stoch":
                self._err(
                    node.value,
                    "ta.stoch(...) returns a single series in PineForge; tuple destructuring is not supported.",
                    hint="Use k = ta.stoch(...) and compute smoothing separately if needed.",
                )
        self._visit_children(node)

    def _visit_FuncCall(self, node: FuncCall) -> None:
        ns, name = _qualified_name(node.callee)

        if ns is None and name is None:
            self._visit_children(node)
            return

        full = f"{ns}.{name}" if ns else name

        # Hard rejects by full name.
        if full in HARD_REJECT_FUNC:
            self._err(node, f"{full}(...) is not supported.", hint=HARD_REJECT_FUNC[full])
            self._visit_children(node)
            return

        # Hard rejects by namespace (e.g. ticker.*).
        if ns is not None and ns in HARD_REJECT_NAMESPACE:
            self._err(
                node,
                f"{full}(...) is not supported.",
                hint=HARD_REJECT_NAMESPACE[ns],
            )
            self._visit_children(node)
            return

        # Not-yet-implemented. Check qualified name (e.g. "timeframe.from_seconds")
        # first, then bare name (e.g. "max_bars_back") for back-compat entries.
        if full in NOT_YET_FUNC:
            self._err(node, f"{full}(...) is not implemented yet.", hint=NOT_YET_FUNC[full])
            self._visit_children(node)
            return
        if ns is not None and name in NOT_YET_FUNC:
            self._err(node, f"{name}(...) is not implemented yet.", hint=NOT_YET_FUNC[name])
            self._visit_children(node)
            return

        # Bare-function rejections (e.g. `color(arg)` cast). Codegen would
        # otherwise fall through to the generic emit at visit_call.py:912 and
        # produce an undeclared C++ symbol.
        if ns is None and self._reject_if_in(
            UNSUPPORTED_BARE_FUNCS,
            name,
            node,
            lambda k, v: f"{k}(...): {v}",
            hint="See https://www.tradingview.com/pine-script-docs/concepts/colors/ for the supported color builders.",
        ):
            return

        # Whole-namespace rejections (e.g. footprint.*, volume_row.*).
        if ns is not None and self._reject_if_in(
            UNSUPPORTED_NAMESPACES,
            ns,
            node,
            lambda k, v: f"{full}: {v}",
            hint="Remove the call or replace it with native OHLCV-based logic.",
        ):
            return

        # Bare barssince() — codegen emits 0 (broken).
        if ns is None and name == "library":
            self._err(
                node,
                "library() declarations are not supported; PineForge runs strategies only.",
                hint="Use strategy(...) and inline or pre-expand library code before uploading.",
            )
            self._visit_children(node)
            return

        # Bare barssince() — codegen emits 0 (broken).
        if ns is None and name == "barssince":
            self._err(
                node,
                "Bare barssince(...) is broken in PineForge codegen.",
                hint="Use ta.barssince(...) instead.",
            )
            self._visit_children(node)
            return

        # ta.sum — not a real Pine identifier.
        if ns == "ta" and name == "sum":
            self._err(
                node,
                "ta.sum is not a PineScript v6 function.",
                hint="Use math.sum(...) instead.",
            )
            self._visit_children(node)
            return

        if ns == "ta" and name in TA_PROPERTY_VARIABLES:
            self._err(
                node,
                f"ta.{name} is a PineScript v6 variable, not a function.",
                hint=f"Use ta.{name} without parentheses.",
            )
            self._visit_children(node)
            return

        if ns == "strategy.closedtrades":
            if name not in CLOSED_TRADE_ACCESSOR_METHODS:
                self._err(node, f"{full}(...) is not implemented in PineForge runtime.")
                self._visit_children(node)
                return
        if ns == "strategy.opentrades":
            if name not in OPEN_TRADE_ACCESSOR_METHODS:
                hint = None
                if name in CLOSED_TRADE_ACCESSOR_METHODS:
                    hint = (
                        f"strategy.opentrades has no '{name}' accessor in Pine v6 "
                        f"(it only exists on strategy.closedtrades)."
                    )
                self._err(node, f"{full}(...) is not implemented in PineForge runtime.", hint=hint)
                self._visit_children(node)
                return

        if ns == "strategy" and name not in sigs.STRATEGY_FUNCTIONS:
            self._err(node, f"strategy.{name}(...) is not implemented in PineForge runtime.")
            self._visit_children(node)
            return

        if ns == "strategy" and name == "exit":
            has_price_param = any(
                self._param_is_provided(node, "strategy", "exit", param)
                for param in STRATEGY_EXIT_PRICE_PARAMS
            )
            if not has_price_param:
                self._err(
                    node,
                    "strategy.exit(...) requires at least one price or trailing parameter in PineForge.",
                    hint="Use strategy.close(...) for market exits.",
                )

        # request.security strictness. Children are visited with constant-
        # namespace reads allowed: barmerge.* gaps/lookahead values are
        # validated above by _check_request_security and consumed by codegen.
        if full == "request.security":
            self._check_request_security(node)
            self._visit_children_const_ok(node)
            return
        # request.security_lower_tf — analyzer/codegen handle parameter validation
        # and element-type rejection (UDT/color/string). Still validate the
        # timeframe literal here so codegen catches malformed TF strings early.
        if full == "request.security_lower_tf":
            self._check_request_security_lower_tf_tf(node)
            self._visit_children_const_ok(node)
            return
        if ns == "request":
            self._err(
                node,
                f"{full}(...) is not supported. Only request.security(...) and request.security_lower_tf(...) are available in PineForge.",
                hint="External request feeds and other request.* variants are intentionally unavailable.",
            )
            self._visit_children(node)
            return

        # strategy.risk.* is partially supported by codegen/runtime for common
        # risk limits. Warn because TradingView risk semantics are broad and
        # not every edge case is modeled exactly.
        if ns == "strategy.risk":
            self._warn(
                node,
                f"strategy.risk.{name}(...) has partial PineForge runtime support.",
                hint="Verify risk behavior against PineForge results; unsupported risk edge cases may diverge from TradingView.",
            )
            self._visit_children(node)
            return

        if ns == "strategy" and name in STRATEGY_UNSUPPORTED_PARAMS:
            for param_name in sorted(STRATEGY_UNSUPPORTED_PARAMS[name]):
                if self._param_is_provided(node, "strategy", name, param_name):
                    warn_node = node.kwargs.get(param_name, node)
                    self._warn(
                        warn_node,
                        f"strategy.{name} parameter '{param_name}' is not supported by PineForge and is ignored.",
                    )

        # Unknown ta.* / math.* / str.* / input.* - codegen emits silent stubs.
        if ns == "ta" and name not in SUPPORTED_TA:
            self._err(node, f"ta.{name}(...) is not implemented in PineForge runtime.")
            self._visit_children(node)
            return
        if ns == "math" and name not in SUPPORTED_MATH:
            self._err(node, f"math.{name}(...) is not implemented in PineForge runtime.")
            self._visit_children(node)
            return
        if ns == "str" and name not in SUPPORTED_STR:
            self._err(node, f"str.{name}(...) is not implemented in PineForge runtime.")
            self._visit_children(node)
            return
        if ns == "input" and name not in SUPPORTED_INPUT:
            self._err(node, f"input.{name}(...) is not implemented in PineForge runtime.")
            self._visit_children(node)
            return
        if ns == "input" and name == "color":
            # The engine has no get_input_color helper; codegen routes
            # input.color through get_input_int with the defval emitted as
            # a packed RGBA int. That is only meaningful when the defval
            # itself is a color literal or builder. An arbitrary int/identifier
            # would silently bind a numeric value with no color encoding,
            # producing wrong-colored UI (or worse, ambiguous state if the
            # value is ever passed back to a color-aware sink).
            self._check_input_color_defval(node)
        if ns == "input" and name == "source":
            # input.source is restricted to native OHLCV series — the engine
            # can only resolve a runtime override to a native base series.
            self._check_input_source_defval(node)
        if ns == "timeframe" and name not in SUPPORTED_TIMEFRAME_FUNC:
            self._err(node, f"timeframe.{name}(...) is not implemented in PineForge runtime.")
            self._visit_children(node)
            return
        if ns == "color" and name not in SUPPORTED_COLOR_FUNC:
            self._err(node, f"color.{name}(...) is not implemented in PineForge runtime.")
            self._visit_children(node)
            return
        if ns == "runtime" and name not in SUPPORTED_RUNTIME_FUNC:
            self._err(node, f"runtime.{name}(...) is not implemented in PineForge runtime.")
            self._visit_children(node)
            return
        if ns == "log" and name not in SUPPORTED_LOG:
            self._err(node, f"log.{name}(...) is not implemented in PineForge runtime.")
            self._visit_children(node)
            return
        if ns == "array" and name not in SUPPORTED_ARRAY:
            self._err(node, f"array.{name}(...) is not implemented in PineForge runtime.")
            self._visit_children(node)
            return
        if ns == "map" and name not in SUPPORTED_MAP:
            self._err(node, f"map.{name}(...) is not implemented in PineForge runtime.")
            self._visit_children(node)
            return
        if ns == "map" and name == "new":
            targs = (getattr(node.callee, "annotations", None) or {}).get("template_args") or []
            targs = [str(t).replace(" ", "") for t in targs]
            if targs and targs[0] != "string":
                self._err(node, "map keys must be string in PineForge's supported map subset.")
            if len(targs) > 1 and targs[1] not in {"float", "int", "bool", "string"}:
                self._err(node, "map values must be primitive in PineForge's supported map subset.")
        if ns == "matrix" and name == "new":
            targs = (getattr(node.callee, "annotations", None) or {}).get("template_args") or []
            targs = [str(t).replace(" ", "") for t in targs]
            if targs:
                t = targs[0]
                if "<" in t:
                    self._err(node, f"matrix<{t}>: nested collection element types not supported in v1.")
                    self._visit_children(node)
                    return
                allowed_prim = {"float", "int", "bool", "string", "color"}
                if t not in allowed_prim and t not in self._user_types:
                    self._err(node, f"matrix<{t}> element type not supported. Allowed: float, int, bool, string, color, or a declared UDT.")
                    self._visit_children(node)
                    return
        if ns == "matrix" and name not in SUPPORTED_MATRIX:
            self._err(node, f"matrix.{name}(...) is not implemented in PineForge runtime.")
            self._visit_children(node)
            return

        # Drawing / charting / alert namespaces — codegen drops silently. Warn,
        # don't error: many strategies include these for the TradingView UI.
        # Their argument subtrees legitimately carry constant-namespace
        # members (plot.style_*, text.align_*, alert.freq_*, ...), so visit
        # children with those reads allowed.
        if ns is None and name in SKIP_FUNC_NAMES:
            self._warn(
                node,
                f"{name}(...) has no effect in PineForge backtests (visual only).",
            )
            self._visit_children_const_ok(node)
            return
        if ns is not None and ns in SKIP_NAMESPACES:
            self._warn(
                node,
                f"{full}(...) has no effect in PineForge backtests (visual only).",
            )
            self._visit_children_const_ok(node)
            return

        self._visit_children(node)

    def _visit_Identifier(self, node: Identifier) -> None:
        # ``export`` is a Pine v6 library keyword the lexer treats as a plain
        # identifier. Library scripts already reject at library(); a stray
        # ``export`` in a strategy script would otherwise only die at the
        # codegen unknown-read guard with a generic message.
        if node.name == "export":
            self._err(
                node,
                "'export' is a Pine library keyword; PineForge runs "
                "strategies only and does not support exported "
                "functions/types.",
                hint="Remove the 'export' keyword (and inline any library "
                     "code into the strategy script).",
            )
            return
        if node.name in DIVERGENT_VARS:
            self._warn(
                node,
                f"{node.name} diverges from TradingView semantics in PineForge.",
                hint=DIVERGENT_VARS[node.name],
            )

    def _visit_IfStmt(self, node: IfStmt) -> None:
        """Visit if-statement; mark the condition as conditional context."""
        self._in_conditional_depth += 1
        self._visit(node.condition)
        self._in_conditional_depth -= 1
        for stmt in node.body:
            self._visit(stmt)
        for stmt in node.else_body:
            self._visit(stmt)

    def _visit_Ternary(self, node: Ternary) -> None:
        """Visit ternary; mark the condition expression as conditional context."""
        self._in_conditional_depth += 1
        self._visit(node.condition)
        self._in_conditional_depth -= 1
        self._visit(node.true_val)
        self._visit(node.false_val)

    def _visit_MemberAccess(self, node: MemberAccess) -> None:
        chain = _resolve_member_chain(node)
        if chain is not None and chain in DIVERGENT_VARS:
            self._warn(
                node,
                f"{chain} diverges from TradingView semantics in PineForge.",
                hint=DIVERGENT_VARS[chain],
            )
        if chain is not None and chain in BARSTATE_APPROX_VARS:
            self._warn(
                node,
                f"{chain} is approximated in PineForge.",
                hint=BARSTATE_APPROX_VARS[chain],
            )
        # Whole-namespace rejections via member access (e.g. footprint.SomeType).
        if isinstance(node.object, Identifier) and self._reject_if_in(
            UNSUPPORTED_NAMESPACES,
            node.object.name,
            node,
            lambda k, v: f"{k}.{node.member}: {v}",
        ):
            return
        # Specific unsupported (namespace, member) pairs (e.g. chart.left_visible_bar_time).
        if isinstance(node.object, Identifier) and self._reject_if_in(
            UNSUPPORTED_MEMBERS,
            (node.object.name, node.member),
            node,
            lambda k, v: f"{k[0]}.{k[1]}: {v}",
        ):
            return
        # Namespace-wide variable rejections (e.g. dividends.*, earnings.*).
        if isinstance(node.object, Identifier) and self._reject_if_in(
            UNSUPPORTED_NAMESPACE_VARS,
            node.object.name,
            node,
            lambda k, v: f"{k}.{node.member}: {v}",
        ):
            return
        # Constant-only namespace members (plot.style_*, text.align_*,
        # barmerge.*, alert.freq_*, ...) used as FREE EXPRESSIONS. Inside
        # parse-and-skip visual call arguments / strategy() / request.security
        # the read is legitimate and ``_const_arg_ctx_depth`` suppresses this.
        if (
            self._const_arg_ctx_depth == 0
            and isinstance(node.object, Identifier)
            and self._reject_if_in(
                UNSUPPORTED_CONST_NAMESPACES,
                node.object.name,
                node,
                lambda k, v: f"{k}.{node.member} {v}",
                hint=(
                    "Constant-namespace members cannot flow into strategy "
                    "logic; codegen would emit a string literal while the "
                    "analyzer types the read as int."
                ),
            )
        ):
            return
        if isinstance(node.object, Identifier) and node.object.name == "syminfo":
            if node.member not in SUPPORTED_SYMINFO:
                self._err(node, f"syminfo.{node.member} is not implemented in PineForge runtime.")
            elif (
                self._in_conditional_depth > 0
                and node.member in self._SYMINFO_SILENT_GAP_FIELDS
            ):
                self._warn(
                    node,
                    f"syminfo.{node.member} returns na in current PineForge; "
                    "condition will always be false. "
                    "Will be backfilled by pineforge-data product.",
                )
        self._visit_children(node)

    # -- request.security parameter rules --

    def _check_request_security(self, node: FuncCall) -> None:
        """Validate request.security: symbol/timeframe/expression/gaps/lookahead.

        Rejects ignore_invalid_symbol/currency (codegen drops silently).
        Symbol must reference current chart symbol.
        gaps/lookahead must be barmerge.{gaps,lookahead}_{on,off} member access
        (codegen only recognizes that shape; anything else silently dropped).
        """
        allowed_hint = (
            "Only symbol, timeframe, expression, gaps, and lookahead are accepted."
        )

        # Disallowed kwargs.
        for kw_name in node.kwargs:
            if kw_name not in SECURITY_ALLOWED_PARAMS:
                self._err(
                    node,
                    f"request.security parameter '{kw_name}' is not allowed in PineForge.",
                    hint=allowed_hint,
                )

        # Disallowed positional args (6th onward = ignore_invalid_symbol/currency).
        if len(node.args) > SECURITY_MAX_POSITIONAL:
            for extra in node.args[SECURITY_MAX_POSITIONAL:]:
                self._err(
                    extra,
                    "Extra positional arguments to request.security are not allowed in PineForge.",
                    hint=allowed_hint,
                )

        # Symbol-must-be-current check.
        symbol_node = None
        if node.args:
            symbol_node = node.args[0]
        elif "symbol" in node.kwargs:
            symbol_node = node.kwargs["symbol"]

        if symbol_node is not None and not self._is_current_symbol_expr(symbol_node):
            self._err(
                symbol_node,
                "request.security symbol must reference the current chart symbol.",
                hint="Use syminfo.tickerid or syminfo.ticker; PineForge backtests do not load alternate symbols.",
            )

        # timeframe literal-format check (positional [1] or kwarg).
        tf_node = node.kwargs.get("timeframe")
        if tf_node is None and len(node.args) > 1:
            tf_node = node.args[1]
        self._check_tf_literal(tf_node, "request.security")

        # gaps / lookahead value-shape check (positional or kwarg).
        gaps_node = node.kwargs.get("gaps")
        if gaps_node is None and len(node.args) > 3:
            gaps_node = node.args[3]
        if gaps_node is not None and not self._is_barmerge_member(gaps_node, "gaps_on", "gaps_off"):
            self._err(
                gaps_node,
                "request.security gaps must be barmerge.gaps_on or barmerge.gaps_off.",
                hint="Codegen only recognizes the barmerge.gaps_* literal; other values are silently treated as gaps_off.",
            )

        lookahead_node = node.kwargs.get("lookahead")
        if lookahead_node is None and len(node.args) > 4:
            lookahead_node = node.args[4]
        if lookahead_node is not None and not self._is_barmerge_member(
            lookahead_node, "lookahead_on", "lookahead_off"
        ):
            self._err(
                lookahead_node,
                "request.security lookahead must be barmerge.lookahead_on or barmerge.lookahead_off.",
                hint="Codegen only recognizes the barmerge.lookahead_* literal; other values are silently treated as lookahead_off.",
            )
        if self._is_barmerge_member(lookahead_node, "lookahead_on"):
            self._err(
                lookahead_node,
                "request.security lookahead_on is not supported in PineForge paid parity mode.",
                hint="Use barmerge.lookahead_off. lookahead_on can expose future/partial HTF values and is highly data-sensitive.",
            )

        # Data-adjustment kwargs: codegen emits a numeric constant but the
        # engine ignores it entirely. Only the no-op values are honored
        # implicitly; reject anything else loudly to surface the silent-
        # wrong-result bug. See SECURITY_ADJUSTMENT_ALLOWED_VALUES.
        self._check_security_adjustment_kwargs(node)

    def _check_security_adjustment_kwargs(self, node: FuncCall) -> None:
        """Reject request.security adjustment kwargs that the engine drops."""
        for kw_name, allowed in SECURITY_ADJUSTMENT_ALLOWED_VALUES.items():
            if kw_name not in node.kwargs:
                continue
            val = node.kwargs[kw_name]
            # Expected shape: ``<kw_name>.<member>`` MemberAccess.
            if isinstance(val, MemberAccess) and isinstance(val.object, Identifier):
                if val.object.name != kw_name or val.member not in allowed:
                    self._err(
                        val,
                        f"request.security: {kw_name}={val.object.name}.{val.member} "
                        f"is not supported. The engine ignores this kwarg, which "
                        f"would silently produce different prices from TradingView.",
                        hint=f"Allowed values: {sorted(allowed)}.",
                    )
            else:
                self._err(
                    val,
                    f"request.security: {kw_name}=... must be a constant member "
                    f"access of the form {kw_name}.<value> "
                    f"(got a non-constant expression).",
                    hint=f"Allowed values: {sorted(allowed)}.",
                )

    def _is_barmerge_member(self, node: ASTNode, *allowed: str) -> bool:
        if not isinstance(node, MemberAccess):
            return False
        if not isinstance(node.object, Identifier) or node.object.name != "barmerge":
            return False
        return node.member in allowed

    def _is_current_symbol_expr(self, node: ASTNode) -> bool:
        chain = _resolve_member_chain(node)
        if chain in SECURITY_CURRENT_SYMBOL_NAMES:
            return True
        # ticker.inherit(symbol, ...) and ticker.standard(symbol) are passthrough;
        # if their first argument is a current-symbol expression, allow it.
        if isinstance(node, FuncCall):
            ns, fname = _qualified_name(node.callee)
            if ns == "ticker" and fname in ("inherit", "standard"):
                if node.args and self._is_current_symbol_expr(node.args[0]):
                    return True
                if "symbol" in node.kwargs and self._is_current_symbol_expr(node.kwargs["symbol"]):
                    return True
        return False

    # -- Pine timeframe-literal validation --

    @staticmethod
    def _validate_pine_tf_literal(tf_str: str) -> str | None:
        """Return None if ``tf_str`` is a well-formed Pine TF literal, else
        a short reason string. Accepts:
          - bare positive integer minutes: "1", "5", "60", "240", "1440"
          - seconds suffix: "1S", "30S"
          - hours / days / weeks / months suffix: "1H", "1D", "1W", "3M"
        Rejects empty, zero, negative, decimals, unknown suffixes, bare suffix.
        """
        if tf_str == "":
            return "empty timeframe literal"
        # Must be all-ASCII; split optional trailing single letter suffix.
        suffix = ""
        digits = tf_str
        if tf_str[-1].isalpha():
            suffix = tf_str[-1]
            digits = tf_str[:-1]
            if suffix not in ("S", "H", "D", "W", "M"):
                return f"unknown timeframe suffix '{suffix}'"
        if digits == "":
            # Bare suffix shorthand — Pine treats "D"/"W"/"M"/"S" as 1<suffix>.
            # Accept the same shorthand here.
            return None
        if not digits.isdigit():
            # catches "1.5", "-15", "abc", etc.
            return f"non-integer timeframe magnitude '{digits}'"
        n = int(digits)
        if n <= 0:
            return "timeframe magnitude must be positive"
        return None

    def _check_tf_literal(self, tf_node: ASTNode | None, fn_label: str) -> None:
        """If ``tf_node`` is a string literal, validate its TF format.
        Variables / function calls / inputs are runtime-resolved and skipped.
        """
        if tf_node is None:
            return
        if not isinstance(tf_node, StringLiteral):
            return
        err = self._validate_pine_tf_literal(tf_node.value)
        if err is None:
            return
        self._err(
            tf_node,
            f"{fn_label}: invalid timeframe literal '{tf_node.value}'. "
            "Expected Pine TF format like '1', '15', '1H', '1D', '15S'.",
            hint=err,
        )

    def _check_request_security_lower_tf_tf(self, node: FuncCall) -> None:
        """Validate the ``timeframe`` argument literal for
        ``request.security_lower_tf``. All other parameter validation lives in
        the analyzer."""
        tf_node = node.kwargs.get("timeframe")
        if tf_node is None and len(node.args) > 1:
            tf_node = node.args[1]
        self._check_tf_literal(tf_node, "request.security_lower_tf")


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------

def check_support(ast: Program, filename: str = "<input>") -> list[Diagnostic]:
    return SupportChecker(ast, filename=filename).check()


def check_support_or_raise(ast: Program, filename: str = "<input>") -> None:
    SupportChecker(ast, filename=filename).check_or_raise()
