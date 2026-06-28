"""Expression-level visitors for the codegen.

``ExprVisitor`` holds the expression-level visitors. ``_visit_expr`` is
the central dispatcher that inspects the AST node kind and either emits
a literal directly (numbers, strings, booleans, ``na``, color literals)
or delegates to one of the kind-specific handlers:

* ``_visit_ident`` — identifier resolution (parameters, ``BAR_FIELDS`` /
  ``BAR_BUILTINS`` builtins, known constants, series-var current value
  reads, per-call-site var remapping).
* ``_visit_member_access`` — namespace member access for ``strategy`` /
  ``math`` / ``ta`` / ``timeframe`` / ``barstate`` / ``syminfo`` /
  ``display`` / ``color``, nested ``strategy.*.*`` (oca / direction /
  commission / closedtrades / opentrades), enum member access, and the
  fallback path for UDT-style member access on known variables.
* ``_visit_binop`` — binary operators (with the Pine ``and`` / ``or``
  -> ``&&`` / ``||`` rewrite and ``%`` -> ``std::fmod``).
* ``_visit_unaryop`` — unary ``not`` -> ``!`` plus pass-through of
  numeric unary operators.
* ``_visit_subscript`` — array/series ``[k]`` history access (function
  parameters, bar-field series, user series-vars, ``strategy.*[k]``
  history shadow series, generic fallback).

These visitors were extracted from ``base.py``'s ``CodeGen`` class as
step 9 of the codegen package refactor; behaviour is preserved
verbatim. The mixin owns no state of its own — it reads/writes only
attributes already established on the host class (``CodeGen``).

Mixin contract — host class must provide the following attributes
(all set by ``CodeGen.__init__`` or other mixins):

- ``self.ctx`` (``AnalyzerContext``): symbol table source. The
  visitors read ``ctx.symbols.resolve`` (``_visit_ident`` /
  ``_visit_member_access``) to disambiguate ``color`` and UDT
  identifiers, ``ctx.series_vars`` to decide between current-value
  reads and history reads, and ``ctx.ta_call_sites`` to find the
  no-arg TA-property call site (``ta.obv`` / ``ta.accdist`` / ...).
- ``self._known_vars`` (``dict[str, bool|int|float|str]``): inlined
  literal constants; consulted by ``_visit_ident``.
- ``self._input_backed_vars`` (``set[str]``): names backed by
  ``input.*`` calls — these are NOT inlined even when in
  ``_known_vars`` because ``strategy_set_input()`` can override them
  at runtime.
- ``self._var_names`` (``set[str]``): names declared at module scope.
  Consulted by ``_visit_ident`` (to decide whether ``color`` is a
  type name vs a variable), ``_visit_member_access`` (UDT fallback)
  and ``_visit_subscript`` (generic fallback).
- ``self._global_member_vars`` (``set[str]``): non-``var`` global
  declarations emitted as class members; ``_visit_ident`` uses it as
  part of the ``color``-as-type-name guard.
- ``self._current_func_locals`` (``set[str]``): function-local names
  emitted in the current function body (used by the ``color``
  identifier guard in ``_visit_ident``).
- ``self._current_func_param_types`` (``dict[str, ...]``): parameter
  name → type for the function currently being emitted; used by
  ``_visit_ident``, ``_visit_member_access`` and ``_visit_subscript``
  to treat parameters as plain locals (never series-member reads).
- ``self._current_func_series_params`` (``set[str]``): subset of the
  current function's parameters that are series-typed; treated as
  ``Series<T>`` so ``src`` becomes ``src[0]`` and ``src[k]`` stays
  ``src[k]``.
- ``self._current_loop_vars`` (``set[str]``): for-in iterator names;
  used by ``_visit_member_access`` to distinguish iterator member
  access from enum constants.
- ``self._active_var_remap`` (``dict[str, str]``): per-call-site
  rename map for cloned function-local var/series names; applied in
  ``_visit_ident`` / ``_visit_member_access`` / ``_visit_subscript``.
- ``self._enum_defs`` (``dict[str, dict[str, ...]]``): enum name →
  member map; ``_visit_member_access`` consults it before falling
  through to the generic ``Identifier.member`` path.
- ``self._strategy_series_vars`` (``set[str]``): collected by
  ``_visit_subscript`` whenever it encounters a
  ``strategy.<member>[k]`` history access; the emitter creates
  matching ``_strat_<member>`` series later.

Sibling-mixin methods consumed via ``self``:

- ``NamingHelper`` (``codegen/helpers.py``): ``_safe_name``.
- ``CodeGen.base``: ``_visit_func_call`` (still on the host class —
  the call-dispatch helpers and ``_visit_func_call`` itself are
  extracted in step 10 of the refactor).

The mixin avoids importing from ``base.py`` to stay free of cycles;
all tables it needs come from ``codegen/tables.py`` and all AST
classes from ``..ast_nodes``.
"""

from __future__ import annotations

from ..ast_nodes import (
    ASTNode,
    BinOp,
    BoolLiteral,
    ColorLiteral,
    FuncCall,
    Identifier,
    MemberAccess,
    NaLiteral,
    NumberLiteral,
    StringLiteral,
    Subscript,
    Ternary,
    TupleLiteral,
    UnaryOp,
)
from .tables import (
    ADJUSTMENT_MAP,
    BAR_BUILTINS,
    BAR_FIELDS,
    BAR_SERIES_PUSH,
    COLOR_CONST_MAP,
    DAYOFWEEK_MAP,
    DISPLAY_MAP,
    ON_OFF_INHERIT_MAP,
    ORDER_DIRECTION_MAP,
    SKIP_NAMESPACES,
    SYMINFO_MEMBER_MAP,
    TA_COMPUTE_ARGS,
    TA_IMPLICIT_COMPUTE_FULL,
)


def _color_literal_to_int64(value: str) -> str:
    """Lower a Pine hex color literal to a packed ARGB int64 C++ literal.

    Pine ``#RRGGBB`` is opaque; ``#RRGGBBAA`` carries an explicit alpha
    (opacity, FF = opaque). The engine packs colors as ``0xAARRGGBB``
    (see color.hpp: ``red == 0xFFFF0000``), so we reorder to alpha-first
    and emit an ``int64_t`` literal — matching ``get_input_int64`` and
    ``pine_color::*``. A malformed literal falls back to 0 (transparent).
    """
    h = value.lstrip("#").strip()
    if len(h) == 6:
        rr, gg, bb, aa = h[0:2], h[2:4], h[4:6], "ff"
    elif len(h) == 8:
        rr, gg, bb, aa = h[0:2], h[2:4], h[4:6], h[6:8]
    else:
        return "0"
    try:
        int(rr + gg + bb + aa, 16)  # validate hex
    except ValueError:
        return "0"
    return f"0x{aa}{rr}{gg}{bb}LL"


# Valid Pine v6 bare builtins that have no value in a batch backtest (no live
# feed). Reading them used to emit an undeclared C++ symbol; they now get a
# tailored hint via the unknown-identifier guard in ``_visit_ident``.
_REALTIME_ONLY_VARS: frozenset[str] = frozenset({"ask", "bid"})

# Builtin namespace prefixes (``format.mintick``, ``barmerge.gaps_on``, …).
# When a member-access handler has no dedicated branch it falls through to the
# generic tail, which visits the root identifier bare — so these must not be
# flagged as unknown variables. Mirrors the namespace list in
# ``analyzer/base.py::_visit_Identifier``.
_BUILTIN_NAMESPACE_NAMES: frozenset[str] = frozenset({
    "strategy", "ta", "input", "math", "str", "color", "display", "syminfo",
    "timeframe", "plot", "alert", "barstate", "position", "shape", "location",
    "size", "currency", "order", "format", "text", "extend", "xloc", "yloc",
    "label", "line", "box", "table", "ticker", "request", "runtime", "chart",
    "barmerge", "adjustment", "earnings", "dividends", "splits", "session",
    "scale", "font", "hline", "backadjustment", "settlement_as_close",
    "dayofweek", "array", "matrix", "map", "polyline", "linefill",
})


class ExprVisitor:
    """Expression-level visitor methods shared across the codegen.

    Mixed into ``CodeGen``; not intended to be instantiated standalone.
    See the module docstring for the full host-class state contract."""

    # ------------------------------------------------------------------
    # Expression visitors
    # ------------------------------------------------------------------

    def _visit_expr(self, node: ASTNode | None) -> str:
        if node is None:
            return "/* null */"
        if isinstance(node, NumberLiteral):
            return str(node.value)
        if isinstance(node, StringLiteral):
            return f'std::string("{self._cpp_string_escape(node.value)}")'
        if isinstance(node, BoolLiteral):
            return "true" if node.value else "false"
        if isinstance(node, NaLiteral):
            return "na<double>()"
        if isinstance(node, ColorLiteral):
            return _color_literal_to_int64(node.value)
        if isinstance(node, Identifier):
            return self._visit_ident(node)
        if isinstance(node, MemberAccess):
            return self._visit_member_access(node)
        if isinstance(node, BinOp):
            return self._visit_binop(node)
        if isinstance(node, UnaryOp):
            return self._visit_unaryop(node)
        if isinstance(node, Ternary):
            c = self._visit_expr(node.condition)
            t = self._visit_expr(node.true_val)
            f = self._visit_expr(node.false_val)
            return f"(({c}) ? ({t}) : ({f}))"
        if isinstance(node, FuncCall):
            return self._visit_func_call(node)
        if isinstance(node, Subscript):
            return self._visit_subscript(node)
        if isinstance(node, TupleLiteral):
            elems = ", ".join(self._visit_expr(e) for e in node.elements)
            return f"std::make_tuple({elems})"
        return "/* unknown */"

    def _visit_ident(self, node: Identifier) -> str:
        name = node.name
        # Bare 'na' identifier → na<double>()
        if name == "na":
            return "na<double>()"
        # Function parameters that are series — read current value
        if name in self._current_func_series_params:
            return f"{self._safe_name(name)}[0]"
        # Function parameters are plain locals, never series members
        if name in self._current_func_param_types:
            return self._safe_name(name)
        if name in BAR_FIELDS:
            return BAR_FIELDS[name]
        if name in BAR_BUILTINS:
            return BAR_BUILTINS[name]
        # Inline known constants (literals). Input-backed names are NOT inlined
        # because strategy_set_input() can override them at runtime; emit the
        # variable name and let get_input_*() produce the current value.
        if name in self._known_vars and name not in self._input_backed_vars:
            val = self._known_vars[name]
            if isinstance(val, bool):
                return "true" if val else "false"
            if isinstance(val, (int, float)):
                return str(val)
            if isinstance(val, str):
                return f'std::string("{val}")'
        # Pine type name `color` used as a value (no variable) → int64 color constant.
        # Params handled above; symbol table does not retain function locals after analysis.
        if name == "color":
            sym = self.ctx.symbols.resolve(node.name)
            if (
                sym is None
                and name not in self.ctx.series_vars
                and name not in self._var_names
                and name not in self._global_member_vars
                and name not in self._current_func_locals
            ):
                return "(int64_t)pine_color::black"
        # Series var — read current value
        safe = self._safe_name(name)
        # Apply per-call-site var remap (for function-local vars)
        if self._active_var_remap and safe in self._active_var_remap:
            safe = self._active_var_remap[safe]
        if name in self.ctx.series_vars:
            return f"{safe}[0]"
        # Safety net: by here the name resolved to none of the builtins,
        # constants, parameters, or declared variables handled above, so
        # ``return safe`` would emit a bare identifier that is an *undeclared
        # C++ symbol* (e.g. ``x = ask;``, ``x = undefined_var;``). That is a
        # silent miscompile — g++ fails against generated C++ with no Pine
        # line. Reject loudly. (Like the call-site guard in visit_call.py,
        # this branch only fires on input that already failed to compile, so
        # the all-green corpus never exercises it.)
        if not self._ident_is_resolvable(name):
            hint = None
            if name in _REALTIME_ONLY_VARS:
                hint = (f"'{name}' is a realtime-only Pine v6 builtin with no "
                        f"value in a batch backtest.")
            self._codegen_error(
                node,
                f"Unknown variable '{name}' — not a PineForge builtin or a "
                f"declared variable.",
                hint=hint,
            )
        return safe

    def _ident_is_resolvable(self, name: str) -> bool:
        """True when a bare identifier maps to a builtin, constant, parameter,
        or any declared variable/type/enum/function — i.e. emitting it will not
        produce an undeclared C++ symbol. Deliberately generous: a missing
        source here only means a genuinely-unknown name slips through (the
        pre-existing behavior), whereas a wrong ``True`` is safe. Only a
        name in NONE of these is rejected."""
        if name == "na" or name == "color":
            return True
        if name in BAR_FIELDS or name in BAR_BUILTINS:
            return True
        if name in _BUILTIN_NAMESPACE_NAMES:
            return True
        if name in self._known_vars or name in self._input_backed_vars:
            return True
        if name in self._var_names or name in self._global_member_vars:
            return True
        if name in self._current_func_locals or name in self._current_loop_vars:
            return True
        if name in self._current_func_param_types or name in self._current_func_series_params:
            return True
        if name in self.ctx.series_vars:
            return True
        if (name in self._array_vars or name in self._map_vars
                or name in self._matrix_specs or name in self._udt_var_types):
            return True
        if name in self._enum_defs or name in self._udt_defs or name in self._func_names:
            return True
        # Names bound anywhere in the program (block-scoped locals the per-scope
        # sets above never see — e.g. a var declared inside an on_bar for-loop).
        if name in self._all_bound_names:
            return True
        # Catch-all: the analyzer's symbol table knows every declared name it
        # retained (globals, inputs, …). Anything it resolves is legitimate.
        if self.ctx.symbols.resolve(name) is not None:
            return True
        return False

    def _visit_member_access(self, node: MemberAccess) -> str:
        # UDT field whose type was a drawing primitive (label/line/box/
        # linefill/polyline/table/chart.point) — the field is dropped from
        # the emitted struct, so a raw ``recv.field`` would fail to compile.
        # Emit a labelled zero placeholder so any read site stays well-formed.
        # See: pineforge-codegen issue #10.
        if self._is_omitted_udt_field(node):
            return "/* drawing field omitted */ 0"
        if isinstance(node.object, Identifier):
            ns = node.object.name
            if ns == "strategy":
                # Direction constants
                if node.member == "long":
                    return "true"
                if node.member == "short":
                    return "false"
                # Position info
                if node.member == "position_size":
                    return "signed_position_size()"
                if node.member == "position_avg_price":
                    return "position_entry_price_"
                if node.member == "position_entry_name":
                    return "position_entry_name()"
                # Trade counts
                if node.member == "opentrades":
                    return "((int)pyramid_entries_.size())"
                if node.member == "closedtrades":
                    return "((int)trades_.size())"
                if node.member == "wintrades":
                    return "count_wintrades()"
                if node.member == "losstrades":
                    return "count_losstrades()"
                if node.member == "eventrades":
                    return "eventrades()"
                # Equity and P&L
                if node.member == "equity":
                    return "(current_equity() + open_profit(current_bar_.close))"
                if node.member == "initial_capital":
                    return "initial_capital_"
                if node.member == "netprofit":
                    return "net_profit()"
                if node.member == "netprofit_percent":
                    return "((net_profit() / initial_capital_) * 100.0)"
                if node.member == "openprofit":
                    return "open_profit(current_bar_.close)"
                if node.member == "openprofit_percent":
                    # Pine: openPL / realizedEquity * 100. current_equity()
                    # is the engine's realized equity (initial capital +
                    # closed-trade net profit); guard the zero denominator
                    # like the engine's *_percent accessors do.
                    return ("((current_equity() != 0.0) ? "
                            "((open_profit(current_bar_.close) / current_equity()) * 100.0) : 0.0)")
                if node.member == "grossprofit":
                    return "gross_profit()"
                if node.member == "grossloss":
                    return "gross_loss()"
                if node.member == "grossprofit_percent":
                    return "grossprofit_percent()"
                if node.member == "grossloss_percent":
                    return "grossloss_percent()"
                if node.member == "max_contracts_held_all":
                    return "max_contracts_held_all()"
                if node.member == "max_contracts_held_long":
                    return "max_contracts_held_long()"
                if node.member == "max_contracts_held_short":
                    return "max_contracts_held_short()"
                if node.member == "max_drawdown":
                    return "max_drawdown_"
                if node.member == "max_runup":
                    return "max_runup_"
                if node.member == "max_drawdown_percent":
                    return "max_drawdown_percent()"
                if node.member == "max_runup_percent":
                    return "max_runup_percent()"
                if node.member == "avg_trade":
                    return "avg_trade()"
                if node.member == "avg_trade_percent":
                    return "avg_trade_percent()"
                if node.member == "avg_winning_trade":
                    return "avg_winning_trade()"
                if node.member == "avg_losing_trade":
                    return "avg_losing_trade()"
                if node.member == "avg_winning_trade_percent":
                    return "avg_winning_trade_percent()"
                if node.member == "avg_losing_trade_percent":
                    return "avg_losing_trade_percent()"
                if node.member == "margin_liquidation_price":
                    return "margin_liquidation_price()"
                # Qty type / commission constants
                if node.member == "fixed":
                    return "0"
                if node.member == "percent_of_equity":
                    return "1"
                if node.member == "cash":
                    return "2"
                if node.member == "account_currency":
                    return "syminfo_.currency"
                # Sub-namespaces (oca, direction, risk, commission) handled below
            if ns == "math":
                if node.member == "pi":
                    return "M_PI"
                if node.member == "e":
                    return "M_E"
                if node.member == "phi":
                    return "1.618033988749895"
                if node.member == "rphi":
                    return "0.6180339887498949"
                if node.member == "abs":
                    return "std::abs"
                if node.member == "max":
                    return "std::max"
                if node.member == "min":
                    return "std::min"
            if ns == "ta":
                if node.member == "tr":
                    return ("(std::isnan(_s_close[1]) ? "
                            "(current_bar_.high - current_bar_.low) : "
                            "std::max(current_bar_.high - current_bar_.low, "
                            "std::max(std::abs(current_bar_.high - _s_close[1]), "
                            "std::abs(current_bar_.low - _s_close[1]))))")
                # No-arg TA indicators used as properties (ta.obv, ta.accdist, etc.)
                if (
                    (node.member in TA_IMPLICIT_COMPUTE_FULL and node.member in TA_COMPUTE_ARGS and TA_COMPUTE_ARGS[node.member] == [])
                    or node.member == "vwap"
                ):
                    # Find the matching call site
                    for site in self.ctx.ta_call_sites:
                        ta_short = site.class_name.split("::")[-1].lower()
                        if site.member_name.startswith(f"_ta_{node.member}_"):
                            if node.member == "vwap":
                                return f"(is_first_tick_ ? {site.member_name}.compute(current_bar_.close, current_bar_.volume, current_bar_.timestamp) : {site.member_name}.recompute(current_bar_.close, current_bar_.volume, current_bar_.timestamp))"
                            return f"(is_first_tick_ ? {site.member_name}.compute({TA_IMPLICIT_COMPUTE_FULL[node.member]}) : {site.member_name}.recompute({TA_IMPLICIT_COMPUTE_FULL[node.member]}))"
                    # No registered call site for this TA property read —
                    # the old fallback emitted std::string("<name>"), a
                    # silent type mismatch. Reject loudly instead.
                    self._codegen_error(
                        node,
                        f"ta.{node.member} property read could not be bound "
                        f"to a TA call site.",
                        hint=f"Use the function form ta.{node.member}(...) "
                             f"instead of the bare property read.",
                    )
                # Any other bare ta.<member> property read (e.g. ``x = ta.rsi``
                # without parentheses) has no value to bind — Pine v6 only
                # defines property forms for tr/obv/accdist/nvi/pvi/pvt/wad/
                # wvad/iii/vwap. The old fallthrough emitted
                # ``std::string("<member>")``. Reject loudly.
                self._codegen_error(
                    node,
                    f"ta.{node.member} is not readable as a bare property in "
                    f"PineForge.",
                    hint=f"Call the function form ta.{node.member}(...) with "
                         f"its arguments instead.",
                )
            if ns == "chart":
                # PineForge batch engine always runs on standard OHLCV bars.
                if node.member == "is_standard":
                    return "true"
                # All non-standard chart types are false in batch mode.
                if node.member in ("is_heikinashi", "is_kagi", "is_linebreak",
                                   "is_pnf", "is_range", "is_renko"):
                    return "false"
                # Defensive: support_checker.UNSUPPORTED_MEMBERS should already have
                # rejected any unhandled chart.* member. Reaching here is a bug.
                raise ValueError(
                    f"codegen: unhandled chart.{node.member} — analyzer should have rejected. "
                    f"Add a handler above or extend UNSUPPORTED_MEMBERS."
                )
            if ns == "timeframe":
                if node.member == "period":
                    return 'script_tf_'
                if node.member == "main_period":
                    return 'main_period()'
                if node.member == "multiplier":
                    return 'tf_multiplier(script_tf_)'
                if node.member == "isintraday":
                    return 'tf_is_intraday(script_tf_)'
                if node.member == "isminutes":
                    return '(tf_is_intraday(script_tf_) && !tf_is_seconds(script_tf_))'
                if node.member == "isdaily":
                    return 'tf_is_daily(script_tf_)'
                if node.member == "isweekly":
                    return 'tf_is_weekly(script_tf_)'
                if node.member == "ismonthly":
                    return 'tf_is_monthly(script_tf_)'
                if node.member == "isdwm":
                    return '(tf_is_daily(script_tf_) || tf_is_weekly(script_tf_) || tf_is_monthly(script_tf_))'
                if node.member == "isseconds":
                    return 'tf_is_seconds(script_tf_)'
                if node.member == "in_seconds":
                    return 'tf_to_seconds(script_tf_)'
                if node.member == "isticks":
                    # Batch engine does not support tick-resolution data.
                    return "false"
                # Defensive: the if-chain above covers every Pine v6 timeframe.*
                # namespace variable. An unknown member is invalid Pine that
                # would otherwise emit a silent "0". Mirror the chart.* guard.
                raise ValueError(
                    f"codegen: unhandled timeframe.{node.member} — not a valid "
                    f"Pine v6 timeframe namespace variable. Add a handler above "
                    f"if this is a new builtin."
                )
            if ns == "barstate":
                if node.member == "isfirst":
                    return "(bar_index_ == 0)"
                if node.member == "islast":
                    return "barstate_islast_"
                if node.member == "isnew":
                    return "is_first_tick_"
                if node.member == "isconfirmed":
                    return "is_last_tick_"
                if node.member == "ishistory":
                    return "true"
                if node.member == "isrealtime":
                    return "false"
                if node.member == "islastconfirmedhistory":
                    return "barstate_islast_"
                return "false"
            if ns in ("backadjustment", "settlement_as_close"):
                # backadjustment.{on,off,inherit} / settlement_as_close.{on,off,inherit}
                # Emit as integer constants (accepted by engine, which ignores them).
                # Codegen silently drops these from request.security kwargs.
                # Unknown member falls back to "inherit" (2).
                return ON_OFF_INHERIT_MAP.get(node.member, "2")
            if ns == "adjustment":
                # adjustment.none / dividends / splits. Unknown member -> "none" (0).
                return ADJUSTMENT_MAP.get(node.member, "0")
            if ns == "dayofweek":
                return DAYOFWEEK_MAP.get(node.member, "0")
            if ns == "session":
                if node.member == "regular":
                    return 'std::string("regular")'
                if node.member == "extended":
                    return 'std::string("extended")'
                # session.is* predicates backed by engine session_ismarket_ etc.
                # session.isfirstbar_regular / islastbar_regular are aliased to
                # their non-_regular counterparts (engine has one session string;
                # see session_time.hpp limitation comment).
                if node.member == "ismarket":
                    return "pine_session_ismarket(syminfo_.session, syminfo_.timezone, current_bar_.timestamp)"
                if node.member == "ispremarket":
                    return "pine_session_ispremarket(syminfo_.session, syminfo_.timezone, current_bar_.timestamp)"
                if node.member == "ispostmarket":
                    return "pine_session_ispostmarket(syminfo_.session, syminfo_.timezone, current_bar_.timestamp)"
                if node.member in ("isfirstbar", "isfirstbar_regular"):
                    return "session_isfirstbar_"
                if node.member in ("islastbar", "islastbar_regular"):
                    return "session_islastbar_"
                return "false"
            if ns == "syminfo":
                _syminfo = SYMINFO_MEMBER_MAP.get(node.member)
                if _syminfo is not None:
                    return _syminfo
                # Defensive: support_checker rejects any syminfo.* member not in
                # SUPPORTED_SYMINFO (== frozenset(SYMINFO_MEMBER_MAP)). Reaching
                # here means the checker was bypassed or the two tables drifted.
                raise ValueError(
                    f"codegen: unhandled syminfo.{node.member} — analyzer should "
                    f"have rejected. Add it to SYMINFO_MEMBER_MAP."
                )
            if ns == "display":
                # plot_display (ints for C++; TV uses these in settings — backtest ignores)
                return DISPLAY_MAP.get(node.member, "0")
            if ns == "color":
                if node.member in COLOR_CONST_MAP:
                    return COLOR_CONST_MAP[node.member]
                return "0"
            if ns in SKIP_NAMESPACES:
                return "0"
            if ns == "currency":
                return f'std::string("{node.member}")'
            if ns == "order":
                # order.ascending / order.descending. Unknown member -> "ascending".
                return ORDER_DIRECTION_MAP.get(node.member, 'std::string("ascending")')

        # Handle nested member access: strategy.oca.reduce, strategy.closedtrades.profit(), etc.
        if isinstance(node.object, MemberAccess):
            if isinstance(node.object.object, Identifier):
                outer_ns = node.object.object.name
                if outer_ns == "strategy":
                    sub = node.object.member
                    # strategy.commission.percent, strategy.oca.*, strategy.direction.*
                    if sub == "oca":
                        if node.member == "cancel":
                            return "1"
                        if node.member == "reduce":
                            return "2"
                        if node.member == "none":
                            return "0"
                        return "0"
                    if sub == "direction":
                        if node.member == "long":
                            return "1"
                        if node.member == "short":
                            return "-1"
                        if node.member == "all":
                            return "0"
                        return "0"
                    if sub == "commission":
                        if node.member == "percent":
                            return "0"
                        if node.member == "cash_per_order":
                            return "1"
                        if node.member == "cash_per_contract":
                            return "2"
                        return "0"
                    # strategy.closedtrades.first_index, strategy.opentrades.capital_held
                    if sub == "closedtrades" and node.member == "first_index":
                        # Hardcoded 0 is correct until the engine implements
                        # trade-list capping (Pine only advances first_index
                        # when the 9000-trade cap drops old trades).
                        return "0"
                    if sub == "opentrades" and node.member == "capital_held":
                        return "open_trades_capital_held()"
                    return "0"

        # Enum member access: EnumName.Member -> named int constant (matches emitted const)
        if isinstance(node.object, Identifier):
            name = node.object.name
            if name in self._enum_defs:
                members = self._enum_defs[name]
                if node.member in members:
                    return f"{name}_{node.member}"

        # Unknown member access — emit as string constant (e.g., enum values)
        obj = self._visit_expr(node.object)
        if isinstance(node.object, Identifier):
            name = node.object.name
            # If the variable is known (defined in symbol table or as a var),
            # member access on a scalar C++ type is invalid. This happens when
            # PineScript UDTs (type declarations) are used — we don't support
            # them yet, so emit a safe default.
            sym = self.ctx.symbols.resolve(name)
            if (
                sym is not None
                or name in self._var_names
                or name in self._current_loop_vars
                or name in self._current_func_param_types
            ):
                safe = self._safe_name(name)
                if self._active_var_remap and safe in self._active_var_remap:
                    safe = self._active_var_remap[safe]
                return f"{safe}.{node.member}"
            if name not in self.ctx.series_vars:
                # Unknown identifier — likely an enum value
                return f'std::string("{node.member}")'
        return f"{obj}.{node.member}"

    def _visit_binop(self, node: BinOp) -> str:
        left = self._visit_expr(node.left)
        right = self._visit_expr(node.right)
        cpp_ops = {"and": "&&", "or": "||"}
        op = cpp_ops.get(node.op, node.op)
        # PineScript % works on floats — use std::fmod in C++
        if node.op == "%":
            return f"std::fmod((double)({left}), (double)({right}))"
        # Pine v6 always returns float for `/`, even on int/int operands
        # (breaking change from v5). C++ does int division on int operands,
        # so cast both sides to double to match Pine v6 semantics.
        # Ref: https://www.tradingview.com/pine-script-docs/concepts/operators/
        if node.op == "/":
            return f"((double)({left}) / (double)({right}))"
        return f"({left} {op} {right})"

    def _visit_unaryop(self, node: UnaryOp) -> str:
        operand = self._visit_expr(node.operand)
        if node.op == "not":
            return f"!({operand})"
        return f"({node.op}{operand})"

    def _visit_subscript(self, node: Subscript) -> str:
        idx = self._visit_expr(node.index)
        if isinstance(node.object, Identifier):
            name = node.object.name
            # Function parameters that are series — src[N] → src[N]
            if name in self._current_func_series_params:
                return f"{self._safe_name(name)}[{idx}]"
            # Function parameters are scalars — src[0] → src, src[N>0] → src
            if name in self._current_func_param_types:
                return self._safe_name(name)
            if name in BAR_FIELDS or name in BAR_SERIES_PUSH:
                # Index matches Pine: [0] current bar, [k] k bars ago (runtime Series deque).
                return f"_s_{name}[{idx}]"
            if name in self.ctx.series_vars:
                safe = self._safe_name(name)
                # Apply per-call-site var remap
                if self._active_var_remap and safe in self._active_var_remap:
                    safe = self._active_var_remap[safe]
                # Same Pine [k] semantics as Series in runtime/series.hpp
                return f"{safe}[{idx}]"
            spec = self._collection_types.get(name)
            if spec is not None and spec.kind in ("array", "map"):
                return f"{self._safe_name(name)}[{idx}]"
        # Handle strategy.* history access (e.g., strategy.position_size[1])
        if isinstance(node.object, MemberAccess):
            if isinstance(node.object.object, Identifier):
                ns = node.object.object.name
                if ns == "strategy":
                    # Strategy variables with history operator — use series tracking
                    member = node.object.member
                    series_name = f"_strat_{member}"
                    if series_name not in self._strategy_series_vars:
                        self._strategy_series_vars.add(series_name)
                    return f"{series_name}[{idx}]"
        obj = self._visit_expr(node.object)
        # If subscripting a non-series variable (e.g., function parameter),
        # src[0] → src (current value), src[N>0] → src (can't access history)
        if isinstance(node.object, Identifier):
            name = node.object.name
            if (name not in BAR_FIELDS and name not in BAR_SERIES_PUSH
                    and name not in self.ctx.series_vars
                    and name not in self._var_names):
                return obj
        return f"{obj}[{idx}]"
