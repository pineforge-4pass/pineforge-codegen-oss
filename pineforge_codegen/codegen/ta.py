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

from typing import TYPE_CHECKING

from ..ast_nodes import (
    ASTNode, Assignment, BinOp, BoolLiteral, ColorLiteral, EnumDecl, ExprStmt,
    FuncCall, ForInStmt, ForStmt, FuncDef, Identifier, IfStmt, MemberAccess,
    MethodDef, NaLiteral, NumberLiteral, StringLiteral, Subscript, SwitchStmt,
    Ternary, TupleAssign, TupleLiteral, TypeDecl, TypeField, UnaryOp, VarDecl,
    WhileStmt,
)
from .tables import TA_IMPLICIT_APPEND, TA_IMPLICIT_COMPUTE_FULL

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

    def _ta_compute_args_for_site(self, site: "TACallSite") -> str:
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

        if ta_name in TA_IMPLICIT_COMPUTE_FULL:
            implicit = TA_IMPLICIT_COMPUTE_FULL[ta_name]
            if site.compute_args:
                explicit = ", ".join(self._visit_expr(a) for a in site.compute_args)
                if ta_name in self._TA_IMPLICIT_REPLACE:
                    return explicit
                return f"{explicit}, {implicit}" if explicit else implicit
            return implicit

        if site.compute_args:
            explicit = ", ".join(self._visit_expr(a) for a in site.compute_args)
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
    # The hoist is an ALLOW-LIST. A broad every-bar hoist of every stateful
    # family measured on Cloud Run against the full population (2026-09-04,
    # like-for-like vs the same lab bundle) fixed the pinned shapes but cost
    # 170 tiers / 30 hard-lane regressions: TradingView's clock under lazy
    # evaluation is per family, so only families pinned every-bar by tape
    # hoist (``LAZY_EVERY_BAR_TA``; ``lowest`` is ``highest``'s mirror).
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
            previous = (
                f"{info['hist']}[{literal - 1}]" if literal >= 1 else "na<double>()"
            )
        else:
            length_expr = f"(int)({self._visit_expr(length_node)})"
            previous = (
                f"(({length_expr}) >= 1 ? {info['hist']}[({length_expr}) - 1] "
                f": na<double>())"
            )
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
                "// history is na before the first execution. The paired Series holds",
                "// `bar_base_source` (the value committed by earlier bars) once per",
                "// chart bar, so `hist[length - 1]` is the source at the most recent",
                "// execution at or before bar-length.",
                f"struct {type_name} {{",
                "    double committed_source = na<double>();",
                "    double bar_base_source = na<double>();",
                "    int working_bar = -1;",
                "",
                "    void reset() {",
                "        committed_source = na<double>();",
                "        bar_base_source = na<double>();",
                "        working_bar = -1;",
                "    }",
                "",
                "    // Once per bar before the script body; a same-bar recalculation",
                "    // keeps the base frozen so the evaluation stays idempotent.",
                "    void begin_bar(int bar) {",
                "        if (working_bar != bar) {",
                "            bar_base_source = committed_source;",
                "            working_bar = bar;",
                "        }",
                "    }",
                "",
                "    double change(double source, double previous) {",
                "        committed_source = source;",
                "        if (is_na(source) || is_na(previous)) {",
                "            return na<double>();",
                "        }",
                "        return source - previous;",
                "    }",
                "",
                "    double roc(double source, double previous) {",
                "        committed_source = source;",
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

    def _lazy_edge_hoist_block_reason(self, site: "TACallSite") -> str | None:
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
        return None

    def _lazy_edge_ta_hoist_plan(self) -> dict:
        """Plan (once) which top-level lazy-edge TA sites are hoisted.

        Returns ``{"by_stmt": {id(stmt): [unit, ...]}, "call_nodes": set,
        "source_clock": {id(node): {"node", "site", "length_literal"}},
        "per_execution_nodes": set, "skipped": [(node, site, reason), ...]}``.
        Only ``LAZY_EVERY_BAR_TA`` families become hoist units.
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

        def scan(expr, under_lazy: bool, units: list[dict]) -> None:
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
                reason = self._lazy_edge_hoist_block_reason(site)
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
                scan(expr.object, under_lazy, units)
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

        def roots(stmt) -> list:
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

        ast = getattr(self.ctx, "ast", None)
        for stmt in getattr(ast, "body", ()) or ():
            units: list[dict] = []
            for root in roots(stmt):
                scan(root, False, units)
            if units:
                by_stmt[id(stmt)] = units

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

    def _security_ta_compute_args_for_site(
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

        if ta_name in TA_IMPLICIT_COMPUTE_FULL:
            implicit = TA_IMPLICIT_COMPUTE_FULL[ta_name].replace("current_bar_.", "bar.")
            if site.compute_args:
                explicit = ", ".join(
                    self._build_security_expr(
                        sec_id,
                        a,
                        None,
                        ta_results,
                        security_mutable_names=security_mutable_names,
                        helper_binding_stack=helper_binding_stack,
                        emitted_lines=emitted_lines,
                    )
                    for a in site.compute_args
                )
                if ta_name in self._TA_IMPLICIT_REPLACE:
                    return explicit
                return f"{explicit}, {implicit}" if explicit else implicit
            return implicit

        if site.compute_args:
            explicit = ", ".join(
                self._build_security_expr(
                    sec_id,
                    a,
                    None,
                    ta_results,
                    security_mutable_names=security_mutable_names,
                    helper_binding_stack=helper_binding_stack,
                    emitted_lines=emitted_lines,
                )
                for a in site.compute_args
            )
            if ta_name in TA_IMPLICIT_APPEND:
                implicit = TA_IMPLICIT_APPEND[ta_name].replace("current_bar_.", "bar.")
                return f"{explicit}, {implicit}" if explicit else implicit
            return explicit

        if ta_name in TA_IMPLICIT_APPEND:
            return TA_IMPLICIT_APPEND[ta_name].replace("current_bar_.", "bar.")

        return ""

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
