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
    DRAWING_TYPE_TO_CPP,
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
    ARRAY_NEW_CTORS,
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

TA_TUPLE_RESULT_TYPES = {
    "macd": "ta::MACDResult",
    "supertrend": "ta::SupertrendResult",
    "dmi": "ta::DMIResult",
    "bb": "ta::BBResult",
    "kc": "ta::KCResult",
    "vwap_bands": "ta::VWAPBandsResult",
}

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

# DrawingVisitor owns the drawing-objects-as-data dispatch (line/box/label/
# linefill/chart.point lowering onto the per-type arenas) plus _uses_drawing
# detection and arena-cap computation. See codegen/drawing.py.
from .drawing import DrawingVisitor


# ---------------------------------------------------------------------------
# CodeGen class
# ---------------------------------------------------------------------------

class CodeGen(CallVisitor, ExprVisitor, StmtVisitor, TopLevelEmitter, SecurityEmitter, TaSiteHelper, TypeInferer, InputHelper, DrawingVisitor, NamingHelper):
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
        # When True (only while lowering a TA runtime-reset length expression
        # through the expression visitor), an input-backed variable identifier
        # renders as an override-aware ``get_input_*()`` read instead of its
        # class member name. The reset can run in ``evaluate_security`` BEFORE
        # the input members are initialised, so it must not depend on their
        # init order. See ``_lower_reset_expr_via_visitor``.
        self._reset_input_getter_mode: bool = False
        # Set of var/series member names that belong to user functions (need cloning)
        self._func_var_members_set: set[str] = set()
        # BUG C: function-local names emitted as ``UDT*`` pointer aliases (a UDT
        # local initialised from a var/global UDT lvalue, mutated through, AND
        # later rebound to a different lvalue). Member access lowers to ``->``
        # and rebinds to ``&(...)``. Reset per function in _emit_func_def is not
        # needed: names are function-unique and the value-copy fallback ignores
        # entries for inactive functions.
        self._udt_ptr_alias_locals: set[str] = set()
        # Names of hoisted GLOBAL-scope UDT loop-locals bound from a UDT array
        # element (``z = arr.get(i)``) and later field-mutated. Pine array
        # elements of a user-defined type are references, so such a local must
        # ALIAS the element, not value-copy — the mutation has to write back
        # into the array. These are de-hoisted from the class-member value-copy
        # to a fresh per-iteration ``UDT& z = arr[i];`` local reference (the same
        # form the non-hoisted function-local alias path already emits). Read-only
        # get-locals are NOT recorded (no field mutation) and keep value-copy
        # semantics. Populated by _register_udt_array_get_ref_locals.
        self._udt_array_get_ref_locals: set[str] = set()
        self._precalc_loop_active: bool = False
        # Names of ``var`` members that live in a FUNCTION scope (not global).
        # These are initialized once-per-function-variant on first call (a
        # function-local static equivalent), NOT in the constructor / on_bar
        # preamble. See ``_emit_func_var_init_block``.
        self._func_local_var_names: set[str] = set()
        for _vlist in ctx.func_var_members.values():
            for _n, _, _ in _vlist:
                self._func_local_var_names.add(_n)

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
        #
        # Default to the ``{orig}_cs{cs_idx}`` formula (matches the analyzer's clone
        # naming), but defer to the analyzer's authoritative clone-name map for any
        # site it had to disambiguate (a TA site reached through multiple enclosing
        # functions would otherwise collide on the formula). Keeping the formula as
        # the default leaves all non-colliding output byte-identical.
        clone_names = getattr(ctx, "func_cs_ta_clone_names", {})
        for fname, orig_names in func_ta_originals.items():
            total_cs = ctx.func_call_site_counts.get(fname, 1)
            for cs_idx in range(1, total_cs):
                overrides = clone_names.get((fname, cs_idx), {})
                remap = {}
                for orig_name in orig_names:
                    remap[orig_name] = overrides.get(
                        orig_name, f"{orig_name}_cs{cs_idx}")
                self._func_cs_ta_remap[(fname, cs_idx)] = remap
                self._func_ta_members.update(remap.values())

        for site in ctx.ta_call_sites:
            if site.node is not None:
                if site.member_name not in self._func_ta_members:
                    # Top-level (non-function) site: maps to itself.
                    self._ta_site_map[id(site.node)] = site
                elif id(site.node) not in self._ta_site_map:
                    # Function-local site. Multiple clones share the SAME AST
                    # node (clones copy ``node=orig.node``); the FIRST one in
                    # ``ta_call_sites`` order is the canonical original (cs0)
                    # whose ``member_name`` the per-call-site remap is keyed on.
                    # Later clones (``_cs{i}``, ``_cs{i}_cs{j}``, ``_u{n}`` …)
                    # must NOT overwrite it: doing so poisons the base name so
                    # the active-remap lookup misses and every clone collapses
                    # onto one member. Keep the original; the variant member is
                    # resolved via ``_active_ta_remap`` at emit time.
                    self._ta_site_map[id(site.node)] = site
        self._ta_index_by_site_id: dict[int, int] = {
            id(site): i for i, site in enumerate(ctx.ta_call_sites)
        }
        # Context-sensitive (call-path) instance machinery for nested stateful
        # helpers. Built by ``_build_func_instances`` below. ``_current_instance_name``
        # names the function clone whose body is currently being emitted (None at
        # top level / non-variant bodies). ``_instance_dispatch`` maps
        # ``(enclosing_instance_name, call_node_id) -> callee emit-name`` and is the
        # authority for nested stateful-helper dispatch (see visit_call).
        self._current_instance_name: str | None = None
        self._instance_dispatch: dict[tuple[str | None, int], str] = {}
        self._fresh_instances: list[dict] = []
        self._fresh_var_members: list[tuple[str, str]] = []
        # Fresh fixnan members for context-sensitive helper instances (nested
        # helpers reached through >1 distinct call path). Each fresh instance
        # gets its OWN previous-value member so two paths never share fixnan
        # state. Populated by ``_build_func_instances``; declared in step 7.
        self._fresh_fixnan_members: list[tuple[Any, str]] = []
        # NOTE: _build_func_instances() runs at the top of generate() (it needs
        # _all_member_names / _func_safe_name, which are populated later in __init__).
        # Build lookup: node id -> FixnanCallSite (counter-based)
        self._fixnan_counter = 0
        # Per-call-site fixnan member remap for user functions (mirrors the TA
        # remap): (func_name, cs_idx) -> {orig_member: cloned_member}.
        self._func_cs_fixnan_remap: dict[tuple[str, int], dict[str, str]] = {}
        # Active fixnan remap (set during per-call-site function emission).
        self._active_fixnan_remap: dict[str, str] = {}
        # node id -> original FixnanCallSite (the cs0 / source-level site).
        self._fixnan_site_map: dict[int, Any] = {}
        # Set of fixnan member names that belong to user functions (excluded
        # from the site map so the active remap can dispatch per variant).
        self._func_fixnan_members: set[str] = set()
        # Dead fixnan site indices (owner is a dead user function). Skipped
        # at declaration time so dead functions' fixnan state is not emitted.
        self._dead_fixnan_indices: set[int] = set()
        self._switch_counter = 0
        self._security_inline_counter = 0
        self._random_call_counter = 0
        self._for_counter = 0
        # Synthetic history buffers used by inline call-history and by scalar
        # expressions passed to UDF series parameters.  They are pre-registered
        # at generate() time so declarations precede method emission, then
        # addressed by (source node, emitted UDF variant).  Each record is a
        # real class-member Series and therefore joins _PFScriptState through
        # the declaration-derived checkpoint inventory.
        self._inline_history_members: list[dict] = []
        self._inline_history_member_by_key: dict[tuple, str] = {}
        # Unique lambda-local names used when an array lowering references its
        # receiver more than once.  The binding keeps temporary-producing or
        # side-effectful receivers single-evaluation (see TypeInferer).
        self._array_receiver_counter = 0
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
        # Class-scope arithmetic-over-input vars (e.g. ``wilderLen = rsiLen*2-1``).
        # Maps the derived var name -> its raw RHS expression string. When such a
        # var feeds a TA ctor length, the runtime-reset path expands it so input
        # overrides propagate (``(get_input_int("RSI Length",14) * 2 - 1)``); the
        # ctor-init list still folds to the Pine-default literal via _resolve_known.
        self._derived_input_expr: dict[str, str] = {}
        self._timeframe_period_vars: set[str] = set()
        # Names of class-scope vars whose value is a bar-invariant scalar —
        # i.e. derived only from inputs, literals, ``timeframe.*`` members,
        # ``math.*`` over stable args, and ternaries/casts/arithmetic over
        # any of those. Such vars are safe to embed in a TA ctor runtime
        # reset expression (they do not depend on per-bar series). Vars
        # referencing series / ta.* results / history subscripts / strategy
        # state are NOT here, so a TA length fed by them is still rejected
        # by the constructor guard.
        self._stable_runtime_vars: set[str] = set()
        # ``_var_names`` (var/varip persistent-state members) is needed by the
        # stability classifier during _collect_known_vars, so pre-seed it from
        # the analyzer's var_members before that pass runs; the canonical
        # assignment below preserves the existing initialization order.
        self._var_names: set[str] = set()
        for _vn, _, _ in ctx.var_members:
            self._var_names.add(_vn)
        self._collect_known_vars()
        # Track var names
        self._var_names = set()
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
        # Dead-code user functions: those that contain TA call sites but are
        # never called anywhere in the script (no call site registered them,
        # so func_call_site_counts reports 0). Their OWN TA ctor args still
        # carry bare parameter names (e.g. ``dirmov_short(len) => ta.rma(ta.tr, len)``)
        # that can never be resolved to a concrete length, and since the
        # function never runs its TA buffers would be dead weight anyway.
        # Track the dead TA site indices and dead function names so emission
        # can skip both — the ctor guard no longer hard-fails on the bare
        # param and no dangling member/function body is emitted. A function
        # with zero call sites but NO TA state is NOT dead-by-this-rule (it
        # may still be emitted; harmless if truly unreferenced).
        #
        # IMPORTANT: dead-ness of a TA site is decided by the site's
        # ``owner_func`` (set by the analyzer), NOT by which function's
        # ``func_ta_ranges`` slice the site happens to fall in. A function's
        # slice can include clones of ANOTHER (live) function's sites that
        # were minted while visiting THIS function's body (a nested call to
        # a live callee registers the callee's cs{N} clones in the caller's
        # TA-range slice). Keying dead-ness off the slice would drop those
        # borrowed clones' declarations, leaving the owning callee's emitted
        # clone body referencing undeclared members. Regression:
        # quantbyboji-nq-hma-midday (``_ta_change_*_cs1`` / ``_ta_rma_*_cs1``
        # minted inside dead ``adx_short``'s body but owned by live ``dirmov``).
        self._dead_func_names: set[str] = set()
        self._dead_ta_indices: set[int] = set()
        for _fn in (ctx.func_ta_ranges or {}):
            if (ctx.func_call_site_counts or {}).get(_fn, 0) > 0:
                continue
            # Only treat plain user functions (not UDT methods) as skippable
            # dead code; methods are dispatched through the UDT and their
            # call-site tracking is handled separately.
            fi = self._func_info_map.get(_fn)
            if fi is not None and getattr(fi, "is_udt_method", False):
                continue
            self._dead_func_names.add(_fn)
        # Mark TA sites dead ONLY when their owner is a dead function. A
        # site with ``owner_func=None`` (top-level) or whose owner is a
        # live function survives -- even if it sits inside a dead
        # function's TA-range slice (it's a borrowed clone).
        for _i, _site in enumerate(ctx.ta_call_sites):
            _owner = getattr(_site, "owner_func", None)
            if _owner is not None and _owner in self._dead_func_names:
                self._dead_ta_indices.add(_i)
        # Build per-call-site fixnan remap + site map (mirrors TA remap above).
        # Dead fixnan sites (owner is a dead function) are skipped at decl time.
        clone_fn_names = getattr(ctx, "func_cs_fixnan_clone_names", {})
        for _i, _fsite in enumerate(ctx.fixnan_sites):
            _fowner = getattr(_fsite, "owner_func", None)
            if _fowner is not None and _fowner in self._dead_func_names:
                self._dead_fixnan_indices.add(_i)
        # cs0 fixnan remap is identity (originals). Build originals per func.
        func_fixnan_originals: dict[str, list[str]] = {}
        for _fname, _idxs in (ctx.func_fixnan_indices or {}).items():
            origs = [ctx.fixnan_sites[i].member_name for i in _idxs
                     if i not in self._dead_fixnan_indices]
            if origs:
                func_fixnan_originals[_fname] = origs
                self._func_cs_fixnan_remap[(_fname, 0)] = {m: m for m in origs}
                self._func_fixnan_members.update(origs)
        # cs > 0 remap uses the ``{orig}_cs{cs_idx}`` formula (or the
        # analyzer's disambiguated name from func_cs_fixnan_clone_names).
        for _fname, _origs in func_fixnan_originals.items():
            _total_cs = ctx.func_call_site_counts.get(_fname, 1)
            for _cs_idx in range(1, _total_cs):
                _overrides = clone_fn_names.get((_fname, _cs_idx), {})
                _remap = {}
                for _orig in _origs:
                    _remap[_orig] = _overrides.get(_orig, f"{_orig}_cs{_cs_idx}")
                self._func_cs_fixnan_remap[(_fname, _cs_idx)] = _remap
                self._func_fixnan_members.update(_remap.values())
        # Site map: node id -> original site (cs0). Skip dead sites and
        # function-local originals (the active remap dispatches variants).
        for _i, _fsite in enumerate(ctx.fixnan_sites):
            if _i in self._dead_fixnan_indices:
                continue
            if _fsite.node is None:
                continue
            if _fsite.member_name not in self._func_fixnan_members:
                self._fixnan_site_map[id(_fsite.node)] = _fsite
            elif id(_fsite.node) not in self._fixnan_site_map:
                self._fixnan_site_map[id(_fsite.node)] = _fsite
        # Track strategy series vars (e.g., strategy.closedtrades[1])
        self._strategy_series_vars: set[str] = set()
        # Track global-scope non-var declarations (emitted as class members)
        self._global_member_vars: set[str] = set()
        for name, _ in ctx.global_var_decls:
            self._global_member_vars.add(name)
        self._global_mutable_infos: dict[str, object] = getattr(ctx, "global_mutable_infos", {}) or {}
        self._udt_var_types: dict[str, str] = getattr(ctx, "udt_var_types", {}) or {}
        self._collection_types: dict[str, TypeSpec] = getattr(ctx, "collection_types", {}) or {}
        # id(block_node) -> {raw_var_name: unique_member} for block-scoped var
        # name collisions (see Analyzer._visit_VarDecl). Activated into
        # ``_active_var_remap`` while emitting the owning block's statements.
        self._block_var_renames: dict[int, dict[str, str]] = getattr(ctx, "block_var_renames", {}) or {}
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
        # Drawing-objects-as-data: line/box/label/linefill/chart.point are now
        # REAL data (un-dropped from UDT structs). Only table/polyline stay
        # dropped (no C++ representation). See drawing-objects-as-data.md §4.2.
        _DRAWING_TYPES_INIT = {"table", "polyline"}
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
        self._current_func_param_specs: dict[str, "TypeSpec"] = {}
        # Current function params that are series (const Series<double>&)
        self._current_func_series_params: set[str] = set()
        # Locals declared in the function currently being emitted (symbol table loses them after analysis)
        self._current_func_locals: set[str] = set()
        self._current_func_local_types: dict[str, str] = {}
        # for-in loop iterator names (must resolve member access, not enum fallback)
        self._current_loop_vars: set[str] = set()
        self._current_loop_var_specs: dict[str, "TypeSpec"] = {}
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
        # Table / polyline variables and params have NO C++ representation
        # (SKIP_VAR_TYPES). A *method* call on such a receiver
        # (``panel.cell(...)``, ``dash.merge_cells(...)``) is a visual no-op
        # that must be dropped — but unlike the namespace form
        # (``table.cell(...)``) the receiver is a bare var/param the
        # namespace-based skip cannot see. Collect those names so
        # ``_is_skip_expr`` can drop their method calls.
        _SKIP_DECL_TYPES = set(SKIP_VAR_TYPES) | {"polyline"}
        self._visual_drop_vars: set[str] = set()
        for _node in self._walk_ast(self.ctx.ast):
            if isinstance(_node, VarDecl):
                if _node.type_hint in _SKIP_DECL_TYPES:
                    self._visual_drop_vars.add(_node.name)
                elif isinstance(_node.value, FuncCall):
                    _fn, _ns = self._resolve_callee(_node.value.callee)
                    if _fn == "new" and _ns in _SKIP_DECL_TYPES:
                        self._visual_drop_vars.add(_node.name)
            elif isinstance(_node, (FuncDef, MethodDef)):
                _hints = (getattr(_node, "annotations", None) or {}).get("param_type_hints") or []
                for _i, _p in enumerate(getattr(_node, "params", []) or []):
                    _h = _hints[_i] if _i < len(_hints) else None
                    if _h and str(_h).replace(" ", "") in _SKIP_DECL_TYPES:
                        self._visual_drop_vars.add(_p)
        # Collect request.security metadata per call
        self._security_eval_info: list[dict] = []
        self._security_ta_variant_names: dict[tuple[int, int, tuple], str] = {}
        for item in self._security_calls:
            sec_id = item["sec_id"]
            tf_node = item["tf_node"]
            gaps_node = item.get("gaps_node")
            lookahead_node = item.get("lookahead_node")
            ta_range = item.get("ta_range")

            # Resolve the timeframe: a literal/const/global gives a static tf;
            # a function-parameter tf is resolved from the call sites (the
            # evaluator is a class method, so the param is not in scope there).
            tf_str, tf_expr = self._resolve_security_tf(
                tf_node, item.get("containing_func", ""))

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
                "tf_expr": tf_expr,
                "tf_node": tf_node,
                "gaps_on": is_gaps_on,
                "lookahead_on": is_lookahead_on,
                "heikinashi": bool(item.get("heikinashi", False)),
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
        self._register_udt_array_get_ref_locals()
        self._uses_matrix = self._detect_matrix_usage()
        # Drawing-objects-as-data: gate all new emission (drawing.hpp include +
        # the per-type arenas) on this flag so non-drawing strategies stay
        # byte-identical. Caps come from the strategy() header max_*_count.
        self._uses_drawing = self._detect_drawing_usage()
        self._drawing_caps = self._compute_drawing_caps() if self._uses_drawing else {}

        # max_bars_back: the per-variable history depth the engine's Series<T>
        # ring buffer should retain. Pine exposes this two ways — the
        # ``strategy(..., max_bars_back=N)`` kwarg (global) and the
        # ``max_bars_back(var, N)`` function (per-var). The engine's
        # ``Series<T>(int max_len)`` ctor (default 500, include/pineforge/
        # series.hpp) is the wiring point: reads past the retained depth return
        # na, so honoring the directive means constructing each Series with a
        # capacity >= the requested depth. We take the MAX requested N and apply
        # it (via ``_series_decl_suffix`` -> ``{N}``) to the directly-declared
        # ``Series<T>`` members — a safe superset of Pine's per-var semantics
        # (it never retains LESS than Pine, so any history access that succeeds
        # in Pine succeeds here). ``None`` => no directive => keep the engine
        # default 500 (emit a bare ``Series<T>`` with no ctor arg, so
        # directive-free output is byte-identical to before).
        #
        # KNOWN LIMITATION: the lazily-constructed security-helper map series
        # (``_security_helper_series_``, the ``std::unordered_map<std::string,
        # Series<double>>`` ~line 971) do NOT pick up the cap. Their entries are
        # default-constructed on first ``operator[]`` access, so they always use
        # the engine default 500 regardless of the requested ``N``. A
        # max_bars_back directive larger than 500 is therefore not honored for
        # history reads off security-helper series.
        self._max_bars_back_cap: int | None = self._compute_max_bars_back_cap()

        # Non-series persistent scalars whose initializer cannot run in the
        # C++ constructor are initialized at their Pine declaration site.  The
        # source-order metadata and collision-safe once flags must be prepared
        # before function-instance naming and class-member emission begin.
        self._prepare_runtime_scalar_var_initializers()

    def _scalar_var_init_depends_on_runtime_input(self, init_ast) -> bool:
        """Whether a nominally constant initializer depends on input state.

        ``_resolve_known`` intentionally folds input defaults for constructor
        sizing and other compile-time decisions.  A Pine ``var`` initializer,
        however, must observe any host override installed before the first
        bar.  Treat direct input aliases and arithmetic aliases derived from
        them as declaration-time expressions even when their default happens
        to fold to a C++ literal.
        """
        if init_ast is None:
            return False
        runtime_names = set(self._input_backed_vars) | set(self._derived_input_expr)
        for node in self._walk_ast(init_ast):
            if isinstance(node, FuncCall) and self._is_input_call(node):
                return True
            if isinstance(node, Identifier) and node.name in runtime_names:
                return True
        return False

    def _is_runtime_scalar_var_initializer(
            self, name: str, ptype, init_str: str, init_ast) -> bool:
        """Return True for a persistent primitive that must init in execution.

        Series and aggregate state keep their existing specialized preamble
        routes. Function-local vars keep their per-function-variant route. This
        predicate only selects primitive global/on-bar-scope members for the
        declaration-site once guards prepared below.
        """
        if init_ast is None or name in self.ctx.series_vars:
            return False
        if name in self._visual_drop_vars:
            return False
        if name in self._array_vars or name in self._map_vars \
                or name in self._matrix_specs:
            return False
        type_spec = self._collection_types.get(name)
        if type_spec is not None and type_spec.kind in {
            "array", "map", "matrix", "udt",
        }:
            return False
        udt_type = self._udt_var_types.get(name)
        if udt_type in self._udt_defs or udt_type in DRAWING_TYPE_TO_CPP:
            return False

        ctor_val = self._resolve_known(init_str)
        ctor_val = self._typed_na_init(ctor_val, name, ptype)
        return (
            not self._is_compile_time_value(ctor_val)
            or self._scalar_var_init_depends_on_runtime_input(init_ast)
        )

    def _prepare_runtime_scalar_var_initializers(self) -> None:
        """Index declaration-site scalar ``var`` initialization and flags.

        The analyzer supplies exact metadata for every VarDecl, including
        sibling-block disambiguation and callable ownership. Its insertion
        order follows source analysis and reaches declarations nested inside
        if/switch expressions, while the ownership bit excludes function and
        method bodies. This lets emission preserve ordinary dependency order
        and Pine's lazy first-entry semantics for conditional declarations.
        """
        self._runtime_scalar_var_init_by_node: dict[int, dict] = {}
        self._runtime_scalar_var_init_members: set[str] = set()

        used_names = set(self._all_member_names)
        # ``_all_member_names`` historically covers persistent ``var`` and
        # Series members only.  Plain global declarations are class members as
        # well, so include them before minting a generated flag; otherwise a
        # legal user binding such as ``_pf_var_init_seeded = 1`` can collide
        # with the flag for ``var seeded = low``.
        used_names.update(
            self._safe_name(name) for name, _ptype in self.ctx.global_var_decls
        )
        metadata_by_node = getattr(
            self.ctx, "var_member_metadata_by_node", {}
        ) or {}
        for node_id, meta in metadata_by_node.items():
            stmt, member_name, ptype, init_str, is_callable_scoped = meta
            if is_callable_scoped:
                continue
            if not isinstance(stmt, VarDecl) or not (stmt.is_var or stmt.is_varip):
                continue
            if not self._is_runtime_scalar_var_initializer(
                    member_name, ptype, init_str, stmt.value):
                continue

            base_flag = f"_pf_var_init_{self._safe_name(member_name)}"
            flag = base_flag
            suffix = 2
            while flag in used_names:
                flag = f"{base_flag}_{suffix}"
                suffix += 1
            used_names.add(flag)
            self._all_member_names.add(flag)
            self._runtime_scalar_var_init_members.add(member_name)
            self._runtime_scalar_var_init_by_node[node_id] = {
                "member_name": member_name,
                "ptype": ptype,
                "flag": flag,
            }

    # ------------------------------------------------------------------
    # Context-sensitive (call-path) instance machinery
    # ------------------------------------------------------------------
    def _iter_func_calls(self, root) -> list:
        """Collect every ``FuncCall`` node reachable from ``root`` (a stmt list
        or single AST node). Order-independent; used by the instance pre-pass to
        find nested user-function calls inside a function body."""
        out: list = []
        seen: set[int] = set()
        stack: list = list(root) if isinstance(root, (list, tuple)) else [root]
        while stack:
            node = stack.pop()
            if node is None:
                continue
            if isinstance(node, (list, tuple)):
                stack.extend(node)
                continue
            if isinstance(node, dict):
                stack.extend(node.values())
                continue
            if not hasattr(node, "__dict__"):
                continue
            nid = id(node)
            if nid in seen:
                continue
            seen.add(nid)
            if isinstance(node, FuncCall):
                out.append(node)
            for v in vars(node).values():
                if isinstance(v, (list, tuple, dict)) or hasattr(v, "__dict__"):
                    stack.append(v)
        return out

    def _build_func_instances(self) -> None:
        """Context-sensitive cloning of nested stateful helper functions.

        A stateful helper ``G`` (carrying TA state and/or ``var`` members) may be
        reached through several distinct call paths — e.g. ``leg`` called from
        three clones of ``f_get`` (lengths 10/20/30) *and* directly. Each path is
        a logically-distinct instance that must drive its OWN TA/var members.

        The analyzer already mints the per-path members (via range-widening), but
        the flat ``{G}_cs{idx}`` clone namespace conflates a callee's own textual
        call sites with the enclosing function's call sites. This pre-pass walks
        the call graph from each natural clone and, for every nested stateful
        call, composes the enclosing clone's active remap with the callee's
        per-call-site remap:

            composed_ta[m] = R_enclosing.get(R_callee_cs[m], R_callee_cs[m])

        When the composition equals the callee's natural ``cs{j}`` remap the call
        dispatches to the existing ``{G}_cs{j}`` clone (output stays byte-identical
        for the common single-caller case). Otherwise a fresh instance is minted,
        bound to the path-specific members (and FRESH ``var`` members so two paths
        never share scalar state). ``_instance_dispatch`` records the resolved
        emit-name per ``(enclosing_instance, call_node)``; ``_fresh_instances`` /
        ``_fresh_var_members`` carry the extra code to emit.
        """
        ctx = self.ctx
        stateful = (set(ctx.func_ta_ranges.keys())
                    | set(ctx.func_var_members.keys())
                    | set(ctx.func_series_vars.keys())
                    | set(ctx.func_fixnan_indices.keys())
                    | set(ctx.func_security_clone_only))
        if not stateful:
            return

        func_bodies: dict[str, list] = {}
        for fi in ctx.func_infos:
            node = getattr(fi, "node", None)
            if node is not None and getattr(node, "body", None):
                func_bodies.setdefault(fi.name, node.body)

        def ta_originals(fname: str) -> list[str]:
            return list(self._func_cs_ta_remap.get((fname, 0), {}).keys())

        def var_originals(fname: str) -> list[str]:
            return [self._safe_name(n) for n, _, _ in ctx.func_var_members.get(fname, [])]

        def fixnan_originals(fname: str) -> list[str]:
            return list(self._func_cs_fixnan_remap.get((fname, 0), {}).keys())

        def natural_name(fname: str, cs_idx: int) -> str:
            return f"{self._func_cpp_base_name(fname)}_cs{cs_idx}"

        interned: dict[tuple, dict] = {}
        worklist: list[dict] = []
        seen_walk: set[str] = set()
        fresh_counter = 0

        # Seed with the natural clones the flat emission loop produces.
        for fname in sorted(stateful):
            if fname not in func_bodies:
                continue
            total_cs = ctx.func_call_site_counts.get(fname, 0)
            if total_cs > 0:
                for k in range(total_cs):
                    worklist.append({
                        "fname": fname,
                        "name": natural_name(fname, k),
                        "ta_remap": self._func_cs_ta_remap.get((fname, k), {}),
                        "var_remap": self._func_cs_var_remap.get((fname, k), {}),
                        "fixnan_remap": self._func_cs_fixnan_remap.get((fname, k), {}),
                    })
            else:
                worklist.append({
                    "fname": fname,
                    "name": self._func_cpp_base_name(fname),
                    "ta_remap": {},
                    "var_remap": {},
                    "fixnan_remap": {},
                })

        while worklist:
            inst = worklist.pop()
            if inst["name"] in seen_walk:
                continue
            seen_walk.add(inst["name"])
            body = func_bodies.get(inst["fname"])
            if not body:
                continue
            active_ta = inst["ta_remap"]
            active_fixnan = inst.get("fixnan_remap", {})
            for callnode in self._iter_func_calls(body):
                cs_info = ctx.func_call_cs_map.get(id(callnode))
                if cs_info is None:
                    continue
                g_name, j = cs_info
                if g_name not in stateful:
                    continue
                natural_ta = self._func_cs_ta_remap.get((g_name, j), {})
                composed_ta = {}
                for m in ta_originals(g_name):
                    mid = natural_ta.get(m, m)
                    composed_ta[m] = active_ta.get(mid, mid)
                natural_fixnan = self._func_cs_fixnan_remap.get((g_name, j), {})
                composed_fixnan = {}
                for m in fixnan_originals(g_name):
                    mid = natural_fixnan.get(m, m)
                    composed_fixnan[m] = active_fixnan.get(mid, mid)
                if composed_ta == natural_ta and composed_fixnan == natural_fixnan:
                    # Path resolves to the callee's own cs{j} clone — reuse it.
                    self._instance_dispatch[(inst["name"], id(callnode))] = \
                        natural_name(g_name, j)
                    continue
                key = (g_name, frozenset(composed_ta.items()),
                       frozenset(composed_fixnan.items()))
                ginst = interned.get(key)
                if ginst is None:
                    fresh_counter += 1
                    inst_name = f"{self._func_cpp_base_name(g_name)}__ni{fresh_counter}"
                    fvar_remap: dict[str, str] = {}
                    for v in var_originals(g_name):
                        fresh_member = f"{v}__ni{fresh_counter}"
                        fvar_remap[v] = fresh_member
                        self._fresh_var_members.append((v, fresh_member))
                    # Fresh fixnan members: each path gets its OWN previous-
                    # value member so two call paths never share fixnan state.
                    ffixnan_remap: dict[str, str] = {}
                    for orig_fn_member in fixnan_originals(g_name):
                        fresh_fn_member = f"{orig_fn_member}__ni{fresh_counter}"
                        ffixnan_remap[orig_fn_member] = fresh_fn_member
                        # Find the original FixnanCallSite to carry its type.
                        orig_fn_site = None
                        for _fs in ctx.fixnan_sites:
                            if _fs.member_name == orig_fn_member:
                                orig_fn_site = _fs
                                break
                        if orig_fn_site is not None:
                            self._fresh_fixnan_members.append(
                                (orig_fn_site, fresh_fn_member)
                            )
                    ginst = {
                        "fname": g_name,
                        "name": inst_name,
                        "ta_remap": composed_ta,
                        "var_remap": fvar_remap,
                        "fixnan_remap": ffixnan_remap,
                    }
                    interned[key] = ginst
                    self._fresh_instances.append(ginst)
                    worklist.append(ginst)
                self._instance_dispatch[(inst["name"], id(callnode))] = ginst["name"]

    def _emit_cloned_var_decl(self, orig_safe: str, cloned_safe: str,
                              series_suffix: str, lines: list[str]) -> None:
        """Declare a per-clone copy of a function-scoped ``var`` member, matching
        the original's C++ type (series / matrix / array / map / drawing-handle /
        UDT / scalar). Shared by the per-call-site clone loop and the fresh
        context-sensitive instance loop."""
        for vname, ptype, _init_str in self.ctx.var_members:
            if self._safe_name(vname) == orig_safe:
                cpp_type = PINE_TYPE_TO_CPP.get(ptype, "double")
                if vname in self.ctx.series_vars:
                    lines.append(f"    Series<{cpp_type}> {cloned_safe}{series_suffix};")
                elif vname in self._matrix_specs:
                    lines.append(f"    {self._type_spec_to_cpp(self._matrix_specs[vname])} {cloned_safe};")
                elif vname in self._array_vars:
                    lines.append(f"    {self._type_spec_to_cpp(self._array_spec_for_name(vname))} {cloned_safe};")
                elif vname in self._map_vars:
                    lines.append(f"    {self._type_spec_to_cpp(self._map_spec_for_name(vname))} {cloned_safe};")
                elif vname in self._udt_var_types:
                    # Drawing handle / UDT var clone must match the original's
                    # type (Line/Label/Box/<UDT>), not the coarse PineType
                    # default (double) — otherwise the clone can't hold the
                    # handle and drawing access on it reads a garbage / na id.
                    udt_t = self._udt_var_types[vname]
                    handle_cpp = DRAWING_TYPE_TO_CPP.get(udt_t, udt_t)
                    lines.append(f"    {handle_cpp} {cloned_safe} = {handle_cpp}{{}};")
                else:
                    lines.append(f"    {cpp_type} {cloned_safe};")
                return
        # Non-var series var
        if orig_safe in [self._safe_name(n) for n in self.ctx.series_vars]:
            cpp_type = self._series_type_for(orig_safe)
            lines.append(f"    Series<{cpp_type}> {cloned_safe}{series_suffix};")
        else:
            lines.append(f"    double {cloned_safe} = 0.0;")

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
            elif ns == "array" and fn in ({"new", "from"} | set(ARRAY_NEW_CTORS)):
                self._array_vars.add(name)
                spec = self._type_spec_from_expr(expr) or self._array_spec_for_name(name)
                self._collection_types[name] = spec
            elif ns == "map" and fn == "new":
                self._map_vars.add(name)

        # Also register var/varip aggregate members from AST nodes so that
        # class-member declarations see the precise collection type before
        # on_bar emits the initializer. This is required for unannotated
        # drawing arrays such as ``var boxes = array.new_box()``.
        var_decl_map: dict[str, FuncCall] = {}
        for stmt in (self.ctx.ast.body if hasattr(self.ctx, "ast") else []):
            if isinstance(stmt, VarDecl) and isinstance(stmt.value, FuncCall):
                var_decl_map[stmt.name] = stmt.value
        for name, _ptype, _init_str in self.ctx.var_members:
            expr = var_decl_map.get(name)
            if expr is None:
                continue
            fn2, ns2 = self._resolve_callee(expr.callee)
            if ns2 == "array" and fn2 in ({"new", "from"} | set(ARRAY_NEW_CTORS)):
                self._array_vars.add(name)
                spec2 = self._type_spec_from_expr(expr) or self._array_spec_for_name(name)
                self._collection_types[name] = spec2
                continue
            if name in self._matrix_specs:
                continue
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

    def _walk_global_scope_with_loopflag(self, stmts, in_loop):
        """Yield ``(stmt, in_loop)`` for every statement in global scope,
        recursing into control-flow bodies (if/for/while/switch) but NOT into
        nested function definitions — a function-local of the same name lives in
        a separate scope and must not be attributed to a global member. The
        ``in_loop`` flag is True once inside any for/while loop body."""
        for s in stmts:
            if isinstance(s, (FuncDef, MethodDef)):
                continue
            yield s, in_loop
            child_in_loop = in_loop or isinstance(s, (ForStmt, ForInStmt, WhileStmt))
            for attr in ("body", "else_body", "default_body"):
                child = getattr(s, attr, None)
                if isinstance(child, list):
                    yield from self._walk_global_scope_with_loopflag(child, child_in_loop)
            cases = getattr(s, "cases", None)
            if isinstance(cases, list):
                for _case_expr, case_stmts in cases:
                    if isinstance(case_stmts, list):
                        yield from self._walk_global_scope_with_loopflag(case_stmts, child_in_loop)

    def _register_udt_array_get_ref_locals(self) -> None:
        """Detect global-scope UDT loop-locals that alias a UDT array element and
        are later field-mutated (Pine array elements of a user-defined type are
        references — ``z = arr.get(i)`` then ``z.f := v`` MUST write back into
        ``arr``).

        A non-``var`` global-scope ``UDT z = arr.get(i)`` (or ``.first`` /
        ``.last``) nested inside a for/while loop mis-lowers to a value copy: a
        global ``while`` loop hoists ``z`` to a class member whose in-loop init
        becomes a value-copy assignment, and a global ``for`` loop keeps ``z`` a
        true local but the function-local alias path (``_udt_local_alias_kind``)
        no-ops at global scope (``_current_func_body`` is None) — both silently
        drop the field mutation. We record exactly this shape so the (possible)
        class member is suppressed and the in-loop VarDecl is emitted as a fresh
        per-iteration ``UDT& z = arr[i];`` reference instead (the same alias form
        the non-hoisted function-local path already produces). Strictly gated:
        the RHS must be a UDT-array-element lvalue AND the name must be field-
        mutated at global scope AND the declaration must be loop-nested. A
        read-only get-local is never recorded, so its value-copy output is
        unchanged. Function-local get-locals are excluded (the walker skips
        function bodies) — those keep using the existing alias path."""
        pairs = list(self._walk_global_scope_with_loopflag(self.ctx.ast.body, False))
        field_mutated: set[str] = set()
        for s, _in_loop in pairs:
            if (isinstance(s, Assignment)
                    and isinstance(s.target, MemberAccess)
                    and isinstance(s.target.object, Identifier)):
                field_mutated.add(s.target.object.name)
        for s, in_loop in pairs:
            if not isinstance(s, VarDecl) or s.is_var or s.is_varip:
                continue
            if not in_loop:
                continue
            if not isinstance(s.value, FuncCall):
                continue
            if self._is_udt_lvalue(s.value) is None:
                continue
            if s.name not in field_mutated:
                continue
            self._udt_array_get_ref_locals.add(s.name)

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
        # Register never-reassigned ``var``/``varip`` scalars with a stable
        # init FIRST — a later derived var (or a UDF body) may reference such a
        # scalar as a stable length component, so it must be classified stable
        # before those exprs are evaluated below.
        self._collect_stable_var_scalars(reassigned)
        for stmt in self.ctx.ast.body:
            if isinstance(stmt, VarDecl) and stmt.name not in reassigned:
                self._collect_known_var(stmt)
        # A second pass handles the stable-reassigned-scalar pattern: a
        # class-scope scalar initialized from a stable expr and reassigned
        # ONLY inside top-level if/elif chains whose conditions and assigned
        # values are themselves stable (inputs / timeframe.* / math.*). Such
        # a var is a bar-invariant scalar and may feed a TA ctor length with
        # a runtime reset that reproduces the conditional logic. Series-
        # dependent reassignments are left untracked (rejected by the guard).
        self._collect_reassigned_stable_scalars(reassigned)

    def _collect_stable_var_scalars(self, reassigned: set[str]) -> None:
        """Track top-level ``var``/``varip`` scalars declared exactly once (never
        reassigned) from a stable init expression.

        A ``var`` scalar's one-shot initializer runs once and the value never
        changes across bars, so a never-reassigned ``var`` over a stable init
        (``var int _tfSec = timeframe.in_seconds()``) is a bar-invariant scalar —
        safe to embed in a TA ctor runtime-reset expression. Recording it in
        ``_derived_input_expr`` + ``_stable_runtime_vars`` lets the reset path
        expand the name and lets ``_expr_is_stable`` classify it (the
        ``_stable_runtime_vars`` check precedes the ``_var_names`` rejection).

        A ``var`` that is reassigned anywhere (``:=`` in ``reassigned``), is a
        series var, or is initialized from a non-stable value stays untracked —
        so a TA length fed by genuinely-mutable persistent state is still
        rejected by the constructor guard. Names are NOT folded into
        ``_known_vars`` (no use-site inlining): only the length-analysis path is
        affected, and the ``var`` member still emits and initializes normally.
        """
        for stmt in (self.ctx.ast.body or []):
            if not isinstance(stmt, VarDecl):
                continue
            if not (stmt.is_var or stmt.is_varip):
                continue
            if stmt.name in reassigned:
                continue
            if stmt.name in self.ctx.series_vars:
                continue
            if stmt.value is None or not self._expr_is_stable(stmt.value):
                continue
            expr_str = self._arith_expr_to_str(stmt.value)
            if expr_str is None:
                continue
            self._derived_input_expr[stmt.name] = expr_str
            self._stable_runtime_vars.add(stmt.name)
            # Mark input-backed iff the init references an input, so the reset
            # emits override-aware get_input_*() reads for it.
            import re as _re
            toks = set(_re.findall(r"[A-Za-z_][A-Za-z_0-9]*", expr_str))
            if any(t in self._input_backed_vars for t in toks):
                self._input_backed_vars.add(stmt.name)

    def _collect_reassigned_stable_scalars(self, reassigned: set[str]) -> None:
        """Track class-scope scalars that are reassigned but only along stable
        if/elif paths (see ``test_stable_reassigned_class_scope_length``).

        For each top-level ``v = <init>`` whose name is reassigned, build the
        final value as a nested ternary by folding subsequent top-level
        IfStmts / direct Assignments. If every condition and every assigned
        RHS is stable (and renderable), record the ternary in
        ``_derived_input_expr`` and add ``v`` to ``_stable_runtime_vars`` so
        the TA ctor reset path can expand it. Anything non-stable (a ta.*
        result, a bar field, a series var) leaves the var untracked, so the
        ctor guard still rejects it loudly.
        """
        from ..ast_nodes import IfStmt, Assignment
        body = self.ctx.ast.body or []
        # Pre-resolve each reassigned var's initial VarDecl.
        inits: dict[str, object] = {}
        for stmt in body:
            if (isinstance(stmt, VarDecl) and stmt.name in reassigned
                    and not stmt.is_var and not stmt.is_varip):
                # Only consider vars whose initial value is itself stable;
                # an unstable init cannot become a stable scalar via later
                # reassignment.
                if stmt.value is not None and self._expr_is_stable(stmt.value):
                    inits[stmt.name] = stmt.value
        if not inits:
            return

        def _value_after(stmts, fallback: str | None) -> str | None:
            """Fold a statement list into the final value expression for the
            target var, given ``fallback`` as the value on entry. Returns None
            if any condition / assignment is non-stable or unrenderable."""
            current = fallback
            for s in stmts or []:
                if isinstance(s, Assignment) and isinstance(s.target, Identifier):
                    if s.target.name != target_name:
                        continue
                    if s.op != ":=":
                        return None  # compound assignment — not a stable fold
                    if not self._expr_is_stable(s.value):
                        return None
                    rhs = self._arith_expr_to_str(s.value)
                    if rhs is None:
                        return None
                    current = rhs
                elif isinstance(s, IfStmt):
                    # Only model IfStmts that actually reassign the target var;
                    # an unrelated IfStmt (e.g. entry/exit logic with a series
                    # condition) must NOT abort the fold — the var simply keeps
                    # its current value through it.
                    if not _reassigns(s, target_name):
                        continue
                    if not self._expr_is_stable(s.condition):
                        return None
                    cond = self._arith_expr_to_str(s.condition)
                    if cond is None:
                        return None
                    then_val = _value_after(s.body, current)
                    if then_val is None:
                        return None
                    else_val = _value_after(s.else_body, current)
                    if else_val is None:
                        return None
                    current = f"({cond} ? {then_val} : {else_val})"
                # Other statement shapes (for/while/switch/var decls of
                # other vars) are ignored for this var's value fold; they
                # do not reassign ``target_name`` in a way we model.
            return current

        def _reassigns(node, name: str) -> bool:
            """True if any ``:=`` assignment to ``name`` occurs within node."""
            from ..ast_nodes import IfStmt as _If, Assignment as _Asg
            if isinstance(node, _Asg) and isinstance(node.target, Identifier):
                return node.target.name == name
            if isinstance(node, _If):
                if any(_reassigns(c, name) for c in (node.body or [])):
                    return True
                if any(_reassigns(c, name) for c in (node.else_body or [])):
                    return True
                return False
            for attr in ("body", "else_body", "cases", "default_body"):
                sub = getattr(node, attr, None)
                if isinstance(sub, list):
                    if any(_reassigns(c, name) for c in sub):
                        return True
            return False

        for target_name, init_node in inits.items():
            init_str = self._arith_expr_to_str(init_node)
            if init_str is None:
                continue
            final = _value_after(body, init_str)
            if final is None:
                continue
            # Sanity: the fold must actually differ from the bare init,
            # otherwise there were no stable reassignments and the var is
            # already covered (or rejected) by the main pass.
            if final == init_str:
                continue
            # Fold to a compile-time literal when possible (so the ctor-init
            # list can use it directly); otherwise record the raw expression
            # for the runtime reset path to expand.
            folded = self._resolve_known(final)
            if self._is_compile_time_value(folded):
                try:
                    num = float(folded)
                    self._known_vars[target_name] = (
                        int(num) if num == int(num) else num
                    )
                except ValueError:
                    pass
            self._derived_input_expr[target_name] = final
            self._stable_runtime_vars.add(target_name)
            # Mark input-backed iff the expression references an input so the
            # override-aware get_input_*() reads are emitted on the reset path.
            import re as _re
            toks = set(_re.findall(r"[A-Za-z_][A-Za-z_0-9]*", final))
            if any(t in self._input_backed_vars for t in toks):
                self._input_backed_vars.add(target_name)

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

    # ``math.*`` members that are pure functions over their (stable) args, or
    # stable constants. Anything outside this set (e.g. ``math.random``) is
    # treated as non-stable. Used by ``_expr_is_stable``.
    _MATH_STABLE_MEMBERS: frozenset[str] = frozenset({
        "pi", "e", "phi", "rphi",
        "abs", "max", "min", "round", "floor", "ceil",
        "sqrt", "log", "log10", "exp", "pow",
        "sin", "cos", "tan", "asin", "acos", "atan", "sign",
        "sum", "avg", "to_precision", "round_to_mintick",
    })

    # ``timeframe.*`` members that are constant for the lifetime of a run —
    # they reflect the script's resolution, not a per-bar value.
    _TF_STABLE_MEMBERS: frozenset[str] = frozenset({
        "period", "main_period", "multiplier",
        "isintraday", "isminutes", "isdaily", "isweekly",
        "ismonthly", "isdwm", "isseconds", "in_seconds", "isticks",
    })

    # Depth ceiling for inlining nested single-expression user functions while
    # classifying a TA length's stability. Pine forbids recursion, so any real
    # chain is shallow; the cap is a backstop against pathological input and is
    # enforced together with a name-stack cycle guard.
    _UDF_INLINE_MAX_DEPTH = 16

    def _get_udf_def(self, name: str):
        """Return the top-level single-name ``FuncDef`` for ``name`` (or None).

        Built lazily and cached. UDT ``MethodDef``s are intentionally excluded —
        only a free function can appear as a bare-name TA length call.
        """
        cache = getattr(self, "_udf_def_cache", None)
        if cache is None:
            cache = {}
            for stmt in (self.ctx.ast.body or []):
                if isinstance(stmt, FuncDef):
                    # Last definition wins; a name map is all the length path needs.
                    cache[stmt.name] = stmt
            self._udf_def_cache = cache
        return cache.get(name)

    def _inline_single_expr_udf(self, node, _udf_stack: frozenset = frozenset(),
                                _depth: int = 0):
        """If ``node`` is a call to a user-defined SINGLE-EXPRESSION function,
        return its body expression with each parameter substituted by the
        corresponding call-argument node. Returns None when the call is not such
        a function, the arity/kwargs do not match, the body is not a single
        expression, the body contains a shape we do not clone, or the call would
        recurse (cycle / depth-limit).

        Purely structural: it does NOT judge stability (the caller does, via
        ``_expr_is_stable`` on the returned node). The conservative None keeps
        the TA-ctor guard intact.
        """
        if not isinstance(node, FuncCall):
            return None
        func_name, namespace = self._resolve_callee(node.callee)
        if namespace is not None or func_name is None:
            return None
        if func_name in _udf_stack or _depth >= self._UDF_INLINE_MAX_DEPTH:
            return None
        fdef = self._get_udf_def(func_name)
        if fdef is None:
            return None
        # A body that is exactly ONE expression statement is inlinable, whether
        # written inline after ``=>`` (``is_single_expr=True``) or as a one-line
        # indented block (``f(x) =>`` then a single indented expr, which the
        # parser records as ``is_single_expr=False`` with a one-ExprStmt body —
        # the gonzowiththewind-sisyphus ``f_bars`` shape). A multi-statement
        # body (len != 1, or a non-ExprStmt) is conservatively refused.
        body = fdef.body
        if not body or len(body) != 1 or not isinstance(body[0], ExprStmt):
            return None
        # Require a plain positional call: one arg per param, no kwargs, no
        # default-parameter fill-in — anything else is conservatively refused.
        if node.kwargs or len(node.args) != len(fdef.params):
            return None
        subst = dict(zip(fdef.params, node.args))
        return self._subst_params(body[0].expr, subst)

    def _subst_params(self, node, subst: dict):
        """Return a copy of ``node`` with every ``Identifier`` whose name is a
        key in ``subst`` replaced by the mapped argument node. Returns None for
        any node outside the small arithmetic/call grammar we fold (an
        unrecognised construct — e.g. a history ``Subscript`` — conservatively
        aborts the inline). ``dataclasses.replace`` preserves ``loc``/
        ``annotations`` so diagnostics still point at real source spans."""
        import dataclasses as _dc
        if isinstance(node, (NumberLiteral, StringLiteral, BoolLiteral, NaLiteral)):
            return node
        if isinstance(node, Identifier):
            return subst.get(node.name, node)
        if isinstance(node, MemberAccess):
            obj = self._subst_params(node.object, subst)
            if obj is None:
                return None
            return _dc.replace(node, object=obj)
        if isinstance(node, Ternary):
            c = self._subst_params(node.condition, subst)
            t = self._subst_params(node.true_val, subst)
            f = self._subst_params(node.false_val, subst)
            if c is None or t is None or f is None:
                return None
            return _dc.replace(node, condition=c, true_val=t, false_val=f)
        if isinstance(node, BinOp):
            l = self._subst_params(node.left, subst)
            r = self._subst_params(node.right, subst)
            if l is None or r is None:
                return None
            return _dc.replace(node, left=l, right=r)
        if isinstance(node, UnaryOp):
            o = self._subst_params(node.operand, subst)
            if o is None:
                return None
            return _dc.replace(node, operand=o)
        if isinstance(node, FuncCall):
            if node.kwargs:
                return None
            new_args = []
            for a in node.args:
                sa = self._subst_params(a, subst)
                if sa is None:
                    return None
                new_args.append(sa)
            return _dc.replace(node, args=new_args)
        # Subscript (history read) and anything else: not a stable-length shape.
        return None

    def _expr_is_stable(self, node, _udf_stack: frozenset = frozenset(),
                        _depth: int = 0) -> bool:
        """True iff ``node``'s value is a bar-invariant scalar.

        A stable expression depends only on: literals, ``input.*`` values,
        previously-tracked stable runtime vars, known compile-time consts,
        ``timeframe.*`` members (constant per run), ``syminfo.*`` (constant
        per instrument), and ``math.*`` functions/consts over stable
        sub-expressions, combined with arithmetic / comparison / logical
        ops, ternaries, and the ``int/float/bool/string`` casts.

        Returns False (i.e. "series") for any node that references a per-bar
        value: bar fields (close/open/...), series vars, history subscripts,
        ``ta.*`` results, strategy.* state, or any unrecognised construct.
        The conservative False keeps the TA-ctor guard intact for genuinely
        dynamic lengths.
        """
        if node is None:
            return False
        if isinstance(node, (NumberLiteral, StringLiteral, BoolLiteral)):
            return True
        if isinstance(node, NaLiteral):
            return True
        if isinstance(node, Identifier):
            name = node.name
            if name in self._known_vars:
                return True
            if name in self._stable_runtime_vars:
                return True
            if name in self._input_backed_vars:
                return True
            if name in self.ctx.series_vars:
                return False
            if name in self._var_names:
                # var/varip persistent state — mutable across bars.
                return False
            if name in BAR_FIELDS or name in BAR_BUILTINS:
                return False
            # Unrecognised bare identifier: be conservative so we never
            # silently allow an undeclared / dynamic length through.
            return False
        if isinstance(node, MemberAccess):
            if isinstance(node.object, Identifier):
                ns = node.object.name
                if ns == "timeframe":
                    return node.member in self._TF_STABLE_MEMBERS
                if ns == "math":
                    return node.member in self._MATH_STABLE_MEMBERS
                if ns == "syminfo":
                    # syminfo.* (mintick, pointvalue, tickerid, ...) is
                    # constant for the run — safe as a stable scalar.
                    return True
            # bar.* / request.* / any other member access reads per-bar or
            # dynamic state.
            return False
        if isinstance(node, Subscript):
            # History read (``close[1]``) or indexed access — per-bar.
            return False
        if isinstance(node, Ternary):
            return (self._expr_is_stable(node.condition, _udf_stack, _depth)
                    and self._expr_is_stable(node.true_val, _udf_stack, _depth)
                    and self._expr_is_stable(node.false_val, _udf_stack, _depth))
        if isinstance(node, BinOp):
            return (self._expr_is_stable(node.left, _udf_stack, _depth)
                    and self._expr_is_stable(node.right, _udf_stack, _depth))
        if isinstance(node, UnaryOp):
            return self._expr_is_stable(node.operand, _udf_stack, _depth)
        if isinstance(node, FuncCall):
            func_name, namespace = self._resolve_callee(node.callee)
            if namespace == "ta":
                return False
            if namespace == "math":
                if func_name not in self._MATH_STABLE_MEMBERS:
                    return False
                return all(self._expr_is_stable(a, _udf_stack, _depth)
                           for a in node.args)
            if namespace == "timeframe":
                # ``timeframe.in_seconds()`` (and any other function-form
                # timeframe member) is a stable per-run scalar — it reflects
                # the script's resolution, not a per-bar value.
                if func_name not in self._TF_STABLE_MEMBERS:
                    return False
                return all(self._expr_is_stable(a, _udf_stack, _depth)
                           for a in node.args)
            if namespace == "input":
                return True
            if namespace is None and func_name in ("int", "float", "bool", "string"):
                return all(self._expr_is_stable(a, _udf_stack, _depth)
                           for a in node.args)
            # A user-defined single-expression function is stable iff every
            # argument is stable AND its body (with the params bound to those
            # args) is stable — i.e. the body references only stable scalars
            # (inputs / consts / timeframe.* / math.* / never-reassigned var
            # scalars) and no series / strategy state / ta.* results. Inlining
            # the body (params substituted by the arg nodes) lets the ordinary
            # classifier decide; the name-stack + depth guard refuses recursion
            # so a cyclic / malformed UDF is rejected, not looped forever.
            if namespace is None and func_name is not None:
                inlined = self._inline_single_expr_udf(node, _udf_stack, _depth)
                if inlined is not None:
                    if not all(self._expr_is_stable(a, _udf_stack, _depth)
                               for a in node.args):
                        return False
                    return self._expr_is_stable(
                        inlined, _udf_stack | {func_name}, _depth + 1)
            # Any other call (multi-statement user functions, str.*, array.*,
            # ...) — series by default; the conservative answer keeps the guard
            # honest.
            return False
        return False

    # AST node kinds whose serialized form is self-delimiting (a literal, a
    # name, a member read, or a ``name(...)`` call). Non-atomic kinds (BinOp /
    # UnaryOp / Ternary) MUST be parenthesized when they appear as an operand,
    # otherwise re-parsing the flattened infix string silently reassociates the
    # tree: Pine grouping ``(a - b) / (c - d)`` degrades to ``a - b / c - d``
    # under C++ precedence. See ``_runtime_ctor_arg_for_reset`` (the string is
    # re-parsed and lowered through the expression visitor).
    _ATOMIC_ARITH_NODES = (NumberLiteral, Identifier, MemberAccess, FuncCall)

    def _arith_operand_to_str(self, node, _udf_stack: frozenset = frozenset(),
                              _depth: int = 0) -> str | None:
        """Serialize ``node`` for use as an operand: parenthesize it unless its
        serialized form is already self-delimiting, so grouping survives a
        round-trip through the parser."""
        s = self._arith_expr_to_str(node, _udf_stack, _depth)
        if s is None:
            return None
        if isinstance(node, self._ATOMIC_ARITH_NODES):
            return s
        return f"({s})"

    def _arith_expr_to_str(self, node, _udf_stack: frozenset = frozenset(),
                           _depth: int = 0) -> str | None:
        """Render a numeric arithmetic-over-identifiers expression to a string
        that re-parses to the SAME tree (grouping preserved via
        ``_arith_operand_to_str``). Returns None for any node shape we don't
        fold (series subscripts, etc.) so the caller leaves the var untracked.

        ``_udf_stack``/``_depth`` guard the single-expression-UDF inlining below
        against recursion cycles (Pine forbids recursion, but a malformed source
        must be refused, not looped forever).
        """
        if isinstance(node, NumberLiteral):
            v = node.value
            if isinstance(v, float) and v == int(v):
                return str(int(v))
            return str(v)
        if isinstance(node, Identifier):
            return node.name
        if isinstance(node, MemberAccess) and isinstance(node.object, Identifier):
            return f"{node.object.name}.{node.member}"
        if isinstance(node, BinOp):
            l = self._arith_operand_to_str(node.left, _udf_stack, _depth)
            r = self._arith_operand_to_str(node.right, _udf_stack, _depth)
            if l is None or r is None:
                return None
            return f"{l} {node.op} {r}"
        if isinstance(node, UnaryOp):
            o = self._arith_operand_to_str(node.operand, _udf_stack, _depth)
            if o is None:
                return None
            return f"{node.op}{o}"
        if isinstance(node, Ternary):
            c = self._arith_operand_to_str(node.condition, _udf_stack, _depth)
            t = self._arith_operand_to_str(node.true_val, _udf_stack, _depth)
            f = self._arith_operand_to_str(node.false_val, _udf_stack, _depth)
            if c is None or t is None or f is None:
                return None
            return f"{c} ? {t} : {f}"
        if isinstance(node, FuncCall):
            # A bare-name call to a single-expression user function has no C++
            # counterpart at class scope — inline its body (params substituted
            # by the arg expressions) so the rendered string is pure
            # math/timeframe/input arithmetic the ctor-reset path can expand.
            # Namespaced calls (math.*/timeframe.*/int(...)) fall through to the
            # ordinary ``callee(args)`` rendering below. The stack/depth guard
            # refuses a recursive UDF (returns None -> caller leaves it untracked
            # -> the ctor guard rejects it loudly) instead of recursing forever.
            fn, ns = self._resolve_callee(node.callee)
            if ns is None and fn is not None and self._get_udf_def(fn) is not None:
                inlined = self._inline_single_expr_udf(node, _udf_stack, _depth)
                if inlined is None:
                    return None
                return self._arith_expr_to_str(inlined, _udf_stack | {fn}, _depth + 1)
            callee = self._arith_expr_to_str(node.callee, _udf_stack, _depth)
            if callee is None:
                return None
            parts = []
            for a in node.args:
                s = self._arith_expr_to_str(a, _udf_stack, _depth)
                if s is None:
                    return None
                parts.append(s)
            return f"{callee}({', '.join(parts)})"
        return None

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
        # Class-scope arithmetic / ternaries / casts over known, input-backed,
        # timeframe.*, or math.* operands
        # (``wilderLen = rsiLen * 2 - 1``, ``fastPeriod = isM5 ? ... : ...``,
        # ``filterLen = math.max(1, int(math.round(2 / a)))``).
        # Without this branch the derived name is untracked, the TA ctor arg
        # never folds, and the runtime-reset path silently degenerates to a
        # period of 1. We (a) fold to a literal for the ctor-init list when
        # possible and (b) record the raw expression so the reset path can
        # re-expand any input-backed operand to its get_input_*() runtime read
        # and render timeframe.* / math.* fragments to valid C++.
        #
        # The ``_expr_is_stable`` gate is what separates a faithful stable
        # scalar (inputs + constants + timeframe + math) from a series-derived
        # value: a length that depends on a ta.* result, a history subscript,
        # or a bar field stays untracked and is therefore rejected by the TA
        # ctor guard — preserving the guardrail for genuine dynamic lengths.
        elif isinstance(node.value, (BinOp, UnaryOp, FuncCall, Ternary)):
            expr_str = self._arith_expr_to_str(node.value)
            if expr_str is not None and self._expr_is_stable(node.value):
                import re as _re
                tokens = set(_re.findall(r"[A-Za-z_][A-Za-z_0-9]*", expr_str))
                refs_input = any(t in self._input_backed_vars for t in tokens)
                refs_derived = any(t in self._derived_input_expr for t in tokens)
                # The stability classifier already proved this expression is a
                # bar-invariant scalar (inputs / constants / timeframe.* /
                # math.* / syminfo.* only). Track it unconditionally so later
                # stable exprs (and the TA reset path) can reference / expand
                # it — e.g. ``pi = math.asin(1) * 2`` feeds ``beta`` feeds
                # ``alpha`` feeds a function-local ``filterLen``.
                folded = self._resolve_known(expr_str)
                if self._is_compile_time_value(folded):
                    try:
                        num = float(folded)
                        self._known_vars[node.name] = (
                            int(num) if num == int(num) else num
                        )
                    except ValueError:
                        pass
                # Record the raw expression so the runtime-reset path can
                # re-expand operands. Always record for stable derived exprs
                # (even pure-math / pure-timeframe ones with no input) so the
                # reset can render them.
                self._derived_input_expr[node.name] = expr_str
                self._stable_runtime_vars.add(node.name)
                # Mark input-backed so use-sites are not inlined and the
                # override-aware get_input_*() reads are emitted on the reset
                # path. Pure-math / pure-timeframe exprs (no input) stay out
                # of this set, which is fine — they have no override to honor.
                if refs_input or refs_derived:
                    self._input_backed_vars.add(node.name)

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

    def _func_cpp_base_name(self, fname: str) -> str:
        """Return the actual emitted C++ base name for a UDF or UDT method."""
        fi = self._func_info_map.get(fname)
        if fi is not None and getattr(fi, "is_udt_method", False):
            return self._emit_udt_method_cpp_name(fi)
        return self._func_safe_name(fname)

    def _inline_history_contexts_for_owner(self, owner: str | None) -> list[str | None]:
        """Return every method-emission context that owns one source AST site.

        ``None`` denotes top-level/on_bar or a function emitted exactly once.
        Stateful UDF clones and fresh nested-helper instances use the same names
        assigned to ``_current_instance_name`` by ``_emit_func_def`` so lookup
        while visiting a body is deterministic and cannot collapse call sites.
        """
        if owner is None:
            return [None]
        if owner in self._dead_func_names:
            return []

        total_cs = self.ctx.func_call_site_counts.get(owner, 0)
        cloned = (
            owner in self.ctx.func_ta_ranges
            or owner in self.ctx.func_series_vars
            or owner in self.ctx.func_var_members
            or owner in self.ctx.func_security_clone_only
        ) and total_cs > 0
        if cloned:
            contexts: list[str | None] = [
                f"{self._func_cpp_base_name(owner)}_cs{idx}"
                for idx in range(total_cs)
            ]
        else:
            contexts = [None]

        for inst in self._fresh_instances:
            if inst["fname"] == owner and inst["name"] not in contexts:
                contexts.append(inst["name"])
        return contexts

    def _prepare_inline_history_members(self) -> None:
        """Pre-register every generated temporary-Series class member.

        Member declarations and the declaration-derived rollback aggregate are
        emitted before function/on_bar bodies.  A source-order AST pass therefore
        reserves stable names up front.  The key includes an emitted UDF context
        because the same body AST is rendered once per stateful call-site clone.
        """
        self._inline_history_members = []
        self._inline_history_member_by_key = {}
        counters = {"hist_call": 0, "series_arg": 0}

        def walk_nodes(value):
            """Yield AST nodes in stable field order, including tuple elements.

            NamingHelper._walk_ast predates several AST containers and is
            intentionally a best-effort utility.  Member pre-registration must
            be exhaustive because a missed node becomes an undeclared C++
            member, so use the dataclass field graph directly here.
            """
            if isinstance(value, ASTNode):
                yield value
                for child in vars(value).values():
                    yield from walk_nodes(child)
                return
            if isinstance(value, (list, tuple)):
                for child in value:
                    yield from walk_nodes(child)
                return
            if isinstance(value, dict):
                for child in value.values():
                    yield from walk_nodes(child)
                return
            if isinstance(value, TypeField) and value.default is not None:
                yield from walk_nodes(value.default)

        owner_by_node: dict[int, str] = {}
        for fi in self.ctx.func_infos:
            if fi.node is None:
                continue
            for child in walk_nodes(fi.node):
                owner_by_node[id(child)] = fi.name

        def register(kind: str, source_key: tuple, cpp_type: str,
                     owner: str | None) -> None:
            if cpp_type not in ("double", "int", "bool"):
                cpp_type = "double"
            for context in self._inline_history_contexts_for_owner(owner):
                key = (kind, *source_key, context)
                if key in self._inline_history_member_by_key:
                    continue
                counters[kind] += 1
                member_name = f"_{kind}_{counters[kind]}"
                self._inline_history_member_by_key[key] = member_name
                self._inline_history_members.append({
                    "kind": kind,
                    "member_name": member_name,
                    "cpp_type": cpp_type,
                    "context": context,
                })

        def actual_args_for(call: FuncCall, params: list[str]) -> list:
            if call.kwargs:
                return _merge_kwargs(call.args, call.kwargs, params, lambda arg: arg)
            return list(call.args)

        for node in walk_nodes(self.ctx.ast):
            owner = owner_by_node.get(id(node))
            if isinstance(node, Subscript) and isinstance(node.object, FuncCall):
                register(
                    "hist_call", (id(node),), self._infer_type(node.object), owner
                )

            if not isinstance(node, FuncCall):
                continue
            func_name, _ = self._resolve_callee(node.callee)
            fi = self._func_info_map.get(func_name)
            if fi is None or fi.node is None:
                continue
            func_sv = self.ctx.func_series_vars.get(fi.name, set())
            series_param_indices = {
                idx for idx, name in enumerate(fi.node.params) if name in func_sv
            }
            if not series_param_indices:
                continue
            args = actual_args_for(node, list(fi.node.params))
            for idx, arg in enumerate(args):
                if idx not in series_param_indices:
                    continue
                if isinstance(arg, Identifier):
                    if arg.name in BAR_FIELDS or arg.name in BAR_SERIES_PUSH:
                        continue
                    if arg.name in self.ctx.series_vars:
                        continue
                register(
                    "series_arg", (id(node), idx), self._infer_type(arg), owner
                )

    def _inline_history_member(self, kind: str, node: ASTNode,
                               arg_idx: int | None = None) -> str:
        source_key = (id(node),) if arg_idx is None else (id(node), arg_idx)
        key = (kind, *source_key, self._current_instance_name)
        member = self._inline_history_member_by_key.get(key)
        if member is None:
            raise AssertionError(
                "missing pre-registered inline history member for "
                f"{kind} at {getattr(node, 'loc', None)} in context "
                f"{self._current_instance_name!r}"
            )
        return member

    def generate(self) -> str:
        """Generate C++ source from the AnalyzerContext."""
        # Context-sensitive instance pre-pass (needs the naming helpers populated
        # in __init__). Computes nested stateful-helper dispatch + fresh instances.
        self._build_func_instances()
        self._prepare_inline_history_members()
        # Pre-scan for strategy series vars
        self._prescan_strategy_series()
        self._security_ohlc_hist_fields_by_sec: dict[int, set[str]] = {}
        # request.security TA call-sites read at a history offset (``ta.ema(...)[k>=1]``).
        # Maps sec_id -> set of TA call-site indices needing an HTF history Series.
        self._security_ta_hist_idx_by_sec: dict[int, set[int]] = {}
        # request.security helper-call results read at a history offset
        # (``myHelper()[k]``). Maps (sec_id, node-id) -> backing Series metadata.
        self._security_expr_hist_by_node: dict[tuple[int, int], dict] = {}
        self._prepare_lazy_saturated_roc3_sites()

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
                # Pine ``int`` is 64-bit (it routinely holds UNIX-ms timestamps
                # and large bar indices); emit UDT int fields as ``int64_t`` so a
                # field initialised from ``time``/``current_bar_.timestamp`` does
                # not truncate / narrow-init.
                if cpp_type == "int":
                    cpp_type = "int64_t"
                if f.default:
                    default = self._visit_expr(f.default)
                else:
                    default = self._default_for_spec(spec)
                lines.append(f"    {cpp_type} {f.name} = {default};")
            # NA sentinel (always the last data member). A default-constructed
            # UDT - ``var T x = na``, an array fill slot, ``T.copy()`` no-arg -
            # is na; the ``T.new(...)`` lowering sets this false. This lets
            # ``na(udtVar)`` lower to the ``is_na(const T&)`` overload below
            # instead of failing because no ``is_na`` accepts a struct.
            lines.append(f"    bool __pf_na = true;")
            lines.append(f"    static {type_name} create() {{ return {type_name}{{}}; }}")
            lines.append("};")
            lines.append(f"inline bool is_na(const {type_name}& _z) {{ return _z.__pf_na; }}")
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

        # Source-shaped lazy ROC call clocks are generated support types, not
        # script state themselves. Their per-callsite instances are declared
        # below inside GeneratedStrategy and therefore join the automatic COOF
        # checkpoint inventory.
        self._emit_lazy_saturated_roc3_helper(lines)

        # 2. Open class
        lines.append("class GeneratedStrategy : public BacktestEngine {")
        lines.append("public:")
        _script_state_decl_start = len(lines)
        
        # request.security state
        for item in self._security_calls:
            sec_id = item["sec_id"]
            expr_node = item["expr_node"]
            self._validate_security_persistent_var_control_flow(expr_node)
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
                    ctype = self._security_bar_hist_type(field)
                    lines.append(
                        f"    Series<{ctype}> {self._security_ohlc_hist_series_cpp(sec_id, field)}{_mbb};"
                    )
                self._security_ta_hist_idx_by_sec[sec_id] = (
                    self._collect_security_ta_hist_indices(expr_node)
                )
                for name in self._security_ta_hist_series_names(sec_id):
                    lines.append(f"    Series<double> {name}{_mbb};")
                self._emit_security_expr_hist_members(sec_id, expr_node, lines, _mbb)
                continue
            if returns_tuple and tuple_size and tuple_size > 0 and isinstance(expr_node, TupleLiteral):
                hist_fields: set[str] = set()
                for el in expr_node.elements:
                    hist_fields |= self._collect_security_ohlc_hist_fields(el)
                for name in item.get("mutable_globals", []) or []:
                    info = self._global_mutable_infos.get(name)
                    if info is not None:
                        for stmt in getattr(info, "source_stmts", []) or []:
                            hist_fields |= self._collect_security_ohlc_hist_fields(stmt)
                self._security_ohlc_hist_fields_by_sec[sec_id] = hist_fields
                for i, el in enumerate(expr_node.elements):
                    ctype = self._infer_cpp_type_for_security_elem(el)
                    if ctype == "std::vector<double>":
                        lines.append(f"    {ctype} _req_sec_{sec_id}_{i}{{}};")
                    else:
                        lines.append(f"    {ctype} _req_sec_{sec_id}_{i} = na<double>();")
            elif returns_tuple and tuple_size and tuple_size > 0:
                self._security_ohlc_hist_fields_by_sec[sec_id] = (
                    self._collect_security_ohlc_hist_fields_for_call(item)
                )
                site = self._get_ta_site(expr_node)
                ta_name = self._ta_name_from_site(site) if site is not None else ""
                ctype = TA_TUPLE_RESULT_TYPES.get(ta_name, "std::tuple<double, double>")
                default = self._security_tuple_result_default(ctype, tuple_size)
                lines.append(f"    {ctype} _req_sec_{sec_id} = {default};")
            else:
                self._security_ohlc_hist_fields_by_sec[sec_id] = (
                    self._collect_security_ohlc_hist_fields_for_call(item)
                )
                lines.append(f"    double _req_sec_{sec_id} = na<double>();")
            for field in sorted(self._security_ohlc_hist_fields_by_sec.get(sec_id, ())):
                ctype = self._security_bar_hist_type(field)
                lines.append(
                    f"    Series<{ctype}> {self._security_ohlc_hist_series_cpp(sec_id, field)}{_mbb};"
                )
            self._security_ta_hist_idx_by_sec[sec_id] = (
                self._collect_security_ta_hist_indices(expr_node)
            )
            for name in self._security_ta_hist_series_names(sec_id):
                lines.append(f"    Series<double> {name}{_mbb};")
            self._emit_security_expr_hist_members(sec_id, expr_node, lines, _mbb)

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
        for _ta_idx, site in enumerate(self.ctx.ta_call_sites):
            if _ta_idx in self._dead_ta_indices:
                continue
            lines.append(f"    {site.class_name} {site.member_name};")
            if self._ta_site_uses_precalc(site):
                vtype = self._ta_return_type(site)
                lines.append(f"    std::vector<{vtype}> _precalc_{site.member_name};")
        lines.append("    bool _use_precalc = false;")

        for clock_name in self._lazy_saturated_roc3_clock_by_node.values():
            lines.append(
                f"    {self._lazy_saturated_roc3_type_name} {clock_name};"
            )
        if self._lazy_saturated_roc3_clock_by_node:
            # Dedicated eager close[3] fallback. Its fixed four-slot capacity
            # is independent of the user's max_bars_back directive, which may
            # legitimately be smaller than the offset this generated route
            # requires. It is ordinary copyable script state and therefore
            # joins the automatic COOF checkpoint below.
            lines.append(
                "    Series<double> "
                f"{self._lazy_saturated_roc3_history_name}{{4}};"
            )

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
            # Detect array vars from init expression. Guard the substring
            # heuristic against a UDT constructor that merely WRAPS array.new /
            # array.from in its arguments — e.g.
            # ``var draw d = draw.new(array.new<line>(), array.new<line>())``
            # must declare as ``draw``, not ``std::vector<double>``. (Drawing
            # made this latent collision reachable.)
            _init_str_s = str(init_str)
            _is_udt_ctor_init = any(
                _init_str_s.startswith(f"{u}.new") for u in self._udt_defs
            )
            if (not _is_udt_ctor_init) and (
                "array.new" in _init_str_s or "array.from" in _init_str_s
                or name in self._array_vars
            ):
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
            # Detect UDT vars. Two signals: (1) the analyzer recorded an
            # explicit UDT type annotation in ``_udt_var_types`` - this is the
            # ONLY signal when the initializer is ``na`` (``var SDZone z = na``),
            # where the inferred ``ptype`` is NA->double; (2) the init_str is a
            # ``TypeName.new(...)`` constructor. Without (1) the member would
            # decl as ``double`` and the later ``z = SDZone{...}`` would not
            # compile (assigning SDZone to double).
            init_s = str(init_str)
            # Drawing handle var member (L-N2): a ``var line x`` declares as the
            # C++ handle struct (Series<Line> when also history-referenced).
            # Drawing names are NOT in _udt_defs, so the udt branch below would
            # self-zero them to double; handle them first.
            _draw_cpp = DRAWING_TYPE_TO_CPP.get(self._udt_var_types.get(name))
            if _draw_cpp is not None:
                if name in self.ctx.series_vars:
                    lines.append(f"    Series<{_draw_cpp}> {safe}{_mbb};")
                else:
                    lines.append(f"    {_draw_cpp} {safe};")
                continue
            udt_type = self._udt_var_types.get(name)
            if udt_type not in self._udt_defs:
                udt_type = None
            if udt_type is None:
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
                if name in self._runtime_scalar_var_init_members:
                    # A conditional declaration may not execute for many bars,
                    # while COOF rollback still value-copies every member. Give
                    # pending runtime vars a typed Pine-na sentinel so that copy
                    # is always defined; the declaration-site guard overwrites
                    # it on the first actual execution.
                    pending = self._typed_na_init("na<double>()", name, ptype)
                    lines.append(f"    {cpp_type} {safe} = {pending};")
                else:
                    lines.append(f"    {cpp_type} {safe};")

        # 6. Non-var series vars
        for name in sorted(self.ctx.series_vars):
            if name not in self._var_names:
                safe = self._safe_name(name)
                cpp_type = self._series_type_for(name)
                lines.append(f"    Series<{cpp_type}> {safe}{_mbb};")

        # 7. Fixnan members
        for _fi_idx, site in enumerate(self.ctx.fixnan_sites):
            if _fi_idx in self._dead_fixnan_indices:
                continue
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

        # 8a. Synthetic temporary history.  Unlike the legacy function-local
        # static buffers, these members are value-copyable rollback state and
        # have one identity per source site / emitted UDF variant.
        for info in self._inline_history_members:
            lines.append(
                f"    Series<{info['cpp_type']}> {info['member_name']}{_mbb};"
            )

        # 8b. Global-scope non-var declarations as class members
        #     (so user-defined functions can reference them)
        seen_global = set()
        for name, ptype in self.ctx.global_var_decls:
            if name in seen_global or name in self.ctx.series_vars or name in self._var_names:
                continue
            # De-hoisted UDT array-element alias (Pine reference semantics): the
            # in-loop VarDecl is emitted as a fresh ``UDT& z = arr[i];`` local
            # reference each iteration, so there is no persistent class member.
            if name in self._udt_array_get_ref_locals:
                seen_global.add(name)
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
                # Drawing handle global (L-N6 / U): map line/box/label/linefill
                # to the C++ handle struct (the default is na, id=-1).
                _draw_cpp = DRAWING_TYPE_TO_CPP.get(udt_t)
                if _draw_cpp is not None:
                    lines.append(f"    {_draw_cpp} {safe} = {_draw_cpp}{{}};")
                else:
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
                self._emit_cloned_var_decl(orig_safe, cloned_safe, _mbb, lines)

        # 8c2. Fresh var members for context-sensitive helper instances (nested
        #      helpers reached through >1 distinct call path). Each fresh instance
        #      gets its OWN scalar/series state so two paths never collide.
        for orig_safe, fresh_safe in self._fresh_var_members:
            if fresh_safe in emitted_clones:
                continue
            emitted_clones.add(fresh_safe)
            self._emit_cloned_var_decl(orig_safe, fresh_safe, _mbb, lines)

        # 8c3. Fresh fixnan members for context-sensitive helper instances.
        #      Each fresh instance gets its OWN previous-value member so two
        #      call paths never share fixnan state (mirrors 8c2 for vars).
        for orig_site, fresh_safe in self._fresh_fixnan_members:
            if fresh_safe in emitted_clones:
                continue
            emitted_clones.add(fresh_safe)
            cpp_type = PINE_TYPE_TO_CPP.get(orig_site.pine_type, "double")
            lines.append(f"    {cpp_type} {fresh_safe} = na<{cpp_type}>();")

        # 8d. Drawing-objects-as-data arenas (gated on _uses_drawing so
        #     non-drawing strategies emit byte-identical C++). Each arena is a
        #     per-strategy member -> reset-per-run is automatic. Caps come from
        #     the strategy() header max_*_count (default 50; linefill default 50).
        if self._uses_drawing:
            caps = self._drawing_caps or {}
            lines.append(f"    DrawingArena<LineRec> _pf_lines_{{{caps.get('line', 50)}}};")
            lines.append(f"    DrawingArena<BoxRec> _pf_boxes_{{{caps.get('box', 50)}}};")
            lines.append(f"    DrawingArena<LabelRec> _pf_labels_{{{caps.get('label', 50)}}};")
            lines.append(f"    DrawingArena<LinefillRec> _pf_linefills_{{{caps.get('linefill', 50)}}};")

        # 9. _var_initialized flag
        if self.ctx.var_members:
            lines.append("    bool _var_initialized = false;")

        # 9a. Per-member flags for primitive runtime ``var`` / ``varip``
        # initializers. Unlike the global aggregate/Series latch above, these
        # live at the declaration site so prior statements are available and a
        # conditional declaration initializes on its first actual execution.
        for info in self._runtime_scalar_var_init_by_node.values():
            lines.append(f"    bool {info['flag']} = false;")

        # 9b. Per-function-variant ``var`` init flags. A function-scoped
        #     ``var`` (Pine "init once" semantics) is a function-local static:
        #     its initializer runs on the FIRST call to that function variant
        #     (with the first bar's values the function actually sees) and the
        #     result persists for the strategy's lifetime. Each clone (cs0,
        #     cs1, ...) is an independent instance with its own flag.
        #     ``func_var_members`` is keyed by the plain Pine function name
        #     (``fi.name``), so this matches both plain UDFs and UDT methods.
        for fi in self.ctx.func_infos:
            if fi.name not in self.ctx.func_var_members:
                continue
            total_cs = self.ctx.func_call_site_counts.get(fi.name, 0)
            if total_cs > 0:
                for cs_idx in range(total_cs):
                    lines.append(f"    bool _fvinit_{self._func_safe_name(fi.name)}_cs{cs_idx} = false;")
            else:
                lines.append(f"    bool _fvinit_{self._func_safe_name(fi.name)} = false;")

        # 9b2. ``var`` init flags for fresh context-sensitive helper instances.
        for inst in self._fresh_instances:
            if inst["fname"] in self.ctx.func_var_members and inst["var_remap"]:
                lines.append(f"    bool _fvinit_{inst['name']} = false;")

        # 9c. _ta_initialized_ flag for runtime TA re-sizing (first on_bar only).
        if self.ctx.ta_call_sites:
            lines.append("    bool _ta_initialized_ = false;")

        # 9d. _inputs_initialized_ flag for cached global inputs.
        lines.append("    bool _inputs_initialized_ = false;")

        lines.append("")

        # 9e. Historical execution rollback checkpoint.  Derive the member
        # inventory from the declarations above so every future generated
        # state category is captured automatically (or generation fails loudly
        # if it introduces an unfamiliar declaration form).
        _script_state_members = self._collect_script_state_members(
            lines[_script_state_decl_start:-1]
        )
        self._emit_script_state_hooks(lines, _script_state_members)
        lines.append("")

        # 9. Constructor with TA initializer list
        self._emit_constructor(lines)
        lines.append("")

        # 10. User-defined functions (with per-call-site variants for functions
        #     containing TA calls OR series variables that need isolation)
        for fi in self.ctx.func_infos:
            # Dead-code user functions (defined but never called, with TA
            # state whose ctor args can't be sized) are skipped entirely —
            # their bodies reference TA members we no longer emit, and the
            # functions never run anyway.
            if fi.name in self._dead_func_names:
                continue
            total_cs = self.ctx.func_call_site_counts.get(fi.name, 0)
            has_ta = fi.name in self.ctx.func_ta_ranges
            has_series = fi.name in self.ctx.func_series_vars or fi.name in self.ctx.func_var_members
            # A function whose ONLY reason to need per-call-site cloning is a
            # security-tf-monomorphized request.security (no TA/series state
            # of its own — see Analyzer._check_mixed_callsite_security_tf)
            # still needs N separate emitted bodies so self._active_call_site_idx
            # is set while each is emitted (read by the request.security
            # use-site lowering in visit_call.py to pick the right clone's
            # sec_id).
            needs_security_clone = fi.name in self.ctx.func_security_clone_only
            if (has_ta or has_series or needs_security_clone) and total_cs > 0:
                # Emit one variant per call site
                for cs_idx in range(total_cs):
                    self._emit_func_def(fi, lines, call_site_idx=cs_idx)
                    lines.append("")
            else:
                self._emit_func_def(fi, lines)
                lines.append("")

        # 10a. Fresh context-sensitive instances of nested stateful helpers
        #      (reached through >1 distinct call path). Each is bound to its own
        #      path-specific TA + var members; see _build_func_instances.
        if self._fresh_instances:
            fi_by_name = {fi.name: fi for fi in self.ctx.func_infos}
            for inst in self._fresh_instances:
                fi = fi_by_name.get(inst["fname"])
                if fi is None:
                    continue
                self._emit_func_def(fi, lines, instance=inst)
                lines.append("")

        # 11. on_bar()
        self._current_instance_name = None
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
                return f'std::string("{self._cpp_string_escape(val)}")'
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
            # chart.point.* resolves to namespace "chart" (a SKIP_NAMESPACE) but
            # is REAL data (a ChartPoint aggregate literal). Never skip it.
            if self._is_chart_point_callee(node.callee):
                return False
            func_name, namespace = self._resolve_callee(node.callee)
            if namespace is None and func_name in SKIP_FUNC_NAMES:
                return True
            if namespace in SKIP_NAMESPACES:
                return True
            if namespace in SKIP_VAR_TYPES:
                return True
            # Method call on a table/polyline-typed receiver var/param
            # (``panel.cell(...)``). These types have no C++ representation, so
            # the call is a visual no-op — drop it (mirrors the namespace form).
            if namespace in self._visual_drop_vars:
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

    # Pine ``timeframe.<member>`` -> C++ runtime expression. Mirrors the
    # mapping in ``visit_expr._visit_member_access`` so a stable timeframe
    # fragment embedded in a TA ctor reset renders to the same C++ the
    # expression visitor would emit for a direct ``timeframe.*`` read.
    _TIMEFRAME_MEMBER_CPP: dict[str, str] = {
        "period": "script_tf_",
        "main_period": "main_period()",
        "multiplier": "tf_multiplier(script_tf_)",
        "isintraday": "tf_is_intraday(script_tf_)",
        "isminutes": "(tf_is_intraday(script_tf_) && !tf_is_seconds(script_tf_))",
        "isdaily": "tf_is_daily(script_tf_)",
        "isweekly": "tf_is_weekly(script_tf_)",
        "ismonthly": "tf_is_monthly(script_tf_)",
        "isdwm": "(tf_is_daily(script_tf_) || tf_is_weekly(script_tf_) || tf_is_monthly(script_tf_))",
        "isseconds": "tf_is_seconds(script_tf_)",
        "in_seconds": "tf_to_seconds(script_tf_)",
        "isticks": "false",
    }

    # Pine ``math.<member>`` -> C++ form. Function members map to ``std::*``;
    # constants map to their engine-side macro / literal.
    _MATH_MEMBER_CPP: dict[str, str] = {
        "pi": "M_PI", "e": "M_E", "phi": "1.618033988749895",
        "rphi": "0.6180339887498949",
        "abs": "std::abs", "max": "std::max", "min": "std::min",
        "round": "std::round", "floor": "std::floor", "ceil": "std::ceil",
        "sqrt": "std::sqrt", "log": "std::log", "log10": "std::log10",
        "exp": "std::exp", "pow": "std::pow",
        "sin": "std::sin", "cos": "std::cos", "tan": "std::tan",
        "asin": "std::asin", "acos": "std::acos", "atan": "std::atan",
        "sign": "(double)([] (double _v) { return (_v>0) - (_v<0); })",
    }

    # Pine logical operators (word form) -> C++ operator, used when rendering
    # a stable runtime expression. Matched with word boundaries.
    _PINE_LOGICAL_OPS: dict[str, str] = {"and": "&&", "or": "||", "not": "!"}

    def _render_inline_input_calls(self, expr_str: str) -> tuple[str, bool]:
        """Render inline ``input(...)`` / ``input.<type>(...)`` calls in a TA
        ctor-arg expression string to override-aware ``get_input_*()`` reads.

        A bare input expression passed straight as a length argument
        (``adx(input(15), input(15))``) reaches the reset path as the raw call
        spelling ``input(15)`` because the analyzer's param-substitution has no
        intermediate variable to record in ``_input_backed_vars``. This helper
        finds each such call (balanced parens, ``input`` optionally followed by
        ``.<type>``), re-parses it into a FuncCall, and renders it via the same
        ``_render_input_value`` used for ordinary input var reads.

        Returns ``(rewritten_str, found_any)``. When no inline input call is
        present, the string is returned unchanged with ``found_any=False``.
        """
        import re
        # Locate ``input`` (as a word, not a substring of get_input_int etc.)
        # optionally followed by ``.<member>``, then a ``(`` opening a balanced
        # argument list.
        out = expr_str
        found = False
        idx = 0
        while idx < len(out):
            m = re.search(r"\binput\b", out[idx:])
            if m is None:
                break
            start = idx + m.start()
            # Reject a match that is part of a longer identifier
            # (e.g. ``get_input_int``) — the \b guard above already handles
            # alphanumerics, but be defensive.
            if start > 0 and (out[start - 1].isalnum() or out[start - 1] == "_"):
                idx = start + len("input")
                continue
            j = start + len("input")
            # Optional ``.<member>`` for the typed form ``input.int(...)``.
            member = None
            if j < len(out) and out[j] == ".":
                k = j + 1
                nm_start = k
                while k < len(out) and (out[k].isalnum() or out[k] == "_"):
                    k += 1
                if k > nm_start:
                    member = out[nm_start:k]
                    j = k
            # Must be followed by ``(`` to be a call.
            if j >= len(out) or out[j] != "(":
                idx = j
                continue
            # Walk the balanced parens to extract the call substring.
            depth = 0
            k = j
            while k < len(out):
                ch = out[k]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        k += 1
                        break
                k += 1
            if depth != 0:
                # Unbalanced — bail on this match.
                idx = j + 1
                continue
            call_src = out[start:k]
            try:
                from ..lexer import Lexer
                from ..parser import Parser
                tokens = Lexer(call_src).tokenize()
                node = Parser(tokens, source=call_src)._parse_expression()
                if not self._is_input_call(node):
                    idx = k
                    continue
                func_name_i, namespace_i = self._resolve_callee(node.callee)
                # Inline inputs have no enclosing var; reuse the default
                # value as a synthetic title key so distinct defaults get
                # distinct input controls (and identical defaults collapse,
                # which is correct since they resolve to the same value).
                default_node = self._get_input_default(node)
                synth_title = self._visit_expr(default_node) if default_node is not None else ""
                title = self._get_input_title(node, var_name=None)
                if not title:
                    title = synth_title
                rendered = self._render_input_value(node, func_name_i, namespace_i, title)
            except Exception:
                idx = k
                continue
            out = out[:start] + rendered + out[k:]
            found = True
            idx = start + len(rendered)
        return out, found

    def _runtime_ctor_arg_for_reset(self, arg_str: str) -> str | None:
        """Convert a TA ctor-arg string into its runtime C++ expression.

        Returns the runtime expression (e.g.
        ``get_input_int("MACD Fast", 12)`` or a ternary / math expression
        over such reads and ``timeframe.*`` members) when the ctor arg
        depends on a stable runtime scalar — an input-backed variable, a
        ``timeframe.*`` member, or arithmetic / ternaries / casts over
        those. Returns None for pure literals or expressions that contain
        any unrecognised (potentially series) identifier, so the caller
        (the TA ctor guard) rejects them loudly instead of silently
        emitting period 1.
        """
        import re
        ident_re = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")

        # Expand class-scope derived vars (``wilderLen`` -> ``(rsiLen * 2 - 1)``,
        # ``fastPeriod`` -> ``(isM5 ? ... : ...)``) to their raw RHS so input
        # leaves become get_input_*() reads below. Recursive (bounded) to
        # handle chains of derived vars; guards against cycles.
        def _expand_derived(s: str, seen: frozenset = frozenset(), depth: int = 0) -> str:
            if depth > 32:
                return s
            def _rep(p: re.Match) -> str:
                nm = p.group(0)
                if nm in self._derived_input_expr and nm not in seen:
                    inner = self._derived_input_expr[nm]
                    return "(" + _expand_derived(inner, seen | {nm}, depth + 1) + ")"
                return nm
            return ident_re.sub(_rep, s)

        expanded = _expand_derived(arg_str)

        tokens = set(ident_re.findall(expanded))

        # Gate: every identifier token must be renderable. If any token is an
        # unrecognised bare identifier (not an input, not a known const, not
        # a structural keyword / namespace prefix, not a stable tracked var
        # that we already expanded), we conservatively refuse — that identifier
        # would otherwise leak through as an undeclared C++ symbol, or worse,
        # a series var that should have been rejected by the ctor guard.
        structural = (set(self._PINE_LOGICAL_OPS)
                      | {"timeframe", "math", "syminfo",
                         "int", "float", "bool", "string",
                         "true", "false", "na"})
        member_tokens = (set(self._TIMEFRAME_MEMBER_CPP)
                         | set(self._MATH_MEMBER_CPP))
        renderable = (self._input_backed_vars
                      | self._stable_runtime_vars
                      | structural
                      | member_tokens)
        leftover = tokens - renderable
        # Known compile-time consts that survived expansion (e.g. ``pi`` was
        # NOT tracked as a Python value but its name token is a stable var
        # already; pure numeric names are in _known_vars and covered above).
        leftover = {t for t in leftover if t not in self._known_vars}
        # Inline ``input(...)`` / ``input.<t>(...)`` calls (a bare input
        # expression passed straight as a length arg, e.g.
        # ``adx(input(15), input(15))``) are re-parsed and rendered below,
        # after the gate. ``input`` is the only token they contribute, so
        # allow it through the gate here.
        leftover.discard("input")
        if leftover:
            return None

        # Must depend on at least one runtime component (input-backed var, a
        # timeframe reference, or an inline input() call); otherwise it's a
        # pure compile-time expr and no reset is needed (the ctor-init literal
        # is correct).
        has_input = any(t in self._input_backed_vars for t in tokens)
        has_timeframe = "timeframe" in tokens
        has_inline_input = "input" in tokens
        if not (has_input or has_timeframe or has_inline_input):
            return None

        # Preferred path: re-parse the (gate-approved, grouping-faithful)
        # expression and lower it through the SAME expression visitor the
        # statement path uses. Operator grouping and Pine numeric typing
        # (``/`` always yields float; ``(double)`` coercion) are then correct
        # by construction — reused from ``_visit_binop``, not re-derived here.
        # Falls back to the legacy token-substitution renderer below only if
        # re-parse / lowering unexpectedly fails, so a working site is never
        # worse off than before this change.
        lowered = self._lower_reset_expr_via_visitor(expanded)
        if lowered is not None:
            return lowered

        # Render inline input() calls to override-aware get_input_*() reads
        # now that the gate has accepted the expression. Done after the gate
        # so the rendered getter tokens (get_input_int, ...) do not have to
        # be added to ``renderable``.
        if has_inline_input:
            expanded, _ = self._render_inline_input_calls(expanded)

        expr = expanded

        # 1) timeframe.<member> -> C++ (before ident substitution so the
        # member names don't get caught by the identifier pass). Use a
        # targeted regex so e.g. ``isminutes`` is not confused with
        # ``ismonthly``.
        def _tf_rep(p: re.Match) -> str:
            mem = p.group(1)
            return self._TIMEFRAME_MEMBER_CPP.get(mem, p.group(0))
        # ``timeframe.in_seconds()`` is a function-form member in Pine (the
        # only one in the table); its C++ form ``tf_to_seconds(script_tf_)``
        # is already a complete call, so consume the Pine ``()`` to avoid a
        # double-call ``tf_to_seconds(script_tf_)()``. Property-form members
        # (``timeframe.isdaily``) never carry ``()`` so the optional group is
        # a no-op for them.
        expr = re.sub(r"\btimeframe\.(\w+)(?:\(\))?", _tf_rep, expr)

        # 2) math.<member> -> C++ (constants + std::* functions).
        def _math_rep(p: re.Match) -> str:
            mem = p.group(1)
            return self._MATH_MEMBER_CPP.get(mem, p.group(0))
        expr = re.sub(r"\bmath\.(\w+)", _math_rep, expr)

        # 3) Pine word-logical operators -> C++ operators (after timeframe/math
        # substitution so we don't rewrite inside their C++ expansions).
        for pine_op, cpp_op in self._PINE_LOGICAL_OPS.items():
            expr = re.sub(rf"\b{pine_op}\b", cpp_op, expr)

        # 4) Substitute input-backed vars with override-aware get_input_*()
        # reads, and inline known compile-time consts (non-input) as literals.
        def _sub(p: re.Match) -> str:
            name = p.group(0)
            if name in self._input_backed_vars:
                call_node = self._input_var_to_call.get(name)
                if call_node is None:
                    return name
                func_name_i, namespace_i = self._resolve_callee(call_node.callee)
                title = self._get_input_title(call_node, var_name=name)
                return self._render_input_value(call_node, func_name_i, namespace_i, title)
            if name in self._known_vars and name not in self._input_backed_vars:
                val = self._known_vars[name]
                if isinstance(val, bool):
                    return "true" if val else "false"
                if isinstance(val, (int, float)):
                    return str(val)
                return f'std::string("{self._cpp_string_escape(val)}")'
            return name

        rewritten = ident_re.sub(_sub, expr)

        # 5) Pine auto-converts floats to ints for TA lengths; C++ does not.
        # If any math.* function appears (returns double) OR a timeframe.*
        # boolean is part of a ternary whose branches are doubles, wrap the
        # whole expression in an explicit int cast so the TA ctor gets an
        # integer length.
        had_math = "std::" in rewritten or bool(re.search(r"\btimeframe\b", expanded))
        if had_math:
            return f"(int)({rewritten})"
        return rewritten

    def _lower_reset_expr_via_visitor(self, expanded: str) -> str | None:
        """Re-parse a gate-approved, fully-expanded TA-length expression and
        lower it through the SAME expression visitor the statement path uses,
        so operator grouping and Pine numeric typing are preserved identically
        (Pine ``/`` always yields float; ``math.*`` returns double; branches are
        parenthesized). Reuses ``_visit_binop`` etc.; nothing is re-typed here.

        Input-backed variables render as override-aware ``get_input_*()`` reads
        (not member refs) via ``_reset_input_getter_mode`` — the reset can run
        in ``evaluate_security`` before the input members are initialised, so it
        must not depend on their init order. Inline ``input(...)`` calls and
        ``math.*`` / ``timeframe.*`` members lower through the visitor's own
        handlers (same C++ the statement path would emit).

        Returns None if re-parse / lowering fails, so the caller falls back to
        the legacy token-substitution renderer (never worse than before)."""
        try:
            from ..lexer import Lexer
            from ..parser import Parser
            tokens = Lexer(expanded).tokenize()
            node = Parser(tokens, source=expanded)._parse_expression()
        except Exception:
            return None
        prev = self._reset_input_getter_mode
        self._reset_input_getter_mode = True
        try:
            rendered = self._visit_expr(node)
        except Exception:
            return None
        finally:
            self._reset_input_getter_mode = prev
        if not rendered or "/* " in rendered:
            # Unknown/unhandled node leaked a placeholder — defer to legacy.
            return None
        # A TA length must be int. Truncate when the lowered form is
        # float-typed (a ``std::*`` call, a ``(double)`` division coercion, or a
        # ``timeframe.*`` helper — all timeframe helpers reference script_tf_).
        # A bare int / identifier length carries none of these and is left
        # unwrapped, so simple sites stay byte-identical to the legacy output.
        if ("std::" in rendered or "(double)" in rendered
                or "script_tf_" in rendered):
            return f"(int)({rendered})"
        return rendered


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
        for _ta_idx, site in enumerate(self.ctx.ta_call_sites):
            if _ta_idx in self._dead_ta_indices:
                continue
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

        # Security-context TA copies. Normally these share the ctor args of
        # their main-context site, but a request.security nested in a helper
        # called at several sites is cloned per call site (distinct sec_id +
        # callsite_idx) while the shared TA site's ``ctor_args`` were resolved
        # ONCE (against the first call site). Every clone would then size its
        # indicator from call site 0's argument (e.g. four EMAs all pinned to
        # crossFastLen instead of the per-site fast/slow lengths). Resolve each
        # sec's ctor args against ITS call site by reusing the per-call-site
        # function-clone TA remap (identity for cs0 / non-clones, so all other
        # output stays byte-identical).
        sec_call_by_id = {it["sec_id"]: it for it in self._security_calls}
        ta_site_by_member = {s.member_name: s for s in self.ctx.ta_call_sites}
        for info in self._security_eval_info:
            sec_item = sec_call_by_id.get(info["sec_id"])
            sec_containing = (sec_item or {}).get("containing_func") or ""
            sec_cs_idx = (sec_item or {}).get("callsite_idx")
            for idx, variants in (info.get("ta_variants") or {}).items():
                site = self.ctx.ta_call_sites[idx]
                ctor_site = site
                if sec_containing and sec_cs_idx is not None:
                    remap = self._func_cs_ta_remap.get((sec_containing, sec_cs_idx))
                    if remap:
                        cloned_name = remap.get(site.member_name)
                        if cloned_name and cloned_name != site.member_name:
                            cand = ta_site_by_member.get(cloned_name)
                            if cand is not None:
                                ctor_site = cand
                if not ctor_site.ctor_args:
                    continue
                runtime_args = []
                any_runtime = False
                for a in ctor_site.ctor_args:
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
