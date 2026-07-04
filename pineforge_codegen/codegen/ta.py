"""TA (technical analysis) call-site helpers for the codegen.

Holds the eval-free TA helpers: call-site lookup, ``.compute()`` arg
construction, the TA-hoisting machinery for if-bodies, and a small
``_is_compile_time_value`` predicate. The runtime-reset chain that
depends on Python's compile-time expression evaluator
(``_resolve_known`` / ``_runtime_ctor_arg_for_reset`` /
``_collect_ta_runtime_resets`` / ``_emit_ta_runtime_reset``) stays
on ``CodeGen`` in ``base.py`` for now — they sit at the bottom of
this file's docstring as a known follow-up.

Mixin contract — host class must provide:

- ``self._ta_site_map`` (``dict[int, TACallSite]``).
- ``self._active_ta_remap`` (``dict[str, str] | None``).
- ``self._hoist_var_counter`` (``int``, optional — auto-managed).

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
    Identifier, MemberAccess, NaLiteral, NumberLiteral, StringLiteral,
    Subscript, Ternary, TupleLiteral, UnaryOp, VarDecl,
)
from .tables import TA_IMPLICIT_APPEND, TA_IMPLICIT_COMPUTE_FULL

if TYPE_CHECKING:
    from ..analyzer import TACallSite


class TaSiteHelper:
    """TA call-site lookups, .compute() argument construction, and TA hoisting in if-bodies."""

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
    # TA hoisting in if-bodies (computations unconditional, result conditional)
    # ------------------------------------------------------------------

    def _if_body_has_ta(self, stmts: list) -> bool:
        """True if any statement in ``stmts`` references a TA call-site (recursively)."""
        for s in stmts:
            if isinstance(s, VarDecl) and s.value is not None:
                if self._expr_contains_ta(s.value):
                    return True
            if isinstance(s, Assignment) and hasattr(s, "value"):
                if self._expr_contains_ta(s.value):
                    return True
            if isinstance(s, ExprStmt):
                if self._expr_contains_ta(s.expr):
                    return True
        return False

    def _is_result_assignment(self, stmt) -> bool:
        """True iff ``stmt`` is an assignment to the synthetic ``result`` variable.

        ``result`` is the function-body return target injected when a Pine
        function body becomes a C++ function-call site; assignments to it
        carry semantic weight in TA hoisting (they are the conditional-emit
        targets)."""
        if isinstance(stmt, Assignment):
            target_name = self._get_target_name(stmt.target)
            if target_name == "result":
                return True
        return False

    def _expr_contains_ta(self, expr) -> bool:
        """Recursive check: does any subnode of ``expr`` resolve to a TA site?"""
        if expr is None:
            return False
        if self._get_ta_site(expr) is not None:
            return True
        if isinstance(expr, BinOp):
            return self._expr_contains_ta(expr.left) or self._expr_contains_ta(expr.right)
        if isinstance(expr, UnaryOp):
            return self._expr_contains_ta(expr.operand)
        if isinstance(expr, Ternary):
            return (self._expr_contains_ta(expr.true_val)
                    or self._expr_contains_ta(expr.false_val))
        if isinstance(expr, FuncCall):
            return any(self._expr_contains_ta(a) for a in expr.args)
        return False

    def _hoist_if_body(self, stmts: list, cond: str, lines: list[str], pad: str, indent: int) -> None:
        """Emit an if-body with TA hoisting.

        Pine evaluates TA on every bar regardless of branch; C++ TA
        instances must compute() unconditionally to keep their state
        in sync. We split each result-assignment whose RHS contains a
        TA call into:

        - an unconditional ``double _hoist_<n> = <rhs>;`` line,
        - a conditional ``if (<cond>) { result = _hoist_<n>; }``.

        Non-result statements are emitted unconditionally inside an
        opening scope block; result assignments without a TA reference
        stay fully conditional."""
        lines.append(f"{pad}{{")
        conditional_stmts: list = []
        _hoist_counter = getattr(self, "_hoist_var_counter", 0)

        for s in stmts:
            if self._is_result_assignment(s):
                rhs = s.value if hasattr(s, "value") else None
                if rhs is not None and self._expr_contains_ta(rhs):
                    _hoist_counter += 1
                    tmp_var = f"_hoist_{_hoist_counter}"
                    compute_expr = self._visit_expr(rhs)
                    lines.append(f"{pad}    double {tmp_var} = {compute_expr};")
                    conditional_stmts.append(("result", tmp_var))
                else:
                    conditional_stmts.append(("stmt", s))
            else:
                self._visit_stmt(s, lines, indent + 1)

        if conditional_stmts:
            lines.append(f"{pad}    if ({cond}) {{")
            for item in conditional_stmts:
                if item[0] == "result":
                    lines.append(f"{pad}        result = {item[1]};")
                else:
                    self._visit_stmt(item[1], lines, indent + 2)
            lines.append(f"{pad}    }}")
        lines.append(f"{pad}}}")
        self._hoist_var_counter = _hoist_counter

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

    def _ta_site_uses_precalc(self, site: "TACallSite") -> bool:
        """Whether a static TA site can safely read from ``_precalc_*``.

        Static-ness from the analyzer means the expression can be represented
        from bar data and constants, but the standalone precompute loop only
        replays a narrow subset of per-bar assignments. A user alias such as
        ``ha_close = close`` is static in that analyzer sense, yet its Series is
        empty during precompute, so ``ta.stdev(ha_close, 20)`` precalculates as
        all-``na``. Opting that site out preserves correctness; it simply uses
        the ordinary stateful TA object during ``on_bar``."""
        if not getattr(site, "is_static", False):
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
