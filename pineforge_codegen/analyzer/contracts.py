"""Output data structures emitted by the analyzer (its contract with the codegen).

These dataclasses form the contract that ``Analyzer.analyze()`` exports
to the codegen. Pulling them into a sibling module breaks the
``base`` <-> ``call_handlers`` import cycle: previously
``analyzer/base.py`` imported ``CallHandlers`` from
``analyzer/call_handlers.py`` and ``call_handlers.py`` imported
``FuncInfo`` / ``TACallSite`` / ``FixnanCallSite`` / ``SecurityCallInfo``
back from ``base.py``. Putting the dataclasses here gives a strict
DAG (``call_handlers``, ``base``, ``__init__`` all import from
``contracts``; nothing reaches back).

The module is named ``contracts.py`` rather than the more obvious
``dataclasses.py`` so it does NOT shadow Python's stdlib
``dataclasses`` module.

Adding new analyzer outputs: append a new ``@dataclass`` here and
re-export it through ``analyzer/__init__.py``. Do NOT define them on
``Analyzer.base`` — that's how the cycle drifts back in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..symbols import TypeSpec


@dataclass
class TACallSite:
    """One ``ta.*`` function call-site -> one C++ member instance."""
    member_name: str           # e.g., "_ta_rsi_1"
    class_name: str            # e.g., "ta::RSI"
    ctor_args: list[str]       # e.g., ["14"]
    compute_args: list         # AST nodes for compute() call args
    returns_tuple: bool        # e.g., MACD, supertrend
    node: Any = None           # the FuncCall AST node
    is_static: bool = False    # true if global scope & arguments are recursively static


@dataclass
class FuncInfo:
    """Resolved info for a Pine function (or UDT instance method)."""
    name: str
    param_types: list          # list of PineType
    return_type: Any           # PineType
    node: Any = None           # the FuncDef AST node
    returns_tuple: bool = False
    tuple_element_count: int = 0
    # UDT instance methods: name is "TypeName.methodName"; node is synthetic FuncDef.
    is_udt_method: bool = False
    udt_type_name: str | None = None
    # Parallel to ``node.params``; each entry is the AST default expression
    # for that parameter, or ``None`` if no default was declared. Currently
    # populated only for UDT instance methods so codegen can fill in
    # missing args at the call site (see
    # ``data/validation/udt-method-probe-04-default-param``).
    param_defaults: list = field(default_factory=list)
    # When the function's body returns a UDT instance (last expression is
    # ``TypeName.new(...)`` for a user-defined type), this is the UDT
    # struct name. The codegen uses it to (a) emit the function's C++
    # return type as ``Sample`` instead of the generic ``double`` fallback
    # and (b) thread the UDT through to the caller's symbol table so
    # ``Sample s = build_sample(...)`` then ``s.score()`` dispatches
    # correctly. Probe: data/validation/udt-method-probe-20-udt-return-from-func.
    udt_return_type: str | None = None
    # Parallel to ``node.params``; each entry is a ``TypeSpec`` (or ``None``)
    # carrying UDT / drawing-handle / precise-scalar typing that the coarse
    # ``param_types`` (PineType) cannot represent. Populated from the
    # function's declared parameter type hints (authoritative) and, for
    # untyped params, from the call-site argument type. The codegen prefers
    # this over ``param_types`` when emitting each parameter's C++ type so a
    # ``pivot hi`` parameter emits as ``pivot hi`` (not ``double hi``) and an
    # untyped ``s`` used as a string emits as ``std::string s``.
    param_type_specs: list = field(default_factory=list)
    # ``TypeSpec`` of the function's return value when it is a collection the
    # coarse ``return_type`` (PineType) cannot represent — today this covers
    # array-returning functions (``buildPDLevels() => array.from(...)`` ->
    # ``std::vector<double>``). UDT / drawing-handle returns use
    # ``udt_return_type``; tuple returns use ``returns_tuple``.
    return_type_spec: Any = None


@dataclass
class FixnanCallSite:
    """Per-call-site state for ``fixnan(...)`` (one previous-value member each)."""
    member_name: str           # e.g., "_prev_fixnan_1"
    pine_type: Any             # PineType


@dataclass
class MutableGlobalInfo:
    """Bookkeeping for a global variable that the codegen may need to shadow inside ``request.security`` evaluators."""
    name: str
    pine_type: Any
    is_series: bool = False
    is_var: bool = False
    decl_node: Any = None
    source_stmts: list = field(default_factory=list)


@dataclass
class SecurityCallInfo:
    """Resolved metadata for one ``request.security(...)`` /
    ``request.security_lower_tf(...)`` call-site.

    ``is_lower_tf_array`` distinguishes the two builtins. When true the
    codegen lowers the call to a ``std::vector<T>`` accumulator (one
    element per synthesised lower-TF sub-bar of the current chart bar)
    rather than a scalar ``_req_sec_<id>``; the runtime side rejects
    the call at validation time if the requested TF is not strictly
    finer than the chart's input TF."""
    sec_id: int
    timeframe: Any
    expression: Any
    returns_tuple: bool
    tuple_size: int
    gaps: Any = None
    lookahead: Any = None
    ta_range: Any = None
    depends_on_mutable_globals: bool = False
    mutable_globals: tuple[str, ...] = ()
    is_lower_tf_array: bool = False
    # Name of the user function whose body contains this call ("" at global
    # scope). A ``request.security(sym, tf, ...)`` whose ``tf`` is that
    # function's parameter cannot be resolved at class scope (the security
    # evaluator is a class method, not the function body) — the codegen resolves
    # such a param-tf from the function's call sites instead.
    containing_func: str = ""


@dataclass
class AnalyzerContext:
    """Output bundle of the analyzer; consumed by the codegen.

    Keep a single class instead of a dict so consumers can rely on
    typed attribute access. Fields with mutable defaults use
    ``field(default_factory=...)`` so multiple ``Analyzer`` runs
    don't share state."""
    ast: Any                   # Program node
    symbols: Any               # SymbolTable
    ta_call_sites: list = field(default_factory=list)
    series_vars: set = field(default_factory=set)
    series_bar_fields: set = field(default_factory=set)
    var_members: list = field(default_factory=list)   # [(name, PineType, init_expr_str)]
    func_infos: list = field(default_factory=list)
    fixnan_sites: list = field(default_factory=list)
    strategy_params: dict = field(default_factory=dict)
    diagnostics: list = field(default_factory=list)    # warnings
    filename: str = "<stdin>"
    global_var_decls: list = field(default_factory=list)  # [(name, PineType)] non-var global scope vars
    global_expr_map: dict = field(default_factory=dict)   # name -> defining AST expr (global, non-var)
    var_member_init_exprs: dict = field(default_factory=dict)  # var-member name -> init AST expr
    # Per-call-site TA member cloning for user functions:
    func_ta_ranges: dict = field(default_factory=dict)    # func_name -> (start_idx, end_idx)
    func_call_cs_map: dict = field(default_factory=dict)  # call_node_id -> (func_name, call_site_index)
    func_call_site_counts: dict = field(default_factory=dict)  # func_name -> int
    # (func_name, cs_idx) -> {orig_member_name: cloned_member_name}. Populated by
    # the analyzer ONLY for clones whose default ``{base}_cs{cs_idx}`` name would
    # collide with a clone minted through another enclosing function; lets codegen
    # use the disambiguated name instead of re-deriving a colliding one.
    func_cs_ta_clone_names: dict = field(default_factory=dict)
    # UDT / enum definitions:
    udt_defs: dict = field(default_factory=dict)          # type_name -> {field_name: PineType}
    enum_defs: dict = field(default_factory=dict)         # enum_name -> [member names]
    enum_member_strings: dict = field(default_factory=dict)  # enum_name -> [str values]
    # request.security calls (see SecurityCallInfo):
    security_calls: list = field(default_factory=list)
    global_mutable_infos: dict = field(default_factory=dict)  # name -> MutableGlobalInfo
    # Per-function var_members + series_vars (used when emitting per-function call-site variants):
    func_var_members: dict = field(default_factory=dict)
    func_series_vars: dict = field(default_factory=dict)
    # Per-function array-return TypeSpec (see FuncInfo.return_type_spec).
    func_return_type_specs: dict = field(default_factory=dict)
    # var_name -> UDT type name for variables instantiated via TypeName.new(...)
    udt_var_types: dict[str, str] = field(default_factory=dict)
    # var_name -> structured collection/UDT type metadata
    collection_types: dict[str, TypeSpec] = field(default_factory=dict)
    # UDT name -> field_name -> structured type metadata
    udt_field_type_specs: dict[str, dict[str, TypeSpec]] = field(default_factory=dict)
    # id(block_node) -> {raw_var_name: scope_unique_member_name} for block-scoped
    # ``var``/``varip`` declarations whose raw name collides with a same-named
    # block var in a sibling scope. Codegen activates the rename via
    # ``_active_var_remap`` while emitting that block's statements so reads/writes
    # of the var resolve to the disambiguated member.
    block_var_renames: dict[int, dict[str, str]] = field(default_factory=dict)
    # ``// @pf-trace name=expr`` pragmas in source order. Populated by
    # :func:`pineforge_codegen.pragmas.extract_pf_trace_pragmas` from
    # the original source text and attached after :class:`Analyzer`
    # finishes (the analyzer never sees pragma expressions because the
    # lexer strips comments). Codegen iterates this list to emit the
    # ``if (trace_enabled_) { trace(...); ... }`` block at the bottom
    # of every ``on_bar()``; an empty list means zero overhead is
    # emitted. Stored as ``list`` (not ``list[PfTracePragma]``) to
    # keep this module free of imports back into ``pragmas.py``.
    pf_trace_pragmas: list = field(default_factory=list)
