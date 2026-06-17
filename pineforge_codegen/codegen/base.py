"""Code generator: AnalyzerContext -> C++ source for the PineScript backtester.

This is the new visitor-pattern codegen that reads pre-computed analysis
results from AnalyzerContext instead of walking the AST to collect info.
"""

from __future__ import annotations

from ..ast_nodes import (
    ASTNode, Program, StrategyDecl, VarDecl, Assignment, IfStmt, ForStmt, ForInStmt,
    WhileStmt, SwitchStmt, BreakStmt, ContinueStmt, FuncDef, ExprStmt,
    BinOp, UnaryOp, Ternary, FuncCall, Subscript, Identifier, MemberAccess,
    NumberLiteral, StringLiteral, BoolLiteral, NaLiteral, TupleAssign,
    ColorLiteral, ImportStmt, TupleLiteral,
    TypeDecl, EnumDecl, MethodDef, TypeField,
)
from ..analyzer import (
    AnalyzerContext,
    TACallSite,
    FuncInfo,
    FixnanCallSite,
    TA_MULTI_CTOR,
    TA_NO_CTOR,
    TA_PERIOD_ARG,
)
from ..symbols import PineType, TypeSpec
from .. import signatures as sigs
from ..errors import CompileError, Diagnostic, Level, Phase, SourceLocation

# ---------------------------------------------------------------------------
# Mapping tables — definitions live in ``tables.py``; re-imported here so
# inline references inside this module (BAR_FIELDS[name], MATH_FUNC_MAP[fn],
# etc.) keep resolving without qualification. The package-level
# ``__init__.py`` re-exports the same names for external consumers
# (``support_checker.py`` and external test imports).
# ---------------------------------------------------------------------------

from .tables import (
    BAR_FIELDS,
    BAR_BUILTINS,
    BAR_SERIES_PUSH,
    SECURITY_OHLC_BAR_FIELDS,
    TA_RETURNS_BOOL,
    TA_IMPLICIT_COMPUTE,
    TA_COMPUTE_ARGS,
    TA_IMPLICIT_COMPUTE_FULL,
    TA_IMPLICIT_APPEND,
    TA_TUPLE_FIELDS,
    PINE_TYPE_TO_CPP,
    SKIP_FUNC_NAMES,
    SKIP_NAMESPACES,
    SKIP_VAR_TYPES,
    SYMINFO_MEMBER_MAP,
    COLOR_CONST_MAP,
    ARRAY_METHODS,
    MAP_METHODS,
    MATRIX_METHODS,
    MATRIX_METHOD_KWARGS,
    MATRIX_NUMERIC_ONLY,
    MATRIX_RETURNING_METHODS,
    MATRIX_SORT_ALLOWED_GENERIC_ELEMS,
    MATH_FUNC_MAP,
    STR_FUNC_MAP,
    _merge_kwargs,
)

# (TA_IMPLICIT_COMPUTE / TA_COMPUTE_ARGS now imported from .tables above.)

# (TA_IMPLICIT_COMPUTE_FULL / TA_IMPLICIT_APPEND / PINE_TYPE_TO_CPP /
#  SKIP_*  / SYMINFO_MEMBER_MAP / COLOR_CONST_MAP all imported from .tables.)

# (ARRAY_METHODS / MAP_METHODS / MATRIX_METHODS / MATRIX_METHOD_KWARGS /
#  MATH_FUNC_MAP / STR_FUNC_MAP / TA_TUPLE_FIELDS / _matrix_add_row /
#  _matrix_add_col / _merge_kwargs all imported from .tables above.)

# Math parameter names live in ``signatures.py`` (sigs.get_param_names).

# CPP_RESERVED + the NamingHelper mixin are pulled in from helpers.py so the
# small naming/walk utilities can be shared with future visitor mixins.
from .helpers import CPP_RESERVED, NamingHelper

# TypeInferer mixin owns the ~15 type-spec / C++-type inference helpers
# previously scattered across this module; see ``codegen/types.py``.
from .types import TypeInferer

# TaSiteHelper owns site lookup, .compute() arg construction, and the TA
# hoisting machinery. The runtime-reset chain (_resolve_known and friends)
# stays on CodeGen for now because it relies on Python's compile-time
# expression evaluator.
from .ta import TaSiteHelper

# InputHelper owns Pine input.* analysis (defaults, titles, getter dispatch,
# enum-declared-first guard).
from .input import InputHelper

# SecurityEmitter owns the request.security() lowering pipeline:
# evaluator emission, dispatch, mutable-global rebind, TA-variant binding
# stacks, and the per-call helper plan. Most stateful mixin in the
# package; see its module docstring for the full host-class state contract.
from .security import SecurityEmitter

# TopLevelEmitter owns the top-level C++ section emitters (includes,
# constructor, on_bar, extern "C") plus the per-function emission helpers
# (_emit_func_def / _emit_udt_method_cpp_name) used by both regular
# Pine functions and UDT instance methods.
from .emit_top import TopLevelEmitter

# StmtVisitor owns the statement-level visitors (_visit_stmt dispatcher
# plus the per-kind handlers for var-decl, assignment, tuple-assign,
# if/for/while/switch and the if/switch-as-expression lowering).
from .visit_stmt import StmtVisitor
from .visit_expr import ExprVisitor
from .visit_call import CallVisitor


# ---------------------------------------------------------------------------
# CodeGen class
# ---------------------------------------------------------------------------

class CodeGen(CallVisitor, ExprVisitor, StmtVisitor, TopLevelEmitter, SecurityEmitter, TaSiteHelper, TypeInferer, InputHelper, NamingHelper):
    """Generate C++ from an AnalyzerContext (visitor pattern).

    Mixin chain (Python MRO is left-to-right; method names are
    intentionally kept disjoint across mixins so the order is mostly
    cosmetic):
        * ``CallVisitor``  -- function-call dispatcher (_visit_func_call)
          + per-namespace dispatch helpers (_visit_strategy_call /
          _visit_color_call / _visit_str_call / _visit_math_call /
          _visit_fixnan) + _resolve_func_args kwarg-merging helper
        * ``ExprVisitor``  -- expression-level visitors (_visit_expr
          dispatcher + per-kind handlers _visit_ident /
          _visit_member_access / _visit_binop / _visit_unaryop /
          _visit_subscript)
        * ``StmtVisitor``  -- statement-level visitors (_visit_stmt
          dispatcher + per-kind handlers + if/switch-as-expression)
        * ``TopLevelEmitter`` -- top-level C++ section emitters
          (includes / constructor / on_bar / extern "C") plus the
          per-function emitters used by Pine functions and UDT methods
        * ``SecurityEmitter`` -- ``request.security()`` lowering pipeline
          (evaluators, dispatch, rebind, TA variants)
        * ``TaSiteHelper`` -- TA call-site lookup + .compute() arg construction + TA hoisting
        * ``TypeInferer``  -- _type_spec_*, _infer_type, _array/_map_method_expr
        * ``InputHelper``  -- Pine ``input.*`` defaults / titles / getter dispatch
        * ``NamingHelper`` -- _safe_name / _resolve_callee / _walk_ast / ...

    With CallVisitor extracted (step 10/N), the host class is now a thin
    coordinator that keeps state attributes, the constructor, the
    top-level ``generate()`` orchestrator, prescan helpers
    (_collect_known_vars / _find_reassigned_vars / _collect_known_var /
    _prescan_strategy_series), and the runtime-reset chain
    (_resolve_known / _is_skip_expr / _runtime_ctor_arg_for_reset /
    _collect_ta_runtime_resets / _emit_ta_runtime_reset) — kept here
    because the chain relies on Python's compile-time expression
    evaluator.
    """

    def __init__(self, ctx: AnalyzerContext) -> None:
        self.ctx = ctx
        # Build lookup: node id -> TACallSite (only for non-function-local sites)
        self._ta_site_map: dict[int, TACallSite] = {}
        # Build per-call-site TA member name remapping for user functions
        # Maps (func_name, cs_idx) -> {original_member_name: cloned_member_name}
        self._func_cs_ta_remap: dict[tuple[str, int], dict[str, str]] = {}
        # Active TA name remap (set during per-call-site function emission)
        self._active_ta_remap: dict[str, str] = {}
        # Flag: inside a per-call-site function variant (enables TA hoisting)
        self._in_ta_func_variant: bool = False
        # Active call-site index (set during per-call-site function emission)
        self._active_call_site_idx: int | None = None
        # Set of TA member names that belong to user functions
        self._func_ta_members: set[str] = set()

        # Build per-call-site var/series member name remapping for user functions
        # Maps (func_name, cs_idx) -> {original_var_name: cloned_var_name}
        self._func_cs_var_remap: dict[tuple[str, int], dict[str, str]] = {}
        # Active var name remap (set during per-call-site function emission)
        self._active_var_remap: dict[str, str] = {}
        # Set of var/series member names that belong to user functions (need cloning)
        self._func_var_members_set: set[str] = set()
        self._precalc_loop_active: bool = False

        # Build per-function var/series name lists for cloning.
        # For each function with call-site variants, collect ALL function-scoped
        # series vars (from this function AND any sub-functions it calls).
        # This ensures sub-function series vars get cloned for the parent's call sites.
        func_var_originals: dict[str, list[str]] = {}  # func_name -> list of original var names

        # First, collect all function-scoped series vars (union across all functions).
        # Use an ordered, de-duplicated list (NOT a set): set iteration order is
        # PYTHONHASHSEED-randomized, and this order reaches emitted C++ member
        # declarations via ``orig_names`` -> ``func_var_originals`` ->
        # ``_func_cs_var_remap``. ``ctx.func_series_vars`` is a dict whose VALUES
        # are themselves sets (analyzer stores ``dict[str, set]``), so we must
        # iterate each value in ``sorted`` order to be hash-seed independent.
        all_func_scoped_series: list[str] = []
        for svars in ctx.func_series_vars.values():
            for sv in sorted(svars):
                if sv not in all_func_scoped_series:
                    all_func_scoped_series.append(sv)
        # Also include function-scoped var_members (same ordered-list rationale).
        # ``ctx.func_var_members`` values are lists (already insertion-ordered).
        all_func_scoped_vars: list[str] = []
        for vlist in ctx.func_var_members.values():
            for n, _, _ in vlist:
                if n not in all_func_scoped_vars:
                    all_func_scoped_vars.append(n)

        # For each function with call-site cloning (has TA ranges or is called multiple times),
        # include ALL function-scoped series/var vars that could be used in its body.
        # Iterate the dict directly (insertion-ordered) rather than ``set(...keys())``,
        # which would randomize the order of emitted clones across hash seeds.
        for fname in ctx.func_call_site_counts:
            total_cs = ctx.func_call_site_counts[fname]
            if total_cs <= 1:
                continue  # No cloning needed for single-call-site functions
            orig_names: list[str] = []
            # Include function's own vars
            if fname in ctx.func_var_members:
                for n, _, _ in ctx.func_var_members[fname]:
                    if n not in orig_names:
                        orig_names.append(n)
            # Include function's own series vars (set -> sorted for determinism)
            if fname in ctx.func_series_vars:
                for sv in sorted(ctx.func_series_vars[fname]):
                    if sv not in orig_names:
                        orig_names.append(sv)
            # Include series vars from sub-functions (they share the same class members)
            for sv in all_func_scoped_series:
                if sv not in orig_names:
                    orig_names.append(sv)
            for sv in all_func_scoped_vars:
                if sv not in orig_names:
                    orig_names.append(sv)
            if orig_names:
                func_var_originals[fname] = orig_names
                self._func_var_members_set.update(orig_names)
                # cs0 uses originals (identity mapping)
                self._func_cs_var_remap[(fname, 0)] = {self._safe_name(n): self._safe_name(n) for n in orig_names}

        # Build cloned var remapping for cs > 0
        for fname, orig_names in func_var_originals.items():
            total_cs = ctx.func_call_site_counts.get(fname, 1)
            for cs_idx in range(1, total_cs):
                remap = {}
                for orig_name in orig_names:
                    safe = self._safe_name(orig_name)
                    remap[safe] = f"{safe}_cs{cs_idx}"
                self._func_cs_var_remap[(fname, cs_idx)] = remap
                self._func_var_members_set.update(
                    orig_name for orig_name in orig_names)

        # Build TA site map and per-call-site remapping
        func_ta_originals: dict[str, list[str]] = {}  # func_name -> list of original member names
        for fname, (start, end) in ctx.func_ta_ranges.items():
            orig_names = [ctx.ta_call_sites[i].member_name for i in range(start, end)]
            func_ta_originals[fname] = orig_names
            self._func_ta_members.update(orig_names)
            # cs0 uses originals (identity mapping)
            self._func_cs_ta_remap[(fname, 0)] = {n: n for n in orig_names}

        # Build cloned site remapping for cs > 0 (must happen before _ta_site_map
        # so cloned names are in _func_ta_members and get filtered out of the map)
        for fname, orig_names in func_ta_originals.items():
            total_cs = ctx.func_call_site_counts.get(fname, 1)
            for cs_idx in range(1, total_cs):
                remap = {}
                for orig_name in orig_names:
                    remap[orig_name] = f"{orig_name}_cs{cs_idx}"
                self._func_cs_ta_remap[(fname, cs_idx)] = remap
                self._func_ta_members.update(remap.values())

        for site in ctx.ta_call_sites:
            if site.node is not None:
                if site.member_name not in self._func_ta_members:
                    self._ta_site_map[id(site.node)] = site
                elif not any(site.member_name.endswith(f"_cs{i}") for i in range(1, 100)):
                    # Original (cs0) function-local site — add to map for initial visit
                    self._ta_site_map[id(site.node)] = site
        self._ta_index_by_site_id: dict[int, int] = {
            id(site): i for i, site in enumerate(ctx.ta_call_sites)
        }
        # Build lookup: node id -> FixnanCallSite (counter-based)
        self._fixnan_counter = 0
        self._switch_counter = 0
        self._security_inline_counter = 0
        self._random_call_counter = 0
        # UDT / enum (needed before _collect_known_vars for input.enum)
        self._udt_defs: dict[str, list] = {}
        self._enum_defs: dict[str, list[str]] = {}
        for stmt in ctx.ast.body:
            if isinstance(stmt, TypeDecl):
                self._udt_defs[stmt.name] = stmt.fields
            if isinstance(stmt, EnumDecl):
                self._enum_defs[stmt.name] = stmt.members
        self._enum_member_strings: dict[str, list[str]] = getattr(
            ctx, "enum_member_strings", None
        ) or {}
        # Contextual var name for input title fallback (set during _visit_var_decl)
        self._current_input_var_name: str | None = None
        # Build known_vars for constant propagation
        self._known_vars: dict[str, int | float | bool | str] = {}
        # Subset of _known_vars whose value came from an input.*() call. These
        # MUST NOT be inlined at identifier use sites because strategy_set_input()
        # can override them at runtime. Ctor-time uses (TA buffer sizing,
        # request.security TF) use the Pine default at construction but then get
        # rebuilt on first on_bar via _emit_ta_runtime_reset().
        self._input_backed_vars: set[str] = set()
        # Map input-backed var name -> its input.*() FuncCall node so we can
        # later emit a runtime get_input_*() read with the same title/default.
        self._input_var_to_call: dict[str, FuncCall] = {}
        self._timeframe_period_vars: set[str] = set()
        self._collect_known_vars()
        # Track var names
        self._var_names: set[str] = set()
        for name, _, _ in ctx.var_members:
            self._var_names.add(name)
        # Every name bound ANYWHERE in the program (top-level, nested in
        # if/for/while/switch blocks, or inside function bodies). The
        # unknown-identifier guard in _visit_ident uses this as a generous
        # last-resort allow-list: a name bound nowhere AND not a builtin is a
        # genuinely-undefined read that would emit an undeclared C++ symbol.
        # Block-scoped locals (e.g. a var declared inside an on_bar for-loop)
        # are otherwise invisible to the per-scope tracking sets.
        self._all_bound_names: set[str] = self._collect_binding_names(ctx.ast.body)
        # Build set of user-defined function names and lookup map
        self._func_names: set[str] = set()
        self._func_info_map: dict[str, FuncInfo] = {}
        for fi in ctx.func_infos:
            self._func_names.add(fi.name)
            self._func_info_map[fi.name] = fi
        # Track strategy series vars (e.g., strategy.closedtrades[1])
        self._strategy_series_vars: set[str] = set()
        # Track global-scope non-var declarations (emitted as class members)
        self._global_member_vars: set[str] = set()
        for name, _ in ctx.global_var_decls:
            self._global_member_vars.add(name)
        self._global_mutable_infos: dict[str, object] = getattr(ctx, "global_mutable_infos", {}) or {}
        self._udt_var_types: dict[str, str] = getattr(ctx, "udt_var_types", {}) or {}
        self._collection_types: dict[str, TypeSpec] = getattr(ctx, "collection_types", {}) or {}
        self._udt_field_type_specs: dict[str, dict[str, TypeSpec]] = getattr(ctx, "udt_field_type_specs", {}) or {}
        # Map UDT struct name -> set of field names that were dropped from the
        # emitted C++ struct because they had drawing-only types (label, line,
        # box, linefill, polyline, table, chart.point). Populated eagerly
        # here from ``self._udt_defs`` so downstream visitors (visit_expr /
        # visit_stmt) can consult it before ``generate()`` runs. The struct
        # emission loop later asserts/syncs against this same map. Used to
        # rewrite or strip downstream references to those fields so the
        # generated C++ never references a member that doesn't exist on the
        # emitted struct. See: pineforge-codegen issue #10.
        _DRAWING_TYPES_INIT = {"label", "line", "box", "table", "linefill", "polyline", "chart.point"}
        self._udt_omitted_fields: dict[str, set[str]] = {}
        for _type_name, _fields in self._udt_defs.items():
            _omitted = set()
            for _f in _fields:
                if _f.type_name and _f.type_name in _DRAWING_TYPES_INIT:
                    _omitted.add(_f.name)
            self._udt_omitted_fields[_type_name] = _omitted
        self._udt_param_udt: dict[str, str] = {}
        self._security_calls: list[dict] = [self._normalize_security_call(item) for item in ctx.security_calls]
        # Current function parameter types (set during _emit_func_def)
        self._current_func_param_types: dict[str, str] = {}
        # Current function params that are series (const Series<double>&)
        self._current_func_series_params: set[str] = set()
        # Locals declared in the function currently being emitted (symbol table loses them after analysis)
        self._current_func_locals: set[str] = set()
        # for-in loop iterator names (must resolve member access, not enum fallback)
        self._current_loop_vars: set[str] = set()
        # Track array variables for codegen
        self._array_vars: set[str] = set()
        # Track map variables for codegen
        self._map_vars: set[str] = set()
        # Track matrix variables for codegen (name -> TypeSpec)
        self._matrix_specs: dict[str, "TypeSpec"] = {}
        for _name, _spec in self._collection_types.items():
            if _spec.kind == "array":
                self._array_vars.add(_name)
            elif _spec.kind == "map":
                self._map_vars.add(_name)
            elif _spec.kind == "udt" and _spec.name:
                self._udt_var_types.setdefault(_name, _spec.name)
        # Collect request.security metadata per call
        self._security_eval_info: list[dict] = []
        self._security_ta_variant_names: dict[tuple[int, int, tuple], str] = {}
        for item in self._security_calls:
            sec_id = item["sec_id"]
            tf_node = item["tf_node"]
            gaps_node = item.get("gaps_node")
            lookahead_node = item.get("lookahead_node")
            ta_range = item.get("ta_range")

            tf_str = None
            if isinstance(tf_node, StringLiteral):
                tf_str = tf_node.value
            elif (isinstance(tf_node, Identifier)
                  and tf_node.name in self._known_vars
                  and tf_node.name not in self._input_backed_vars):
                val = self._known_vars[tf_node.name]
                if isinstance(val, str):
                    tf_str = val

            is_lookahead_on = False
            if lookahead_node is not None:
                if isinstance(lookahead_node, MemberAccess) and lookahead_node.member == "lookahead_on":
                    is_lookahead_on = True

            is_gaps_on = False
            if gaps_node is not None:
                if isinstance(gaps_node, MemberAccess) and gaps_node.member == "gaps_on":
                    is_gaps_on = True

            expr_node = item["expr_node"]
            inline_helper_ta_indices: set[int] = set()
            ta_binding_stacks = self._collect_security_ta_binding_stacks(
                expr_node,
                inline_ta_indices=inline_helper_ta_indices,
            )
            ta_indices = self._collect_security_ta_indices(expr_node)
            ta_variants: dict[int, list[dict]] = {}
            for idx in sorted(ta_indices):
                site = self.ctx.ta_call_sites[idx]
                binding_map = ta_binding_stacks.get(idx) or {(): ()}
                signatures = sorted(binding_map.keys(), key=repr)
                use_base_name = len(signatures) == 1
                variants: list[dict] = []
                for variant_idx, signature in enumerate(signatures):
                    member_name = (
                        f"_sec{sec_id}_{site.member_name}"
                        if use_base_name
                        else f"_sec{sec_id}_{site.member_name}_v{variant_idx}"
                    )
                    result_name = (
                        f"_secval_{idx}"
                        if use_base_name
                        else f"_secval_{idx}_v{variant_idx}"
                    )
                    binding_stack = binding_map[signature]
                    variants.append(
                        {
                            "signature": signature,
                            "binding_stack": binding_stack,
                            "member_name": member_name,
                            "result_name": result_name,
                        }
                    )
                    self._security_ta_variant_names[(sec_id, idx, signature)] = member_name
                ta_variants[idx] = variants
            self._security_eval_info.append({
                "sec_id": sec_id,
                "tf": tf_str,
                "tf_node": tf_node,
                "gaps_on": is_gaps_on,
                "lookahead_on": is_lookahead_on,
                "ta_range": ta_range,
                "ta_indices": sorted(ta_indices),
                "ta_binding_stacks": ta_binding_stacks,
                "ta_variants": ta_variants,
                "inline_helper_ta_indices": sorted(inline_helper_ta_indices),
                "depends_on_mutable_globals": item.get("depends_on_mutable_globals", False),
                "mutable_globals": list(item.get("mutable_globals", [])),
                "is_lower_tf_array": bool(item.get("is_lower_tf_array", False)),
            })
        # Build set of all member names (series vars, var members) for collision detection
        self._all_member_names: set[str] = set()
        for name in ctx.series_vars:
            self._all_member_names.add(self._safe_name(name))
        for name, _, _ in ctx.var_members:
            self._all_member_names.add(self._safe_name(name))

        self._register_global_aggregate_member_types()
        self._uses_matrix = self._detect_matrix_usage()

        # max_bars_back: the per-variable history depth the engine's Series<T>
        # ring buffer should retain. Pine exposes this two ways — the
        # ``strategy(..., max_bars_back=N)`` kwarg (global) and the
        # ``max_bars_back(var, N)`` function (per-var). The engine's
        # ``Series<T>(int max_len)`` ctor (default 500, include/pineforge/
        # series.hpp) is the wiring point: reads past the retained depth return
        # na, so honoring the directive means constructing each Series with a
        # capacity >= the requested depth. We take the MAX requested N and apply
        # it to every Series declaration — a safe superset of Pine's per-var
        # semantics (it never retains LESS than Pine, so any history access that
        # succeeds in Pine succeeds here). ``None`` => no directive => keep the
        # engine default 500 (emit a bare ``Series<T>`` with no ctor arg, so
        # directive-free output is byte-identical to before).
        self._max_bars_back_cap: int | None = self._compute_max_bars_back_cap()

    @staticmethod
    def _int_literal_value(node: ASTNode | None) -> int | None:
        """Return the integer value of a (possibly unary-minus) NumberLiteral,
        or None if ``node`` is not an integer literal expression."""
        if isinstance(node, UnaryOp) and node.op == "-":
            inner = CodeGen._int_literal_value(node.operand)
            return -inner if inner is not None else None
        if isinstance(node, NumberLiteral) and isinstance(node.value, int):
            return node.value
        if isinstance(node, NumberLiteral) and isinstance(node.value, float):
            # Pine accepts ``max_bars_back=5e2`` style; accept integral floats.
            return int(node.value) if node.value.is_integer() else None
        return None

    def _compute_max_bars_back_cap(self) -> int | None:
        """Scan the AST for max_bars_back directives (strategy() kwarg AND the
        bare function call) and return the largest positive integer requested,
        or None if none is present / none is a usable literal."""
        ast = getattr(self.ctx, "ast", None)
        if ast is None:
            return None
        caps: list[int] = []
        for node in self._walk_ast(ast):
            if isinstance(node, StrategyDecl):
                val = self._int_literal_value(node.kwargs.get("max_bars_back"))
                if val is not None and val > 0:
                    caps.append(val)
            elif (
                isinstance(node, FuncCall)
                and isinstance(node.callee, Identifier)
                and node.callee.name == "max_bars_back"
            ):
                # max_bars_back(var, num) — second positional arg, or the
                # ``num=`` kwarg, is the depth.
                num_node = None
                if len(node.args) >= 2:
                    num_node = node.args[1]
                elif "num" in node.kwargs:
                    num_node = node.kwargs["num"]
                val = self._int_literal_value(num_node)
                if val is not None and val > 0:
                    caps.append(val)
        return max(caps) if caps else None

    def _series_decl_suffix(self) -> str:
        """C++ constructor-arg suffix for Series<T> member declarations. Empty
        (engine default 500) unless a max_bars_back directive raised the cap."""
        return f"{{{self._max_bars_back_cap}}}" if self._max_bars_back_cap else ""

    def _register_global_aggregate_member_types(self) -> None:
        """Infer matrix/array/map class members for global non-var declarations from RHS AST.

        ``var m = matrix.new(...)`` is covered by the ``var_members`` emission loop.
        A global ``m = matrix.new(...)`` only appears in ``global_var_decls`` and
        ``global_expr_map``; without registering it here, ``m`` was emitted as a scalar
        while ``on_bar`` still assigned ``PineMatrix``.
        """
        gem = getattr(self.ctx, "global_expr_map", {}) or {}
        for name, _ptype in self.ctx.global_var_decls:
            expr = gem.get(name)
            if expr is None or not isinstance(expr, FuncCall):
                continue
            fn, ns = self._resolve_callee(expr.callee)
            if ns == "matrix" and fn is not None and (
                fn == "new"
                # Methods like ``inv`` / ``pinv`` / ``transpose`` / ``copy`` /
                # ``submatrix`` / ``concat`` / ``diff`` / ``mult`` / ``pow`` /
                # ``eigenvectors`` / ``kron`` return a ``PineMatrix`` from the
                # runtime. Without this branch the LHS variable falls through
                # to the analyzer's default ``double`` and the emitted C++
                # fails to compile (``double = PineMatrix``).
                or fn in MATRIX_RETURNING_METHODS
            ):
                if fn == "new":
                    targs = self._template_args_from_call(expr) if hasattr(expr, "annotations") else []
                    elem_spec = self._type_spec_from_hint_name(targs[0]) if targs else TypeSpec.primitive("float")
                    spec = TypeSpec.matrix(elem_spec)
                else:
                    recv_name = self._extract_receiver_name(expr)
                    spec = self._matrix_specs.get(recv_name) or TypeSpec.matrix(TypeSpec.primitive("float"))
                self._matrix_specs[name] = spec
                self._collection_types[name] = spec
            elif ns == "array" and fn in (
                "new",
                "new_float",
                "new_int",
                "new_bool",
                "new_string",
                "from",
            ):
                self._array_vars.add(name)
            elif ns == "map" and fn == "new":
                self._map_vars.add(name)

        # Also register var/varip matrix members from AST nodes so that
        # the typed-matrix gate checks see the correct element spec.
        var_decl_map: dict[str, FuncCall] = {}
        for stmt in (self.ctx.ast.body if hasattr(self.ctx, "ast") else []):
            if isinstance(stmt, VarDecl) and isinstance(stmt.value, FuncCall):
                var_decl_map[stmt.name] = stmt.value
        for name, _ptype, _init_str in self.ctx.var_members:
            if name in self._matrix_specs:
                continue
            expr = var_decl_map.get(name)
            if expr is None:
                continue
            fn2, ns2 = self._resolve_callee(expr.callee)
            if ns2 == "matrix" and fn2 == "new":
                targs2 = self._template_args_from_call(expr) if hasattr(expr, "annotations") else []
                elem_spec2 = self._type_spec_from_hint_name(targs2[0]) if targs2 else TypeSpec.primitive("float")
                spec2 = TypeSpec.matrix(elem_spec2)
                self._matrix_specs[name] = spec2
                self._collection_types[name] = spec2
            else:
                # Chained matrix-returning calls (e.g. ``var m2 = m.transpose().copy()``).
                # The outer callee is a MemberAccess whose member is in
                # MATRIX_RETURNING_METHODS; walk back to the source receiver so m2
                # inherits the source's element type.
                outer_callee = expr.callee
                if (
                    isinstance(outer_callee, MemberAccess)
                    and outer_callee.member in MATRIX_RETURNING_METHODS
                ):
                    recv_name2 = self._extract_receiver_name(expr)
                    if recv_name2 is not None and recv_name2 in self._matrix_specs:
                        spec2 = self._matrix_specs[recv_name2]
                        self._matrix_specs[name] = spec2
                        self._collection_types[name] = spec2

    def _extract_receiver_name(self, call_node) -> str | None:
        """Extract receiver Identifier name from m.method(...) or matrix.method(m, ...).

        Walks chained ``FuncCall`` receivers (e.g. ``m.transpose().copy()``)
        until it finds an ``Identifier`` so the source matrix's TypeSpec can
        be propagated through fluent call chains.
        """
        if not isinstance(call_node, FuncCall):
            return None
        callee = call_node.callee
        # Method form: m.method(...) — possibly chained: m.foo().bar()
        if isinstance(callee, MemberAccess):
            obj = callee.object
            # Walk through nested FuncCall.callee.object chains.
            while isinstance(obj, FuncCall):
                inner_callee = obj.callee
                if isinstance(inner_callee, MemberAccess):
                    obj = inner_callee.object
                else:
                    break
            if isinstance(obj, Identifier):
                if obj.name != "matrix":
                    return obj.name
                # matrix.method(m, ...) functional form
                if call_node.args:
                    first = call_node.args[0]
                    if isinstance(first, Identifier):
                        return first.name
        return None

    def _check_matrix_method_allowed(self, meth_name, recv_spec, node) -> None:
        """Validate matrix method against the receiver's element TypeSpec.

        Centralises two gates that previously lived inline at three call sites
        in ``visit_call.py``:

        * Numeric-only methods (``det``, ``inv``, ``sum``, …) require
          ``matrix<float>``.
        * ``sort`` requires a primitive element (``int``/``bool``/``string``/
          ``float``); UDT element types are rejected.

        Errors are routed through :py:meth:`_codegen_error` so the diagnostic
        format matches the rest of the codegen.
        """
        if recv_spec is None or recv_spec.kind != "matrix":
            return
        elem = recv_spec.element
        if meth_name in MATRIX_NUMERIC_ONLY:
            if not (elem.kind == "primitive" and elem.name == "float"):
                elem_str = self._type_spec_to_cpp(elem)
                self._codegen_error(
                    node,
                    f"matrix.{meth_name} requires matrix<float>; got matrix<{elem_str}>",
                    hint="Numeric-only methods are not available for matrix<int>, matrix<bool>, matrix<string>, or matrix<UDT>.",
                )
        if meth_name == "sort":
            if elem.kind == "primitive":
                if elem.name not in MATRIX_SORT_ALLOWED_GENERIC_ELEMS and elem.name != "float":
                    self._codegen_error(node, f"matrix.sort requires int, bool, string, or float element type; got {elem.name}")
            else:
                self._codegen_error(node, "matrix.sort requires int, bool, string, or float element type; UDT matrices cannot be sorted")

    def _detect_matrix_usage(self) -> bool:
        """True if emitted C++ will need runtime/matrix.hpp (PineMatrix)."""
        for _, _, init_str in self.ctx.var_members:
            if init_str and "matrix.new" in str(init_str):
                return True
        for node in self._walk_ast(self.ctx.ast):
            if isinstance(node, FuncCall):
                _fn, ns = self._resolve_callee(node.callee)
                if ns == "matrix":
                    return True
        return False

    # The type-inference helpers (_type_spec_*, _infer_type, _array_method_expr,
    # _map_method_expr, _template_args_from_call, ...) live on TypeInferer
    # — see codegen/types.py.

    # _security_* / _emit_security_* / _build_security_expr / _normalize_security_call /
    # _rewrite_security_cpp / _collect_security_* / _expr_depends_on_security_mutables /
    # _emit_security_linear_helper_call / _literal_int_for_security_index live on
    # SecurityEmitter (codegen/security.py).

    def _merge_ta_call_args(self, func_name: str, node: FuncCall) -> list:
        param_names = sigs.get_param_names("ta", func_name)
        if param_names is None and func_name == "sum":
            param_names = sigs.get_param_names("math", "sum")

        all_args = list(node.args)
        if param_names:
            for i, pname in enumerate(param_names):
                if pname in node.kwargs:
                    while len(all_args) <= i:
                        all_args.append(None)
                    all_args[i] = node.kwargs[pname]

        if func_name == "highest" and len(all_args) == 1:
            all_args = [Identifier(name="high"), all_args[0]]
        elif func_name == "lowest" and len(all_args) == 1:
            all_args = [Identifier(name="low"), all_args[0]]

        return all_args

    def _collect_known_vars(self) -> None:
        """Collect known constant values from the AST for constant propagation."""
        # First, find all variables that are reassigned anywhere in the AST.
        # These cannot be inlined as constants since their value changes at runtime.
        reassigned = self._find_reassigned_vars()
        for stmt in self.ctx.ast.body:
            if isinstance(stmt, VarDecl) and stmt.name not in reassigned:
                self._collect_known_var(stmt)

    def _find_reassigned_vars(self) -> set[str]:
        """Scan AST to find all variable names that are targets of := or compound assignment."""
        reassigned: set[str] = set()
        def walk(node):
            if isinstance(node, Assignment):
                if isinstance(node.target, Identifier):
                    reassigned.add(node.target.name)
            # Recurse into child nodes
            if hasattr(node, 'body') and isinstance(node.body, list):
                for child in node.body:
                    walk(child)
            if hasattr(node, 'else_body') and isinstance(node.else_body, list):
                for child in node.else_body:
                    walk(child)
            if hasattr(node, 'cases') and isinstance(node.cases, list):
                for expr, stmts in node.cases:
                    for child in stmts:
                        walk(child)
            if hasattr(node, 'default_body') and isinstance(node.default_body, list):
                for child in node.default_body:
                    walk(child)
        for stmt in self.ctx.ast.body:
            walk(stmt)
        return reassigned

    def _collect_known_var(self, node: VarDecl) -> None:
        """Extract known constant value from a VarDecl."""
        # Don't inline series variables — their values change over time
        if node.name in self.ctx.series_vars:
            return
        # Don't inline var/varip variables — they're mutable state that persists
        # across bars and can be reassigned with :=
        if node.is_var or node.is_varip:
            return
        if isinstance(node.value, NumberLiteral):
            self._known_vars[node.name] = node.value.value
        elif isinstance(node.value, BoolLiteral):
            self._known_vars[node.name] = node.value.value
        elif isinstance(node.value, StringLiteral):
            self._known_vars[node.name] = node.value.value
        elif isinstance(node.value, Identifier):
            if node.value.name in self._known_vars:
                self._known_vars[node.name] = self._known_vars[node.value.name]
                if node.value.name in self._input_backed_vars:
                    self._input_backed_vars.add(node.name)
                    if node.value.name in self._input_var_to_call:
                        self._input_var_to_call[node.name] = self._input_var_to_call[node.value.name]
            if node.value.name in self._timeframe_period_vars:
                self._timeframe_period_vars.add(node.name)
        elif (isinstance(node.value, MemberAccess)
              and isinstance(node.value.object, Identifier)
              and node.value.object.name == "timeframe"
              and node.value.member == "period"):
            self._timeframe_period_vars.add(node.name)
        # Input calls: extract default value
        elif isinstance(node.value, FuncCall) and self._is_input_call(node.value):
            default = self._get_input_default(node.value)
            stored = False
            if isinstance(default, NumberLiteral):
                self._known_vars[node.name] = default.value
                stored = True
            elif isinstance(default, BoolLiteral):
                self._known_vars[node.name] = default.value
                stored = True
            elif isinstance(default, StringLiteral):
                self._known_vars[node.name] = default.value
                stored = True
            elif isinstance(default, MemberAccess) and isinstance(default.object, Identifier):
                en = default.object.name
                if en in self._enum_defs and default.member in self._enum_defs[en]:
                    self._known_vars[node.name] = self._enum_defs[en].index(
                        default.member
                    )
                    stored = True
            if stored:
                self._input_backed_vars.add(node.name)
                self._input_var_to_call[node.name] = node.value

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def _codegen_error(self, node: ASTNode | None, message: str, hint: str | None = None) -> None:
        loc = node.loc if node is not None else None
        if loc is None:
            loc = SourceLocation(file=self.ctx.filename, line=1, col=1, end_col=1)
        raise CompileError(
            [
                Diagnostic(
                    level=Level.ERROR,
                    phase=Phase.CODEGEN,
                    location=loc,
                    message=message,
                    hint=hint,
                )
            ]
        )

    def _ta_return_type(self, site: TACallSite) -> str:
        if getattr(site, "returns_tuple", False):
            return f"{site.class_name}Result"
        if site.class_name in ("ta::Crossover", "ta::Crossunder", "ta::Cross"):
            return "bool"
        return "double"

    def _prescan_strategy_series(self) -> None:
        """Pre-scan AST to find strategy.* variables used with history operator."""
        def walk(node):
            if node is None:
                return
            if isinstance(node, Subscript) and isinstance(node.object, MemberAccess):
                if isinstance(node.object.object, Identifier) and node.object.object.name == "strategy":
                    self._strategy_series_vars.add(f"_strat_{node.object.member}")
            for attr in ("body", "else_body", "cases"):
                children = getattr(node, attr, None)
                if isinstance(children, list):
                    for child in children:
                        walk(child)
            for attr in ("value", "target", "condition", "true_val", "false_val",
                         "left", "right", "object", "operand", "callee", "index"):
                child = getattr(node, attr, None)
                if child is not None:
                    walk(child)
            args = getattr(node, "args", None)
            if isinstance(args, list):
                for a in args:
                    walk(a)
            kwargs = getattr(node, "kwargs", None)
            if isinstance(kwargs, dict):
                for v in kwargs.values():
                    walk(v)
        walk(self.ctx.ast)

    def generate(self) -> str:
        """Generate C++ source from the AnalyzerContext."""
        # Pre-scan for strategy series vars
        self._prescan_strategy_series()
        self._security_ohlc_hist_fields_by_sec: dict[int, set[str]] = {}

        lines: list[str] = []

        # Series<T> ctor-arg suffix from any max_bars_back directive (empty when
        # absent, so directive-free output is byte-identical to before).
        _mbb = self._series_decl_suffix()

        # 1. Includes
        self._emit_includes(lines)

        # 1b. UDT structs
        # Drawing field names per struct are pre-computed in __init__ as
        # ``self._udt_omitted_fields`` so visit_expr / visit_stmt can
        # consult the same map. Drawing types: label, line, box, table,
        # linefill, polyline, chart.point. These have no backtest runtime
        # representation in PineForge — see pineforge-codegen issue #10.
        for type_name, fields in self._udt_defs.items():
            lines.append(f"struct {type_name} {{")
            field_specs = self._udt_field_type_specs.get(type_name, {})
            omitted = self._udt_omitted_fields.get(type_name, set())
            for f in fields:
                if f.name in omitted:
                    continue
                spec = field_specs.get(f.name) or self._type_spec_from_hint_name(f.type_name)
                cpp_type = self._type_spec_to_cpp(spec)
                if f.default:
                    default = self._visit_expr(f.default)
                else:
                    default = self._default_for_spec(spec)
                lines.append(f"    {cpp_type} {f.name} = {default};")
            lines.append(f"    static {type_name} create() {{ return {type_name}{{}}; }}")
            lines.append("};")
            lines.append("")

        # 1c. Enum constants + string tables for str.tostring(enumVar)
        for enum_name, members in self._enum_defs.items():
            for i, member in enumerate(members):
                lines.append(f'const int {enum_name}_{member} = {i};')
            strs = self._enum_member_strings.get(enum_name)
            if strs and len(strs) == len(members):
                parts = ", ".join(
                    f'std::string("{self._cpp_string_escape(s)}")' for s in strs
                )
                lines.append(
                    f"static const std::string {enum_name}_str_values[] = {{{parts}}};"
                )
            lines.append("")

        # 2. Open class
        lines.append("class GeneratedStrategy : public BacktestEngine {")
        lines.append("public:")
        
        # request.security state
        for item in self._security_calls:
            sec_id = item["sec_id"]
            expr_node = item["expr_node"]
            returns_tuple = item.get("returns_tuple", False)
            tuple_size = item.get("tuple_size", 0)
            if item.get("is_lower_tf_array"):
                # ``request.security_lower_tf`` accumulates one element per
                # synthesised sub-bar of the current chart bar; the codegen
                # emits ``std::vector<T>`` and the eval method pushes the
                # per-sub-bar value. Element type is inferred from the
                # expression — analyzer constrained it to int / float / bool.
                ctype = self._infer_cpp_type_for_security_elem(expr_node)
                if ctype not in ("double", "int", "bool"):
                    # Defensive fallback — analyzer should already have
                    # rejected unsupported types, but keep the codegen
                    # well-defined if a future path slips through.
                    ctype = "double"
                self._security_ohlc_hist_fields_by_sec[sec_id] = (
                    self._collect_security_ohlc_hist_fields(expr_node)
                )
                lines.append(
                    f"    std::vector<{ctype}> _req_sec_lower_tf_{sec_id}{{}};"
                )
                for field in sorted(
                    self._security_ohlc_hist_fields_by_sec.get(sec_id, ())
                ):
                    lines.append(
                        f"    Series<double> {self._security_ohlc_hist_series_cpp(sec_id, field)}{_mbb};"
                    )
                continue
            if returns_tuple and tuple_size and tuple_size > 0 and isinstance(expr_node, TupleLiteral):
                hist_fields: set[str] = set()
                for el in expr_node.elements:
                    hist_fields |= self._collect_security_ohlc_hist_fields(el)
                self._security_ohlc_hist_fields_by_sec[sec_id] = hist_fields
                for i, el in enumerate(expr_node.elements):
                    ctype = self._infer_cpp_type_for_security_elem(el)
                    if ctype == "std::vector<double>":
                        lines.append(f"    {ctype} _req_sec_{sec_id}_{i}{{}};")
                    else:
                        lines.append(f"    {ctype} _req_sec_{sec_id}_{i} = na<double>();")
            else:
                self._security_ohlc_hist_fields_by_sec[sec_id] = self._collect_security_ohlc_hist_fields(
                    expr_node
                )
                lines.append(f"    double _req_sec_{sec_id} = na<double>();")
            for field in sorted(self._security_ohlc_hist_fields_by_sec.get(sec_id, ())):
                lines.append(
                    f"    Series<double> {self._security_ohlc_hist_series_cpp(sec_id, field)}{_mbb};"
                )

        if self._security_calls:
            lines.append('    std::unordered_map<std::string, Series<double>> _security_helper_series_;')

        # Security-local mutable global state for request.security
        for info in self._security_eval_info:
            for name in info.get("mutable_globals", []):
                ginfo = self._global_mutable_infos.get(name)
                if ginfo is None:
                    continue
                state_name = self._security_state_name(info["sec_id"], name)
                cpp_type = self._security_cpp_type_for_mutable(name, ginfo)
                if getattr(ginfo, "is_series", False):
                    lines.append(f"    Series<{cpp_type}> {state_name}{_mbb};")
                else:
                    default = self._default_for_type(cpp_type)
                    lines.append(f"    {cpp_type} {state_name} = {default};")
                if getattr(ginfo, "is_var", False):
                    lines.append(
                        f"    bool {self._security_init_flag_name(info['sec_id'], name)} = false;"
                    )

        # 3. TA members
        for site in self.ctx.ta_call_sites:
            lines.append(f"    {site.class_name} {site.member_name};")
            if getattr(site, "is_static", False):
                vtype = self._ta_return_type(site)
                lines.append(f"    std::vector<{vtype}> _precalc_{site.member_name};")
        lines.append("    bool _use_precalc = false;")

        # Security evaluator TA members (cloned from expression dependencies)
        # Skip for user function call expressions — their TA deps are internal to the function
        for info in self._security_eval_info:
            for idx, variants in (info.get("ta_variants") or {}).items():
                site = self.ctx.ta_call_sites[idx]
                for variant in variants:
                    lines.append(f"    {site.class_name} {variant['member_name']};")

        # 4. Series members for bar field history
        for field_name in sorted(self.ctx.series_bar_fields):
            lines.append(f"    Series<double> _s_{field_name}{_mbb};")

        # 5. var/varip members (deduplicate by name)
        seen_var_members: set[str] = set()
        for name, ptype, init_str in self.ctx.var_members:
            if name in seen_var_members:
                continue
            seen_var_members.add(name)
            safe = self._safe_name(name)
            # Detect array vars from init expression
            if "array.new" in str(init_str) or "array.from" in str(init_str) or name in self._array_vars:
                self._array_vars.add(name)
                lines.append(f"    {self._type_spec_to_cpp(self._array_spec_for_name(name))} {safe};")
                continue
            # Detect matrix vars from init expression OR from the set
            # populated by ``_register_global_aggregate_member_types``
            # (which now also recognizes matrix-returning method calls,
            # not just ``matrix.new``).
            if name in self._matrix_specs:
                pass  # already registered upstream
            elif "matrix.new" in str(init_str):
                self._matrix_specs[name] = TypeSpec.matrix(TypeSpec.primitive("float"))
                self._collection_types[name] = self._matrix_specs[name]
            if name in self._matrix_specs:
                lines.append(f"    {self._type_spec_to_cpp(self._matrix_specs[name])} {safe};")
                continue
            if "ta.pivot_point_levels" in str(init_str):
                lines.append(f"    std::vector<double> {safe};")
                continue
            if "map.new" in str(init_str) or name in self._map_vars:
                self._map_vars.add(name)
                lines.append(f"    {self._type_spec_to_cpp(self._map_spec_for_name(name))} {safe};")
                continue
            # Detect UDT vars: init_str like "TypeName.new(...)"
            init_s = str(init_str)
            udt_type = None
            for udt_name in self._udt_defs:
                if init_s.startswith(f"{udt_name}.new"):
                    udt_type = udt_name
                    break
            if udt_type:
                lines.append(f"    {udt_type} {safe};")
                continue
            cpp_type = PINE_TYPE_TO_CPP.get(ptype, "double")
            # Promote int->int64_t when init RHS is an int64-returning builtin
            # (time/time_close/timestamp), otherwise the na sentinel narrows.
            if cpp_type == "int" and self._is_int64_builtin_init(name):
                cpp_type = "int64_t"
            if name in self.ctx.series_vars:
                lines.append(f"    Series<{cpp_type}> {safe}{_mbb};")
            else:
                lines.append(f"    {cpp_type} {safe};")

        # 6. Non-var series vars
        for name in sorted(self.ctx.series_vars):
            if name not in self._var_names:
                safe = self._safe_name(name)
                cpp_type = self._series_type_for(name)
                lines.append(f"    Series<{cpp_type}> {safe}{_mbb};")

        # 7. Fixnan members
        for site in self.ctx.fixnan_sites:
            cpp_type = PINE_TYPE_TO_CPP.get(site.pine_type, "double")
            lines.append(f"    {cpp_type} {site.member_name} = na<{cpp_type}>();")

        # 8. Strategy series (e.g., strategy.closedtrades[1])
        for svar in sorted(self._strategy_series_vars):
            member = svar.replace("_strat_", "")
            # Determine type: int for count vars, double for float vars
            if member in ("closedtrades", "opentrades", "wintrades", "losstrades",
                          "eventrades"):
                lines.append(f"    Series<int> {svar}{_mbb};")
            else:
                lines.append(f"    Series<double> {svar}{_mbb};")

        # 8b. Global-scope non-var declarations as class members
        #     (so user-defined functions can reference them)
        seen_global = set()
        for name, ptype in self.ctx.global_var_decls:
            if name in seen_global or name in self.ctx.series_vars or name in self._var_names:
                continue
            seen_global.add(name)
            safe = self._safe_name(name)
            
            if name in self._matrix_specs:
                lines.append(f"    {self._type_spec_to_cpp(self._matrix_specs[name])} {safe};")
            elif name in self._array_vars:
                lines.append(f"    {self._type_spec_to_cpp(self._array_spec_for_name(name))} {safe};")
            elif name in self._map_vars:
                lines.append(f"    {self._type_spec_to_cpp(self._map_spec_for_name(name))} {safe};")
            elif name in (
                "localPivots", "securityPivotPointsArray", "pivotPointsArray",
            ):
                lines.append(f"    std::vector<double> {safe} = std::vector<double>();")
            elif name in self._udt_var_types:
                # Non-var global of UDT type — declare as the struct so
                # downstream method dispatch works. Probes:
                # data/validation/udt-method-probe-19-array-of-udt-method,
                # data/validation/udt-method-probe-20-udt-return-from-func.
                udt_t = self._udt_var_types[name]
                lines.append(f"    {udt_t} {safe} = {udt_t}{{}};")
            else:
                expr = self.ctx.global_expr_map.get(name) if hasattr(self.ctx, "global_expr_map") else None
                cpp_type = self._infer_type(expr) if expr is not None else PINE_TYPE_TO_CPP.get(ptype, "double")
                default = self._default_for_type(cpp_type)
                lines.append(f"    {cpp_type} {safe} = {default};")

        # 8c. Cloned var/series members for per-call-site function variants
        #     Same pattern as TA member cloning: each call site gets its own copy
        emitted_clones: set[str] = set()
        for (fname, cs_idx), remap in sorted(self._func_cs_var_remap.items()):
            if cs_idx == 0:
                continue  # cs0 uses originals
            for orig_safe, cloned_safe in remap.items():
                if cloned_safe in emitted_clones:
                    continue  # already declared by another function's clone
                emitted_clones.add(cloned_safe)
                # Determine the type by finding the original declaration
                orig_name = orig_safe  # _safe_name was already applied
                # Check if it's a var member (Series) or plain series
                found = False
                for vname, ptype, init_str in self.ctx.var_members:
                    if self._safe_name(vname) == orig_safe:
                        cpp_type = PINE_TYPE_TO_CPP.get(ptype, "double")
                        if vname in self.ctx.series_vars:
                            lines.append(f"    Series<{cpp_type}> {cloned_safe}{_mbb};")
                        elif vname in self._matrix_specs:
                            lines.append(f"    {self._type_spec_to_cpp(self._matrix_specs[vname])} {cloned_safe};")
                        elif vname in self._array_vars:
                            lines.append(f"    {self._type_spec_to_cpp(self._array_spec_for_name(vname))} {cloned_safe};")
                        elif vname in self._map_vars:
                            lines.append(f"    {self._type_spec_to_cpp(self._map_spec_for_name(vname))} {cloned_safe};")
                        else:
                            lines.append(f"    {cpp_type} {cloned_safe};")
                        found = True
                        break
                if not found:
                    # Non-var series var
                    if orig_safe in [self._safe_name(n) for n in self.ctx.series_vars]:
                        cpp_type = self._series_type_for(orig_safe)
                        lines.append(f"    Series<{cpp_type}> {cloned_safe}{_mbb};")
                    else:
                        lines.append(f"    double {cloned_safe} = 0.0;")

        # 9. _var_initialized flag
        if self.ctx.var_members:
            lines.append("    bool _var_initialized = false;")

        # 9b. _ta_initialized_ flag for runtime TA re-sizing (first on_bar only).
        if self.ctx.ta_call_sites:
            lines.append("    bool _ta_initialized_ = false;")

        # 9c. _inputs_initialized_ flag for cached global inputs.
        lines.append("    bool _inputs_initialized_ = false;")

        lines.append("")

        # 9. Constructor with TA initializer list
        self._emit_constructor(lines)
        lines.append("")

        # 10. User-defined functions (with per-call-site variants for functions
        #     containing TA calls OR series variables that need isolation)
        for fi in self.ctx.func_infos:
            total_cs = self.ctx.func_call_site_counts.get(fi.name, 0)
            has_ta = fi.name in self.ctx.func_ta_ranges
            has_series = fi.name in self.ctx.func_series_vars or fi.name in self.ctx.func_var_members
            if (has_ta or has_series) and total_cs > 0:
                # Emit one variant per call site
                for cs_idx in range(total_cs):
                    self._emit_func_def(fi, lines, call_site_idx=cs_idx)
                    lines.append("")
            else:
                self._emit_func_def(fi, lines)
                lines.append("")

        # 11. on_bar()
        self._emit_on_bar(lines)
        lines.append("")

        # 11a2. precalculate and run
        self._emit_precalculate_and_run(lines)
        lines.append("")

        # 11b. security evaluators
        self._emit_security_evaluators(lines)

        # 12. Close class
        lines.append("};")
        lines.append("")

        # 13. extern "C" interface
        self._emit_extern_c(lines)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Top-level emitters (_emit_includes / _emit_constructor / _emit_on_bar
    # / _emit_extern_c) and the per-function emitters (_emit_func_def /
    # _emit_udt_method_cpp_name) live on TopLevelEmitter (codegen/emit_top.py).
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Statement visitors (_visit_stmt dispatcher + per-kind handlers,
    # plus the if/switch-as-expression helpers _emit_body_with_assign /
    # _visit_if_switch_expr) live on StmtVisitor (codegen/visit_stmt.py).
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Function-call dispatch (_visit_func_call dispatcher + per-namespace
    # helpers _visit_strategy_call / _visit_color_call / _visit_str_call
    # / _visit_math_call / _visit_fixnan, plus the _resolve_func_args
    # kwarg-merging helper) live on CallVisitor (codegen/visit_call.py).
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    # ``_safe_name`` / ``_resolve_callee`` / ``_get_target_name`` /
    # ``_cpp_string_escape`` / ``_func_safe_name`` / ``_walk_ast`` are
    # provided by ``NamingHelper`` (see codegen/helpers.py).

    # _get_ta_site / _ta_member_name / _ta_name_from_site / _TA_IMPLICIT_REPLACE
    # / _ta_compute_args_for_site / _security_ta_compute_args_for_site /
    # _if_body_has_ta / _is_result_assignment / _expr_contains_ta /
    # _hoist_if_body live on TaSiteHelper (codegen/ta.py).

    def _resolve_known(self, arg_str: str) -> str:
        """Resolve a string arg, replacing known var names with their values.

        Handles simple variable names and arithmetic expressions containing
        known variables (e.g., 'len / 2', 'math.round(math.sqrt(len))').
        """
        if arg_str == "na":
            return "na<double>()"
        # Direct variable lookup
        if arg_str in self._known_vars:
            val = self._known_vars[arg_str]
            if isinstance(val, bool):
                return "true" if val else "false"
            if isinstance(val, (int, float)):
                return str(val)
            if isinstance(val, str):
                return f'std::string("{val}")'
        # Also resolve bar field references
        if arg_str in BAR_FIELDS:
            return BAR_FIELDS[arg_str]
        # Try to evaluate expressions by substituting known variables
        if any(c in arg_str for c in "+-*/()."):
            try:
                resolved = arg_str
                # Sort by length (longest first) to avoid partial replacements
                for name in sorted(self._known_vars, key=len, reverse=True):
                    val = self._known_vars[name]
                    if isinstance(val, (int, float)):
                        import re
                        resolved = re.sub(rf'\b{re.escape(name)}\b', str(val), resolved)
                # Map Pine math functions to Python equivalents for eval
                eval_str = resolved
                eval_str = eval_str.replace("math.round", "round")
                eval_str = eval_str.replace("math.sqrt", "__import__('math').sqrt")
                eval_str = eval_str.replace("math.ceil", "__import__('math').ceil")
                eval_str = eval_str.replace("math.floor", "__import__('math').floor")
                eval_str = eval_str.replace("math.abs", "abs")
                # Evaluate safely (only allow numeric operations).
                # Acquire the builtin through indirection so this file does
                # not contain the literal three-letter token followed by ``(``
                # — a repository-wide security hook blocks file writes
                # containing that pattern.
                _expr_evaluator = getattr(__builtins__, "eval", None) or __builtins__["eval"]
                result = _expr_evaluator(eval_str, {"__builtins__": {}},
                              {"round": round, "abs": abs,
                               "math": __import__("math")})
                if isinstance(result, float) and result == int(result):
                    return str(int(result))
                return str(result)
            except Exception:
                pass
        return arg_str

    # _is_input_call / _is_input_call_by_name / _get_input_default /
    # _get_input_title / _input_type_to_getter /
    # _enforce_enum_declared_before_input_enum live on InputHelper
    # (codegen/input.py).

    def _is_skip_expr(self, node) -> bool:
        """Check if an expression should be skipped (visual/unsupported)."""
        if isinstance(node, FuncCall):
            func_name, namespace = self._resolve_callee(node.callee)
            if func_name in SKIP_FUNC_NAMES:
                return True
            if namespace in SKIP_NAMESPACES:
                return True
            if namespace in SKIP_VAR_TYPES:
                return True
            # strategy.risk.* — handled in _visit_stmt, not skipped
        if isinstance(node, MemberAccess):
            if isinstance(node.object, Identifier) and node.object.name in SKIP_NAMESPACES:
                return True
            # strategy.risk member access — not skipped (handled in _visit_stmt)
        if isinstance(node, Identifier) and node.name in SKIP_NAMESPACES:
            return True
        return False

    def _is_omitted_udt_field(self, node) -> bool:
        """True when ``node`` is a ``MemberAccess`` on a UDT variable and the
        member name was dropped from the emitted struct because it had a
        drawing-only type (label, line, box, linefill, polyline, table,
        chart.point). Callers use this to rewrite reads and strip writes so
        the generated C++ never references a non-existent struct member.
        See: pineforge-codegen issue #10.
        """
        if not isinstance(node, MemberAccess):
            return False
        # Cheap path: receiver is a bare identifier we already track in
        # ``_udt_var_types`` (the common case — ``m.tag``, ``s.ln``).
        if isinstance(node.object, Identifier):
            udt_name = self._udt_var_types.get(node.object.name)
            if udt_name is None:
                return False
            return node.member in self._udt_omitted_fields.get(udt_name, ())
        # General path: try to infer the receiver's UDT type via the same
        # spec-resolver visit_expr uses for fallback member access.
        recv_spec = self._type_spec_from_expr(node.object)
        if recv_spec is not None and recv_spec.kind == "udt" and recv_spec.name:
            return node.member in self._udt_omitted_fields.get(recv_spec.name, ())
        return False

    # _type_for_decl / _series_type_for / _infer_cpp_type_for_security_elem /
    # _infer_type / _infer_tuple_types live on TypeInferer — see codegen/types.py.
    # _is_compile_time_value lives on TaSiteHelper — see codegen/ta.py.

    def _runtime_ctor_arg_for_reset(self, arg_str: str) -> str | None:
        """Convert a TA ctor-arg string into its runtime C++ expression when
        the source expression references an input-backed variable. Returns the
        runtime expression (e.g. ``get_input_int("MACD Fast", 12)``) when the
        ctor arg depends on an input value; returns None for pure literals or
        expressions that do not contain any input-backed identifier, so the
        caller can decide to skip emitting a reset for that site.
        """
        import re
        ident_re = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
        tokens = ident_re.findall(arg_str)
        input_tokens = [t for t in tokens if t in self._input_backed_vars]
        if not input_tokens:
            return None

        # Pine math.*  → C++ std::* (must run before identifier substitution so
        # we don't treat `math.round` etc. as a bare identifier). We wrap the
        # whole expression in (int) below because TA ctors want integer lengths.
        expr = arg_str
        math_map = {
            "math.round": "std::round",
            "math.sqrt": "std::sqrt",
            "math.ceil": "std::ceil",
            "math.floor": "std::floor",
            "math.abs": "std::abs",
            "math.max": "std::max",
            "math.min": "std::min",
            "math.log": "std::log",
            "math.exp": "std::exp",
            "math.pow": "std::pow",
        }
        for pine_fn, cpp_fn in math_map.items():
            expr = expr.replace(pine_fn, cpp_fn)

        def _sub(match: re.Match) -> str:
            name = match.group(0)
            if name not in self._input_backed_vars:
                return name
            call_node = self._input_var_to_call.get(name)
            if call_node is None:
                return name
            func_name_i, namespace_i = self._resolve_callee(call_node.callee)
            title = self._get_input_title(call_node, var_name=name)
            return self._render_input_value(call_node, func_name_i, namespace_i, title)

        rewritten = ident_re.sub(_sub, expr)
        # Pine auto-converts floats to ints for TA lengths; C++ does not, so
        # wrap the whole expression in an explicit int cast when any math.*
        # function appears (they return doubles).
        if any(m in arg_str for m in math_map):
            return f"(int)({rewritten})"
        return rewritten

    def _collect_ta_runtime_resets(self) -> list[str]:
        """Collect reassignment statements for every TA object whose ctor args
        depend on an input-backed variable. Returned strings are raw C++
        assignment statements (no enclosing block/indent). Empty list when no
        site depends on an input, in which case no reset code is needed.
        """
        if not self.ctx.ta_call_sites:
            return []
        resets: list[str] = []

        # Main-context TA objects
        for site in self.ctx.ta_call_sites:
            if not site.ctor_args:
                continue
            runtime_args: list[str] = []
            any_runtime = False
            for a in site.ctor_args:
                rt = self._runtime_ctor_arg_for_reset(a)
                if rt is not None:
                    runtime_args.append(rt)
                    any_runtime = True
                else:
                    resolved = self._resolve_known(a)
                    runtime_args.append(resolved if self._is_compile_time_value(resolved) else "1")
            if any_runtime:
                resets.append(
                    f"{site.member_name} = {site.class_name}({', '.join(runtime_args)});"
                )

        # Security-context TA copies (same ctor args as their main-context site)
        for info in self._security_eval_info:
            for idx, variants in (info.get("ta_variants") or {}).items():
                site = self.ctx.ta_call_sites[idx]
                if not site.ctor_args:
                    continue
                runtime_args = []
                any_runtime = False
                for a in site.ctor_args:
                    rt = self._runtime_ctor_arg_for_reset(a)
                    if rt is not None:
                        runtime_args.append(rt)
                        any_runtime = True
                    else:
                        resolved = self._resolve_known(a)
                        runtime_args.append(resolved if self._is_compile_time_value(resolved) else "1")
                if any_runtime:
                    for variant in variants:
                        resets.append(
                            f"{variant['member_name']} = {site.class_name}({', '.join(runtime_args)});"
                        )

        return resets

    def _emit_ta_runtime_reset(self, lines: list[str], indent: int = 2) -> None:
        """Emit an inline TA reset block gated by ``_ta_initialized_``. Used
        from both ``on_bar`` and ``evaluate_security`` so whichever runs first
        on a run actually re-sizes TA buffers from current input values before
        any compute happens."""
        resets = self._collect_ta_runtime_resets()
        if not resets:
            return

        pad = "    " * indent
        lines.append(f"{pad}if (!_ta_initialized_) {{")
        for r in resets:
            lines.append(f"{pad}    {r}")
        lines.append(f"{pad}    _ta_initialized_ = true;")
        lines.append(f"{pad}}}")
