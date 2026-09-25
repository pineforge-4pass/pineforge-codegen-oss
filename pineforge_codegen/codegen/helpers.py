"""Pure-utility codegen helpers.

Mixin holding stateless / near-stateless name-mangling and AST-walk
helpers used everywhere in the codegen. Lives here so the heavier
visitor / emitter mixins can depend on it without owning its
implementation. Keep this module free of imports from any other
``codegen/*`` submodule so it stays at the bottom of the dependency
graph.

Mixin contract: ``NamingHelper`` reads at most one piece of host state,
``self._all_member_names`` (used by ``_func_safe_name``). The class
mixing this in (``CodeGen``) sets that attribute in its constructor.
"""

from __future__ import annotations

import re

from ..ast_nodes import (
    Identifier, MemberAccess, TypeDecl, EnumDecl, FuncDef, MethodDef,
    VarDecl, Assignment, TupleAssign, ForStmt, ForInStmt,
)
from ..limits import iter_ast_nodes


# Integer C++ types an na-capable ``double`` expression may be narrowed into.
# ``bool`` has its own Pine truthiness lowering below: unlike a C++ cast, it
# treats both the floating NaN and the integer ``na`` sentinel as false.
NA_PRESERVING_INT_TYPES = ("int", "int64_t")
_INT_LITERAL_TEXT = re.compile(
    r"^\s*\(?(?:[-+]?\d+)(?:LL|ULL|L|U)?\)?\s*$"
)


def na_preserving_int_cast(value_cpp: str, int_cpp_type: str = "int") -> str:
    """Narrow a numeric expression to an integer type without losing ``na``.

    An *implicit* ``double`` -> ``int`` conversion of a NaN is undefined
    behaviour ([conv.fpint]), and compilers disagree in practice: AppleClang
    arm64 and g++ aarch64 produce 0 at every ``-O``, g++ x86-64 produces
    ``INT_MIN`` at ``-O0``/``-O1`` and 0 from ``-O2``. The engine's contract
    (``include/pineforge/na.hpp``) is that an integer ``na`` *is*
    ``std::numeric_limits<T>::min()``, which is what ``is_na(T)`` tests, so
    every such narrowing must be spelled out. Check the original value type:
    converting an ``int64_t`` na sentinel to ``double`` first erases it.

    The value is evaluated exactly once. ``na`` in, ``na<T>()`` out; anything
    else truncates toward zero exactly as the implicit conversion did, so a
    non-``na`` result is bit-for-bit what it was before.
    """
    # Integer literals are proven non-``na``. Keep the historical explicit
    # cast for these tiny paths so a helper does not churn every matrix/color
    # call that passes a literal index or channel.
    if _INT_LITERAL_TEXT.fullmatch(value_cpp):
        return f"({int_cpp_type})({value_cpp})"
    return (f"[&](){{ auto _pf_v = ({value_cpp}); "
            f"return is_na(_pf_v) ? na<{int_cpp_type}>() : "
            f"({int_cpp_type})_pf_v; }}()")


def pine_index_int_cast(value_cpp: str) -> str:
    """Narrow a numeric index after checking its *original* na sentinel.

    Table-driven matrix methods receive C++ text without the source AST.
    Converting an integer sentinel to ``double`` first loses it (INT64_MIN is
    finite as a double), so this helper retains the source C++ type until the
    `is_na` check. Bool has no separate na state in Pine v6.
    """
    if _INT_LITERAL_TEXT.fullmatch(value_cpp):
        return f"(int)({value_cpp})"
    return (
        f"([&](){{ auto _pf_idx_v = ({value_cpp}); "
        f"using _pf_idx_t = std::decay_t<decltype(_pf_idx_v)>; "
        f"if constexpr (std::is_same_v<_pf_idx_t, bool>) "
        f"return (int)_pf_idx_v; "
        f"else return is_na(_pf_idx_v) ? na<int>() : (int)_pf_idx_v; }}())"
    )


def pine_truth_cast(value_cpp: str) -> str:
    """Convert one scalar expression using Pine's boolean truthiness.

    Pine's boolean context is false for ``na``.  C++ differs for both numeric
    sentinels: ``bool(NaN)`` is true and ``bool(INT_MIN)`` is true.  Keep the
    expression single-evaluation and use ``if constexpr`` so the generated
    lambda is valid for either a real ``bool`` or a numeric scalar.  The final
    branch is intentionally a normal C++ conversion for a type that cannot
    carry Pine ``na`` (for example an engine handle); callers only use this
    helper at scalar boolean boundaries.
    """
    return (
        f"[&](){{ auto _pf_bool_v = ({value_cpp}); "
        f"using _pf_bool_t = std::decay_t<decltype(_pf_bool_v)>; "
        f"if constexpr (std::is_same_v<_pf_bool_t, bool>) {{ "
        f"return _pf_bool_v; }} "
        f"else if constexpr (std::is_floating_point_v<_pf_bool_t> || "
        f"std::is_integral_v<_pf_bool_t>) {{ "
        f"return is_na(_pf_bool_v) ? false : (_pf_bool_v != 0); }} "
        f"else {{ return static_cast<bool>(_pf_bool_v); }} }}()"
    )


def color_alpha_cast(value_cpp: str) -> str:
    """Convert Pine transparency without feeding ``na<int>()`` to color.hpp.

    The engine's color helper performs integer arithmetic on transparency;
    passing the integer ``na`` sentinel there would overflow before the color
    is built.  TradingView's ``color.new``/``color.rgb`` treat a missing
    transparency as 100 (fully transparent); a finite value keeps its
    truncating conversion.
    """
    return (
        f"[&](){{ auto _pf_color_v = ({value_cpp}); "
        f"using _pf_color_t = std::decay_t<decltype(_pf_color_v)>; "
        f"if constexpr (std::is_same_v<_pf_color_t, bool>) "
        f"return _pf_color_v ? 1 : 0; "
        f"else return is_na(_pf_color_v) ? 100 : (int)_pf_color_v; }}()"
    )


# Preserve the historic spelling for names already escaped in released TUs.
LEGACY_CPP_RESERVED = frozenset({
    "exp", "log", "abs", "max", "min", "and", "or", "not",
    "int", "float", "bool", "string", "short", "long", "new", "delete",
    "class", "struct", "return", "void", "auto", "const", "static",
    "prepare_script_run",
})

# Every C++17 keyword and alternative operator token, plus the C++20
# additions.  Pine reserves some of these itself, but the others are legal
# Pine identifiers and must not reach a C++ declaration unchanged.
CPP_KEYWORDS = frozenset("""
    alignas alignof and and_eq asm auto bitand bitor bool break case catch
    char char8_t char16_t char32_t class co_await co_return co_yield compl
    concept const consteval constexpr constinit const_cast continue decltype
    default delete do double dynamic_cast else enum explicit export extern
    false float for friend goto if inline int long mutable namespace new
    noexcept not not_eq nullptr operator or or_eq private protected public
    register reinterpret_cast requires return short signed sizeof static
    static_assert static_cast struct switch template this thread_local throw
    true try typedef typeid typename union unsigned using virtual void
    volatile wchar_t while xor xor_eq
""".split())

# C++20 contextual spellings can be identifiers in some contexts, but can
# become special after future emitter changes or on newer compilers.
CPP_CONTEXTUAL = frozenset({"final", "override", "import", "module"})

# Macros visible through the standard and engine headers included by every
# generated TU.  Their expansion in a member declaration is invalid C++.
CPP_STANDARD_MACROS = frozenset("""
    __LINE__ __FILE__ __DATE__ __TIME__ __TIMESTAMP__ __cplusplus
    __STDC__ __STDC_HOSTED__ __STDC_VERSION__ __STDC_MB_MIGHT_NEQ_WC__
    __STDC_UTF_16__ __STDC_UTF_32__
    NULL EOF NAN INFINITY HUGE_VAL HUGE_VALF HUGE_VALL
    stdin stdout stderr assert offsetof va_start va_end va_arg va_copy
    EXIT_SUCCESS EXIT_FAILURE RAND_MAX MB_CUR_MAX EDOM ERANGE EILSEQ
    CHAR_BIT CHAR_MIN CHAR_MAX SCHAR_MIN SCHAR_MAX UCHAR_MAX
    SHRT_MIN SHRT_MAX USHRT_MAX INT_MIN INT_MAX UINT_MAX
    LONG_MIN LONG_MAX ULONG_MAX LLONG_MIN LLONG_MAX ULLONG_MAX
    INT8_MIN INT8_MAX UINT8_MAX INT16_MIN INT16_MAX UINT16_MAX
    INT32_MIN INT32_MAX UINT32_MAX INT64_MIN INT64_MAX UINT64_MAX
    INTPTR_MIN INTPTR_MAX UINTPTR_MAX PTRDIFF_MIN PTRDIFF_MAX
    SIZE_MAX SIG_ATOMIC_MIN SIG_ATOMIC_MAX WCHAR_MIN WCHAR_MAX
    WINT_MIN WINT_MAX INTMAX_MIN INTMAX_MAX UINTMAX_MAX
    M_E M_PI M_LOG2E M_LOG10E M_LN2 M_LN10 M_PI_2 M_PI_4
    M_1_PI M_2_PI M_2_SQRTPI M_SQRT2 M_SQRT1_2
    PINEFORGE_HAS_NATIVE_LOWERING_V1 PINEFORGE_HAS_NATIVE_LIVE_V1
    PINEFORGE_HAS_AUX_SECURITY_FEED_V1 PINEFORGE_HAS_SCRIPT_RUN_PREPARE_V1
    PINEFORGE_HAS_EXPLICIT_PINE_CAP_V1
    PINEFORGE_HAS_EXPLICIT_PINE_EXECUTION_ADAPTER_V1
    PINEFORGE_NO_STRATEGY_DECLS PF_PINE_TIME_HAS_SESSION_DAY
    PF_PINE_TIME_SESSION_DAY_ARGS PF_VWAP_HAS_SESSION_ANCHOR
    PF_VWAP_SESSION_ANCHOR_ARGS PF_ALMA_HAS_FLOOR
    PF_KC_HAS_USE_TRUE_RANGE PF_VWAP_HAS_ANCHOR_INPUT
    PF_PIVOT_LEVELS_HAS_ANCHOR
""".split())

# Identifiers used by the emitter as class/type names, namespaces, and
# generated or inherited methods.  A user member with one of these names can
# hide a type/callee, or (GeneratedStrategy) conflict with the constructor.
CPP_EMITTER_NAMES = frozenset("""
    GeneratedStrategy BacktestEngine Bar Series PineArray PineMap PineMatrix
    PineGenericMatrix PineStrategyConfig PineStrategyHost StrategyOverrides
    SymInfo ReportC MagnifierDistribution ChartPoint Line Box Label Linefill
    std pineforge ta math
    on_bar on_source_bar prepare_script_run configure_pine_strategy
    configure_security_evaluators snapshot_script_state restore_script_state
    commit_script_state set_strategy_override set_input
    set_magnifier_volume_weighted fill_report run precalculate
    strategy_entry strategy_close strategy_close_all strategy_exit
    strategy_exit_cancel_bracket strategy_cancel strategy_cancel_all strategy_order
    pine_bar_index pine_last_bar_index prev_chart_close is_first_tick is_last_tick
    history_advances_new_bar security_series_slot_is_new last_bar_dual_entry_path
    live_position_size pending_order_count market_admission_journal
    pine_time pine_time_close pine_time_tradingday pine_random
    pine_runtime_error pine_enum_str_at pine_session_ismarket
    pine_session_ispostmarket pine_session_ispremarket
    tf_change tf_is_daily tf_is_intraday tf_is_monthly tf_is_seconds
    tf_is_weekly tf_multiplier tf_to_seconds time_close timestamp main_period
    round_to_mintick calc_qty
    pf_noop
    pf_line_new pf_line_new_pts pf_line_copy pf_line_delete
    pf_line_get_price pf_line_get_x1 pf_line_get_x2 pf_line_get_y1 pf_line_get_y2
    pf_line_set_first_point pf_line_set_second_point pf_line_set_x1 pf_line_set_x2
    pf_line_set_xloc pf_line_set_xy1 pf_line_set_xy2 pf_line_set_y1 pf_line_set_y2
    pf_label_new pf_label_new_pt pf_label_copy pf_label_delete
    pf_label_get_text pf_label_get_x pf_label_get_y pf_label_set_point
    pf_label_set_text pf_label_set_x pf_label_set_xloc pf_label_set_xy
    pf_label_set_y pf_label_set_yloc
    pf_box_new pf_box_new_pts pf_box_copy pf_box_delete
    pf_box_get_bottom pf_box_get_left pf_box_get_right pf_box_get_top
    pf_box_set_bottom pf_box_set_bottom_right_point pf_box_set_left
    pf_box_set_lefttop pf_box_set_right pf_box_set_rightbottom pf_box_set_top
    pf_box_set_top_left_point pf_box_set_xloc
    pf_linefill_new pf_linefill_delete pf_linefill_get_line1 pf_linefill_get_line2
    get_input_int get_input_float get_input_bool get_input_string
    trace is_na na nz fixnan
""".split())

CPP_RESERVED = set(
    LEGACY_CPP_RESERVED | CPP_KEYWORDS | CPP_CONTEXTUAL |
    CPP_STANDARD_MACROS | CPP_EMITTER_NAMES
)


# Bare C++ identifiers that ``strategy.*`` (and a few other) read-only
# accessors lower to as zero-arg free-function calls — e.g.
# ``strategy.grossprofit`` -> ``gross_profit()`` (see codegen/visit_expr.py
# and codegen/emit_top.py). A user variable emitted with one of these names
# becomes a class member that shadows the engine accessor, so the codegen
# would emit ``gross_profit = gross_profit();`` and clang rejects the call
# ("called object type 'double' is not a function"). Escaping such user
# identifiers in ``_safe_name`` keeps the two namespaces disjoint; the
# accessor call strings are emitted verbatim and never routed through
# ``_safe_name``, so they are unaffected. Keep in sync with the accessor
# lowerings if new bare-call accessors are added.
BUILTIN_ACCESSOR_NAMES = {
    "signed_position_size", "position_entry_name",
    "count_wintrades", "count_losstrades", "eventrades",
    "net_profit", "gross_profit", "gross_loss",
    "grossprofit_percent", "grossloss_percent",
    "max_contracts_held_all", "max_contracts_held_long",
    "max_contracts_held_short", "max_drawdown_percent", "max_runup_percent",
    "avg_trade", "avg_trade_percent", "avg_winning_trade", "avg_losing_trade",
    "avg_winning_trade_percent", "avg_losing_trade_percent",
    "margin_liquidation_price", "open_profit", "current_equity",
    "open_trades_capital_held",
}


class NamingHelper:
    """Identifier escaping, callee resolution, and a generic AST walker.

    Mixed into ``CodeGen``; not meant to be instantiated standalone.
    Methods that need shared state (``_all_member_names``) document the
    contract explicitly so substitution is safe."""

    # Set by CodeGen.__init__; declared here only as documentation of the
    # mixin contract. Stays a plain set; the host class owns the value.
    _all_member_names: set[str]

    @staticmethod
    def _cpp_string_escape(s: str) -> str:
        """Escape a Python string for embedding inside a C++ string literal."""
        return (
            s.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )

    def _initialise_safe_names(self, ast) -> None:
        """Reserve all authored spellings before assigning escaped ones.

        A fixed ``_name_`` escape can itself collide with an authored
        ``_name_``.  Pre-allocating against the entire AST makes the mapping
        stable and one-to-one regardless of emission order.
        """
        authored: set[str] = set()
        bound: set[str] = set()
        for node, _depth in iter_ast_nodes(ast):
            for attr in ("name", "var", "member"):
                value = getattr(node, attr, None)
                if isinstance(value, str) and value:
                    authored.add(value)
            for attr in ("names", "vars", "params", "members"):
                values = getattr(node, attr, None)
                if isinstance(values, list):
                    authored.update(value for value in values
                                    if isinstance(value, str) and value)
            if isinstance(node, TypeDecl):
                authored.update(field.name for field in node.fields)
                bound.add(node.name)
                bound.update(field.name for field in node.fields)
            elif isinstance(node, EnumDecl):
                bound.add(node.name)
                bound.update(node.members)
            elif isinstance(node, VarDecl):
                bound.add(node.name)
            elif isinstance(node, TupleAssign):
                bound.update(node.names)
            elif isinstance(node, (FuncDef, MethodDef)):
                bound.add(node.name)
                bound.update(node.params)
            elif isinstance(node, (ForStmt, ForInStmt)):
                if node.var:
                    bound.add(node.var)
                if isinstance(getattr(node, "vars", None), list):
                    bound.update(node.vars)
        self._safe_name_map: dict[str, str] = {}
        self._safe_name_occupied = authored
        self._safe_name_bound = bound
        for name in sorted(bound):
            if name in CPP_RESERVED or name in BUILTIN_ACCESSOR_NAMES:
                self._allocate_safe_name(name)

    def _allocate_safe_name(self, name: str) -> str:
        if name in LEGACY_CPP_RESERVED or name in BUILTIN_ACCESSOR_NAMES:
            base = f"_{name}_"
        else:
            base = f"pf_safe_{name}"
        occupied = getattr(self, "_safe_name_occupied", set())
        candidate = base
        suffix = 2
        while (candidate in occupied or candidate in CPP_RESERVED
               or candidate in BUILTIN_ACCESSOR_NAMES):
            candidate = f"{base}_{suffix}"
            suffix += 1
        self._safe_name_map[name] = candidate
        occupied.add(candidate)
        return candidate

    def _safe_name(self, name: str) -> str:
        """Rename Pine identifiers that collide with emitted C++ names."""
        mapping = getattr(self, "_safe_name_map", None)
        if mapping is not None and name in mapping:
            return mapping[name]
        if mapping is not None and name not in self._safe_name_bound:
            return name
        if name in CPP_RESERVED or name in BUILTIN_ACCESSOR_NAMES:
            if mapping is None:
                return (f"_{name}_" if name in LEGACY_CPP_RESERVED
                        or name in BUILTIN_ACCESSOR_NAMES
                        else f"pf_safe_{name}")
            return self._allocate_safe_name(name)
        return name

    def _func_safe_name(self, name: str) -> str:
        """Prefix function names that collide with class members (series vars or var members)."""
        safe = self._safe_name(name)
        if safe in self._all_member_names:
            return f"_fn_{safe}"
        return safe

    def _resolve_callee(self, callee) -> tuple[str | None, str | None]:
        """Extract ``(func_name, namespace)`` from a callee expression.

        - ``foo()``                        -> ``("foo", None)``
        - ``ns.foo()``                     -> ``("foo", "ns")``
        - ``strategy.risk.max_orders(...)`` -> ``("max_orders", "strategy")``
          (only the outermost root namespace is reported; nested chains
           collapse to the leftmost identifier — historical behavior).
        - anything else                    -> ``(None, None)``"""
        if isinstance(callee, Identifier):
            return callee.name, None
        if isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier):
            return callee.member, callee.object.name
        if isinstance(callee, MemberAccess) and isinstance(callee.object, MemberAccess):
            if isinstance(callee.object.object, Identifier):
                return callee.member, callee.object.object.name
        return None, None

    def _get_target_name(self, target) -> str | None:
        """Return the bare name of an assignment target, or ``None`` for non-trivial LHS."""
        if isinstance(target, Identifier):
            return target.name
        return None

    def _walk_ast(self, node):
        """Yield every node in the subtree rooted at ``node`` (including ``node``).

        Walks every attribute that historically holds an AST child or list
        of children: ``body``/``else_body``/``cases``/``default_body``,
        the standard binary/unary/ternary slots, ``args``, ``kwargs``,
        and ``TypeDecl.fields[*].default``. Order is depth-first; the
        visit order itself is not part of the public contract — only the
        set of yielded nodes."""
        if node is None:
            return
        yield node
        for attr in ("body", "else_body"):
            children = getattr(node, attr, None)
            if isinstance(children, list):
                for child in children:
                    yield from self._walk_ast(child)
        if hasattr(node, "cases") and isinstance(node.cases, list):
            for expr, stmts in node.cases:
                if expr is not None:
                    yield from self._walk_ast(expr)
                for child in stmts:
                    yield from self._walk_ast(child)
        if hasattr(node, "default_body") and isinstance(node.default_body, list):
            for child in node.default_body:
                yield from self._walk_ast(child)
        for attr in ("value", "target", "condition", "true_val", "false_val",
                     "left", "right", "object", "operand", "callee", "index",
                     "expr"):
            child = getattr(node, attr, None)
            if child is not None:
                yield from self._walk_ast(child)
        args = getattr(node, "args", None)
        if isinstance(args, list):
            for a in args:
                yield from self._walk_ast(a)
        kwargs = getattr(node, "kwargs", None)
        if isinstance(kwargs, dict):
            for v in kwargs.values():
                yield from self._walk_ast(v)
        fields = getattr(node, "fields", None)
        if isinstance(fields, list):
            for f in fields:
                if hasattr(f, "default") and f.default is not None:
                    yield from self._walk_ast(f.default)

    def _collect_binding_names(self, stmts) -> set[str]:
        """Return every name bound by a statement in ``stmts`` (recursively):
        ``var``/plain declarations, assignment targets, tuple-assign names, and
        ``for`` loop variables. Used to teach the unknown-identifier guard
        about ordinary function-local scalars, which are emitted inline and are
        otherwise tracked nowhere (``func_var_members`` only carries vars that
        become persistent struct members)."""
        names: set[str] = set()
        for stmt in stmts or []:
            for n in self._walk_ast(stmt):
                if isinstance(n, VarDecl) and n.name:
                    names.add(n.name)
                elif isinstance(n, TupleAssign):
                    names.update(x for x in n.names if x)
                elif isinstance(n, Assignment) and isinstance(n.target, Identifier):
                    names.add(n.target.name)
                elif isinstance(n, ForStmt) and n.var:
                    names.add(n.var)
                elif isinstance(n, ForInStmt):
                    if n.var:
                        names.add(n.var)
                    if n.vars:
                        names.update(x for x in n.vars if x)
        return names
