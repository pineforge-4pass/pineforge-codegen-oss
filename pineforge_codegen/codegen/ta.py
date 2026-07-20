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
    Assignment, BinOp, BoolLiteral, ColorLiteral, ExprStmt, FuncCall,
    ForInStmt, ForStmt, FuncDef, Identifier, MemberAccess, NaLiteral,
    NumberLiteral, StringLiteral, Subscript, Ternary, TupleAssign,
    TupleLiteral, TypeDecl, UnaryOp, VarDecl,
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

    def _ta_call_nodes_by_lazy_scope(self) -> tuple[set[int], set[int], set[int]]:
        """Classify chart ``ta.*`` sites below Pine-v6 lazy-expression edges.

        The first set contains sites below an ``and`` RHS, preserving the
        already accepted lazy-SMA scope.  The second contains sites below any
        Pine-v6 lazy edge: an ``and``/``or`` RHS or either ``?:`` branch.  The
        broader set is consumed only by the recursive-EMA route;
        other TA families retain their current precalculation behavior.  The
        third is the narrow ``and``-only subset: the site is below an ``and``
        RHS and is not nested anywhere inside an ``or`` or ternary expression.
        It gives source-shaped lazy call clocks a stable AST identity without
        inspecting generated member names.
        """
        cached = getattr(self, "_lazy_scope_ta_call_nodes", None)
        if cached is not None:
            return cached

        and_rhs: set[int] = set()
        lazy_rhs: set[int] = set()
        plain_and_rhs: set[int] = set()

        def note_ta_calls(
            expr,
            under_and_rhs: bool,
            under_lazy_rhs: bool,
            under_disallowed_shape: bool,
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
                if isinstance(expr.callee, MemberAccess):
                    obj = expr.callee.object
                    if isinstance(obj, Identifier) and obj.name == "ta":
                        if under_and_rhs:
                            and_rhs.add(id(expr))
                        if under_lazy_rhs:
                            lazy_rhs.add(id(expr))
                        if under_and_rhs and not under_disallowed_shape:
                            plain_and_rhs.add(id(expr))
                # A call target may itself be an evaluated expression, as in
                # ``array.new_float(...).get(0)``. Walk the callee subtree so
                # TA nested in a chained receiver inherits the surrounding
                # lazy context. Plain identifiers and namespace receivers are
                # leaves, so ordinary ``ta.sma`` / ``request.security`` calls
                # remain unaffected here.
                note_ta_calls(
                    callee,
                    under_and_rhs,
                    under_lazy_rhs,
                    under_disallowed_shape,
                )
                for idx, arg in enumerate(getattr(expr, "args", ()) or ()):
                    # The third request.security* argument is evaluated by its
                    # own security evaluator. Symbol, timeframe, and remaining
                    # options are chart-context expressions and must still be
                    # inspected for lazy chart TA.
                    if is_security and idx == 2:
                        continue
                    note_ta_calls(
                        arg,
                        under_and_rhs,
                        under_lazy_rhs,
                        under_disallowed_shape,
                    )
                for key, value in (getattr(expr, "kwargs", None) or {}).items():
                    if is_security and key == "expression":
                        continue
                    note_ta_calls(
                        value,
                        under_and_rhs,
                        under_lazy_rhs,
                        under_disallowed_shape,
                    )
                return
            if isinstance(expr, BinOp):
                if expr.op == "and":
                    # LHS always runs first; RHS is short-circuit conditional.
                    note_ta_calls(
                        expr.left,
                        under_and_rhs,
                        under_lazy_rhs,
                        under_disallowed_shape,
                    )
                    note_ta_calls(
                        expr.right,
                        True,
                        True,
                        under_disallowed_shape,
                    )
                elif expr.op == "or":
                    # ``or`` preserves an enclosing ``and`` scope, but its own
                    # RHS is a Pine-v6 lazy edge for the EMA classifier.
                    note_ta_calls(expr.left, under_and_rhs, under_lazy_rhs, True)
                    note_ta_calls(expr.right, under_and_rhs, True, True)
                else:
                    note_ta_calls(
                        expr.left,
                        under_and_rhs,
                        under_lazy_rhs,
                        under_disallowed_shape,
                    )
                    note_ta_calls(
                        expr.right,
                        under_and_rhs,
                        under_lazy_rhs,
                        under_disallowed_shape,
                    )
                return
            if isinstance(expr, Ternary):
                note_ta_calls(expr.condition, under_and_rhs, under_lazy_rhs, True)
                note_ta_calls(expr.true_val, under_and_rhs, True, True)
                note_ta_calls(expr.false_val, under_and_rhs, True, True)
                return
            if isinstance(expr, UnaryOp):
                note_ta_calls(
                    expr.operand,
                    under_and_rhs,
                    under_lazy_rhs,
                    under_disallowed_shape,
                )
                return
            if isinstance(expr, (MemberAccess, Subscript)):
                note_ta_calls(
                    getattr(expr, "object", None),
                    under_and_rhs,
                    under_lazy_rhs,
                    under_disallowed_shape,
                )
                note_ta_calls(
                    getattr(expr, "index", None),
                    under_and_rhs,
                    under_lazy_rhs,
                    under_disallowed_shape,
                )
                return
            if isinstance(expr, TupleLiteral):
                for elem in expr.elements:
                    note_ta_calls(
                        elem,
                        under_and_rhs,
                        under_lazy_rhs,
                        under_disallowed_shape,
                    )
                return

        def walk_stmt(stmt) -> None:
            if stmt is None:
                return
            if isinstance(stmt, VarDecl):
                note_ta_calls(stmt.value, False, False, False)
                return
            if isinstance(stmt, ExprStmt):
                note_ta_calls(
                    getattr(stmt, "value", None) or getattr(stmt, "expr", None),
                    False,
                    False,
                    False,
                )
                return
            if isinstance(stmt, Assignment):
                note_ta_calls(getattr(stmt, "target", None), False, False, False)
                note_ta_calls(getattr(stmt, "value", None), False, False, False)
                return
            if isinstance(stmt, TupleAssign):
                note_ta_calls(getattr(stmt, "value", None), False, False, False)
                return
            if isinstance(stmt, TypeDecl):
                for field in getattr(stmt, "fields", ()) or ():
                    note_ta_calls(
                        getattr(field, "default", None), False, False, False
                    )
                return
            # If / for / while / assign-like — best-effort field walk
            for attr in ("condition", "body", "else_body", "else_ifs", "value", "target", "iterable"):
                child = getattr(stmt, attr, None)
                if child is None:
                    continue
                if isinstance(child, list):
                    for item in child:
                        if isinstance(item, (list, tuple)):
                            for sub in item:
                                walk_stmt(sub)
                        elif hasattr(item, "body") or hasattr(item, "name") or hasattr(item, "value"):
                            walk_stmt(item)
                        else:
                            note_ta_calls(item, False, False, False)
                elif hasattr(child, "op") or hasattr(child, "args") or hasattr(child, "left"):
                    note_ta_calls(child, False, False, False)
                elif hasattr(child, "body") or hasattr(child, "value") or hasattr(child, "condition"):
                    walk_stmt(child)

        ast = getattr(self.ctx, "ast", None)
        for stmt in getattr(ast, "body", ()) or ():
            walk_stmt(stmt)
        # User function bodies (original sites live here; clones share node ids
        # only when they reuse the same FuncCall object — still best-effort).
        for finfo in getattr(self.ctx, "func_infos", None) or []:
            node = getattr(finfo, "node", None)
            body = getattr(node, "body", None) if node is not None else None
            if body:
                for stmt in body:
                    walk_stmt(stmt)

        result = (and_rhs, lazy_rhs, plain_and_rhs)
        self._lazy_scope_ta_call_nodes = result
        return result

    def _ta_call_nodes_under_and_rhs(self) -> set[int]:
        return self._ta_call_nodes_by_lazy_scope()[0]

    def _ta_call_nodes_under_lazy_rhs(self) -> set[int]:
        return self._ta_call_nodes_by_lazy_scope()[1]

    def _ta_call_nodes_under_plain_and_rhs(self) -> set[int]:
        return self._ta_call_nodes_by_lazy_scope()[2]

    def _ta_call_nodes_in_top_level_var_values(self) -> set[int]:
        cached = getattr(self, "_top_level_var_value_ta_nodes", None)
        if cached is not None:
            return cached
        result: set[int] = set()
        ast = getattr(self.ctx, "ast", None)
        for stmt in getattr(ast, "body", ()) or ():
            if not isinstance(stmt, VarDecl):
                continue
            for child in self._walk_ast(stmt.value):
                if isinstance(child, FuncCall):
                    result.add(id(child))
        self._top_level_var_value_ta_nodes = result
        return result

    def _script_has_user_binding(self, name: str) -> bool:
        cache = getattr(self, "_user_binding_names", None)
        if cache is None:
            cache = set()
            ast = getattr(self.ctx, "ast", None)
            for node in self._walk_ast(ast):
                if isinstance(node, VarDecl):
                    cache.add(node.name)
                elif isinstance(node, TupleAssign):
                    cache.update(node.names)
                elif isinstance(node, ForStmt):
                    cache.add(node.var)
                elif isinstance(node, ForInStmt):
                    if node.var:
                        cache.add(node.var)
                    cache.update(node.vars or ())
                elif isinstance(node, FuncDef):
                    cache.add(node.name)
                    cache.update(node.params)
            self._user_binding_names = cache
        return name in cache

    def _is_lazy_saturated_roc3_site(self, site: "TACallSite") -> bool:
        """Return whether ``site`` has the oracle-backed lazy ROC source shape.

        Selection is deliberately syntactic and callsite-local: a direct
        top-level variable value containing ``ta.roc(close, 3)`` below a plain
        ``and`` RHS, where ``close`` is not shadowed by any user binding. User
        functions, control-flow bodies, request.security evaluators,
        aliases/other sources, named or dynamic lengths, shadowed built-ins,
        and any surrounding ``or``/ternary shape remain on their existing eager
        route. Comparator polarity is intentionally absent; two otherwise
        identical callsites must not acquire different history semantics merely
        because one compares above zero and one below it.
        """
        node = getattr(site, "node", None)
        if (
            node is None
            or getattr(site, "owner_func", None) is not None
            or self._ta_name_from_site(site) != "roc"
            or id(node) not in self._ta_call_nodes_under_plain_and_rhs()
            or id(node) not in self._ta_call_nodes_in_top_level_var_values()
            or self._script_has_user_binding("close")
            or not isinstance(node, FuncCall)
            or getattr(node, "kwargs", None)
            or len(getattr(node, "args", ())) != 2
        ):
            return False
        source, length = node.args
        return (
            isinstance(source, Identifier)
            and source.name == "close"
            and isinstance(length, NumberLiteral)
            and not isinstance(length.value, bool)
            and length.value == 3
        )

    def _prepare_lazy_saturated_roc3_sites(self) -> None:
        """Assign one copyable clock member to each selected AST callsite."""
        if hasattr(self, "_lazy_saturated_roc3_clock_by_node"):
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
        self._lazy_saturated_roc3_type_name = allocate(
            "_PFLazySaturatedROC3Clock", reserved_types
        )
        self._lazy_saturated_roc3_history_name = allocate(
            "_pf_lazy_saturated_roc3_close_history", reserved_members
        )

        clocks: dict[int, str] = {}
        for index, site in enumerate(self.ctx.ta_call_sites):
            if index in getattr(self, "_dead_ta_indices", set()):
                continue
            if self._is_lazy_saturated_roc3_site(site):
                clocks[id(site.node)] = allocate(
                    f"_pf_lazy_saturated_roc3_clock_{len(clocks) + 1}",
                    reserved_members,
                )
        self._lazy_saturated_roc3_clock_by_node = clocks

    def _lazy_saturated_roc3_clock_name(self, site: "TACallSite") -> str:
        self._prepare_lazy_saturated_roc3_sites()
        return self._lazy_saturated_roc3_clock_by_node[id(site.node)]

    def _lazy_saturated_roc3_expr(self, site: "TACallSite") -> str:
        """Lower a reached lazy ROC site through its per-callsite clock."""
        clock = self._lazy_saturated_roc3_clock_name(site)
        history = self._lazy_saturated_roc3_history_name
        return (
            f"{clock}.evaluate(current_bar_.close, "
            f"{history}[3], bar_index_)"
        )

    def _emit_lazy_saturated_roc3_helper(self, lines: list[str]) -> None:
        """Emit the value-copyable generated runtime helper when needed."""
        self._prepare_lazy_saturated_roc3_sites()
        if not self._lazy_saturated_roc3_clock_by_node:
            return
        type_name = self._lazy_saturated_roc3_type_name
        lines.extend(
            [
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
                "    double evaluate(double source, double eager_previous, int bar) {",
                "        if (working_bar != bar) {",
                "            bar_base_source = committed_source;",
                "            bar_base_bar = committed_bar;",
                "            working_bar = bar;",
                "        }",
                "        const bool saturated = bar_base_bar >= 0 &&",
                "            bar - bar_base_bar >= 3;",
                "        const double previous = saturated ? bar_base_source : eager_previous;",
                "        double result = na<double>();",
                "        if (!is_na(source) && !is_na(previous) && previous != 0.0) {",
                "            result = (source - previous) / previous * 100.0;",
                "        }",
                "        committed_source = source;",
                "        committed_bar = bar;",
                "        return result;",
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

        A chart-context ``ta.sma`` nested under an ``and`` RHS is also opted
        out: precalc advances it every bar, which is eager and TV-incorrect for
        the pinned dual-volume-SMA case. Recursive chart ``ta.ema`` sites below
        any Pine-v6 lazy edge follow the same call-clock rule. Other TA families
        and request.security sites retain their existing behavior."""
        if not getattr(site, "is_static", False):
            return False
        if self._is_lazy_saturated_roc3_site(site):
            return False
        node = getattr(site, "node", None)
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
