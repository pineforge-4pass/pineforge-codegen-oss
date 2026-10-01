"""TA (technical analysis) call-site helpers for the codegen.

Holds the eval-free TA helpers: call-site lookup, ``.compute()`` arg
construction, and a small ``_is_compile_time_value`` predicate. The runtime-reset chain that
depends on Python's compile-time expression evaluator
(``_resolve_known`` / ``_runtime_ctor_arg_for_reset`` /
``_collect_ta_runtime_resets`` / ``_emit_ta_runtime_reset``) stays
on ``CodeGen`` in ``base.py`` for now — they sit at the bottom of
this file's docstring as a known follow-up.

Mixin contract — host class must provide:

- ``self._ta_site_map`` (``dict[int, TACallSite]``).
- ``self._active_ta_remap`` (``dict[str, str] | None``).
Sibling-mixin methods consumed via ``self``:

- ``self._visit_expr`` / ``self._visit_stmt`` (visitor mixins, currently
  on ``base.py``; will move to ``visit_expr`` / ``visit_stmt`` mixins).
- ``self._build_security_expr`` (security mixin, currently on ``base.py``).
- ``self._get_target_name`` (``NamingHelper``).
"""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING

from ..ast_nodes import (
    ASTNode, Assignment, BinOp, BoolLiteral, ColorLiteral, EnumDecl, ExprStmt,
    FuncCall, ForInStmt, ForStmt, FuncDef, Identifier, IfStmt, MemberAccess,
    MethodDef, NaLiteral, NumberLiteral, StringLiteral, Subscript, SwitchStmt,
    Ternary, TupleAssign, TupleLiteral, TypeDecl, TypeField, UnaryOp, VarDecl,
    WhileStmt,
)
from ..pine_spelling import blank_string_literals
from ..symbols import PineType
from .helpers import pine_index_int_cast
from .tables import (
    BAR_SERIES_PUSH,
    TA_CHART_PREV_CLOSE,
    TA_CHART_PREV_CLOSE_ARG,
    TA_IMPLICIT_APPEND,
    TA_IMPLICIT_COMPUTE_FULL,
)

if TYPE_CHECKING:
    from ..analyzer import TACallSite


class TaSiteHelper:
    """TA call-site lookups and ``.compute()`` argument construction."""

    # TA functions where an explicit source argument REPLACES the implicit
    # bar-data default (vs. ATR / supertrend / DMI where bar OHLC must
    # always be appended). Class-level so subclasses can override.
    _TA_IMPLICIT_REPLACE = {"pivothigh", "pivotlow"}

    # ------------------------------------------------------------------
    # Site / member lookup
    # ------------------------------------------------------------------

    def _get_ta_site(self, node) -> "TACallSite | None":
        """Look up the TA call-site bound to ``node`` (by ``id(node)``)."""
        if node is None:
            return None
        return self._ta_site_map.get(id(node))

    def _ta_member_name(self, site: "TACallSite") -> str:
        """Resolve the C++ member name for a TA site, applying any active per-site remap.

        Per-call-site function variants temporarily install a remap from
        the canonical ``_ta_<name>_<n>`` member to a variant member; this
        helper hides the lookup so call-sites stay readable."""
        name = site.member_name
        if self._active_ta_remap:
            return self._active_ta_remap.get(name, name)
        return name

    @staticmethod
    def _ta_name_from_site(site: "TACallSite") -> str:
        """Extract the TA function name (e.g. ``"rsi"`` or ``"vwap_bands"``) from a TACallSite.

        Member names follow the ``_ta_<name>_<n>`` convention where <name>
        may itself contain underscores (e.g. ``_ta_vwap_bands_1``). We split
        on ``_``, drop the leading empty string and ``"ta"`` prefix (parts 0
        and 1 after split), then drop the trailing numeric counter (last part)
        and rejoin the remaining components with ``_``."""
        parts = site.member_name.split("_")
        # parts = ['', 'ta', ...name_parts..., '<n>']
        if len(parts) >= 4:
            return "_".join(parts[2:-1])
        if len(parts) >= 3:
            return parts[2]
        # Defensive: TA member names are internally generated as `_ta_<name>_<n>`
        # (>= 3 parts after splitting on '_'). A shorter name is an internal
        # codegen invariant violation, not reachable from Pine source. Returning
        # "" here would flow an empty TA name into emission and produce invalid
        # C++; raise loudly instead.
        raise ValueError(
            f"codegen: malformed TA member name {site.member_name!r} — expected "
            f"'_ta_<name>_<n>' convention. Internal codegen bug."
        )

    # ------------------------------------------------------------------
    # .compute() arg-string construction
    # ------------------------------------------------------------------

    _TA_BOOL_COMPUTE_ARGS = {
        "barssince": frozenset({0}),
        "valuewhen": frozenset({0}),
        "pivot_point_levels": frozenset({1, 2}),
        "vwap_anchored": frozenset({1}),
        "vwap_anchored_bands": frozenset({1}),
    }

    def _ta_compute_arg_cpp(self, ta_name: str, position: int, node) -> str:
        """Render one TA compute argument with Pine bool truthiness."""
        rendered = self._visit_expr(node)
        if position in self._TA_BOOL_COMPUTE_ARGS.get(ta_name, ()):
            return self._coerce_bool_expr(rendered, node)
        return rendered

    def _ta_compute_args_for_site(self, site: "TACallSite") -> str:
        """``_ta_compute_args_for_site_base`` plus, for a site whose length the
        constructor cannot take, the per-call arguments of its lowering
        (``_ta_dynamic_compute_args``)."""
        args = self._ta_compute_args_for_site_base(site)
        # In a user-function body emitted for one call site, the member is
        # that call site's clone: its own constructor arguments decide.
        site = self._ta_active_site(site)
        plan = self._ta_dynamic_plan(site)
        if plan is None:
            return args
        return self._ta_dynamic_compute_args(site, plan, args, self._visit_expr)

    def _ta_active_site(self, site: "TACallSite") -> "TACallSite":
        """The site whose member ``_ta_member_name`` resolves ``site`` to."""
        remap = self._active_ta_remap
        if not remap:
            return site
        name = remap.get(site.member_name)
        if not name or name == site.member_name:
            return site
        return self._ta_site_by_member().get(name, site)

    def _ta_site_by_member(self) -> dict:
        by_member = self.__dict__.get("_ta_site_by_member_cache")
        if by_member is None:
            by_member = {s.member_name: s for s in self.ctx.ta_call_sites}
            self.__dict__["_ta_site_by_member_cache"] = by_member
        return by_member

    def _ta_compute_args_for_site_base(self, site: "TACallSite") -> str:
        """Build the C++ argument string for ``<member>.compute(...)`` of a TA site.

        Three layered cases:

        - TA in ``TA_IMPLICIT_COMPUTE_FULL`` (atr / supertrend / dmi /
          sar / pivothigh / pivotlow / wpr / volume indicators) gets
          bar OHLC threaded in implicitly; explicit args either prefix
          (most TA) or replace (pivothigh / pivotlow).
        - TA with explicit ``compute_args`` from the analyzer renders
          them and appends any implicit-suffix tokens (``vwma`` /
          ``kc`` / ``mfi`` / ``kcw`` / ``vwap``).
        - TA with no explicit args still gets implicit suffix tokens
          when applicable (e.g. ``vwma()`` -> ``volume`` only)."""
        ta_name = self._ta_name_from_site(site)

        # TA1's new stateful wrappers need the Pine anchor after the chart
        # clock arguments. The compatibility shims use the timestamp and
        # symbol clock only on an older engine; the TA1 branch ignores them.
        if site.class_name in ("_PFAnchoredVWAP", "_PFAnchoredVWAPBands"):
            explicit = [
                self._ta_compute_arg_cpp(ta_name, i, a)
                for i, a in enumerate(site.compute_args)
            ]
            if len(explicit) < 2:
                raise AssertionError("anchored VWAP site is missing source/anchor")
            return (
                f"{explicit[0]}, current_bar_.volume, current_bar_.timestamp, "
                f"syminfo_.timezone, syminfo_.session, {explicit[1]}"
            )

        if site.class_name == "_PFPivotPointLevels":
            explicit = [
                self._ta_compute_arg_cpp(ta_name, i, a)
                for i, a in enumerate(site.compute_args)
            ]
            if len(explicit) < 3:
                raise AssertionError("pivot TA1 site is missing anchor arguments")
            return (
                f"{explicit[0]}, {explicit[1]}, {explicit[2]}, "
                "current_bar_.open, current_bar_.high, current_bar_.low, "
                "current_bar_.close"
            )

        if ta_name in TA_IMPLICIT_COMPUTE_FULL:
            implicit = TA_IMPLICIT_COMPUTE_FULL[ta_name]
            # issue #178: chart-context atr / tr take the previous CHART
            # bar's close as a 4th argument (see TA_CHART_PREV_CLOSE).
            if ta_name in TA_CHART_PREV_CLOSE:
                implicit = f"{implicit}, {TA_CHART_PREV_CLOSE_ARG}"
            if site.compute_args:
                explicit = ", ".join(
                    self._ta_compute_arg_cpp(ta_name, i, a)
                    for i, a in enumerate(site.compute_args)
                )
                if ta_name in self._TA_IMPLICIT_REPLACE:
                    return explicit
                return f"{explicit}, {implicit}" if explicit else implicit
            return implicit

        if site.compute_args:
            explicit = ", ".join(
                self._ta_compute_arg_cpp(ta_name, i, a)
                for i, a in enumerate(site.compute_args)
            )
            if ta_name in TA_IMPLICIT_APPEND:
                return f"{explicit}, {TA_IMPLICIT_APPEND[ta_name]}"
            return explicit

        if ta_name in TA_IMPLICIT_APPEND:
            return TA_IMPLICIT_APPEND[ta_name]

        return ""

    # ------------------------------------------------------------------
    # Precalculation safety
    # ------------------------------------------------------------------

    _PRECALC_BAR_IDENTIFIERS = {
        "open", "high", "low", "close", "volume",
        "hl2", "hlc3", "ohlc4", "hlcc4",
        "time", "time_close", "bar_index",
    }

    def _is_precalc_replayed_source_var(self, name: str) -> bool:
        """True for top-level ``x = input.source(...)`` variables replayed in
        ``precalculate()``.

        The precompute loop explicitly advances native source series and then
        replays those source-input assignments before computing static TA
        sites. Other user aliases, even when they are statically derived from
        bar data (``src = close`` / ``ha_close = close``), are not replayed
        there and must therefore use the normal per-bar TA path."""
        ast = getattr(self.ctx, "ast", None)
        for stmt in getattr(ast, "body", ()):
            if (
                isinstance(stmt, VarDecl)
                and stmt.name == name
                and isinstance(stmt.value, FuncCall)
                and self._is_source_input(stmt.value)
            ):
                return True
        return False

    def _expr_safe_for_ta_precalc(self, expr) -> bool:
        if expr is None:
            return True
        if isinstance(expr, (NumberLiteral, StringLiteral, BoolLiteral, NaLiteral, ColorLiteral)):
            return True
        if isinstance(expr, Identifier):
            if expr.name in self._PRECALC_BAR_IDENTIFIERS:
                return True
            if self._is_precalc_replayed_source_var(expr.name):
                return True
            if expr.name in getattr(self.ctx, "series_vars", set()):
                return False
            return expr.name in getattr(self, "_static_vars", set())
        if isinstance(expr, MemberAccess):
            if isinstance(expr.object, Identifier) and (
                expr.object.name.startswith("input") or expr.object.name in getattr(self, "_enum_defs", {})
            ):
                return True
            return self._expr_safe_for_ta_precalc(expr.object)
        if isinstance(expr, BinOp):
            return self._expr_safe_for_ta_precalc(expr.left) and self._expr_safe_for_ta_precalc(expr.right)
        if isinstance(expr, UnaryOp):
            return self._expr_safe_for_ta_precalc(expr.operand)
        if isinstance(expr, Ternary):
            return (
                self._expr_safe_for_ta_precalc(expr.condition)
                and self._expr_safe_for_ta_precalc(expr.true_val)
                and self._expr_safe_for_ta_precalc(expr.false_val)
            )
        if isinstance(expr, Subscript):
            return self._expr_safe_for_ta_precalc(expr.object) and self._expr_safe_for_ta_precalc(expr.index)
        if isinstance(expr, TupleLiteral):
            return all(self._expr_safe_for_ta_precalc(elem) for elem in expr.elements)
        if isinstance(expr, FuncCall):
            if isinstance(expr.callee, MemberAccess) and isinstance(expr.callee.object, Identifier):
                if expr.callee.object.name in ("math", "str", "color"):
                    return all(self._expr_safe_for_ta_precalc(arg) for arg in expr.args)
            return False
        return False

    # ------------------------------------------------------------------
    # Lazy-edge classification in every scope -- consumed by the precalc gate
    # ------------------------------------------------------------------

    _LAZY_SCOPE_STMT_TYPES = (
        VarDecl, Assignment, TupleAssign, ExprStmt, IfStmt, ForStmt, ForInStmt,
        WhileStmt, SwitchStmt, FuncDef, MethodDef, TypeDecl, EnumDecl,
    )

    def _ta_call_nodes_by_lazy_scope(self) -> tuple[set[int], set[int]]:
        """Classify chart ``ta.*`` call nodes below Pine-v6 lazy edges, everywhere.

        Returns ``(and_rhs, lazy_rhs)``: sites below an ``and`` RHS, and sites
        below any lazy edge (an ``and``/``or`` RHS or a ``?:`` arm), walking the
        top level, control-flow bodies, user-function bodies and UDT field
        defaults. Entering a statement resets both flags, expression nodes
        propagate them, and a ``request.security*`` payload is skipped because
        its own evaluator lowers it. Consumed only by the block-scope opt-outs
        in ``_ta_site_uses_precalc``; top-level statement operands are governed
        by ``_lazy_edge_ta_hoist_plan`` instead.
        """
        cached = getattr(self, "_lazy_scope_ta_call_nodes", None)
        if cached is not None:
            return cached

        and_rhs: set[int] = set()
        lazy_rhs: set[int] = set()

        def note(value, under_and: bool, under_lazy: bool) -> None:
            if value is None:
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    note(item, under_and, under_lazy)
                return
            if isinstance(value, dict):
                for item in value.values():
                    note(item, under_and, under_lazy)
                return
            if isinstance(value, TypeField):
                note(value.default, False, False)
                return
            if not isinstance(value, ASTNode):
                return
            if isinstance(value, self._LAZY_SCOPE_STMT_TYPES):
                for child in vars(value).values():
                    note(child, False, False)
                return
            if isinstance(value, FuncCall):
                callee = value.callee
                is_security = (
                    isinstance(callee, MemberAccess)
                    and isinstance(callee.object, Identifier)
                    and callee.object.name == "request"
                    and callee.member in ("security", "security_lower_tf")
                )
                # A chained receiver (``label.new(...).get_y()``) is an
                # evaluated expression too; namespace/identifier callees are
                # leaves.
                note(callee, under_and, under_lazy)
                for idx, arg in enumerate(getattr(value, "args", ()) or ()):
                    if is_security and idx == 2:
                        continue
                    note(arg, under_and, under_lazy)
                for key, kw_value in (getattr(value, "kwargs", None) or {}).items():
                    if is_security and key == "expression":
                        continue
                    note(kw_value, under_and, under_lazy)
                if (
                    isinstance(callee, MemberAccess)
                    and isinstance(callee.object, Identifier)
                    and callee.object.name == "ta"
                ):
                    if under_and:
                        and_rhs.add(id(value))
                    if under_lazy:
                        lazy_rhs.add(id(value))
                return
            if isinstance(value, BinOp) and value.op == "and":
                note(value.left, under_and, under_lazy)
                note(value.right, True, True)
                return
            if isinstance(value, BinOp) and value.op == "or":
                note(value.left, under_and, under_lazy)
                note(value.right, under_and, True)
                return
            if isinstance(value, Ternary):
                note(value.condition, under_and, under_lazy)
                note(value.true_val, under_and, True)
                note(value.false_val, under_and, True)
                return
            for child in vars(value).values():
                note(child, under_and, under_lazy)

        note(getattr(self.ctx, "ast", None), False, False)
        for finfo in getattr(self.ctx, "func_infos", None) or []:
            note(getattr(finfo, "node", None), False, False)

        result = (and_rhs, lazy_rhs)
        self._lazy_scope_ta_call_nodes = result
        return result

    def _ta_call_nodes_under_and_rhs(self) -> set[int]:
        return self._ta_call_nodes_by_lazy_scope()[0]

    def _ta_call_nodes_under_lazy_rhs(self) -> set[int]:
        return self._ta_call_nodes_by_lazy_scope()[1]

    # ------------------------------------------------------------------
    # TradingView's per-family clocks below a top-level lazy edge
    # ------------------------------------------------------------------
    #
    # Pinned 2026-09-03 with ``lab tv`` on NYSE:F 1D (range 2025-04-01 ..
    # 2026-05-01; cadence-7 ternary probes ``v = bar_index % 7 == 3 ? <call> :
    # na`` exposing the value through the entry size, plus lazy-``and``
    # probes), each scored per call against three models:
    #
    #   every-bar natives   highest 9/9, sma 25/25, ema 23/23 -- the runtime
    #                       advances the built-in on every chart bar; only the
    #                       value is gated (``_lazy_edge_ta_hoist_plan``).
    #   hold-last source    roc 38/38 (+39/39 entries), change 39/39, mom
    #                       39/39 -- TradingView computes these from the CALL'S
    #                       OWN ``source[length]`` history: the source is
    #                       written only on bars where the call executes, the
    #                       last executed value is held on skipped bars, and
    #                       the history is na before the first execution
    #                       (call 1 of every value probe has no TV entry).
    #                       every-bar 0/38..0/39, ring-of-executions 0..1/39.
    #   per-execution       cum, barssince, valuewhen, cross, crossover 39/39,
    #                       rising 39/39 (strictly monotonic over its executed
    #                       samples) and math.sum 39/39 (na until three
    #                       executed samples) -- the native only ever sees the
    #                       samples of bars where the call executes, which is
    #                       exactly the reached-only inline ``compute`` lowering
    #                       (every-bar 0..31/39).
    #
    # The hoist is an ALLOW-LIST and requires a direct ``[k]`` read. A broad
    # every-bar hoist of every stateful family measured on Cloud Run against
    # the full population (2026-09-04, like-for-like vs the same lab bundle)
    # fixed the pinned shapes but cost 170 tiers / 30 hard-lane regressions,
    # and hoisting allow-listed families WITHOUT a history read still broke
    # four ETH hard-lane probes that main matched at 100%: ``adxVal > 30 and
    # adxVal > ta.sma(adxVal, 7)`` (louislapis9), ``volume < ta.sma(volume,
    # 20)`` under ``and`` (oliver1002 -- the original pf-probe-oliver-dual-
    # vol-sma pin), ``ta.change(ta.sma(close, 50)) > 0`` under ``and``
    # (ycelestine77), five ``ta.ema(close, 200)`` under ``or`` (quantbyboji),
    # ``ta.highest(high, n) / entry > 1.05`` under ``and`` (miemomo3). Every
    # every-bar tape (highest/sma/ema ``[1]`` under ``and``, the ternary
    # ``ta.highest(high, 5)[1]``, robmagnaye) reads ``[k]`` on the call, and
    # the bare twins of those tapes (2026-09-04, same cadence-7 probes) are
    # per-execution: ``close > ta.sma(close, 5)`` ring 39/39 (every-bar 23),
    # ``close > ta.ema(close, 5)`` ring 39/39 (26), ``? ta.sma(close, 5) : na``
    # ring 39/39 (2, na until five executed samples), ``? ta.highest(high, 5)
    # : na`` ring 38/39 (11; TV is na with one executed sample, then the
    # partial maximum). So: TradingView keeps a lazily executed call on the
    # reached-only clock unless the call's own history is referenced (``[k]``),
    # in which case it materialises a continuous series and the call is
    # evaluated on every bar. Only ``LAZY_EVERY_BAR_TA`` families
    # (``lowest`` is ``highest``'s mirror) with such a read hoist.
    # ``LAZY_SOURCE_CLOCK_TA`` routes to the hold-last clock;
    # ``LAZY_PER_EXECUTION_TA`` (the seven taped families -- ``math.sum``
    # 39/39 per-execution, na until three executed samples -- plus the exact
    # mirrors ``crossunder``/``falling``) keeps the inline compute and never
    # precalcs. Every other family keeps its existing lowering unchanged
    # (inline compute when reached; precalc when static) until a tape pins
    # it -- ``_lazy_edge_hoist_plan()["skipped"]`` lists them per script.
    LAZY_EVERY_BAR_TA = frozenset({"highest", "lowest", "sma", "ema"})
    LAZY_SOURCE_CLOCK_TA = frozenset({"change", "mom", "roc"})
    LAZY_PER_EXECUTION_TA = frozenset({
        "cum", "barssince", "valuewhen", "cross", "crossover", "crossunder",
        "rising", "falling", "sum",
    })
    UNPINNED_LAZY_EDGE_REASON = (
        "family not pinned every-bar by tape (LAZY_EVERY_BAR_TA allow-list): "
        "existing lowering"
    )
    NO_HISTORY_READ_REASON = (
        "allow-listed family without a direct [k] history read: TradingView "
        "keeps the reached-only clock (louislapis9/oliver1002/miemomo3/"
        "ycelestine77/quantbyboji exact on main, 2026-09-04)"
    )

    @staticmethod
    def _lazy_source_clock_length_node(node):
        kwargs = getattr(node, "kwargs", None) or {}
        if "length" in kwargs:
            return kwargs["length"]
        args = getattr(node, "args", ()) or ()
        return args[1] if len(args) > 1 else None

    @staticmethod
    def _lazy_source_clock_length_literal(length_node) -> int | None:
        if length_node is None:
            return 1
        if (
            isinstance(length_node, NumberLiteral)
            and not isinstance(length_node.value, bool)
            and float(length_node.value) == int(length_node.value)
        ):
            return int(length_node.value)
        return None

    def _lazy_source_clock_eligible(self, site: "TACallSite") -> bool:
        """change/mom/roc with a numeric source; anything else keeps its lowering."""
        if getattr(site, "owner_func", None) is not None or getattr(site, "returns_tuple", False):
            return False
        if not site.compute_args:
            return False
        return self._infer_type(site.compute_args[0]) in ("double", "int", "int64_t")

    def _prepare_lazy_source_clock_sites(self) -> None:
        """Allocate one clock + one held-source Series per routed site (idempotent)."""
        if hasattr(self, "_lazy_source_clock_by_node"):
            return

        def allocate(base: str, reserved: set[str]) -> str:
            candidate = base
            suffix = 2
            while candidate in reserved:
                candidate = f"{base}_{suffix}"
                suffix += 1
            reserved.add(candidate)
            return candidate

        # Pine permits leading-underscore identifiers. Reserve every emitted
        # user member, not only history/var members already tracked by
        # _all_member_names, before minting generated support names.
        reserved_members = set(getattr(self, "_all_member_names", set()))
        reserved_members.update(
            self._safe_name(name)
            for name, _ptype in getattr(self.ctx, "global_var_decls", ())
        )
        reserved_members.update(
            self._safe_name(name)
            for name, _ptype, _init in getattr(self.ctx, "var_members", ())
        )
        reserved_types = set(getattr(self, "_udt_defs", {}))
        for info in getattr(self.ctx, "func_infos", ()):
            emitted = self._func_cpp_base_name(info.name)
            reserved_members.add(emitted)
            reserved_types.add(emitted)
            total = self.ctx.func_call_site_counts.get(info.name, 0)
            for callsite in range(total):
                reserved_members.add(f"{emitted}_cs{callsite}")
        for instance in getattr(self, "_fresh_instances", ()):
            reserved_members.add(instance["name"])
        self._lazy_source_clock_type_name = allocate("_PFLazySourceClock", reserved_types)

        clocks: dict[int, dict] = {}
        routed = self._lazy_edge_ta_hoist_plan()["source_clock"]
        for index, (node_id, info) in enumerate(routed.items(), start=1):
            clocks[node_id] = {
                "clock": allocate(f"_pf_lazy_src_clock_{index}", reserved_members),
                "hist": allocate(f"_pf_lazy_src_hist_{index}", reserved_members),
                "site": info["site"],
                "node": info["node"],
                "length_literal": info["length_literal"],
            }
        self._lazy_source_clock_by_node = clocks

    def _lazy_source_clock_expr(self, site: "TACallSite", node) -> str:
        """Lower a reached change/mom/roc site through its hold-last source clock."""
        info = self._lazy_source_clock_by_node[id(node)]
        source = self._visit_expr(site.compute_args[0])
        length_node = self._lazy_source_clock_length_node(node)
        literal = self._lazy_source_clock_length_literal(length_node)
        if literal is not None:
            length_expr = str(literal)
            held = f"{info['hist']}[{literal - 1}]" if literal >= 1 else "na<double>()"
        else:
            length_raw = self._visit_expr(length_node)
            length_expr = self._coerce_int_slot(
                length_raw, length_node, "int"
            )
            if (length_expr == length_raw
                    and not self._emitted_value_is_double(length_node)):
                length_expr = pine_index_int_cast(length_raw)
            held = (
                f"(({length_expr}) >= 1 ? {info['hist']}[({length_expr}) - 1] "
                f": na<double>())"
            )
        previous = f"{info['clock']}.previous_source({held}, {length_expr})"
        method = "roc" if self._ta_name_from_site(site) == "roc" else "change"
        return f"{info['clock']}.{method}({source}, {previous})"

    def _emit_lazy_source_clock_helper(self, lines: list[str]) -> None:
        """Emit the value-copyable generated runtime helper when needed."""
        self._prepare_lazy_source_clock_sites()
        if not self._lazy_source_clock_by_node:
            return
        type_name = self._lazy_source_clock_type_name
        lines.extend(
            [
                "// TradingView keeps a lazily executed call's `source[k]` history per",
                "// call: the source is written only on bars where the call executes,",
                "// the last executed value is held on the bars it skips, and the",
                "// history is na before the first execution. The paired hist Series",
                "// holds `bar_base_source` (the value committed by earlier bars) once",
                "// per chart bar, so `hist[length - 1]` is the source at the most",
                "// recent execution at or before bar-length, also when the previous",
                "// execution is closer than `length` bars.",
                f"struct {type_name} {{",
                "    double committed_source = na<double>();",
                "    int committed_bar = -1;",
                "    double bar_base_source = na<double>();",
                "    int bar_base_bar = -1;",
                "    int working_bar = -1;",
                "",
                "    void reset() {",
                "        committed_source = na<double>();",
                "        committed_bar = -1;",
                "        bar_base_source = na<double>();",
                "        bar_base_bar = -1;",
                "        working_bar = -1;",
                "    }",
                "",
                "    // Once per bar before the script body; a same-bar recalculation",
                "    // keeps the base frozen so the evaluation stays idempotent.",
                "    void begin_bar(int bar) {",
                "        if (working_bar != bar) {",
                "            bar_base_source = committed_source;",
                "            bar_base_bar = committed_bar;",
                "            working_bar = bar;",
                "        }",
                "    }",
                "",
                "    double previous_source(double held, int length) const {",
                "        if (bar_base_bar < 0 || length < 1) {",
                "            return na<double>();",
                "        }",
                "        return held;",
                "    }",
                "",
                "    double change(double source, double previous) {",
                "        committed_source = source;",
                "        committed_bar = working_bar;",
                "        if (is_na(source) || is_na(previous)) {",
                "            return na<double>();",
                "        }",
                "        return source - previous;",
                "    }",
                "",
                "    double roc(double source, double previous) {",
                "        committed_source = source;",
                "        committed_bar = working_bar;",
                "        if (is_na(source) || is_na(previous) || previous == 0.0) {",
                "            return na<double>();",
                "        }",
                "        return (source - previous) / previous * 100.0;",
                "    }",
                "};",
                "",
            ]
        )

    def _ta_site_uses_precalc(self, site: "TACallSite") -> bool:
        """Whether a static TA site can safely read from ``_precalc_*``.

        Static-ness from the analyzer means the expression can be represented
        from bar data and constants, but the standalone precompute loop only
        replays a narrow subset of per-bar assignments. A user alias such as
        ``ha_close = close`` is static in that analyzer sense, yet its Series is
        empty during precompute, so ``ta.stdev(ha_close, 20)`` precalculates as
        all-``na``. Opting that site out preserves correctness; it simply uses
        the ordinary stateful TA object during ``on_bar``.

        A site hoisted to unconditional per-bar evaluation (top-level lazy
        ``and``/``or`` RHS or ternary arm -- see
        ``_lazy_edge_ta_hoist_plan``) advances on every chart bar by the pinned
        TV rule, which is exactly what precalc computes; the lazy-edge opt-outs
        below therefore never apply to it. A top-level lazy-edge site routed to
        the hold-last source clock (change/mom/roc) or left on its
        per-execution inline compute (cum/barssince/valuewhen/cross*/rising/...)
        must never precalc.

        Re-pinned 2026-09-03: the old opt-out for a chart ``ta.sma`` under an
        ``and`` RHS (pf-probe-oliver-dual-vol-sma, "eager precalc is
        TV-incorrect") and the matching recursive ``ta.ema`` rule encoded a
        per-call clock that ``lab tv`` refuted for top-level shapes (NYSE:F 1D,
        ``... and close > ta.sma(close,5)[1]``: every-bar 25/25 vs per-call 28;
        ``ta.ema``: 23/23 vs 27). The analyzer never marks a site inside an
        ``if``/loop/function scope static, so after hoisting the opt-outs
        only reach a static lazy-edge ``ta.sma``/``ta.ema`` that is not a
        top-level statement operand -- in practice a UDT field default
        (``test_ta_precalc_walks_type_field_defaults``), whose clock is the
        ``Type.new()`` call site's and may sit in a block. Other TA families
        and request.security sites retain their existing behavior."""
        if not getattr(site, "is_static", False):
            return False
        if self._ta_dynamic_plan(site) is not None:
            # The length is read on the bar (its first execution, or every
            # call); precalculate() has no such value.
            return False
        node = getattr(site, "node", None)
        plan = self._lazy_edge_ta_hoist_plan()
        if node is not None and (
            id(node) in plan["source_clock"] or id(node) in plan["per_execution_nodes"]
        ):
            # Hold-last / per-execution clocks: precalc would advance the site
            # on every bar, which the tapes refute for these families.
            return False
        if node is not None and id(node) in plan["call_nodes"]:
            return all(self._expr_safe_for_ta_precalc(arg) for arg in site.compute_args)
        ta_name = self._ta_name_from_site(site)
        if (
            ta_name == "sma"
            and node is not None
            and id(node) in self._ta_call_nodes_under_and_rhs()
        ):
            return False
        if (
            ta_name == "ema"
            and node is not None
            and id(node) in self._ta_call_nodes_under_lazy_rhs()
        ):
            return False
        return all(self._expr_safe_for_ta_precalc(arg) for arg in site.compute_args)

    # ------------------------------------------------------------------
    # Every-bar hoisting of stateful ``ta.*`` sites below top-level lazy edges
    # ------------------------------------------------------------------
    #
    # TradingView rule, pinned 2026-09-03 with ``lab tv`` on NYSE:F 1D (tapes
    # out-pin-ring-lazyand / lazyand-sma / lazyand-ema / ring-ternary): a
    # stateful ``ta.*`` call inside ANY expression operand of a top-level
    # statement -- the RHS of a Pine-v6 lazy ``and``/``or``, either branch of a
    # ternary, nested comparisons -- is evaluated on EVERY bar. Short-circuiting
    # and branch selection gate only the *value*, never the built-in's state,
    # and ``[1]`` on such a call is the previous BAR's value:
    #
    #   c = bar_index % 7 == 3 and close > ta.highest(high,5)[1]   9/9 entries
    #   c = bar_index % 7 == 3 and close > ta.sma(close,5)[1]      25/25
    #   c = bar_index % 7 == 3 and close > ta.ema(close,5)[1]      23/23
    #   v = bar_index % 7 == 3 ? ta.highest(high,5)[1] : na        38/39
    #
    # (the per-call / "previous evaluation" model predicts 8/9, 28, 27 and
    # 2/39). Production instance: robmagnaye14 ``bullMSS = setupAlive and
    # dir == 1 and close > ta.highest(high, 10)[1]``.
    #
    # A stateful call INSIDE an ``if``/local-block/function body that does not
    # execute every bar IS execution-gated on TV (pinned separately) and keeps
    # the in-block compute + ``_hist_call_*`` push lowering untouched.
    #
    # Lowering: every eligible site below a lazy edge of a top-level statement
    # is evaluated once, unconditionally, in a ``const auto _pf_every_bar_ta_N``
    # local emitted BEFORE the statement (in dynamic mode too -- this is
    # independent of ``_use_precalc``). A direct ``[k]`` on the hoisted call
    # pushes its ``_hist_call_*`` Series there as well, so ``[1]`` reads the
    # previous chart bar. The statement's expression then reads the local /
    # the Series instead of stepping the indicator when the operand is reached.

    _LAZY_EDGE_HOIST_NAME_PREFIX = "_pf_every_bar_ta_"

    def _lazy_edge_hoist_block_reason(
        self, site: "TACallSite", history_read: bool = False
    ) -> str | None:
        """Why an otherwise-eligible lazy-edge site is left on its lowering."""
        if getattr(site, "owner_func", None) is not None:
            return "site belongs to a user function body"
        if getattr(site, "returns_tuple", False):
            return "tuple-returning site"
        family = self._ta_name_from_site(site)
        if family in self.LAZY_PER_EXECUTION_TA:
            return (
                "per-execution native: the reached-only inline compute is "
                "TradingView's clock (lab tv 2026-09-03)"
            )
        if family in self.LAZY_SOURCE_CLOCK_TA:
            return "hold-last source-clock family with a non-numeric source"
        if family not in self.LAZY_EVERY_BAR_TA:
            return self.UNPINNED_LAZY_EDGE_REASON
        if not history_read:
            return self.NO_HISTORY_READ_REASON
        return None

    def _lazy_edge_ta_hoist_plan(self) -> dict:
        """Plan (once) which top-level lazy-edge TA sites are hoisted.

        Returns ``{"by_stmt": {id(stmt): [unit, ...]}, "call_nodes": set,
        "source_clock": {id(node): {"node", "site", "length_literal"}},
        "per_execution_nodes": set, "skipped": [(node, site, reason), ...]}``.
        Only ``LAZY_EVERY_BAR_TA`` families whose call carries a direct
        ``[k]`` history read (``Subscript`` on the call) become hoist units.
        ``source_clock`` sites (change/mom/roc) lower through
        ``_lazy_source_clock_expr``; ``per_execution_nodes`` keep the inline
        compute; both are opted out of precalc. Every other family is listed
        in ``skipped`` with ``UNPINNED_LAZY_EDGE_REASON`` and keeps its
        existing lowering. A hoist unit is either
        ``{"kind": "call", "node": FuncCall, "site": TACallSite, "name": str}``
        or ``{"kind": "hist", "node": Subscript}`` (a direct ``[k]`` on a hoisted
        call). Units are in evaluation order: a nested hoisted call precedes the
        call whose argument it is, and a ``hist`` unit follows its call.

        Only top-level statement expressions are scanned (``VarDecl`` values
        except ``var``/``varip`` initializers, ``Assignment`` target/value,
        ``TupleAssign`` value, ``ExprStmt`` expression, and the head condition
        of an ``IfStmt``). ``if``/``for``/``while``/``switch`` bodies, ``else
        if`` conditions (Pine's ``else if`` is an ``if`` inside the ``else``
        local block) and user-function bodies are never hoisted. Inside an
        expression, the walk descends into every operand and call argument
        except a ``request.security*`` payload, which its own evaluator runs.
        Block-local arguments cannot occur at top level, so the skip list only
        carries the shapes named in ``_lazy_edge_hoist_block_reason``.
        """
        cached = getattr(self, "_lazy_edge_hoist_plan_cache", None)
        if cached is not None:
            return cached

        by_stmt: dict[int, list[dict]] = {}
        call_nodes: set[int] = set()
        source_clock: dict[int, dict] = {}
        per_execution_nodes: set[int] = set()
        skipped: list[tuple] = []
        counter = [0]

        def scan(
            expr, under_lazy: bool, units: list[dict], history_read: bool = False
        ) -> None:
            if expr is None:
                return
            if isinstance(expr, FuncCall):
                callee = expr.callee
                is_security = (
                    isinstance(callee, MemberAccess)
                    and isinstance(callee.object, Identifier)
                    and callee.object.name == "request"
                    and callee.member in ("security", "security_lower_tf")
                )
                # Chained receivers (``label.new(...).get_y()``) are evaluated
                # expressions too; namespace/identifier callees are leaves.
                scan(callee, under_lazy, units)
                for idx, arg in enumerate(getattr(expr, "args", ()) or ()):
                    if is_security and idx == 2:
                        continue
                    scan(arg, under_lazy, units)
                for key, value in (getattr(expr, "kwargs", None) or {}).items():
                    if is_security and key == "expression":
                        continue
                    scan(value, under_lazy, units)
                if not under_lazy:
                    return
                site = self._get_ta_site(expr)
                if site is None:
                    return
                family = self._ta_name_from_site(site)
                if family in self.LAZY_SOURCE_CLOCK_TA and self._lazy_source_clock_eligible(site):
                    source_clock[id(expr)] = {
                        "node": expr,
                        "site": site,
                        "length_literal": self._lazy_source_clock_length_literal(
                            self._lazy_source_clock_length_node(expr)
                        ),
                    }
                    return
                reason = self._lazy_edge_hoist_block_reason(site, history_read)
                if reason is not None:
                    if family in self.LAZY_PER_EXECUTION_TA:
                        per_execution_nodes.add(id(expr))
                    skipped.append((expr, site, reason))
                    return
                counter[0] += 1
                units.append({
                    "kind": "call",
                    "node": expr,
                    "site": site,
                    "name": f"{self._LAZY_EDGE_HOIST_NAME_PREFIX}{counter[0]}",
                })
                call_nodes.add(id(expr))
                return
            if isinstance(expr, BinOp):
                if expr.op in ("and", "or"):
                    scan(expr.left, under_lazy, units)
                    scan(expr.right, True, units)
                else:
                    scan(expr.left, under_lazy, units)
                    scan(expr.right, under_lazy, units)
                return
            if isinstance(expr, Ternary):
                scan(expr.condition, under_lazy, units)
                scan(expr.true_val, True, units)
                scan(expr.false_val, True, units)
                return
            if isinstance(expr, UnaryOp):
                scan(expr.operand, under_lazy, units)
                return
            if isinstance(expr, Subscript):
                # ``call(...)[k]``: the call's own history is referenced.
                scan(expr.object, under_lazy, units, history_read=True)
                scan(expr.index, under_lazy, units)
                if isinstance(expr.object, FuncCall) and id(expr.object) in call_nodes:
                    units.append({"kind": "hist", "node": expr})
                return
            if isinstance(expr, MemberAccess):
                scan(expr.object, under_lazy, units)
                return
            if isinstance(expr, TupleLiteral):
                for element in expr.elements:
                    scan(element, under_lazy, units)
                return
            # ``x = if cond ... else ...`` / ``x = switch ...`` value forms: the
            # head expression is top-level, the bodies are local blocks.
            if isinstance(expr, IfStmt):
                scan(expr.condition, under_lazy, units)
                return
            if isinstance(expr, SwitchStmt):
                scan(expr.expr, under_lazy, units)
                return
            # Literals, identifiers and anything else are leaves.

        ast = getattr(self.ctx, "ast", None)
        for stmt in getattr(ast, "body", ()) or ():
            units: list[dict] = []
            for root in self._lazy_edge_statement_roots(stmt):
                scan(root, False, units)
            if units:
                by_stmt[id(stmt)] = units

        # A change / mom / roc call in a top-level if block -- an else-if's
        # and a nested if's too -- runs only on the bars its block runs, and
        # reads its own held source history as a lazy operand's call does:
        # TradingView's tape of te_lazy_if_source reads ta.roc(close, 3) on
        # its tenth bar from the close its third bar held, where the
        # executions' own ring is still short (and the stateful ROC oracles
        # pf-probe-quant-stateful-roc-* read their reached ROC so). Loop and
        # switch bodies, a var initializer and a call read with history keep
        # their lowering.
        def block_sites(node, history_read: bool = False) -> None:
            if node is None or isinstance(node, (str, int, float, bool)):
                return
            if isinstance(node, (list, tuple)):
                for child in node:
                    block_sites(child)
                return
            if not isinstance(node, ASTNode) or isinstance(
                    node, (FuncDef, MethodDef, ForStmt, ForInStmt, WhileStmt, SwitchStmt)):
                return
            if isinstance(node, VarDecl) and (node.is_var or node.is_varip):
                return
            if isinstance(node, Subscript):
                block_sites(node.object, history_read=True)
                block_sites(node.index)
                return
            if isinstance(node, FuncCall):
                callee = node.callee
                is_security = (
                    isinstance(callee, MemberAccess)
                    and isinstance(callee.object, Identifier)
                    and callee.object.name == "request"
                    and callee.member in ("security", "security_lower_tf")
                )
                site = None if history_read else self._get_ta_site(node)
                if (site is not None
                        and self._ta_name_from_site(site) in self.LAZY_SOURCE_CLOCK_TA
                        and self._lazy_source_clock_eligible(site)):
                    source_clock.setdefault(id(node), {
                        "node": node,
                        "site": site,
                        "length_literal": self._lazy_source_clock_length_literal(
                            self._lazy_source_clock_length_node(node)
                        ),
                    })
                for idx, arg in enumerate(getattr(node, "args", ()) or ()):
                    if not (is_security and idx == 2):
                        block_sites(arg)
                for key, value in (getattr(node, "kwargs", None) or {}).items():
                    if not (is_security and key == "expression"):
                        block_sites(value)
                return
            for key, value in vars(node).items():
                if key not in ("loc", "annotations"):
                    block_sites(value)

        for stmt in getattr(ast, "body", ()) or ():
            blocks = [stmt] if isinstance(stmt, IfStmt) else [
                value for value in (getattr(stmt, "value", None),)
                if isinstance(stmt, (VarDecl, Assignment)) and isinstance(value, IfStmt)
            ]
            for block in blocks:
                block_sites(block.body)
                block_sites(block.else_body)

        plan = {
            "by_stmt": by_stmt,
            "call_nodes": call_nodes,
            "source_clock": source_clock,
            "per_execution_nodes": per_execution_nodes,
            "skipped": skipped,
        }
        self._lazy_edge_hoist_plan_cache = plan
        return plan

    def _lazy_edge_hoisted_ta_call_nodes(self) -> set[int]:
        return self._lazy_edge_ta_hoist_plan()["call_nodes"]

    def _emit_lazy_edge_ta_hoists(self, stmt, lines: list[str], indent: int) -> None:
        """Emit the every-bar evaluations a top-level statement depends on.

        Must be paired with ``_clear_lazy_edge_ta_hoists`` after the statement
        is visited: the maps make ``_visit_func_call`` / ``_visit_subscript``
        read the hoisted local / Series while the statement is lowered.
        """
        units = self._lazy_edge_ta_hoist_plan()["by_stmt"].get(id(stmt))
        if not units:
            return
        pad = "    " * indent
        lines.append(
            f"{pad}// Pine v6 lazy operand: TA state advances every bar, only the "
            "value is gated."
        )
        for unit in units:
            node = unit["node"]
            if unit["kind"] == "call":
                rendered = self._visit_expr(node)
                lines.append(f"{pad}const auto {unit['name']} = {rendered};")
                self._hoisted_ta_values[id(node)] = unit["name"]
            else:
                member = self._inline_history_member("hist_call", node)
                value = self._hoisted_ta_values[id(node.object)]
                self._emit_history_series_write(lines, pad, member, value)
                self._hoisted_hist_reads[id(node)] = member

    def _clear_lazy_edge_ta_hoists(self) -> None:
        self._hoisted_ta_values.clear()
        self._hoisted_hist_reads.clear()

    @staticmethod
    def _lazy_edge_statement_roots(stmt) -> list:
        """The expressions of one statement a scope runs on every execution:
        a ``VarDecl`` value (not a ``var``/``varip`` initializer), an
        ``Assignment``'s target and value, a ``TupleAssign`` value, an
        ``ExprStmt`` and an ``IfStmt``'s head condition."""
        if isinstance(stmt, VarDecl):
            if stmt.is_var or stmt.is_varip:
                return []
            return [stmt.value]
        if isinstance(stmt, Assignment):
            return [stmt.target, stmt.value]
        if isinstance(stmt, TupleAssign):
            return [stmt.value]
        if isinstance(stmt, ExprStmt):
            return [stmt.expr]
        if isinstance(stmt, IfStmt):
            return [stmt.condition]
        return []

    # ------------------------------------------------------------------
    # A pure user call's history below a lazy edge
    # ------------------------------------------------------------------
    #
    # TradingView rule, pinned 2026-09-29 with ``lab tv`` on BINANCE:BTCUSDT
    # 15 (``tests/fixtures/lazy_call_history``): ``f(...)[k]`` on a user
    # function's call in the lazily evaluated right operand of ``and``/``or``
    # or in a ternary's arm reads the call's value k executions of its scope
    # ago -- k bars at the top level, k calls inside a function -- on all 95
    # every-third-bar reads of each shape, in a function and at the top level:
    # the call runs on every execution of its scope, and the lazy edge gates
    # only the read. So ``isNew(s) => inS(s) and not inS(s)[1]`` is true on
    # the first bar of every session. The call-local history the Subscript
    # lowering keeps otherwise (pushed only where the operand runs) reads the
    # previous time the operand ran: ``[1]`` three bars back on the tape's
    # reads, and ``isNew`` true once in a whole run.
    #
    # Only a call whose value cannot depend on when it runs is hoisted: a
    # user function whose body is one expression of its parameters, bar
    # fields, literals, ``timeframe.*``/``syminfo.*`` facts, operators, and
    # ``na``/``nz``/``time``/``time_close`` or such functions (the tape's
    # shapes), called positionally with arguments of literals, names and
    # operators. A stateful or side-effecting callee, a read in an ``if`` or
    # loop body, a read not below a lazy edge, one in the arguments of an
    # every-bar ``ta.*`` hoist (already pushed on every bar) and one in a
    # statement the lowering drops keep their lowering.

    _LAZY_CALL_HISTORY_BUILTINS = frozenset({"na", "nz", "time", "time_close"})
    _LAZY_CALL_HISTORY_NAMESPACES = frozenset({"timeframe", "syminfo"})

    def _lazy_call_history_pure_expr(self, expr, params: set[str] | None) -> bool:
        """Whether ``expr`` reads only literals, names and fixed facts through
        operators -- and, when ``params`` is given (a function body), bar
        fields, parameters and the calls ``_lazy_call_history_pure_call``
        admits instead of arbitrary names."""
        stack = [expr]
        while stack:
            n = stack.pop()
            if isinstance(n, (NumberLiteral, BoolLiteral, StringLiteral, NaLiteral,
                              ColorLiteral)):
                continue
            if isinstance(n, Identifier):
                if (params is None or n.name in params or n.name in BAR_SERIES_PUSH
                        or n.name in ("bar_index", "time")):
                    continue
                return False
            if isinstance(n, MemberAccess):
                if (isinstance(n.object, Identifier)
                        and n.object.name in self._LAZY_CALL_HISTORY_NAMESPACES):
                    continue
                return False
            if isinstance(n, BinOp):
                stack.extend((n.left, n.right))
            elif isinstance(n, UnaryOp):
                stack.append(n.operand)
            elif isinstance(n, Ternary):
                stack.extend((n.condition, n.true_val, n.false_val))
            elif (params is not None and isinstance(n, FuncCall)
                    and isinstance(n.callee, Identifier) and not n.kwargs
                    and (n.callee.name in self._LAZY_CALL_HISTORY_BUILTINS
                         or self._lazy_call_history_pure_call(n))):
                stack.extend(n.args)
            else:
                return False
        return True

    def _lazy_call_history_pure_call(self, call) -> bool:
        """A positional call of the one user function of its name whose body
        is a single ``_lazy_call_history_pure_expr`` of its parameters. A body
        reached again through its own calls is not pure."""
        if not isinstance(call, FuncCall) or not isinstance(call.callee, Identifier):
            return False
        name = call.callee.name
        overloads = [fi for fi in self.ctx.func_infos if fi.name == name]
        if call.kwargs or len(overloads) != 1 or overloads[0].node is None:
            return False
        node = overloads[0].node
        if len(call.args) != len(node.params):
            return False
        cache = getattr(self, "_lazy_call_history_pure_cache", None)
        if cache is None:
            cache = self._lazy_call_history_pure_cache = {}
        if id(node) not in cache:
            cache[id(node)] = False
            body = getattr(node, "body", None) or []
            cache[id(node)] = (
                len(body) == 1 and isinstance(body[0], ExprStmt)
                and self._lazy_call_history_pure_expr(body[0].expr, set(node.params))
            )
        return cache[id(node)]

    def _lazy_ctor_history_call(self, call) -> bool:
        """A positional call of the one user function of its name whose body
        is a single ``T.new(...)`` of a user-defined type over
        ``_lazy_call_history_pure_expr`` arguments of its parameters: its value
        is a new object of fixed fields, and TradingView runs it on every
        execution of its scope below a lazy edge when its history is read
        (fixtures/udt_history_tv udth_expr: ``(mk(bar_index * 7)[1]).v`` in a
        ternary's arm reads the previous bar's call)."""
        if not isinstance(call, FuncCall) or not isinstance(call.callee, Identifier):
            return False
        overloads = [fi for fi in self.ctx.func_infos if fi.name == call.callee.name]
        if call.kwargs or len(overloads) != 1 or overloads[0].node is None:
            return False
        node = overloads[0].node
        body = getattr(node, "body", None) or []
        if len(call.args) != len(node.params) or len(body) != 1:
            return False
        ctor = body[0].expr if isinstance(body[0], ExprStmt) else None
        if not (isinstance(ctor, FuncCall)
                and isinstance(ctor.callee, MemberAccess)
                and isinstance(ctor.callee.object, Identifier)
                and ctor.callee.object.name in self._udt_defs
                and ctor.callee.member == "new"):
            return False
        params = set(node.params)
        return all(self._lazy_call_history_pure_expr(arg, params)
                   for arg in [*ctor.args, *ctor.kwargs.values()])

    def _lazy_reference_history_object(self, obj) -> bool:
        """Whether the history of the object or drawing reference ``obj``
        below a lazy edge is kept on every execution of its scope, as
        TradingView keeps it (udth_expr): a pure call or a
        ``_lazy_ctor_history_call`` over pure arguments, a ternary over
        names, literals and operators, or a field of a named object (read
        na while the object is na, ``_lazy_history_read_cpp``)."""
        if self._reference_cpp_type(obj) is None:
            return False
        if isinstance(obj, FuncCall):
            return ((self._lazy_call_history_pure_call(obj)
                     or self._lazy_ctor_history_call(obj))
                    and all(self._lazy_call_history_pure_expr(arg, None)
                            for arg in obj.args))
        if isinstance(obj, Ternary):
            return self._lazy_call_history_pure_expr(obj, None)
        return (isinstance(obj, MemberAccess)
                and isinstance(obj.object, Identifier)
                and self._reference_cpp_type(obj.object) is not None)

    def _lazy_history_read_cpp(self, obj) -> str:
        """The value a hoisted history read pushes: the expression, a field of
        a na object reading na (TradingView stops on the read itself)."""
        if (isinstance(obj, MemberAccess)
                and self._reference_cpp_type(obj.object) is not None):
            handle = self._reference_cpp_type(obj)
            receiver = self._visit_expr(obj.object)
            return (f"(is_na({receiver}) ? {handle}{{}} : "
                    f"{handle}({self._visit_expr(obj)}))")
        return self._visit_expr(obj)

    def _lazy_call_history_units(self, stmt) -> list:
        """The ``pure_call(...)[k]`` reads below a lazy edge of ``stmt``'s
        roots (``_lazy_edge_statement_roots``), in evaluation order, whose
        arguments are ``_lazy_call_history_pure_expr``. A
        ``request.security*`` payload, which its own evaluator runs, is not
        walked."""
        cache = getattr(self, "_lazy_call_history_units_cache", None)
        if cache is None:
            cache = self._lazy_call_history_units_cache = {}
        if id(stmt) in cache:
            return cache[id(stmt)]
        units: list = []
        # A statement the lowering drops (plot, bgcolor, ...) reads nothing.
        if isinstance(stmt, ExprStmt) and self._is_skip_expr(stmt.expr):
            cache[id(stmt)] = units
            return units
        hoisted_ta = self._lazy_edge_hoisted_ta_call_nodes()

        def scan(expr, under_lazy: bool) -> None:
            if expr is None:
                return
            if isinstance(expr, Subscript):
                call = expr.object
                if (under_lazy and self._lazy_call_history_pure_call(call)
                        and all(self._lazy_call_history_pure_expr(arg, None)
                                for arg in call.args)):
                    units.append(expr)
                    return
                if under_lazy and self._lazy_reference_history_object(call):
                    units.append(expr)
                    return
                scan(expr.object, under_lazy)
                scan(expr.index, under_lazy)
            elif isinstance(expr, FuncCall):
                # An every-bar ta.* hoist evaluates its arguments on every
                # bar, so a read inside one already pushes on every bar.
                if id(expr) in hoisted_ta:
                    return
                callee = expr.callee
                is_security = (
                    isinstance(callee, MemberAccess)
                    and isinstance(callee.object, Identifier)
                    and callee.object.name == "request"
                    and callee.member in ("security", "security_lower_tf")
                )
                scan(callee, under_lazy)
                for idx, arg in enumerate(getattr(expr, "args", ()) or ()):
                    if not (is_security and idx == 2):
                        scan(arg, under_lazy)
                for key, value in (getattr(expr, "kwargs", None) or {}).items():
                    if not (is_security and key == "expression"):
                        scan(value, under_lazy)
            elif isinstance(expr, BinOp):
                scan(expr.left, under_lazy)
                scan(expr.right, under_lazy or expr.op in ("and", "or"))
            elif isinstance(expr, Ternary):
                scan(expr.condition, under_lazy)
                scan(expr.true_val, True)
                scan(expr.false_val, True)
            elif isinstance(expr, UnaryOp):
                scan(expr.operand, under_lazy)
            elif isinstance(expr, MemberAccess):
                scan(expr.object, under_lazy)
            elif isinstance(expr, TupleLiteral):
                for element in expr.elements:
                    scan(element, under_lazy)
            elif isinstance(expr, IfStmt):
                scan(expr.condition, under_lazy)
            elif isinstance(expr, SwitchStmt):
                scan(expr.expr, under_lazy)

        for root in self._lazy_edge_statement_roots(stmt):
            scan(root, False)
        cache[id(stmt)] = units
        return units

    def _emit_lazy_call_history_hoists(self, stmt, lines: list[str], indent: int) -> list[int]:
        """Push each ``_lazy_call_history_units`` read's call once, before
        ``stmt``, and make the read return its Series. Returns the reads to
        hand ``_clear_lazy_call_history_hoists`` once ``stmt`` is lowered."""
        units = self._lazy_call_history_units(stmt)
        if not units:
            return []
        pad = "    " * indent
        lines.append(
            f"{pad}// Pine v6 lazy operand: a call read at an offset runs on every "
            "execution, only the read is gated."
        )
        hoisted = []
        for read in units:
            member = self._inline_history_member("hist_call", read)
            self._emit_history_series_write(
                lines, pad, member, self._lazy_history_read_cpp(read.object))
            self._hoisted_hist_reads[id(read)] = member
            hoisted.append(id(read))
        return hoisted

    def _clear_lazy_call_history_hoists(self, hoisted: list[int]) -> None:
        for key in hoisted:
            self._hoisted_hist_reads.pop(key, None)

    def _security_ta_compute_args_for_site(
        self,
        sec_id: int,
        site: "TACallSite",
        ta_results: dict[int, str],
        security_mutable_names: set[str] | None = None,
        helper_binding_stack: tuple[dict, ...] | None = None,
        emitted_lines: list[str] | None = None,
    ) -> str:
        """``_security_ta_compute_args_for_site_base`` plus the per-call
        arguments of a copy whose length the constructor cannot take
        (``_ta_security_plan``), a series argument rendered in the requested
        context."""
        args = self._security_ta_compute_args_for_site_base(
            sec_id, site, ta_results, security_mutable_names,
            helper_binding_stack, emitted_lines,
        )
        plan = self._ta_security_plan(sec_id, site, helper_binding_stack)
        if plan is None:
            return args
        return self._ta_dynamic_compute_args(
            site,
            plan,
            args,
            lambda node: self._build_security_expr(
                sec_id, node, None, ta_results,
                security_mutable_names=security_mutable_names,
                helper_binding_stack=helper_binding_stack,
                emitted_lines=emitted_lines,
            ),
        )

    def _security_ta_compute_args_for_site_base(
        self,
        sec_id: int,
        site: "TACallSite",
        ta_results: dict[int, str],
        security_mutable_names: set[str] | None = None,
        helper_binding_stack: tuple[dict, ...] | None = None,
        emitted_lines: list[str] | None = None,
    ) -> str:
        """Same as ``_ta_compute_args_for_site`` but inside an ``evaluate_security`` body.

        ``current_bar_.<field>`` references are rewritten to ``bar.<field>``
        (the security context's local) and explicit args are funneled
        through ``_build_security_expr`` so identifiers referencing
        mutable globals get rebound to the security-context shadows."""
        ta_name = self._ta_name_from_site(site)

        if site.class_name in ("_PFAnchoredVWAP", "_PFAnchoredVWAPBands"):
            explicit = [
                self._coerce_bool_expr(
                    self._build_security_expr(
                        sec_id, a, None, ta_results,
                        security_mutable_names=security_mutable_names,
                        helper_binding_stack=helper_binding_stack,
                        emitted_lines=emitted_lines,
                    ),
                    a,
                ) if i in self._TA_BOOL_COMPUTE_ARGS.get(ta_name, ()) else
                self._build_security_expr(
                    sec_id, a, None, ta_results,
                    security_mutable_names=security_mutable_names,
                    helper_binding_stack=helper_binding_stack,
                    emitted_lines=emitted_lines,
                )
                for i, a in enumerate(site.compute_args)
            ]
            if len(explicit) < 2:
                raise AssertionError("anchored VWAP security site is missing source/anchor")
            return (
                f"{explicit[0]}, bar.volume, bar.timestamp, syminfo_.timezone, "
                f"syminfo_.session, {explicit[1]}"
            )

        if site.class_name == "_PFPivotPointLevels":
            explicit = [
                self._coerce_bool_expr(
                    self._build_security_expr(
                        sec_id, a, None, ta_results,
                        security_mutable_names=security_mutable_names,
                        helper_binding_stack=helper_binding_stack,
                        emitted_lines=emitted_lines,
                    ),
                    a,
                ) if i in self._TA_BOOL_COMPUTE_ARGS.get(ta_name, ()) else
                self._build_security_expr(
                    sec_id, a, None, ta_results,
                    security_mutable_names=security_mutable_names,
                    helper_binding_stack=helper_binding_stack,
                    emitted_lines=emitted_lines,
                )
                for i, a in enumerate(site.compute_args)
            ]
            if len(explicit) < 3:
                raise AssertionError("pivot security site is missing anchor arguments")
            return (
                f"{explicit[0]}, {explicit[1]}, {explicit[2]}, bar.open, bar.high, "
                "bar.low, bar.close"
            )

        if ta_name in TA_IMPLICIT_COMPUTE_FULL:
            implicit = TA_IMPLICIT_COMPUTE_FULL[ta_name].replace("current_bar_.", "bar.")
            if site.compute_args:
                explicit = ", ".join(
                    (
                        self._coerce_bool_expr(
                            self._build_security_expr(
                                sec_id,
                                a,
                                None,
                                ta_results,
                                security_mutable_names=security_mutable_names,
                                helper_binding_stack=helper_binding_stack,
                                emitted_lines=emitted_lines,
                            ),
                            a,
                        )
                        if i in self._TA_BOOL_COMPUTE_ARGS.get(ta_name, ())
                        else self._build_security_expr(
                            sec_id,
                            a,
                            None,
                            ta_results,
                            security_mutable_names=security_mutable_names,
                            helper_binding_stack=helper_binding_stack,
                            emitted_lines=emitted_lines,
                        )
                    )
                    for i, a in enumerate(site.compute_args)
                )
                if ta_name in self._TA_IMPLICIT_REPLACE:
                    return explicit
                return f"{explicit}, {implicit}" if explicit else implicit
            return implicit

        if site.compute_args:
            explicit = ", ".join(
                (
                    self._coerce_bool_expr(
                        self._build_security_expr(
                            sec_id,
                            a,
                            None,
                            ta_results,
                            security_mutable_names=security_mutable_names,
                            helper_binding_stack=helper_binding_stack,
                            emitted_lines=emitted_lines,
                        ),
                        a,
                    )
                    if i in self._TA_BOOL_COMPUTE_ARGS.get(ta_name, ())
                    else self._build_security_expr(
                        sec_id,
                        a,
                        None,
                        ta_results,
                        security_mutable_names=security_mutable_names,
                        helper_binding_stack=helper_binding_stack,
                        emitted_lines=emitted_lines,
                    )
                )
                for i, a in enumerate(site.compute_args)
            )
            if ta_name in TA_IMPLICIT_APPEND:
                implicit = TA_IMPLICIT_APPEND[ta_name].replace("current_bar_.", "bar.")
                return f"{explicit}, {implicit}" if explicit else implicit
            return explicit

        if ta_name in TA_IMPLICIT_APPEND:
            return TA_IMPLICIT_APPEND[ta_name].replace("current_bar_.", "bar.")

        return ""

    # ------------------------------------------------------------------
    # Lengths the constructor cannot take (K-TA-DYNLEN)
    # ------------------------------------------------------------------
    #
    # A ``ta.*`` constructor argument that is neither a compile-time value nor
    # a runtime-resettable input/timeframe expression used to be refused. Its
    # Pine qualifier decides TradingView's answer (lab tv, K-TA-DYNLEN
    # 2026-09-26; the engine header pineforge/source/pine_ta_length.hpp states
    # every rule with its tape):
    #
    # * simple (fixed for the run -- a syminfo preset, str.* over one):
    #   TradingView answers exactly the constant-length call
    #   (the ring of a sparse ta.lowest included). Lowered onto
    #   ``pineforge::source::FirstCallBound<class>``, built from the call's
    #   first execution; a length goes through ``simple_ta_length`` (0, a
    #   negative length or na stops the run, as TradingView does).
    # * series, for ta.highest / ta.lowest / ta.highestbars / ta.lowestbars:
    #   re-windowed on every call -- ``pineforge::source::Series*``, the length
    #   passed per call.
    # * ta.supertrend: the factor (series or simple) of the first execution is
    #   the run's -- ``pineforge::source::PineSupertrend``.
    #
    # TradingView refuses a series length for ta.rma / ema / rsi / atr / dmi /
    # macd / kc / hma / tsi / rci and supertrend's atrPeriod at compile time,
    # so those only ever carry a simple one here. A series length for the
    # other window functions (ta.sma, ta.wma, ...) keeps the refusal.
    # Constant- and input-length sites never reach this path: their C++ is
    # unchanged. An input.string choice of a length (``mode == "A" ? 5 : 10``)
    # is an input length since CG-SECURITY-2 spells string literals; the other
    # arguments those literals bring to the constructor stay here
    # (``_ta_arg_takes_plan``).

    _TA_SERIES_EXTREME_CLASSES = {
        "highest": "pineforge::source::SeriesHighest",
        "lowest": "pineforge::source::SeriesLowest",
        "highestbars": "pineforge::source::SeriesHighestBars",
        "lowestbars": "pineforge::source::SeriesLowestBars",
    }
    # Pure str.* functions of simple arguments (a simple result).
    _SIMPLE_STR_FUNCS = frozenset({
        "upper", "lower", "contains", "startswith", "endswith", "length",
        "pos", "substring", "replace", "replace_all", "trim", "repeat",
        "tonumber", "tostring",
    })

    def _ta_site_function(self, site: "TACallSite") -> str:
        """The Pine ``ta.<name>`` of a site (a clone keeps its template's node)."""
        node = getattr(site, "node", None)
        if isinstance(node, FuncCall):
            func_name, namespace = self._resolve_callee(node.callee)
            if namespace == "ta" and func_name:
                return func_name
        return self._ta_name_from_site(site)

    def _ta_dynamic_plan(self, site: "TACallSite") -> dict | None:
        """The lowering of a site with a constructor argument the constructor
        cannot take, or None (the ordinary constructor / runtime-reset path).
        ``kind`` is ``first_call`` / ``series_extreme`` / ``supertrend``, or
        ``refused`` (the constructor guard reports it)."""
        cache = self.__dict__.setdefault("_ta_dynamic_plan_cache", {})
        key = id(site)
        if key not in cache:
            cache[key] = self._build_ta_dynamic_plan(site)
        plan = cache[key]
        if plan is None or plan["kind"] == "refused":
            return None
        return plan

    def _ta_dynamic_refusal(self, site: "TACallSite") -> dict | None:
        """The refused plan of a site, when it has one."""
        self._ta_dynamic_plan(site)
        plan = self.__dict__.get("_ta_dynamic_plan_cache", {}).get(id(site))
        if plan is not None and plan["kind"] == "refused":
            return plan
        return None

    # A string literal in a length's spelling, and the names a length may read
    # beside input-backed ones and still be a choice between inputs' options.
    _TA_STRING_LITERAL = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
    _TA_INPUT_CHOICE_NAMES = frozenset({
        "and", "or", "not", "true", "false", "na", "int", "float", "bool",
        "string", "input", "math",
    })

    def _ta_length_literal_facts(self, arg: str, inline: bool = True) -> tuple[bool, bool]:
        """Whether the constructor argument ``arg`` reaches the constructor
        only because string literals are spelled -- a derived name whose value
        holds a literal (it went untracked), or, when ``inline``, a literal
        naming something written in ``arg`` itself (the reset path's
        identifier scans read it as a name) -- and whether it then reads
        anything beside inputs, literals and ``math.*``.

        CG-SECURITY-2 spells those literals so that a length chosen by
        comparing an ``input.string`` with its options is input-derived and
        keeps its constructor. Any other argument they bring to the
        constructor -- a ``syminfo.*`` or ``timeframe.*`` comparison,
        ``str.*`` over one -- stays with this module's lowering, as before
        (the reset path reads ``timeframe.*`` on the chart's timeframe in a
        request.security copy, where TradingView reads the requested one)."""
        newly = False
        names: set[str] = set()
        pending, seen = [(arg, inline)], set()
        while pending:
            raw, literal_counts = pending.pop()
            text = self._inline_inputs_masked(raw)
            if literal_counts and any(re.search(r"[A-Za-z_]", lit[1:-1])
                                      for lit in self._TA_STRING_LITERAL.findall(text)):
                newly = True
            for name in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", blank_string_literals(text)):
                derived = self._derived_input_expr.get(name)
                if derived is None or self._known_var_is_lexically_shadowed(name):
                    names.add(name)
                elif name not in seen:
                    seen.add(name)
                    if self._TA_STRING_LITERAL.search(self._inline_inputs_masked(derived)):
                        newly = True
                    pending.append((derived, True))
        if not newly:
            return False, False
        allowed = (self._TA_INPUT_CHOICE_NAMES | self._input_backed_vars
                   | set(self._MATH_MEMBER_CPP))
        outside = {
            name for name in names - allowed
            if name not in self._known_vars or self._known_var_is_lexically_shadowed(name)
        }
        return True, bool(outside)

    def _ta_arg_takes_plan(self, site: "TACallSite", position: int, arg: str,
                           inline: bool = True) -> bool:
        """A constructor argument the constructor path takes only because
        string literals are spelled, that this module lowers instead
        (``_ta_length_literal_facts``): anything beside an input.string
        choice, and an input.string choice of a parameter that is not known
        to be an int -- a float, or one ``_ta_ctor_param`` does not know (a
        VWAP band multiplier, ``math.sum``'s length) -- since the runtime
        reset casts its expression to a length."""
        newly, outside = self._ta_length_literal_facts(arg, inline)
        if not newly:
            return False
        if outside:
            return True
        param = self._ta_ctor_param(site, position)
        return param is None or param.pine_type != PineType.INT

    def _ta_bound_arg_takes_plan(self, site: "TACallSite", position: int, node) -> bool:
        """``_ta_arg_takes_plan`` of a helper-bound argument's AST. A literal
        written in the helper call itself always reached the constructor
        (its AST is stable); only a derived name's counts."""
        spelled = self._arith_expr_to_str(node) if node is not None else None
        return spelled is not None and self._ta_arg_takes_plan(site, position, spelled, inline=False)

    def _build_ta_dynamic_plan(self, site: "TACallSite") -> dict | None:
        if not site.ctor_args:
            return None
        refused = [
            pos for pos, arg in enumerate(site.ctor_args)
            if not self._is_compile_time_value(self._resolve_ta_ctor_arg(arg))
            and (self._runtime_ctor_arg_for_reset(arg) is None
                 or self._ta_arg_takes_plan(site, pos, arg))
        ]
        if not refused:
            return None
        nodes = list(getattr(site, "ctor_nodes", None) or [])
        if len(nodes) != len(site.ctor_args):
            return {"kind": "refused", "refused": refused}
        # A call outside every callable is spelled from its own nodes, whose
        # identifiers carry the analyzer's scope. A callable's constructor
        # arguments are its call site's spelling substituted into the body.
        own_nodes = site.owner_func is None
        simple = {
            pos: (self._simple_ta_arg_cpp_node(nodes[pos], scoped=True)
                  if own_nodes else self._simple_ta_arg_cpp(site.ctor_args[pos]))
            for pos in refused
        }
        return self._ta_plan_for(site, refused, simple)

    def _ta_plan_for(self, site: "TACallSite", refused: list, simple: dict) -> dict:
        """The plan of a site whose ``refused`` constructor arguments have the
        context-free C++ ``simple`` (None: series)."""
        family = self._ta_site_function(site)
        if family in self._TA_SERIES_EXTREME_CLASSES:
            kind = "first_call" if simple[refused[0]] is not None else "series_extreme"
        elif family == "supertrend":
            kind = "supertrend"
        elif all(simple[pos] is not None for pos in refused):
            kind = "first_call"
        else:
            return {"kind": "refused", "refused": refused}
        return {"kind": kind, "refused": refused, "simple": simple, "family": family}

    def _ta_security_plan(self, sec_id: int, site: "TACallSite",
                          helper_binding_stack=None) -> dict | None:
        """The lowering of the request.security copy of ``site`` that
        ``sec_id`` evaluates through ``helper_binding_stack`` (the variant
        key), or None (the constructor / runtime-reset path, whose guards
        report a length they cannot take). A copy reached through a helper
        call is planned from the arguments that call binds; a copy of a
        callable's request.security cloned per call site, from that call
        site's clone of the TA site."""
        stack = tuple(helper_binding_stack or ())
        key = (sec_id, id(site), self._security_binding_stack_signature(stack))
        cache = self.__dict__.setdefault("_ta_security_plan_cache", {})
        if key not in cache:
            cache[key] = self._build_ta_security_plan(sec_id, site, stack)
        return cache[key]

    def _build_ta_security_plan(self, sec_id: int, site: "TACallSite",
                                stack: tuple) -> dict | None:
        if not stack:
            ctor_site = self._security_ta_ctor_site(sec_id, site)
            plan = self._ta_dynamic_plan(ctor_site)
            if (plan is None and ctor_site is not site
                    and self._ta_dynamic_plan(site) is not None):
                # The constructor path sizes every copy from the first call
                # site's arguments, which are not this call site's: build
                # the constant-length class from this call site's.
                plan = {"kind": "first_call", "refused": [], "simple": {},
                        "family": self._ta_site_function(ctor_site)}
            if plan is None:
                # An input.string choice keeps the constructor, which sizes a
                # callable's one evaluator from its first call site's
                # arguments: refuse call sites passing different ones, as the
                # lowering above does (``_ta_length_literal_facts``).
                dead = getattr(self, "_dead_ta_indices", set())
                if any(
                    self._ta_length_literal_facts(arg)[0]
                    for index, other in enumerate(self.ctx.ta_call_sites)
                    if other.node is site.node and index not in dead
                    for arg in other.ctor_args
                ):
                    self._refuse_shared_security_ta_lengths(sec_id, site)
                return None
            self._refuse_shared_security_ta_lengths(sec_id, site)
            return {**plan, "site": ctor_site, "sec_id": sec_id}
        if not site.ctor_args:
            return None
        nodes = self._security_ta_ctor_arg_nodes(site)
        ctor_args, stability = self._security_ta_ctor_args_for_variant(sec_id, site, stack)
        if stability is None or len(nodes) != len(ctor_args):
            if self._ta_dynamic_plan(site) is not None:
                self._codegen_error(
                    site.node,
                    f"Unsupported requested-context TA constructor for "
                    f"{site.class_name}: PineForge cannot bind the helper "
                    "call's arguments to its constructor arguments.",
                    hint="Call the ta.* function directly inside request.security().",
                )
            return None
        bound = [self._security_helper_bound_ast(node, stack) for node in nodes]
        # The positions the runtime-reset path would refuse
        # (``_collect_ta_runtime_resets``); none -> that path, unchanged.
        # ``ctor_args`` are rendered C++ here: the bound argument's Pine
        # spelling decides ``_ta_bound_arg_takes_plan``.
        refused = [
            pos for pos, arg in enumerate(ctor_args)
            if not self._is_compile_time_value(self._resolve_ta_ctor_arg(arg))
            and ((self._runtime_ctor_arg_for_reset(arg) is None and not stability[pos])
                 or self._ta_bound_arg_takes_plan(site, pos, bound[pos]))
        ]
        if not refused:
            return None
        bound_simple = {
            pos: self._simple_ta_arg_cpp_node(node, scoped=True)
            for pos, node in enumerate(bound) if node is not None
        }
        plan = self._ta_plan_for(
            site, refused, {pos: bound_simple.get(pos) for pos in refused})
        if plan["kind"] == "refused":
            forced = [pos for pos in plan["refused"]
                      if stability[pos] or self._runtime_ctor_arg_for_reset(ctor_args[pos]) is not None]
            if forced:
                # An argument the constructor path takes only because string
                # literals are spelled (``_ta_bound_arg_takes_plan``) that this
                # lowering cannot spell either: refused, as before.
                pos = forced[0]
                self._refuse_security_ta_ctor_arg(site, pos, ctor_args[pos], nodes[pos], stack)
            return None
        return {**plan, "site": site, "sec_id": sec_id, "bound_simple": bound_simple}

    def _refuse_security_ta_ctor_arg(self, site: "TACallSite", pos: int, cpp: str,
                                     node, stack) -> None:
        """Refuse a requested-context TA constructor argument the evaluator
        cannot size the object from, naming it. One that reads a helper's
        ``var`` (or a local an if arm or a history read makes a value of each
        requested bar) is named in Pine, with the local."""
        shown, reason = cpp, ""
        state = self._security_helper_state_reads(node, stack) if node is not None else []
        if state:
            shown = self._arith_expr_to_str(node) or cpp
            reason = (
                f": it reads {', '.join(repr(name) for name in state)}, a helper "
                "local holding a value of each requested bar, and PineForge takes "
                "such a length only for ta.highest, ta.lowest, ta.highestbars and "
                "ta.lowestbars"
            )
        self._codegen_error(
            site.node,
            f"Unsupported requested-context TA constructor "
            f"{'flag' if self._ta_ctor_arg_is_bool(site, pos) else 'length'} "
            f"'{shown}' for {site.class_name}: the "
            f"helper-bound expression is not a stable per-run scalar{reason}.",
            hint=("Use a literal, an input.*() value, timeframe.* metadata, "
                  "or arithmetic over those for TA lengths."),
        )

    def _security_call_item(self, sec_id: int) -> dict:
        for item in self._security_calls:
            if item["sec_id"] == sec_id:
                return item
        return {}

    def _security_ta_ctor_site(self, sec_id: int, site: "TACallSite") -> "TACallSite":
        """The site whose constructor arguments the ``sec_id`` copy of
        ``site`` takes: a callable's request.security cloned per call site
        uses that call site's clone (``_collect_ta_runtime_resets`` does the
        same)."""
        item = self._security_call_item(sec_id)
        containing = item.get("containing_func") or ""
        cs_idx = item.get("callsite_idx")
        if containing and cs_idx is not None:
            remap = self._func_cs_ta_remap.get((containing, cs_idx)) or {}
            name = remap.get(site.member_name)
            if name and name != site.member_name:
                return self._ta_site_by_member().get(name, site)
        return site

    def _refuse_shared_security_ta_lengths(self, sec_id: int, site: "TACallSite") -> None:
        """A callable's request.security that is not cloned per call site is
        one evaluator for every call: it can only take a length all the
        calls spell the same."""
        item = self._security_call_item(sec_id)
        containing = item.get("containing_func") or ""
        if not containing or item.get("callsite_idx") is not None:
            return
        dead = getattr(self, "_dead_ta_indices", set())
        spellings = sorted({
            ", ".join(other.ctor_args)
            for index, other in enumerate(self.ctx.ta_call_sites)
            if other.node is site.node and index not in dead
        })
        if len(spellings) > 1:
            self._codegen_error(
                site.node,
                f"Unsupported TA constructor length for {site.class_name} "
                f"inside request.security in '{containing}': its call sites "
                f"pass different lengths ({' / '.join(spellings)}), and one "
                "requested-context evaluator serves them all.",
                hint=("Call request.security() at each call site, or pass "
                      "the same length from every call."),
            )

    _TF_EXPR_ATOM = re.compile(r'"[^"\\]*"|\w+')

    def _ta_payload_cpp(self, cpp: str, sec_id: int) -> str:
        """``cpp`` (a length rendered for the chart) evaluated in the
        request.security context ``sec_id``: ``timeframe.*`` reads that
        context's timeframe, as ``_build_security_timeframe_member`` lowers
        it for the rest of the payload."""
        if "script_tf_" not in cpp:
            return cpp
        tf = self._security_timeframe_expr(sec_id)
        if not self._TF_EXPR_ATOM.fullmatch(tf):
            tf = f"({tf})"
        # String literals are left alone.
        parts = re.split(r'("(?:\\.|[^"\\])*")', cpp)
        return "".join(
            part if index % 2 else re.sub(r"\bscript_tf_\b", lambda _m: tf, part)
            for index, part in enumerate(parts)
        )

    def _ta_plan_cpp_type(self, site: "TACallSite", plan: dict | None) -> str:
        if plan is None:
            return site.class_name
        if plan["kind"] == "series_extreme":
            return self._TA_SERIES_EXTREME_CLASSES[plan["family"]]
        if plan["kind"] == "supertrend":
            return "pineforge::source::PineSupertrend"
        return f"pineforge::source::FirstCallBound<{site.class_name}>"

    def _ta_member_cpp_type(self, site: "TACallSite") -> str:
        """The C++ type a site's member is declared with."""
        return self._ta_plan_cpp_type(site, self._ta_dynamic_plan(site))

    def _ta_security_member_cpp_type(self, sec_id: int, site: "TACallSite",
                                     variant: dict) -> str:
        """The C++ type of one request.security copy of a site."""
        return self._ta_plan_cpp_type(
            site, self._ta_security_plan(sec_id, site, variant.get("binding_stack", ())))

    def _ta_uses_dynamic_lengths(self) -> bool:
        """Whether any live site or request.security copy takes the lowering
        above (the TU then includes its engine header)."""
        dead = getattr(self, "_dead_ta_indices", set())
        if any(
            self._ta_dynamic_plan(site) is not None
            for index, site in enumerate(self.ctx.ta_call_sites)
            if index not in dead
        ):
            return True
        return any(
            self._ta_security_plan(
                info["sec_id"], self.ctx.ta_call_sites[idx],
                variant.get("binding_stack", ())) is not None
            for info in self._security_eval_info
            for idx, variants in (info.get("ta_variants") or {}).items()
            for variant in variants
        )

    def _ta_ctor_param(self, site: "TACallSite", position: int):
        """The signature parameter a constructor argument binds (Param or None)."""
        from ..analyzer.tables import TA_MULTI_CTOR, TA_PERIOD_ARG
        from ..signatures import TA_FUNCTIONS
        family = self._ta_site_function(site)
        if family in TA_MULTI_CTOR:
            indices = list(TA_MULTI_CTOR[family])
            if family in ("pivothigh", "pivotlow") and len(site.ctor_args) == 2:
                indices = [1, 2]
        elif family in TA_PERIOD_ARG:
            indices = [TA_PERIOD_ARG[family]]
        else:
            return None
        func = TA_FUNCTIONS.get(family)
        if func is None or position >= len(indices):
            return None
        widest = max(func.signatures, key=lambda sig: len(sig.params))
        index = indices[position]
        if index >= len(widest.params):
            return None
        return widest.params[index]

    def _ta_dynamic_ctor_arg(self, site: "TACallSite", plan: dict, position: int,
                             render_node) -> str:
        """One constructor argument of a lowered site: a compile-time value, a
        runtime-reset expression, a context-free simple expression, or (a
        series argument) the call site's own rendering. A request.security
        copy reached through a helper call takes the argument that call
        binds (``ctor_args`` holds the first call site's); any request.security
        copy reads ``timeframe.*`` in its requested context."""
        if "bound_simple" in plan:
            value = plan["bound_simple"].get(position)
        elif position in plan.get("refused", ()):
            # A refused position takes its context-free spelling, else (a
            # series one) the call site's rendering: never the reset path's
            # reading, which one ``_ta_arg_takes_plan`` sends here would
            # have cast to an int length, on the chart's timeframe, at the
            # end of the bar.
            value = plan["simple"].get(position)
        else:
            arg = site.ctor_args[position]
            # The live input first: a compile-time resolution of an input-backed
            # argument is only its default (the constructor's placeholder).
            value = self._runtime_ctor_arg_for_reset(arg)
            if value is None:
                resolved = self._resolve_ta_ctor_arg(arg)
                if self._is_compile_time_value(resolved):
                    value = resolved
            if value is None:
                value = plan["simple"].get(position)
        if value is None:
            value = render_node(site.ctor_nodes[position])
        elif plan.get("sec_id") is not None:
            value = self._ta_payload_cpp(value, plan["sec_id"])
        if self._ta_ctor_arg_is_bool(site, position):
            value = self._ta_ctor_bool_cpp(value)
        return value

    def _ta_dynamic_compute_args(self, site: "TACallSite", plan: dict, base_args: str,
                                 render_node) -> str:
        """The ``compute()`` / ``recompute()`` arguments of a lowered site."""
        kind = plan["kind"]
        site = plan.get("site", site)
        if kind == "series_extreme":
            length = self._ta_dynamic_ctor_arg(site, plan, 0, render_node)
            return f"{base_args}, pineforge::source::ta_number({length})"
        if kind == "supertrend":
            factor = self._ta_dynamic_ctor_arg(site, plan, 0, render_node)
            period = self._ta_dynamic_ctor_arg(site, plan, 1, render_node)
            dynamic = (f"pineforge::source::ta_number({factor}), "
                       f"pineforge::source::ta_number({period})")
            return f"{dynamic}, {base_args}" if base_args else dynamic
        family = plan["family"]
        ctor = []
        for position in range(len(site.ctor_args)):
            value = self._ta_dynamic_ctor_arg(site, plan, position, render_node)
            param = self._ta_ctor_param(site, position)
            if (position in plan["refused"] and param is not None
                    and param.pine_type == PineType.INT
                    and not self._ta_ctor_arg_is_bool(site, position)):
                value = (f'pineforge::source::simple_ta_length({value}, '
                         f'"{family}", "{param.name}")')
            ctor.append(value)
        make = f"[&]() {{ return {site.class_name}({', '.join(ctor)}); }}"
        return f"{make}, {base_args}" if base_args else make

    def _simple_ta_top_level_decls(self) -> dict:
        """Top-level plain declarations read as simple values: declared once,
        never reassigned, not ``var``/``varip``, not declared ``series``."""
        cached = self.__dict__.get("_simple_ta_decls_cache")
        if cached is not None:
            return cached
        reassigned = self._find_reassigned_vars()
        counts: dict[str, int] = {}
        decls: dict[str, VarDecl] = {}
        for stmt in self.ctx.ast.body or []:
            if isinstance(stmt, VarDecl):
                counts[stmt.name] = counts.get(stmt.name, 0) + 1
                decls[stmt.name] = stmt
            elif isinstance(stmt, TupleAssign):
                for name in getattr(stmt, "names", []) or []:
                    counts[name] = counts.get(name, 0) + 2
        result = {
            name: decl for name, decl in decls.items()
            if counts.get(name) == 1 and name not in reassigned
            and not decl.is_var and not decl.is_varip and decl.value is not None
            and (decl.annotations or {}).get("qualifier") != "series"
        }
        self.__dict__["_simple_ta_decls_cache"] = result
        return result

    def _ta_locally_declared_names(self) -> frozenset:
        """Every name a block, loop or callable declares: a parameter, a loop
        variable, a local."""
        cached = self.__dict__.get("_ta_local_names_cache")
        if cached is not None:
            return cached
        top_level = {id(stmt) for stmt in self.ctx.ast.body or []}
        names: set[str] = set()
        for node in self._walk_ast(self.ctx.ast):
            if isinstance(node, (FuncDef, MethodDef)):
                names.update(p for p in node.params if isinstance(p, str))
            elif isinstance(node, VarDecl) and id(node) not in top_level:
                names.add(node.name)
            elif isinstance(node, TupleAssign) and id(node) not in top_level:
                names.update(name for name in node.names if name)
            elif isinstance(node, ForStmt) and node.var:
                names.add(node.var)
            elif isinstance(node, ForInStmt):
                if node.var:
                    names.add(node.var)
                names.update(name for name in node.vars or [] if name)
        result = frozenset(names)
        self.__dict__["_ta_local_names_cache"] = result
        return result

    def _ta_identifier_reads_top_level(self, node: Identifier, scoped: bool) -> bool:
        """Whether ``node`` reads the top-level binding of its name. A node of
        the analysed program (``scoped``) answers from the scope the analyzer
        resolved it in; a spelling re-parsed from ``ctor_args`` has none, so
        its name must be one no block, loop or callable declares."""
        if scoped:
            scopes = getattr(self.ctx, "identifier_binding_scopes", {}) or {}
            return scopes.get(id(node)) == "global"
        return node.name not in self._ta_locally_declared_names()

    def _simple_ta_arg_ast(self, node, seen: frozenset = frozenset(), depth: int = 0,
                           scoped: bool = False):
        """A context-free copy of ``node`` whose leaves are literals, inputs,
        ``syminfo.*`` / ``timeframe.*`` metadata and ``math`` constants
        combined by operators, ternaries, casts and pure ``math.*`` /
        ``str.*`` calls, top-level simple declarations expanded in place --
        a Pine simple value. None when any part may vary by bar, or when an
        identifier may be bound by a block, loop or callable rather than the
        top-level declaration (``_ta_identifier_reads_top_level``)."""
        if node is None or depth > 64:
            return None
        if isinstance(node, (NumberLiteral, StringLiteral, BoolLiteral, NaLiteral)):
            return copy.deepcopy(node)
        if isinstance(node, Identifier):
            name = node.name
            if self._known_var_is_lexically_shadowed(name):
                return None
            if not self._ta_identifier_reads_top_level(node, scoped):
                return None
            # A derived name tracked only since string literals are spelled
            # (``_ta_length_literal_facts``) is expanded from its declaration,
            # as before, not read from the chart's member.
            literal_derived = (name in self._derived_input_expr
                               and self._ta_length_literal_facts(name, inline=False)[0])
            if not literal_derived and (name in self._input_backed_vars
                                        or name in self._known_vars):
                return copy.deepcopy(node)
            decl = self._simple_ta_top_level_decls().get(name)
            if decl is None or name in seen:
                return None
            # The declaration's own nodes carry the analyzer's scope.
            return self._simple_ta_arg_ast(decl.value, seen | {name}, depth + 1, True)
        if isinstance(node, MemberAccess):
            if not isinstance(node.object, Identifier):
                return None
            namespace = node.object.name
            if (namespace == "syminfo"
                    or (namespace == "timeframe" and node.member in self._TF_STABLE_MEMBERS)
                    or (namespace == "math" and node.member in self._MATH_STABLE_MEMBERS)):
                return copy.deepcopy(node)
            return None
        if isinstance(node, BinOp):
            left = self._simple_ta_arg_ast(node.left, seen, depth + 1, scoped)
            right = self._simple_ta_arg_ast(node.right, seen, depth + 1, scoped)
            if left is None or right is None:
                return None
            out = copy.copy(node)
            out.left, out.right = left, right
            return out
        if isinstance(node, UnaryOp):
            operand = self._simple_ta_arg_ast(node.operand, seen, depth + 1, scoped)
            if operand is None:
                return None
            out = copy.copy(node)
            out.operand = operand
            return out
        if isinstance(node, Ternary):
            parts = [self._simple_ta_arg_ast(child, seen, depth + 1, scoped)
                     for child in (node.condition, node.true_val, node.false_val)]
            if any(part is None for part in parts):
                return None
            out = copy.copy(node)
            out.condition, out.true_val, out.false_val = parts
            return out
        if isinstance(node, FuncCall):
            func_name, namespace = self._resolve_callee(node.callee)
            if namespace == "input" or (namespace is None and func_name == "input"):
                return copy.deepcopy(node) if self._is_stable_inline_input(node) else None
            pure = (
                (namespace == "str" and func_name in self._SIMPLE_STR_FUNCS)
                or (namespace == "math" and func_name in self._MATH_STABLE_MEMBERS)
                or (namespace == "timeframe" and func_name in self._TF_STABLE_MEMBERS)
                or (namespace is None and func_name in ("int", "float", "bool", "string"))
            )
            if not pure:
                return None
            args = [self._simple_ta_arg_ast(arg, seen, depth + 1, scoped) for arg in node.args]
            kwargs = {key: self._simple_ta_arg_ast(value, seen, depth + 1, scoped)
                      for key, value in (node.kwargs or {}).items()}
            if any(arg is None for arg in args) or any(v is None for v in kwargs.values()):
                return None
            out = copy.copy(node)
            out.callee = copy.deepcopy(node.callee)
            out.args = args
            out.kwargs = kwargs
            return out
        return None

    def _simple_ta_arg_cpp_node(self, node, scoped: bool = False) -> str | None:
        """``_simple_ta_arg_cpp`` of an AST node (not cached); ``scoped`` when
        the node belongs to the analysed program
        (``_ta_identifier_reads_top_level``)."""
        expanded = self._simple_ta_arg_ast(node, scoped=scoped) if node is not None else None
        if expanded is None:
            return None
        previous = self._reset_input_getter_mode
        self._reset_input_getter_mode = True
        try:
            rendered = self._visit_expr(expanded)
        except Exception:
            rendered = None
        finally:
            self._reset_input_getter_mode = previous
        if rendered and "/* " not in rendered:
            return f"({rendered})"
        return None

    def _simple_ta_arg_cpp(self, arg_str: str) -> str | None:
        """The context-free C++ of a simple constructor argument spelled
        ``arg_str`` (inputs read through their override-aware getters, so it
        evaluates the same in ``on_bar`` and in ``evaluate_security`` before
        either initialised anything), or None when the argument is series.
        The spelling carries no scope: the answer depends on the text alone."""
        cache = self.__dict__.setdefault("_simple_ta_arg_cache", {})
        if arg_str in cache:
            return cache[arg_str]
        try:
            from ..lexer import Lexer
            from ..parser import Parser
            node = Parser(Lexer(arg_str).tokenize(), source=arg_str)._parse_expression()
        except Exception:
            node = None
        result = self._simple_ta_arg_cpp_node(node)
        cache[arg_str] = result
        return result

    # ------------------------------------------------------------------
    # Compile-time-value predicate (paired with the runtime-reset chain
    # that still lives on CodeGen because it uses Python's expression
    # evaluator at codegen time).
    # ------------------------------------------------------------------

    @staticmethod
    def _is_compile_time_value(val: str) -> bool:
        """True if ``val`` is a literal that can be safely embedded in a TA ctor arg."""
        try:
            float(val)
            return True
        except ValueError:
            pass
        return val in (
            "true", "false", "0", "0.0",
            "na<double>()", "na<int>()", "na<int64_t>()", "na<bool>()",
        )
