"""``request.security()`` lowering for the codegen.

This is the most stateful mixin in the ``codegen/`` package. It owns the
~30 helpers that lower Pine ``request.security(...)`` calls into
per-security ``_eval_security_N()`` methods, an ``evaluate_security()``
dispatch, the ``clear_security()`` reset path, and the supporting binding,
TA-variant, and mutable-global rebind machinery.

Mixin contract — the host class (``CodeGen``) must provide the following
attributes (all set by ``CodeGen.__init__`` unless noted):

- ``self.ctx`` (``AnalyzerContext``): symbol table source. Reads
  ``ctx.ast.body``, ``ctx.ta_call_sites``, ``ctx.global_expr_map``,
  ``ctx.func_series_vars``, and ``ctx.global_mutable_infos``.
- ``self._global_mutable_infos`` (``dict[str, MutableInfo]``):
  per-mutable-global metadata captured by the analyzer
  (``is_var``/``is_series``/``pine_type``/``source_stmts``).
- ``self._security_calls`` (``list[dict]``): normalized security-call
  records. Built by this mixin's ``_normalize_security_call``.
- ``self._security_eval_info`` (``list[dict]``): per ``sec_id`` eval
  metadata (``ta_indices``, ``ta_variants``, ``ta_binding_stacks``,
  ``inline_helper_ta_indices``, ``mutable_globals``, ...).
- ``self._security_inline_counter`` (``int``): used by
  ``_security_next_inline_name`` for unique helper temporary names.
- ``self._security_ta_variant_names``
  (``dict[tuple[int, int, tuple], str]``):
  ``(sec_id, ta_idx, signature) -> C++ member name``.
- ``self._security_ohlc_hist_fields_by_sec`` (``dict[int, set[str]]``):
  set in ``CodeGen.generate()`` before ``_emit_security_evaluators`` runs.
- ``self._ta_index_by_site_id`` (``dict[int, int]``): TA call-site
  identity → index in ``ctx.ta_call_sites``.
- ``self._func_names`` (``set[str]``): user-defined function names.
- ``self._func_info_map`` (``dict[str, FuncInfo]``): name -> FuncInfo.

Sibling-mixin methods consumed via ``self``:

- ``self._safe_name`` / ``self._get_target_name`` (``NamingHelper``).
- ``self._series_type_for`` / ``self._type_for_decl`` /
  ``self._infer_cpp_type_for_security_elem`` (``TypeInferer``).
- ``self._get_ta_site`` / ``self._security_ta_compute_args_for_site`` /
  ``self._ta_name_from_site`` (``TaSiteHelper``).
  ``_security_ta_compute_args_for_site`` stays on ``TaSiteHelper`` because
  it is structurally a TA helper that calls back into this mixin via
  ``self._build_security_expr``.
- ``self._merge_ta_call_args`` (``CodeGen.base``): not security-specific,
  kept on base.
- ``self._visit_expr`` (``CodeGen.base``): the fallback expression
  renderer used by ``_build_security_expr``.
- ``self._codegen_error`` (``CodeGen.base``).
- ``self._emit_ta_runtime_reset`` (``CodeGen.base``): called from
  ``_emit_security_evaluators`` to gate the TA reset before the dispatch
  switch.

The mixin avoids importing from ``base.py`` to stay free of cycles; all
tables and types come from ``codegen/tables.py``, ``..ast_nodes``,
``..analyzer``, and ``..symbols``.
"""

from __future__ import annotations

from ..ast_nodes import (
    ASTNode, Assignment, BinOp, BreakStmt, ContinueStmt, ExprStmt, ForStmt,
    ForInStmt, FuncCall, FuncDef, Identifier, IfStmt, MemberAccess, NumberLiteral,
    StringLiteral, Subscript, SwitchStmt, Ternary, TupleAssign, TupleLiteral,
    UnaryOp, VarDecl, WhileStmt,
)
from ..analyzer import (
    FuncInfo, TACallSite, TA_MULTI_CTOR, TA_NO_CTOR, TA_PERIOD_ARG,
)
from .. import signatures as sigs
from ..symbols import PineType
from .tables import MATH_FUNC_MAP, PINE_TYPE_TO_CPP, SECURITY_OHLC_BAR_FIELDS, _merge_kwargs


class SecurityEmitter:
    """Mixin owning ``request.security()`` lowering: evaluators, dispatch,
    rebind/binding/TA-variant machinery, and the per-call helper plan.

    Mixed into ``CodeGen``; not intended to be instantiated standalone."""

    def _resolve_security_tf(self, tf_node, containing_func: str):
        """Resolve a ``request.security`` timeframe argument to ``(tf_str, tf_expr)``.

        ``tf_str`` is a compile-time string literal value; ``tf_expr`` is a runtime
        C++ expression used at evaluator-registration time. Exactly one is non-None
        for a usable tf (both None is acceptable only as an explicit "unknown").

        A function-parameter tf (e.g. ``f(tf) => request.security(sym, tf, ...)``)
        is not visible at class scope (the evaluator is a class method), so it is
        resolved from the function's call sites. A dead-code UDF (never called)
        falls back to the chart timeframe — its evaluator result is never read.
        """
        if isinstance(tf_node, StringLiteral):
            return tf_node.value, None
        if isinstance(tf_node, Identifier):
            name = tf_node.name
            if name in self._timeframe_period_vars:
                return None, "script_tf_"
            if (name in self._known_vars and name not in self._input_backed_vars
                    and isinstance(self._known_vars[name], str)):
                return self._known_vars[name], None
            if name in self._input_backed_vars and name in self._input_var_to_call:
                return None, self._visit_expr(self._input_var_to_call[name])
            # class-scope resolvable (global / input member)?
            if self._ident_is_resolvable(name):
                try:
                    return None, self._visit_expr(tf_node)
                except Exception:
                    pass
            # function-parameter tf -> resolve from the call sites
            if containing_func:
                resolved = self._resolve_param_tf_from_callsites(containing_func, name)
                if resolved is not None:
                    return resolved
            # graceful fallback so transpile does not hard-fail
            return None, "input_tf_"
        # any other expression — visit if it resolves at class scope
        try:
            return None, self._visit_expr(tf_node)
        except Exception:
            return None, "input_tf_"

    def _resolve_param_tf_from_callsites(self, func_name: str, param_name: str):
        """For a ``request.security`` whose tf is function parameter ``param_name``
        of user function ``func_name``, return ``(tf_str, tf_expr)`` resolved from
        the call sites, or None. If every call passes the same literal/member tf,
        that tf is used; mixed timeframes or a never-called (dead-code) function
        fall back to the chart timeframe (``input_tf_``)."""
        fdef = None
        for node in self._walk_ast(self.ctx.ast):
            if isinstance(node, FuncDef) and node.name == func_name:
                fdef = node
                break
        if fdef is None or param_name not in fdef.params:
            return None
        pidx = fdef.params.index(param_name)
        resolved: list = []
        found_call = False
        for node in self._walk_ast(self.ctx.ast):
            if (isinstance(node, FuncCall) and isinstance(node.callee, Identifier)
                    and node.callee.name == func_name):
                found_call = True
                arg = node.args[pidx] if pidx < len(node.args) else None
                if arg is None:
                    continue
                # Resolve the call-site arg (no further containing func — these
                # are global-scope / input args).
                resolved.append(self._resolve_security_tf(arg, ""))
        if not found_call:
            # dead code — evaluator never read; register with chart tf.
            return (None, "input_tf_")
        valid = [r for r in resolved if r is not None]
        if not valid:
            return (None, "input_tf_")
        strs = {r[0] for r in valid}
        exprs = {r[1] for r in valid}
        if len(strs) == 1 and next(iter(strs), None) is not None:
            return (next(iter(strs)), None)
        if len(exprs) == 1 and next(iter(exprs), None) is not None:
            return (None, next(iter(exprs)))
        # mixed timeframes across call sites — cannot pick one statically
        return (None, "input_tf_")

    def _normalize_security_call(self, item) -> dict:
        if hasattr(item, "sec_id"):
            return {
                "sec_id": item.sec_id,
                "tf_node": item.timeframe,
                "expr_node": item.expression,
                "returns_tuple": item.returns_tuple,
                "tuple_size": item.tuple_size,
                "gaps_node": item.gaps,
                "lookahead_node": item.lookahead,
                "ta_range": item.ta_range,
                "heikinashi": bool(getattr(item, "heikinashi", False)),
                "depends_on_mutable_globals": bool(getattr(item, "depends_on_mutable_globals", False)),
                "mutable_globals": list(getattr(item, "mutable_globals", ()) or ()),
                "is_lower_tf_array": bool(getattr(item, "is_lower_tf_array", False)),
                "containing_func": getattr(item, "containing_func", "") or "",
                "callsite_idx": getattr(item, "callsite_idx", None),
            }
        return {
            "sec_id": item[0],
            "tf_node": item[1] if len(item) > 1 else None,
            "expr_node": item[2] if len(item) > 2 else None,
            "returns_tuple": item[3] if len(item) > 3 else False,
            "tuple_size": item[4] if len(item) > 4 else 0,
            "gaps_node": item[5] if len(item) > 5 else None,
            "lookahead_node": item[6] if len(item) > 6 else None,
            "ta_range": item[7] if len(item) > 7 else None,
            "depends_on_mutable_globals": False,
            "mutable_globals": [],
            "is_lower_tf_array": False,
            "containing_func": "",
            "callsite_idx": None,
        }

    def _security_state_name(self, sec_id: int, name: str) -> str:
        return f"_sec{sec_id}_{self._safe_name(name)}"

    def _security_init_flag_name(self, sec_id: int, name: str) -> str:
        return f"{self._security_state_name(sec_id, name)}_initialized"

    def _security_cpp_type_for_mutable(self, name: str, info) -> str:
        if getattr(info, "is_series", False):
            return self._series_type_for(name)
        return PINE_TYPE_TO_CPP.get(getattr(info, "pine_type", PineType.FLOAT), "double")

    def _security_relevant_top_level_stmts(self, mutable_globals: list[str]) -> list[ASTNode]:
        if not mutable_globals:
            return []
        source_ids: set[int] = set()
        for name in mutable_globals:
            info = self._global_mutable_infos.get(name)
            if info is None:
                continue
            for stmt in getattr(info, "source_stmts", []) or []:
                source_ids.add(id(stmt))
        return [stmt for stmt in self.ctx.ast.body if id(stmt) in source_ids]

    def _rewrite_security_cpp(
        self,
        cpp: str,
        sec_id: int,
        security_mutable_names: set[str],
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
    ) -> str:
        import re

        result = cpp.replace("current_bar_.", "bar.")
        for name in sorted(security_mutable_names, key=len, reverse=True):
            info = self._global_mutable_infos.get(name)
            if info is None:
                continue
            safe = self._safe_name(name)
            state = self._security_state_name(sec_id, name)
            if getattr(info, "is_series", False):
                result = re.sub(rf"\b{re.escape(safe)}\b(?=\s*\[)", state, result)
                result = re.sub(rf"\b{re.escape(safe)}\b(?!\s*\[)", f"{state}[0]", result)
            else:
                result = re.sub(rf"\b{re.escape(safe)}\b", state, result)
        if helper_binding_stack:
            for frame in helper_binding_stack:
                for name, bound in frame.items():
                    if not isinstance(bound, str):
                        continue
                    series_name = self._security_series_binding_target(bound)
                    if series_name is not None:
                        result = re.sub(
                            rf"\b{re.escape(name)}\b(?=\s*\[)",
                            f'_security_helper_series_["{series_name}"]',
                            result,
                        )
                        result = re.sub(
                            rf"\b{re.escape(name)}\b(?!\s*\[)",
                            f'_security_helper_series_["{series_name}"][0]',
                            result,
                        )
                    else:
                        result = re.sub(rf"\b{re.escape(name)}\b", bound, result)
        return result

    def _security_lookup_helper_binding(
        self,
        name: str,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None,
    ):
        if not helper_binding_stack:
            return None
        for frame in reversed(helper_binding_stack):
            if name not in frame:
                continue
            bound = frame[name]
            if isinstance(bound, Identifier) and bound.name == name:
                continue
            return bound
        return None

    def _literal_int_for_security_index(self, node) -> int | None:
        """Integer index for OHLC[ n ] inside request.security (must be literal)."""
        if isinstance(node, NumberLiteral):
            v = node.value
            if isinstance(v, bool):
                return None
            if float(v) == int(v):
                return int(v)
            return None
        if (
            isinstance(node, UnaryOp)
            and node.op == "-"
            and isinstance(node.operand, NumberLiteral)
        ):
            v = -node.operand.value
            if float(v) == int(v):
                return int(v)
            return None
        return None

    def _collect_security_ohlc_hist_fields(self, node) -> set[str]:
        """Which OHLC fields need HTF history (subscript index >= 1) for this expression."""
        out: set[str] = set()

        def walk(n):
            if n is None:
                return
            if isinstance(n, Subscript) and isinstance(n.object, Identifier):
                if n.object.name in SECURITY_OHLC_BAR_FIELDS:
                    idx = self._literal_int_for_security_index(n.index)
                    # high[0] uses current HTF `bar`; high[k>=1] reads prior completed HTF
                    # bars from Series history (filled before push in _eval_security_*).
                    if idx is not None and idx >= 1:
                        out.add(n.object.name)
            if isinstance(n, (list, tuple)):
                for x in n:
                    walk(x)
                return
            for _k, v in getattr(n, "__dict__", {}).items():
                if isinstance(v, ASTNode):
                    walk(v)
                elif isinstance(v, (list, tuple)):
                    for x in v:
                        if isinstance(x, ASTNode):
                            walk(x)

        walk(node)
        return out

    def _collect_security_ohlc_hist_fields_for_call(self, item: dict) -> set[str]:
        """Collect HTF OHLC history needed by a security expression and any
        mutable-global rebinds replayed inside that security evaluator."""
        fields = self._collect_security_ohlc_hist_fields(item.get("expr_node"))
        for name in item.get("mutable_globals", []) or []:
            info = self._global_mutable_infos.get(name)
            if info is None:
                continue
            for stmt in getattr(info, "source_stmts", []) or []:
                fields |= self._collect_security_ohlc_hist_fields(stmt)
        return fields

    def _security_ohlc_hist_series_cpp(self, sec_id: int, field: str) -> str:
        return f"_sec{sec_id}_hist_{field}"

    @staticmethod
    def _security_tuple_result_default(cpp_type: str, tuple_size: int) -> str:
        vals = ", ".join("na<double>()" for _ in range(max(0, tuple_size)))
        return f"{cpp_type}{{{vals}}}"

    def _collect_security_ta_hist_indices(self, node) -> set[int]:
        """Which security TA call-site indices need HTF history (subscript index >= 1).

        ``request.security(..., ta.ema(close, 55)[1], ...)`` reads a *confirmed*
        HTF TA value at a past-bar offset. The inner TA call runs in the security
        (HTF) context and commits one value per COMPLETED HTF bar; offsets read a
        per-site ``Series`` filled (gated on ``is_complete``) in
        ``_eval_security_N``. Mirrors ``_collect_security_ohlc_hist_fields`` for
        OHLC offsets. Offset 0 reuses the current committed value (``_secval_*``)
        and needs no Series, so only index >= 1 registers here."""
        out: set[int] = set()
        global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}

        def resolve_ta_site(obj, resolving: set[str] | None = None):
            """_get_ta_site only matches the literal ta.* FuncCall node by
            identity; fall back through global_expr_map for an indirect
            binding (``v = ta.ema(close, 55)`` then ``...v[1]...``), mirroring
            the same fallback in _build_security_expr's Subscript branch."""
            site = self._get_ta_site(obj)
            if site is not None:
                return site
            if isinstance(obj, Identifier):
                if resolving is None:
                    resolving = set()
                if obj.name in global_expr_map and obj.name not in resolving:
                    resolving.add(obj.name)
                    return resolve_ta_site(global_expr_map[obj.name], resolving)
            return None

        def walk(n):
            if n is None:
                return
            if isinstance(n, Subscript):
                site = resolve_ta_site(n.object)
                if site is not None:
                    idx_lit = self._literal_int_for_security_index(n.index)
                    if idx_lit is not None and idx_lit >= 1:
                        site_idx = self._ta_index_by_site_id.get(id(site))
                        if site_idx is not None:
                            out.add(site_idx)
            if isinstance(n, (list, tuple)):
                for x in n:
                    walk(x)
                return
            for _k, v in getattr(n, "__dict__", {}).items():
                if isinstance(v, ASTNode):
                    walk(v)
                elif isinstance(v, (list, tuple)):
                    for x in v:
                        if isinstance(x, ASTNode):
                            walk(x)

        walk(node)
        return out

    def _security_ta_hist_series_cpp(self, member_name: str) -> str:
        """Per-(sec, site) ``Series<double>`` backing ``ta.<fn>(...)[k>=1]`` HTF history."""
        return f"{member_name}_hist"

    def _security_ta_hist_series_names(self, sec_id: int) -> list[str]:
        """Hist Series names for every security TA site (and variant) read at an
        offset >= 1 in sec ``sec_id``."""
        info = self._security_eval_info[sec_id]
        names: list[str] = []
        for idx in sorted(self._security_ta_hist_idx_by_sec.get(sec_id, ())):
            for variant in (info.get("ta_variants") or {}).get(idx, []):
                names.append(self._security_ta_hist_series_cpp(variant["member_name"]))
        return names

    def _collect_security_expr_hist_subscripts(
        self, node, resolving: set[str] | None = None
    ) -> list[Subscript]:
        """Subscripted helper-call results needing security-context history."""
        if node is None:
            return []
        if resolving is None:
            resolving = set()

        out: list[Subscript] = []
        seen: set[int] = set()

        def add(n: Subscript) -> None:
            key = id(n)
            if key not in seen:
                seen.add(key)
                out.append(n)

        def walk(n) -> None:
            if n is None:
                return
            if isinstance(n, Identifier):
                global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
                if n.name in global_expr_map and n.name not in resolving:
                    resolving.add(n.name)
                    walk(global_expr_map[n.name])
                    resolving.remove(n.name)
                return
            if (
                isinstance(n, Subscript)
                and isinstance(n.object, FuncCall)
                and self._get_ta_site(n.object) is None
            ):
                add(n)
            if isinstance(n, (list, tuple)):
                for x in n:
                    walk(x)
                return
            for _k, v in getattr(n, "__dict__", {}).items():
                if isinstance(v, ASTNode):
                    walk(v)
                elif isinstance(v, (list, tuple)):
                    for x in v:
                        if isinstance(x, ASTNode):
                            walk(x)

        walk(node)
        return out

    def _security_expr_hist_series_names(self, sec_id: int) -> list[str]:
        names = []
        for (sid, _node_id), meta in sorted(self._security_expr_hist_by_node.items()):
            if sid == sec_id:
                names.append(meta["name"])
        return names

    def _emit_security_expr_hist_members(
        self, sec_id: int, expr_node, lines: list[str], mbb_suffix: str
    ) -> None:
        for idx, node in enumerate(self._collect_security_expr_hist_subscripts(expr_node)):
            cpp_t = self._infer_type(node.object)
            if cpp_t not in ("double", "int", "bool"):
                cpp_t = "double"
            name = f"_sec{sec_id}_expr_hist_{idx}"
            self._security_expr_hist_by_node[(sec_id, id(node))] = {
                "name": name,
                "type": cpp_t,
            }
            lines.append(f"    Series<{cpp_t}> {name}{mbb_suffix};")

    def _build_security_math_call(
        self,
        sec_id: int,
        func_name: str,
        node: FuncCall,
        ta_range,
        ta_results: dict,
        resolving: set[str],
        security_mutable_names: set[str],
        helper_binding_stack: tuple[dict[str, ASTNode], ...],
        emitted_lines: list[str] | None,
    ) -> str:
        visit = lambda arg: self._build_security_expr(
            sec_id,
            arg,
            ta_range,
            ta_results,
            resolving,
            security_mutable_names,
            helper_binding_stack,
            emitted_lines,
        )
        args = _merge_kwargs(
            node.args,
            node.kwargs,
            sigs.get_param_names("math", func_name),
            visit,
        )
        if func_name == "round" and len(args) == 2:
            return f"(std::round({args[0]} * std::pow(10.0, {args[1]})) / std::pow(10.0, {args[1]}))"
        if func_name == "round_to_mintick":
            x = args[0] if args else "0.0"
            return f"round_to_mintick({x})"
        if func_name == "todegrees":
            x = args[0] if args else "0.0"
            return f"({x} * 180.0 / M_PI)"
        if func_name == "toradians":
            x = args[0] if args else "0.0"
            return f"({x} * M_PI / 180.0)"
        if func_name == "random":
            lo = args[0] if len(args) > 0 else "0.0"
            hi = args[1] if len(args) > 1 else "1.0"
            seed = args[2] if len(args) > 2 else "0"
            call_site = self._random_call_counter
            self._random_call_counter += 1
            return f"pine_random({lo}, {call_site}u, {hi}, (uint32_t)({seed}), bar_index_)"
        if func_name == "avg" and len(args) > 2:
            sum_expr = " + ".join(f"(double)({a})" for a in args)
            return f"(({sum_expr}) / {len(args)}.0)"
        if func_name == "max" and len(args) > 2:
            result = f"std::max((double)({args[0]}), (double)({args[1]}))"
            for a in args[2:]:
                result = f"std::max({result}, (double)({a}))"
            return result
        if func_name == "min" and len(args) > 2:
            result = f"std::min((double)({args[0]}), (double)({args[1]}))"
            for a in args[2:]:
                result = f"std::min({result}, (double)({a}))"
            return result
        if func_name in MATH_FUNC_MAP:
            mapped = MATH_FUNC_MAP[func_name]
            if "{0}" in mapped:
                return mapped.format(*args)
            if func_name in ("min", "max") and len(args) == 2:
                return f"{mapped}((double)({args[0]}), (double)({args[1]}))"
            return f"{mapped}({', '.join(args)})"
        return f"0.0 /* unsupported: math.{func_name} */"

    def _security_timeframe_expr(self, sec_id: int) -> str:
        """C++ expression for the timeframe of a request.security evaluator."""
        info = self._security_eval_info[sec_id]
        if info.get("tf"):
            return f'"{info["tf"]}"'
        if info.get("tf_expr"):
            return info["tf_expr"]
        return "input_tf_"

    def _build_security_timeframe_member(self, sec_id: int, member: str) -> str | None:
        """Lower timeframe.* reads inside request.security to the requested TF."""
        tf = self._security_timeframe_expr(sec_id)
        if member == "period":
            return tf
        if member == "main_period":
            return "main_period()"
        if member == "multiplier":
            return f"tf_multiplier({tf})"
        if member == "isintraday":
            return f"tf_is_intraday({tf})"
        if member == "isminutes":
            return f"(tf_is_intraday({tf}) && !tf_is_seconds({tf}))"
        if member == "isdaily":
            return f"tf_is_daily({tf})"
        if member == "isweekly":
            return f"tf_is_weekly({tf})"
        if member == "ismonthly":
            return f"tf_is_monthly({tf})"
        if member == "isdwm":
            return f"(tf_is_daily({tf}) || tf_is_weekly({tf}) || tf_is_monthly({tf}))"
        if member == "isseconds":
            return f"tf_is_seconds({tf})"
        if member == "in_seconds":
            return f"tf_to_seconds({tf})"
        if member == "isticks":
            return "false"
        return None

    @staticmethod
    def _security_series_binding(series_name: str) -> str:
        return f"@series:{series_name}"

    @staticmethod
    def _security_series_binding_target(binding: str) -> str | None:
        if isinstance(binding, str) and binding.startswith("@series:"):
            return binding[len("@series:") :]
        return None

    def _emit_security_linear_helper_call(
        self,
        sec_id: int,
        plan: dict,
        ta_results: dict,
        security_mutable_names: set[str],
        lines: list[str],
        resolving: set[str] | None = None,
    ) -> str:
        if plan["mode"] != "linear":
            self._codegen_error(
                plan["func_info"].node,
                "Internal security helper emission requested for a non-linear helper plan",
            )
        local_cpp_bindings: dict[str, str] = {}
        runtime_stack = plan["binding_stack"] + (local_cpp_bindings,)
        local_series_names = set(plan.get("local_series_names", ()))

        def _series_expr(binding_name: str, index_expr: str) -> str:
            return f'_security_helper_series_["{binding_name}"][{index_expr}]'

        def emit_stmt(stmt: ASTNode, active_bindings: dict[str, str], indent: int) -> None:
            pad = "    " * indent
            runtime_stack_local = plan["binding_stack"] + (active_bindings,)

            if isinstance(stmt, VarDecl):
                if stmt.name in local_series_names:
                    binding = active_bindings.get(stmt.name)
                    if binding is None:
                        binding = self._security_series_binding(
                            self._security_next_inline_name(sec_id, plan["func_info"].name, stmt.name)
                        )
                    series_name = self._security_series_binding_target(binding)
                    assert series_name is not None
                    expr_cpp = self._build_security_expr(
                        sec_id,
                        stmt.value,
                        None,
                        ta_results,
                        resolving,
                        security_mutable_names,
                        runtime_stack_local,
                        lines,
                    )
                    active_bindings[stmt.name] = binding
                    lines.append(f'{pad}if (_security_helper_series_["{series_name}"].size() == 0) {{')
                    lines.append(f'{pad}    _security_helper_series_["{series_name}"].push({expr_cpp});')
                    lines.append(f'{pad}}} else if (security_series_slot_is_new({sec_id})) {{')
                    lines.append(f'{pad}    _security_helper_series_["{series_name}"].push({expr_cpp});')
                    lines.append(f'{pad}}} else {{')
                    lines.append(f'{pad}    _security_helper_series_["{series_name}"].update({expr_cpp});')
                    lines.append(f'{pad}}}')
                    return

                local_name = active_bindings.get(stmt.name)
                if local_name is None:
                    local_name = self._security_next_inline_name(
                        sec_id,
                        plan["func_info"].name,
                        stmt.name,
                    )
                    cpp_type = self._type_for_decl(stmt)
                    expr_cpp = self._build_security_expr(
                        sec_id,
                        stmt.value,
                        None,
                        ta_results,
                        resolving,
                        security_mutable_names,
                        runtime_stack_local,
                        lines,
                    )
                    active_bindings[stmt.name] = local_name
                    lines.append(f"{pad}{cpp_type} {local_name} = {expr_cpp};")
                else:
                    expr_cpp = self._build_security_expr(
                        sec_id,
                        stmt.value,
                        None,
                        ta_results,
                        resolving,
                        security_mutable_names,
                        runtime_stack_local,
                        lines,
                    )
                    lines.append(f"{pad}{local_name} = {expr_cpp};")
                return

            if isinstance(stmt, Assignment):
                target_name = self._get_target_name(stmt.target)
                if target_name is None:
                    self._codegen_error(
                        stmt,
                        "request.security multi-statement helpers may only assign to local identifier temporaries",
                    )
                binding = active_bindings.get(target_name)
                if binding is None:
                    self._codegen_error(
                        stmt,
                        "request.security multi-statement helper assignment target must be declared before use",
                    )
                expr_cpp = self._build_security_expr(
                    sec_id,
                    stmt.value,
                    None,
                    ta_results,
                    resolving,
                    security_mutable_names,
                    runtime_stack_local,
                    lines,
                )
                series_name = self._security_series_binding_target(binding)
                if series_name is not None:
                    if stmt.op == ":=":
                        lines.append(f'{pad}_security_helper_series_["{series_name}"].update({expr_cpp});')
                    else:
                        op_char = stmt.op[0]
                        lines.append(
                            f'{pad}_security_helper_series_["{series_name}"].update('
                            f'{_series_expr(series_name, "0")} {op_char} {expr_cpp});'
                        )
                    return

                if stmt.op == ":=":
                    lines.append(f"{pad}{binding} = {expr_cpp};")
                else:
                    lines.append(f"{pad}{binding} {stmt.op} {expr_cpp};")
                return

            if isinstance(stmt, IfStmt):
                cond_cpp = self._build_security_expr(
                    sec_id,
                    stmt.condition,
                    None,
                    ta_results,
                    resolving,
                    security_mutable_names,
                    runtime_stack_local,
                    lines,
                )
                lines.append(f"{pad}if ({cond_cpp}) {{")
                body_bindings = dict(active_bindings)
                for child in stmt.body:
                    emit_stmt(child, body_bindings, indent + 1)
                if stmt.else_body:
                    lines.append(f"{pad}}} else {{")
                    else_bindings = dict(active_bindings)
                    for child in stmt.else_body:
                        emit_stmt(child, else_bindings, indent + 1)
                lines.append(f"{pad}}}")
                return

            self._codegen_error(
                stmt,
                "request.security multi-statement helpers may only use local declarations, assignments, and if-branches before the final expression",
            )

        for stmt in plan["body"]:
            emit_stmt(stmt, local_cpp_bindings, indent=2)

        return self._build_security_expr(
            sec_id,
            plan["expr"],
            None,
            ta_results,
            resolving,
            security_mutable_names,
            runtime_stack,
            lines,
        )

    def _security_binding_stack_signature(
        self,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None,
    ) -> tuple:
        if not helper_binding_stack:
            return ()
        def _sig_value(value):
            if isinstance(value, str):
                return value
            return id(value)

        sig_frames = []
        for idx, frame in enumerate(helper_binding_stack):
            if idx == 0:
                sig_frames.append(
                    tuple((name, _sig_value(node)) for name, node in sorted(frame.items()))
                )
            else:
                # Helper-local bindings only need to preserve which locals have been
                # materialized at this point; using raw runtime names here causes
                # the declaration/lookup paths to disagree on the same TA variant.
                sig_frames.append(tuple(sorted(frame.keys())))
        return tuple(sig_frames)

    def _security_bind_helper_args(
        self,
        node: FuncCall,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
    ) -> tuple[FuncInfo, tuple[dict[str, ASTNode], ...]]:
        if not isinstance(node.callee, Identifier):
            self._codegen_error(
                node,
                "request.security helper calls must target named user-defined functions",
            )

        func_name = node.callee.name
        fi = self._func_info_map.get(func_name)
        if fi is None or fi.node is None:
            self._codegen_error(
                node,
                f"request.security helper function '{func_name}' is not defined",
            )

        params = list(fi.node.params)
        unknown_kwargs = set(node.kwargs) - set(params)
        if unknown_kwargs:
            unknown_list = ", ".join(sorted(unknown_kwargs))
            self._codegen_error(
                node,
                f"request.security helper call has unknown parameter(s): {unknown_list}",
            )

        bound_args = list(node.args)
        for idx, param_name in enumerate(params):
            if param_name in node.kwargs:
                while len(bound_args) <= idx:
                    bound_args.append(None)
                if bound_args[idx] is None:
                    bound_args[idx] = node.kwargs[param_name]

        if len(bound_args) != len(params) or any(arg is None for arg in bound_args):
            self._codegen_error(
                node,
                "request.security helper calls must bind every parameter explicitly",
            )

        new_frame = {param_name: bound_args[idx] for idx, param_name in enumerate(params)}
        base_stack = helper_binding_stack or ()
        return fi, base_stack + (new_frame,)

    def _security_helper_call_plan(
        self,
        node: FuncCall,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
    ) -> dict:
        fi, bound_stack = self._security_bind_helper_args(node, helper_binding_stack)
        assert fi.node is not None
        body = fi.node.body
        params = list(fi.node.params)

        if len(body) == 1 and isinstance(body[0], ExprStmt):
            return {
                "mode": "expr",
                "func_info": fi,
                "binding_stack": bound_stack,
                "expr": body[0].expr,
                "body": [],
            }

        if not body:
            self._codegen_error(
                node,
                "request.security multi-statement helpers must end with a final expression result",
            )

        stmt_body = list(body[:-1])
        final_stmt = body[-1]
        if isinstance(final_stmt, ExprStmt):
            final_expr = final_stmt.expr
        elif isinstance(final_stmt, Assignment):
            target_name = self._get_target_name(final_stmt.target)
            if target_name is None:
                self._codegen_error(
                    node,
                    "request.security multi-statement helpers must end with a final expression result",
                )
            stmt_body.append(final_stmt)
            final_expr = Identifier(name=target_name)
        elif isinstance(final_stmt, VarDecl):
            stmt_body.append(final_stmt)
            final_expr = Identifier(name=final_stmt.name)
        else:
            self._codegen_error(
                node,
                "request.security multi-statement helpers must end with a final expression result",
            )

        unsupported_control_flow = (
            ForStmt,
            ForInStmt,
            WhileStmt,
            SwitchStmt,
            BreakStmt,
            ContinueStmt,
            TupleAssign,
        )
        for stmt in stmt_body:
            if isinstance(stmt, unsupported_control_flow):
                self._codegen_error(
                    node,
                    "request.security does not support multi-statement helpers with control flow",
                    hint="Inline a straight-line helper body or hoist the control-flow helper outside request.security().",
                )
            if not isinstance(stmt, (VarDecl, Assignment, IfStmt)):
                self._codegen_error(
                    node,
                    "request.security multi-statement helpers may only use local declarations, assignments, and if-branches before the final expression",
                )
            if isinstance(stmt, VarDecl):
                if stmt.is_var or stmt.is_varip:
                    self._codegen_error(
                        node,
                        "request.security does not support multi-statement helpers with helper-local var state",
                        hint="Rewrite helper-local state as plain temporaries or hoist it outside request.security().",
                    )
            if isinstance(stmt, Assignment):
                target_name = self._get_target_name(stmt.target)
                if target_name is None:
                    self._codegen_error(
                        stmt,
                        "request.security multi-statement helpers may only assign to local identifier temporaries",
                    )

        local_series_names = sorted(set(self.ctx.func_series_vars.get(fi.name, set())) - set(params))
        return {
            "mode": "linear",
            "func_info": fi,
            "binding_stack": bound_stack,
            "expr": final_expr,
            "body": stmt_body,
            "local_series_names": local_series_names,
        }

    def _security_next_inline_name(self, sec_id: int, func_name: str, base_name: str) -> str:
        self._security_inline_counter += 1
        return (
            f"_sec{sec_id}_{self._safe_name(func_name)}_"
            f"{self._security_inline_counter}_{self._safe_name(base_name)}"
        )

    def _expr_depends_on_security_mutables(
        self,
        expr_node,
        security_mutable_names: set[str],
        resolving: set[str] | None = None,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
    ) -> bool:
        if expr_node is None or not security_mutable_names:
            return False
        if resolving is None:
            resolving = set()

        if isinstance(expr_node, Identifier):
            bound = self._security_lookup_helper_binding(expr_node.name, helper_binding_stack)
            if bound is not None:
                return self._expr_depends_on_security_mutables(
                    bound,
                    security_mutable_names,
                    resolving,
                    helper_binding_stack,
                )
            if expr_node.name in security_mutable_names:
                return True
            global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
            if expr_node.name in global_expr_map and expr_node.name not in resolving:
                resolving.add(expr_node.name)
                depends = self._expr_depends_on_security_mutables(
                    global_expr_map[expr_node.name],
                    security_mutable_names,
                    resolving,
                    helper_binding_stack,
                )
                resolving.remove(expr_node.name)
                return depends
            return False

        if isinstance(expr_node, FuncCall) and isinstance(expr_node.callee, Identifier):
            func_name = expr_node.callee.name
            if func_name in self._func_names:
                call_key = f"func:{func_name}"
                if call_key in resolving:
                    return False
                resolving.add(call_key)
                plan = self._security_helper_call_plan(
                    expr_node,
                    helper_binding_stack,
                )
                if plan["mode"] == "expr":
                    depends = self._expr_depends_on_security_mutables(
                        plan["expr"],
                        security_mutable_names,
                        resolving,
                        plan["binding_stack"],
                    )
                else:
                    local_ast_bindings: dict[str, ASTNode] = {}
                    linear_stack = plan["binding_stack"] + (local_ast_bindings,)
                    depends = False
                    for stmt in plan["body"][:-1]:
                        value = stmt.value if isinstance(stmt, (VarDecl, Assignment)) else None
                        if value is not None and self._expr_depends_on_security_mutables(
                            value,
                            security_mutable_names,
                            resolving,
                            linear_stack,
                        ):
                            depends = True
                            break
                        target_name = (
                            stmt.name if isinstance(stmt, VarDecl) else self._get_target_name(stmt.target)
                        )
                        if target_name is not None and value is not None:
                            local_ast_bindings[target_name] = value
                    if not depends:
                        depends = self._expr_depends_on_security_mutables(
                            plan["expr"],
                            security_mutable_names,
                            resolving,
                            linear_stack,
                        )
                resolving.remove(call_key)
                return depends

        def walk(value) -> bool:
            if value is None:
                return False
            if hasattr(value, "__dict__"):
                return self._expr_depends_on_security_mutables(
                    value,
                    security_mutable_names,
                    resolving,
                    helper_binding_stack,
                )
            if isinstance(value, (list, tuple)):
                return any(walk(item) for item in value)
            if isinstance(value, dict):
                return any(walk(item) for item in value.values())
            return False

        return any(walk(child) for child in vars(expr_node).values())

    def _security_ta_depends_on_mutables(
        self,
        site: TACallSite,
        security_mutable_names: set[str],
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
    ) -> bool:
        return any(
            self._expr_depends_on_security_mutables(
                arg,
                security_mutable_names,
                helper_binding_stack=helper_binding_stack,
            )
            for arg in site.compute_args
        )

    def _security_ta_ctor_arg_nodes(self, site: TACallSite) -> list:
        node = site.node
        if not isinstance(node, FuncCall):
            return []

        func_name = self._ta_name_from_site(site)
        all_args = self._merge_ta_call_args(func_name, node)
        effective_multi_ctor = TA_MULTI_CTOR.copy()
        if func_name in ("pivothigh", "pivotlow") and len(all_args) == 3:
            effective_multi_ctor[func_name] = [1, 2]

        ctor_indices: list[int] = []
        if func_name in TA_NO_CTOR:
            ctor_indices = []
        elif func_name in effective_multi_ctor:
            ctor_indices = list(effective_multi_ctor[func_name])
        elif func_name in TA_PERIOD_ARG:
            ctor_indices = [TA_PERIOD_ARG[func_name]]

        return [
            all_args[idx]
            for idx in ctor_indices
            if idx < len(all_args) and all_args[idx] is not None
        ]

    def _security_ta_ctor_depends_on_mutables(
        self,
        site: TACallSite,
        security_mutable_names: set[str],
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
    ) -> bool:
        return any(
            self._expr_depends_on_security_mutables(
                arg,
                security_mutable_names,
                helper_binding_stack=helper_binding_stack,
            )
            for arg in self._security_ta_ctor_arg_nodes(site)
        )

    def _collect_security_ta_binding_stacks(
        self,
        expr_node,
        resolving: set[str] | None = None,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
        collected: dict[int, tuple[dict[str, ASTNode], ...]] | None = None,
        inline_ta_indices: set[int] | None = None,
        inline_helper: bool = False,
    ) -> dict[int, tuple[dict[str, ASTNode], ...]]:
        if collected is None:
            collected = {}
        if expr_node is None:
            return collected
        if resolving is None:
            resolving = set()

        if isinstance(expr_node, Identifier):
            bound = self._security_lookup_helper_binding(expr_node.name, helper_binding_stack)
            if bound is not None:
                if isinstance(bound, str):
                    return collected
                self._collect_security_ta_binding_stacks(
                    bound,
                    resolving,
                    helper_binding_stack,
                    collected,
                    inline_ta_indices,
                    inline_helper,
                )
                return collected

            mutable_info = self._global_mutable_infos.get(expr_node.name)
            if mutable_info is not None and expr_node.name not in resolving:
                resolving.add(expr_node.name)
                for stmt in getattr(mutable_info, "source_stmts", []) or []:
                    self._collect_security_ta_binding_stacks(
                        stmt,
                        resolving,
                        helper_binding_stack,
                        collected,
                        inline_ta_indices,
                        inline_helper,
                    )
                resolving.remove(expr_node.name)
                return collected

            global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
            if expr_node.name in global_expr_map and expr_node.name not in resolving:
                resolving.add(expr_node.name)
                self._collect_security_ta_binding_stacks(
                    global_expr_map[expr_node.name],
                    resolving,
                    helper_binding_stack,
                    collected,
                    inline_ta_indices,
                    inline_helper,
                )
                resolving.remove(expr_node.name)
                return collected

        if isinstance(expr_node, FuncCall) and isinstance(expr_node.callee, Identifier):
            func_name = expr_node.callee.name
            if func_name in self._func_names:
                call_key = f"func:{func_name}"
                if call_key in resolving:
                    return collected
                resolving.add(call_key)
                plan = self._security_helper_call_plan(
                    expr_node,
                    helper_binding_stack,
                )
                if plan["mode"] == "expr":
                    self._collect_security_ta_binding_stacks(
                        plan["expr"],
                        resolving,
                        plan["binding_stack"],
                        collected,
                        inline_ta_indices,
                        inline_helper,
                    )
                else:
                    local_series_names = set(plan.get("local_series_names", ()))
                    local_ast_bindings: dict[str, object] = {}
                    linear_stack = plan["binding_stack"] + (local_ast_bindings,)

                    def collect_stmt(stmt, active_bindings: dict[str, object]) -> None:
                        local_stack = plan["binding_stack"] + (active_bindings,)

                        if isinstance(stmt, VarDecl):
                            value = stmt.value
                            if value is not None:
                                self._collect_security_ta_binding_stacks(
                                    value,
                                    resolving,
                                    local_stack,
                                    collected,
                                    inline_ta_indices,
                                    True,
                                )
                            if stmt.name in local_series_names:
                                active_bindings[stmt.name] = self._security_series_binding(
                                    f"{plan['func_info'].name}:{stmt.name}"
                                )
                            elif value is not None:
                                active_bindings[stmt.name] = value
                            return

                        if isinstance(stmt, Assignment):
                            value = stmt.value
                            target_name = self._get_target_name(stmt.target)
                            if value is not None:
                                self._collect_security_ta_binding_stacks(
                                    value,
                                    resolving,
                                    local_stack,
                                    collected,
                                    inline_ta_indices,
                                    True,
                                )
                            if target_name in local_series_names:
                                active_bindings[target_name] = self._security_series_binding(
                                    f"{plan['func_info'].name}:{target_name}"
                                )
                            elif target_name is not None and value is not None:
                                active_bindings[target_name] = value
                            return

                        if isinstance(stmt, IfStmt):
                            self._collect_security_ta_binding_stacks(
                                stmt.condition,
                                resolving,
                                local_stack,
                                collected,
                                inline_ta_indices,
                                True,
                            )
                            body_bindings = dict(active_bindings)
                            for child in stmt.body:
                                collect_stmt(child, body_bindings)
                            else_bindings = dict(active_bindings)
                            for child in stmt.else_body:
                                collect_stmt(child, else_bindings)
                            return

                    for stmt in plan["body"]:
                        collect_stmt(stmt, local_ast_bindings)

                    self._collect_security_ta_binding_stacks(
                        plan["expr"],
                        resolving,
                        linear_stack,
                        collected,
                        inline_ta_indices,
                        True,
                    )
                resolving.remove(call_key)
                return collected

        site = self._get_ta_site(expr_node)
        if site is not None:
            idx = self._ta_index_by_site_id.get(id(site))
            if idx is not None:
                current_sig = self._security_binding_stack_signature(helper_binding_stack)
                existing = collected.setdefault(idx, {})
                existing[current_sig] = helper_binding_stack or ()
                if inline_helper and inline_ta_indices is not None:
                    inline_ta_indices.add(idx)

        def walk(value) -> None:
            if value is None:
                return
            if hasattr(value, "__dict__"):
                self._collect_security_ta_binding_stacks(
                    value,
                    resolving,
                    helper_binding_stack,
                    collected,
                    inline_ta_indices,
                    inline_helper,
                )
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    walk(item)
                return
            if isinstance(value, dict):
                for item in value.values():
                    walk(item)

        for child in vars(expr_node).values():
            walk(child)
        return collected

    def _emit_security_rebind_var_decl(
        self,
        sec_id: int,
        node: VarDecl,
        lines: list[str],
        relevant_names: set[str],
        ta_results: dict[int, str],
        indent: int,
        emitted_lines: list[str] | None = None,
    ) -> None:
        if node.name not in relevant_names:
            return
        info = self._global_mutable_infos.get(node.name)
        if info is None:
            return

        pad = "    " * indent
        state_name = self._security_state_name(sec_id, node.name)
        init_flag = self._security_init_flag_name(sec_id, node.name)
        expr_cpp = self._build_security_expr(
            sec_id,
            node.value,
            None,
            ta_results,
            security_mutable_names=relevant_names,
            emitted_lines=emitted_lines,
        )

        if getattr(info, "is_var", False):
            if getattr(info, "is_series", False):
                lines.append(f"{pad}if (!{init_flag}) {{")
                lines.append(f"{pad}    {state_name}.push({expr_cpp});")
                lines.append(f"{pad}    {init_flag} = true;")
                lines.append(f"{pad}}} else if (security_series_slot_is_new({sec_id})) {{")
                lines.append(f"{pad}    {state_name}.push({state_name}[0]);")
                lines.append(f"{pad}}}")
            else:
                lines.append(f"{pad}if (!{init_flag}) {{")
                lines.append(f"{pad}    {state_name} = {expr_cpp};")
                lines.append(f"{pad}    {init_flag} = true;")
                lines.append(f"{pad}}}")
            return

        if getattr(info, "is_series", False):
            lines.append(f"{pad}if (security_series_slot_is_new({sec_id})) {{")
            lines.append(f"{pad}    {state_name}.push({expr_cpp});")
            lines.append(f"{pad}}} else {{")
            lines.append(f"{pad}    {state_name}.update({expr_cpp});")
            lines.append(f"{pad}}}")
        else:
            lines.append(f"{pad}{state_name} = {expr_cpp};")

    def _emit_security_rebind_assignment(
        self,
        sec_id: int,
        node: Assignment,
        lines: list[str],
        relevant_names: set[str],
        ta_results: dict[int, str],
        indent: int,
        emitted_lines: list[str] | None = None,
    ) -> None:
        target_name = self._get_target_name(node.target)
        if target_name not in relevant_names:
            return
        info = self._global_mutable_infos.get(target_name)
        if info is None:
            return

        pad = "    " * indent
        state_name = self._security_state_name(sec_id, target_name)
        value_cpp = self._build_security_expr(
            sec_id,
            node.value,
            None,
            ta_results,
            security_mutable_names=relevant_names,
            emitted_lines=emitted_lines,
        )

        if getattr(info, "is_series", False):
            if node.op == ":=":
                lines.append(f"{pad}{state_name}.update({value_cpp});")
            else:
                op_char = node.op[0]
                lines.append(f"{pad}{state_name}.update({state_name}[0] {op_char} {value_cpp});")
            return

        if node.op == ":=":
            lines.append(f"{pad}{state_name} = {value_cpp};")
        else:
            lines.append(f"{pad}{state_name} {node.op} {value_cpp};")

    def _emit_security_rebind_stmt(
        self,
        sec_id: int,
        node: ASTNode,
        lines: list[str],
        relevant_names: set[str],
        ta_results: dict[int, str],
        indent: int,
        emitted_lines: list[str] | None = None,
    ) -> None:
        if isinstance(node, VarDecl):
            self._emit_security_rebind_var_decl(
                sec_id, node, lines, relevant_names, ta_results, indent, emitted_lines
            )
            return
        if isinstance(node, Assignment):
            self._emit_security_rebind_assignment(
                sec_id, node, lines, relevant_names, ta_results, indent, emitted_lines
            )
            return
        if isinstance(node, IfStmt):
            body_lines: list[str] = []
            else_lines: list[str] = []
            for stmt in node.body:
                self._emit_security_rebind_stmt(
                    sec_id, stmt, body_lines, relevant_names, ta_results, indent + 1, emitted_lines
                )
            for stmt in node.else_body:
                self._emit_security_rebind_stmt(
                    sec_id, stmt, else_lines, relevant_names, ta_results, indent + 1, emitted_lines
                )
            if not body_lines and not else_lines:
                return
            pad = "    " * indent
            cond_cpp = self._build_security_expr(
                sec_id,
                node.condition,
                None,
                ta_results,
                security_mutable_names=relevant_names,
                emitted_lines=emitted_lines,
            )
            lines.append(f"{pad}if ({cond_cpp}) {{")
            lines.extend(body_lines)
            if else_lines:
                lines.append(f"{pad}}} else {{")
                lines.extend(else_lines)
                lines.append(f"{pad}}}")
            else:
                lines.append(f"{pad}}}")
            return
        if isinstance(node, SwitchStmt):
            self._codegen_error(
                node,
                "request.security mutable global rebinding does not support top-level switch",
                hint="Rewrite the switch as if/else assignments before passing the value to request.security().",
            )
        if isinstance(node, (ForStmt, ForInStmt, WhileStmt)):
            self._codegen_error(
                node,
                "request.security mutable global rebinding does not support top-level loops",
                hint="Move loop-driven mutable state out of request.security() expressions or rewrite it as direct assignments.",
            )

    def _emit_security_rebinds(
        self,
        sec_id: int,
        info: dict,
        lines: list[str],
        ta_results: dict[int, str],
        indent: int = 2,
        emitted_lines: list[str] | None = None,
    ) -> None:
        mutable_globals = info.get("mutable_globals") or []
        if not mutable_globals:
            return
        relevant_names = set(mutable_globals)
        for stmt in self._security_relevant_top_level_stmts(mutable_globals):
            self._emit_security_rebind_stmt(
                sec_id, stmt, lines, relevant_names, ta_results, indent, emitted_lines
            )

    def _collect_security_ta_indices(self, expr_node, resolving: set[str] | None = None) -> set[int]:
        """Collect TA call-site indices used by a security expression.

        Includes TA calls reachable through global identifier bindings.
        """
        if expr_node is None:
            return set()
        if resolving is None:
            resolving = set()

        out: set[int] = set()

        if isinstance(expr_node, Identifier):
            mutable_info = self._global_mutable_infos.get(expr_node.name)
            if mutable_info is not None and expr_node.name not in resolving:
                resolving.add(expr_node.name)
                for stmt in getattr(mutable_info, "source_stmts", []) or []:
                    out |= self._collect_security_ta_indices(stmt, resolving)
                resolving.remove(expr_node.name)
                return out

            global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
            if expr_node.name in global_expr_map and expr_node.name not in resolving:
                resolving.add(expr_node.name)
                out |= self._collect_security_ta_indices(global_expr_map[expr_node.name], resolving)
                resolving.remove(expr_node.name)
                return out

        if isinstance(expr_node, FuncCall) and isinstance(expr_node.callee, Identifier):
            func_name = expr_node.callee.name
            if func_name in self._func_names:
                return set(
                    self._collect_security_ta_binding_stacks(
                        expr_node,
                        resolving,
                    ).keys()
                )

        out |= set(
            self._collect_security_ta_binding_stacks(
                expr_node,
                resolving,
            ).keys()
        )
        return out

    def _emit_security_ohlc_hist_pushes(self, sec_id: int, lines: list[str]) -> None:
        """Emit the OHLC history-offset Series pushes for ``sec_id``, gated on
        ``is_complete``.

        ``request.security(..., [high[1], low[1], ...], ...)`` reads HTF OHLC at
        past-bar offsets. Each offset is backed by a per-field Series whose
        history must advance once per COMPLETED HTF bar — not once per (partial)
        chart-bar evaluation. ``_eval_security_N`` fires on every chart bar; only
        the bar that completes the HTF aggregate has ``is_complete == true``.
        Pushing unconditionally advanced the offset history every chart bar, so
        ``high[1]`` resolved to a recent partial bar instead of the prior
        completed HTF bar. Gate all pushes for this sec in one combined block."""
        fields = sorted(self._security_ohlc_hist_fields_by_sec.get(sec_id, ()))
        if not fields:
            return
        lines.append("        if (is_complete) {")
        for field in fields:
            lines.append(
                f"            {self._security_ohlc_hist_series_cpp(sec_id, field)}.push(bar.{field});"
            )
        lines.append("        }")

    def _emit_security_ta_hist_pushes(
        self, sec_id: int, info: dict, ta_results: dict, lines: list[str]
    ) -> None:
        """Emit the TA history-offset Series pushes for ``sec_id``, gated on
        ``is_complete`` (mirrors ``_emit_security_ohlc_hist_pushes``).

        ``request.security(..., ta.ema(close, 55)[1], ...)`` reads a confirmed
        HTF TA value at a past-bar offset. The committed value (``_secval_*``,
        produced with ``.compute()`` only when ``is_complete``) is pushed onto a
        per-site Series once per COMPLETED HTF bar, AFTER the expression
        assignment so the offset read sees the prior completed bar. Pushing on
        every chart-bar eval would otherwise advance the offset history per
        partial eval / chart tick (the bug this replaces, where the chart-context
        ``_hist_call`` buffer advanced on ``is_first_tick_``)."""
        indices = sorted(self._security_ta_hist_idx_by_sec.get(sec_id, ()))
        if not indices:
            return
        pushes: list[str] = []
        for idx in indices:
            for variant in (info.get("ta_variants") or {}).get(idx, []):
                result_name = ta_results.get((idx, variant["signature"]))
                if result_name is None:
                    continue
                hist = self._security_ta_hist_series_cpp(variant["member_name"])
                pushes.append(f"            {hist}.push({result_name});")
        if not pushes:
            return
        lines.append("        if (is_complete) {")
        lines.extend(pushes)
        lines.append("        }")

    def _emit_security_evaluators(self, lines: list[str]) -> None:
        """Emit _eval_security_N() methods and evaluate_security() dispatch."""
        if not self._security_calls:
            return

        for item in self._security_calls:
            sec_id = item["sec_id"]
            expr_node = item["expr_node"]
            info = self._security_eval_info[sec_id]
            ta_indices = info.get("ta_indices") or []
            security_mutable_names = set(info.get("mutable_globals", []))
            inline_helper_ta_indices = set(info.get("inline_helper_ta_indices", []))

            lines.append(f"    void _eval_security_{sec_id}(const Bar& bar, bool is_complete) {{")

            ta_results = {}
            pre_rebind_ta_indices: list[int] = []
            post_rebind_ta_indices: list[int] = []
            for idx in ta_indices:
                if idx in inline_helper_ta_indices:
                    continue
                site = self.ctx.ta_call_sites[idx]
                variants = (info.get("ta_variants") or {}).get(idx, [])
                depends_on_mutables = False
                for variant in variants:
                    helper_binding_stack = variant.get("binding_stack", ())
                    if self._security_ta_ctor_depends_on_mutables(
                        site,
                        security_mutable_names,
                        helper_binding_stack,
                    ):
                        self._codegen_error(
                            site.node or expr_node,
                            "request.security does not support TA constructor args that depend on rebound mutable globals",
                            hint="Keep TA constructor arguments immutable/simple inside request.security(), or hoist the TA call outside the security expression.",
                        )
                    if self._security_ta_depends_on_mutables(
                        site,
                        security_mutable_names,
                        helper_binding_stack,
                    ):
                        depends_on_mutables = True
                if depends_on_mutables:
                    post_rebind_ta_indices.append(idx)
                else:
                    pre_rebind_ta_indices.append(idx)

            def emit_security_ta(indices: list[int]) -> None:
                for idx in indices:
                    site = self.ctx.ta_call_sites[idx]
                    variants = (info.get("ta_variants") or {}).get(idx, [])
                    for variant in variants:
                        helper_binding_stack = variant.get("binding_stack", ())
                        compute_args = self._security_ta_compute_args_for_site(
                            sec_id,
                            site,
                            ta_results,
                            security_mutable_names,
                            helper_binding_stack,
                            emitted_lines=lines,
                        )
                        var_name = variant["result_name"]
                        sec_name = variant["member_name"]
                        lines.append(f"        auto {var_name} = security_series_slot_is_new({sec_id}) "
                                     f"? {sec_name}.compute({compute_args}) "
                                     f": {sec_name}.recompute({compute_args});")
                        ta_results[(idx, variant["signature"])] = var_name

            emit_security_ta(pre_rebind_ta_indices)

            self._emit_security_rebinds(sec_id, info, lines, ta_results, indent=2, emitted_lines=lines)
            emit_security_ta(post_rebind_ta_indices)
            returns_tuple = item.get("returns_tuple", False)
            tuple_size = item.get("tuple_size", 0)
            if (
                returns_tuple
                and tuple_size
                and tuple_size > 0
                and isinstance(expr_node, TupleLiteral)
            ):
                # A tuple body destructures into per-element scalar members
                # ``_req_sec_{sec_id}_{i}`` (declared in ``base.py`` and reset in
                # ``clear_security``). Assign each element individually rather
                # than building the whole ``TupleLiteral`` (which lowers to an
                # ``std::make_tuple(...)`` against the non-existent aggregate
                # member ``_req_sec_{sec_id}``).
                for i, el in enumerate(expr_node.elements):
                    el_cpp = self._build_security_expr(
                        sec_id,
                        el,
                        None,
                        ta_results,
                        security_mutable_names=security_mutable_names,
                        emitted_lines=lines,
                    )
                    lines.append(f"        _req_sec_{sec_id}_{i} = {el_cpp};")
                self._emit_security_ohlc_hist_pushes(sec_id, lines)
                self._emit_security_ta_hist_pushes(sec_id, info, ta_results, lines)
                lines.append("    }")
                lines.append("")
                continue
            expr_cpp = self._build_security_expr(
                sec_id,
                expr_node,
                None,
                ta_results,
                security_mutable_names=security_mutable_names,
                emitted_lines=lines,
            )
            if item.get("is_lower_tf_array"):
                # ``request.security_lower_tf`` accumulates one element per
                # synthesised sub-bar of the current chart bar. The runtime's
                # ``feed_security_eval_state`` resets ``lower_tf_sub_bar_index``
                # to 0 at the start of every chart bar's synthesis loop, so
                # we clear the vector on index 0 and push for every sub-bar
                # (including index 0).
                lines.append(
                    f"        if (security_lower_tf_sub_bar_index({sec_id}) == 0)"
                    f" _req_sec_lower_tf_{sec_id}.clear();"
                )
                lines.append(
                    f"        _req_sec_lower_tf_{sec_id}.push_back({expr_cpp});"
                )
            else:
                lines.append(f"        _req_sec_{sec_id} = {expr_cpp};")
            self._emit_security_ohlc_hist_pushes(sec_id, lines)
            self._emit_security_ta_hist_pushes(sec_id, info, ta_results, lines)
            lines.append("    }")
            lines.append("")

        # Dispatch method. Security evaluators fire BEFORE on_bar, so we also
        # gate a TA reset here: whichever path fires first (evaluate_security
        # on the bar the HTF aggregator first completes, or on_bar on bar 0)
        # will run the reset and set _ta_initialized_. This makes sure security
        # TA objects use runtime-resolved ctor args on their very first compute.
        lines.append("    void evaluate_security(int sec_id, const Bar& bar, bool is_complete) override {")
        self._emit_ta_runtime_reset(lines, indent=2)
        lines.append("        switch (sec_id) {")
        for item in self._security_calls:
            sec_id = item["sec_id"]
            lines.append(f"            case {sec_id}: _eval_security_{sec_id}(bar, is_complete); break;")
        lines.append("        }")
        lines.append("    }")

        lines.append("    void clear_security(int sec_id) override {")
        lines.append("        switch (sec_id) {")
        for item in self._security_calls:
            sec_id = item["sec_id"]
            expr_node = item["expr_node"]
            returns_tuple = item.get("returns_tuple", False)
            tuple_size = item.get("tuple_size", 0)
            if item.get("is_lower_tf_array"):
                # The accumulator is reset on each sub-bar 0 inside the
                # eval method itself, so ``clear_security`` only needs to
                # forget the previous chart bar's contents (e.g. when
                # gaps mode flushes between completions). Clearing the
                # vector is the right fallback.
                lines.append(f"            case {sec_id}:")
                lines.append(f"                _req_sec_lower_tf_{sec_id}.clear();")
                for field in sorted(self._security_ohlc_hist_fields_by_sec.get(sec_id, ())):
                    lines.append(
                        f"                {self._security_ohlc_hist_series_cpp(sec_id, field)}.clear();"
                    )
                for name in self._security_ta_hist_series_names(sec_id):
                    lines.append(f"                {name}.clear();")
                for name in self._security_expr_hist_series_names(sec_id):
                    lines.append(f"                {name}.clear();")
                lines.append("                break;")
                continue
            if returns_tuple and tuple_size and tuple_size > 0 and isinstance(expr_node, TupleLiteral):
                lines.append(f"            case {sec_id}:")
                for i, el in enumerate(expr_node.elements):
                    ctype = self._infer_cpp_type_for_security_elem(el)
                    if ctype == "double":
                        lines.append(f"                _req_sec_{sec_id}_{i} = na<double>();")
                    elif ctype == "bool":
                        lines.append(f"                _req_sec_{sec_id}_{i} = false;")
                    elif ctype == "int":
                        lines.append(f"                _req_sec_{sec_id}_{i} = 0;")
                    elif ctype == "std::string":
                        lines.append(f'                _req_sec_{sec_id}_{i} = std::string("");')
                    elif ctype == "std::vector<double>":
                        lines.append(f"                _req_sec_{sec_id}_{i}.clear();")
                    else:
                        lines.append(f"                _req_sec_{sec_id}_{i} = 0;")
                for field in sorted(self._security_ohlc_hist_fields_by_sec.get(sec_id, ())):
                    lines.append(
                        f"                {self._security_ohlc_hist_series_cpp(sec_id, field)}.clear();"
                    )
                for name in self._security_ta_hist_series_names(sec_id):
                    lines.append(f"                {name}.clear();")
                for name in self._security_expr_hist_series_names(sec_id):
                    lines.append(f"                {name}.clear();")
                lines.append("                break;")
            elif returns_tuple and tuple_size and tuple_size > 0:
                site = self._get_ta_site(expr_node)
                ta_name = self._ta_name_from_site(site) if site is not None else ""
                ctype = {
                    "macd": "ta::MACDResult",
                    "supertrend": "ta::SupertrendResult",
                    "dmi": "ta::DMIResult",
                    "bb": "ta::BBResult",
                    "kc": "ta::KCResult",
                    "vwap_bands": "ta::VWAPBandsResult",
                }.get(ta_name, "std::tuple<double, double>")
                lines.append(f"            case {sec_id}:")
                lines.append(
                    f"                _req_sec_{sec_id} = "
                    f"{self._security_tuple_result_default(ctype, tuple_size)};"
                )
                for field in sorted(self._security_ohlc_hist_fields_by_sec.get(sec_id, ())):
                    lines.append(
                        f"                {self._security_ohlc_hist_series_cpp(sec_id, field)}.clear();"
                    )
                for name in self._security_ta_hist_series_names(sec_id):
                    lines.append(f"                {name}.clear();")
                for name in self._security_expr_hist_series_names(sec_id):
                    lines.append(f"                {name}.clear();")
                lines.append("                break;")
            else:
                hist = self._security_ohlc_hist_fields_by_sec.get(sec_id, ())
                ta_hist_names = self._security_ta_hist_series_names(sec_id)
                expr_hist_names = self._security_expr_hist_series_names(sec_id)
                if hist or ta_hist_names or expr_hist_names:
                    lines.append(f"            case {sec_id}:")
                    lines.append(f"                _req_sec_{sec_id} = na<double>();")
                    for field in sorted(hist):
                        lines.append(
                            f"                {self._security_ohlc_hist_series_cpp(sec_id, field)}.clear();"
                        )
                    for name in ta_hist_names:
                        lines.append(f"                {name}.clear();")
                    for name in expr_hist_names:
                        lines.append(f"                {name}.clear();")
                    lines.append("                break;")
                else:
                    lines.append(f"            case {sec_id}: _req_sec_{sec_id} = na<double>(); break;")
        lines.append("        }")
        lines.append("    }")

    def _build_security_expr(
        self,
        sec_id: int,
        expr_node,
        ta_range,
        ta_results: dict,
        resolving: set[str] | None = None,
        security_mutable_names: set[str] | None = None,
        helper_binding_stack: tuple[dict[str, ASTNode], ...] | None = None,
        emitted_lines: list[str] | None = None,
    ) -> str:
        """Build C++ expression for a security evaluator."""
        if expr_node is None:
            return "na<double>()"

        if resolving is None:
            resolving = set()
        if security_mutable_names is None:
            security_mutable_names = set()
        if helper_binding_stack is None:
            helper_binding_stack = ()

        if isinstance(expr_node, Identifier):
            bound = self._security_lookup_helper_binding(expr_node.name, helper_binding_stack)
            if bound is not None:
                if isinstance(bound, str):
                    series_name = self._security_series_binding_target(bound)
                    if series_name is not None:
                        return f'_security_helper_series_["{series_name}"][0]'
                    return bound
                return self._build_security_expr(
                    sec_id,
                    bound,
                    ta_range,
                    ta_results,
                    resolving,
                    security_mutable_names,
                    helper_binding_stack,
                    emitted_lines,
                )
            bar_fields = {
                "close": "bar.close", "high": "bar.high",
                "low": "bar.low", "open": "bar.open",
                "volume": "bar.volume",
                "hl2": "((bar.high + bar.low) / 2.0)",
                "hlc3": "((bar.high + bar.low + bar.close) / 3.0)",
                "ohlc4": "((bar.open + bar.high + bar.low + bar.close) / 4.0)",
            }
            if expr_node.name in bar_fields:
                return bar_fields[expr_node.name]

            if expr_node.name in security_mutable_names:
                info = self._global_mutable_infos.get(expr_node.name)
                state_name = self._security_state_name(sec_id, expr_node.name)
                if info is not None and getattr(info, "is_series", False):
                    return f"{state_name}[0]"
                return state_name

            global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
            if expr_node.name in global_expr_map and expr_node.name not in resolving:
                resolving.add(expr_node.name)
                resolved = self._build_security_expr(
                    sec_id,
                    global_expr_map[expr_node.name],
                    ta_range,
                    ta_results,
                    resolving,
                    security_mutable_names,
                    helper_binding_stack,
                    emitted_lines,
                )
                resolving.remove(expr_node.name)
                return resolved

        if (
            isinstance(expr_node, MemberAccess)
            and isinstance(expr_node.object, Identifier)
            and expr_node.object.name == "timeframe"
        ):
            resolved = self._build_security_timeframe_member(sec_id, expr_node.member)
            if resolved is not None:
                return resolved

        if isinstance(expr_node, Subscript):
            index_cpp = self._build_security_expr(
                sec_id,
                expr_node.index,
                ta_range,
                ta_results,
                resolving,
                security_mutable_names,
                helper_binding_stack,
                emitted_lines,
            )
            if isinstance(expr_node.object, Identifier):
                bound = self._security_lookup_helper_binding(expr_node.object.name, helper_binding_stack)
                if bound is not None:
                    if isinstance(bound, str):
                        series_name = self._security_series_binding_target(bound)
                        if series_name is not None:
                            return f'_security_helper_series_["{series_name}"][{index_cpp}]'
                        return bound
                    obj_cpp = self._build_security_expr(
                        sec_id,
                        bound,
                        ta_range,
                        ta_results,
                        resolving,
                        security_mutable_names,
                        helper_binding_stack,
                        emitted_lines,
                    )
                    return f"{obj_cpp}[{index_cpp}]"
                if expr_node.object.name in SECURITY_OHLC_BAR_FIELDS:
                    idx_lit = self._literal_int_for_security_index(expr_node.index)
                    if idx_lit is not None:
                        bar_map = {
                            "open": "bar.open",
                            "high": "bar.high",
                            "low": "bar.low",
                            "close": "bar.close",
                            "volume": "bar.volume",
                        }
                        if idx_lit == 0:
                            return bar_map[expr_node.object.name]
                        if idx_lit >= 1:
                            # lookahead_off: we evaluate when an HTF bar completes; `bar` is that
                            # bar. On the HTF series, high[0]/close is the current (just-finished)
                            # bar; high[1] is one HTF bar back = hist[field][0] *before* we push
                            # `bar` (Series [0] = most recent prior push). high[k] -> hist[k-1].
                            field = expr_node.object.name
                            hist = self._security_ohlc_hist_series_cpp(sec_id, field)
                            return f"{hist}[{idx_lit - 1}]"
                    self._codegen_error(
                        expr_node,
                        "request.security() OHLC history index must be a literal integer (e.g. high[1])",
                    )

                # Indirect TA binding: ``v = ta.ema(close, 55)`` then
                # ``request.security(..., v[1], ...)``. _get_ta_site below only
                # matches the literal ta.* FuncCall node by identity, so a bare
                # Identifier subscript target silently misses it and falls
                # through to a chart-resolution read of the wrong (non-HTF)
                # series. Resolve through the same global_expr_map the
                # non-subscript Identifier path above already uses, and
                # recurse on a synthetic Subscript over the resolved value so
                # it re-enters this whole branch (TA site, OHLC field, or
                # helper binding, whichever the resolved expression turns out
                # to be) instead of duplicating that dispatch here.
                global_expr_map = getattr(self.ctx, "global_expr_map", {}) or {}
                if (expr_node.object.name in global_expr_map
                        and expr_node.object.name not in resolving):
                    resolving.add(expr_node.object.name)
                    resolved = self._build_security_expr(
                        sec_id,
                        Subscript(object=global_expr_map[expr_node.object.name], index=expr_node.index),
                        ta_range,
                        ta_results,
                        resolving,
                        security_mutable_names,
                        helper_binding_stack,
                        emitted_lines,
                    )
                    resolving.remove(expr_node.object.name)
                    return resolved
            if (
                isinstance(expr_node.object, FuncCall)
                and self._get_ta_site(expr_node.object) is None
            ):
                meta = self._security_expr_hist_by_node.get((sec_id, id(expr_node)))
                hist = meta["name"] if meta else f"_sec{sec_id}_expr_hist_missing"
                cpp_t = meta["type"] if meta else "double"
                inner = self._build_security_expr(
                    sec_id,
                    expr_node.object,
                    ta_range,
                    ta_results,
                    resolving,
                    security_mutable_names,
                    helper_binding_stack,
                    emitted_lines,
                )
                return (
                    f"([&]() -> {cpp_t} {{ "
                    f"{cpp_t} _hv = ({inner}); "
                    f"int _hidx = (int)({index_cpp}); "
                    f"{cpp_t} _out = (_hidx <= 0) ? _hv : {hist}[_hidx - 1]; "
                    f"if (is_complete) {hist}.push(_hv); "
                    f"return _out; }}())"
                )
            ta_site = self._get_ta_site(expr_node.object)
            if ta_site is not None:
                # ``ta.<fn>(...)[k]`` inside request.security(): the inner TA call
                # runs in the HTF (security) context and commits one value per
                # COMPLETED HTF bar. Read the already-emitted security TA result —
                # offset 0 reuses the current committed value (``_secval_*``),
                # offset k>=1 reads a per-site Series that advances on
                # ``is_complete`` (HTF-bar boundary) in ``_eval_security_N``. The
                # buggy generic path re-lowered the inner TA to the CHART member
                # and gated a ``_hist_call`` buffer on ``is_first_tick_`` (chart
                # tick), so without a magnifier it advanced every chart bar and
                # produced the chart-tf TA instead of the confirmed HTF value.
                idx = self._ta_index_by_site_id.get(id(ta_site))
                sig = self._security_binding_stack_signature(helper_binding_stack)
                idx_lit = self._literal_int_for_security_index(expr_node.index)
                if idx_lit is None:
                    self._codegen_error(
                        expr_node,
                        "request.security() TA history index must be a literal integer (e.g. ta.ema(close, 55)[1])",
                    )
                if idx_lit == 0:
                    # Current completed-HTF-bar value: reuse the bare-TA emission.
                    return self._build_security_expr(
                        sec_id,
                        expr_node.object,
                        ta_range,
                        ta_results,
                        resolving,
                        security_mutable_names,
                        helper_binding_stack,
                        emitted_lines,
                    )
                member_name = self._security_ta_variant_names.get(
                    (sec_id, idx, sig),
                    f"_sec{sec_id}_{ta_site.member_name}",
                )
                hist = self._security_ta_hist_series_cpp(member_name)
                # ta(...)[k] -> hist[k-1]: hist[0] is the prior completed HTF bar
                # (current value not yet pushed — push happens after this assign).
                return f"{hist}[{idx_lit - 1}]"

        if isinstance(expr_node, BinOp):
            left = self._build_security_expr(
                sec_id, expr_node.left, ta_range, ta_results, resolving, security_mutable_names, helper_binding_stack, emitted_lines
            )
            right = self._build_security_expr(
                sec_id, expr_node.right, ta_range, ta_results, resolving, security_mutable_names, helper_binding_stack, emitted_lines
            )
            cpp_ops = {"and": "&&", "or": "||"}
            op = cpp_ops.get(expr_node.op, expr_node.op)
            if expr_node.op == "%":
                return f"std::fmod((double)({left}), (double)({right}))"
            return f"({left} {op} {right})"

        if isinstance(expr_node, UnaryOp):
            operand = self._build_security_expr(
                sec_id, expr_node.operand, ta_range, ta_results, resolving, security_mutable_names, helper_binding_stack, emitted_lines
            )
            if expr_node.op == "not":
                return f"!({operand})"
            return f"({expr_node.op}{operand})"

        if isinstance(expr_node, Ternary):
            cond = self._build_security_expr(
                sec_id, expr_node.condition, ta_range, ta_results, resolving, security_mutable_names, helper_binding_stack, emitted_lines
            )
            tv = self._build_security_expr(
                sec_id, expr_node.true_val, ta_range, ta_results, resolving, security_mutable_names, helper_binding_stack, emitted_lines
            )
            fv = self._build_security_expr(
                sec_id, expr_node.false_val, ta_range, ta_results, resolving, security_mutable_names, helper_binding_stack, emitted_lines
            )
            return f"(({cond}) ? ({tv}) : ({fv}))"

        if isinstance(expr_node, FuncCall) and isinstance(expr_node.callee, Identifier):
            func_name = expr_node.callee.name
            if func_name in self._func_names:
                call_key = f"func:{func_name}"
                if call_key in resolving:
                    self._codegen_error(
                        expr_node,
                        "request.security helper functions must not recurse while building a security context",
                    )
                resolving.add(call_key)
                plan = self._security_helper_call_plan(
                    expr_node,
                    helper_binding_stack,
                )
                if plan["mode"] == "expr":
                    resolved = self._build_security_expr(
                        sec_id,
                        plan["expr"],
                        ta_range,
                        ta_results,
                        resolving,
                        security_mutable_names,
                        plan["binding_stack"],
                        emitted_lines,
                    )
                else:
                    if emitted_lines is None:
                        self._codegen_error(
                            expr_node,
                            "request.security multi-statement helpers require statement-capable security evaluation context",
                        )
                    resolved = self._emit_security_linear_helper_call(
                        sec_id,
                        plan,
                        ta_results,
                        security_mutable_names,
                        emitted_lines,
                        resolving,
                    )
                resolving.remove(call_key)
                return resolved

        if (
            isinstance(expr_node, FuncCall)
            and isinstance(expr_node.callee, MemberAccess)
            and isinstance(expr_node.callee.object, Identifier)
            and expr_node.callee.object.name == "math"
        ):
            return self._build_security_math_call(
                sec_id,
                expr_node.callee.member,
                expr_node,
                ta_range,
                ta_results,
                resolving,
                security_mutable_names,
                helper_binding_stack,
                emitted_lines,
            )

        site = self._get_ta_site(expr_node)
        if site:
            idx = self._ta_index_by_site_id.get(id(site))
            sig = self._security_binding_stack_signature(helper_binding_stack)
            if idx is not None:
                result_key = (idx, sig)
                if result_key in ta_results:
                    return ta_results[result_key]
            sec_name = self._security_ta_variant_names.get(
                (sec_id, idx, sig),
                f"_sec{sec_id}_{site.member_name}",
            )
            compute_args = self._security_ta_compute_args_for_site(
                sec_id,
                site,
                ta_results,
                security_mutable_names,
                helper_binding_stack,
                emitted_lines,
            )
            return f"(security_series_slot_is_new({sec_id}) ? {sec_name}.compute({compute_args}) : {sec_name}.recompute({compute_args}))"

        result = self._visit_expr(expr_node)
        return self._rewrite_security_cpp(result, sec_id, security_mutable_names, helper_binding_stack)
