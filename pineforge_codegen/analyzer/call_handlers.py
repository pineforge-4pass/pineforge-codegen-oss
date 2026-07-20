"""Per-callee dispatch + bookkeeping for the analyzer.

This is the largest analyzer mixin (~500 lines). It owns the
``_handle_*_call`` family that routes Pine ``ta.*`` / ``request.*``
/ ``strategy.*`` / ``input.*`` / ``fixnan(...)`` / user-defined
function calls into TA-call-site allocation, ``input.*`` defval
inference, ``request.security()`` recording, ``fixnan`` site
allocation, and per-call-site cloning of TA + series state for
user functions called more than once per bar.

Mixin contract -- host class must provide the following attributes
(all set by ``Analyzer.__init__`` unless noted):

- ``self._ta_call_sites`` (``list[TACallSite]``): TA call-site
  registry. Appended to by ``_handle_ta_call`` and (per-call-site
  clones only) by ``_handle_user_func_call``.
- ``self._ta_counter`` (``int``): monotonically increasing TA member
  index used to mint unique ``_ta_<func>_<n>`` member names.
- ``self._series_bar_fields`` (``set[str]``): bar-field identifiers
  used as TA inputs anywhere in the program.
- ``self._security_calls`` (``list[SecurityCallInfo]``): created on
  first ``request.security(...)`` via ``getattr(..., "_security_calls",
  [])`` and stored back on ``self`` -- the analyzer also reads this
  attribute via ``getattr`` in ``analyze()``.
- ``self._fixnan_counter`` / ``self._fixnan_sites``: ``fixnan(...)``
  member-name counter + site list.
- ``self._symbols`` (``SymbolTable``): consulted by
  ``_handle_input_call`` to type a series-defval input.
- ``self._enum_defs`` (``dict[str, list[str]]``): enum schema, used
  by ``_validate_input_member_tv`` for input.enum() checks.
- ``self._func_defs`` / ``self._func_return_types`` /
  ``self._func_returns_tuple`` / ``self._func_tuple_element_count``:
  user-function metadata captured during initial pass; consumed by
  ``_handle_user_func_call``.
- ``self._func_series_vars`` / ``self._func_var_members`` /
  ``self._func_ta_ranges``: per-function state needed for
  call-site cloning.
- ``self._func_call_site_count`` / ``self._func_call_cs_map``:
  per-call-site indices populated by ``_handle_user_func_call``.
- ``self._func_infos`` (``list[FuncInfo]``): the function-info list
  surfaced through ``AnalyzerContext``.

Sibling-mixin methods consumed via ``self``:

- ``self._visit`` -- visitor entry (``Analyzer.base``).
- ``self._expr_to_str`` -- expression stringifier
  (``DiagnosticsHelper``); used by ``_handle_ta_call`` to render
  ctor args and by ``_handle_user_func_call`` for the param
  substitution map.
- ``self._warn`` / ``self._error`` (``DiagnosticsHelper``).
- ``self._warn_if_unknown_source_id`` (``DiagnosticsHelper``).
- ``self._input_diag_loc`` (``DiagnosticsHelper``).
- ``self._extract_literal_value`` (``TypeHelper``).
- ``self._collect_security_mutable_globals`` (``Analyzer.base``):
  stays on the host class because it walks the AST collecting
  mutable globals -- not an isolated call-handling concern.

Output dataclasses (``TACallSite`` / ``FuncInfo`` / ``FixnanCallSite``
/ ``SecurityCallInfo``) are imported from sibling ``contracts.py`` so
the analyzer package's import graph stays a strict DAG with no cycle
back through ``base.py``.
"""

from __future__ import annotations

from typing import Any

from ..ast_nodes import (
    ASTNode, Assignment, BinOp, BoolLiteral, ExprStmt, FuncCall, Identifier,
    IfStmt, MemberAccess, NumberLiteral, StringLiteral, Subscript, SwitchStmt,
    Ternary, TupleLiteral, UnaryOp, VarDecl,
)
from ..symbols import PineType
from .. import signatures as sigs
from .. import tv_input_choices as tv_in
from .contracts import FixnanCallSite, FuncInfo, SecurityCallInfo, TACallSite
from .tables import (
    BAR_FIELDS, TA_CLASS_MAP, TA_MULTI_CTOR, TA_NO_CTOR, TA_PERIOD_ARG,
    TA_TUPLE_RETURNS, TA_TUPLE_ELEMENT_COUNTS, TA_COMPUTE_ARGS,
)


class CallHandlers:
    """``_handle_*_call`` dispatch + bookkeeping for analyzer call-sites.

    Mixed into ``Analyzer``; not meant to be instantiated standalone.
    See the module docstring for the host-class state contract."""

    # ------------------------------------------------------------------
    # TA call handling
    # ------------------------------------------------------------------

    def _callsite_primitive_expr_type(
        self,
        expr: ASTNode | None,
        parameter_types: dict[str, PineType],
    ) -> PineType:
        """Infer a primitive return using one written call's parameter types.

        The analyzer's legacy ``FuncInfo`` is definition-wide, but Pine's
        untyped parameters are polymorphic per written call.  Keep this helper
        intentionally primitive and expression-local: it provides the exact
        family needed by history-preserving identity/wrapper functions without
        attempting to monomorphize collections or reference types here.
        """
        if expr is None:
            return PineType.UNKNOWN
        if isinstance(expr, ExprStmt):
            return self._callsite_primitive_expr_type(
                expr.expr, parameter_types
            )
        if isinstance(expr, Identifier):
            if expr.name in parameter_types:
                return parameter_types[expr.name]
            if expr.name in {"true", "false"}:
                return PineType.BOOL
            return PineType.UNKNOWN
        if isinstance(expr, Subscript):
            # Pine's history operator preserves the receiver's scalar family.
            return self._callsite_primitive_expr_type(
                expr.object, parameter_types
            )
        if isinstance(expr, NumberLiteral):
            return (
                PineType.FLOAT
                if isinstance(expr.value, float)
                else PineType.INT
            )
        if isinstance(expr, BoolLiteral):
            return PineType.BOOL
        if isinstance(expr, StringLiteral):
            return PineType.STRING
        if isinstance(expr, UnaryOp):
            if expr.op == "not":
                return PineType.BOOL
            return self._callsite_primitive_expr_type(
                expr.operand, parameter_types
            )
        if isinstance(expr, BinOp):
            left = self._callsite_primitive_expr_type(
                expr.left, parameter_types
            )
            right = self._callsite_primitive_expr_type(
                expr.right, parameter_types
            )
            if expr.op in {"==", "!=", ">", "<", ">=", "<=", "and", "or"}:
                return PineType.BOOL
            if left == PineType.STRING or right == PineType.STRING:
                return PineType.STRING
            if expr.op == "/" or PineType.FLOAT in {left, right}:
                return PineType.FLOAT
            if left == PineType.INT and right == PineType.INT:
                return PineType.INT
            return PineType.UNKNOWN
        if isinstance(expr, Ternary):
            true_type = self._callsite_primitive_expr_type(
                expr.true_val, parameter_types
            )
            false_type = self._callsite_primitive_expr_type(
                expr.false_val, parameter_types
            )
            if true_type == false_type:
                return true_type
            if PineType.STRING in {true_type, false_type}:
                return PineType.STRING
            if PineType.FLOAT in {true_type, false_type}:
                return PineType.FLOAT
            return PineType.UNKNOWN
        if isinstance(expr, FuncCall):
            callee = expr.callee
            if isinstance(callee, Identifier):
                if callee.name == "int":
                    return PineType.INT
                if callee.name == "float":
                    return PineType.FLOAT
                if callee.name == "bool":
                    return PineType.BOOL
                # A user callable's definition-wide return cache is not a
                # call-site fact.  In particular, a pure untyped transform
                # nested inside a polymorphic history wrapper may have been
                # analyzed first as FLOAT even when this path carries an
                # int64 timestamp or bool.  Exact direct-wrapper call edges are
                # reconciled later; arbitrary nested transforms stay UNKNOWN
                # and therefore fail closed instead of source-order coercing.
                return PineType.UNKNOWN
        return PineType.UNKNOWN

    @staticmethod
    def _join_callsite_primitive_types(
        types: list[PineType],
    ) -> PineType:
        if not types or PineType.UNKNOWN in types:
            return PineType.UNKNOWN
        first = types[0]
        if all(value == first for value in types):
            return first
        if set(types).issubset({PineType.INT, PineType.FLOAT}):
            return PineType.FLOAT
        return PineType.UNKNOWN

    def _apply_callsite_local_type_effects(
        self,
        stmt: ASTNode,
        local_types: dict[str, PineType],
    ) -> None:
        """Flow one statement's primitive local writes into ``local_types``.

        This is deliberately a small callable-return analysis, not a second
        semantic analyzer. It exists so a terminal alias such as
        ``prior = src[1]; prior`` keeps the exact written-call family. Unknown
        loops/collections remain unknown and cannot silently pick FLOAT.
        """
        if isinstance(stmt, VarDecl):
            hinted = {
                "int": PineType.INT,
                "float": PineType.FLOAT,
                "bool": PineType.BOOL,
                "string": PineType.STRING,
                "color": PineType.COLOR,
            }.get(stmt.type_hint or "", PineType.UNKNOWN)
            local_types[stmt.name] = (
                hinted
                if hinted != PineType.UNKNOWN
                else self._callsite_primitive_expr_type(
                    stmt.value, local_types
                )
            )
            return
        if (
            isinstance(stmt, Assignment)
            and isinstance(stmt.target, Identifier)
        ):
            rhs = self._callsite_primitive_expr_type(
                stmt.value, local_types
            )
            if stmt.op == ":=":
                # Pine variables retain their declaration/inferred primitive
                # family for their lifetime. An ``na``/otherwise unknown
                # declaration may acquire the first concrete family here.
                current = local_types.get(
                    stmt.target.name, PineType.UNKNOWN
                )
                local_types[stmt.target.name] = (
                    current if current != PineType.UNKNOWN else rhs
                )
            else:
                local_types[stmt.target.name] = (
                    self._join_callsite_primitive_types(
                        [
                            local_types.get(
                                stmt.target.name, PineType.UNKNOWN
                            ),
                            rhs,
                        ]
                    )
                )
            return
        if isinstance(stmt, IfStmt):
            before = dict(local_types)
            branch_envs: list[dict[str, PineType]] = []
            for body in (stmt.body, stmt.else_body):
                branch = dict(before)
                for child in body:
                    self._apply_callsite_local_type_effects(child, branch)
                branch_envs.append(branch)
            all_names = set().union(*(env.keys() for env in branch_envs))
            for name in all_names:
                local_types[name] = self._join_callsite_primitive_types(
                    [
                        env.get(name, before.get(name, PineType.UNKNOWN))
                        for env in branch_envs
                    ]
                )
            return
        if isinstance(stmt, SwitchStmt):
            before = dict(local_types)
            bodies = [body for _, body in stmt.cases]
            if stmt.default_body:
                bodies.append(stmt.default_body)
            else:
                bodies.append([])
            branch_envs = []
            for body in bodies:
                branch = dict(before)
                for child in body:
                    self._apply_callsite_local_type_effects(child, branch)
                branch_envs.append(branch)
            all_names = set().union(*(env.keys() for env in branch_envs))
            for name in all_names:
                local_types[name] = self._join_callsite_primitive_types(
                    [
                        env.get(name, before.get(name, PineType.UNKNOWN))
                        for env in branch_envs
                    ]
                )

    def _callsite_body_return_type(
        self,
        body: list[ASTNode],
        parameter_types: dict[str, PineType],
    ) -> PineType:
        if not body:
            return PineType.UNKNOWN
        local_types = dict(parameter_types)
        for stmt in body[:-1]:
            self._apply_callsite_local_type_effects(stmt, local_types)
        terminal = body[-1]
        if isinstance(terminal, ExprStmt):
            terminal = terminal.expr
        if isinstance(terminal, VarDecl):
            return self._callsite_primitive_expr_type(
                terminal.value, local_types
            )
        if isinstance(terminal, IfStmt):
            if not terminal.body:
                return PineType.UNKNOWN
            branches = [terminal.body]
            # A missing else produces contextual ``na`` in Pine and therefore
            # does not erase the concrete family's type.
            if terminal.else_body:
                branches.append(terminal.else_body)
            return self._join_callsite_primitive_types(
                [
                    self._callsite_body_return_type(
                        branch, dict(local_types)
                    )
                    for branch in branches
                ]
            )
        if isinstance(terminal, SwitchStmt):
            bodies = [case_body for _, case_body in terminal.cases]
            if terminal.default_body:
                bodies.append(terminal.default_body)
            if not bodies:
                return PineType.UNKNOWN
            return self._join_callsite_primitive_types(
                [
                    self._callsite_body_return_type(
                        branch, dict(local_types)
                    )
                    for branch in bodies
                ]
            )
        return self._callsite_primitive_expr_type(terminal, local_types)

    def _callsite_callable_return_type(
        self,
        func_def,
        param_types: list[PineType],
        fallback: PineType,
    ) -> PineType:
        inferred = self._callsite_body_return_type(
            func_def.body,
            {
                name: (
                    param_types[index]
                    if index < len(param_types)
                    else PineType.UNKNOWN
                )
                for index, name in enumerate(func_def.params)
            },
        )
        return inferred if inferred != PineType.UNKNOWN else fallback

    def _merge_ta_args(self, func_name: str, node: FuncCall) -> list:
        """Merge positional args and kwargs into a unified positional list."""
        param_names = sigs.get_param_names("ta", func_name)
        if param_names is None and func_name == "sum":
            param_names = sigs.get_param_names("math", "sum")
        if param_names is None or not node.kwargs:
            return list(node.args)

        # Start with positional args
        merged = list(node.args)
        # Fill in kwargs at their expected positions
        for i, pname in enumerate(param_names):
            if pname in node.kwargs:
                # Extend list if needed
                while len(merged) <= i:
                    merged.append(None)
                if merged[i] is None:
                    merged[i] = node.kwargs[pname]
        # Remove trailing Nones
        while merged and merged[-1] is None:
            merged.pop()
        return merged

    def _handle_ta_call(self, func_name: str, node: FuncCall) -> PineType:
        """Handle ta.* function calls."""
        # Visit all args for side effects (series detection, etc.)
        for arg in node.args:
            self._visit(arg)
        for val in node.kwargs.values():
            self._visit(val)

        # ta.pivot_point_levels is a free runtime function (not a stateful
        # indicator), but its codegen lowers to use `_s_high[1]`, `_s_low[1]`,
        # `_s_close[1]` so the pivot is calculated from the PREVIOUS bar's
        # HLC (matching Pine v6 semantics where `developing` defaults to
        # false). Register the bar-field history series here so that the
        # codegen emits the corresponding `Series<double> _s_high/...` members
        # and pushes them at the top of every on_bar tick.
        if func_name == "pivot_point_levels":
            self._series_bar_fields.add("high")
            self._series_bar_fields.add("low")
            self._series_bar_fields.add("close")
            return PineType.FLOAT  # actual array<float> handled by type inference

        # ta.vwap(source, anchor, stdev_mult) → 3-arg bands form.
        # When called with 3 args (or anchor/stdev_mult kwargs), remap to the
        # internal "vwap_bands" key which maps to ta::VWAPBands (returns tuple).
        if func_name == "vwap":
            param_names_v = ["source", "anchor", "stdev_mult"]
            merged_v = list(node.args)
            for i, pname in enumerate(param_names_v):
                if pname in node.kwargs:
                    while len(merged_v) <= i:
                        merged_v.append(None)
                    if merged_v[i] is None:
                        merged_v[i] = node.kwargs[pname]
            if len(merged_v) >= 3:
                func_name = "vwap_bands"

        if func_name not in TA_CLASS_MAP:
            return PineType.FLOAT

        # Merge positional + kwargs into a unified arg list
        all_args = self._merge_ta_args(func_name, node)

        # ta.tr(handle_na) — TV v6 default for handle_na is false. When the
        # caller omits the arg, inject the explicit ``false`` so the C++
        # TR ctor receives an unambiguous compile-time literal at the
        # initializer-list site (`_ta_tr_1(false)`).
        if func_name == "tr" and not all_args:
            default_arg = BoolLiteral(value=False)
            self._visit(default_arg)
            all_args = [default_arg]

        if func_name == "vwap" and not all_args:
            default_src = Identifier(name="close")
            self._visit(default_src)
            self._series_bar_fields.add("close")
            all_args = [default_src]

        # Handle ta.highest(length) / ta.lowest(length) with 1 arg:
        # single arg is the length, source defaults to high/low respectively.
        # Remap so all_args = [default_source, length_arg].
        _DEFAULT_SOURCE = {"highest": "high", "lowest": "low"}
        if func_name in _DEFAULT_SOURCE and len(all_args) == 1:
            default_src = Identifier(name=_DEFAULT_SOURCE[func_name])
            self._visit(default_src)
            self._series_bar_fields.add(_DEFAULT_SOURCE[func_name])
            all_args = [default_src, all_args[0]]

        self._ta_counter += 1
        class_name = TA_CLASS_MAP[func_name]
        member_name = f"_ta_{func_name}_{self._ta_counter}"
        returns_tuple = func_name in TA_TUPLE_RETURNS

        # vwap_bands special dispatch: ta.vwap(source, anchor, stdev_mult)
        # ctor receives stdev_mult only; compute receives source only.
        # The anchor arg (index 1) is the Pine-level "when to reset" series;
        # our VWAPBands wrapper uses UTC-day boundaries matching the daily
        # anchor default, so anchor is intentionally ignored in codegen.
        if func_name == "vwap_bands":
            ctor_args: list[str] = []
            if len(all_args) >= 3 and all_args[2] is not None:
                ctor_args = [self._expr_to_str(all_args[2])]
            compute_args: list = []
            if all_args and all_args[0] is not None:
                compute_args = [all_args[0]]
            is_static = self._global_scope and all(self._is_static_expression(arg) for arg in compute_args)
            site = TACallSite(
                member_name=member_name,
                class_name=class_name,
                ctor_args=ctor_args,
                compute_args=compute_args,
                returns_tuple=returns_tuple,
                node=node,
                is_static=is_static,
                owner_func=(self._enclosing_func_names[-1] if self._enclosing_func_names else None),
            )
            self._ta_call_sites.append(site)
            self._ta_member_names.add(site.member_name)
            return PineType.FLOAT

        # Determine constructor args
        ctor_args: list[str] = []
        effective_multi_ctor = TA_MULTI_CTOR.copy()
        if func_name in ("pivothigh", "pivotlow") and len(all_args) == 3:
            effective_multi_ctor[func_name] = [1, 2]

        if func_name in TA_NO_CTOR:
            pass
        elif func_name in effective_multi_ctor:
            for idx in effective_multi_ctor[func_name]:
                if idx < len(all_args) and all_args[idx] is not None:
                    ctor_args.append(self._expr_to_str(all_args[idx]))
        elif func_name in TA_PERIOD_ARG:
            idx = TA_PERIOD_ARG[func_name]
            if idx < len(all_args) and all_args[idx] is not None:
                ctor_args.append(self._expr_to_str(all_args[idx]))

        # Determine compute args (all args that aren't ctor args)
        compute_args: list = []
        ctor_indices = set()
        if func_name in effective_multi_ctor:
            ctor_indices = set(effective_multi_ctor[func_name])
        elif func_name in TA_PERIOD_ARG:
            ctor_indices = {TA_PERIOD_ARG[func_name]}

        if func_name in TA_COMPUTE_ARGS:
            for i in TA_COMPUTE_ARGS[func_name]:
                if i < len(all_args) and all_args[i] is not None:
                    compute_args.append(all_args[i])
        else:
            for i, arg in enumerate(all_args):
                if i not in ctor_indices and arg is not None:
                    compute_args.append(arg)

        is_static = self._global_scope and all(self._is_static_expression(arg) for arg in compute_args)
        site = TACallSite(
            member_name=member_name,
            class_name=class_name,
            ctor_args=ctor_args,
            compute_args=compute_args,
            returns_tuple=returns_tuple,
            node=node,
            is_static=is_static,
            owner_func=(self._enclosing_func_names[-1] if self._enclosing_func_names else None),
        )
        self._ta_call_sites.append(site)
        self._ta_member_names.add(site.member_name)

        return PineType.FLOAT

    def _security_symbol_is_heikinashi(self, node, _seen=None) -> bool:
        """True when a request.security symbol is ``ticker.heikinashi(<chart
        symbol>)`` — directly, or via a global alias (``haTicker =
        ticker.heikinashi(syminfo.tickerid)``). Name-cycle-guarded. The
        support_checker has already rejected the cross-symbol HA case, so any HA
        reaching here is the chart's own symbol."""
        if _seen is None:
            _seen = set()
        if (isinstance(node, FuncCall) and isinstance(node.callee, MemberAccess)
                and isinstance(node.callee.object, Identifier)
                and node.callee.object.name == "ticker"
                and node.callee.member == "heikinashi"):
            return True
        if (isinstance(node, Identifier) and node.name in self._global_expr_map
                and node.name not in _seen):
            _seen.add(node.name)
            return self._security_symbol_is_heikinashi(self._global_expr_map[node.name], _seen)
        return False

    def _handle_request_call(self, func_name: str, node: FuncCall) -> PineType:
        """Handle request.* function calls."""
        if func_name == "security":
            param_names = ["symbol", "timeframe", "expression", "gaps", "lookahead",
                           "ignore_invalid_symbol", "currency"]
            all_args = list(node.args)
            for i, pname in enumerate(param_names):
                if pname in node.kwargs:
                    while len(all_args) <= i:
                        all_args.append(None)
                    all_args[i] = node.kwargs[pname]

            tf_node = all_args[1] if len(all_args) > 1 else None
            expr_node = all_args[2] if len(all_args) > 2 else None

            # Visit non-expression args first (symbol, tf, gaps, lookahead)
            for arg in node.args:
                if arg is not None and arg is not expr_node:
                    self._visit(arg)
            for k, val in node.kwargs.items():
                if val is not None and val is not expr_node:
                    self._visit(val)

            # Track TA sites created by the expression
            ta_start = len(self._ta_call_sites)
            if expr_node is not None:
                self._visit(expr_node)
            ta_end = len(self._ta_call_sites)
            security_ta_range = (ta_start, ta_end) if ta_end > ta_start else None

            # Assign ID and record the call
            self._security_calls = getattr(self, "_security_calls", [])
            sec_id = len(self._security_calls)

            returns_tuple = isinstance(expr_node, TupleLiteral)
            tuple_size = len(expr_node.elements) if returns_tuple else 0
            tuple_element_types = (
                self._tuple_element_types_by_node.get(id(expr_node), ())
                if returns_tuple
                else ()
            )
            if not returns_tuple and isinstance(expr_node, FuncCall):
                expr_func = None
                expr_ns = None
                if (isinstance(expr_node.callee, MemberAccess)
                        and isinstance(expr_node.callee.object, Identifier)):
                    expr_ns = expr_node.callee.object.name
                    expr_func = expr_node.callee.member
                elif isinstance(expr_node.callee, Identifier):
                    expr_func = expr_node.callee.name
                    if self._func_returns_tuple.get(expr_func, False):
                        tuple_size = self._func_tuple_element_count.get(expr_func, 0)
                        tuple_types = self._func_tuple_element_types.get(expr_func, ())
                        numeric_tuple = (
                            tuple_size >= 2
                            and len(tuple_types) == tuple_size
                            and all(
                                item in (PineType.INT, PineType.FLOAT)
                                for item in tuple_types
                            )
                        )
                        bool_tuple = (
                            tuple_size >= 2
                            and len(tuple_types) == tuple_size
                            and all(item == PineType.BOOL for item in tuple_types)
                        )
                        if not (numeric_tuple or bool_tuple):
                            inferred_types = ", ".join(
                                item.value for item in tuple_types
                            ) or "unknown"
                            self._error(
                                "request.security tuple-return helpers support two or more "
                                "numeric int/float elements or homogeneous bool elements; inferred "
                                f"{tuple_size} element(s) [{inferred_types}]",
                                expr_node.loc,
                            )
                        else:
                            returns_tuple = True
                            tuple_element_types = tuple_types
                if expr_ns == "ta":
                    if expr_func == "vwap":
                        merged_v = list(expr_node.args)
                        for i, pname in enumerate(["source", "anchor", "stdev_mult"]):
                            if pname in expr_node.kwargs:
                                while len(merged_v) <= i:
                                    merged_v.append(None)
                                if merged_v[i] is None:
                                    merged_v[i] = expr_node.kwargs[pname]
                        if len(merged_v) >= 3:
                            expr_func = "vwap_bands"
                    if expr_func in TA_TUPLE_RETURNS:
                        returns_tuple = True
                        tuple_size = TA_TUPLE_ELEMENT_COUNTS.get(expr_func, 0)

            gaps_node = all_args[3] if len(all_args) > 3 else None
            lookahead_node = all_args[4] if len(all_args) > 4 else None

            mutable_globals = tuple(sorted(self._collect_security_mutable_globals(expr_node)))
            # Heikin-Ashi same-symbol read: request.security(ticker.heikinashi(
            # syminfo.tickerid), ...) (directly or via a global alias). The engine
            # applies the HA candle transform inside the security eval.
            symbol_node = all_args[0] if all_args else None
            heikinashi = self._security_symbol_is_heikinashi(symbol_node)
            # Capture the user function (if any) whose body contains this call,
            # so the codegen can resolve a parameter ``tf`` via the call sites.
            scope_name = self._symbols.current_scope.name
            containing_func = scope_name[5:] if scope_name.startswith("func_") else ""
            if returns_tuple and tuple_element_types:
                self._tuple_element_types_by_node[id(node)] = tuple_element_types
            self._security_calls.append(SecurityCallInfo(
                sec_id=sec_id,
                timeframe=tf_node,
                expression=expr_node,
                returns_tuple=returns_tuple,
                tuple_size=tuple_size,
                tuple_element_types=tuple_element_types,
                gaps=gaps_node,
                lookahead=lookahead_node,
                ta_range=security_ta_range,
                heikinashi=heikinashi,
                depends_on_mutable_globals=bool(mutable_globals),
                mutable_globals=mutable_globals,
                containing_func=containing_func,
            ))

            return PineType.FLOAT

        if func_name == "security_lower_tf":
            return self._handle_request_security_lower_tf(node)

        # Fallback for other request.*
        for arg in node.args:
            self._visit(arg)
        for val in node.kwargs.values():
            self._visit(val)
        return PineType.FLOAT

    def _handle_request_security_lower_tf(self, node: FuncCall) -> PineType:
        """Lower ``request.security_lower_tf(symbol, timeframe, expression, ...)``.

        TV signature differs from ``request.security``: there is no ``gaps``
        or ``lookahead`` keyword (lower-TF emulation pins both off), and
        the result is an ``array<T>`` with one element per synthesised
        sub-bar of the current chart bar instead of a scalar T.

        We piggy-back on the existing ``SecurityCallInfo`` plumbing — same
        ``sec_id`` allocation, same TA-binding-stack collection, same
        mutable-global discovery — but flip ``is_lower_tf_array=True`` so
        the codegen knows to emit a ``std::vector<T>`` accumulator that
        gets cleared on sub-bar 0 and pushed-to per sub-bar.

        UDT / color / string element types are deliberately rejected here
        with a precise diagnostic; the runtime path only knows how to
        accumulate ``double`` / ``int`` / ``bool``."""
        param_names = ["symbol", "timeframe", "expression",
                       "ignore_invalid_symbol", "currency",
                       "ignore_invalid_timeframe", "calc_bars_count"]

        unknown = set(node.kwargs) - set(param_names)
        if unknown:
            self._error(
                "request.security_lower_tf has unknown parameter(s): "
                + ", ".join(sorted(unknown))
                + ". Supported parameters: " + ", ".join(param_names),
                node.loc,
            )

        all_args = list(node.args)
        for i, pname in enumerate(param_names):
            if pname in node.kwargs:
                while len(all_args) <= i:
                    all_args.append(None)
                all_args[i] = node.kwargs[pname]

        tf_node = all_args[1] if len(all_args) > 1 else None
        expr_node = all_args[2] if len(all_args) > 2 else None

        if expr_node is None:
            self._error(
                "request.security_lower_tf requires an expression argument",
                node.loc,
            )

        if isinstance(expr_node, TupleLiteral):
            self._error(
                "request.security_lower_tf does not support tuple expressions yet. "
                "Issue separate request.security_lower_tf calls for each series.",
                node.loc,
            )

        for arg in node.args:
            if arg is not None and arg is not expr_node:
                self._visit(arg)
        for k, val in node.kwargs.items():
            if val is not None and val is not expr_node:
                self._visit(val)

        ta_start = len(self._ta_call_sites)
        expr_pine_type = PineType.FLOAT
        if expr_node is not None:
            expr_pine_type = self._visit(expr_node)
        ta_end = len(self._ta_call_sites)
        security_ta_range = (ta_start, ta_end) if ta_end > ta_start else None

        # Cache the resolved element type on the call node so the
        # ``_type_spec_from_expr`` pass can map ``request.security_lower_tf``
        # to ``array<T>`` without re-visiting the expression (which would
        # double-allocate TA call sites for expressions like ``ta.ema``).
        cached_anns = getattr(node, "annotations", None) or {}
        cached_anns["lower_tf_element_pine_type"] = expr_pine_type
        node.annotations = cached_anns

        if expr_pine_type not in (PineType.FLOAT, PineType.INT, PineType.BOOL,
                                   PineType.NA, PineType.UNKNOWN):
            element_label = {
                PineType.STRING: "string",
                PineType.COLOR: "color",
            }.get(expr_pine_type, str(expr_pine_type))
            self._error(
                "request.security_lower_tf element type '" + element_label
                + "' is not yet supported. Supported element types: float, int, bool. "
                "UDT / color / string element types are out of scope.",
                node.loc,
            )

        self._security_calls = getattr(self, "_security_calls", [])
        sec_id = len(self._security_calls)

        mutable_globals = tuple(sorted(self._collect_security_mutable_globals(expr_node)))
        self._security_calls.append(SecurityCallInfo(
            sec_id=sec_id,
            timeframe=tf_node,
            expression=expr_node,
            returns_tuple=False,
            tuple_size=0,
            gaps=None,
            lookahead=None,
            ta_range=security_ta_range,
            depends_on_mutable_globals=bool(mutable_globals),
            mutable_globals=mutable_globals,
            is_lower_tf_array=True,
        ))

        # ``request.security_lower_tf`` returns an array; the value-level
        # PineType remains UNKNOWN here so callers fall through to
        # ``_type_spec_from_expr`` for the structured ``array<T>`` spec.
        return PineType.UNKNOWN

    def _handle_strategy_call(self, func_name: str, node: FuncCall) -> PineType:
        """Handle strategy.* function calls."""
        for arg in node.args:
            self._visit(arg)
        for val in node.kwargs.values():
            self._visit(val)
        if func_name in ("convert_to_account", "convert_to_symbol", "default_entry_qty"):
            return PineType.FLOAT
        return PineType.VOID

    # ------------------------------------------------------------------
    # Input call handling
    # ------------------------------------------------------------------

    def _handle_input_call(self, node: FuncCall) -> PineType:
        """Handle input() calls without qualifier."""
        # First arg is defval
        if node.args:
            defval = node.args[0]
            self._visit(defval)
            # Infer type from defval
            if isinstance(defval, NumberLiteral):
                if isinstance(defval.value, float):
                    return PineType.FLOAT
                return PineType.INT
            if isinstance(defval, StringLiteral):
                return PineType.STRING
            if isinstance(defval, BoolLiteral):
                return PineType.BOOL
            if isinstance(defval, Identifier):
                # input(close) => source input
                self._validate_plain_input_source(defval, node)
                sym = self._symbols.resolve(defval.name)
                if sym:
                    return sym.pine_type
                return PineType.FLOAT
        # Check kwargs for defval
        if "defval" in node.kwargs:
            defval = node.kwargs["defval"]
            self._visit(defval)
            if isinstance(defval, Identifier):
                self._validate_plain_input_source(defval, node)
            if isinstance(defval, NumberLiteral):
                if isinstance(defval.value, float):
                    return PineType.FLOAT
                return PineType.INT
            if isinstance(defval, StringLiteral):
                return PineType.STRING
            if isinstance(defval, BoolLiteral):
                return PineType.BOOL

        # Visit remaining args
        for arg in node.args[1:]:
            self._visit(arg)
        for val in node.kwargs.values():
            self._visit(val)

        return PineType.FLOAT  # default

    def _merge_input_params(self, member: str | None, node: FuncCall) -> dict[str, Any]:
        """Positional + kwargs merged like codegen (for input.* validation)."""
        if member is None:
            param_names = sigs.get_param_names(None, "input")
        else:
            param_names = sigs.get_param_names("input", member)
        if not param_names:
            return {}
        merged: list[Any] = list(node.args)
        for i, pname in enumerate(param_names):
            if pname in node.kwargs:
                while len(merged) <= i:
                    merged.append(None)
                if i >= len(merged) or merged[i] is None:
                    merged[i] = node.kwargs[pname]
        out: dict[str, Any] = {}
        for i, pname in enumerate(param_names):
            if i < len(merged) and merged[i] is not None:
                out[pname] = merged[i]
        for k, v in node.kwargs.items():
            if k not in out:
                out[k] = v
        return out

    def _input_enum_type_name(self, node: FuncCall) -> str | None:
        """If this is input.enum(...) with Enum.member defval, return the enum type name."""
        callee = node.callee
        if not isinstance(callee, MemberAccess):
            return None
        if not isinstance(callee.object, Identifier) or callee.object.name != "input":
            return None
        if callee.member != "enum":
            return None
        merged = self._merge_input_params("enum", node)
        dv = merged.get("defval")
        if dv is None and node.args:
            dv = node.args[0]
        if isinstance(dv, MemberAccess) and isinstance(dv.object, Identifier):
            return dv.object.name
        return None

    def _validate_plain_input_source(self, defval: ASTNode, node: FuncCall) -> None:
        """Warn when plain input() uses a series defval unlike TV built-ins."""
        if isinstance(defval, Identifier):
            self._warn_if_unknown_source_id(defval.name, defval, node)

    def _validate_input_member_tv(self, member: str, node: FuncCall) -> None:
        """TradingView-style const checks for input.* (warnings only)."""
        merged = self._merge_input_params(member, node)
        defval = merged.get("defval")
        if defval is None and node.args:
            defval = node.args[0]

        if member == "source" and defval is not None:
            if isinstance(defval, Identifier):
                self._warn_if_unknown_source_id(defval.name, defval, node)
            else:
                self._warn(
                    "input.source defval is not a native chart series (open, high, low, close, …); "
                    "complex indicators or expressions are not supported in PineForge.",
                    self._input_diag_loc(node, defval),
                )
            return

        if member == "timeframe" and isinstance(defval, StringLiteral):
            if not tv_in.is_valid_timeframe_string(defval.value):
                self._warn(
                    f"input.timeframe defval {defval.value!r} is not a typical Pine timeframe string.",
                    self._input_diag_loc(node, defval),
                )
            return

        if member == "session" and isinstance(defval, StringLiteral):
            if not tv_in.is_plausible_session_string(defval.value):
                self._warn(
                    f"input.session defval {defval.value!r} may be invalid (expected e.g. "
                    "'24x7', '0930-1600', or weekday flags).",
                    self._input_diag_loc(node, defval),
                )
            return

        if member == "string":
            opts = merged.get("options")
            if isinstance(opts, TupleLiteral):
                literals: list[str] = []
                non_const = False
                for el in opts.elements:
                    if isinstance(el, StringLiteral):
                        literals.append(el.value)
                    else:
                        non_const = True
                        break
                if not non_const and literals and isinstance(defval, StringLiteral):
                    if defval.value not in literals:
                        self._warn(
                            "input.string defval is not among the options list values.",
                            self._input_diag_loc(node, defval),
                        )
            return

        if member == "enum" and defval is not None:
            if isinstance(defval, MemberAccess) and isinstance(defval.object, Identifier):
                ename = defval.object.name
                emem = defval.member
                members = self._enum_defs.get(ename)
                if members is None:
                    self._error(
                        f"Enum '{ename}' must be declared above this input.enum() call "
                        "(or the name is misspelled).",
                        self._input_diag_loc(node, defval),
                    )
                if emem not in members:
                    self._warn(
                        f"input.enum defval {ename}.{emem} is not a member of enum {ename}.",
                        self._input_diag_loc(node, defval),
                    )

    def _handle_input_member_call(self, member: str, node: FuncCall) -> PineType:
        """Handle input.int(), input.float(), etc."""
        for arg in node.args:
            self._visit(arg)
        for val in node.kwargs.values():
            self._visit(val)

        self._validate_input_member_tv(member, node)

        type_map = {
            "int": PineType.INT,
            "float": PineType.FLOAT,
            "bool": PineType.BOOL,
            "string": PineType.STRING,
            "source": PineType.FLOAT,
            "color": PineType.COLOR,
            "enum": PineType.INT,
            "session": PineType.STRING,
            "timeframe": PineType.STRING,
            "time": PineType.INT,
            "symbol": PineType.STRING,
            "price": PineType.FLOAT,
            "text_area": PineType.STRING,
        }
        return type_map.get(member, PineType.FLOAT)

    def _check_input_call(self, node: FuncCall) -> tuple[PineType, bool, Any] | None:
        """Check if a FuncCall is an input call and extract default value.
        Returns (type, is_const, const_value) or None.
        """
        callee = node.callee

        if isinstance(callee, Identifier) and callee.name == "input":
            # input(defval, ...)
            defval = self._extract_defval(node)
            ptype = self._handle_input_call(node)
            return (ptype, True, defval)

        if isinstance(callee, MemberAccess):
            if isinstance(callee.object, Identifier) and callee.object.name == "input":
                member = callee.member
                defval = self._extract_defval(node)
                ptype = self._handle_input_member_call(member, node)
                return (ptype, True, defval)

        return None

    def _extract_defval(self, node: FuncCall) -> Any:
        """Extract the default value from an input call."""
        # First positional arg is typically defval
        if node.args:
            first = node.args[0]
            return self._extract_literal_value(first)
        if "defval" in node.kwargs:
            return self._extract_literal_value(node.kwargs["defval"])
        return None

    # ------------------------------------------------------------------
    # matrix method dispatch
    # ------------------------------------------------------------------

    def _handle_matrix_method(self, member: str, recv_spec) -> PineType:
        """Map a matrix.<member>(receiver, ...) call to its PineType.

        ``recv_spec`` is the receiver's :class:`TypeSpec` (kind ``"matrix"``).
        ``_type_spec_from_expr`` (in ``analyzer/types.py``) already returns
        the correct structured ``TypeSpec`` for matrix-method calls so codegen
        downstream is unaffected; this helper exists so the smaller
        :class:`PineType` enum surface used by ``_visit_FuncCall`` and
        ``_visit_VarDecl`` no longer collapses element types to ``VOID``.

        Phase D Task 2: previously the general MemberAccess arm in
        :meth:`_visit_FuncCall` returned ``PineType.VOID`` for matrix-method
        calls, so ``v = m.get(0, 0)`` typed ``v`` as ``VOID`` even on
        ``matrix<int>``.
        """
        from ..symbols import TypeSpec

        if recv_spec is None or recv_spec.kind != "matrix":
            return PineType.VOID

        elem: TypeSpec | None = recv_spec.element

        # Element-typed return paths
        if member == "get":
            return self._element_pine_type(elem)
        if member in ("row", "col"):
            # Element type is preserved via TypeSpec.array(elem); the legacy
            # PineType slot can't represent array<T>, so fall back to a
            # reasonable scalar PineType (UNKNOWN is a poor fit because
            # downstream defaults to FLOAT for UNKNOWN).
            return PineType.VOID

        # Scalar-return methods (numeric matrix only — codegen rejects
        # these on non-float matrices via MATRIX_NUMERIC_ONLY).
        if member in ("det", "trace", "rank", "sum", "avg", "min", "max", "mode"):
            return PineType.FLOAT
        if member == "elements_count":
            return PineType.INT
        if member in (
            "is_square", "is_identity", "is_diagonal", "is_antidiagonal",
            "is_symmetric", "is_antisymmetric", "is_triangular",
            "is_stochastic", "is_binary", "is_zero",
        ):
            return PineType.BOOL
        if member in ("rows", "columns"):
            return PineType.INT

        # Mutators / matrix-returning methods don't carry a usable scalar
        # PineType; type_spec on the LHS Symbol is what codegen reads for
        # those cases.
        return PineType.VOID

    @staticmethod
    def _element_pine_type(elem) -> PineType:
        """Element ``TypeSpec`` -> ``PineType`` for matrix.get() / array.get().

        Mirrors :meth:`TypeHelper._pine_type_to_spec` (inverse direction).
        Returns ``PineType.VOID`` when the element has no clean PineType
        slot (UDT / nested collection) -- callers should consult ``type_spec``
        / ``udt_type_name`` on the resulting Symbol instead.
        """
        if elem is None:
            return PineType.VOID
        if elem.kind == "primitive":
            mapping = {
                "int": PineType.INT,
                "float": PineType.FLOAT,
                "bool": PineType.BOOL,
                "string": PineType.STRING,
                "color": PineType.COLOR,
            }
            return mapping.get(elem.name or "", PineType.VOID)
        # UDT, array, map, matrix: PineType enum can't represent these.
        # _visit_VarDecl's type_spec / udt_type_name path covers the gap.
        return PineType.VOID

    # ------------------------------------------------------------------
    # fixnan handling
    # ------------------------------------------------------------------

    def _handle_fixnan_call(self, node: FuncCall) -> PineType:
        """Handle fixnan() calls."""
        arg_type = PineType.FLOAT
        for arg in node.args:
            arg_type = self._visit(arg)

        self._fixnan_counter += 1
        owner = self._enclosing_func_names[-1] if self._enclosing_func_names else None
        site = FixnanCallSite(
            member_name=f"_prev_fixnan_{self._fixnan_counter}",
            pine_type=arg_type,
            node=node,
            owner_func=owner,
        )
        idx = len(self._fixnan_sites)
        self._fixnan_sites.append(site)
        self._fixnan_member_names.add(site.member_name)
        if owner is not None:
            self._func_fixnan_indices.setdefault(owner, []).append(idx)

        return arg_type

    # ------------------------------------------------------------------
    # User-defined function calls
    # ------------------------------------------------------------------

    def _func_local_length_defs(self, func_def) -> dict[str, str]:
        """Collect a user function's local scalar length-vars to their RHS
        expression string, e.g. ``qqeCalc`` with ``wp = sf * 2 - 1`` returns
        ``{"wp": "sf * 2 - 1"}``.

        Only plain (non-``var``/``varip``) declarations whose RHS is a pure
        arithmetic expression over identifiers/numbers (NumberLiteral, Identifier,
        BinOp, UnaryOp, or a math.* FuncCall) qualify — these are the shapes that
        can legitimately feed a TA constructor length. Series-valued locals (whose
        RHS is a ta.* call, a subscript, a ternary, etc.) are skipped so we never
        inline a price series into a ctor-length slot. Names reassigned with ``:=``
        are also skipped (their value is not a stable compile-time length).
        """
        def _is_arith(n) -> bool:
            if isinstance(n, (NumberLiteral, Identifier)):
                return True
            if isinstance(n, BinOp):
                return _is_arith(n.left) and _is_arith(n.right)
            if isinstance(n, UnaryOp):
                return _is_arith(n.operand)
            if isinstance(n, Ternary):
                # ``cond ? a : b`` — expand when both branches are arith.
                # The condition may be a comparison/logical over arith leaves;
                # rely on the codegen stability gate to reject series deps.
                return (_is_arith(n.true_val) and _is_arith(n.false_val)
                        and _is_arith(n.condition))
            if isinstance(n, MemberAccess):
                # ``timeframe.*`` / ``syminfo.*`` / ``math.pi`` etc. — stable
                # per-run scalars that may appear inside a function-local
                # derived length. Let the codegen stability classifier decide.
                return True
            if isinstance(n, FuncCall):
                callee = n.callee
                # Allow math.* helpers (math.round/sqrt/cos/...) over arith args.
                if (isinstance(callee, MemberAccess)
                        and isinstance(callee.object, Identifier)
                        and callee.object.name == "math"):
                    return all(_is_arith(a) for a in n.args)
                # Pine type-cast builtins ``int(x)`` / ``float(x)`` / ``bool(x)``
                # / ``string(x)`` — transparent over arith args. Common in
                # derived TA lengths (``int(math.round(2 / a))``).
                if (isinstance(callee, Identifier)
                        and callee.name in ("int", "float", "bool", "string")):
                    return all(_is_arith(a) for a in n.args)
            return False

        reassigned: set[str] = set()
        def _scan_reassign(stmts):
            from ..ast_nodes import Assignment
            for s in stmts or []:
                if isinstance(s, Assignment) and isinstance(s.target, Identifier):
                    reassigned.add(s.target.name)
                for attr in ("body", "else_body"):
                    sub = getattr(s, attr, None)
                    if isinstance(sub, list):
                        _scan_reassign(sub)
        _scan_reassign(func_def.body)

        defs: dict[str, str] = {}
        for stmt in func_def.body or []:
            if (isinstance(stmt, VarDecl)
                    and not stmt.is_var and not stmt.is_varip
                    and stmt.name not in reassigned
                    and stmt.value is not None
                    and _is_arith(stmt.value)):
                defs[stmt.name] = self._expr_to_str(stmt.value)
        return defs

    def _materialize_user_func_call_site_state(
            self, func_name: str, cs_idx: int, node: FuncCall,
            *, reuse_existing_owner: str | None = None,
            reuse_existing_targets: dict[int, int] | None = None,
            ta_site_indices: list[int] | None = None,
            materialize_fixnan: bool = True) -> dict[int, int]:
        """Materialize TA/fixnan state for one UDF call-site variant.

        Ordinary call sites are handled while walking the AST.  A second class
        is discovered only after that walk: a stateful helper reached through a
        multi-call-site parent needs the parent's additional call-path indices
        even though the helper has only one textual call.  The late propagation
        pass in ``Analyzer._propagate_call_site_counts`` calls this same helper
        for those inherited variants so the exported count never references a
        TA/fixnan clone that was not actually declared.

        ``reuse_existing_owner`` is used only by late propagation.  A
        range-widened parent may already have materialized the default
        ``{member}_cs{idx}`` clone for the borrowed callee site; when that clone
        belongs to the parent currently being propagated, it is the desired
        call-path state and must be reused rather than duplicated under a
        disambiguated-but-unused name. ``reuse_existing_targets`` maps each
        source TA identity to the immediate parent's already-resolved target;
        this extends the proof through another borrowed layer even when clone
        name collisions forced a disambiguating suffix.
        """
        func_def = self._func_defs.get(func_name)
        method_info = None
        if func_def is None:
            method_info = next(
                (
                    info
                    for info in self._func_infos
                    if info.name == func_name
                    and getattr(info, "is_udt_method", False)
                ),
                None,
            )
            if method_info is not None:
                func_def = method_info.node
        if func_def is None:
            self._error(
                f"Cannot materialize callable state for unknown function '{func_name}'.",
                node.loc,
            )
            return {}

        selected_ta_indices: dict[int, int] = {}

        param_arg_map: dict[str, str] = {}
        positional_args = list(node.args)
        if (
            method_info is not None
            and isinstance(node.callee, MemberAccess)
        ):
            positional_args.insert(0, node.callee.object)
        for p_idx, param_name in enumerate(func_def.params):
            if p_idx < len(positional_args):
                param_arg_map[param_name] = self._expr_to_str(
                    positional_args[p_idx]
                )
            elif param_name in node.kwargs:
                param_arg_map[param_name] = self._expr_to_str(
                    node.kwargs[param_name]
                )

        if func_name in self._func_ta_ranges:
            start, end = self._func_ta_ranges[func_name]
            site_indices = (
                list(ta_site_indices)
                if ta_site_indices is not None
                else list(self._func_ta_indices.get(func_name, ()))
            )
            if ta_site_indices is None and not site_indices:
                site_indices = list(range(start, end))
            func_ctor_templates = self._func_ta_ctor_args.setdefault(
                func_name, {}
            )

            # Map local derived length variables back to expressions over the
            # function's parameters before substituting call-site arguments.
            local_defs = self._func_local_length_defs(func_def)

            def _subst_params(arg: str, pmap: dict[str, str]) -> str:
                import re
                result = arg
                for param, value in sorted(
                        pmap.items(), key=lambda item: len(item[0]), reverse=True):
                    result = re.sub(rf'\b{re.escape(param)}\b', value, result)
                return result

            def _expand_locals(arg: str) -> str:
                import re
                if not local_defs:
                    return arg
                for _ in range(32):
                    def _rep(match: re.Match) -> str:
                        name = match.group(0)
                        if name in local_defs:
                            return "(" + local_defs[name] + ")"
                        return name
                    expanded = re.sub(r"[A-Za-z_][A-Za-z_0-9]*", _rep, arg)
                    if expanded == arg:
                        break
                    arg = expanded
                return arg

            import re as _re
            enclosing_params: set[str] = set()
            for names in self._enclosing_func_params:
                enclosing_params |= names

            if cs_idx == 0:
                # cs0 owns the source-level sites. Preserve their parameterized
                # ctor args for every later direct or inherited clone.
                for i in site_indices:
                    site = self._ta_call_sites[i]
                    if not hasattr(site, '_orig_ctor_args'):
                        site._orig_ctor_args = [
                            _expand_locals(arg) for arg in site.ctor_args
                        ]
                    # Each callable owns its own view of a borrowed site's
                    # constructor expression. Expand locals in that owner view
                    # before substituting actual parameters; a template first
                    # recorded at definition time can still contain a local
                    # alias such as ``effectiveLen``.
                    func_ctor_templates[i] = [
                        _expand_locals(arg)
                        for arg in func_ctor_templates.get(
                            i, site._orig_ctor_args
                        )
                    ]
                    site.ctor_args = [
                        _subst_params(arg, param_arg_map)
                        for arg in func_ctor_templates[i]
                    ]
                    selected_ta_indices[i] = i
                    if enclosing_params and self._nested_ta_touched is not None:
                        for arg in site.ctor_args:
                            tokens = set(_re.findall(
                                r"[A-Za-z_][A-Za-z_0-9]*", arg))
                            if tokens & enclosing_params:
                                if self._enclosing_func_names:
                                    caller = self._enclosing_func_names[-1]
                                    self._func_ta_ctor_args.setdefault(
                                        caller, {}
                                    )[i] = list(site.ctor_args)
                                break
            else:
                clone_name_map: dict[str, str] = {}
                for i in site_indices:
                    orig = self._ta_call_sites[i]
                    if not hasattr(orig, '_orig_ctor_args'):
                        orig._orig_ctor_args = [
                            _expand_locals(arg) for arg in orig.ctor_args
                        ]
                    func_ctor_templates[i] = [
                        _expand_locals(arg)
                        for arg in func_ctor_templates.get(
                            i, orig._orig_ctor_args
                        )
                    ]
                    orig_args = func_ctor_templates[i]
                    resolved_ctor = [
                        _subst_params(arg, param_arg_map) for arg in orig_args
                    ]
                    reuse_target = (
                        reuse_existing_targets.get(i)
                        if reuse_existing_targets is not None
                        else None
                    )
                    if reuse_target is not None:
                        # The parent's active clone already resolved this
                        # exact source identity through the next outer call
                        # boundary. Reuse it directly; do not overwrite its
                        # concrete constructor with this edge's still-local
                        # parameter spelling.
                        selected_ta_indices[i] = reuse_target
                        target_name = self._ta_call_sites[
                            reuse_target
                        ].member_name
                        default_name = f"{orig.member_name}_cs{cs_idx}"
                        if target_name != default_name:
                            clone_name_map[orig.member_name] = target_name
                        continue
                    existing_target = self._func_ta_call_targets.get(
                        (id(node), cs_idx), {}
                    ).get(i)
                    if existing_target is not None:
                        self._ta_call_sites[existing_target].ctor_args = resolved_ctor
                        selected_ta_indices[i] = existing_target
                        continue
                    clone_name = f"{orig.member_name}_cs{cs_idx}"
                    existing_pair = next(
                        (
                            (index, site)
                            for index, site in enumerate(self._ta_call_sites)
                            if site.member_name == clone_name
                        ),
                        None,
                    )
                    if (
                        existing_pair is not None
                        and (
                            (
                                reuse_existing_owner is not None
                                and existing_pair[1].owner_func
                                == reuse_existing_owner
                            )
                        )
                    ):
                        # The active parent's widened range already made the
                        # exact member this inherited callee variant needs.
                        selected_ta_indices[i] = existing_pair[0]
                        continue
                    if clone_name in self._ta_member_names:
                        base = clone_name
                        suffix = 2
                        while clone_name in self._ta_member_names:
                            clone_name = f"{base}_u{suffix}"
                            suffix += 1
                        clone_name_map[orig.member_name] = clone_name
                    cloned = TACallSite(
                        member_name=clone_name,
                        class_name=orig.class_name,
                        ctor_args=resolved_ctor,
                        compute_args=orig.compute_args[:],
                        returns_tuple=orig.returns_tuple,
                        node=orig.node,
                        is_static=orig.is_static,
                        owner_func=func_name,
                    )
                    selected_ta_indices[i] = len(self._ta_call_sites)
                    self._ta_call_sites.append(cloned)
                    self._ta_member_names.add(clone_name)
                if clone_name_map:
                    self._func_cs_ta_clone_names[(func_name, cs_idx)] = clone_name_map

            # A nested stateful helper belongs to the enclosing callable's
            # call path even when its constructor has no forwarded parameters
            # (``ta.change`` is the canonical example). Constructor-template
            # forwarding above is intentionally narrower: it only rewrites
            # expressions that reference enclosing parameters. Keep state
            # ownership independent from that substitution test so every
            # written parent call site receives its exact borrowed TA targets.
            if self._nested_ta_touched is not None:
                self._nested_ta_touched.update(selected_ta_indices.values())

            call_targets = self._func_ta_call_targets.setdefault(
                (id(node), cs_idx), {}
            )
            call_targets.update(selected_ta_indices)
            call_templates = self._func_ta_call_templates.setdefault(id(node), {})
            for index in site_indices:
                template = func_ctor_templates.get(index)
                if template is not None:
                    call_templates[index] = tuple(template)

        # fixnan is stateful for the same reason as a rolling TA reducer: each
        # emitted function variant needs its own previous-value member.
        fn_indices = self._func_fixnan_indices.get(func_name, [])
        if materialize_fixnan and cs_idx > 0 and fn_indices:
            clone_map: dict[str, str] = {}
            for fi in fn_indices:
                orig = self._fixnan_sites[fi]
                clone_name = f"{orig.member_name}_cs{cs_idx}"
                existing = next(
                    (site for site in self._fixnan_sites
                     if site.member_name == clone_name),
                    None,
                )
                if (reuse_existing_owner is not None
                        and existing is not None
                        and existing.owner_func == reuse_existing_owner):
                    continue
                if clone_name in self._fixnan_member_names:
                    base = clone_name
                    suffix = 2
                    while clone_name in self._fixnan_member_names:
                        clone_name = f"{base}_u{suffix}"
                        suffix += 1
                    clone_map[orig.member_name] = clone_name
                cloned = FixnanCallSite(
                    member_name=clone_name,
                    pine_type=orig.pine_type,
                    node=orig.node,
                    owner_func=func_name,
                )
                self._fixnan_sites.append(cloned)
                self._fixnan_member_names.add(clone_name)
            if clone_map:
                self._func_cs_fixnan_clone_names[(func_name, cs_idx)] = clone_map

        return selected_ta_indices

    def _handle_user_func_call(self, func_name: str, node: FuncCall) -> PineType:
        """Handle calls to user-defined functions."""
        func_def = self._func_defs[func_name]

        # Visit every supplied argument in Pine source order, then bind both
        # positional and keyword forms into declaration order.  The old
        # positional-only path skipped kwargs entirely, hiding nested calls and
        # leaving deferred map-history validation without a concrete TypeSpec.
        bound_args = self._bind_callable_args(node, list(func_def.params))
        visited_types: dict[int, PineType] = {}
        for arg in node.args:
            visited_types[id(arg)] = self._visit(arg)
        for arg in node.kwargs.values():
            visited_types[id(arg)] = self._visit(arg)

        param_types = [
            visited_types.get(id(arg), PineType.UNKNOWN)
            if arg is not None
            else PineType.UNKNOWN
            for arg in bound_args
        ]
        declared_specs = list(
            self._func_param_type_specs.get(func_name, ())
        )
        effective_param_types = [
            (
                self._primitive_pine_type_from_spec(declared_specs[index])
                if index < len(declared_specs)
                and self._primitive_pine_type_from_spec(
                    declared_specs[index]
                ) != PineType.UNKNOWN
                else param_type
            )
            for index, param_type in enumerate(param_types)
        ]
        self._callable_bound_param_types_by_node[id(node)] = list(
            effective_param_types
        )

        # Per-param TypeSpec: declared hints are authoritative; for untyped
        # params, infer from this call site's argument.  Validate deferred
        # history receivers before series propagation/cloning can reinterpret
        # a map handle (or map-bearing UDT) as ``Series<double>``.
        param_specs = list(
            getattr(self, "_func_param_type_specs", {}).get(func_name)
            or self._param_type_specs_from_def(func_def)
        )
        arg_specs = [
            self._type_spec_from_expr(arg) if arg is not None else None
            for arg in bound_args
        ]
        for i in range(len(param_specs)):
            if param_specs[i] is None and i < len(arg_specs):
                param_specs[i] = arg_specs[i]
        self._record_deferred_param_call_edge(
            node,
            func_name,
            list(func_def.params),
            bound_args,
        )
        self._validate_deferred_param_history_refs(
            func_name,
            {
                name: spec
                for name, spec in zip(func_def.params, param_specs)
            },
        )

        # Determine return type: re-analyze the function body with known param types
        # For now, use the cached return type from initial analysis
        return_type = self._func_return_types.get(func_name, PineType.FLOAT)

        # If the return type was UNKNOWN or VOID, infer it ONLY when the body
        # is a single bare identifier that returns a parameter directly
        # (``f(s) => s``). Inferring from params for arbitrary bodies misfires
        # when a function merely HAS a string param but returns something else
        # (e.g. ``getLineStyle(s) => switch s ... => line.style_solid`` or a
        # body ending in ``label.new(...)``). Other cases rely on the cached
        # body type plus udt_return_type / tuple inference.
        if return_type in (PineType.UNKNOWN, PineType.VOID):
            if (func_def.is_single_expr and func_def.body
                    and isinstance(func_def.body[0], ExprStmt)
                    and isinstance(func_def.body[0].expr, Identifier)):
                ret_name = func_def.body[0].expr.name
                for idx, pname in enumerate(func_def.params):
                    if pname == ret_name and idx < len(param_types):
                        pt = param_types[idx]
                        if pt == PineType.STRING:
                            return_type = PineType.STRING
                        elif pt == PineType.INT:
                            return_type = PineType.INT
                        elif pt == PineType.FLOAT:
                            return_type = PineType.FLOAT
                        break

        # History and other primitive operations preserve an untyped
        # parameter's family independently at each written call.  Do this
        # before the legacy FuncInfo merge so the first call cannot dictate
        # every later call's return type.
        return_type = self._callsite_callable_return_type(
            func_def, effective_param_types, return_type
        )

        # If this function has series params, ensure bar-field arguments
        # passed at the call site are registered as series_bar_fields so that
        # the codegen can create Series<double> members for them.
        func_sv = self._func_series_vars.get(func_name, set())
        if func_sv:
            for p_idx, param_name in enumerate(func_def.params):
                if param_name in func_sv and p_idx < len(bound_args):
                    arg = bound_args[p_idx]
                    if arg is None:
                        continue
                    if isinstance(arg, Identifier) and arg.name in BAR_FIELDS:
                        self._series_bar_fields.add(arg.name)
                    elif isinstance(arg, Identifier):
                        sym = self._symbols.resolve(arg.name)
                        spec = getattr(sym, "type_spec", None) if sym is not None else None
                        if spec is not None and spec.kind in ("array", "map", "matrix"):
                            continue
                        if sym is not None:
                            sym.is_series = True
                            if sym.scope and sym.scope.startswith("func_"):
                                caller_name = sym.scope[5:]
                                self._func_series_vars.setdefault(caller_name, set()).add(arg.name)
                            else:
                                # Keep the exact declaration/member registries
                                # in lockstep with the legacy raw-name series
                                # promotion.  Codegen uses those identities to
                                # distinguish the real global binding from a
                                # same-named lexical scalar tombstone.
                                exact_member = getattr(
                                    sym, "_pf_var_member_name", None
                                )
                                if exact_member is not None:
                                    self._series_var_members.add(exact_member)
                                decl_node_id = getattr(
                                    sym, "_pf_decl_node_id", None
                                )
                                if decl_node_id is not None:
                                    self._series_decl_nodes.add(decl_node_id)
                                    binding_name = getattr(
                                        sym,
                                        "_pf_decl_binding_name",
                                        arg.name,
                                    )
                                    self._series_decl_bindings.add(
                                        (decl_node_id, binding_name)
                                    )
                                self._series_vars.add(arg.name)

        # Per-call-site cloning: TA, series/var, and fixnan state all advance
        # across bars/calls and therefore require isolated UDF variants.
        has_ta = func_name in self._func_ta_ranges
        has_series = func_name in self._func_series_vars or func_name in self._func_var_members
        has_fixnan = func_name in self._func_fixnan_indices
        if has_ta or has_series or has_fixnan:
            existing_site = self._func_call_cs_map.get(id(node))
            if existing_site is None or existing_site[0] != func_name:
                # Type/return inference may revisit the same FuncCall AST node
                # several times.  It is still one Pine textual call site, not
                # a new state instance on every analyzer pass.  Real caller
                # clones are expanded later by _propagate_call_site_counts().
                cs_idx = self._func_call_site_count.get(func_name, 0)
                self._func_call_site_count[func_name] = cs_idx + 1
                self._func_call_cs_map[id(node)] = (func_name, cs_idx)
                self._materialize_user_func_call_site_state(
                    func_name, cs_idx, node
                )
            callsite = self._func_call_cs_map.get(id(node))
            if callsite is not None and callsite[0] == func_name:
                key = (func_name, callsite[1])
                self._func_callsite_param_types[key] = list(
                    effective_param_types
                )
                self._func_callsite_return_types[key] = return_type

        # Create or update FuncInfo
        is_tuple = self._func_returns_tuple.get(func_name, False)
        tuple_count = self._func_tuple_element_count.get(func_name, 0)
        # Forward UDT-return inference (set in _visit_FuncDef) so codegen can
        # emit the struct return type. Probe: udt-method-probe-20.
        udt_ret = self._func_udt_return_types.get(func_name)
        existing = [fi for fi in self._func_infos if fi.name == func_name]

        # A direct terminal map call on an untyped parameter cannot be typed
        # while the function definition is first visited: its map TypeSpec is
        # learned only here, from the call-site argument. Re-run only the
        # terminal-map classifier with those established parameter specs; do
        # not re-analyze the body or participate in general return inference.
        effective_param_specs = list(param_specs)
        if existing and existing[0].param_type_specs:
            for i, spec in enumerate(existing[0].param_type_specs):
                if i < len(effective_param_specs) and spec is not None:
                    effective_param_specs[i] = spec
        terminal_map_return = self._terminal_map_call_return(
            self._direct_terminal_return_expr(func_def),
            {
                name: spec
                for name, spec in zip(func_def.params, effective_param_specs)
            },
        )
        ret_spec = getattr(self, "_func_return_type_specs", {}).get(func_name)
        terminal_expr = self._direct_terminal_return_expr(func_def)
        terminal_selection_spec = self._terminal_map_selection_return_spec(
            terminal_expr,
            {
                name: spec
                for name, spec in zip(
                    func_def.params, effective_param_specs
                )
            },
        )
        if (
            terminal_map_return is None
            and isinstance(terminal_expr, Identifier)
            and terminal_expr.name in func_def.params
        ):
            terminal_index = func_def.params.index(terminal_expr.name)
            terminal_identity_spec = (
                effective_param_specs[terminal_index]
                if terminal_index < len(effective_param_specs)
                else None
            )
            if (terminal_identity_spec is not None
                    and terminal_identity_spec.kind == "map"):
                # An untyped identity UDF learns the map handle type from its
                # call site. The handle is returned by value, preserving the
                # backing ID rather than cloning map contents.
                self._func_return_type_specs[func_name] = terminal_identity_spec
                ret_spec = terminal_identity_spec
        if terminal_selection_spec is not None:
            self._func_return_type_specs[func_name] = terminal_selection_spec
            ret_spec = terminal_selection_spec
        if terminal_map_return is not None:
            return_type, inferred_ret_spec = terminal_map_return
            self._func_return_types[func_name] = return_type
            if inferred_ret_spec is not None:
                self._func_return_type_specs[func_name] = inferred_ret_spec
                ret_spec = inferred_ret_spec

        if not existing:
            fi = FuncInfo(
                name=func_name,
                param_types=param_types,
                return_type=return_type,
                node=func_def,
                returns_tuple=is_tuple,
                tuple_element_count=tuple_count,
                udt_return_type=udt_ret,
                param_type_specs=param_specs,
                return_type_spec=ret_spec,
            )
            self._func_infos.append(fi)
        else:
            # Update with better type info if available
            fi = existing[0]
            if terminal_map_return is not None:
                fi.return_type = return_type
                fi.return_type_spec = ret_spec
            elif fi.return_type in (PineType.UNKNOWN, PineType.VOID) and return_type not in (PineType.UNKNOWN, PineType.VOID):
                fi.return_type = return_type
            for i, pt in enumerate(param_types):
                if i < len(fi.param_types) and fi.param_types[i] == PineType.UNKNOWN:
                    fi.param_types[i] = pt
            # Merge per-param TypeSpecs: keep declared hints (authoritative),
            # fill untyped slots from this call site if still unknown.
            if not fi.param_type_specs:
                fi.param_type_specs = list(param_specs)
            else:
                for i in range(len(param_specs)):
                    if i < len(fi.param_type_specs) and fi.param_type_specs[i] is None:
                        fi.param_type_specs[i] = param_specs[i]
            if fi.udt_return_type is None and udt_ret is not None:
                fi.udt_return_type = udt_ret
            if fi.return_type_spec is None and ret_spec is not None:
                fi.return_type_spec = ret_spec

        # A concrete map can arrive only at an outer wrapper's eventual call
        # site, after every nested untyped UDF was initially analyzed with
        # scalar fallbacks.  Propagate the concrete specs through the exact
        # definition-time call edges and infer returns in post-order before
        # codegen snapshots the FuncInfo table.
        self._propagate_deferred_map_callable_specs(
            func_name,
            {
                name: spec
                for name, spec in zip(func_def.params, param_specs)
            },
        )

        return return_type
