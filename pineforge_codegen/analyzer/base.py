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
        self._series_bar_fields: set[str] = set()
        self._var_members: list[tuple[str, PineType, str]] = []
        self._func_infos: list[FuncInfo] = []
        self._fixnan_sites: list[FixnanCallSite] = []
        self._strategy_params: dict = {}
        self._diagnostics: list[Diagnostic] = []
        self._global_var_decls: list[tuple[str, PineType]] = []
        self._global_expr_map: dict[str, Any] = {}
        self._var_member_init_exprs: dict[str, Any] = {}
        # Block-scoped ``var``/``varip`` name-collision disambiguation.
        # Two same-named block-scoped vars in SIBLING non-global, non-function
        # scopes (e.g. ``var bool valid`` declared inside ``if A`` and again
        # inside ``if B``) would otherwise dedupe to ONE C++ member and
        # cross-contaminate. ``_block_node_stack`` tracks the enclosing
        # block AST nodes during analysis; ``_block_var_owner`` maps a raw
        # block-var name to the id() of the FIRST block that declared it;
        # ``_block_var_renames`` maps id(block_node) -> {raw_name: unique}
        # for every later colliding block so codegen can activate the
        # rename via ``_active_var_remap`` while emitting that block.
        self._block_node_stack: list[Any] = []
        self._block_var_owner: dict[str, int] = {}
        self._block_var_renames: dict[int, dict[str, str]] = {}
        self._block_var_seq = 0
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
        # Per-function var_members and series_vars (for call-site cloning)
        self._func_var_members: dict[str, list] = {}  # func_name -> [(name, PineType, init_str)]
        self._func_series_vars: dict[str, set] = {}   # func_name -> set[str]
        # Per-call-site TA tracking for user functions
        self._func_ta_ranges: dict[str, tuple[int, int]] = {}  # func_name -> (start, end) indices
        self._func_call_site_count: dict[str, int] = {}  # func_name -> count
        self._func_call_cs_map: dict[int, tuple[str, int]] = {}  # call_node_id -> (func_name, cs_idx)
        # Textual nested calls whose identity is inherited from the active
        # parent clone rather than assigned a fixed source-level cs index.
        # Kept separately so a second propagation pass (security TF cloning)
        # does not accidentally backfill them as a new cs{N} call site.
        self._func_inherited_call_nodes: set[int] = set()
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
        # Set of TA-site indices a nested user-func call rewrote in terms of the
        # current enclosing function's params (None when not inside a FuncDef body).
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

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def analyze(self) -> AnalyzerContext:
        """Run semantic analysis and return the analyzer context."""
        self._ensure_pine_v6()
        self._visit(self._ast)

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
            var_member_init_exprs=self._var_member_init_exprs,
            func_ta_ranges=self._func_ta_ranges,
            func_call_cs_map=self._func_call_cs_map,
            func_call_site_counts=self._func_call_site_count,
            func_security_clone_only=self._func_security_clone_only,
            func_cs_ta_clone_names=self._func_cs_ta_clone_names,
            udt_defs=self._udt_fields,
            enum_defs=self._enum_defs,
            enum_member_strings=self._enum_member_strings,
            security_calls=getattr(self, "_security_calls", []),
            global_mutable_infos=mutable_global_infos,
            func_var_members=self._func_var_members,
            func_series_vars=self._func_series_vars,
            func_return_type_specs=dict(self._func_return_type_specs),
            udt_var_types=dict(self._udt_var_types),
            collection_types=dict(self._collection_types),
            udt_field_type_specs=dict(self._udt_field_type_specs),
            block_var_renames=dict(self._block_var_renames),
        )

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
                udt_name = self._udt_var_types.get(recv.name)
                owner_info = func_info_by_name.get(owner or "")
                if udt_name is None and owner_info is not None \
                        and owner_info.node is not None:
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
            if udt_name is None:
                spec = self._type_spec_from_expr(recv)
                if spec is not None and spec.kind == "udt":
                    udt_name = spec.name
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
                next_idx += 1
            if next_idx > current:
                self._func_call_site_count[fname] = next_idx

        # Inherit each multi-call-site parent's index space down the full path.
        # Re-run to a fixed point for A -> B -> C chains.
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
                        for cs_idx in range(current, count):
                            self._materialize_user_func_call_site_state(
                                sub,
                                cs_idx,
                                call_node,
                                reuse_existing_owner=fname,
                            )
                        self._func_call_site_count[sub] = count
                        changed = True

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

        # Map a drawing-handle local var name -> drawing type. Seeded from
        # declared drawing type hints (``line result``) and the function's own
        # drawing-typed parameters, plus any local first bound to a drawing
        # ``<ns>.new(...)`` constructor.
        local_drawing: dict[str, str] = {}
        param_hints = (func_node.annotations or {}).get("param_type_hints", [])
        for i, p in enumerate(func_node.params):
            hint = param_hints[i] if i < len(param_hints) else None
            if hint in _DRAWING_TYPE_NAMES:
                local_drawing[p] = hint

        def _scan(stmts):
            for st in stmts:
                if isinstance(st, VarDecl):
                    if st.type_hint in _DRAWING_TYPE_NAMES:
                        local_drawing[st.name] = st.type_hint
                    else:
                        dt = self._udt_name_from_ctor(st.value)
                        if dt in _DRAWING_TYPE_NAMES:
                            local_drawing.setdefault(st.name, dt)
                elif isinstance(st, Assignment) and isinstance(st.target, Identifier):
                    dt = self._udt_name_from_ctor(st.value)
                    if dt in _DRAWING_TYPE_NAMES:
                        local_drawing.setdefault(st.target.name, dt)
                elif isinstance(st, IfStmt):
                    _scan(st.body)
                    _scan(st.else_body)

        _scan(body)

        def _resolve_terminal(stmt):
            # An if used as the function's return expression: the value is the
            # terminal of the executed branch — recurse into the body's (then
            # else's) terminal statement.
            if isinstance(stmt, IfStmt):
                for branch in (stmt.body, stmt.else_body):
                    if branch:
                        t = _resolve_terminal(branch[-1])
                        if t is not None:
                            return t
                return None
            expr = None
            if isinstance(stmt, ExprStmt):
                expr = stmt.expr
            elif not isinstance(stmt, TupleLiteral) and hasattr(stmt, "loc"):
                expr = stmt
            if expr is None:
                return None
            if isinstance(expr, Identifier) and expr.name in local_drawing:
                return local_drawing[expr.name]
            return self._udt_name_from_ctor(expr)

        return _resolve_terminal(body[-1])

    def _visit_VarDecl(self, node: VarDecl) -> PineType:
        # Infer type from the value expression
        val_type = self._visit(node.value)
        type_spec = self._type_spec_from_hint(node.type_hint) if node.type_hint else None
        if type_spec is None:
            type_spec = self._type_spec_from_expr(node.value)

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
        if node.name in self._static_vars:
            setattr(sym, "is_static_series", True)
        self._symbols.define(sym)
        if udt_ctor is not None:
            self._udt_var_types[node.name] = udt_ctor
        if type_spec is not None:
            self._collection_types[node.name] = type_spec
            if type_spec.kind == "udt" and type_spec.name:
                self._udt_var_types[node.name] = type_spec.name

        # Track var members
        if node.is_var or node.is_varip:
            init_str = self._expr_to_str(node.value)
            scope_name = self._symbols.current_scope.name
            # Block-scoped var name-collision disambiguation. A ``var``/``varip``
            # declared inside a non-global, non-function block (an ``if`` / ``for``
            # / ``while`` body at on_bar scope) is keyed by RAW name. Two sibling
            # blocks declaring the same name would dedupe to ONE C++ member and
            # cross-contaminate (proven: egoigor1976-1-trendline-strategy's
            # ``var bool valid`` in the upper- and lower-trendline ``if`` blocks).
            # When such a name already belongs to a DIFFERENT block, mint a
            # scope-unique member name and record the rename so codegen activates
            # it (via ``_active_var_remap``) while emitting that block.
            member_name = node.name
            is_block_scoped = (
                not self._global_scope
                and not scope_name.startswith("func_")
                and not scope_name.startswith("method_")
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
            # Capture the init AST too so codegen can inspect the RHS callee
            # (used to detect int64-returning builtins like ``time()`` and
            # promote the symbol storage type to ``int64_t``).
            if node.value is not None:
                self._var_member_init_exprs[member_name] = node.value
            # Track function-scoped var members
            if scope_name.startswith("func_"):
                func_name = scope_name[5:]  # strip "func_" prefix
                if func_name not in self._func_var_members:
                    self._func_var_members[func_name] = []
                self._func_var_members[func_name].append((node.name, val_type, init_str))

        # Track global-scope non-var declarations (needed as class members
        # so user functions can reference them)
        if (not node.is_var and not node.is_varip
                and self._symbols.current_scope.name == "global"
                and node.name not in self._series_vars):
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
                self._collection_types[node.target.name] = spec

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

        is_val_static = self._is_static_expression(node.value)

        for name in node.names:
            if name == "_":
                continue

            if self._global_scope and is_val_static:
                self._static_vars.add(name)
            else:
                self._static_vars.discard(name)

            sym = Symbol(
                name=name,
                pine_type=PineType.FLOAT,  # tuple elements are typically float
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

            # Track global-scope tuple-assign targets (e.g.
            # ``[pdH, pdL] = request.security(...)``) as class members so user
            # functions / later references resolve — mirroring _visit_VarDecl.
            # Without this the names are never declared and the C++ errors with
            # "use of undeclared identifier".
            if (self._global_scope
                    and self._symbols.current_scope.name == "global"
                    and name not in self._series_vars):
                self._global_var_decls.append((name, PineType.FLOAT))
                self._global_expr_map[name] = node.value
                self._record_global_binding_stmt(
                    name, PineType.FLOAT, False, decl_node=node,
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
            self._symbols.define(sym)

        # Record TA counter before visiting body
        ta_start = len(self._ta_call_sites)

        # Visit body to discover return type
        body_type = PineType.VOID
        old_global = self._global_scope
        self._global_scope = False
        self._enclosing_func_params.append(set(node.params))
        self._enclosing_func_names.append(node.name)
        self._nested_ta_touched = set()
        try:
            for stmt in node.body:
                body_type = self._visit(stmt)
        finally:
            self._global_scope = old_global
            self._enclosing_func_params.pop()
            self._enclosing_func_names.pop()
            nested_touched = self._nested_ta_touched
            self._nested_ta_touched = None

        # Record TA range for this function. Widen to cover any nested-callee TA
        # sites whose ctor args were rewritten in terms of THIS function's params
        # (e.g. f_basisMa's sites parameterized by f_bbwp's _bbwLen), so resolving
        # this function at its call site re-substitutes those nested sites too.
        ta_end = len(self._ta_call_sites)
        lo, hi = ta_start, ta_end
        if nested_touched:
            lo = min(lo, min(nested_touched))
            hi = max(hi, max(nested_touched) + 1)
        if hi > lo:
            self._func_ta_ranges[node.name] = (lo, hi)

        inferred_param_specs = self._param_type_specs_from_def(node)
        for i, param in enumerate(node.params):
            if i < len(inferred_param_specs) and inferred_param_specs[i] is not None:
                continue
            sym = self._symbols.resolve(param)
            spec = getattr(sym, "type_spec", None) if sym is not None else None
            if spec is not None and i < len(inferred_param_specs):
                inferred_param_specs[i] = spec
        self._func_param_type_specs[node.name] = inferred_param_specs

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

        # Detect if the function returns a UDT instance via ``T.new(...)`` —
        # used by codegen to emit the C++ return type as the struct name and
        # to propagate UDT typing onto the caller's local. Probe:
        # data/validation/udt-method-probe-20-udt-return-from-func.
        if node.body:
            last_stmt = node.body[-1]
            ret_expr = None
            if isinstance(last_stmt, ExprStmt):
                ret_expr = last_stmt.expr
            elif not isinstance(last_stmt, (TupleLiteral,)):
                # last_stmt is itself an expression node (single-expr funcs)
                ret_expr = last_stmt if hasattr(last_stmt, "loc") else None
            udt_ret = self._udt_name_from_ctor(ret_expr) if ret_expr is not None else None
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
                ret_spec = self._type_spec_from_expr(ret_expr)
                if ret_spec is not None and ret_spec.kind == "array":
                    self._func_return_type_specs[node.name] = ret_spec

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
            ptype = self._type_hint_to_pine(hint) if hint else PineType.FLOAT
            pspec = self._type_spec_from_hint(hint) if hint else None
            param_types.append(ptype)
            param_specs.append(pspec)
            self._symbols.define(Symbol(
                name=p, pine_type=ptype, is_series=False,
                is_var=False, is_const=False, const_value=None,
                scope=self._symbols.current_scope.name, loc=loc,
                udt_type_name=udt_self,
                type_spec=pspec,
            ))
        ret_type = PineType.VOID
        old_global = self._global_scope
        self._global_scope = False
        try:
            for stmt in node.body:
                ret_type = self._visit(stmt)
        finally:
            self._global_scope = old_global
        self._symbols.exit_scope()

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
        fi = FuncInfo(
            name=method_key,
            param_types=param_types,
            return_type=ret_type,
            node=FuncDef(name=node.name, params=node.params,
                         body=node.body, is_single_expr=node.is_single_expr),
            is_udt_method=True,
            udt_type_name=node.type_name,
            returns_tuple=returns_tuple,
            tuple_element_count=tuple_element_count,
            param_defaults=param_defaults,
            param_type_specs=param_specs,
        )
        self._func_infos.append(fi)
        return PineType.VOID

    # ------------------------------------------------------------------
    # Control flow
    # ------------------------------------------------------------------

    def _visit_IfStmt(self, node: IfStmt) -> PineType:
        old_global = self._global_scope
        self._global_scope = False
        self._block_node_stack.append(node)
        try:
            self._visit(node.condition)
            body_type = PineType.VOID
            for stmt in node.body:
                body_type = self._visit(stmt)
            for stmt in node.else_body:
                self._visit(stmt)
        finally:
            self._block_node_stack.pop()
            self._global_scope = old_global
        # If used as expression (x = if ...), return last expr type
        return body_type

    def _visit_ForStmt(self, node: ForStmt) -> PineType:
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
        )
        self._symbols.define(sym)

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
            self._symbols.enter_scope("for_in")
            if node.var:
                loc = node.loc or SourceLocation(file=self._filename, line=1, col=1, end_col=1)
                self._symbols.define(Symbol(
                    name=node.var, pine_type=PineType.FLOAT, is_series=False,
                    is_var=False, is_const=False, const_value=None,
                    scope=self._symbols.current_scope.name, loc=loc,
                ))
            if node.vars:
                loc = node.loc or SourceLocation(file=self._filename, line=1, col=1, end_col=1)
                for v in node.vars:
                    self._symbols.define(Symbol(
                        name=v, pine_type=PineType.FLOAT, is_series=False,
                        is_var=False, is_const=False, const_value=None,
                        scope=self._symbols.current_scope.name, loc=loc,
                    ))
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
        self._block_node_stack.append(node)
        try:
            self._visit(node.condition)
            for stmt in node.body:
                self._visit(stmt)
        finally:
            self._block_node_stack.pop()
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
                for stmt in case_body:
                    result_type = self._visit(stmt)
            for stmt in node.default_body:
                self._visit(stmt)
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
            self._visit(obj)
            for arg in node.args:
                self._visit(arg)
            for val in node.kwargs.values():
                self._visit(val)
            # Matrix method dispatch: ``m.get(0, 0)`` on ``matrix<int>`` must
            # type as INT, not VOID, so ``v = m.get(...)`` propagates the
            # element PineType. ``_type_spec_from_expr`` already carries the
            # full TypeSpec for downstream codegen; this branch keeps the
            # legacy PineType-slot consumers (Symbol.pine_type, scalar
            # arithmetic inference) honest. See call_handlers.py
            # ``_handle_matrix_method``.
            if isinstance(obj, Identifier):
                recv_spec = self._collection_types.get(obj.name)
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

    def _visit_Subscript(self, node: Subscript) -> PineType:
        obj_type = self._visit(node.object)
        self._visit(node.index)

        # Detect series vars / bar fields
        if isinstance(node.object, Identifier):
            name = node.object.name
            if name in BAR_FIELDS:
                self._series_bar_fields.add(name)
            else:
                sym = self._symbols.resolve(name)
                if sym is not None:
                    if getattr(sym, "type_spec", None) is None or sym.type_spec.kind not in ("array", "map"):
                        self._series_vars.add(name)
                        sym.is_series = True
                        # Track function-scoped series vars
                        if sym.scope and sym.scope.startswith("func_"):
                            func_name = sym.scope[5:]
                            if func_name not in self._func_series_vars:
                                self._func_series_vars[func_name] = set()
                            self._func_series_vars[func_name].add(name)

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
