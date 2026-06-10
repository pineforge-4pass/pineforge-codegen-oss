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
        self._ta_counter = 0
        self._fixnan_counter = 0
        # Track user-defined function nodes for deferred analysis
        self._func_defs: dict[str, FuncDef] = {}
        # Track user-defined function return types
        self._func_return_types: dict[str, PineType] = {}
        # Track user-defined function tuple returns
        self._func_returns_tuple: dict[str, bool] = {}
        self._func_tuple_element_count: dict[str, int] = {}
        # Track user-defined functions whose body returns a UDT instance —
        # maps func_name -> UDT type name. Detected from the body's final
        # expression (``=> Sample.new(...)`` or last stmt ``Sample.new(...)``).
        # Probe: data/validation/udt-method-probe-20-udt-return-from-func.
        self._func_udt_return_types: dict[str, str] = {}
        # Per-function var_members and series_vars (for call-site cloning)
        self._func_var_members: dict[str, list] = {}  # func_name -> [(name, PineType, init_str)]
        self._func_series_vars: dict[str, set] = {}   # func_name -> set[str]
        # Per-call-site TA tracking for user functions
        self._func_ta_ranges: dict[str, tuple[int, int]] = {}  # func_name -> (start, end) indices
        self._func_call_site_count: dict[str, int] = {}  # func_name -> count
        self._func_call_cs_map: dict[int, tuple[str, int]] = {}  # call_node_id -> (func_name, cs_idx)
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
            strategy_params=self._strategy_params,
            diagnostics=self._diagnostics,
            filename=self._filename,
            global_var_decls=self._global_var_decls,
            global_expr_map=pure_global_expr_map,
            var_member_init_exprs=self._var_member_init_exprs,
            func_ta_ranges=self._func_ta_ranges,
            func_call_cs_map=self._func_call_cs_map,
            func_call_site_counts=self._func_call_site_count,
            udt_defs=self._udt_fields,
            enum_defs=self._enum_defs,
            enum_member_strings=self._enum_member_strings,
            security_calls=getattr(self, "_security_calls", []),
            global_mutable_infos=mutable_global_infos,
            func_var_members=self._func_var_members,
            func_series_vars=self._func_series_vars,
            udt_var_types=dict(self._udt_var_types),
            collection_types=dict(self._collection_types),
            udt_field_type_specs=dict(self._udt_field_type_specs),
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
        """Propagate call-site counts from parent functions to sub-functions.

        If function F has N call sites and calls sub-function G internally,
        G also needs N variants so that F_csK can call G_csK with isolated state.
        This ensures every stateful sub-function gets per-call-site isolation
        inherited from its parent.
        """
        from pineforge_codegen.ast_nodes import FuncCall, FuncDef, Identifier

        # Collect all user function definitions from AST
        func_defs: dict[str, FuncDef] = {}
        for stmt in self._ast.body:
            if isinstance(stmt, FuncDef):
                func_defs[stmt.name] = stmt

        # Find which functions call which sub-functions (direct calls only)
        def _find_calls(node, known_funcs: set[str]) -> set[str]:
            calls: set[str] = set()
            if isinstance(node, FuncCall) and isinstance(node.callee, Identifier):
                if node.callee.name in known_funcs:
                    calls.add(node.callee.name)
            for attr_val in vars(node).values():
                if isinstance(attr_val, list):
                    for item in attr_val:
                        if hasattr(item, '__dict__'):
                            calls |= _find_calls(item, known_funcs)
                elif hasattr(attr_val, '__dict__'):
                    calls |= _find_calls(attr_val, known_funcs)
            return calls

        known_func_names = set(func_defs.keys())

        # For each multi-call-site function, propagate count to sub-functions
        # that have stateful locals (series vars or var members)
        changed = True
        while changed:
            changed = False
            for fname, count in list(self._func_call_site_count.items()):
                if count <= 1:
                    continue
                if fname not in func_defs:
                    continue
                sub_calls = _find_calls(func_defs[fname], known_func_names)
                for sub in sub_calls:
                    has_state = (sub in self._func_series_vars or
                                 sub in self._func_var_members)
                    if not has_state:
                        continue
                    current = self._func_call_site_count.get(sub, 0)
                    if current < count:
                        self._func_call_site_count[sub] = count
                        changed = True

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
        """If value is ``TypeName.new(...)`` for a user-defined type, return TypeName."""
        if not isinstance(value, FuncCall):
            return None
        cal = value.callee
        if not isinstance(cal, MemberAccess) or not isinstance(cal.object, Identifier):
            return None
        owner = cal.object.name
        if owner not in self._udt_fields:
            return None
        m = cal.member
        if m == "new" or (isinstance(m, str) and m.startswith("new")):
            return owner
        return None

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
            self._var_members.append((node.name, val_type, init_str))
            # Capture the init AST too so codegen can inspect the RHS callee
            # (used to detect int64-returning builtins like ``time()`` and
            # promote the symbol storage type to ``int64_t``).
            if node.value is not None:
                self._var_member_init_exprs[node.name] = node.value
            # Track function-scoped var members
            scope_name = self._symbols.current_scope.name
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

        return val_type

    # ------------------------------------------------------------------
    # Function definition
    # ------------------------------------------------------------------

    def _visit_FuncDef(self, node: FuncDef) -> PineType:
        # Store the function def for later analysis
        self._func_defs[node.name] = node

        # Enter function scope
        self._symbols.enter_scope(f"func_{node.name}")

        # Define parameters (type unknown until called)
        loc = node.loc or SourceLocation(file=self._filename, line=1, col=1, end_col=1)
        for param in node.params:
            sym = Symbol(
                name=param,
                pine_type=PineType.UNKNOWN,
                is_series=False,
                is_var=False,
                is_const=False,
                const_value=None,
                scope=f"func_{node.name}",
                loc=loc,
            )
            self._symbols.define(sym)

        # Record TA counter before visiting body
        ta_start = len(self._ta_call_sites)

        # Visit body to discover return type
        body_type = PineType.VOID
        old_global = self._global_scope
        self._global_scope = False
        try:
            for stmt in node.body:
                body_type = self._visit(stmt)
        finally:
            self._global_scope = old_global

        # Record TA range for this function
        ta_end = len(self._ta_call_sites)
        if ta_end > ta_start:
            self._func_ta_ranges[node.name] = (ta_start, ta_end)

        self._symbols.exit_scope()

        # Detect if function returns a tuple (last stmt is TupleLiteral)
        self._func_returns_tuple[node.name] = False
        self._func_tuple_element_count[node.name] = 0
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
            if udt_ret is not None:
                self._func_udt_return_types[node.name] = udt_ret

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
        for i, p in enumerate(node.params):
            udt_self = node.type_name if i == 0 else None
            hint = param_hints[i] if i < len(param_hints) else None
            ptype = self._type_hint_to_pine(hint) if hint else PineType.FLOAT
            pspec = self._type_spec_from_hint(hint) if hint else None
            param_types.append(ptype)
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
        )
        self._func_infos.append(fi)
        return PineType.VOID

    # ------------------------------------------------------------------
    # Control flow
    # ------------------------------------------------------------------

    def _visit_IfStmt(self, node: IfStmt) -> PineType:
        old_global = self._global_scope
        self._global_scope = False
        try:
            self._visit(node.condition)
            body_type = PineType.VOID
            for stmt in node.body:
                body_type = self._visit(stmt)
            for stmt in node.else_body:
                self._visit(stmt)
        finally:
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
        try:
            self._visit(node.start)
            self._visit(node.end)
            if node.step:
                self._visit(node.step)
            for stmt in node.body:
                self._visit(stmt)
        finally:
            self._global_scope = old_global

        self._symbols.exit_scope()
        return PineType.VOID

    def _visit_ForInStmt(self, node) -> PineType:
        old_global = self._global_scope
        self._global_scope = False
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
            self._global_scope = old_global
        return PineType.VOID

    def _visit_WhileStmt(self, node: WhileStmt) -> PineType:
        old_global = self._global_scope
        self._global_scope = False
        try:
            self._visit(node.condition)
            for stmt in node.body:
                self._visit(stmt)
        finally:
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
                return PineType.STRING

            # request.* calls
            if isinstance(obj, Identifier) and obj.name == "request":
                return self._handle_request_call(member, node)

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
                if node.member == "mintick":
                    return PineType.FLOAT
                return PineType.STRING

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
                return PineType.INT

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
        for elem in node.elements:
            self._visit(elem)
        return PineType.FLOAT
