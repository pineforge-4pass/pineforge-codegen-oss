"""Semantic analyzer for PineScript v6 AST.

Walks the AST produced by the parser, builds a symbol table, infers types,
detects series variables, collects TA call-sites, and outputs an
AnalyzerContext for the code generator.
"""

from __future__ import annotations

from typing import Any

from ..ast_nodes import (
    ASTNode,
    Program, StrategyDecl, ImportStmt,
    VarDecl, Assignment, TupleAssign,
    IfStmt, ForStmt, ForInStmt, WhileStmt, SwitchStmt, BreakStmt, ContinueStmt,
    FuncDef, ExprStmt,
    BinOp, UnaryOp, Ternary, FuncCall, Subscript,
    Identifier, MemberAccess, TypeAnnotation,
    NumberLiteral, StringLiteral, BoolLiteral, NaLiteral, ColorLiteral,
    TupleLiteral,
    TypeDecl, EnumDecl, MethodDef, TypeField,
)
from ..symbols import PineType, Symbol, SymbolTable, TypeSpec
from ..errors import SourceLocation, Diagnostic, CompileError, Level, Phase
from .. import signatures as sigs
from .. import tv_input_choices as tv_in
# Output dataclasses (contract with the codegen) live in contracts.py so
# the import graph stays a strict DAG: contracts <- {base, call_handlers,
# __init__}.
from .contracts import (
    AnalyzerContext,
    FixnanCallSite,
    FuncInfo,
    MutableGlobalInfo,
    SecurityCallInfo,
    TACallSite,
)


# ---------------------------------------------------------------------------
# Output data structures (defined in contracts.py; re-imported above so
# this module's existing references like ``TACallSite(...)`` resolve
# without qualification).
# ---------------------------------------------------------------------------


# AnalyzerContext is defined in ``contracts.py`` (re-imported above).
# Adding new context fields: edit ``contracts.py``, not this file.


# ---------------------------------------------------------------------------
# Mapping tables — definitions live in ``tables.py``; re-imported here so
# inline references inside this module (TA_CLASS_MAP[name], BUILTIN_VARS,
# etc.) keep resolving without qualification. The package-level
# ``__init__.py`` re-exports the same names for external consumers
# (``codegen/base.py``, ``support_checker.py``, and external tests).
# ---------------------------------------------------------------------------

from .tables import (
    TA_CLASS_MAP,
    TA_PERIOD_ARG,
    TA_TUPLE_RETURNS,
    TA_MULTI_CTOR,
    TA_NO_CTOR,
    BUILTIN_VARS,
    BAR_FIELDS,
    SKIP_FUNCS,
)

# TypeHelper mixin owns the Pine type-hint / expression -> TypeSpec / PineType
# inference helpers previously inlined in this module; see
# ``compiler/transpiler/analyzer/types.py``.
from .types import TypeHelper

# DiagnosticsHelper mixin owns _error / _warn / _input_diag_loc /
# _expr_to_str / _warn_if_unknown_source_id; see
# ``compiler/transpiler/analyzer/diagnostics.py``.
from .diagnostics import DiagnosticsHelper

# CallHandlers mixin owns the ~14 _handle_*_call dispatchers plus
# input-validation / TA-arg-merge helpers; see
# ``compiler/transpiler/analyzer/call_handlers.py``. Largest analyzer
# mixin (~500 lines).
from .call_handlers import CallHandlers


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class Analyzer(CallHandlers, DiagnosticsHelper, TypeHelper):
    """Semantic analysis pass over PineScript v6 AST.

    Mixin chain (Python MRO is left-to-right; method names are
    intentionally kept disjoint across mixins so the order is mostly
    cosmetic):
        * ``CallHandlers`` -- per-callee dispatch and bookkeeping for
          ``ta.*`` / ``request.*`` / ``strategy.*`` / ``input.*`` /
          ``fixnan(...)`` / user-function calls (``_handle_*_call``,
          ``_merge_ta_args``, ``_merge_input_params``,
          ``_validate_input_member_tv``, ...)
        * ``DiagnosticsHelper`` -- ``_error`` / ``_warn`` /
          ``_input_diag_loc`` / ``_expr_to_str`` /
          ``_warn_if_unknown_source_id``
        * ``TypeHelper`` -- Pine type-hint / expression -> TypeSpec /
          PineType inference helpers (``_type_spec_from_hint``,
          ``_type_spec_from_expr``, ``_extract_literal_value``, ...)

    Future steps may further peel visitor / top-level / UDT mixins
    out of this class; see ``compiler/transpiler/analyzer/`` for
    the package layout.
    """

    def __init__(self, ast: Program, filename: str = "<stdin>") -> None:
        self._ast = ast
        self._filename = filename
        self._symbols = SymbolTable()
        self._ta_call_sites: list[TACallSite] = []
        self._series_vars: set[str] = set()
        self._series_var_members: set[str] = set()
        self._series_decl_nodes: set[int] = set()
        self._series_decl_bindings: set[tuple[int, str]] = set()
        self._series_bar_fields: set[str] = set()
        self._var_members: list[tuple[str, PineType, str]] = []
        self._func_infos: list[FuncInfo] = []
        self._fixnan_sites: list[FixnanCallSite] = []
        self._strategy_params: dict = {}
        self._diagnostics: list[Diagnostic] = []
        self._global_var_decls: list[tuple[str, PineType]] = []
        # Top-level ordinary bindings are lexical global state even when a
        # same-named callable history reference has already polluted the
        # legacy raw ``series_vars`` set and suppresses ``global_var_decls``.
        # Keep an AST-authoritative inventory for storage collision routing.
        self._ordinary_global_binding_names: set[str] = {
            stmt.name
            for stmt in ast.body
            if isinstance(stmt, VarDecl)
            and not stmt.is_var
            and not stmt.is_varip
        }
        self._ordinary_global_binding_names.update(
            name
            for stmt in ast.body
            if isinstance(stmt, TupleAssign)
            for name in stmt.names
            if name != "_"
        )
        self._direct_program_binding_names: set[str] = {
            stmt.name
            for stmt in ast.body
            if isinstance(stmt, VarDecl)
        }
        self._direct_program_binding_names.update(
            name
            for stmt in ast.body
            if isinstance(stmt, TupleAssign)
            for name in stmt.names
            if name != "_"
        )
        # Exact declaration typing retained only for lexical-boundary checks.
        # The legacy symbol/type registries are raw-name keyed and therefore
        # cannot distinguish a direct script binding from a same-spelled local
        # declared inside a top-level control-flow block.
        self._var_decl_types_by_node: dict[
            int, tuple[PineType, TypeSpec | None]
        ] = {}
        self._ordinary_global_binding_info: dict[
            str, tuple[int, PineType, ASTNode | None]
        ] = {}
        self._ordinary_global_series_names: set[str] = set()
        self._global_expr_map: dict[str, Any] = {}
        self._var_member_init_exprs: dict[str, Any] = {}
        # Exact declaration-node ownership for persistent vars.  The member
        # name can differ from ``node.name`` when sibling blocks declare the
        # same raw identifier, so codegen must not reconstruct this mapping
        # from names when it emits declaration-site initialization.
        self._var_member_metadata_by_node: dict[int, tuple] = {}
        self._var_member_type_specs_by_node: dict[int, TypeSpec | None] = {}
        self._var_member_owners_by_node: dict[int, str | None] = {}
        # Block-scoped ``var``/``varip`` name-collision disambiguation.
        # Two same-named block-scoped vars in SIBLING non-global, non-function
        # scopes (e.g. ``var bool valid`` declared inside ``if A`` and again
        # inside ``if B``) would otherwise dedupe to ONE C++ member and
        # cross-contaminate. ``_block_node_stack`` tracks the enclosing
        # branch/loop body owners during analysis; ``_block_var_owner`` maps a
        # raw block-var name to the id() of the FIRST body that declared it;
        # ``_block_var_renames`` maps id(body_owner) -> {raw_name: unique}
        # for every later colliding block so codegen can activate the
        # rename via ``_active_var_remap`` while emitting that block.
        self._block_node_stack: list[Any] = []
        self._block_var_owner: dict[str, int] = {}
        self._block_var_renames: dict[int, dict[str, str]] = {}
        self._block_var_seq = 0
        # Identifier node identity -> the lexical Symbol.scope resolved while
        # that node's source scope is live.  Codegen uses this narrow
        # provenance to distinguish true global aliases from same-named local
        # shadows after the analyzer's scope stack has unwound.
        self._identifier_binding_scopes: dict[int, str | None] = {}
        self._ta_counter = 0
        self._fixnan_counter = 0
        # All fixnan member names minted so far (base + clones), for O(1)
        # collision detection when minting a per-call-site fixnan clone.
        self._fixnan_member_names: set[str] = set()
        # Authoritative fixnan clone-name map for collisions: (func, cs_idx)
        # -> {orig_member: cloned_member}. Consumed verbatim by the codegen
        # when the default ``{base}_cs{cs_idx}`` formula would collide.
        self._func_cs_fixnan_clone_names: dict[tuple[str, int], dict[str, str]] = {}
        # Track user-defined function nodes for deferred analysis
        self._func_defs: dict[str, FuncDef] = {}
        # Track user-defined function return types
        self._func_return_types: dict[str, PineType] = {}
        # Track user-defined function tuple returns
        self._func_returns_tuple: dict[str, bool] = {}
        self._func_tuple_element_count: dict[str, int] = {}
        self._func_tuple_element_types: dict[str, tuple[PineType, ...]] = {}
        self._tuple_element_types_by_node: dict[int, tuple[PineType, ...]] = {}
        # Track user-defined functions whose body returns a UDT instance —
        # maps func_name -> UDT type name. Detected from the body's final
        # expression (``=> Sample.new(...)`` or last stmt ``Sample.new(...)``).
        # Probe: data/validation/udt-method-probe-20-udt-return-from-func.
        self._func_udt_return_types: dict[str, str] = {}
        self._func_return_type_specs: dict[str, "TypeSpec"] = {}
        self._func_param_type_specs: dict[str, list] = {}
        # Direct terminal gets on an unshadowed built-in temporary are
        # registered while their lexical scope is live.  A later-defined UDF
        # used by ``array.from`` may make the first-pass scalar fallback stale;
        # a bounded, side-effect-free refresh corrects only this proven shape.
        self._direct_terminal_array_temporary_exprs: dict[str, ASTNode] = {}
        # History receivers can mention an untyped UDF parameter before its
        # concrete TypeSpec is learned from a call site.  Keep the exact AST
        # identifier identities that resolved to parameters during the
        # definition pass; call handling re-checks those receivers once the
        # argument specs are available.  Node identity (rather than raw name)
        # prevents a later local/loop binding with the same spelling from
        # being mistaken for the parameter it shadows.
        self._deferred_param_history_refs: dict[
            str, list[tuple[Subscript, dict[int, str]]]
        ] = {}
        # Parameter flow between user callables, captured while the caller's
        # lexical symbols are still in scope.  This lets a later concrete map
        # call revalidate history hidden behind wrappers without re-analyzing
        # whole function bodies or conflating same-spelled locals.
        self._deferred_param_call_edges: dict[
            str,
            list[
                tuple[
                    int,
                    str,
                    list[str],
                    list[ASTNode | None],
                    list[dict[int, str]],
                ]
            ],
        ] = {}
        self._func_series_history_nodes: dict[
            tuple[str, str], Subscript
        ] = {}
        # Per-function var_members and series_vars (for call-site cloning)
        self._func_var_members: dict[str, list] = {}  # func_name -> [(name, PineType, init_str)]
        # Ordinary FuncDef raw persistent name -> exact emitted member.  The
        # map is identity for ordinary non-colliding functions and changes only
        # when distinct persistent declarations would otherwise share a
        # supported primitive/collection ``var`` owned by an ordinary FuncDef
        # with the same raw spelling.  Methods and top-level bindings remain on
        # their established paths.
        self._func_var_storage_names: dict[str, dict[str, str]] = {}
        self._func_series_vars: dict[str, set] = {}   # func_name -> set[str]
        # Declaration-bound non-persistent history locals are distinct from
        # history parameters/global reads carried by ``func_series_vars``.
        # Codegen needs this exact subset when a raw spelling also belongs to
        # owner-qualified persistent state.
        self._nonpersistent_series_decl_names: set[str] = set()
        self._func_nonpersistent_series_vars: dict[str, set[str]] = {}
        # Per-call-site TA tracking for user functions
        self._func_ta_ranges: dict[str, tuple[int, int]] = {}  # func_name -> (start, end) indices
        # Exact counterpart to the legacy contiguous ranges. Borrowed nested
        # sites can straddle unrelated TA allocations, so constructor argument
        # substitution and codegen remaps must iterate these identities only.
        self._func_ta_indices: dict[str, list[int]] = {}
        # A shared TA site can be viewed through more than one callable's
        # parameter names (innerLen -> outerLen). Preserve one constructor
        # template per callable/site instead of mutating the site's single
        # source template and relying on same-spelled forwarding parameters.
        self._func_ta_ctor_args: dict[str, dict[int, list[str]]] = {}
        # Exact TA-site mapping and source-template snapshot for each textual
        # callable edge.  Late whole-program propagation uses these to revisit
        # an already-numbered edge when its callee gains another owned TA site
        # (or refines an existing per-owner constructor template), without
        # cloning the edge's previously-materialized state a second time.
        self._func_ta_call_targets: dict[
            tuple[int, int], dict[int, int]
        ] = {}
        self._func_ta_call_templates: dict[
            int, dict[int, tuple[str, ...]]
        ] = {}
        self._func_call_site_count: dict[str, int] = {}  # func_name -> count
        self._func_call_cs_map: dict[int, tuple[str, int]] = {}  # call_node_id -> (func_name, cs_idx)
        # Primitive type facts retained per written call AST and reconciled to
        # the emitted cs0/cs1/... identities after the stateful call graph is
        # closed. Pine explicitly permits an untyped parameter to inherit a
        # different type at each written call site.
        self._callable_bound_param_types_by_node: dict[int, list[PineType]] = {}
        self._func_callsite_param_types: dict[
            tuple[str, int], list[PineType]
        ] = {}
        self._func_callsite_return_types: dict[
            tuple[str, int], PineType
        ] = {}
        # Textual nested calls whose identity is inherited from the active
        # parent clone rather than assigned a fixed source-level cs index.
        # Kept separately so a second propagation pass (security TF cloning)
        # does not accidentally backfill them as a new cs{N} call site.
        self._func_inherited_call_nodes: set[int] = set()
        # Exact callee identity for those removed mappings. Natural parent
        # clones still use active-index fallback, while a context-sensitive
        # fresh parent has no active index and needs this edge identity to
        # compose and dispatch its nested instance explicitly.
        self._func_inherited_call_names: dict[int, str] = {}
        # Per-function fixnan site ownership: func_name -> list of fixnan site
        # indices in self._fixnan_sites owned by that function. Mirrors the
        # TA-range slicing but for fixnan state, so per-call-site cloning can
        # mint fresh fixnan members per variant and the codegen dead-code pass
        # can skip fixnan state owned by dead functions.
        self._func_fixnan_indices: dict[str, list[int]] = {}
        # Functions that need per-call-site BODY cloning despite owning no
        # TA/series/var state.  The set originated for security-tf
        # monomorphization; it is also the existing emitter contract used by
        # pure wrappers around stateful callees and fixnan-only functions.
        # Security evaluator identity remains independently tracked by each
        # SecurityCallInfo.callsite_idx.
        self._func_security_clone_only: set[str] = set()
        # Authoritative clone-name map: (func_name, cs_idx) -> {orig_member_name:
        # cloned_member_name}. The codegen rebuilds its TA remap from the
        # ``{orig}_cs{cs_idx}`` formula by default, but a TA site reached through
        # MULTIPLE enclosing functions (e.g. a helper cloned both directly and via
        # a range-widened outer function) can collide on that formula. When the
        # analyzer must disambiguate a clone's member name, it records the actual
        # chosen name here so the codegen consumes it verbatim instead of
        # re-deriving a colliding name. Empty for the common (no-collision) case,
        # keeping generated output byte-identical.
        self._func_cs_ta_clone_names: dict[tuple[str, int], dict[str, str]] = {}
        # All TA member names minted so far (base + clones), for O(1) collision
        # detection when minting a new clone.
        self._ta_member_names: set[str] = set()
        # UDT field definitions: type_name -> {field_name: PineType}
        self._udt_fields: dict[str, dict[str, PineType]] = {}
        # var_name -> UDT type for variables holding UDT instances
        self._udt_var_types: dict[str, str] = {}
        self._collection_types: dict[str, TypeSpec] = {}
        # Collection metadata for callable locals must retain lexical identity.
        # Keys match FuncInfo.name: ``func`` for ordinary UDFs and
        # ``Type.method`` for UDT methods.  ``_collection_types`` remains the
        # top-level/on_bar registry consumed outside callable emission.
        self._func_collection_types: dict[str, dict[str, TypeSpec]] = {}
        # id(immediate branch/loop owner) -> raw local name -> TypeSpec.
        # A callable may legally reuse one raw name for unrelated collections
        # in sibling lexical blocks; keeping those bindings in the flat
        # callable inventory would make the last analyzed branch win.
        self._block_collection_types: dict[int, dict[str, TypeSpec | None]] = {}
        self._block_collection_owners: dict[int, str] = {}
        # id(callable-local VarDecl) -> its exact collection TypeSpec, or None
        # for a scalar/UDT tombstone. Codegen activates these in source order
        # after emitting each declaration RHS.
        self._callable_collection_bindings: dict[int, TypeSpec | None] = {}
        self._callable_collection_binding_owners: dict[int, str] = {}
        self._collection_scope_stack: list[str] = []
        self._udt_field_type_specs: dict[str, dict[str, TypeSpec]] = {}
        # Enum definitions: enum_name -> list of member names
        self._enum_defs: dict[str, list[str]] = {}
        self._enum_member_strings: dict[str, list[str]] = {}
        # request.security rebinding helpers for mutable globals
        self._global_binding_infos: dict[str, MutableGlobalInfo] = {}
        self._global_reassigned_names: set[str] = set()
        self._current_top_level_stmt: ASTNode | None = None
        self._global_scope = True
        self._static_vars: set[str] = set()
        # Stack of enclosing user-function param-name sets, pushed while visiting
        # a FuncDef body. Lets a nested user-func call detect when it substitutes
        # a TA ctor length with one of the OUTER function's params, so the outer
        # call site can re-substitute (e.g. f_bbwp(_bbwLen) -> f_basisMa(_len)).
        self._enclosing_func_params: list[set[str]] = []
        # Parallel stack of the function NAMES whose param-sets are in
        # ``_enclosing_func_params``. The top of stack (or None at global
        # scope) is the owner of any ORIGINAL ``ta.*`` site minted right now
        # -- recorded on ``TACallSite.owner_func`` so the codegen dead-code
        # pass can tell borrowed clones apart from a dead function's own
        # sites (see contracts.TACallSite.owner_func).
        self._enclosing_func_names: list[str] = []
        # Exact TA targets borrowed through nested callable edges while visiting
        # the current callable body. Constructor templates are tracked
        # separately in ``_func_ta_ctor_args`` because state ownership also
        # matters for parameterless helpers such as ``ta.change``. ``None``
        # means no callable body is active.
        self._nested_ta_touched: set | None = None

        # Pre-populate builtins
        self._populate_builtins()

    def _populate_builtins(self) -> None:
        """Add built-in PineScript variables to the global scope."""
        dummy_loc = SourceLocation(file=self._filename, line=0, col=0, end_col=0)
        for name, ptype in BUILTIN_VARS.items():
            sym = Symbol(
                name=name,
                pine_type=ptype,
                is_series=name in BAR_FIELDS,
                is_var=False,
                is_const=False,
                const_value=None,
                scope="global",
                loc=dummy_loc,
            )
            self._symbols.define(sym)

    def _ensure_pine_v6(self) -> None:
        """PineForge implements PineScript v6 only; reject other versions or missing directive."""
        if self._ast.version is None:
            loc = SourceLocation(file=self._filename, line=1, col=1, end_col=1)
            raise CompileError(
                [
                    Diagnostic(
                        level=Level.ERROR,
                        phase=Phase.ANALYZER,
                        location=loc,
                        message=(
                            "Missing PineScript version directive. "
                            "PineForge supports PineScript v6 only."
                        ),
                        hint='Add //@version=6 as the first line of your script.',
                    )
                ]
            )
        if self._ast.version != 6:
            loc = SourceLocation(file=self._filename, line=1, col=1, end_col=1)
            raise CompileError(
                [
                    Diagnostic(
                        level=Level.ERROR,
                        phase=Phase.ANALYZER,
                        location=loc,
                        message=(
                            f"PineForge supports PineScript v6 only (found //@version={self._ast.version})."
                        ),
                        hint="Migrate the script to //@version=6 using TradingView's editor.",
                    )
                ]
            )

    def _check_direct_terminal_array_element_callee_shadows(self) -> None:
        """Reject terminal temporary reads whose element call is shadowed.

        This is a whole-program syntactic preflight: it runs before the first
        AST visit so definition order cannot leave partial function, call-site,
        or TA state behind. Only bindings whose lexical lifetime reaches the
        read are relevant: FuncDef parameters and declarations in the immediate
        function body before the direct terminal or adjacent alias initializer.
        The alias declaration is not active on its own RHS. Nested block locals
        have expired and must not shadow the global UDF at either read point.
        """
        for func_def in self._ast.body:
            if not isinstance(func_def, FuncDef):
                continue
            terminal = self._direct_terminal_return_expr(func_def)
            element_call: FuncCall | None = None
            read_index: int | None = None
            if self._terminal_array_get_uses_direct_temporary(terminal):
                element_call = (
                    self._direct_terminal_array_temporary_element_call(
                        terminal
                    )
                )
                read_index = len(func_def.body) - 1
            else:
                alias_candidate = (
                    self._direct_terminal_array_temporary_alias_candidate(
                        func_def, terminal
                    )
                )
                if alias_candidate is not None:
                    read_index, _, element_call = alias_candidate
            if element_call is None or read_index is None:
                continue

            bindings = set(func_def.params)
            for statement in func_def.body[:read_index]:
                if isinstance(statement, VarDecl):
                    bindings.add(statement.name)
                elif isinstance(statement, TupleAssign):
                    bindings.update(
                        name for name in statement.names if name != "_"
                    )
            if element_call.callee.name not in bindings:
                continue
            self._error(
                "Direct temporary-array element call "
                f"'{element_call.callee.name}()' resolves to a local or "
                "parameter, not a user-defined function.",
                element_call.loc,
            )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def analyze(self) -> AnalyzerContext:
        """Run semantic analysis and return the analyzer context."""
        self._ensure_pine_v6()
        self._check_direct_terminal_array_element_callee_shadows()
        self._visit(self._ast)
        self._check_direct_terminal_array_temporary_cycles()
        self._register_resolved_direct_terminal_array_forward_calls()
        self._refresh_direct_terminal_array_temporary_returns()
        self._check_forward_tuple_helper_wrappers()
        self._check_top_level_block_shadow_boundaries()
        self._qualify_colliding_func_var_members()
        self._check_cross_callable_series_collection_collisions()
        self._check_declaration_exact_series_storage_boundaries()
        self._check_persistent_drawing_member_collisions()

        # Propagate call-site counts to sub-functions called within
        # multi-call-site functions. If f() has N call sites and calls g()
        # internally, g() also needs N call-site variants so each f_csK
        # can call g_csK with isolated state.
        self._propagate_call_site_counts()

        # Reject (loudly) a request.security whose timeframe is a UDF parameter
        # called with multiple distinct literal timeframes — a single evaluator
        # cannot serve them and per-callsite specialization is not yet wired.
        self._check_mixed_callsite_security_tf()

        # Keep only truly pure global expressions for request.security rebinding.
        # Globals later reassigned with := become series/stateful variables and
        # must not be rebound to their declaration-time initializer.
        for name, info in self._global_binding_infos.items():
            info.is_series = name in self._series_vars

        mutable_global_infos = {
            name: info
            for name, info in self._global_binding_infos.items()
            if info.is_var or name in self._global_reassigned_names
        }

        pure_global_expr_map = {
            k: v for k, v in self._global_expr_map.items() if k not in mutable_global_infos
        }

        return AnalyzerContext(
            ast=self._ast,
            symbols=self._symbols,
            ta_call_sites=self._ta_call_sites,
            series_vars=self._series_vars,
            series_var_members=self._series_var_members,
            series_decl_nodes=self._series_decl_nodes,
            series_decl_bindings=self._series_decl_bindings,
            series_bar_fields=self._series_bar_fields,
            var_members=self._var_members,
            func_infos=self._func_infos,
            fixnan_sites=self._fixnan_sites,
            func_fixnan_indices=self._func_fixnan_indices,
            func_cs_fixnan_clone_names=self._func_cs_fixnan_clone_names,
            strategy_params=self._strategy_params,
            diagnostics=self._diagnostics,
            filename=self._filename,
            global_var_decls=self._global_var_decls,
            global_expr_map=pure_global_expr_map,
            identifier_binding_scopes=dict(self._identifier_binding_scopes),
            var_member_init_exprs=self._var_member_init_exprs,
            var_member_metadata_by_node=self._var_member_metadata_by_node,
            var_member_type_specs_by_node=self._var_member_type_specs_by_node,
            var_member_owners_by_node=self._var_member_owners_by_node,
            func_ta_ranges=self._func_ta_ranges,
            func_ta_indices=self._func_ta_indices,
            func_call_cs_map=self._func_call_cs_map,
            func_inherited_call_names=dict(
                self._func_inherited_call_names
            ),
            func_call_site_counts=self._func_call_site_count,
            func_callsite_param_types={
                key: tuple(types)
                for key, types in self._func_callsite_param_types.items()
            },
            func_callsite_return_types=dict(
                self._func_callsite_return_types
            ),
            func_declared_param_type_specs={
                name: tuple(specs)
                for name, specs in self._func_param_type_specs.items()
            },
            func_security_clone_only=self._func_security_clone_only,
            func_cs_ta_clone_names=self._func_cs_ta_clone_names,
            udt_defs=self._udt_fields,
            enum_defs=self._enum_defs,
            enum_member_strings=self._enum_member_strings,
            security_calls=getattr(self, "_security_calls", []),
            global_mutable_infos=mutable_global_infos,
            ordinary_global_binding_names=set(
                self._ordinary_global_binding_names
            ),
            ordinary_global_series_names=set(
                self._ordinary_global_series_names
            ),
            func_var_members=self._func_var_members,
            func_var_storage_names={
                owner: dict(names)
                for owner, names in self._func_var_storage_names.items()
            },
            func_series_vars=self._func_series_vars,
            nonpersistent_series_decl_names=set(
                self._nonpersistent_series_decl_names
            ),
            func_nonpersistent_series_vars={
                owner: set(names)
                for owner, names in (
                    self._func_nonpersistent_series_vars.items()
                )
            },
            func_return_type_specs=dict(self._func_return_type_specs),
            udt_var_types=dict(self._udt_var_types),
            collection_types=dict(self._collection_types),
            func_collection_types={
                name: dict(specs)
                for name, specs in self._func_collection_types.items()
            },
            block_collection_types={
                owner_id: dict(specs)
                for owner_id, specs in self._block_collection_types.items()
            },
            block_collection_owners=dict(self._block_collection_owners),
            callable_collection_bindings=dict(
                self._callable_collection_bindings
            ),
            callable_collection_binding_owners=dict(
                self._callable_collection_binding_owners
            ),
            udt_field_type_specs=dict(self._udt_field_type_specs),
            block_var_renames=dict(self._block_var_renames),
        )

    def _check_forward_tuple_helper_wrappers(self) -> None:
        """Fail before codegen on a tuple wrapper calling a later definition.

        Direct terminal tuple metadata can be propagated after the callee is
        analyzed, but the earlier wrapper body did not register that callee's
        ordinary FuncInfo/call graph. Emitting it would therefore reach an
        unknown-function error in codegen. Keep the boundary explicit until
        forward callable registration itself is implemented.
        """
        positions = {
            stmt.name: index
            for index, stmt in enumerate(self._ast.body)
            if isinstance(stmt, FuncDef)
        }
        for wrapper_name, wrapper_def in self._func_defs.items():
            terminal = self._direct_terminal_return_expr(wrapper_def)
            if not (
                isinstance(terminal, FuncCall)
                and isinstance(terminal.callee, Identifier)
            ):
                continue
            callee_name = terminal.callee.name
            if not self._func_returns_tuple.get(callee_name, False):
                continue
            if positions.get(callee_name, -1) <= positions.get(wrapper_name, -1):
                continue
            self._error(
                "Tuple-return helper wrapper "
                f"'{wrapper_name}' calls later-defined helper '{callee_name}'; "
                "forward tuple-wrapper calls are not supported yet. "
                f"Move '{callee_name}' above '{wrapper_name}' before passing "
                "the wrapper to request.security().",
                terminal.loc,
            )

    def _refresh_direct_terminal_array_temporary_returns(self) -> None:
        """Reconcile exact primitive returns without revisiting call sites.

        ``array.from(later_defined_udf()).get(0)`` can be visited before the
        producer's return type exists.  The ordinary fallback is then float,
        while final codegen correctly learns the producer's primitive type.
        Re-run only a preregistered, direct built-in temporary shape using the
        cached structural resolver.  It never calls ``_visit`` and therefore
        cannot mint phantom TA/series/fixnan call-site state.
        """
        pending = self._direct_terminal_array_temporary_exprs
        if not pending:
            return

        # One new exact result can unlock one earlier direct dependency per
        # pass.  Cycles retain their existing fail-closed fallback.
        for _ in range(len(pending) + 1):
            changed = False
            for name, terminal in pending.items():
                spec = self._cached_terminal_temporary_array_get_spec(terminal)
                if spec is None or spec.kind != "primitive":
                    continue
                pine_type = self._element_pine_type(spec)
                if pine_type in (PineType.UNKNOWN, PineType.VOID):
                    continue
                if (
                    self._func_return_types.get(name) == pine_type
                    and self._func_return_type_specs.get(name) == spec
                ):
                    continue
                self._func_return_types[name] = pine_type
                self._func_return_type_specs[name] = spec
                symbol = self._symbols.resolve(name)
                if symbol is not None:
                    symbol.pine_type = pine_type
                    symbol.type_spec = spec
                for func_info in self._func_infos:
                    if func_info.name == name:
                        func_info.return_type = pine_type
                        func_info.return_type_spec = spec
                changed = True
            if not changed:
                break

    def _direct_terminal_array_temporary_return_expr(
        self,
        func_def: FuncDef,
        terminal: ASTNode | None,
    ) -> ASTNode | None:
        """Return the exact temporary read that determines a UDF return.

        The established path accepts a direct terminal ``array.get``.  A
        single ordinary local may also carry that same value to a bare
        terminal identity return::

            reader() =>
                value = array.from(later()).get(0)
                value

        Capture only that adjacent one-hop lexical shape while the function
        scope is still live.  Deferred reconciliation can then reuse the
        initializer AST without re-visiting it after ``later`` is known.
        Persistent/typed locals, alias chains, intervening statements, block
        bindings, argument-bearing element calls, and every non-direct
        producer stay outside this path.
        """
        if self._terminal_array_get_uses_direct_temporary(terminal):
            return terminal
        alias_candidate = self._direct_terminal_array_temporary_alias_candidate(
            func_def, terminal
        )
        if alias_candidate is None:
            return None
        _, declaration, element_call = alias_candidate

        # Raw-name lookup is safe only because the exact declaration identity
        # was attached to its live lexical Symbol by _visit_VarDecl.
        symbol = self._symbols.resolve(terminal.name)
        if (
            symbol is None
            or getattr(symbol, "_pf_decl_node_id", None) != id(declaration)
        ):
            return None

        known_definition = self._func_defs.get(element_call.callee.name)
        if known_definition is not None and known_definition.params:
            return None
        return declaration.value

    def _direct_terminal_array_temporary_alias_candidate(
        self,
        func_def: FuncDef,
        terminal: ASTNode | None,
    ) -> tuple[int, VarDecl, FuncCall] | None:
        """Return one exact adjacent identity alias and its element call.

        This helper is deliberately syntactic so the same program point can be
        checked before any AST visit and captured later while declaration
        identity is live. The declaration itself is therefore excluded from
        the caller's active-binding scan: Pine evaluates its initializer before
        introducing the new local name.
        """
        if not isinstance(terminal, Identifier):
            return None

        declarations = [
            (index, stmt)
            for index, stmt in enumerate(func_def.body[:-1])
            if isinstance(stmt, VarDecl) and stmt.name == terminal.name
        ]
        if len(declarations) != 1:
            return None
        declaration_index, declaration = declarations[0]
        if (
            declaration.is_var
            or declaration.is_varip
            or declaration.type_hint is not None
            or declaration_index != len(func_def.body) - 2
        ):
            return None

        initializer = declaration.value
        if not self._terminal_array_get_uses_direct_temporary(initializer):
            return None
        element_call = self._direct_terminal_array_temporary_element_call(
            initializer
        )
        if (
            element_call is None
            or element_call.args
            or element_call.kwargs
        ):
            return None
        return declaration_index, declaration, element_call

    @classmethod
    def _direct_terminal_array_temporary_element_call(
        cls,
        terminal: ASTNode | None,
    ) -> FuncCall | None:
        """Return a direct UDF element call from the registered shape."""
        if not isinstance(terminal, FuncCall) or not isinstance(
            terminal.callee, MemberAccess
        ):
            return None
        callee = terminal.callee
        if callee.member != "get":
            return None
        if isinstance(callee.object, Identifier) and callee.object.name == "array":
            receiver = (
                terminal.args[0]
                if terminal.args
                else terminal.kwargs.get("id")
            )
        else:
            receiver = callee.object

        while True:
            copy_source = cls._direct_namespace_array_copy_source(receiver)
            if copy_source is not None:
                receiver = copy_source
                continue
            if (
                isinstance(receiver, FuncCall)
                and isinstance(receiver.callee, MemberAccess)
                and receiver.callee.member == "copy"
                and not receiver.args
                and not receiver.kwargs
            ):
                receiver = receiver.callee.object
                continue
            break
        if not (
            isinstance(receiver, FuncCall)
            and isinstance(receiver.callee, MemberAccess)
            and isinstance(receiver.callee.object, Identifier)
            and receiver.callee.object.name == "array"
            and receiver.callee.member == "from"
            and receiver.args
        ):
            return None
        element = receiver.args[0]
        if (
            isinstance(element, FuncCall)
            and isinstance(element.callee, Identifier)
        ):
            return element
        return None

    @classmethod
    def _direct_terminal_array_temporary_user_call(
        cls,
        terminal: ASTNode | None,
    ) -> FuncCall | None:
        """Return a forward-registerable namespace-functional element call."""
        if not (
            isinstance(terminal, FuncCall)
            and isinstance(terminal.callee, MemberAccess)
            and isinstance(terminal.callee.object, Identifier)
            and terminal.callee.object.name == "array"
        ):
            return None
        element = cls._direct_terminal_array_temporary_element_call(terminal)
        if element is not None and not element.args and not element.kwargs:
            return element
        return None

    def _register_resolved_direct_terminal_array_forward_calls(self) -> None:
        """Register a formerly forward zero-argument element call once.

        The initial lexical visit cannot dispatch a later-defined UDF.  Once
        its definition exists, ordinary call handling can safely register the
        call because this deliberately bounded shape has no arguments whose
        lexical bindings could have gone out of scope.
        """
        for terminal in self._direct_terminal_array_temporary_exprs.values():
            call = self._direct_terminal_array_temporary_user_call(terminal)
            if call is None:
                continue
            name = call.callee.name
            if name not in self._func_defs:
                continue
            # Regular UDF default arguments are not emitted in C++ today.
            # A syntactically empty call is forward-registerable only when the
            # declaration itself is genuinely zero-parameter.
            if self._func_defs[name].params:
                continue
            stateful = (
                name in self._func_ta_ranges
                or name in self._func_series_vars
                or name in self._func_var_members
                or name in self._func_fixnan_indices
            )
            if stateful and id(call) in self._func_call_cs_map:
                continue
            if not stateful and any(
                func_info.name == name for func_info in self._func_infos
            ):
                continue
            self._handle_user_func_call(name, call)

    def _check_direct_terminal_array_temporary_cycles(self) -> None:
        """Reject recursion reached through a temporary-reader UDF edge.

        Pine forbids recursive UDF execution.  Forward registration makes a
        formerly unknown helper visible, so preserve the language boundary
        explicitly instead of generating a C++ recursion that only fails at
        runtime.
        """
        wrappers = self._direct_terminal_array_temporary_exprs
        known = set(self._func_defs)

        def callees(node: Any, seen: set[int] | None = None) -> set[str]:
            if node is None:
                return set()
            if seen is None:
                seen = set()
            if isinstance(node, (list, tuple, dict)) or hasattr(node, "__dict__"):
                node_id = id(node)
                if node_id in seen:
                    return set()
                seen.add(node_id)
            if isinstance(node, (list, tuple)):
                return set().union(*(callees(item, seen) for item in node))
            if isinstance(node, dict):
                return set().union(*(callees(item, seen) for item in node.values()))
            if not hasattr(node, "__dict__"):
                return set()
            found: set[str] = set()
            if (
                isinstance(node, FuncCall)
                and isinstance(node.callee, Identifier)
                and node.callee.name in known
            ):
                found.add(node.callee.name)
            for value in vars(node).values():
                found.update(callees(value, seen))
            return found

        graph = {
            name: callees(func_def.body)
            for name, func_def in self._func_defs.items()
        }

        def path_to(start: str, target: str) -> list[str] | None:
            stack: list[tuple[str, list[str]]] = [(start, [start])]
            visited: set[str] = set()
            while stack:
                name, path = stack.pop()
                if name == target:
                    return path
                if name in visited:
                    continue
                visited.add(name)
                for child in sorted(graph.get(name, ()), reverse=True):
                    stack.append((child, [*path, child]))
            return None

        for owner, terminal in wrappers.items():
            element = self._direct_terminal_array_temporary_element_call(
                terminal
            )
            if element is None or element.callee.name not in known:
                continue
            return_path = path_to(element.callee.name, owner)
            if return_path is None:
                continue
            cycle = [owner, *return_path]
            self._error(
                "Recursive direct temporary-array reader cycle is not "
                f"supported: {' -> '.join(cycle)}.",
                element.loc,
            )

    def _qualify_colliding_func_var_members(self) -> None:
        """Give colliding ordinary-UDF primitive/collection vars distinct storage.

        Pine function locals are lexical, but persistent locals are lowered to
        generated class members.  Historically the direct FuncDef path used the
        raw Pine spelling as that member identity, so two functions declaring
        ``var float state`` shared both the member and the raw-name-keyed
        initializer cache.  Separate init flags did not help: both flags still
        guarded writes into one member, and the later definition's initializer
        won for *both* functions.

        Keep this first migration deliberately bounded:

        * direct persistent declarations in ordinary ``FuncDef`` bodies only;
        * only groups whose exact types are primitive or a supported collection;
        * only when its member collides with another persistent/global binding;
        * no output change for a non-colliding source.

        ``func_var_members`` deliberately retains raw lexical names.  The new
        ``func_var_storage_names`` overlay lets codegen clone exact members and
        activate raw->storage remaps at each declaration site without making a
        future local shadow earlier global reads.
        """
        direct_node_ids_by_owner = {
            owner: {
                id(stmt)
                for stmt in func_def.body
                if isinstance(stmt, VarDecl)
                and (stmt.is_var or stmt.is_varip)
            }
            for owner, func_def in self._func_defs.items()
        }

        bindings_by_member: dict[str, list[int]] = {}
        for node_id, meta in self._var_member_metadata_by_node.items():
            bindings_by_member.setdefault(meta[1], []).append(node_id)
        ordinary_global_members = set(self._ordinary_global_binding_names)
        nonpersistent_series_raw_names = {
            name
            for node_id, name in self._series_decl_bindings
            if node_id not in self._var_member_metadata_by_node
        }

        qualifiable_bindings: list[tuple[int, str, VarDecl]] = []
        for node_id, meta in self._var_member_metadata_by_node.items():
            node, _member_name, _ptype, _init, is_callable_scoped = meta
            owner = self._var_member_owners_by_node.get(node_id)
            if (not is_callable_scoped
                    or owner not in self._func_defs
                    or node_id not in direct_node_ids_by_owner.get(owner, set())
                    or not isinstance(node, VarDecl)):
                continue
            self._func_var_storage_names.setdefault(owner, {})[node.name] = (
                meta[1]
            )
            spec = self._var_member_type_specs_by_node.get(node_id)
            reserves_raw_series = (
                meta[1] in nonpersistent_series_raw_names
                # Preserve the established, more specific scalar-Series vs
                # persistent-map fail-closed diagnostic. Primitive/array/
                # matrix storage can still move out of the raw Series name.
                and (spec is None or spec.kind != "map")
            )

            # Only a storage identity that is currently shared needs
            # migration.  This includes global-vs-UDF and method-vs-UDF
            # collisions, plus a declaration-bound non-persistent Series that
            # needs to retain the raw member spelling, not just two ordinary
            # UDF owners.
            if (len(bindings_by_member.get(meta[1], [])) < 2
                    and meta[1] not in ordinary_global_members
                    and not reserves_raw_series):
                continue

            # The current overlay is owner/raw keyed.  If this owner has two
            # declaration nodes already sharing the member (for example a
            # direct local plus a first nested-block shadow), it cannot route
            # them independently.  Leave both untouched so the exact-member
            # collision diagnostic below fails closed instead of pretending
            # the owner was repaired while silently targeting the outer var.
            same_owner_nodes = [
                other_id
                for other_id in bindings_by_member[meta[1]]
                if self._var_member_owners_by_node.get(other_id) == owner
            ]
            if len(same_owner_nodes) > 1:
                continue

            if (spec is not None
                    and spec.kind in {
                        "primitive", "array", "map", "matrix",
                    }):
                qualifiable_bindings.append((node_id, owner, node))

        # Protect every existing user/class identity, including names declared
        # later in source.  The deterministic allocator advances its leading
        # token when the user already occupies a base or derived clone spelling.
        used_names = {
            name for name, _ptype, _init in self._var_members
        }
        used_names.update(name for name, _ptype in self._global_var_decls)
        used_names.update(self._series_vars)
        used_names.update(self._func_defs)

        # Generated members are referenced as bare identifiers inside emitted
        # methods.  Protect against every authored identifier, not only class
        # members: a parameter/plain local/loop binder named like the helper
        # token (or one of its ``_csN`` / ``__niN`` clones) would otherwise
        # shadow the intended member in C++.
        authored_names: set[str] = set()

        def collect_authored_names(value: Any) -> None:
            if isinstance(value, Identifier):
                authored_names.add(value.name)
            if isinstance(value, VarDecl):
                authored_names.add(value.name)
            elif isinstance(value, TupleAssign):
                authored_names.update(
                    name for name in value.names if name != "_"
                )
            elif isinstance(value, ForStmt):
                if value.var:
                    authored_names.add(value.var)
            elif isinstance(value, ForInStmt):
                if value.var:
                    authored_names.add(value.var)
                authored_names.update(
                    name for name in (value.vars or []) if name != "_"
                )
            elif isinstance(value, (FuncDef, MethodDef)):
                authored_names.add(value.name)
                authored_names.update(value.params)
            if isinstance(value, ASTNode):
                for child in vars(value).values():
                    collect_authored_names(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    collect_authored_names(child)
            elif isinstance(value, dict):
                for child in value.values():
                    collect_authored_names(child)

        collect_authored_names(self._ast)
        used_names.update(authored_names)

        allocated_storage_names: set[str] = set()

        def generated_namespace_overlaps(candidate: str, other: str) -> bool:
            """Whether either base can be emitted as the other's clone.

            Stateful call-site and nested-instance storage is derived by
            appending ``_csN`` / ``__niN`` to the persistent member base.  A
            distinct owner can legitimately make one of those suffixes part
            of its authored function name (for example ``left`` and
            ``left_cs1``), so exact-name reservation alone is insufficient.
            Reserve the derived namespaces symmetrically, independent of
            source-definition order.
            """
            return any(
                candidate.startswith(f"{other}{suffix}")
                or other.startswith(f"{candidate}{suffix}")
                for suffix in ("_cs", "__ni")
            )

        def helper_name_conflicts(candidate: str) -> bool:
            return (
                candidate in used_names
                or any(
                    generated_namespace_overlaps(candidate, name)
                    for name in used_names
                )
                or any(
                    generated_namespace_overlaps(candidate, allocated)
                    for allocated in allocated_storage_names
                )
            )

        storage_sequence = 0
        for node_id, owner, node in qualifiable_bindings:
            # Start in a codegen-reserved namespace so an adversarial but
            # valid raw name such as ``_fvinit_left`` cannot equal a
            # generated function-init flag.  Put a monotonically allocated
            # token before every authored component: appending ``_2`` to a
            # clone-shaped base (``left_cs1``) would remain inside the
            # earlier base's ``_cs*`` namespace forever.  Advancing this
            # leading token instead always reaches a disjoint namespace.
            # Non-colliding sources never enter this path and therefore
            # retain byte-identical output.
            while True:
                storage_sequence += 1
                member_name = (
                    f"_pfv_{storage_sequence}_{node.name}__{owner}"
                )
                if not helper_name_conflicts(member_name):
                    break
            used_names.add(member_name)
            allocated_storage_names.add(member_name)

            old = self._var_member_metadata_by_node[node_id]
            self._var_member_metadata_by_node[node_id] = (
                old[0], member_name, old[2], old[3], old[4],
            )
            self._func_var_storage_names[owner][node.name] = member_name

        qualified_raw_names = {
            raw_name
            for storage_names in self._func_var_storage_names.values()
            for raw_name, storage_name in storage_names.items()
            if storage_name != raw_name
        }
        exact_series_bindings = set(self._series_decl_bindings)
        exact_series_node_ids = {
            node_id for node_id, _name in exact_series_bindings
        }
        for name, (node_id, ptype, expr) in (
            self._ordinary_global_binding_info.items()
        ):
            is_exact_series = (
                (node_id, name) in exact_series_bindings
                or (
                    node_id in self._series_decl_nodes
                    and node_id not in exact_series_node_ids
                )
            )
            if is_exact_series:
                self._ordinary_global_series_names.add(name)
                continue
            if name not in qualified_raw_names:
                continue
            # A same-named callable history binding can suppress this scalar
            # or collection from the legacy raw ``global_var_decls`` list.
            # Restore only collision-qualified globals from their exact
            # declaration record; all unrelated generated output stays stable.
            if not any(existing == name for existing, _ in self._global_var_decls):
                self._global_var_decls.append((name, ptype))
            if expr is not None:
                self._global_expr_map[name] = expr

        # Rebuild the two flat, member-keyed inventories from the exact
        # declaration metadata.  Dict insertion order is source-analysis order;
        # unresolved out-of-scope raw collisions retain their established
        # last-initializer-wins behavior rather than being silently broadened.
        self._var_members = []
        self._var_member_init_exprs = {}
        for _node_id, meta in self._var_member_metadata_by_node.items():
            node, member_name, ptype, init_str, _callable = meta
            self._var_members.append((member_name, ptype, init_str))
            if node.value is not None:
                self._var_member_init_exprs[member_name] = node.value

        # History tracking is declaration-exact.  Replace only the persistent
        # member portion of the set so renamed Series members (and later their
        # call-site clones) keep the right storage type.
        old_persistent_members = {
            meta[1] for meta in self._var_member_metadata_by_node.values()
        }
        # The set above contains the post-rename names; include pre-rename raw
        # spellings from every declaration to remove stale entries as well.
        old_persistent_members.update(
            meta[0].name for meta in self._var_member_metadata_by_node.values()
        )
        nonpersistent_series = (
            self._series_var_members - old_persistent_members
        )
        exact_persistent_series = {
            self._var_member_metadata_by_node[node_id][1]
            for node_id in self._series_decl_nodes
            if node_id in self._var_member_metadata_by_node
        }
        self._series_var_members = (
            nonpersistent_series | exact_persistent_series
        )

        # Any exact member identity still owned by multiple persistent
        # declarations is outside the owner/raw overlay's safe scope (for
        # example two UDT methods, or a direct UDF var plus its first
        # same-named nested-block shadow).  Do not retain the historical
        # last-initializer-wins behavior: fail closed until that declaration
        # shape has declaration-exact codegen remapping.  Drawing collisions
        # keep their more specific diagnostic in the following check.
        from .types import _DRAWING_TYPE_NAMES

        remaining_by_member: dict[str, list[int]] = {}
        for node_id, meta in self._var_member_metadata_by_node.items():
            remaining_by_member.setdefault(meta[1], []).append(node_id)

        for member_name, node_ids in remaining_by_member.items():
            binding_count = len(node_ids) + int(
                member_name in ordinary_global_members
            )
            if binding_count < 2:
                continue
            specs = [
                self._var_member_type_specs_by_node.get(node_id)
                for node_id in node_ids
            ]
            if any(
                spec is not None
                and spec.kind == "udt"
                and spec.name in _DRAWING_TYPE_NAMES
                for spec in specs
            ):
                continue
            # An ordinary global contributes to ``binding_count`` without a
            # persistent declaration-node entry, so a single unsupported
            # persistent binding can still reach this diagnostic.
            node = self._var_member_metadata_by_node[node_ids[-1]][0]
            raw_name = getattr(node, "name", member_name)
            self._error(
                "Persistent bindings named "
                f"'{raw_name}' still share generated storage across distinct "
                "lexical declarations; this declaration shape is not "
                "supported yet.",
                node.loc,
            )

    def _check_declaration_exact_series_storage_boundaries(self) -> None:
        """Reject raw Series identities that still cross lexical owners.

        Non-persistent history locals are currently emitted as class-level
        ``Series`` members under their raw Pine spelling.  That is safe for a
        single declaration, but the raw member is shared (or suppressed by a
        persistent member) when the spelling is reused by another lexical
        declaration.  Parameters are intentionally absent from
        ``series_decl_bindings`` and remain legal: their history buffers are
        routed by the existing callable parameter path.

        Keep this boundary declaration-exact and fail closed until ordinary
        local Series storage has the same owner-qualified overlay as persistent
        callable state.  Direct script Series declarations remain supported,
        including a same-named persistent UDF member that was qualified above.
        """
        exact_series = set(self._series_decl_bindings)
        if not exact_series:
            return

        # A helper reached exclusively through a request.security expression
        # does not use the ordinary raw class Series member: the security
        # emitter keys its local history by evaluator/function/source identity.
        # Preserve that proven isolation (including transitive helper calls)
        # while keeping any helper also reachable on the chart path subject to
        # the ordinary raw-storage collision checks below.
        known_func_names = set(self._func_defs)
        security_expression_ids = {
            id(sec.expression)
            for sec in getattr(self, "_security_calls", []) or []
            if getattr(sec, "expression", None) is not None
        }
        ordinary_roots: set[str] = set()
        security_roots: set[str] = set()
        call_edges: dict[str, set[str]] = {}

        def scan_calls(
            value: Any,
            owner: str | None,
            in_security: bool = False,
        ) -> None:
            if value is None:
                return
            in_security = in_security or id(value) in security_expression_ids
            if isinstance(value, (FuncDef, MethodDef)):
                return
            if isinstance(value, FuncCall):
                callee = value.callee
                if (isinstance(callee, Identifier)
                        and callee.name in known_func_names):
                    if in_security:
                        security_roots.add(callee.name)
                    elif owner is None:
                        ordinary_roots.add(callee.name)
                    else:
                        call_edges.setdefault(owner, set()).add(callee.name)
            if isinstance(value, (list, tuple)):
                for item in value:
                    scan_calls(item, owner, in_security)
                return
            if isinstance(value, dict):
                for item in value.values():
                    scan_calls(item, owner, in_security)
                return
            if isinstance(value, ASTNode):
                for child in vars(value).values():
                    scan_calls(child, owner, in_security)

        scan_calls(self._ast.body, None)
        for owner, func_def in self._func_defs.items():
            scan_calls(func_def.body, owner)

        def reachable(roots: set[str]) -> set[str]:
            found = set(roots)
            pending = list(roots)
            while pending:
                owner = pending.pop()
                for callee in call_edges.get(owner, set()):
                    if callee in found:
                        continue
                    found.add(callee)
                    pending.append(callee)
            return found

        security_evaluator_only = (
            reachable(security_roots) - reachable(ordinary_roots)
        )

        self._nonpersistent_series_decl_names = {
            name
            for node_id, name in exact_series
            if node_id not in self._var_member_metadata_by_node
        }
        self._func_nonpersistent_series_vars = {}

        persistent_raw_names = {
            meta[0].name
            for meta in self._var_member_metadata_by_node.values()
            if isinstance(meta[0], VarDecl)
        }
        unqualified_persistent_raw_names = {
            meta[0].name
            for meta in self._var_member_metadata_by_node.values()
            if (isinstance(meta[0], VarDecl)
                and meta[1] == meta[0].name)
        }
        callable_nonpersistent: dict[
            str, list[tuple[str, ASTNode]]
        ] = {}
        block_nonpersistent: dict[str, list[ASTNode]] = {}

        def record_bindings(
            stmt: ASTNode,
            names: list[str],
            callable_owner: str | None,
            direct_program: bool,
        ) -> None:
            is_persistent = id(stmt) in self._var_member_metadata_by_node
            if is_persistent:
                return
            for name in names:
                if name == "_" or (id(stmt), name) not in exact_series:
                    continue
                if callable_owner is not None:
                    self._func_nonpersistent_series_vars.setdefault(
                        callable_owner, set()
                    ).add(name)
                    if (callable_owner in security_evaluator_only
                            and name not in self._ordinary_global_binding_names
                            and name not in unqualified_persistent_raw_names):
                        continue
                    callable_nonpersistent.setdefault(name, []).append(
                        (callable_owner, stmt)
                    )
                elif not direct_program:
                    block_nonpersistent.setdefault(name, []).append(stmt)

        def walk_embedded(
            value: Any,
            callable_owner: str | None,
        ) -> None:
            """Visit expression-valued if/switch branches as lexical blocks."""
            if value is None or isinstance(value, (FuncDef, MethodDef)):
                return
            if isinstance(value, IfStmt):
                walk_embedded(value.condition, callable_owner)
                walk_statements(value.body, callable_owner, False)
                walk_statements(
                    value.else_body or [], callable_owner, False
                )
                return
            if isinstance(value, SwitchStmt):
                walk_embedded(value.expr, callable_owner)
                for case_expr, body in value.cases:
                    walk_embedded(case_expr, callable_owner)
                    walk_statements(body, callable_owner, False)
                walk_statements(
                    value.default_body or [], callable_owner, False
                )
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    walk_embedded(item, callable_owner)
                return
            if isinstance(value, dict):
                for item in value.values():
                    walk_embedded(item, callable_owner)
                return
            if isinstance(value, ASTNode):
                for child in vars(value).values():
                    walk_embedded(child, callable_owner)

        def walk_statements(
            stmts: list[ASTNode],
            callable_owner: str | None,
            direct_program: bool,
        ) -> None:
            for stmt in stmts:
                if isinstance(stmt, FuncDef):
                    walk_statements(stmt.body, stmt.name, False)
                    continue
                # Method state still uses a separate legacy ownership path.
                # Do not misclassify a method-local declaration as a script
                # block while this guard is deliberately scoped to FuncDef.
                if isinstance(stmt, MethodDef):
                    continue
                if isinstance(stmt, VarDecl):
                    walk_embedded(stmt.value, callable_owner)
                    record_bindings(
                        stmt, [stmt.name], callable_owner, direct_program
                    )
                elif isinstance(stmt, TupleAssign):
                    walk_embedded(stmt.value, callable_owner)
                    record_bindings(
                        stmt, stmt.names, callable_owner, direct_program
                    )
                elif isinstance(stmt, Assignment):
                    walk_embedded(stmt.target, callable_owner)
                    walk_embedded(stmt.value, callable_owner)
                elif isinstance(stmt, ExprStmt):
                    walk_embedded(stmt.expr, callable_owner)

                if isinstance(stmt, IfStmt):
                    walk_embedded(stmt.condition, callable_owner)
                    walk_statements(stmt.body, callable_owner, False)
                    walk_statements(
                        stmt.else_body or [], callable_owner, False
                    )
                elif isinstance(stmt, ForStmt):
                    record_bindings(
                        stmt, [stmt.var], callable_owner, False
                    )
                    walk_embedded(stmt.start, callable_owner)
                    walk_embedded(stmt.end, callable_owner)
                    walk_embedded(stmt.step, callable_owner)
                    walk_statements(stmt.body, callable_owner, False)
                elif isinstance(stmt, ForInStmt):
                    record_bindings(
                        stmt,
                        ([stmt.var] if stmt.var else (stmt.vars or [])),
                        callable_owner,
                        False,
                    )
                    walk_embedded(stmt.iterable, callable_owner)
                    walk_statements(stmt.body, callable_owner, False)
                elif isinstance(stmt, WhileStmt):
                    walk_embedded(stmt.condition, callable_owner)
                    walk_statements(stmt.body, callable_owner, False)
                elif isinstance(stmt, SwitchStmt):
                    walk_embedded(stmt.expr, callable_owner)
                    for _case, body in stmt.cases:
                        walk_embedded(_case, callable_owner)
                        walk_statements(body, callable_owner, False)
                    walk_statements(
                        stmt.default_body or [], callable_owner, False
                    )

        walk_statements(self._ast.body, None, True)

        # A block-local history declaration is normally emitted as the raw
        # class Series member.  A persistent or callable-local claimant with
        # that spelling either suppresses the member or shares its buffer.
        for name, bindings in block_nonpersistent.items():
            if (name not in persistent_raw_names
                    and name not in callable_nonpersistent
                    and len(bindings) == 1):
                continue
            self._error(
                "A top-level block history binding named "
                f"'{name}' shares raw generated Series state with another "
                "lexical declaration; declaration-exact block storage is not "
                "supported yet.",
                getattr(bindings[-1], "loc", None),
            )

        # A direct ordinary global reserves its class-member spelling even when
        # it is scalar.  Persistent declarations reserve their raw spelling in
        # legacy var inventories even after exact owner qualification.  Either
        # conflicts with a raw non-persistent callable Series member.
        for name, bindings in callable_nonpersistent.items():
            if (name not in self._ordinary_global_binding_names
                    and name not in persistent_raw_names
                    and len(bindings) == 1):
                continue
            self._error(
                "A callable history local named "
                f"'{name}' shares raw generated Series state with another "
                "lexical declaration; declaration-exact callable storage is "
                "not supported yet.",
                getattr(bindings[-1][1], "loc", None),
            )

    def _check_cross_callable_series_collection_collisions(self) -> None:
        """Fail closed when legacy raw member names cannot preserve scoping.

        Persistent callable locals are class members.  A scalar Series local
        with the same spelling in another callable would otherwise bind to the
        collection member during C++ emission (``slot.push`` on PineMap).  Keep
        the valid source out of malformed C++ until callable-owned Series
        members have fully namespaced storage.
        """
        persistent_collections: dict[str, set[str]] = {}
        for owner, specs in self._func_collection_types.items():
            persistent_names = {
                item[0] for item in self._func_var_members.get(owner, [])
            }
            persistent_collections[owner] = {
                self._func_var_storage_names.get(owner, {}).get(name, name)
                for name, spec in specs.items()
                if name in persistent_names
                and self._type_spec_contains_map(spec)
            }

        emitted: set[tuple[str, str, str]] = set()
        for series_owner, names in self._func_series_vars.items():
            exact_series_names = {
                (
                    self._func_var_storage_names.get(
                        series_owner, {}
                    ).get(name, name)
                    if self._func_var_storage_names.get(
                        series_owner, {}
                    ).get(name, name) in self._series_var_members
                    else name
                )

                for name in names
            }
            for collection_owner, collection_names in persistent_collections.items():
                if series_owner == collection_owner:
                    continue
                for exact_name in exact_series_names & collection_names:
                    raw_name = next(
                        (
                            name for name in names
                            if self._func_var_storage_names.get(
                                series_owner, {}
                            ).get(name, name) == exact_name
                        ),
                        exact_name,
                    )
                    key = (series_owner, collection_owner, exact_name)
                    if key in emitted:
                        continue
                    emitted.add(key)
                    node = self._func_series_history_nodes.get(
                        (series_owner, raw_name)
                    )
                    self._error(
                        "History references on a scalar callable local named "
                        f"'{raw_name}' conflict with a persistent map local of the "
                        "same name in another callable; scoped Series member "
                        "storage is not implemented yet.",
                        node.loc if node is not None else None,
                    )

    def _check_top_level_block_shadow_boundaries(self) -> None:
        """Fail closed for block shadows whose storage is not node-exact yet.

        Same-typed primitive non-history declarations can use an ordinary C++
        lexical local once the analyzer preserves the outer symbol (see the
        scoped control-flow visitors below). Aggregate/UDT registries, local
        history Series storage, and cross-type codegen typing remain raw-name
        keyed, so allowing those shapes to shadow a direct script binding could
        silently retarget the class member or emit invalid C++.
        """
        def tuple_binding_type(
            stmt: TupleAssign, name: str
        ) -> tuple[PineType, TypeSpec]:
            try:
                index = stmt.names.index(name)
            except ValueError:
                index = -1
            element_types = self._tuple_element_types_by_node.get(
                id(stmt.value), ()
            )
            inferred = (
                element_types[index]
                if 0 <= index < len(element_types)
                else PineType.FLOAT
            )
            # Mirrors _visit_TupleAssign: bool is the only newly exact family;
            # all numeric elements retain the established float storage.
            pine_type = (
                PineType.BOOL if inferred == PineType.BOOL else PineType.FLOAT
            )
            return pine_type, TypeSpec.primitive(pine_type.value)

        direct: dict[str, tuple[PineType, TypeSpec | None]] = {}
        for stmt in self._ast.body:
            if isinstance(stmt, VarDecl):
                direct[stmt.name] = self._var_decl_types_by_node.get(
                    id(stmt), (PineType.UNKNOWN, None)
                )
            elif isinstance(stmt, TupleAssign):
                for name in stmt.names:
                    if name != "_":
                        direct[name] = tuple_binding_type(stmt, name)

        def nested_bindings(
            stmts: list[ASTNode], primitive_mismatch_unsafe: bool
        ):
            for stmt in stmts:
                if isinstance(stmt, VarDecl):
                    yield from embedded_bindings(stmt.value)
                    yield stmt, stmt.name, primitive_mismatch_unsafe
                    continue
                if isinstance(stmt, TupleAssign):
                    yield from embedded_bindings(stmt.value)
                    for name in stmt.names:
                        if name != "_":
                            yield stmt, name, primitive_mismatch_unsafe
                    continue
                if isinstance(stmt, IfStmt):
                    yield from embedded_bindings(stmt.condition)
                    yield from nested_bindings(stmt.body, True)
                    yield from nested_bindings(stmt.else_body or [], True)
                elif isinstance(stmt, (ForStmt, ForInStmt)):
                    if isinstance(stmt, ForStmt):
                        yield from embedded_bindings(stmt.start)
                        yield from embedded_bindings(stmt.end)
                        yield from embedded_bindings(stmt.step)
                    else:
                        yield from embedded_bindings(stmt.iterable)
                    yield from nested_bindings(stmt.body, True)
                elif isinstance(stmt, WhileStmt):
                    yield from embedded_bindings(stmt.condition)
                    yield from nested_bindings(stmt.body, True)
                elif isinstance(stmt, SwitchStmt):
                    yield from embedded_bindings(stmt.expr)
                    for _case, body in stmt.cases:
                        yield from embedded_bindings(_case)
                        yield from nested_bindings(body, True)
                    yield from nested_bindings(stmt.default_body or [], True)
                elif isinstance(stmt, Assignment):
                    yield from embedded_bindings(stmt.target)
                    yield from embedded_bindings(stmt.value)
                elif isinstance(stmt, ExprStmt):
                    yield from embedded_bindings(stmt.expr)

        def embedded_bindings(value: Any):
            """Yield declarations in expression-valued control-flow blocks."""
            if value is None or isinstance(value, (FuncDef, MethodDef)):
                return
            if isinstance(value, IfStmt):
                yield from embedded_bindings(value.condition)
                yield from nested_bindings(value.body, True)
                yield from nested_bindings(value.else_body or [], True)
                return
            if isinstance(value, SwitchStmt):
                yield from embedded_bindings(value.expr)
                for case_expr, body in value.cases:
                    yield from embedded_bindings(case_expr)
                    yield from nested_bindings(body, True)
                yield from nested_bindings(value.default_body or [], True)
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    yield from embedded_bindings(item)
                return
            if isinstance(value, dict):
                for item in value.values():
                    yield from embedded_bindings(item)
                return
            if isinstance(value, ASTNode):
                for child in vars(value).values():
                    yield from embedded_bindings(child)

        top_level_blocks: list[tuple[list[ASTNode], bool]] = []
        for stmt in self._ast.body:
            if isinstance(stmt, IfStmt):
                top_level_blocks.extend([
                    (stmt.body, True), (stmt.else_body or [], True)
                ])
            elif isinstance(stmt, (ForStmt, ForInStmt)):
                top_level_blocks.append((stmt.body, True))
            elif isinstance(stmt, WhileStmt):
                top_level_blocks.append((stmt.body, True))
            elif isinstance(stmt, SwitchStmt):
                top_level_blocks.extend(
                    (body, True) for _case, body in stmt.cases
                )
                top_level_blocks.append((stmt.default_body or [], True))

        # Pine control flow is also expression-valued (for example
        # ``result = if ...``).  Inspect those blocks without treating the
        # containing direct Program declaration itself as a nested binding.
        embedded_top_level_bindings: list[
            tuple[ASTNode, str, bool]
        ] = []
        for stmt in self._ast.body:
            if isinstance(stmt, VarDecl):
                embedded_top_level_bindings.extend(
                    embedded_bindings(stmt.value)
                )
            elif isinstance(stmt, TupleAssign):
                embedded_top_level_bindings.extend(
                    embedded_bindings(stmt.value)
                )
            elif isinstance(stmt, Assignment):
                embedded_top_level_bindings.extend(
                    embedded_bindings(stmt.target)
                )
                embedded_top_level_bindings.extend(
                    embedded_bindings(stmt.value)
                )
            elif isinstance(stmt, ExprStmt):
                embedded_top_level_bindings.extend(
                    embedded_bindings(stmt.expr)
                )

        exact_series = set(self._series_decl_bindings)
        exact_series_nodes = {node_id for node_id, _name in exact_series}
        aggregate_kinds = {"array", "map", "matrix", "udt"}
        candidate_bindings = list(embedded_top_level_bindings)
        for block, primitive_mismatch_unsafe in top_level_blocks:
            candidate_bindings.extend(
                nested_bindings(block, primitive_mismatch_unsafe)
            )
        for node, name, mismatch_unsafe in candidate_bindings:
            if name not in direct:
                continue
            if isinstance(node, ForStmt):
                nested_type, nested_spec = (
                    PineType.INT, TypeSpec.primitive("int")
                )
            elif isinstance(node, TupleAssign):
                nested_type, nested_spec = tuple_binding_type(node, name)
            else:
                nested_type, nested_spec = self._var_decl_types_by_node.get(
                    id(node), (PineType.FLOAT, None)
                )
            direct_type, direct_spec = direct[name]
            is_exact_series = (
                (id(node), name) in exact_series
                or (
                    id(node) in self._series_decl_nodes
                    and id(node) not in exact_series_nodes
                )
            )
            has_aggregate = any(
                spec is not None and spec.kind in aggregate_kinds
                for spec in (direct_spec, nested_spec)
            )
            has_incompatible_primitive = (
                mismatch_unsafe and nested_type != direct_type
                # The standard corpus uses a loop-local inferred int under
                # a direct float binding (Hexatrades ``base``).  C++ widens
                # that RHS safely.  The reverse direction and all other
                # cross-type shadows can truncate or fail compilation.
                and not (
                    direct_type == PineType.FLOAT
                    and nested_type == PineType.INT
                )
            )
            if (not is_exact_series
                    and not has_aggregate
                    and not has_incompatible_primitive):
                continue
            self._error(
                "A top-level block binding named "
                f"'{name}' shadows a direct script binding with history, "
                "aggregate, or incompatible typed state; declaration-exact "
                "block storage is not supported yet.",
                getattr(node, "loc", None),
            )

    def _check_persistent_drawing_member_collisions(self) -> None:
        """Fail closed when two lexical drawing vars share one C++ member.

        Persistent callable locals and the first block-scoped declaration still
        use their raw Pine spelling as the class-member identity.  Reusing that
        spelling in another callable/global scope would silently share state
        (and can emit uncompilable C++ when the handle kinds differ).  Sibling
        on-bar blocks that received ``__blkN`` identities are already safe.
        Until all callable storage is owner-qualified, reject only the drawing
        collisions made reachable by drawing-handle target typing.
        """
        from .types import _DRAWING_TYPE_NAMES

        persistent_groups: dict[str, list[tuple[ASTNode, TypeSpec | None]]] = {}
        for node_id, meta in self._var_member_metadata_by_node.items():
            node, member_name, _ptype, _init, _callable = meta
            spec = self._var_member_type_specs_by_node.get(node_id)
            persistent_groups.setdefault(member_name, []).append((node, spec))

        def is_drawing(spec: TypeSpec | None) -> bool:
            return bool(
                spec is not None
                and spec.kind == "udt"
                and spec.name in _DRAWING_TYPE_NAMES
            )

        for member_name, bindings in persistent_groups.items():
            if len(bindings) < 2 or not any(is_drawing(spec) for _, spec in bindings):
                continue
            node = bindings[1][0]
            self._error(
                "Persistent drawing bindings named "
                f"'{getattr(node, 'name', member_name)}' in distinct lexical "
                "owners would share one generated state member; rename one "
                "binding until callable-owned persistent storage is "
                "owner-qualified.",
                node.loc,
            )

        global_names = set(self._ordinary_global_binding_names)
        global_drawing_names: set[str] = set()
        for stmt in self._ast.body:
            if not isinstance(stmt, VarDecl) or stmt.is_var or stmt.is_varip:
                continue
            spec = (
                self._type_spec_from_hint(stmt.type_hint)
                if stmt.type_hint
                else self._type_spec_from_expr(stmt.value)
            )
            if is_drawing(spec):
                global_drawing_names.add(stmt.name)

        for member_name, bindings in persistent_groups.items():
            if member_name not in global_names:
                continue
            if not (
                member_name in global_drawing_names
                or any(is_drawing(spec) for _, spec in bindings)
            ):
                continue
            node = bindings[0][0]
            self._error(
                "Persistent drawing state named "
                f"'{getattr(node, 'name', member_name)}' collides with a "
                "top-level class-member binding; rename one binding until "
                "persistent storage is owner-qualified.",
                node.loc,
            )

        # Callable persistent drawing storage is currently cloned as a class
        # member and remapped by raw Pine name.  If such a declaration shadows
        # an ancestor local or parameter, the preloaded clone remap and C++
        # lexical scope disagree about which binding is visible before/after
        # the declaration.  Reject this narrow shape instead of silently
        # reading the parameter/local from the wrong storage.  Independent
        # sibling branches receive separate lexical inventories here (the
        # existing distinct-owner collision gate above may still reject two
        # persistent drawings that would share the same member identity).
        callable_drawing_nodes = {
            node_id
            for node_id, meta in self._var_member_metadata_by_node.items()
            if meta[4] and is_drawing(
                self._var_member_type_specs_by_node.get(node_id)
            )
        }

        def walk_callable_body(
            body: list[ASTNode],
            inherited_names: set[str],
        ) -> None:
            def walk_embedded_controls(value: Any, visible: set[str]) -> None:
                """Find block-valued if/switch expressions inside an RHS.

                Pine permits ``x = if ...``.  Those branch declarations have
                the same storage hazard as statement-level blocks, and the RHS
                must be inspected against the pre-declaration environment.
                """
                if value is None:
                    return
                if isinstance(value, IfStmt):
                    walk_embedded_controls(value.condition, visible)
                    walk_callable_body(value.body, visible)
                    walk_callable_body(value.else_body, visible)
                    return
                if isinstance(value, SwitchStmt):
                    walk_embedded_controls(value.expr, visible)
                    for case_expr, case_body in value.cases:
                        walk_embedded_controls(case_expr, visible)
                        walk_callable_body(case_body, visible)
                    walk_callable_body(value.default_body, visible)
                    return
                if isinstance(value, (FuncDef, MethodDef)):
                    return
                if isinstance(value, (list, tuple)):
                    for item in value:
                        walk_embedded_controls(item, visible)
                    return
                if isinstance(value, dict):
                    for item in value.values():
                        walk_embedded_controls(item, visible)
                    return
                if isinstance(value, ASTNode):
                    for child in vars(value).values():
                        walk_embedded_controls(child, visible)

            visible = set(inherited_names)
            for stmt in body:
                if isinstance(stmt, VarDecl):
                    walk_embedded_controls(stmt.value, visible)
                    if (id(stmt) in callable_drawing_nodes
                            and stmt.name in visible):
                        self._error(
                            "Persistent drawing binding "
                            f"'{stmt.name}' shadows an ancestor callable "
                            "parameter or local; rename one binding until "
                            "callable-owned persistent storage has lexical "
                            "owner qualification.",
                            stmt.loc,
                        )
                    visible.add(stmt.name)
                    continue
                if isinstance(stmt, TupleAssign):
                    walk_embedded_controls(stmt.value, visible)
                    visible.update(stmt.names)
                    continue
                if isinstance(stmt, Assignment):
                    walk_embedded_controls(stmt.target, visible)
                    walk_embedded_controls(stmt.value, visible)
                    continue
                if isinstance(stmt, ExprStmt):
                    walk_embedded_controls(stmt.expr, visible)
                    continue
                if isinstance(stmt, IfStmt):
                    walk_embedded_controls(stmt.condition, visible)
                    walk_callable_body(stmt.body, visible)
                    walk_callable_body(stmt.else_body, visible)
                    continue
                if isinstance(stmt, WhileStmt):
                    walk_embedded_controls(stmt.condition, visible)
                    walk_callable_body(stmt.body, visible)
                    continue
                if isinstance(stmt, ForStmt):
                    walk_embedded_controls(stmt.start, visible)
                    walk_embedded_controls(stmt.end, visible)
                    walk_embedded_controls(stmt.step, visible)
                    walk_callable_body(stmt.body, visible | {stmt.var})
                    continue
                if isinstance(stmt, ForInStmt):
                    walk_embedded_controls(stmt.iterable, visible)
                    loop_names = set(stmt.vars or [])
                    if stmt.var:
                        loop_names.add(stmt.var)
                    walk_callable_body(stmt.body, visible | loop_names)
                    continue
                if isinstance(stmt, SwitchStmt):
                    walk_embedded_controls(stmt.expr, visible)
                    for case_expr, case_body in stmt.cases:
                        walk_embedded_controls(case_expr, visible)
                        walk_callable_body(case_body, visible)
                    walk_callable_body(stmt.default_body, visible)

        for stmt in self._ast.body:
            if isinstance(stmt, (FuncDef, MethodDef)):
                walk_callable_body(stmt.body, set(stmt.params))

    def _record_global_binding_stmt(self, name: str, pine_type: PineType,
                                    is_var: bool, decl_node: ASTNode | None = None) -> None:
        info = self._global_binding_infos.get(name)
        if info is None:
            info = MutableGlobalInfo(
                name=name,
                pine_type=pine_type,
                is_var=is_var,
                decl_node=decl_node,
            )
            self._global_binding_infos[name] = info
        else:
            info.pine_type = pine_type
            info.is_var = info.is_var or is_var
            if decl_node is not None and info.decl_node is None:
                info.decl_node = decl_node

        top_stmt = self._current_top_level_stmt or decl_node
        if top_stmt is not None and (not info.source_stmts or info.source_stmts[-1] is not top_stmt):
            info.source_stmts.append(top_stmt)

    @staticmethod
    def _is_input_func_call(node: FuncCall) -> bool:
        """True for an ``input(...)`` or ``input.<member>(...)`` call."""
        callee = node.callee
        if isinstance(callee, Identifier) and callee.name == "input":
            return True
        return (
            isinstance(callee, MemberAccess)
            and isinstance(callee.object, Identifier)
            and callee.object.name == "input"
        )

    def _collect_security_mutable_globals(
        self, node: ASTNode | None, resolving: set[str] | None = None
    ) -> set[str]:
        if node is None:
            return set()
        if resolving is None:
            resolving = set()

        out: set[str] = set()

        if isinstance(node, Identifier):
            name = node.name
            if name in self._global_binding_infos:
                info = self._global_binding_infos[name]
                if info.is_var or name in self._global_reassigned_names:
                    out.add(name)
                    if name in resolving:
                        return out
                    resolving.add(name)
                    for stmt in info.source_stmts:
                        out |= self._collect_security_mutable_globals(stmt, resolving)
                    resolving.remove(name)
                    return out
            if name in self._global_expr_map and name not in resolving:
                resolving.add(name)
                out |= self._collect_security_mutable_globals(self._global_expr_map[name], resolving)
                resolving.remove(name)
                return out

        if isinstance(node, FuncCall) and isinstance(node.callee, Identifier):
            func_name = node.callee.name
            if func_name in self._func_defs:
                call_key = f"func:{func_name}"
                if call_key in resolving:
                    return out
                resolving.add(call_key)
                for arg in node.args:
                    out |= self._collect_security_mutable_globals(arg, resolving)
                for value in node.kwargs.values():
                    out |= self._collect_security_mutable_globals(value, resolving)
                for stmt in self._func_defs[func_name].body:
                    out |= self._collect_security_mutable_globals(stmt, resolving)
                resolving.remove(call_key)
                return out

        if isinstance(node, FuncCall) and self._is_input_func_call(node):
            # An ``input.*()`` / ``input()`` initializer is a compile-time
            # constant. Only its defval (first positional or ``defval=``) can
            # carry a genuine data dependency; the cosmetic kwargs
            # (group/tooltip/title/inline/display/confirm/minval/maxval/step)
            # are presentation-only. Walking them would falsely pull a
            # ``var string GROUP = "..."`` label into the security's
            # mutable-globals set and trip the "TA ctor depends on rebound
            # mutable globals" reject (parallax / higherTimeframeLength).
            defval = node.args[0] if node.args else node.kwargs.get("defval")
            if defval is not None:
                out |= self._collect_security_mutable_globals(defval, resolving)
            return out

        def walk(value: Any) -> None:
            nonlocal out
            if value is None:
                return
            if hasattr(value, "__dict__"):
                out |= self._collect_security_mutable_globals(value, resolving)
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    walk(item)
                return
            if isinstance(value, dict):
                for item in value.values():
                    walk(item)

        for child in vars(node).values():
            walk(child)

        return out

    def _propagate_call_site_counts(self) -> None:
        """Propagate stateful UDF identity through complete call paths.

        A UDF is stateful when it owns series/``var`` state, TA state, or
        ``fixnan`` state, and every pure wrapper that reaches such a UDF is
        stateful transitively.  Give each wrapper's textual calls stable cs
        identities, then inherit a multi-call-site parent's count down to its
        stateful callees.  Any inherited TA/fixnan variants are materialized
        immediately; exporting a count without the corresponding members would
        make codegen reference undeclared or shared state.
        """
        from pineforge_codegen.ast_nodes import FuncCall, FuncDef, Identifier

        func_defs: dict[str, FuncDef] = {}
        for stmt in self._ast.body:
            if isinstance(stmt, FuncDef):
                func_defs[stmt.name] = stmt

        # UDT methods participate in the same stateful call graph as plain
        # UDFs.  Their analyzer identity is ``Type.method`` while FuncInfo.node
        # carries the synthetic FuncDef used by codegen.
        func_info_by_name = {fi.name: fi for fi in self._func_infos}
        for fi in self._func_infos:
            if getattr(fi, "is_udt_method", False) and fi.node is not None:
                func_defs.setdefault(fi.name, fi.node)

        # Preserve call-node identity as well as the callee name: late clone
        # materialization needs the textual call's argument mapping to resolve
        # parameterized TA constructor lengths.
        def _resolved_user_call_name(call: FuncCall, owner: str | None) -> str | None:
            if isinstance(call.callee, Identifier):
                return call.callee.name if call.callee.name in func_defs else None
            if not isinstance(call.callee, MemberAccess):
                return None

            recv = call.callee.object
            method = call.callee.member
            udt_name: str | None = None
            if isinstance(recv, Identifier):
                owner_info = func_info_by_name.get(owner or "")
                # Resolve the active callable's lexical parameters before the
                # flat variable registry.  A parameter may legally shadow a
                # top-level UDT variable with a different type; consulting the
                # global binding first misidentifies the method call edge and
                # silently shares state between written wrapper call sites.
                if owner_info is not None and owner_info.node is not None:
                    if (getattr(owner_info, "is_udt_method", False)
                            and owner_info.node.params
                            and recv.name == owner_info.node.params[0]):
                        udt_name = owner_info.udt_type_name
                    elif recv.name in owner_info.node.params:
                        param_idx = owner_info.node.params.index(recv.name)
                        specs = getattr(owner_info, "param_type_specs", []) or []
                        spec = specs[param_idx] if param_idx < len(specs) else None
                        if spec is not None and spec.kind == "udt":
                            udt_name = spec.name
            # Resolve the surviving exact global/lexical symbol before the
            # flat raw-name registry. A later callable-local declaration can
            # overwrite that registry and otherwise attach the wrong stateful
            # method edge to wrappers and their written call sites.
            if udt_name is None:
                spec = self._type_spec_from_expr(recv)
                if spec is not None and spec.kind == "udt":
                    udt_name = spec.name
            if udt_name is None and isinstance(recv, Identifier):
                udt_name = self._udt_var_types.get(recv.name)
            key = f"{udt_name}.{method}" if udt_name else ""
            return key if key in func_defs else None

        def _find_calls(node, known_funcs: set[str],
                        owner: str | None = None,
                        seen: set[int] | None = None) -> list[tuple[str, FuncCall]]:
            calls: list[tuple[str, FuncCall]] = []
            if node is None:
                return calls
            if seen is None:
                seen = set()
            if isinstance(node, (list, tuple, dict)) or hasattr(node, "__dict__"):
                node_id = id(node)
                if node_id in seen:
                    return calls
                seen.add(node_id)
            if isinstance(node, (list, tuple)):
                for item in node:
                    calls.extend(_find_calls(item, known_funcs, owner, seen))
                return calls
            if isinstance(node, dict):
                for item in node.values():
                    calls.extend(_find_calls(item, known_funcs, owner, seen))
                return calls
            if not hasattr(node, "__dict__"):
                return calls
            if isinstance(node, FuncCall):
                resolved = _resolved_user_call_name(node, owner)
                if resolved in known_funcs:
                    calls.append((resolved, node))
            for attr_val in vars(node).values():
                calls.extend(_find_calls(attr_val, known_funcs, owner, seen))
            return calls

        known_func_names = set(func_defs.keys())
        calls_by_parent = {
            name: _find_calls(func_def, known_func_names, name)
            for name, func_def in func_defs.items()
        }
        calls_by_callee: dict[str, list[FuncCall]] = {
            name: [] for name in known_func_names
        }
        call_edges: list[tuple[str | None, str, FuncCall]] = []
        # Preserve source order across definitions and top-level statements.
        # Method bodies use their ``Type.method`` owner so ``self.sibling()``
        # resolves without relying on a now-exited symbol-table scope.
        for stmt in self._ast.body:
            if isinstance(stmt, FuncDef):
                owner = stmt.name
            elif isinstance(stmt, MethodDef):
                owner = f"{stmt.type_name}.{stmt.name}"
            else:
                owner = None
            for callee, call in _find_calls(stmt, known_func_names, owner):
                calls_by_callee.setdefault(callee, []).append(call)
                call_edges.append((owner, callee, call))

        # A source-ordered method visit threads TA constructor arguments through
        # any already-known method callee.  Forward definitions require the
        # complete call graph above, and an edge may need revisiting even after
        # it has a cs identity: its callee can acquire another exact TA owner (or
        # a refined per-owner ctor template) later in this same closure.
        #
        # Track the exact source-index/template snapshot materialized for each
        # textual edge.  Only the delta is materialized on a later round, which
        # prevents cs>0 state from being cloned twice.  A finite callable graph
        # converges in at most its path depth; retain a conservative explicit
        # bound and fail closed rather than looping on a recursive TA cycle.
        max_ta_rounds = max(2, len(func_defs) + len(call_edges) + 2)
        last_changed_call: FuncCall | None = None
        for _round in range(max_ta_rounds):
            late_method_ta_changed = False
            for owner, callee, call in call_edges:
                call_id = id(call)
                if call_id in self._func_inherited_call_nodes:
                    continue
                callee_info = func_info_by_name.get(callee)
                if (
                    callee_info is None
                    or not getattr(callee_info, "is_udt_method", False)
                    or callee not in self._func_ta_ranges
                ):
                    continue

                source_indices = list(self._func_ta_indices.get(callee, ()))
                if not source_indices:
                    source_range = self._func_ta_ranges[callee]
                    source_indices = list(range(*source_range))
                callee_templates = self._func_ta_ctor_args.get(callee, {})
                processed_templates = self._func_ta_call_templates.get(
                    call_id, {}
                )
                existing_site = self._func_call_cs_map.get(call_id)
                candidate_cs_idx = (
                    existing_site[1]
                    if existing_site is not None and existing_site[0] == callee
                    else self._func_call_site_count.get(callee, 0)
                )
                selected_targets = self._func_ta_call_targets.get(
                    (call_id, candidate_cs_idx), {}
                )
                owner_indices = (
                    set(self._func_ta_indices.get(owner, ()))
                    if owner is not None
                    else set()
                )
                owner_templates = (
                    self._func_ta_ctor_args.get(owner, {})
                    if owner is not None
                    else {}
                )
                stale_indices: list[int] = []
                for index in source_indices:
                    site = self._ta_call_sites[index]
                    source_template = tuple(
                        callee_templates.get(
                            index,
                            getattr(site, "_orig_ctor_args", site.ctor_args),
                        )
                    )
                    target = selected_targets.get(index)
                    if (
                        processed_templates.get(index) != source_template
                        or target is None
                        or (
                            owner is not None
                            and (
                                target not in owner_indices
                                or target not in owner_templates
                            )
                        )
                    ):
                        stale_indices.append(index)
                if not stale_indices:
                    continue

                if existing_site is not None and existing_site[0] == callee:
                    cs_idx = existing_site[1]
                    materialize_fixnan = False
                else:
                    cs_idx = candidate_cs_idx
                    self._func_call_site_count[callee] = cs_idx + 1
                    self._func_call_cs_map[call_id] = (callee, cs_idx)
                    materialize_fixnan = True

                selected = self._materialize_user_func_call_site_state(
                    callee,
                    cs_idx,
                    call,
                    ta_site_indices=stale_indices,
                    materialize_fixnan=materialize_fixnan,
                )
                if selected and owner is not None:
                    current_indices = self._func_ta_indices.setdefault(owner, [])
                    current_indices[:] = sorted(
                        set(current_indices) | set(selected.values())
                    )
                    self._func_ta_ranges[owner] = (
                        min(current_indices), max(current_indices) + 1
                    )
                    owner_templates = self._func_ta_ctor_args.setdefault(
                        owner, {}
                    )
                    for target in selected.values():
                        owner_templates[target] = list(
                            self._ta_call_sites[target].ctor_args
                        )
                late_method_ta_changed = True
                last_changed_call = call
            if not late_method_ta_changed:
                break
        else:
            self._error(
                "Callable TA ownership propagation did not converge; "
                "recursive stateful call paths are unsupported.",
                last_changed_call.loc if last_changed_call is not None else None,
            )

        # A history-reading callable receives ``Series<T>`` parameters. That
        # requirement must flow outward through every wrapper parameter that
        # is forwarded unchanged; otherwise a wrapper stays ``double`` and
        # generated C++ cannot bind it to the callee's ``const Series&``.
        # Resolve this after every UDF/method definition is known and iterate
        # to a fixed point so method <- inner <- outer chains are independent
        # of declaration/source order.
        def _bound_user_call_args(callee: str, call: FuncCall) -> list:
            info = func_info_by_name.get(callee)
            if info is None or info.node is None:
                return []
            params = list(info.node.params)
            if (
                getattr(info, "is_udt_method", False)
                and isinstance(call.callee, MemberAccess)
            ):
                return [
                    call.callee.object,
                    *self._bind_callable_args(call, params[1:]),
                ]
            return self._bind_callable_args(call, params)

        series_requirements: dict[str, set[str]] = {
            name: set(params)
            for name, params in self._func_series_vars.items()
            if params
        }
        series_changed = True
        while series_changed:
            series_changed = False
            for owner, callee, call in call_edges:
                callee_info = func_info_by_name.get(callee)
                if callee_info is None or callee_info.node is None:
                    continue
                callee_series = series_requirements.get(callee, set())
                if not callee_series:
                    continue
                actuals = _bound_user_call_args(callee, call)
                for index, param_name in enumerate(callee_info.node.params):
                    if param_name not in callee_series or index >= len(actuals):
                        continue
                    actual = actuals[index]
                    if not isinstance(actual, Identifier):
                        continue
                    if actual.name in BAR_FIELDS:
                        self._series_bar_fields.add(actual.name)
                    if owner is None:
                        continue
                    owner_info = func_info_by_name.get(owner)
                    if (
                        owner_info is None
                        or owner_info.node is None
                        or actual.name not in owner_info.node.params
                    ):
                        continue
                    owner_series = self._func_series_vars.setdefault(
                        owner, set()
                    )
                    if actual.name not in owner_series:
                        owner_series.add(actual.name)
                    owner_requirements = series_requirements.setdefault(
                        owner, set()
                    )
                    if actual.name not in owner_requirements:
                        owner_requirements.add(actual.name)
                        series_changed = True

        # Codegen synthesizes a Series buffer for two expression shapes that
        # do not appear in ``_func_series_vars`` themselves:
        #
        #   * a call result read through history, e.g. ``f()[1]``;
        #   * a scalar expression bridged into a UDF series parameter, e.g.
        #     ``history(close + open)`` where ``history(src) => src[1]``.
        #
        # A buffer is mutable per-call-site state just like TA/fixnan.  Mark
        # its lexical owner stateful before the normal call-path closure so a
        # function invoked from two source sites receives two emitted bodies
        # (and therefore two independent generated buffer members).  Without
        # this, moving the old function-local static into a class member would
        # accidentally merge both Pine call sites into one history stream.
        def _actual_arg(call: FuncCall, param_name: str, param_idx: int):
            if param_idx < len(call.args):
                return call.args[param_idx]
            return call.kwargs.get(param_name)

        def _needs_scalar_series_bridge(call: FuncCall) -> bool:
            if not isinstance(call.callee, Identifier):
                return False
            callee = call.callee.name
            fi = func_info_by_name.get(callee)
            if fi is None or fi.node is None:
                return False
            series_params = self._func_series_vars.get(callee, set())
            if not series_params:
                return False
            direct_bar_series = {
                "open", "high", "low", "close", "volume",
                "hl2", "hlc3", "ohlc4",
            }
            for idx, param_name in enumerate(fi.node.params):
                if param_name not in series_params:
                    continue
                arg = _actual_arg(call, param_name, idx)
                if arg is None:
                    continue
                if isinstance(arg, Identifier) and (
                    arg.name in direct_bar_series or arg.name in self._series_vars
                ):
                    continue
                return True
            return False

        def _has_synthetic_history_state(
                node, seen: set[int] | None = None) -> bool:
            if node is None:
                return False
            if seen is None:
                seen = set()
            if isinstance(node, (list, tuple, dict)) or hasattr(node, "__dict__"):
                node_id = id(node)
                if node_id in seen:
                    return False
                seen.add(node_id)
            if isinstance(node, (list, tuple)):
                return any(
                    _has_synthetic_history_state(item, seen) for item in node
                )
            if isinstance(node, dict):
                return any(
                    _has_synthetic_history_state(item, seen)
                    for item in node.values()
                )
            if not hasattr(node, "__dict__"):
                return False
            if (isinstance(node, Subscript)
                    and isinstance(node.object, FuncCall)):
                return True
            if isinstance(node, FuncCall) and _needs_scalar_series_bridge(node):
                return True
            return any(
                _has_synthetic_history_state(value, seen)
                for value in vars(node).values()
            )

        synthetic_history_stateful = {
            name for name, func_def in func_defs.items()
            if _has_synthetic_history_state(func_def)
        }

        # request.security owns a separate evaluator context and already
        # materializes/remaps its embedded TA state per SecurityCallInfo.  Do
        # not thread ordinary UDF clone indices through a function used as a
        # security expression (or through the containing wrapper); doing so
        # would double-clone evaluator state and disturb expression identity.
        security_boundary_funcs: set[str] = set()
        for sec in getattr(self, "_security_calls", []) or []:
            containing = getattr(sec, "containing_func", "") or ""
            if containing:
                security_boundary_funcs.add(containing)
            expression = getattr(sec, "expression", None)
            if expression is not None:
                security_boundary_funcs.update(
                    sub for sub, _ in _find_calls(
                        expression, known_func_names, containing or None
                    )
                )

        # Canonical direct-state predicate.  TA-only and fixnan-only helpers
        # are just as stateful as functions carrying an explicit series/var.
        stateful = (
            set(self._func_series_vars)
            | set(self._func_var_members)
            | set(self._func_ta_ranges)
            | set(self._func_fixnan_indices)
            | synthetic_history_stateful
        )

        # Close upward over the call graph so a pure A -> B -> stateful C chain
        # receives variants at every level.  The existing clone-only emitter
        # marker is intentionally separate from request.security evaluator
        # identity, so ordinary security calls remain shared unless the
        # dedicated timeframe-monomorphization pass clones them.
        changed = True
        while changed:
            changed = False
            for fname, calls in calls_by_parent.items():
                if fname in stateful:
                    continue
                if any(sub in stateful for sub, _ in calls):
                    stateful.add(fname)
                    changed = True

        # Direct fixnan-only functions and pure transitive wrappers own no
        # TA/series member that would trip the emitter's ordinary body-clone
        # gate. Reuse its established body-only clone marker; this does not
        # create or renumber any SecurityCallInfo.
        for fname in sorted(stateful):
            if (fname not in self._func_ta_ranges
                    and fname not in self._func_series_vars
                    and fname not in self._func_var_members):
                self._func_security_clone_only.add(fname)

        # The initial visitor only numbers directly-stateful callees. Backfill
        # stable identities for newly discovered pure wrappers without
        # disturbing any indices already assigned by the visitor or security
        # monomorphization.
        for fname in sorted(stateful):
            calls = calls_by_callee.get(fname, [])
            current = self._func_call_site_count.get(fname, 0)
            next_idx = current
            for call in calls:
                if id(call) in self._func_inherited_call_nodes:
                    continue
                existing = self._func_call_cs_map.get(id(call))
                if existing is not None and existing[0] == fname:
                    next_idx = max(next_idx, existing[1] + 1)
                    continue
                self._func_call_cs_map[id(call)] = (fname, next_idx)
                # Most ordinary UDFs were already materialized by the lexical
                # call visitor. UDT method calls are resolved in this late
                # graph pass, so their TA/fixnan state still needs matching
                # cs0/cs1/... members before codegen emits the variants.
                self._materialize_user_func_call_site_state(
                    fname, next_idx, call
                )
                next_idx += 1
            if next_idx > current:
                self._func_call_site_count[fname] = next_idx

        # Inherit each multi-call-site parent's index space down the full path.
        # Re-run to a fixed point for A -> B -> C chains.
        def _ta_variant_target_map(
            func_name: str, cs_idx: int
        ) -> dict[int, int]:
            source_indices = list(self._func_ta_indices.get(func_name, ()))
            if not source_indices and func_name in self._func_ta_ranges:
                source_indices = list(range(*self._func_ta_ranges[func_name]))
            if cs_idx == 0:
                return {index: index for index in source_indices}
            overrides = self._func_cs_ta_clone_names.get(
                (func_name, cs_idx), {}
            )
            by_member = {
                site.member_name: index
                for index, site in enumerate(self._ta_call_sites)
            }
            targets: dict[int, int] = {}
            for source_index in source_indices:
                source_name = self._ta_call_sites[source_index].member_name
                target_name = overrides.get(
                    source_name, f"{source_name}_cs{cs_idx}"
                )
                target_index = by_member.get(target_name)
                if target_index is not None:
                    targets[source_index] = target_index
            return targets

        def _edge_ta_variant_target_map(
            parent_name: str,
            parent_cs_idx: int,
            callee_name: str,
            call_node: FuncCall,
        ) -> dict[int, int]:
            """Compose callee source identity through one parent call edge.

            ``_ta_variant_target_map`` is keyed by the parent's base TA
            identities.  Those identities need not equal the callee's source
            indices: an earlier textual call can make this edge select a
            shifted parent site.  The edge's original materialization records
            the exact ``callee source -> parent base`` relation; compose that
            with the active parent variant instead of assuming equal keys.
            """
            parent_targets = _ta_variant_target_map(
                parent_name, parent_cs_idx
            )
            cs_info = self._func_call_cs_map.get(id(call_node))
            edge_cs_idx = (
                cs_info[1]
                if cs_info is not None and cs_info[0] == callee_name
                else 0
            )
            edge_targets = self._func_ta_call_targets.get(
                (id(call_node), edge_cs_idx), {}
            )
            source_indices = list(
                self._func_ta_indices.get(callee_name, ())
            )
            if not source_indices and callee_name in self._func_ta_ranges:
                source_indices = list(
                    range(*self._func_ta_ranges[callee_name])
                )
            composed: dict[int, int] = {}
            for source_index in source_indices:
                parent_source_index = edge_targets.get(
                    source_index, source_index
                )
                active_target = parent_targets.get(parent_source_index)
                if active_target is not None:
                    composed[source_index] = active_target
            return composed

        changed = True
        while changed:
            changed = False
            for fname, count in list(self._func_call_site_count.items()):
                if count <= 1:
                    continue
                if fname not in func_defs:
                    continue
                if fname in security_boundary_funcs:
                    continue
                for sub, call_node in calls_by_parent.get(fname, []):
                    if sub not in stateful:
                        continue
                    current = self._func_call_site_count.get(sub, 0)
                    if current < count:
                        # One textual nested call is not an independent cs0
                        # path: it inherits the active parent clone index.
                        # Remove the visitor's provisional cs0 map so codegen's
                        # established active-index fallback dispatches
                        # F_csK -> G_csK.  Keeping the map would make the
                        # context-sensitive instance pre-pass pin every parent
                        # clone to G_cs0 before that fallback can run.
                        if current == 1:
                            cs_info = self._func_call_cs_map.get(id(call_node))
                            if cs_info == (sub, 0):
                                self._func_call_cs_map.pop(id(call_node), None)
                                self._func_inherited_call_nodes.add(id(call_node))
                                self._func_inherited_call_names[
                                    id(call_node)
                                ] = sub
                                # Its definition-time type profile was likewise
                                # provisional: forwarded untyped owner params
                                # are still UNKNOWN during that visit. Each
                                # inherited parent clone supplies the real
                                # primitive profile below.
                                self._func_callsite_param_types.pop(
                                    (sub, 0), None
                                )
                                self._func_callsite_return_types.pop(
                                    (sub, 0), None
                                )
                        for cs_idx in range(current, count):
                            parent_ta_targets = _edge_ta_variant_target_map(
                                fname,
                                cs_idx,
                                sub,
                                call_node,
                            )
                            self._materialize_user_func_call_site_state(
                                sub,
                                cs_idx,
                                call_node,
                                reuse_existing_owner=fname,
                                reuse_existing_targets=parent_ta_targets,
                            )
                        self._func_call_site_count[sub] = count
                        changed = True

        self._resolve_callable_callsite_primitive_types(
            call_edges,
            func_info_by_name,
            _bound_user_call_args,
        )

    @staticmethod
    def _primitive_pine_type_from_spec(spec) -> PineType:
        if spec is None or getattr(spec, "kind", None) != "primitive":
            return PineType.UNKNOWN
        return {
            "int": PineType.INT,
            "float": PineType.FLOAT,
            "bool": PineType.BOOL,
            "string": PineType.STRING,
            "color": PineType.COLOR,
        }.get(getattr(spec, "name", None), PineType.UNKNOWN)

    def _resolve_callable_callsite_primitive_types(
        self,
        call_edges,
        func_info_by_name,
        bound_user_call_args,
    ) -> None:
        """Reconcile per-written-call primitive types after clone closure.

        Direct UDF calls are typed during the lexical visit, but UDT methods
        and pure wrappers receive their cs identities only in the late stateful
        call-graph pass.  Propagate an enclosing variant's parameter profile
        through exact forwarded argument ASTs until every emitted history
        callable variant has its own stable primitive profile.
        """
        edge_by_call_id = {
            id(call): (owner, callee, call)
            for owner, callee, call in call_edges
        }

        def declared_types(name: str, size: int) -> list[PineType]:
            specs = list(self._func_param_type_specs.get(name, ()))
            return [
                self._primitive_pine_type_from_spec(
                    specs[index] if index < len(specs) else None
                )
                for index in range(size)
            ]

        def merge_profile(
            callee: str,
            cs_idx: int,
            incoming: list[PineType],
            call,
        ) -> bool:
            info = func_info_by_name.get(callee)
            if info is None or info.node is None:
                return False
            size = len(info.node.params)
            declared = declared_types(callee, size)
            key = (callee, cs_idx)
            current = list(
                self._func_callsite_param_types.get(
                    key, [PineType.UNKNOWN] * size
                )
            )
            while len(current) < size:
                current.append(PineType.UNKNOWN)
            changed = False
            for index in range(size):
                candidate = declared[index]
                if candidate == PineType.UNKNOWN and index < len(incoming):
                    candidate = incoming[index]
                if candidate == PineType.UNKNOWN:
                    continue
                if current[index] == PineType.UNKNOWN:
                    current[index] = candidate
                    changed = True
                elif current[index] != candidate:
                    self._error(
                        "Cannot safely specialize untyped parameter '"
                        + info.node.params[index]
                        + "' of callable '"
                        + callee
                        + "': distinct primitive types collapse onto the same "
                        + f"written-call variant cs{cs_idx}. Inline the calls "
                        + "or declare an explicit parameter type.",
                        call.loc,
                    )
            if changed or key not in self._func_callsite_param_types:
                self._func_callsite_param_types[key] = current
            return changed

        # Backfill every direct UDF/method identity assigned by the late graph
        # pass. Definition-time wrapper calls can still contain UNKNOWN params;
        # the fixed point below resolves those from their owner variant.
        for _owner, callee, call in call_edges:
            if _owner is not None:
                # Definition-time analyzer fallbacks for an expression over an
                # untyped owner parameter are not concrete facts (``y + 0``
                # historically reports FLOAT while ``y`` is still UNKNOWN).
                # The owner-variant fixed point below resolves nested calls.
                continue
            cs_info = self._func_call_cs_map.get(id(call))
            if cs_info is None or cs_info[0] != callee:
                continue
            incoming = self._callable_bound_param_types_by_node.get(
                id(call), []
            )
            merge_profile(callee, cs_info[1], incoming, call)

        def owner_profile(owner: str, owner_cs: int | None) -> list[PineType]:
            info = func_info_by_name.get(owner)
            if info is None or info.node is None:
                return []
            size = len(info.node.params)
            if owner_cs is not None:
                profile = self._func_callsite_param_types.get(
                    (owner, owner_cs)
                )
                if profile is not None:
                    return list(profile)
            declared = declared_types(owner, size)
            legacy = list(getattr(info, "param_types", ()) or ())
            return [
                declared[index]
                if declared[index] != PineType.UNKNOWN
                else (
                    legacy[index]
                    if index < len(legacy)
                    else PineType.UNKNOWN
                )
                for index in range(size)
            ]

        def target_variant(
            owner: str | None,
            owner_cs: int | None,
            callee: str,
            call,
        ) -> int | None:
            cs_info = self._func_call_cs_map.get(id(call))
            callee_count = self._func_call_site_count.get(callee, 0)
            if owner is None:
                if cs_info is not None and cs_info[0] == callee:
                    return cs_info[1]
                return None
            if cs_info is not None and cs_info[0] == callee:
                # A surviving lexical mapping is authoritative. Codegen's
                # context-sensitive instance dispatcher pins every clone of
                # this owner to that same written callee variant. If two owner
                # profiles disagree, merge_profile must reject the collapse;
                # pretending the owner index selects another callee clone
                # would type a function different from the one actually
                # emitted at the call edge.
                return cs_info[1]
            if owner_cs is not None and callee_count > 1:
                # A removed lexical map marks an inherited single-call path;
                # those variants deliberately follow the enclosing clone.
                return owner_cs if owner_cs < callee_count else None
            return None

        def mentions_owner_parameter(value, names: set[str]) -> bool:
            if value is None:
                return False
            if isinstance(value, Identifier):
                return value.name in names
            if isinstance(value, (list, tuple)):
                return any(
                    mentions_owner_parameter(item, names) for item in value
                )
            if isinstance(value, dict):
                return any(
                    mentions_owner_parameter(item, names)
                    for item in value.values()
                )
            if not hasattr(value, "__dict__"):
                return False
            return any(
                mentions_owner_parameter(child, names)
                for child in vars(value).values()
            )

        # Calls visited inside an untyped callable are initially analyzed
        # before any concrete outer call-site profile exists.  The ordinary
        # expression analyzer therefore records defaults for transformed
        # expressions (many numeric built-ins default to FLOAT).  That value
        # is not evidence about the eventual written call: discard it for
        # every nested argument that depends on an untyped owner parameter so
        # the fixed point below either derives the type from the owner variant
        # or rejects the unresolved transformation deterministically.
        for owner, callee, call in call_edges:
            if owner is None:
                continue
            owner_info = func_info_by_name.get(owner)
            callee_info = func_info_by_name.get(callee)
            if (
                owner_info is None
                or owner_info.node is None
                or callee_info is None
                or callee_info.node is None
            ):
                continue
            owner_declared = declared_types(
                owner, len(owner_info.node.params)
            )
            untyped_owner_params = {
                param
                for index, param in enumerate(owner_info.node.params)
                if owner_declared[index] == PineType.UNKNOWN
            }
            if not untyped_owner_params:
                continue
            actuals = bound_user_call_args(callee, call)
            dependent_slots = {
                index
                for index, actual in enumerate(actuals)
                if mentions_owner_parameter(actual, untyped_owner_params)
            }
            if not dependent_slots:
                continue
            callee_declared = declared_types(
                callee, len(callee_info.node.params)
            )
            owner_count = self._func_call_site_count.get(owner, 0)
            owner_variants = range(owner_count) if owner_count > 0 else (None,)
            for owner_cs in owner_variants:
                callee_cs = target_variant(owner, owner_cs, callee, call)
                if callee_cs is None:
                    continue
                key = (callee, callee_cs)
                current = list(
                    self._func_callsite_param_types.get(
                        key,
                        [PineType.UNKNOWN] * len(callee_info.node.params),
                    )
                )
                while len(current) < len(callee_info.node.params):
                    current.append(PineType.UNKNOWN)
                invalidated = False
                for index in dependent_slots:
                    if (
                        index < len(current)
                        and index < len(callee_declared)
                        and callee_declared[index] == PineType.UNKNOWN
                        and current[index] != PineType.UNKNOWN
                    ):
                        current[index] = PineType.UNKNOWN
                        invalidated = True
                if invalidated:
                    self._func_callsite_param_types[key] = current
                    self._func_callsite_return_types.pop(key, None)

        # Resolve parameter forwarding and direct-wrapper returns together.
        # The graph is finite and primitive types only move UNKNOWN -> known.
        for _ in range(64):
            changed = False
            for owner, callee, call in call_edges:
                if owner is None:
                    continue
                owner_count = self._func_call_site_count.get(owner, 0)
                owner_variants = (
                    range(owner_count) if owner_count > 0 else (None,)
                )
                owner_info = func_info_by_name.get(owner)
                if owner_info is None or owner_info.node is None:
                    continue
                actuals = bound_user_call_args(callee, call)
                recorded = self._callable_bound_param_types_by_node.get(
                    id(call), []
                )
                for owner_cs in owner_variants:
                    callee_cs = target_variant(
                        owner, owner_cs, callee, call
                    )
                    if callee_cs is None:
                        continue
                    profile = owner_profile(owner, owner_cs)
                    param_map = {
                        name: (
                            profile[index]
                            if index < len(profile)
                            else PineType.UNKNOWN
                        )
                        for index, name in enumerate(owner_info.node.params)
                    }
                    incoming: list[PineType] = []
                    owner_param_names = set(owner_info.node.params)
                    for index, actual in enumerate(actuals):
                        inferred = self._callsite_primitive_expr_type(
                            actual, param_map
                        )
                        if (
                            inferred == PineType.UNKNOWN
                            and not mentions_owner_parameter(
                                actual, owner_param_names
                            )
                            and index < len(recorded)
                        ):
                            inferred = recorded[index]
                        incoming.append(inferred)
                    if merge_profile(callee, callee_cs, incoming, call):
                        changed = True

            # Recompute each variant's primitive return. A direct terminal
            # wrapper call uses the exact callee variant return rather than the
            # shared definition-level cache.
            for key, profile in list(
                self._func_callsite_param_types.items()
            ):
                name, cs_idx = key
                info = func_info_by_name.get(name)
                if info is None or info.node is None:
                    continue
                ret = self._callsite_callable_return_type(
                    info.node, list(profile), info.return_type
                )
                terminal = self._direct_terminal_return_expr(info.node)
                if isinstance(terminal, FuncCall):
                    edge = edge_by_call_id.get(id(terminal))
                    if edge is not None:
                        _, terminal_callee, terminal_call = edge
                        terminal_cs = target_variant(
                            name, cs_idx, terminal_callee, terminal_call
                        )
                        if terminal_cs is not None:
                            nested_ret = self._func_callsite_return_types.get(
                                (terminal_callee, terminal_cs),
                                PineType.UNKNOWN,
                            )
                            if nested_ret != PineType.UNKNOWN:
                                ret = nested_ret
                if self._func_callsite_return_types.get(key) != ret:
                    self._func_callsite_return_types[key] = ret
                    changed = True
            if not changed:
                break

        # A live untyped history parameter without a variant type cannot be
        # emitted faithfully. Reject it rather than falling back to the first
        # call's global FuncInfo type and reintroducing source-order coercion.
        for name, series_names in self._func_series_vars.items():
            info = func_info_by_name.get(name)
            if info is None or info.node is None:
                continue
            count = self._func_call_site_count.get(name, 0)
            declared = declared_types(name, len(info.node.params))
            for cs_idx in range(count):
                profile = self._func_callsite_param_types.get(
                    (name, cs_idx), []
                )
                for index, param in enumerate(info.node.params):
                    if param not in series_names:
                        continue
                    if declared[index] != PineType.UNKNOWN:
                        continue
                    resolved = (
                        profile[index]
                        if index < len(profile)
                        else PineType.UNKNOWN
                    )
                    if resolved == PineType.UNKNOWN:
                        self._error(
                            "Cannot infer the per-callsite primitive type of "
                            f"history parameter '{param}' in callable '{name}' "
                            f"variant cs{cs_idx}; declare its type explicitly.",
                            info.node.loc,
                        )

    # ------------------------------------------------------------------
    # Mixed-callsite UDF timeframe-param security rejection.
    #
    # A ``request.security`` whose ``timeframe`` is a parameter of its
    # containing UDF maps to ONE evaluator regardless of how many times the
    # UDF is called. When the UDF is called from >= 2 sites with DISTINCT
    # literal timeframes, a single evaluator cannot faithfully serve them
    # all and the resolver would silently collapse onto the chart timeframe
    # (``input_tf_``). Per-callsite evaluator specialization (cloning the
    # evaluator + UDF) is the correct fix but is not wired in this iteration,
    # so we reject deterministically instead of emitting wrong semantics.
    # ------------------------------------------------------------------
    def _check_mixed_callsite_security_tf(self) -> None:
        sec_calls = getattr(self, "_security_calls", None)
        if not sec_calls:
            return
        # Build user-function definitions lookup once.
        func_defs: dict[str, FuncDef] = {}
        for stmt in self._ast.body:
            if isinstance(stmt, FuncDef):
                func_defs[stmt.name] = stmt

        new_calls: list[SecurityCallInfo] = []
        cloned_any = False
        for sec in sec_calls:
            containing = getattr(sec, "containing_func", "") or ""
            tf_node = getattr(sec, "timeframe", None)
            if not containing or not isinstance(tf_node, Identifier):
                new_calls.append(sec)
                continue
            param_name = tf_node.name
            fdef = func_defs.get(containing)
            if fdef is None or param_name not in fdef.params:
                new_calls.append(sec)
                continue
            pidx = fdef.params.index(param_name)
            calls = list(self._iter_user_func_calls(containing))
            if not calls:
                new_calls.append(sec)  # dead code — evaluator result never read
                continue
            # Number call sites for THIS function. If ``containing`` already
            # has TA call sites or series/var members, the per-call-site
            # UDF-body-cloning mechanism already numbered its call sites in
            # _func_call_cs_map (a request.security nested ta.* registers as
            # part of the function-body walk, same as a top-level ta.* call)
            # — reuse that authoritative numbering so this clone's
            # callsite_idx stays aligned with self._active_call_site_idx.
            # Otherwise (e.g. ``f(tf) => request.security(sym, tf, close)``
            # with no nested ta.* call) that mechanism never fired for this
            # function at all, since it gates on has_ta/has_series; assign
            # fresh cs_idx ourselves in call-site order and BACKFILL
            # func_call_cs_map / func_call_site_count / func_security_clone_only
            # so the codegen actually clones this function's body too (the
            # has_ta/has_series emission gate ORs in func_security_clone_only
            # — see codegen/base.py) and the existing top-level call-site
            # naming (which keys purely off func_call_cs_map, not
            # has_ta/has_series) picks the right ``_cs{N}`` variant.
            already_tracked = self._func_call_site_count.get(containing, 0) > 0
            per_cs: list[tuple[int, str | None]] = []
            for i, call in enumerate(calls):
                if already_tracked:
                    cs_info = self._func_call_cs_map.get(id(call))
                    if cs_info is None or cs_info[0] != containing:
                        continue  # shouldn't happen: has_ta/has_series tracks ALL call sites
                    cs_idx = cs_info[1]
                else:
                    cs_idx = i
                    self._func_call_cs_map.setdefault(id(call), (containing, cs_idx))
                arg = call.args[pidx] if pidx < len(call.args) else None
                lit = self._callsite_tf_literal_value(arg)
                per_cs.append((cs_idx, lit))
            if not per_cs:
                new_calls.append(sec)  # dead code — evaluator result never read
                continue
            distinct_literals = {lit for _, lit in per_cs if lit is not None}
            if len(distinct_literals) < 2:
                new_calls.append(sec)  # single TF (or unresolved) — no cloning needed
                continue
            if any(lit is None for _, lit in per_cs):
                # Some call site's tf isn't a compile-time literal — can't
                # pin every clone to a concrete timeframe. Keep the original
                # deterministic rejection rather than guess.
                self._error(
                    "request.security timeframe parameter '"
                    + param_name
                    + "' of function '"
                    + containing
                    + "' is called with multiple distinct literal timeframes ("
                    + ", ".join(sorted(distinct_literals))
                    + "). A single request.security evaluator cannot serve "
                    "them all and would silently collapse onto the chart "
                    "timeframe. Pass a single timeframe, or inline a separate "
                    "request.security call at each call site.",
                    tf_node.loc,
                )
                new_calls.append(sec)
                continue
            if not already_tracked:
                # First thing establishing per-call-site identity for this
                # function — make it authoritative so the codegen's
                # function-emission gate (has_ta or has_series or
                # func_security_clone_only) actually clones its body, with
                # self._active_call_site_idx set to each of our cs_idx values
                # in turn while it does.
                self._func_call_site_count[containing] = len(calls)
                self._func_security_clone_only.add(containing)
            # Clone: one SecurityCallInfo per call site, each pinned to that
            # site's literal timeframe via a synthetic StringLiteral (so the
            # existing literal-timeframe resolution path needs no changes)
            # and given a fresh, currently-unused sec_id.
            next_sec_id = max((s.sec_id for s in sec_calls), default=-1) + 1
            next_sec_id = max(next_sec_id, len(sec_calls) + len(new_calls))
            for cs_idx, lit in sorted(per_cs):
                clone = SecurityCallInfo(
                    sec_id=next_sec_id,
                    timeframe=StringLiteral(value=lit, loc=tf_node.loc),
                    expression=sec.expression,
                    returns_tuple=sec.returns_tuple,
                    tuple_size=sec.tuple_size,
                    tuple_element_types=sec.tuple_element_types,
                    gaps=sec.gaps,
                    lookahead=sec.lookahead,
                    ta_range=sec.ta_range,
                    depends_on_mutable_globals=sec.depends_on_mutable_globals,
                    mutable_globals=sec.mutable_globals,
                    is_lower_tf_array=sec.is_lower_tf_array,
                    containing_func=sec.containing_func,
                    callsite_idx=cs_idx,
                )
                new_calls.append(clone)
                next_sec_id += 1
            cloned_any = True

        if cloned_any:
            # A freshly-backfilled func_call_site_count[containing] may need
            # to cascade to a sub-function containing's body calls (the same
            # propagation _propagate_call_site_counts() already did once
            # before this method ran) — re-run it now that backfill exists.
            self._propagate_call_site_counts()
            # Renumber sec_id contiguously 0..N-1 in final list order — the
            # codegen indexes _security_eval_info / register_security_eval /
            # _eval_security_N by sec_id, all assumed dense from registration
            # order. The provisional IDs assigned above only needed to be
            # distinct from each other while building new_calls; this final
            # pass is what callers (e.g. the codegen's expr_node identity
            # lookup, which now also keys off callsite_idx) actually see.
            for i, sec in enumerate(new_calls):
                sec.sec_id = i
            self._security_calls = new_calls

    def _iter_user_func_calls(self, func_name: str):
        """Yield every ``func_name(...)`` call anywhere in the AST (top-level
        and nested inside function bodies)."""
        def _walk(node):
            if node is None:
                return
            if (isinstance(node, FuncCall) and isinstance(node.callee, Identifier)
                    and node.callee.name == func_name):
                yield node
            for attr_val in vars(node).values():
                if isinstance(attr_val, list):
                    for item in attr_val:
                        if hasattr(item, "__dict__"):
                            yield from _walk(item)
                elif attr_val is not None and hasattr(attr_val, "__dict__"):
                    yield from _walk(attr_val)
        yield from _walk(self._ast)

    def _callsite_tf_literal_value(self, arg) -> str | None:
        """Resolve a UDF call-site timeframe argument to a literal string
        value when it is statically known: a string literal, or a known
        constant / input-backed variable whose stored value is a string.
        Returns None for anything that is not a compile-time string."""
        if isinstance(arg, StringLiteral):
            return arg.value
        if isinstance(arg, Identifier):
            sym = self._symbols.resolve(arg.name)
            if sym is not None and getattr(sym, "const_value", None) is not None:
                val = sym.const_value
                if isinstance(val, str):
                    return val
        return None

    def _is_static_expression(self, node: ASTNode | None) -> bool:
        if node is None:
            return True

        if isinstance(node, (NumberLiteral, StringLiteral, BoolLiteral, NaLiteral, ColorLiteral)):
            return True

        if isinstance(node, Identifier):
            if node.name in ("open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "time", "time_close"):
                return True
            if node.name in self._static_vars:
                return True
            sym = self._symbols.resolve(node.name)
            if sym is not None:
                if sym.is_const or node.name.startswith("input"):
                    return True
                if getattr(sym, "is_static_series", False):
                    return True
            return False

        if isinstance(node, MemberAccess):
            if isinstance(node.object, Identifier):
                if node.object.name.startswith("input") or node.object.name in self._enum_defs:
                    return True
            return self._is_static_expression(node.object)

        if isinstance(node, BinOp):
            return self._is_static_expression(node.left) and self._is_static_expression(node.right)

        if isinstance(node, UnaryOp):
            return self._is_static_expression(node.operand)

        if isinstance(node, Ternary):
            return (self._is_static_expression(node.condition) and
                    self._is_static_expression(node.true_val) and
                    self._is_static_expression(node.false_val))

        if isinstance(node, Subscript):
            return self._is_static_expression(node.object) and self._is_static_expression(node.index)

        if isinstance(node, TupleLiteral):
            return all(self._is_static_expression(elem) for elem in node.elements)

        if isinstance(node, FuncCall):
            if isinstance(node.callee, MemberAccess) and isinstance(node.callee.object, Identifier):
                ns = node.callee.object.name
                if ns in ("math", "str", "color"):
                    return all(self._is_static_expression(arg) for arg in node.args)
            return False

        return False

    def _get_target_base_name(self, target: ASTNode) -> str | None:
        if isinstance(target, Identifier):
            return target.name
        if isinstance(target, MemberAccess):
            return self._get_target_base_name(target.object)
        if isinstance(target, Subscript):
            return self._get_target_base_name(target.object)
        return None

    # ------------------------------------------------------------------
    # Visitor dispatch
    # ------------------------------------------------------------------

    def _visit(self, node: ASTNode | None) -> PineType:
        """Dispatch to the appropriate visitor and return the inferred type."""
        if node is None:
            return PineType.VOID

        method_name = f"_visit_{type(node).__name__}"
        # Convert CamelCase to snake_case for method lookup
        visitor = getattr(self, method_name, None)
        if visitor is not None:
            return visitor(node)

        # Fallback: try generic visit
        return PineType.VOID

    # ------------------------------------------------------------------
    # Top-level visitors
    # ------------------------------------------------------------------

    def _visit_Program(self, node: Program) -> PineType:
        for stmt in node.body:
            prev_top = self._current_top_level_stmt
            self._current_top_level_stmt = stmt
            self._visit(stmt)
            self._current_top_level_stmt = prev_top
        return PineType.VOID

    def _visit_StrategyDecl(self, node: StrategyDecl) -> PineType:
        # Extract strategy parameters
        if node.args:
            # First arg is title
            title_node = node.args[0]
            if isinstance(title_node, StringLiteral):
                self._strategy_params["title"] = title_node.value
        for key, val_node in node.kwargs.items():
            self._strategy_params[key] = self._extract_literal_value(val_node)
        return PineType.VOID

    def _visit_ImportStmt(self, node: ImportStmt) -> PineType:
        loc = node.loc or SourceLocation(file=self._filename, line=1, col=1, end_col=1)
        diag = Diagnostic(
            level=Level.ERROR,
            phase=Phase.ANALYZER,
            location=loc,
            message=f"Import is not supported: '{node.path}'",
        )
        raise CompileError([diag])

    # ------------------------------------------------------------------
    # Declaration / assignment visitors
    # ------------------------------------------------------------------

    def _udt_name_from_ctor(self, value: ASTNode) -> str | None:
        """If value is ``TypeName.new(...)`` for a user-defined type OR a
        drawing handle (``label.new``/``line.new``/``box.new``/``linefill.new``),
        return the type name."""
        if not isinstance(value, FuncCall):
            return None
        cal = value.callee
        if not isinstance(cal, MemberAccess) or not isinstance(cal.object, Identifier):
            return None
        owner = cal.object.name
        m = cal.member
        if not (m == "new" or (isinstance(m, str) and m.startswith("new"))):
            return None
        # Drawing-objects-as-data: label.new(...)/line.new(...)/... return a
        # handle of the self-type. These are not in _udt_fields (they are not
        # user UDTs) but must still be recognised so a function whose body ends
        # in label.new(...) emits a ``Label`` (not ``double``) return type.
        from .types import _DRAWING_TYPE_NAMES
        if owner in _DRAWING_TYPE_NAMES:
            return owner
        if owner not in self._udt_fields:
            return None
        return owner

    def _udt_name_from_nullable_ctor_selection(
        self, value: ASTNode | None
    ) -> str | None:
        """Exact user-UDT type for ctor-only nullable selections.

        Keep this deliberately narrower than generic UDT expression
        inference.  In particular, a terminal ``array.get(...UDT...)`` is a
        reference-identity surface with its own fail-closed rules; treating
        every UDT-valued expression selected against ``na`` as a by-value
        return would accidentally bypass those rules.
        """
        nullable = object()

        def terminal(body: list[ASTNode]) -> ASTNode | None:
            if not body:
                return None
            node = body[-1]
            return node.expr if isinstance(node, ExprStmt) else node

        def resolve(node: ASTNode | None) -> str | None | object:
            if node is None:
                return nullable
            if isinstance(node, ExprStmt):
                return resolve(node.expr)
            if isinstance(node, NaLiteral):
                return nullable
            direct = self._udt_name_from_ctor(node)
            if direct in self._udt_fields:
                return direct
            if isinstance(node, Ternary):
                return merge((resolve(node.true_val), resolve(node.false_val)))
            if isinstance(node, IfStmt):
                return merge((
                    resolve(terminal(node.body)),
                    resolve(terminal(node.else_body)),
                ))
            if isinstance(node, SwitchStmt):
                results = [
                    resolve(terminal(branch))
                    for _case, branch in node.cases
                ]
                results.append(resolve(terminal(node.default_body)))
                return merge(results)
            return None

        def merge(results) -> str | None | object:
            resolved = list(results)
            if any(item is None for item in resolved):
                return None
            concrete = {item for item in resolved if item is not nullable}
            if not concrete:
                return nullable
            return next(iter(concrete)) if len(concrete) == 1 else None

        result = resolve(value)
        return result if isinstance(result, str) else None

    def _func_terminal_drawing_type(self, func_node: FuncDef) -> str | None:
        """Resolve the drawing-handle / UDT type of a function's terminal
        (return) expression for cases the direct ``_udt_name_from_ctor`` on the
        last statement misses:

          - the last statement is an ``IfStmt`` whose terminal branch yields a
            drawing/UDT constructor (``makeEventLabel`` => ``if cond\\n
            label.new(...)``); and
          - the last statement is a bare ``Identifier`` bound to a
            drawing-handle local (``setTradeLine`` => ``line result = ...`` then
            a trailing ``result``).

        Returns the drawing/UDT type name, or ``None``. Without this a function
        that returns a ``line``/``label`` handle this way is mis-typed
        ``double`` and clang rejects ``no viable conversion from Line to
        double``.
        """
        from .types import _DRAWING_TYPE_NAMES

        body = func_node.body
        if not body:
            return None

        # Build a source-ordered lexical binding map.  ``None`` is an explicit
        # non-drawing tombstone: it prevents a same-named drawing declaration
        # from a nested or earlier block from poisoning a later scalar return.
        bindings: dict[str, str | None] = {}
        param_hints = (func_node.annotations or {}).get("param_type_hints", [])
        for i, p in enumerate(func_node.params):
            hint = param_hints[i] if i < len(param_hints) else None
            bindings[p] = hint if hint in _DRAWING_TYPE_NAMES else None

        def _decl_drawing_type(stmt: VarDecl) -> str | None:
            if stmt.type_hint in _DRAWING_TYPE_NAMES:
                return stmt.type_hint
            direct = self._udt_name_from_ctor(stmt.value)
            if direct in _DRAWING_TYPE_NAMES:
                return direct
            exact = self._var_member_type_specs_by_node.get(id(stmt))
            if (exact is not None
                    and exact.kind == "udt"
                    and exact.name in _DRAWING_TYPE_NAMES):
                return exact.name
            spec = self._type_spec_from_expr(stmt.value)
            if (spec is not None
                    and spec.kind == "udt"
                    and spec.name in _DRAWING_TYPE_NAMES):
                return spec.name
            return None

        def _scan_direct_prefix(
            stmts: list[ASTNode],
            env: dict[str, str | None],
        ) -> None:
            # Declarations inside nested control-flow bodies never leak into
            # this lexical environment.  Their own branch environment is
            # created only when that control node is the terminal expression.
            for stmt in stmts:
                if isinstance(stmt, VarDecl):
                    env[stmt.name] = _decl_drawing_type(stmt)
                elif isinstance(stmt, TupleAssign):
                    for name in stmt.names:
                        env[name] = None

        terminal_na = object()

        def _resolve_terminal(
            stmt: ASTNode,
            env: dict[str, str | None],
        ) -> str | None | object:
            if isinstance(stmt, ExprStmt):
                return _resolve_terminal(stmt.expr, env)
            if (isinstance(stmt, NaLiteral)
                    or (isinstance(stmt, Identifier)
                        and stmt.name == "na")):
                return terminal_na
            if isinstance(stmt, IfStmt):
                branch_results: list[str | None | object] = []
                for branch in (stmt.body, stmt.else_body):
                    if not branch:
                        continue  # implicit na arm inherits the drawing type
                    branch_env = dict(env)
                    _scan_direct_prefix(branch[:-1], branch_env)
                    branch_results.append(
                        _resolve_terminal(branch[-1], branch_env)
                    )
                concrete = [
                    item for item in branch_results if item is not terminal_na
                ]
                if not concrete:
                    return terminal_na
                first = concrete[0]
                return (
                    first
                    if all(item == first for item in concrete)
                    else None
                )
            if isinstance(stmt, SwitchStmt):
                branch_results: list[str | None | object] = []
                for _case_expr, branch in stmt.cases:
                    if not branch:
                        continue
                    branch_env = dict(env)
                    _scan_direct_prefix(branch[:-1], branch_env)
                    branch_results.append(
                        _resolve_terminal(branch[-1], branch_env)
                    )
                if stmt.default_body:
                    branch_env = dict(env)
                    _scan_direct_prefix(stmt.default_body[:-1], branch_env)
                    branch_results.append(
                        _resolve_terminal(stmt.default_body[-1], branch_env)
                    )
                concrete = [
                    item for item in branch_results if item is not terminal_na
                ]
                if not concrete:
                    return terminal_na
                first = concrete[0]
                return (
                    first
                    if all(item == first for item in concrete)
                    else None
                )
            if isinstance(stmt, VarDecl):
                return _decl_drawing_type(stmt)
            if isinstance(stmt, Identifier):
                return env.get(stmt.name)
            direct = self._udt_name_from_ctor(stmt)
            if direct in _DRAWING_TYPE_NAMES:
                return direct
            spec = self._type_spec_from_expr(stmt)
            if (spec is not None
                    and spec.kind == "udt"
                    and spec.name in _DRAWING_TYPE_NAMES):
                return spec.name
            return None

        _scan_direct_prefix(body[:-1], bindings)
        resolved = _resolve_terminal(body[-1], bindings)
        return resolved if isinstance(resolved, str) else None

    def _record_collection_type(
        self,
        name: str,
        spec: TypeSpec,
        *,
        symbol_scope: str | None = None,
    ) -> None:
        """Record collection metadata without erasing another callable's local.

        ``symbol_scope`` is supplied for reassignments so a UDF that mutates a
        top-level collection keeps updating the top-level binding.  A VarDecl
        inside an active callable is necessarily lexical to that callable.
        UDT TypeSpecs retain their established registry path; this overlay is
        intentionally limited to array/map/matrix dispatch.
        """
        callable_key = (
            self._collection_scope_stack[-1]
            if self._collection_scope_stack
            else None
        )
        if (callable_key is not None
                and symbol_scope != "global"
                and self._block_node_stack):
            owner_id = id(self._block_node_stack[-1])
            self._block_collection_owners[owner_id] = callable_key
            self._block_collection_types.setdefault(owner_id, {})[name] = (
                spec if spec.kind in {"array", "map", "matrix"} else None
            )
            if spec.kind in {"array", "map", "matrix"}:
                return
        if spec.kind not in {"array", "map", "matrix"}:
            # Primitive locals have no collection metadata to export and must
            # not overwrite a same-named top-level collection.  Preserve the
            # established UDT registry path until UDT identity gets its own
            # lexical migration.
            if (callable_key is not None
                    and symbol_scope != "global"
                    and spec.kind == "primitive"):
                return
            self._collection_types[name] = spec
            return
        if callable_key is not None and symbol_scope != "global":
            self._func_collection_types.setdefault(callable_key, {})[name] = spec
            return
        self._collection_types[name] = spec

    def _visit_VarDecl(self, node: VarDecl) -> PineType:
        outer_symbol = self._symbols.resolve(node.name)
        outer_spec = (
            getattr(outer_symbol, "type_spec", None)
            if outer_symbol is not None
            else self._collection_types.get(node.name)
        )
        # Infer type from the value expression
        val_type = self._visit(node.value)
        type_spec = self._type_spec_from_hint(node.type_hint) if node.type_hint else None
        if type_spec is None:
            type_spec = self._type_spec_from_expr(node.value)
        if self._collection_scope_stack:
            self._callable_collection_bindings[id(node)] = (
                type_spec
                if type_spec is not None
                    and type_spec.kind in {"array", "map", "matrix"}
                else None
            )
            self._callable_collection_binding_owners[id(node)] = (
                self._collection_scope_stack[-1]
            )

        # Check for type hint override
        if node.type_hint:
            hint_type = self._type_hint_to_pine(node.type_hint)
            if hint_type != PineType.UNKNOWN:
                val_type = hint_type

        # Check for input calls
        is_const = False
        const_value = None
        enum_type_name: str | None = None
        if isinstance(node.value, FuncCall):
            ic = self._check_input_call(node.value)
            if ic is not None:
                val_type, is_const, const_value = ic
            enum_type_name = self._input_enum_type_name(node.value)

        udt_ctor = self._udt_name_from_ctor(node.value)
        # User function return propagation: if the value is a call to a user
        # function whose body returns ``T.new(...)``, the local picks up
        # type ``T``. Without this the caller's symbol would track as
        # ``double``, breaking ``s.score()`` dispatch downstream. Probe:
        # data/validation/udt-method-probe-20-udt-return-from-func.
        if udt_ctor is None and isinstance(node.value, FuncCall):
            cal = node.value.callee
            if isinstance(cal, Identifier):
                udt_ctor = self._func_udt_return_types.get(cal.name)
                if udt_ctor is not None and type_spec is None:
                    type_spec = TypeSpec.udt(udt_ctor)

        self._var_decl_types_by_node[id(node)] = (val_type, type_spec)

        if self._global_scope:
            if self._is_static_expression(node.value):
                self._static_vars.add(node.name)
            else:
                self._static_vars.discard(node.name)
        else:
            self._static_vars.discard(node.name)

        loc = node.loc or SourceLocation(file=self._filename, line=1, col=1, end_col=1)
        sym = Symbol(
            name=node.name,
            pine_type=val_type,
            is_series=False,
            is_var=node.is_var or node.is_varip,
            is_const=is_const,
            const_value=const_value,
            scope=self._symbols.current_scope.name,
            loc=loc,
            enum_type_name=enum_type_name,
            udt_type_name=udt_ctor,
            type_spec=type_spec,
        )
        # A direct persistent primitive in an ordinary UDF can safely shadow
        # a top-level map once post-analysis owner qualification gives it exact
        # storage.  Other scalar/map shadows still need the legacy fail-closed
        # marker because their scoped Series identity is unresolved.
        if (
            not self._global_scope
            and self._type_spec_contains_map(outer_spec)
            and not self._type_spec_contains_map(type_spec)
            and not (
                (node.is_var or node.is_varip)
                and not self._block_node_stack
                and bool(self._collection_scope_stack)
                and self._collection_scope_stack[-1] in self._func_defs
                and (
                    node.name in self._ordinary_global_binding_names
                    or any(
                        meta[1] == node.name
                        for meta in self._var_member_metadata_by_node.values()
                    )
                )
            )
        ):
            setattr(sym, "_pf_shadows_map_state", True)
        if node.name in self._static_vars:
            setattr(sym, "is_static_series", True)
        self._symbols.define(sym)
        setattr(sym, "_pf_decl_node_id", id(node))
        setattr(sym, "_pf_decl_binding_name", node.name)
        if (type_spec is None
                and self._collection_scope_stack
                and self._block_node_stack
                and self._symbols.current_scope.name != "global"):
            owner_id = id(self._block_node_stack[-1])
            self._block_collection_owners[owner_id] = self._collection_scope_stack[-1]
            self._block_collection_types.setdefault(owner_id, {})[node.name] = None
        if udt_ctor is not None:
            self._udt_var_types[node.name] = udt_ctor
        if type_spec is not None:
            self._record_collection_type(
                node.name,
                type_spec,
                symbol_scope=self._symbols.current_scope.name,
            )
            if type_spec.kind == "udt" and type_spec.name:
                self._udt_var_types[node.name] = type_spec.name

        # Track var members
        if node.is_var or node.is_varip:
            init_str = self._expr_to_str(node.value)
            # Block-scoped var name-collision disambiguation. A ``var``/``varip``
            # declared inside any non-global block (an ``if`` / ``for`` /
            # ``while`` body, including one nested in a callable) is keyed by
            # RAW name. Two sibling blocks declaring the same name would dedupe
            # to ONE C++ member and cross-contaminate (proven:
            # egoigor1976-1-trendline-strategy's ``var bool valid`` in the
            # upper- and lower-trendline ``if`` blocks).
            # When such a name already belongs to a DIFFERENT block, mint a
            # scope-unique member name and record the rename so codegen activates
            # it (via ``_active_var_remap``) while emitting that block.
            member_name = node.name
            is_block_scoped = (
                not self._global_scope
                and bool(self._block_node_stack)
            )
            if is_block_scoped:
                block_id = id(self._block_node_stack[-1])
                owner = self._block_var_owner.get(node.name)
                if owner is None:
                    # First block to claim this name keeps the raw member name.
                    self._block_var_owner[node.name] = block_id
                elif owner != block_id:
                    # Sibling-scope collision: disambiguate this declaration.
                    self._block_var_seq += 1
                    member_name = f"{node.name}__blk{self._block_var_seq}"
                    self._block_var_renames.setdefault(block_id, {})[node.name] = member_name
            self._var_members.append((member_name, val_type, init_str))
            scope_cursor = self._symbols.current_scope
            is_callable_scoped = False
            callable_owner: str | None = None
            while scope_cursor is not None:
                if scope_cursor.name.startswith(("func_", "method_")):
                    is_callable_scoped = True
                    callable_owner = (
                        self._collection_scope_stack[-1]
                        if self._collection_scope_stack
                        else scope_cursor.name.split("_", 1)[1]
                    )
                    break
                scope_cursor = scope_cursor.parent
            self._var_member_metadata_by_node[id(node)] = (
                node, member_name, val_type, init_str, is_callable_scoped,
            )
            self._var_member_type_specs_by_node[id(node)] = type_spec
            self._var_member_owners_by_node[id(node)] = callable_owner
            # Preserve the emitted storage identity on the lexical Symbol so a
            # later history read can mark the exact sibling member rather than
            # only the legacy raw spelling.
            setattr(sym, "_pf_var_member_name", member_name)
            # Capture the init AST too so codegen can inspect the RHS callee
            # (used to detect int64-returning builtins like ``time()`` and
            # promote the symbol storage type to ``int64_t``).
            if node.value is not None:
                self._var_member_init_exprs[member_name] = node.value
            # Track callable-scoped var members under the analyzer's canonical
            # owner identity.  Ordinary UDFs use ``name`` and UDT methods use
            # ``Type.method``; both feed the same written-callsite clone graph.
            if is_callable_scoped and callable_owner is not None:
                if callable_owner not in self._func_var_members:
                    self._func_var_members[callable_owner] = []
                self._func_var_members[callable_owner].append(
                    (member_name, val_type, init_str)
                )

        # Track global-scope non-var declarations (needed as class members
        # so user functions can reference them)
        if (not node.is_var and not node.is_varip
                and self._symbols.current_scope.name == "global"):
            if self._global_scope and not self._block_node_stack:
                self._ordinary_global_binding_info[node.name] = (
                    id(node), val_type, node.value,
                )
            if node.name not in self._series_vars:
                self._global_var_decls.append((node.name, val_type))
                self._global_expr_map[node.name] = node.value

        if self._symbols.current_scope.name == "global":
            self._record_global_binding_stmt(
                node.name,
                val_type,
                node.is_var or node.is_varip,
                decl_node=node,
            )

        return val_type

    def _visit_Assignment(self, node: Assignment) -> PineType:
        # Visit the value first
        val_type = self._visit(node.value)

        udt_ctor = self._udt_name_from_ctor(node.value)
        if isinstance(node.target, Identifier) and udt_ctor is not None:
            self._udt_var_types[node.target.name] = udt_ctor
        if isinstance(node.target, Identifier):
            spec = self._type_spec_from_expr(node.value)
            if spec is not None:
                target_sym = self._symbols.resolve(node.target.name)
                self._record_collection_type(
                    node.target.name,
                    spec,
                    symbol_scope=(target_sym.scope if target_sym is not None else None),
                )

        # Resolve the target
        if isinstance(node.target, Identifier):
            sym = self._symbols.resolve(node.target.name)
            if sym is None:
                self._error(
                    f"Undefined variable: '{node.target.name}'",
                    node.loc or node.target.loc,
                )
            else:
                if sym.scope == "global":
                    self._global_reassigned_names.add(node.target.name)
                    self._record_global_binding_stmt(
                        node.target.name,
                        sym.pine_type,
                        sym.is_var,
                    )
                if self._global_scope:
                    if self._is_static_expression(node.value):
                        self._static_vars.add(node.target.name)
                        setattr(sym, "is_static_series", True)
                    else:
                        self._static_vars.discard(node.target.name)
                        if hasattr(sym, "is_static_series"):
                            delattr(sym, "is_static_series")
                else:
                    self._static_vars.discard(node.target.name)
                    if hasattr(sym, "is_static_series"):
                        delattr(sym, "is_static_series")
        else:
            self._visit(node.target)
            base_name = self._get_target_base_name(node.target)
            if base_name:
                self._static_vars.discard(base_name)
                sym = self._symbols.resolve(base_name)
                if sym and hasattr(sym, "is_static_series"):
                    delattr(sym, "is_static_series")

        return val_type

    def _visit_TupleAssign(self, node: TupleAssign) -> PineType:
        val_type = self._visit(node.value)
        loc = node.loc or SourceLocation(file=self._filename, line=1, col=1, end_col=1)
        element_types = self._tuple_element_types_by_node.get(id(node.value), ())

        is_val_static = self._is_static_expression(node.value)

        for idx, name in enumerate(node.names):
            if name == "_":
                continue

            inferred_element_type = (
                element_types[idx]
                if idx < len(element_types)
                else PineType.FLOAT
            )
            # Tuple bindings historically use double storage for every
            # numeric element, including integer literals. Preserve that
            # contract while retaining the newly-authoritative bool family.
            element_type = (
                PineType.BOOL
                if inferred_element_type == PineType.BOOL
                else PineType.FLOAT
            )

            if self._global_scope and is_val_static:
                self._static_vars.add(name)
            else:
                self._static_vars.discard(name)

            sym = Symbol(
                name=name,
                pine_type=element_type,
                is_series=False,
                is_var=False,
                is_const=False,
                const_value=None,
                scope=self._symbols.current_scope.name,
                loc=loc,
            )
            if name in self._static_vars:
                setattr(sym, "is_static_series", True)
            self._symbols.define(sym)
            setattr(sym, "_pf_decl_node_id", id(node))
            setattr(sym, "_pf_decl_binding_name", name)

            if (self._collection_scope_stack
                    and self._block_node_stack
                    and self._symbols.current_scope.name != "global"):
                owner_id = id(self._block_node_stack[-1])
                self._block_collection_owners[owner_id] = self._collection_scope_stack[-1]
                self._block_collection_types.setdefault(owner_id, {})[name] = None

            # Track global-scope tuple-assign targets (e.g.
            # ``[pdH, pdL] = request.security(...)``) as class members so user
            # functions / later references resolve — mirroring _visit_VarDecl.
            # Without this the names are never declared and the C++ errors with
            # "use of undeclared identifier".
            if (self._global_scope
                    and self._symbols.current_scope.name == "global"
                    ):
                self._ordinary_global_binding_info[name] = (
                    id(node), element_type, node.value,
                )
                if name not in self._series_vars:
                    self._global_var_decls.append((name, element_type))
                    self._global_expr_map[name] = node.value
                self._record_global_binding_stmt(
                    name, element_type, False, decl_node=node,
                )

        return val_type

    # ------------------------------------------------------------------
    # Function definition
    # ------------------------------------------------------------------

    def _visit_FuncDef(self, node: FuncDef) -> PineType:
        # Store the function def for later analysis
        self._func_defs[node.name] = node

        # Enter function scope
        self._symbols.enter_scope(f"func_{node.name}")

        # Define parameters. The type is UNKNOWN until inferred from a call
        # site, BUT a declared type hint (``string tf``, ``pivot hi``, ``line[] arr``)
        # is authoritative — record it as the symbol's ``type_spec`` / ``pine_type``
        # so (a) the param emits with the right C++ type and (b) callers passing
        # this param into another function can infer that function's param type
        # (e.g. ``getLineStyle(styleStr)`` where ``styleStr`` is a ``string`` param).
        loc = node.loc or SourceLocation(file=self._filename, line=1, col=1, end_col=1)
        param_hints = (node.annotations or {}).get("param_type_hints", [])
        for i, param in enumerate(node.params):
            hint = param_hints[i] if i < len(param_hints) else None
            pspec = self._type_spec_from_hint(hint) if hint else None
            ptype = self._type_hint_to_pine(hint) if hint else PineType.UNKNOWN
            sym = Symbol(
                name=param,
                pine_type=ptype,
                is_series=False,
                is_var=False,
                is_const=False,
                const_value=None,
                scope=f"func_{node.name}",
                loc=loc,
                type_spec=pspec,
            )
            setattr(sym, "_pf_parameter_owner", node.name)
            self._symbols.define(sym)

        # Record TA counter before visiting body
        ta_start = len(self._ta_call_sites)

        # Visit body to discover return type
        body_type = PineType.VOID
        old_global = self._global_scope
        self._global_scope = False
        self._enclosing_func_params.append(set(node.params))
        self._enclosing_func_names.append(node.name)
        self._collection_scope_stack.append(node.name)
        self._nested_ta_touched = set()
        try:
            for stmt in node.body:
                body_type = self._visit(stmt)
        finally:
            self._global_scope = old_global
            self._collection_scope_stack.pop()
            self._enclosing_func_params.pop()
            self._enclosing_func_names.pop()
            nested_touched = self._nested_ta_touched
            self._nested_ta_touched = None

        # Record exact TA ownership for this function: direct allocations plus
        # targets borrowed from every nested stateful call. Per-owner ctor
        # templates retain forwarded-parameter expressions independently.
        ta_end = len(self._ta_call_sites)
        exact_indices = sorted(
            set(range(ta_start, ta_end)) | set(nested_touched or ())
        )
        if exact_indices:
            self._func_ta_indices[node.name] = exact_indices
            self._func_ta_ranges[node.name] = (
                min(exact_indices), max(exact_indices) + 1
            )
            templates = self._func_ta_ctor_args.setdefault(node.name, {})
            for index in exact_indices:
                templates.setdefault(
                    index, list(self._ta_call_sites[index].ctor_args)
                )

        inferred_param_specs = self._param_type_specs_from_def(node)
        for i, param in enumerate(node.params):
            if i < len(inferred_param_specs) and inferred_param_specs[i] is not None:
                continue
            sym = self._symbols.resolve(param)
            spec = getattr(sym, "type_spec", None) if sym is not None else None
            if spec is not None and i < len(inferred_param_specs):
                inferred_param_specs[i] = spec
        self._func_param_type_specs[node.name] = inferred_param_specs

        # Capture direct terminal map-call metadata before leaving the lexical
        # function scope. Local map variables and typed map parameters are no
        # longer resolvable from the symbol table after ``exit_scope()``. Keep
        # this narrow to map terminals so general/nonterminal inference and
        # generated output remain unchanged.
        terminal_ret_expr = self._direct_terminal_return_expr(node)
        temporary_return_expr = (
            self._direct_terminal_array_temporary_return_expr(
                node, terminal_ret_expr
            )
        )
        if temporary_return_expr is not None:
            self._direct_terminal_array_temporary_exprs[node.name] = (
                temporary_return_expr
            )
        terminal_direct_return_spec = self._type_spec_from_expr(
            terminal_ret_expr
        )
        terminal_map_return = self._terminal_map_call_return(
            terminal_ret_expr,
            {
                name: spec
                for name, spec in zip(node.params, inferred_param_specs)
            },
        )
        terminal_array_get_return = self._terminal_array_get_return(
            terminal_ret_expr,
            {
                name: spec
                for name, spec in zip(node.params, inferred_param_specs)
            },
            terminal_direct_return_spec,
        )

        self._symbols.exit_scope()

        # Detect if function returns a tuple (last stmt is TupleLiteral)
        self._func_returns_tuple[node.name] = False
        self._func_tuple_element_count[node.name] = 0
        self._func_tuple_element_types[node.name] = ()
        if node.body:
            last_stmt = node.body[-1]
            tuple_node = None
            if isinstance(last_stmt, ExprStmt) and isinstance(last_stmt.expr, TupleLiteral):
                tuple_node = last_stmt.expr
            elif isinstance(last_stmt, TupleLiteral):
                tuple_node = last_stmt
            if tuple_node is not None:
                self._func_returns_tuple[node.name] = True
                self._func_tuple_element_count[node.name] = len(tuple_node.elements)
                self._func_tuple_element_types[node.name] = (
                    self._tuple_element_types_by_node.get(id(tuple_node), ())
                )
            elif (
                isinstance(terminal_ret_expr, FuncCall)
                and isinstance(terminal_ret_expr.callee, Identifier)
            ):
                # A direct terminal helper wrapper preserves the callee's
                # tuple shape. Without this metadata request.security treats
                # ``outer(x) => inner(x)`` as scalar and emits std::get against
                # a double result. Keep propagation deliberately direct: more
                # complex conditional/collection return shapes remain outside
                # the supported helper-tuple contract.
                terminal_callee = terminal_ret_expr.callee.name
                if self._func_returns_tuple.get(terminal_callee, False):
                    self._func_returns_tuple[node.name] = True
                    self._func_tuple_element_count[node.name] = (
                        self._func_tuple_element_count.get(terminal_callee, 0)
                    )
                    self._func_tuple_element_types[node.name] = (
                        self._func_tuple_element_types.get(terminal_callee, ())
                    )

        # Re-run direct-wrapper propagation to a fixed point whenever a new
        # definition is analyzed. This makes ``outer()=>inner()`` source-order
        # safe when ``inner`` is defined later: once the callee's literal tuple
        # shape is known, every already-seen wrapper chain is updated before a
        # following request.security call is analyzed.
        changed = True
        while changed:
            changed = False
            for wrapper_name, wrapper_def in self._func_defs.items():
                wrapper_terminal = self._direct_terminal_return_expr(
                    wrapper_def
                )
                if not (
                    isinstance(wrapper_terminal, FuncCall)
                    and isinstance(wrapper_terminal.callee, Identifier)
                ):
                    continue
                callee_name = wrapper_terminal.callee.name
                if not self._func_returns_tuple.get(callee_name, False):
                    continue
                tuple_count = self._func_tuple_element_count.get(
                    callee_name, 0
                )
                tuple_types = self._func_tuple_element_types.get(
                    callee_name, ()
                )
                if (
                    self._func_returns_tuple.get(wrapper_name, False)
                    and self._func_tuple_element_count.get(wrapper_name, 0)
                    == tuple_count
                    and self._func_tuple_element_types.get(wrapper_name, ())
                    == tuple_types
                ):
                    continue
                self._func_returns_tuple[wrapper_name] = True
                self._func_tuple_element_count[wrapper_name] = tuple_count
                self._func_tuple_element_types[wrapper_name] = tuple_types
                for info in self._func_infos:
                    if info.name == wrapper_name:
                        info.returns_tuple = True
                        info.tuple_element_count = tuple_count
                changed = True

        # Detect if the function returns a UDT instance via ``T.new(...)`` —
        # used by codegen to emit the C++ return type as the struct name and
        # to propagate UDT typing onto the caller's local. Probe:
        # data/validation/udt-method-probe-20-udt-return-from-func.
        if node.body:
            ret_expr = terminal_ret_expr
            udt_ret = self._udt_name_from_ctor(ret_expr) if ret_expr is not None else None
            if udt_ret is None:
                udt_ret = self._udt_name_from_nullable_ctor_selection(ret_expr)
            if (udt_ret is None
                    and terminal_direct_return_spec is not None
                    and terminal_direct_return_spec.kind == "udt"):
                from .types import _DRAWING_TYPE_NAMES
                if terminal_direct_return_spec.name in _DRAWING_TYPE_NAMES:
                    udt_ret = terminal_direct_return_spec.name
            if udt_ret is None:
                # Drawing-handle returns wrapped in an if-statement terminal
                # branch (``makeEventLabel``) or returned as a bare drawing-handle
                # local (``setTradeLine``) are not direct ctors on the last
                # expression — resolve them so the function emits the C++ handle
                # type (Line/Label/...) instead of the ``double`` default.
                udt_ret = self._func_terminal_drawing_type(node)
            if udt_ret is not None:
                self._func_udt_return_types[node.name] = udt_ret
            # Array-return inference: a function whose body ends in
            # ``array.from(...)`` / ``array.new<T>(...)`` / a UDT method
            # returning an array returns a ``std::vector<...>``. The coarse
            # PineType return can't represent this, so carry the TypeSpec.
            if ret_expr is not None:
                # This exact spec was captured while the function's lexical
                # symbols were still active.  Re-resolving the terminal after
                # ``exit_scope()`` can bind a same-named top-level collection
                # and falsely turn a scalar UDF return into an array.
                ret_spec = terminal_direct_return_spec
                if ret_spec is not None and ret_spec.kind == "array":
                    self._func_return_type_specs[node.name] = ret_spec
            if (terminal_direct_return_spec is not None
                    and terminal_direct_return_spec.kind == "map"):
                self._func_return_type_specs[node.name] = terminal_direct_return_spec
            if (isinstance(terminal_ret_expr, Identifier)
                    and terminal_direct_return_spec is not None
                    and terminal_direct_return_spec.kind == "primitive"):
                # Preserve the lexical terminal identity before leaving the
                # function. Deferred map propagation must not reinterpret a
                # same-named scalar local through a top-level collection. Keep
                # this to direct identifier returns so unrelated call results
                # (for example ``map.size()``) retain legacy output typing.
                self._func_return_type_specs[node.name] = (
                    terminal_direct_return_spec
                )

        if terminal_map_return is not None:
            body_type, terminal_spec = terminal_map_return
            if terminal_spec is not None:
                self._func_return_type_specs[node.name] = terminal_spec

        if terminal_array_get_return is not None:
            body_type, terminal_spec = terminal_array_get_return
            # Preserve the exact primitive TypeSpec as well as the coarse
            # PineType. Downstream collection construction consults this cache
            # directly; without it, asking for the UDF's element type can
            # re-visit the call and duplicate stateful call sites.
            self._func_return_type_specs[node.name] = terminal_spec

        # Store return type
        self._func_return_types[node.name] = body_type

        # Define the function name in the enclosing scope
        sym = Symbol(
            name=node.name,
            pine_type=body_type,
            is_series=False,
            is_var=False,
            is_const=False,
            const_value=None,
            scope=self._symbols.current_scope.name,
            loc=loc,
        )
        self._symbols.define(sym)

        # A definition just completed may supply the primitive return needed
        # by an earlier direct ``array.from(udf()).get(...)`` reader.  Refresh
        # before the next top-level statement is analyzed so its call site
        # observes the reconciled type.
        self._register_resolved_direct_terminal_array_forward_calls()
        self._refresh_direct_terminal_array_temporary_returns()

        return PineType.VOID

    # ------------------------------------------------------------------
    # UDT visitors
    # ------------------------------------------------------------------

    def _visit_TypeDecl(self, node) -> PineType:
        """Register UDT name and field types in symbol table."""
        loc = node.loc or SourceLocation(file=self._filename, line=1, col=1, end_col=1)
        self._symbols.define(Symbol(
            name=node.name, pine_type=PineType.FLOAT, is_series=False,
            is_var=False, is_const=True, const_value=None,
            scope="global", loc=loc,
        ))
        self._udt_fields[node.name] = {
            f.name: self._type_hint_to_pine(f.type_name) for f in node.fields
        }
        self._udt_field_type_specs[node.name] = {
            f.name: self._type_spec_from_hint(f.type_name) for f in node.fields
            if self._type_spec_from_hint(f.type_name) is not None
        }
        for f in node.fields:
            if f.default:
                self._visit(f.default)
        return PineType.VOID

    def _visit_EnumDecl(self, node) -> PineType:
        """Register user enum (derived type): ordinals in _enum_defs, field strings separately."""
        loc = node.loc or SourceLocation(file=self._filename, line=1, col=1, end_col=1)
        # Enum *type* name in scope uses INT as a placeholder; real typing is via enum_defs.
        self._symbols.define(Symbol(
            name=node.name, pine_type=PineType.INT, is_series=False,
            is_var=False, is_const=True, const_value=None,
            scope="global", loc=loc,
        ))
        self._enum_defs[node.name] = node.members
        # Parallel string payloads (arbitrary per field in TV); str.tostring uses this, not ordinals
        strs: list[str] = []
        for m in node.members:
            av = node.member_values.get(m)
            if isinstance(av, StringLiteral):
                strs.append(av.value)
            else:
                strs.append(m)
        self._enum_member_strings[node.name] = strs
        return PineType.VOID

    def _visit_MethodDef(self, node) -> PineType:
        """Register UDT instance method under a unique key ``TypeName.methodName``."""
        method_key = f"{node.type_name}.{node.name}"
        self._symbols.enter_scope(f"method_{node.type_name}_{node.name}")
        loc = node.loc or SourceLocation(file=self._filename, line=1, col=1, end_col=1)
        param_hints = (node.annotations or {}).get("param_type_hints", [])
        param_types: list[PineType] = []
        param_specs: list = []
        for i, p in enumerate(node.params):
            udt_self = node.type_name if i == 0 else None
            hint = param_hints[i] if i < len(param_hints) else None
            # Only the receiver is required to be typed in Pine methods. Every
            # other omitted type is polymorphic per written call, exactly like
            # a regular UDF parameter; seeding it as FLOAT makes even a single
            # bool/int history call silently coerce through Series<double>.
            ptype = self._type_hint_to_pine(hint) if hint else PineType.UNKNOWN
            pspec = self._type_spec_from_hint(hint) if hint else None
            param_types.append(ptype)
            param_specs.append(pspec)
            sym = Symbol(
                name=p, pine_type=ptype, is_series=False,
                is_var=False, is_const=False, const_value=None,
                scope=self._symbols.current_scope.name, loc=loc,
                udt_type_name=udt_self,
                type_spec=pspec,
            )
            setattr(sym, "_pf_parameter_owner", method_key)
            self._symbols.define(sym)
        ret_type = PineType.VOID
        ta_start = len(self._ta_call_sites)
        old_global = self._global_scope
        self._global_scope = False
        self._enclosing_func_params.append(set(node.params))
        self._enclosing_func_names.append(method_key)
        self._collection_scope_stack.append(method_key)
        previous_nested_ta_touched = self._nested_ta_touched
        self._nested_ta_touched = set()
        terminal_ret_expr = self._direct_terminal_return_expr(node)
        return_type_spec = None
        method_udt_return = None
        try:
            for stmt in node.body:
                ret_type = self._visit(stmt)
            if terminal_ret_expr is not None:
                terminal_spec = self._type_spec_from_expr(terminal_ret_expr)
                if terminal_spec is not None and terminal_spec.kind == "map":
                    return_type_spec = terminal_spec
                method_udt_return = (
                    self._udt_name_from_ctor(terminal_ret_expr)
                    or self._udt_name_from_nullable_ctor_selection(
                        terminal_ret_expr
                    )
                )
                if method_udt_return is not None:
                    return_type_spec = TypeSpec.udt(method_udt_return)
        finally:
            self._global_scope = old_global
            self._collection_scope_stack.pop()
            self._enclosing_func_params.pop()
            self._enclosing_func_names.pop()
            nested_touched = self._nested_ta_touched
            self._nested_ta_touched = previous_nested_ta_touched

        # UDT methods participate in the same stateful call graph as ordinary
        # UDFs. Preserve exact direct and borrowed TA ownership under the
        # canonical ``Type.method`` identity so written call sites can clone it.
        ta_end = len(self._ta_call_sites)
        exact_indices = sorted(
            set(range(ta_start, ta_end)) | set(nested_touched or ())
        )
        if exact_indices:
            self._func_ta_indices[method_key] = exact_indices
            self._func_ta_ranges[method_key] = (
                min(exact_indices), max(exact_indices) + 1
            )
            templates = self._func_ta_ctor_args.setdefault(method_key, {})
            for index in exact_indices:
                templates.setdefault(
                    index, list(self._ta_call_sites[index].ctor_args)
                )
        self._symbols.exit_scope()
        if method_udt_return is not None:
            self._func_udt_return_types[method_key] = method_udt_return

        # Detect tuple return on UDT methods (mirrors the regular FuncDef logic
        # earlier in this file). Without this, codegen emits the method with a
        # scalar return type and clang chokes on the ``std::make_tuple(...)``
        # body. Probe: data/validation/udt-method-probe-17-tuple-return-destructure.
        returns_tuple = False
        tuple_element_count = 0
        if node.body:
            last_stmt = node.body[-1]
            tuple_node = None
            if isinstance(last_stmt, ExprStmt) and isinstance(last_stmt.expr, TupleLiteral):
                tuple_node = last_stmt.expr
            elif isinstance(last_stmt, TupleLiteral):
                tuple_node = last_stmt
            if tuple_node is not None:
                returns_tuple = True
                tuple_element_count = len(tuple_node.elements)

        # Forward the parser-captured per-param defaults onto the FuncInfo so
        # codegen can fill in missing args at UDT-method call sites. Probe:
        # data/validation/udt-method-probe-04-default-param.
        param_defaults = list((node.annotations or {}).get("param_defaults", []))
        # Pad to len(params) for safety when an older parser did not record
        # them (e.g., synthetic MethodDef nodes from tests).
        while len(param_defaults) < len(node.params):
            param_defaults.append(None)
        self._func_param_type_specs[method_key] = list(param_specs)
        fi = FuncInfo(
            name=method_key,
            param_types=param_types,
            return_type=ret_type,
            node=FuncDef(
                name=node.name,
                params=node.params,
                body=node.body,
                is_single_expr=node.is_single_expr,
                annotations=dict(node.annotations or {}),
            ),
            is_udt_method=True,
            udt_type_name=node.type_name,
            returns_tuple=returns_tuple,
            tuple_element_count=tuple_element_count,
            param_defaults=param_defaults,
            param_type_specs=param_specs,
            return_type_spec=return_type_spec,
            udt_return_type=method_udt_return,
        )
        self._func_infos.append(fi)
        return PineType.VOID

    # ------------------------------------------------------------------
    # Control flow
    # ------------------------------------------------------------------

    def _top_level_branch_needs_lexical_scope(
        self, body: list[ASTNode]
    ) -> bool:
        """Whether a top-level branch declares over a direct script binding.

        Functions already own a symbol-table scope and ``for``/``for in``
        create one explicitly.  Top-level if/while/switch branches historically
        reused the global Scope, so a local declaration could overwrite the
        analyzer's outer Symbol even though codegen emits a C++ lexical local.
        Add a child scope only for the collision shape handled by this change;
        every unrelated program keeps the established analysis path.
        """
        if self._collection_scope_stack:
            return False
        for stmt in body:
            if (isinstance(stmt, VarDecl)
                    and stmt.name in self._direct_program_binding_names):
                return True
            if (isinstance(stmt, TupleAssign)
                    and any(
                        name in self._direct_program_binding_names
                        for name in stmt.names
                        if name != "_"
                    )):
                return True
        return False

    def _enter_top_level_branch_scope(
        self, body: list[ASTNode], name: str
    ) -> bool:
        scoped = self._top_level_branch_needs_lexical_scope(body)
        if scoped:
            self._symbols.enter_scope(name)
        return scoped

    def _visit_IfStmt(self, node: IfStmt) -> PineType:
        old_global = self._global_scope
        self._global_scope = False
        try:
            self._visit(node.condition)
            body_type = PineType.VOID
            body_scoped = self._enter_top_level_branch_scope(
                node.body, "top_if"
            )
            self._block_node_stack.append(node.body)
            try:
                for stmt in node.body:
                    body_type = self._visit(stmt)
            finally:
                self._block_node_stack.pop()
                if body_scoped:
                    self._symbols.exit_scope()
            if node.else_body:
                else_scoped = self._enter_top_level_branch_scope(
                    node.else_body, "top_else"
                )
                self._block_node_stack.append(node.else_body)
                try:
                    for stmt in node.else_body:
                        self._visit(stmt)
                finally:
                    self._block_node_stack.pop()
                    if else_scoped:
                        self._symbols.exit_scope()
        finally:
            self._global_scope = old_global
        # If used as expression (x = if ...), return last expr type
        return body_type

    def _visit_ForStmt(self, node: ForStmt) -> PineType:
        outer_symbol = self._symbols.resolve(node.var)
        outer_spec = (
            getattr(outer_symbol, "type_spec", None)
            if outer_symbol is not None
            else self._collection_types.get(node.var)
        )
        self._symbols.enter_scope("for")
        loc = node.loc or SourceLocation(file=self._filename, line=1, col=1, end_col=1)

        # Define loop variable
        sym = Symbol(
            name=node.var,
            pine_type=PineType.INT,
            is_series=False,
            is_var=False,
            is_const=False,
            const_value=None,
            scope="for",
            loc=loc,
            type_spec=TypeSpec.primitive("int"),
        )
        if self._type_spec_contains_map(outer_spec):
            setattr(sym, "_pf_shadows_map_state", True)
        self._symbols.define(sym)
        setattr(sym, "_pf_decl_node_id", id(node))
        setattr(sym, "_pf_decl_binding_name", node.var)

        old_global = self._global_scope
        self._global_scope = False
        self._block_node_stack.append(node)
        try:
            self._visit(node.start)
            self._visit(node.end)
            if node.step:
                self._visit(node.step)
            for stmt in node.body:
                self._visit(stmt)
        finally:
            self._block_node_stack.pop()
            self._global_scope = old_global

        self._symbols.exit_scope()
        return PineType.VOID

    def _visit_ForInStmt(self, node) -> PineType:
        old_global = self._global_scope
        self._global_scope = False
        self._block_node_stack.append(node)
        try:
            self._visit(node.iterable)
            iterable_spec = self._type_spec_from_expr(node.iterable)
            element_spec = (
                iterable_spec.element
                if iterable_spec is not None and iterable_spec.kind == "array"
                else None
            )
            tuple_specs: list[TypeSpec | None] = []
            if iterable_spec is not None and iterable_spec.kind == "map":
                tuple_specs = [iterable_spec.key, iterable_spec.value]
            self._symbols.enter_scope("for_in")
            if node.var:
                loc = node.loc or SourceLocation(file=self._filename, line=1, col=1, end_col=1)
                pine_type = self._element_pine_type(element_spec)
                if pine_type == PineType.VOID:
                    pine_type = PineType.FLOAT
                outer_symbol = self._symbols.resolve(node.var)
                outer_spec = (
                    getattr(outer_symbol, "type_spec", None)
                    if outer_symbol is not None
                    else self._collection_types.get(node.var)
                )
                sym = Symbol(
                    name=node.var, pine_type=pine_type, is_series=False,
                    is_var=False, is_const=False, const_value=None,
                    scope=self._symbols.current_scope.name, loc=loc,
                    type_spec=element_spec,
                )
                if self._type_spec_contains_map(outer_spec):
                    setattr(sym, "_pf_shadows_map_state", True)
                self._symbols.define(sym)
                setattr(sym, "_pf_decl_node_id", id(node))
                setattr(sym, "_pf_decl_binding_name", node.var)
            if node.vars:
                loc = node.loc or SourceLocation(file=self._filename, line=1, col=1, end_col=1)
                for idx, v in enumerate(node.vars):
                    binder_spec = (
                        tuple_specs[idx]
                        if idx < len(tuple_specs)
                        else None
                    )
                    pine_type = self._element_pine_type(binder_spec)
                    if pine_type == PineType.VOID:
                        pine_type = PineType.FLOAT
                    outer_symbol = self._symbols.resolve(v)
                    outer_spec = (
                        getattr(outer_symbol, "type_spec", None)
                        if outer_symbol is not None
                        else self._collection_types.get(v)
                    )
                    sym = Symbol(
                        name=v, pine_type=pine_type, is_series=False,
                        is_var=False, is_const=False, const_value=None,
                        scope=self._symbols.current_scope.name, loc=loc,
                        type_spec=binder_spec,
                    )
                    if self._type_spec_contains_map(outer_spec):
                        setattr(sym, "_pf_shadows_map_state", True)
                    self._symbols.define(sym)
                    setattr(sym, "_pf_decl_node_id", id(node))
                    setattr(sym, "_pf_decl_binding_name", v)
            for stmt in node.body:
                self._visit(stmt)
            self._symbols.exit_scope()
        finally:
            self._block_node_stack.pop()
            self._global_scope = old_global
        return PineType.VOID

    def _visit_WhileStmt(self, node: WhileStmt) -> PineType:
        old_global = self._global_scope
        self._global_scope = False
        body_scoped = self._enter_top_level_branch_scope(
            node.body, "top_while"
        )
        self._block_node_stack.append(node)
        try:
            self._visit(node.condition)
            for stmt in node.body:
                self._visit(stmt)
        finally:
            self._block_node_stack.pop()
            if body_scoped:
                self._symbols.exit_scope()
            self._global_scope = old_global
        return PineType.VOID

    def _visit_SwitchStmt(self, node: SwitchStmt) -> PineType:
        old_global = self._global_scope
        self._global_scope = False
        try:
            if node.expr:
                self._visit(node.expr)
            result_type = PineType.VOID
            for case_expr, case_body in node.cases:
                if case_expr:
                    self._visit(case_expr)
                case_scoped = self._enter_top_level_branch_scope(
                    case_body, "top_switch_case"
                )
                self._block_node_stack.append(case_body)
                try:
                    for stmt in case_body:
                        result_type = self._visit(stmt)
                finally:
                    self._block_node_stack.pop()
                    if case_scoped:
                        self._symbols.exit_scope()
            if node.default_body:
                default_scoped = self._enter_top_level_branch_scope(
                    node.default_body, "top_switch_default"
                )
                self._block_node_stack.append(node.default_body)
                try:
                    for stmt in node.default_body:
                        self._visit(stmt)
                finally:
                    self._block_node_stack.pop()
                    if default_scoped:
                        self._symbols.exit_scope()
        finally:
            self._global_scope = old_global
        # If used as expression (x = switch ...), return last expr type
        return result_type

    def _visit_BreakStmt(self, node: BreakStmt) -> PineType:
        return PineType.VOID

    def _visit_ContinueStmt(self, node: ContinueStmt) -> PineType:
        return PineType.VOID

    # ------------------------------------------------------------------
    # Expression wrapper
    # ------------------------------------------------------------------

    def _visit_ExprStmt(self, node: ExprStmt) -> PineType:
        return self._visit(node.expr)

    # ------------------------------------------------------------------
    # Expression visitors
    # ------------------------------------------------------------------

    def _visit_BinOp(self, node: BinOp) -> PineType:
        left_type = self._visit(node.left)
        right_type = self._visit(node.right)

        # Comparison and logical operators return BOOL
        if node.op in ("==", "!=", ">", "<", ">=", "<=", "and", "or"):
            return PineType.BOOL

        # String concatenation: if either side is STRING, result is STRING
        if left_type == PineType.STRING or right_type == PineType.STRING:
            def _mark_string_param(expr) -> None:
                if not isinstance(expr, Identifier):
                    return
                sym = self._symbols.resolve(expr.name)
                if sym is None or not (sym.scope or "").startswith("func_"):
                    return
                if sym.pine_type == PineType.UNKNOWN:
                    sym.pine_type = PineType.STRING
                    sym.type_spec = TypeSpec.primitive("string")

            if left_type == PineType.STRING:
                _mark_string_param(node.right)
            if right_type == PineType.STRING:
                _mark_string_param(node.left)
            return PineType.STRING

        # Arithmetic: promote to FLOAT if either side is FLOAT
        if left_type == PineType.FLOAT or right_type == PineType.FLOAT:
            return PineType.FLOAT
        if left_type == PineType.INT and right_type == PineType.INT:
            return PineType.INT

        # Division always returns FLOAT
        if node.op == "/":
            return PineType.FLOAT

        return PineType.FLOAT  # default

    def _visit_UnaryOp(self, node: UnaryOp) -> PineType:
        operand_type = self._visit(node.operand)
        if node.op == "not":
            return PineType.BOOL
        return operand_type

    def _visit_Ternary(self, node: Ternary) -> PineType:
        self._visit(node.condition)
        true_type = self._visit(node.true_val)
        false_type = self._visit(node.false_val)

        # String type dominates
        if true_type == PineType.STRING or false_type == PineType.STRING:
            return PineType.STRING
        # Promote types
        if true_type == PineType.FLOAT or false_type == PineType.FLOAT:
            return PineType.FLOAT
        return true_type

    def _visit_FuncCall(self, node: FuncCall) -> PineType:
        # Determine what is being called
        callee = node.callee

        if isinstance(callee, MemberAccess):
            obj = callee.object
            member = callee.member

            # ta.* calls
            if isinstance(obj, Identifier) and obj.name == "ta":
                if member == "sum":
                    self._error(
                        "PineScript has no ta.sum; use math.sum(source, length) for rolling sum",
                        node.loc,
                    )
                return self._handle_ta_call(member, node)

            # strategy.* calls
            if isinstance(obj, Identifier) and obj.name == "strategy":
                return self._handle_strategy_call(member, node)

            # input.* calls
            if isinstance(obj, Identifier) and obj.name == "input":
                return self._handle_input_member_call(member, node)

            # math.* calls
            if isinstance(obj, Identifier) and obj.name == "math":
                # math.sum is a rolling sum — redirect to TA handling
                if member == "sum":
                    return self._handle_ta_call("sum", node)
                # Visit args
                for arg in node.args:
                    self._visit(arg)
                return PineType.FLOAT

            # str.* calls
            if isinstance(obj, Identifier) and obj.name == "str":
                for arg in node.args:
                    self._visit(arg)
                # Most str.* return a string, but predicates and index helpers
                # are scalar. Keep this aligned with signatures.py and the C++
                # emitter's _infer_type path.
                if member in ("contains", "startswith", "endswith"):
                    return PineType.BOOL
                if member == "tonumber":
                    return PineType.FLOAT
                if member in ("length", "pos"):
                    return PineType.INT
                return PineType.STRING

            # request.* calls
            if isinstance(obj, Identifier) and obj.name == "request":
                return self._handle_request_call(member, node)

            # strategy.closedtrades.*(idx) / strategy.opentrades.*(idx).
            # The callee is a nested MemberAccess, so it does not enter the
            # direct ``strategy.*`` branch above.  Its MemberAccess visitor
            # already owns the authoritative accessor return-type mapping;
            # preserve that type for the call instead of falling through to
            # VOID (which later becomes a C++ double declaration).
            if (isinstance(obj, MemberAccess)
                    and isinstance(obj.object, Identifier)
                    and obj.object.name == "strategy"
                    and obj.member in ("closedtrades", "opentrades")):
                for arg in node.args:
                    self._visit(arg)
                for val in node.kwargs.values():
                    self._visit(val)
                return self._visit(callee)

            # General member call (e.g., array.push, etc.)
            receiver_pine_type = self._visit(obj)
            visited_member_arg_types: dict[int, PineType] = {}
            for arg in node.args:
                visited_member_arg_types[id(arg)] = self._visit(arg)
            for val in node.kwargs.values():
                visited_member_arg_types[id(val)] = self._visit(val)
            # UDT method call-site typing.  Method definitions may contain an
            # untyped parameter history read; resolve receiver + args here and
            # apply the same deferred map-history gate as regular UDFs before
            # codegen can emit the parameter as a scalar double.
            receiver_spec = self._type_spec_from_expr(obj)
            if (
                receiver_spec is not None
                and receiver_spec.kind == "udt"
                and receiver_spec.name
            ):
                method_key = f"{receiver_spec.name}.{member}"
                method_info = next(
                    (
                        info
                        for info in self._func_infos
                        if info.name == method_key
                        and getattr(info, "is_udt_method", False)
                    ),
                    None,
                )
                if method_info is not None and method_info.node is not None:
                    method_params = list(method_info.node.params)
                    rest_params = method_params[1:]
                    rest_bound = self._bind_callable_args(node, rest_params)
                    full_bound: list[ASTNode | None] = [obj, *rest_bound]
                    full_param_types = [
                        receiver_pine_type,
                        *[
                            visited_member_arg_types.get(
                                id(arg), PineType.UNKNOWN
                            )
                            if arg is not None
                            else PineType.UNKNOWN
                            for arg in rest_bound
                        ],
                    ]
                    declared_specs = list(
                        self._func_param_type_specs.get(method_key, ())
                    )
                    full_param_types = [
                        (
                            self._primitive_pine_type_from_spec(
                                declared_specs[index]
                            )
                            if index < len(declared_specs)
                            and self._primitive_pine_type_from_spec(
                                declared_specs[index]
                            ) != PineType.UNKNOWN
                            else param_type
                        )
                        for index, param_type in enumerate(full_param_types)
                    ]
                    self._callable_bound_param_types_by_node[id(node)] = list(
                        full_param_types
                    )
                    effective_specs = list(
                        getattr(method_info, "param_type_specs", None) or []
                    )
                    while len(effective_specs) < len(method_params):
                        effective_specs.append(None)
                    for index, arg in enumerate(full_bound):
                        if effective_specs[index] is None and arg is not None:
                            effective_specs[index] = self._type_spec_from_expr(arg)
                    self._record_deferred_param_call_edge(
                        node,
                        method_key,
                        method_params,
                        full_bound,
                    )
                    self._validate_deferred_param_history_refs(
                        method_key,
                        {
                            name: spec
                            for name, spec in zip(
                                method_params, effective_specs
                            )
                        },
                    )
                    method_series = self._func_series_vars.get(
                        method_key, set()
                    )
                    for index, param_name in enumerate(method_params):
                        if (
                            param_name not in method_series
                            or index >= len(full_bound)
                        ):
                            continue
                        arg = full_bound[index]
                        if isinstance(arg, Identifier) and arg.name in BAR_FIELDS:
                            self._series_bar_fields.add(arg.name)
                        elif isinstance(arg, Identifier):
                            arg_sym = self._symbols.resolve(arg.name)
                            if arg_sym is not None:
                                arg_sym.is_series = True
                    # Materialize TA state while the lexical caller is still
                    # active, just as ``_handle_user_func_call`` does for a
                    # bare UDF call.  Deferring every UDT-method call to the
                    # whole-program call-graph pass loses the enclosing
                    # callable's parameter stack: ``outer(self, len) =>
                    # self.inner(len)`` then leaves ``inner``'s constructor
                    # argument as the bare local name ``len`` instead of
                    # threading the eventual top-level input through both
                    # boundaries.  Besides substituting the arguments, the
                    # cs0 path records the borrowed TA site in
                    # ``_nested_ta_touched`` so the caller's range is widened
                    # and can be resolved again at its own call site.
                    if method_key in self._func_ta_ranges:
                        existing_site = self._func_call_cs_map.get(id(node))
                        if (
                            existing_site is None
                            or existing_site[0] != method_key
                        ):
                            cs_idx = self._func_call_site_count.get(
                                method_key, 0
                            )
                            self._func_call_site_count[method_key] = cs_idx + 1
                            self._func_call_cs_map[id(node)] = (
                                method_key, cs_idx
                            )
                            self._materialize_user_func_call_site_state(
                                method_key, cs_idx, node
                            )
                    return self._callsite_callable_return_type(
                        method_info.node,
                        full_param_types,
                        method_info.return_type,
                    )
            # Matrix method dispatch: ``m.get(0, 0)`` on ``matrix<int>`` must
            # type as INT, not VOID, so ``v = m.get(...)`` propagates the
            # element PineType. ``_type_spec_from_expr`` already carries the
            # full TypeSpec for downstream codegen; this branch keeps the
            # legacy PineType-slot consumers (Symbol.pine_type, scalar
            # arithmetic inference) honest. See call_handlers.py
            # ``_handle_matrix_method``.
            if isinstance(obj, Identifier):
                recv_sym = self._symbols.resolve(obj.name)
                recv_spec = (
                    getattr(recv_sym, "type_spec", None)
                    if recv_sym is not None
                    else None
                )
                # Only fall back to the top-level raw registry when lexical
                # resolution did not find a shadowing local/parameter.
                if recv_sym is None or recv_sym.scope == "global":
                    recv_spec = recv_spec or self._collection_types.get(obj.name)
                if recv_spec is not None and recv_spec.kind == "matrix":
                    return self._handle_matrix_method(member, recv_spec)
            return PineType.VOID

        if isinstance(callee, Identifier):
            func_name = callee.name

            # Skip functions (plot, etc.)
            if func_name in SKIP_FUNCS:
                # Still visit args for side effects
                for arg in node.args:
                    self._visit(arg)
                for val in node.kwargs.values():
                    self._visit(val)
                return PineType.VOID

            # input() without qualifier
            if func_name == "input":
                return self._handle_input_call(node)

            # fixnan
            if func_name == "fixnan":
                return self._handle_fixnan_call(node)

            # nz
            if func_name == "nz":
                for arg in node.args:
                    self._visit(arg)
                return PineType.FLOAT

            # na() as function
            if func_name == "na":
                for arg in node.args:
                    self._visit(arg)
                return PineType.BOOL

            # color.* (e.g., color.new, color.rgb)
            if func_name == "color":
                for arg in node.args:
                    self._visit(arg)
                return PineType.COLOR

            # User-defined function call
            if func_name in self._func_defs:
                return self._handle_user_func_call(func_name, node)

            # Built-in functions we don't specifically handle
            # Visit args for side effects
            for arg in node.args:
                self._visit(arg)
            for val in node.kwargs.values():
                self._visit(val)

            # Check if it's a known symbol
            sym = self._symbols.resolve(func_name)
            if sym is not None:
                return sym.pine_type

            return PineType.FLOAT  # default for unknown functions

        # Fallback
        self._visit(callee)
        for arg in node.args:
            self._visit(arg)
        for val in node.kwargs.values():
            self._visit(val)
        return PineType.VOID

    def _type_spec_contains_map(
        self,
        spec: TypeSpec | None,
        visiting_udts: frozenset[str] = frozenset(),
    ) -> bool:
        """Whether a lexical TypeSpec recursively owns a PineMap handle.

        History buffers value-copy their elements. A map ID anywhere in that
        shape would therefore retain live storage across bars and silently
        turn ``[1]`` into a current-state alias. This check belongs in the
        analyzer, where symbol resolution is scope-aware: a scalar parameter
        or block local can safely shadow a same-named global map, while typed
        map parameters and inferred aliases are still rejected.
        """
        if spec is None:
            return False
        if spec.kind == "map":
            return True
        if spec.kind in {"array", "matrix"}:
            return self._type_spec_contains_map(spec.element, visiting_udts)
        if spec.kind == "udt" and spec.name:
            if spec.name in visiting_udts:
                return False
            nested_visiting = visiting_udts | {spec.name}
            return any(
                self._type_spec_contains_map(field_spec, nested_visiting)
                for field_spec in self._udt_field_type_specs.get(
                    spec.name, {}
                ).values()
            )
        return False

    def _history_receiver_type_spec(
        self,
        node: ASTNode,
        parameter_specs_by_node: dict[int, TypeSpec | None] | None = None,
    ) -> TypeSpec | None:
        """Resolve the value shape on the left of Pine's history operator.

        General expression inference intentionally stays conservative.  The
        history safety gate needs two extra, narrow facts: a ternary selecting
        two equal aggregate handles keeps that aggregate type, and an untyped
        parameter can be substituted with the TypeSpec learned at its call
        site.  The substitution is keyed by AST identifier identity so a
        same-spelled local or loop binder is never confused with the parameter.
        """
        overrides = parameter_specs_by_node or {}
        if isinstance(node, Identifier):
            if id(node) in overrides:
                return overrides[id(node)]
            return self._type_spec_from_expr(node)
        if isinstance(node, Ternary):
            true_spec = self._history_receiver_type_spec(
                node.true_val, overrides
            )
            false_spec = self._history_receiver_type_spec(
                node.false_val, overrides
            )
            if true_spec is not None and true_spec == false_spec:
                return true_spec
            # ``na`` is context-typed by the opposite branch in Pine.  Keep
            # this rule local to the history gate rather than broadening all
            # declaration inference.
            if isinstance(node.true_val, NaLiteral):
                return false_spec
            if isinstance(node.false_val, NaLiteral):
                return true_spec
            return None
        if isinstance(node, MemberAccess):
            owner = self._history_receiver_type_spec(node.object, overrides)
            if owner is not None and owner.kind == "udt" and owner.name:
                return (self._udt_field_type_specs.get(owner.name) or {}).get(
                    node.member
                )
            return self._type_spec_from_expr(node)
        if isinstance(node, FuncCall) and isinstance(
            node.callee, MemberAccess
        ):
            callee = node.callee
            receiver = None
            if (
                isinstance(callee.object, Identifier)
                and callee.object.name == "map"
                and node.args
            ):
                receiver = node.args[0]
            elif not (
                isinstance(callee.object, Identifier)
                and callee.object.name in {
                    "array", "matrix", "map", "request", "ta"
                }
            ):
                receiver = callee.object
            if receiver is not None:
                recv_spec = self._history_receiver_type_spec(
                    receiver, overrides
                )
                if recv_spec is not None and recv_spec.kind == "map":
                    if callee.member == "copy":
                        return recv_spec
                    if callee.member in {"put", "get", "remove"}:
                        return recv_spec.value
                    if callee.member == "keys":
                        return TypeSpec.array(
                            recv_spec.key or TypeSpec.primitive("string")
                        )
                    if callee.member == "values":
                        return TypeSpec.array(
                            recv_spec.value or TypeSpec.primitive("float")
                        )
        return self._type_spec_from_expr(node)

    def _parameter_identifiers_in_expr(
        self, node: ASTNode
    ) -> dict[str, dict[int, str]]:
        """Return parameter identifier nodes grouped by their callable owner."""
        grouped: dict[str, dict[int, str]] = {}

        def visit(value) -> None:
            if value is None:
                return
            if isinstance(value, Identifier):
                sym = self._symbols.resolve(value.name)
                owner = getattr(sym, "_pf_parameter_owner", None)
                if owner:
                    grouped.setdefault(owner, {})[id(value)] = value.name
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    visit(item)
                return
            if isinstance(value, dict):
                for item in value.values():
                    visit(item)
                return
            if isinstance(value, ASTNode):
                for child in vars(value).values():
                    visit(child)

        visit(node)
        return grouped

    @staticmethod
    def _bind_callable_args(
        node: FuncCall,
        param_names: list[str],
    ) -> list[ASTNode | None]:
        """Bind positional/keyword AST arguments into declaration order."""
        bound: list[ASTNode | None] = [None] * len(param_names)
        for index, arg in enumerate(node.args):
            if index < len(bound):
                bound[index] = arg
        for name, value in node.kwargs.items():
            if name in param_names:
                bound[param_names.index(name)] = value
        return bound

    def _record_deferred_param_call_edge(
        self,
        call_node: FuncCall,
        callee_owner: str,
        callee_param_names: list[str],
        bound_args: list[ASTNode | None],
    ) -> None:
        """Capture caller-param flow while lexical symbol identity is known."""
        if not self._collection_scope_stack:
            return
        caller_owner = self._collection_scope_stack[-1]
        parameter_nodes: list[dict[int, str]] = []
        has_flow = False
        for arg in bound_args:
            grouped = (
                self._parameter_identifiers_in_expr(arg)
                if arg is not None
                else {}
            )
            current = grouped.get(caller_owner, {})
            parameter_nodes.append(current)
            has_flow = has_flow or bool(current)
        if not has_flow:
            return
        edges = self._deferred_param_call_edges.setdefault(caller_owner, [])
        if any(edge[0] == id(call_node) for edge in edges):
            return
        edges.append(
            (
                id(call_node),
                callee_owner,
                list(callee_param_names),
                list(bound_args),
                parameter_nodes,
            )
        )

    def _reject_unsupported_map_history(
        self, spec: TypeSpec, node: Subscript
    ) -> None:
        if spec.kind == "map":
            message = (
                "History references on map IDs are not supported in "
                "PineForge; map rollback uses identity snapshots rather "
                "than Series<PineMap>."
            )
        else:
            message = (
                "History references on map-bearing UDTs or collections "
                "are not supported in PineForge; their Series value copy "
                "would retain live map aliases."
            )
        self._error(message, node.loc)

    def _validate_deferred_param_history_refs(
        self,
        owner: str,
        parameter_specs: dict[str, TypeSpec | None],
        visiting: frozenset[str] = frozenset(),
    ) -> None:
        """Re-run map-history safety after untyped callable args are known."""
        if owner in visiting:
            return
        next_visiting = visiting | {owner}
        for node, parameter_nodes in self._deferred_param_history_refs.get(
            owner, []
        ):
            overrides = {
                node_id: parameter_specs.get(name)
                for node_id, name in parameter_nodes.items()
            }
            object_spec = self._history_receiver_type_spec(
                node.object, overrides
            )
            if self._type_spec_contains_map(object_spec):
                assert object_spec is not None
                self._reject_unsupported_map_history(object_spec, node)

        # Propagate concrete caller specs through wrapper calls.  Each edge is
        # identity-keyed from the definition pass, so a local that shadows a
        # caller parameter cannot accidentally inherit its map TypeSpec.
        for (
            _call_id,
            callee_owner,
            callee_param_names,
            bound_args,
            parameter_nodes_by_arg,
        ) in self._deferred_param_call_edges.get(owner, []):
            callee_specs: dict[str, TypeSpec | None] = {}
            for index, param_name in enumerate(callee_param_names):
                arg = bound_args[index] if index < len(bound_args) else None
                if arg is None:
                    callee_specs[param_name] = None
                    continue
                parameter_nodes = (
                    parameter_nodes_by_arg[index]
                    if index < len(parameter_nodes_by_arg)
                    else {}
                )
                overrides = {
                    node_id: parameter_specs.get(name)
                    for node_id, name in parameter_nodes.items()
                }
                callee_specs[param_name] = self._history_receiver_type_spec(
                    arg, overrides
                )
            self._validate_deferred_param_history_refs(
                callee_owner,
                callee_specs,
                next_visiting,
            )

    def _propagate_deferred_map_callable_specs(
        self,
        owner: str,
        parameter_specs: dict[str, TypeSpec | None],
        visiting: frozenset[str] = frozenset(),
    ) -> bool:
        """Monomorphize an all-untyped wrapper chain for one concrete map call.

        Function definitions are analyzed before their concrete top-level call
        sites.  Consequently ``wrapper(a) => identity(a)`` initially creates
        both ``FuncInfo`` records with scalar fallbacks.  The definition pass
        already captured identity-keyed parameter-flow edges for deferred map
        history validation; reuse those exact edges to carry a later map
        ``TypeSpec`` inward, then infer map returns on the way back out.

        This is deliberately bounded and map-triggered.  Cycles stop at the
        active owner, incompatible previously-established parameter specs stop
        that edge, and scalar-only call graphs never mutate.  PineForge emits
        one C++ body per ordinary UDF, so silently replacing one concrete map
        specialization with a different one would be a false polymorphic
        inference rather than a valid widening.
        """
        if owner in visiting:
            return False
        if not any(
            spec is not None and spec.kind == "map"
            for spec in parameter_specs.values()
        ):
            return False

        func_info = next(
            (info for info in self._func_infos if info.name == owner),
            None,
        )
        func_def = self._func_defs.get(owner)
        if func_info is None or func_def is None:
            return False

        changed = False
        while len(func_info.param_type_specs) < len(func_def.params):
            func_info.param_type_specs.append(None)
        declared_specs = list(
            self._func_param_type_specs.get(owner)
            or self._param_type_specs_from_def(func_def)
        )
        while len(declared_specs) < len(func_def.params):
            declared_specs.append(None)

        # Refuse to overwrite a declared or previously learned concrete map
        # specialization.  Equal specs and still-unresolved slots are safe.
        for index, param_name in enumerate(func_def.params):
            incoming = parameter_specs.get(param_name)
            if incoming is None:
                continue
            established = declared_specs[index] or func_info.param_type_specs[index]
            if (
                established is not None
                and established != incoming
                and (established.kind == "map" or incoming.kind == "map")
            ):
                self._error(
                    f"User function '{owner}' is called with incompatible "
                    "map parameter types; PineForge cannot emit multiple "
                    "map specializations for one untyped function.",
                    func_def.loc,
                )
        for index, param_name in enumerate(func_def.params):
            incoming = parameter_specs.get(param_name)
            if incoming is None or func_info.param_type_specs[index] is not None:
                continue
            func_info.param_type_specs[index] = incoming
            changed = True

        next_visiting = visiting | {owner}
        edges = self._deferred_param_call_edges.get(owner, [])
        # Nested actual arguments can depend on a sibling edge's newly learned
        # return spec.  A bounded local fixed point removes source-order
        # dependence without turning this into whole-program re-analysis.
        for _ in range(max(1, len(edges) + 1)):
            pass_changed = False
            for (
                _call_id,
                callee_owner,
                callee_param_names,
                bound_args,
                parameter_nodes_by_arg,
            ) in edges:
                callee_specs: dict[str, TypeSpec | None] = {}
                for index, param_name in enumerate(callee_param_names):
                    arg = bound_args[index] if index < len(bound_args) else None
                    if arg is None:
                        callee_specs[param_name] = None
                        continue
                    parameter_nodes = (
                        parameter_nodes_by_arg[index]
                        if index < len(parameter_nodes_by_arg)
                        else {}
                    )
                    overrides = {
                        node_id: parameter_specs.get(name)
                        for node_id, name in parameter_nodes.items()
                    }
                    callee_specs[param_name] = self._history_receiver_type_spec(
                        arg, overrides
                    )
                # A bare ``na`` actual argument acquires the one unambiguous
                # map type carried by its sibling arguments.  This is needed
                # for wrappers such as ``select(c, m) => choose(c, m, na)``;
                # without it the inner untyped parameter remains scalar even
                # though Pine context-types the na handle.  Multiple distinct
                # map specs stay unresolved rather than guessing.
                concrete_map_specs = {
                    spec
                    for spec in callee_specs.values()
                    if spec is not None and spec.kind == "map"
                }
                if len(concrete_map_specs) == 1:
                    contextual_map_spec = next(iter(concrete_map_specs))
                    for index, param_name in enumerate(callee_param_names):
                        arg = (
                            bound_args[index]
                            if index < len(bound_args)
                            else None
                        )
                        if (
                            callee_specs.get(param_name) is None
                            and (
                                isinstance(arg, NaLiteral)
                                or (
                                    isinstance(arg, Identifier)
                                    and arg.name == "na"
                                )
                            )
                        ):
                            callee_specs[param_name] = contextual_map_spec
                if self._propagate_deferred_map_callable_specs(
                    callee_owner,
                    callee_specs,
                    next_visiting,
                ):
                    pass_changed = True
            changed = changed or pass_changed
            if not pass_changed:
                break

        terminal = self._direct_terminal_return_expr(func_def)
        return_spec = None
        scalar_return_type = None
        if isinstance(terminal, Identifier) and terminal.name in parameter_specs:
            candidate = parameter_specs.get(terminal.name)
            if candidate is not None and candidate.kind == "map":
                return_spec = candidate
        if return_spec is None:
            return_spec = self._terminal_map_selection_return_spec(
                terminal, parameter_specs
            )
        if return_spec is None:
            terminal_map_call = self._terminal_map_call_return(
                terminal, parameter_specs
            )
            if terminal_map_call is not None:
                terminal_return_type, candidate = terminal_map_call
                if candidate is not None and candidate.kind == "map":
                    return_spec = candidate
                elif terminal_return_type not in {
                    PineType.UNKNOWN, PineType.VOID
                }:
                    scalar_return_type = terminal_return_type
        if return_spec is None:
            candidate = self._type_spec_from_expr(terminal)
            if candidate is not None and candidate.kind == "map":
                return_spec = candidate
        if return_spec is not None:
            established_return = self._func_return_type_specs.get(owner)
            if established_return is None:
                self._func_return_type_specs[owner] = return_spec
                func_info.return_type_spec = return_spec
                changed = True
            elif established_return == return_spec:
                if func_info.return_type_spec is None:
                    func_info.return_type_spec = return_spec
                    changed = True
            # A different established return belongs to another concrete map
            # specialization.  Keep it unchanged rather than falsely widening.

        if scalar_return_type is None and isinstance(terminal, FuncCall):
            callee = terminal.callee
            callee_name = (
                callee.name if isinstance(callee, Identifier) else None
            )
            callee_info = next(
                (
                    info
                    for info in self._func_infos
                    if info.name == callee_name
                ),
                None,
            )
            if (
                callee_info is not None
                and callee_info.return_type
                not in {PineType.UNKNOWN, PineType.VOID}
            ):
                scalar_return_type = callee_info.return_type
        if (
            return_spec is None
            and scalar_return_type is not None
            and func_info.return_type != scalar_return_type
        ):
            func_info.return_type = scalar_return_type
            self._func_return_types[owner] = scalar_return_type
            changed = True

        return changed

    def _visit_Subscript(self, node: Subscript) -> PineType:
        obj_type = self._visit(node.object)
        self._visit(node.index)

        object_spec = self._history_receiver_type_spec(node.object)
        if self._type_spec_contains_map(object_spec):
            assert object_spec is not None
            self._reject_unsupported_map_history(object_spec, node)

        # An untyped parameter has no aggregate TypeSpec during the function
        # definition pass.  Remember only the identifier nodes that resolved
        # to actual parameters; the call handler validates them before any
        # codegen state is committed.
        for owner, parameter_nodes in self._parameter_identifiers_in_expr(
            node.object
        ).items():
            self._deferred_param_history_refs.setdefault(owner, []).append(
                (node, parameter_nodes)
            )

        # Detect series vars / bar fields
        if isinstance(node.object, Identifier):
            name = node.object.name
            if name in BAR_FIELDS:
                self._series_bar_fields.add(name)
            else:
                sym = self._symbols.resolve(name)
                if sym is not None:
                    if getattr(sym, "_pf_shadows_map_state", False):
                        self._error(
                            "History references on scalar local or loop "
                            "bindings that shadow a map ID are not supported "
                            "until PineForge can allocate a lexically scoped "
                            "Series buffer for that binding.",
                            node.loc,
                        )
                    if getattr(sym, "type_spec", None) is None or sym.type_spec.kind not in ("array", "map"):
                        exact_member = getattr(sym, "_pf_var_member_name", None)
                        if exact_member is not None:
                            self._series_var_members.add(exact_member)
                        decl_node_id = getattr(sym, "_pf_decl_node_id", None)
                        if decl_node_id is not None:
                            self._series_decl_nodes.add(decl_node_id)
                            binding_name = getattr(
                                sym, "_pf_decl_binding_name", name
                            )
                            self._series_decl_bindings.add(
                                (decl_node_id, binding_name)
                            )
                        global_sym = self._symbols.global_scope.symbols.get(name)
                        shadows_map_state = (
                            sym.scope != "global"
                            and global_sym is not None
                            and self._type_spec_contains_map(global_sym.type_spec)
                        )
                        # ``_series_vars`` is a legacy global-by-name set. Do
                        # not let a lexical scalar series parameter poison a
                        # same-named global PineMap member; the scoped series
                        # registry below is authoritative for the function.
                        if not shadows_map_state:
                            self._series_vars.add(name)
                        sym.is_series = True
                        # Track callable-scoped series vars. Method symbol
                        # scopes use ``method_Type_name`` while every later
                        # clone/remap table is keyed by canonical
                        # ``Type.method`` identity.
                        func_name: str | None = None
                        if sym.scope and sym.scope.startswith("func_"):
                            func_name = sym.scope[5:]
                        elif (
                            sym.scope != "global"
                            and self._collection_scope_stack
                        ):
                            func_name = self._collection_scope_stack[-1]
                        if func_name is not None:
                            if func_name not in self._func_series_vars:
                                self._func_series_vars[func_name] = set()
                            self._func_series_vars[func_name].add(name)
                            self._func_series_history_nodes.setdefault(
                                (func_name, name), node
                            )

        return obj_type

    def _visit_Identifier(self, node: Identifier) -> PineType:
        # Some identifiers are namespace prefixes handled elsewhere
        if node.name in ("strategy", "ta", "input", "math", "str", "color",
                         "display", "syminfo", "timeframe", "plot",
                         "alert", "barstate", "position", "shape", "location",
                         "size", "currency", "order", "format", "text",
                         "extend", "xloc", "yloc", "label", "line", "box",
                         "table", "ticker", "request", "runtime", "chart",
                         "barmerge", "adjustment", "earnings", "dividends",
                         "splits", "session", "scale", "font",
                         "hline", "backadjustment", "settlement_as_close",
                         "dayofweek"):
            return PineType.VOID

        sym = self._symbols.resolve(node.name)
        self._identifier_binding_scopes[id(node)] = (
            getattr(sym, "scope", None) if sym is not None else None
        )
        if sym is not None:
            return sym.pine_type

        # Check if it's a user-defined function
        if node.name in self._func_defs:
            return self._func_return_types.get(node.name, PineType.FLOAT)

        # Check for well-known PineScript built-in functions/types
        # that we didn't pre-populate (nz, na, fixnan, etc.)
        if node.name in ("nz", "na", "fixnan", "int", "float", "bool", "string",
                         "array", "matrix", "label", "line", "box", "table",
                         "log", "map", "type", "__array_literal__",
                         "timestamp", "year", "month", "dayofmonth",
                         "dayofweek", "hour", "minute", "second",
                         "max_bars_back", "timenow", "barssince",
                         "ta", "math", "str", "input", "color",
                         "request", "ticker", "runtime"):
            return PineType.VOID

        # Unknown identifier — treat as float (may be from skipped enum/type blocks)
        return PineType.FLOAT

    def _visit_MemberAccess(self, node: MemberAccess) -> PineType:
        # Handle specific namespaces
        if isinstance(node.object, Identifier):
            ns = node.object.name

            # strategy.* variables and constants
            if ns == "strategy":
                # Direction constants
                if node.member in ("long", "short"):
                    return PineType.INT
                # Qty type constants
                if node.member in ("percent_of_equity", "fixed", "cash"):
                    return PineType.INT
                # Commission type constants
                if node.member in ("commission",):
                    return PineType.VOID  # namespace prefix for .percent etc.
                # Integer strategy variables
                if node.member in ("closedtrades", "opentrades", "wintrades",
                                   "losstrades", "eventrades"):
                    return PineType.INT
                # Float strategy variables
                if node.member in ("position_size", "position_avg_price",
                                   "equity", "initial_capital", "netprofit",
                                   "netprofit_percent", "openprofit",
                                   "openprofit_percent", "grossprofit",
                                   "grossprofit_percent", "grossloss",
                                   "grossloss_percent", "max_drawdown",
                                   "max_drawdown_percent", "max_runup",
                                   "max_runup_percent", "avg_trade",
                                   "avg_trade_percent", "avg_winning_trade",
                                   "avg_winning_trade_percent",
                                   "avg_losing_trade", "avg_losing_trade_percent",
                                   "margin_liquidation_price",
                                   "max_contracts_held_all",
                                   "max_contracts_held_long",
                                   "max_contracts_held_short"):
                    return PineType.FLOAT
                # String strategy variables
                if node.member in ("account_currency", "position_entry_name"):
                    return PineType.STRING
                # OCA / direction sub-namespaces
                if node.member in ("oca", "direction", "risk"):
                    return PineType.VOID  # namespace prefix
                # Default for unknown strategy members
                return PineType.INT

            # ta.tr (no parens -- it's a property, not a function call)
            if ns == "ta":
                if node.member == "tr":
                    # ta.tr uses close for previous bar
                    self._series_bar_fields.add("close")
                    self._series_bar_fields.add("high")
                    self._series_bar_fields.add("low")
                    return PineType.FLOAT
                # No-arg TA indicators used as properties (ta.obv, ta.accdist, etc.)
                _TA_PROPERTY_INDICATORS = {
                    "obv", "accdist", "nvi", "pvi", "pvt", "wad", "wvad", "iii", "vwap",
                }
                if node.member in _TA_PROPERTY_INDICATORS and node.member in TA_CLASS_MAP:
                    # Create a synthetic FuncCall node for the analyzer
                    synthetic_call = FuncCall(
                        callee=MemberAccess(object=Identifier(name="ta"), member=node.member),
                        args=[], kwargs={},
                    )
                    return self._handle_ta_call(node.member, synthetic_call)
                return PineType.FLOAT

            # math.* properties
            if ns == "math":
                return PineType.FLOAT

            # syminfo.*
            if ns == "syminfo":
                from .. import signatures as _pf_sigs
                return _pf_sigs.SYMINFO_VARIABLES.get(
                    f"syminfo.{node.member}",
                    PineType.STRING,
                )

            # color.* constants
            if ns == "color":
                return PineType.COLOR

            # display.* constants
            if ns == "display":
                return PineType.INT

            # plot.* constants (e.g., plot.style_areabr)
            if ns == "plot":
                return PineType.INT

            # timeframe.*
            if ns == "timeframe":
                return PineType.STRING

            # barstate.* (ishistory, isrealtime, islast, isfirst, etc.)
            if ns == "barstate":
                return PineType.BOOL

            # alert.* constants (freq_once_per_bar, freq_once_per_bar_close, etc.)
            if ns == "alert":
                return PineType.INT

            # position.* constants for tables (middle_right, top_left, etc.)
            if ns == "position":
                return PineType.INT

            # shape.* constants (triangleup, triangledown, cross, etc.)
            if ns == "shape":
                return PineType.INT

            # location.* constants (belowbar, abovebar, absolute, etc.)
            if ns == "location":
                return PineType.INT

            # size.* constants (small, normal, large, etc.)
            if ns == "size":
                return PineType.INT

            # currency.* constants (USD, EUR, TWD, etc.)
            if ns == "currency":
                return PineType.STRING

            # order.* constants
            if ns == "order":
                return PineType.INT

            # format.* constants — Pine ``const string`` ("mintick",
            # "percent", ...). Codegen emits them as std::string literals
            # (consumed by pine_str_tostring), so bare reads must type STRING
            # for the declared C++ variable to match.
            if ns == "format":
                return PineType.STRING

            # text.* constants (align_left, align_right, etc.)
            if ns == "text":
                return PineType.STRING

            # extend.* constants (left, right, both, none)
            if ns == "extend":
                return PineType.INT

            # xloc.*, yloc.* constants
            if ns in ("xloc", "yloc"):
                return PineType.INT

            # label.*, line.*, box.*, table.* methods
            if ns in ("label", "line", "box", "table"):
                return PineType.VOID

            # ticker.* functions
            if ns == "ticker":
                return PineType.STRING

            # request.* (security, etc.) — skipped but valid
            if ns == "request":
                return PineType.FLOAT

            # runtime.* (error, etc.)
            if ns == "runtime":
                return PineType.VOID

            # chart.* (fg_color, bg_color, etc.)
            if ns == "chart":
                return PineType.COLOR

            # barmerge.* (lookahead_off, gaps_off, etc.)
            if ns == "barmerge":
                return PineType.INT

            # adjustment.*, session.* constants
            if ns in ("adjustment", "session"):
                return PineType.INT

            # scale.*, font.*, backadjustment.*, settlement_as_close.* constants
            if ns in ("scale", "font", "backadjustment", "settlement_as_close"):
                return PineType.INT

            # hline.* constants (style_dashed, style_dotted, etc.)
            if ns == "hline":
                return PineType.INT

            # dayofweek.* constants (monday, tuesday, etc.)
            if ns == "dayofweek":
                return PineType.INT

            # dividends.*, earnings.*, splits.* variables
            if ns in ("dividends", "earnings", "splits"):
                return PineType.FLOAT

            sym = self._symbols.resolve(ns)
            udt_name = None
            if sym is not None:
                udt_name = sym.udt_type_name
                if udt_name is None and sym.type_spec is not None and sym.type_spec.kind == "udt":
                    udt_name = sym.type_spec.name
            if udt_name:
                field_type = (self._udt_fields.get(udt_name) or {}).get(node.member)
                if field_type is not None:
                    return field_type

        # Handle nested member access (e.g., strategy.oca.reduce,
        # strategy.closedtrades.profit, strategy.commission.percent)
        if isinstance(node.object, MemberAccess):
            owner_spec = self._type_spec_from_expr(node.object)
            if owner_spec is not None and owner_spec.kind == "udt" and owner_spec.name:
                field_spec = (self._udt_field_type_specs.get(owner_spec.name) or {}).get(node.member)
                if field_spec is not None:
                    return self._type_hint_to_pine(str(field_spec))
            self._visit(node.object)
            # strategy.closedtrades.* and strategy.opentrades.* return types
            if (isinstance(node.object.object, Identifier) and
                    node.object.object.name == "strategy"):
                sub = node.object.member
                if sub in ("closedtrades", "opentrades"):
                    # .profit, .entry_price, .exit_price, .commission, etc. → FLOAT
                    # .entry_bar_index, .exit_bar_index → INT
                    # .entry_id, .exit_id, .entry_comment, .exit_comment → STRING
                    if node.member in ("entry_bar_index", "exit_bar_index",
                                       "first_index"):
                        return PineType.INT
                    if node.member in ("entry_id", "exit_id", "entry_comment",
                                       "exit_comment"):
                        return PineType.STRING
                    if node.member in ("entry_time", "exit_time"):
                        return PineType.INT
                    return PineType.FLOAT  # profit, entry_price, etc.
                if sub == "commission":
                    return PineType.INT  # strategy.commission.percent, etc.
                if sub in ("oca", "direction", "risk"):
                    return PineType.INT
            return PineType.INT

        # General case
        obj_type = self._visit(node.object)
        return PineType.UNKNOWN

    def _visit_TypeAnnotation(self, node: TypeAnnotation) -> PineType:
        return self._type_hint_to_pine(node.type_name)

    # ------------------------------------------------------------------
    # Literal visitors
    # ------------------------------------------------------------------

    def _visit_NumberLiteral(self, node: NumberLiteral) -> PineType:
        if isinstance(node.value, float):
            return PineType.FLOAT
        return PineType.INT

    def _visit_StringLiteral(self, node: StringLiteral) -> PineType:
        return PineType.STRING

    def _visit_BoolLiteral(self, node: BoolLiteral) -> PineType:
        return PineType.BOOL

    def _visit_NaLiteral(self, node: NaLiteral) -> PineType:
        return PineType.NA

    def _visit_ColorLiteral(self, node: ColorLiteral) -> PineType:
        return PineType.COLOR

    def _visit_TupleLiteral(self, node: TupleLiteral) -> PineType:
        self._tuple_element_types_by_node[id(node)] = tuple(
            self._visit(elem) for elem in node.elements
        )
        return PineType.FLOAT
