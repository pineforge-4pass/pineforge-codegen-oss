"""Expression-level visitors for the codegen.

``ExprVisitor`` holds the expression-level visitors. ``_visit_expr`` is
the central dispatcher that inspects the AST node kind and either emits
a literal directly (numbers, strings, booleans, ``na``, color literals)
or delegates to one of the kind-specific handlers:

* ``_visit_ident`` — identifier resolution (parameters, ``BAR_FIELDS`` /
  ``BAR_BUILTINS`` builtins, known constants, series-var current value
  reads, per-call-site var remapping).
* ``_visit_member_access`` — namespace member access for ``strategy`` /
  ``math`` / ``ta`` / ``timeframe`` / ``barstate`` / ``syminfo`` /
  ``display`` / ``color``, nested ``strategy.*.*`` (oca / direction /
  commission / closedtrades / opentrades), enum member access, and the
  fallback path for UDT-style member access on known variables.
* ``_visit_binop`` — binary operators (with the Pine ``and`` / ``or``
  -> ``&&`` / ``||`` rewrite and ``%`` -> ``std::fmod``).
* ``_visit_unaryop`` — unary ``not`` -> ``!`` plus pass-through of
  numeric unary operators.
* ``_visit_subscript`` — array/series ``[k]`` history access (function
  parameters, bar-field series, user series-vars, ``strategy.*[k]``
  history shadow series, generic fallback).

These visitors were extracted from ``base.py``'s ``CodeGen`` class as
step 9 of the codegen package refactor; behaviour is preserved
verbatim. The mixin owns no state of its own — it reads/writes only
attributes already established on the host class (``CodeGen``).

Mixin contract — host class must provide the following attributes
(all set by ``CodeGen.__init__`` or other mixins):

- ``self.ctx`` (``AnalyzerContext``): symbol table source. The
  visitors read ``ctx.symbols.resolve`` (``_visit_ident`` /
  ``_visit_member_access``) to disambiguate ``color`` and UDT
  identifiers, ``ctx.series_vars`` to decide between current-value
  reads and history reads, and ``ctx.ta_call_sites`` to find the
  no-arg TA-property call site (``ta.obv`` / ``ta.accdist`` / ...).
- ``self._known_vars`` (``dict[str, bool|int|float|str]``): inlined
  literal constants; consulted by ``_visit_ident``.
- ``self._input_backed_vars`` (``set[str]``): names backed by
  ``input.*`` calls — these are NOT inlined even when in
  ``_known_vars`` because ``strategy_set_input()`` can override them
  at runtime.
- ``self._var_names`` (``set[str]``): names declared at module scope.
  Consulted by ``_visit_ident`` (to decide whether ``color`` is a
  type name vs a variable), ``_visit_member_access`` (UDT fallback)
  and ``_visit_subscript`` (generic fallback).
- ``self._global_member_vars`` (``set[str]``): non-``var`` global
  declarations emitted as class members; ``_visit_ident`` uses it as
  part of the ``color``-as-type-name guard.
- ``self._current_func_locals`` (``set[str]``): function-local names
  emitted in the current function body (used by the ``color``
  identifier guard in ``_visit_ident``).
- ``self._current_func_param_types`` (``dict[str, ...]``): parameter
  name → type for the function currently being emitted; used by
  ``_visit_ident``, ``_visit_member_access`` and ``_visit_subscript``
  to treat parameters as plain locals (never series-member reads).
- ``self._current_func_series_params`` (``set[str]``): subset of the
  current function's parameters that are series-typed; treated as
  ``Series<T>`` so ``src`` becomes ``src[0]`` and ``src[k]`` stays
  ``src[k]``.
- ``self._current_loop_vars`` (``set[str]``): for-in iterator names;
  used by ``_visit_member_access`` to distinguish iterator member
  access from enum constants.
- ``self._active_var_remap`` (``dict[str, str]``): per-call-site
  rename map for cloned function-local var/series names; applied in
  ``_visit_ident`` / ``_visit_member_access`` / ``_visit_subscript``.
- ``self._enum_defs`` (``dict[str, dict[str, ...]]``): enum name →
  member map; ``_visit_member_access`` consults it before falling
  through to the generic ``Identifier.member`` path.
- ``self._strategy_series_vars`` (``set[str]``): collected by
  ``_visit_subscript`` whenever it encounters a
  ``strategy.<member>[k]`` history access; the emitter creates
  matching ``_strat_<member>`` series later.

Sibling-mixin methods consumed via ``self``:

- ``NamingHelper`` (``codegen/helpers.py``): ``_safe_name``.
- ``CodeGen.base``: ``_visit_func_call`` (still on the host class —
  the call-dispatch helpers and ``_visit_func_call`` itself are
  extracted in step 10 of the refactor).

The mixin avoids importing from ``base.py`` to stay free of cycles;
all tables it needs come from ``codegen/tables.py`` and all AST
classes from ``..ast_nodes``.
"""

from __future__ import annotations

from ..errors import Phase
from ..external_requests import UNPINNED_ANNOTATION
from ..ast_nodes import (
    ASTNode,
    BinOp,
    BoolLiteral,
    ColorLiteral,
    FuncCall,
    Identifier,
    MemberAccess,
    NaLiteral,
    NumberLiteral,
    StringLiteral,
    Subscript,
    Ternary,
    TupleLiteral,
    UnaryOp,
)
from .helpers import pine_index_int_cast
from .tables import (
    ADJUSTMENT_MAP,
    ALERT_FREQ_VALUES,
    BAR_BUILTINS,
    BAR_FIELDS,
    BAR_SERIES_PUSH,
    COLOR_CONST_MAP,
    DAYOFWEEK_MAP,
    DISPLAY_MAP,
    DRAWING_STYLE_NS,
    DRAWING_TYPE_TO_CPP,
    ON_OFF_INHERIT_MAP,
    ORDER_DIRECTION_MAP,
    SKIP_NAMESPACES,
    SYMINFO_MEMBER_MAP,
    TA_COMPUTE_ARGS,
    TA_IMPLICIT_COMPUTE_FULL,
)


def _color_literal_to_int64(value: str) -> str:
    """Lower a Pine hex color literal to a packed ARGB int64 C++ literal.

    Pine ``#RRGGBB`` is opaque; ``#RRGGBBAA`` carries an explicit alpha
    (opacity, FF = opaque). The engine packs colors as ``0xAARRGGBB``
    (see color.hpp: ``red == 0xFFFF0000``), so we reorder to alpha-first
    and emit an ``int64_t`` literal — matching ``get_input_int64`` and
    ``pine_color::*``. A malformed literal falls back to 0 (transparent).
    """
    h = value.lstrip("#").strip()
    if len(h) == 6:
        rr, gg, bb, aa = h[0:2], h[2:4], h[4:6], "ff"
    elif len(h) == 8:
        rr, gg, bb, aa = h[0:2], h[2:4], h[4:6], h[6:8]
    else:
        return "0"
    try:
        int(rr + gg + bb + aa, 16)  # validate hex
    except ValueError:
        return "0"
    return f"0x{aa}{rr}{gg}{bb}LL"


# Valid Pine v6 bare builtins that have no value in a batch backtest (no live
# feed). Reading them used to emit an undeclared C++ symbol; they now get a
# tailored hint via the unknown-identifier guard in ``_visit_ident``.
_REALTIME_ONLY_VARS: frozenset[str] = frozenset({"ask", "bid"})

# Builtin namespace prefixes (``format.mintick``, ``barmerge.gaps_on``, …).
# When a member-access handler has no dedicated branch it falls through to the
# generic tail, which visits the root identifier bare — so these must not be
# flagged as unknown variables. Mirrors the namespace list in
# ``analyzer/base.py::_visit_Identifier``.
_BUILTIN_NAMESPACE_NAMES: frozenset[str] = frozenset({
    "strategy", "ta", "input", "math", "str", "color", "display", "syminfo",
    "timeframe", "plot", "alert", "barstate", "position", "shape", "location",
    "size", "currency", "order", "format", "text", "extend", "xloc", "yloc",
    "label", "line", "box", "table", "ticker", "request", "runtime", "chart",
    "barmerge", "adjustment", "earnings", "dividends", "splits", "session",
    "scale", "font", "hline", "backadjustment", "settlement_as_close",
    "dayofweek", "array", "matrix", "map", "polyline", "linefill",
})


# KI-71/KI-73: Pine relational comparisons (``==`` ``!=`` ``<`` ``>``
# ``<=`` ``>=``) with an ``na`` operand evaluate *falsy*. Pine also treats
# finite float operands at most 1e-10 apart as equal, independent of their
# magnitude. Naive C++ relationals honour neither rule completely, so numeric
# comparisons route through the shared lowering below.
_RELATIONAL_OPS: frozenset[str] = frozenset({"==", "!=", "<", ">", "<=", ">="})

# C++ scalar types carrying a detectable ``na`` sentinel via ``is_na``:
# ``double`` -> NaN (IEEE), ``int``/``int64_t`` -> ``numeric_limits<T>::min()``.
# Only these route through the na-aware relational lowering — ``is_na`` has no
# overload for ``bool``/``std::string``/vector/UDT-value operands, and Pine's
# na-bool is engine-indistinguishable from ``false`` (na<bool>() == false).
_NA_SCALAR_CPP: frozenset[str] = frozenset({"int", "int64_t", "double"})


class ExprVisitor:
    """Expression-level visitor methods shared across the codegen.

    Mixed into ``CodeGen``; not intended to be instantiated standalone.
    See the module docstring for the full host-class state contract."""

    # ------------------------------------------------------------------
    # Expression visitors
    # ------------------------------------------------------------------

    def _visit_expr(self, node: ASTNode | None) -> str:
        if node is None:
            return "/* null */"
        if self._budget is not None:
            self._budget_visit_count += 1
            if self._budget_visit_count % 128 == 0:
                self._budget.check(node.loc, Phase.CODEGEN)
        if node.annotations and UNPINNED_ANNOTATION in node.annotations:
            return self._unpinned_read(node)
        if self._security_fallback_frame is not None:
            delegated = self._security_fallback_delegate(node)
            if delegated is not None:
                return delegated
        if isinstance(node, NumberLiteral):
            return str(node.value)
        if isinstance(node, StringLiteral):
            return f'std::string("{self._cpp_string_escape(node.value)}")'
        if isinstance(node, BoolLiteral):
            return "true" if node.value else "false"
        if isinstance(node, NaLiteral):
            return "na<double>()"
        if isinstance(node, ColorLiteral):
            return _color_literal_to_int64(node.value)
        if isinstance(node, Identifier):
            return self._visit_ident(node)
        if isinstance(node, MemberAccess):
            return self._visit_member_access(node)
        if isinstance(node, BinOp):
            return self._visit_binop(node)
        if isinstance(node, UnaryOp):
            return self._visit_unaryop(node)
        if isinstance(node, Ternary):
            return self._visit_ternary(node)
        if isinstance(node, FuncCall):
            return self._visit_func_call(node)
        if isinstance(node, Subscript):
            return self._visit_subscript(node)
        if isinstance(node, TupleLiteral):
            elems = []
            for element in node.elements:
                element_spec = self._type_spec_from_expr(element)
                element_target = None
                if (element_spec is not None
                        and element_spec.kind == "udt"
                        and element_spec.name in DRAWING_TYPE_TO_CPP):
                    element_target = self._type_spec_to_cpp(element_spec)
                elems.append(
                    self._visit_rhs_value(
                        element,
                        target_cpp_type=element_target,
                    )
                )
            elems = ", ".join(elems)
            return f"std::make_tuple({elems})"
        return "/* unknown */"

    # A ``?:`` chain continued in its false arms (``a ? x : b ? y : z``) of at
    # least this many links is emitted without the per-link brackets: they add
    # three bracket levels per link and clang stops at 256
    # (``-fbracket-depth``).  C++'s conditional operator is right-associative,
    # so ``a ? x : b ? y : z`` reads ``a ? x : (b ? y : z)``.  Shorter chains
    # keep their historical spelling byte for byte.
    _FLAT_TERNARY_CHAIN = 32

    def _visit_ternary(self, node: Ternary) -> str:
        links = [node]
        while isinstance(links[-1].false_val, Ternary):
            links.append(links[-1].false_val)
        string_na = self._ternary_string_na(links)
        if len(links) < self._FLAT_TERNARY_CHAIN:
            c = self._coerce_bool_expr(
                self._visit_expr(node.condition), node.condition
            )
            t = self._ternary_arm(node.true_val, string_na)
            f = self._ternary_arm(node.false_val, string_na)
            return f"(({c}) ? ({t}) : ({f}))"
        arms = []
        for link in links:
            c = self._coerce_bool_expr(
                self._visit_expr(link.condition), link.condition
            )
            t = self._ternary_arm(link.true_val, string_na)
            arms.append(f"({c}) ? ({t}) : ")
        return f"({''.join(arms)}({self._ternary_arm(links[-1].false_val, string_na)}))"

    def _ternary_string_na(self, links: list[Ternary]) -> bool:
        """A ``?:`` chain whose every value arm but a bare ``na`` is a
        string: its ``na`` arm is the string na (``cond ? "BUY" : na``, as
        libraries return a signal). It used to emit ``na<double>()`` beside a
        ``std::string``, which does not compile."""
        arms = [link.true_val for link in links] + [links[-1].false_val]
        values = [arm for arm in arms if not self._is_na_expr(arm)]
        if len(values) == len(arms) or not values:
            return False
        return all(self._infer_type(arm) == "std::string" for arm in values)

    def _ternary_arm(self, arm, string_na: bool) -> str:
        if string_na and self._is_na_expr(arm):
            return "na<std::string>()"
        return self._visit_expr(arm)

    # ------------------------------------------------------------------
    # Target-typed RHS lowering (drawing handles are C++ structs, not doubles)
    # ------------------------------------------------------------------

    def _is_na_expr(self, node) -> bool:
        """True for a bare ``na`` (keyword NaLiteral or ``na`` identifier)."""
        return (isinstance(node, NaLiteral)
                or (isinstance(node, Identifier) and node.name == "na"))

    def _drawing_target_cpp_type(
        self,
        target_name: str | None,
        target_cpp_type: str | None,
    ) -> str | None:
        """Resolve a drawing target without leaking a shadowed global type.

        An explicit contextual type always wins.  For reassignments, where the
        caller may not have one, consult function-local and parameter types
        before the analyzer's flat global drawing registry.  A same-named
        scalar local/parameter must therefore block the global fallback.
        """
        if target_cpp_type is not None:
            return (
                target_cpp_type
                if target_cpp_type in DRAWING_TYPE_TO_CPP.values()
                else None
            )
        if not target_name:
            return None
        if target_name in self._lexical_drawing_types:
            return self._lexical_drawing_types[target_name]
        param_spec = getattr(self, "_current_func_param_specs", {}).get(
            target_name
        )
        if (param_spec is not None
                and param_spec.kind == "udt"
                and param_spec.name in DRAWING_TYPE_TO_CPP):
            return DRAWING_TYPE_TO_CPP[param_spec.name]
        for scoped_types in (
            getattr(self, "_current_func_local_types", {}),
            getattr(self, "_current_func_param_types", {}),
        ):
            if target_name in scoped_types:
                scoped_type = scoped_types[target_name].removesuffix("&")
                return (
                    scoped_type
                    if scoped_type in DRAWING_TYPE_TO_CPP.values()
                    else None
                )
        # Only declarations already emitted into the current lexical path may
        # shadow a global.  Scanning the whole future function body makes a
        # later local declaration pre-shadow earlier statements.  Exact
        # top-level types are captured separately because the analyzer's flat
        # raw-name registry can be overwritten by a same-named local.
        return self._global_drawing_cpp_types.get(target_name)

    def _udt_target_cpp_type(
        self,
        *,
        target_name: str | None = None,
        target_node=None,
        type_hint: str | None = None,
    ) -> str | None:
        """Return the exact authored UDT type for a contextual RHS target.

        Pine's bare ``na`` is target typed.  Arbitrary UDTs represent their na
        ID with a default-constructed ``T{}``, just as generated drawing
        handles do, but the drawing-only registry cannot safely answer for
        same-spelled ordinary UDT declarations in sibling blocks/functions.
        Prefer explicit/node context, then the source-ordered lexical map, and
        consult legacy raw-name metadata only when no lexical binding exists.
        """
        spec = self._type_spec_from_hint_name(type_hint) if type_hint else None
        if spec is None and target_node is not None:
            spec = self._type_spec_from_expr(target_node)
        if spec is not None and spec.kind == "udt" and spec.name in self._udt_defs:
            return spec.name
        if not target_name:
            return None
        udt_name = self._identifier_udt_type(target_name)
        return udt_name if udt_name in self._udt_defs else None

    def _visit_rhs_value(self, value_node, target_name: str | None = None,
                         target_cpp_type: str | None = None) -> str:
        """Visit an assignment / declaration RHS.

        A bare ``na`` lowers to a type-appropriate initializer for the target
        instead of ``na<double>()``: drawing and authored UDT handles
        brace-init to their na object (``Box{}`` / ``State{}``), while
        ``std::string``/``int``/``int64_t``/``bool`` use ``na<T>()``. Without
        this, ``State s = na;`` and ``string x = na;`` would both emit
        ``na<double>()`` and fail to compile (no viable assignment/conversion).
        Every other RHS lowers unchanged.
        """
        drawing_target = self._drawing_target_cpp_type(
            target_name,
            target_cpp_type,
        )
        if self._is_na_expr(value_node):
            if drawing_target is not None:
                return f"{drawing_target}{{}}"
            if target_cpp_type in self._udt_defs:
                return f"{self._safe_name(target_cpp_type)}{{}}"
            if self._is_nullable_collection_cpp_type(target_cpp_type):
                # Maps and matrices use default construction for a typed
                # ``na`` ID. Their ``*.new`` factories create a valid ID,
                # including a valid empty collection.
                return f"{target_cpp_type}{{}}"
            if target_cpp_type in ("std::string", "int", "int64_t", "bool"):
                return f"na<{target_cpp_type}>()"
        if (isinstance(value_node, Ternary)
                and (self._is_nullable_collection_cpp_type(target_cpp_type)
                     or drawing_target is not None
                     or target_cpp_type in self._udt_defs)):
            # C++ cannot deduce a common type for ``na<double>()`` and a
            # collection/drawing handle. Pine's ternary is target typed, so
            # propagate the exact declared/reassignment target into both arms.
            # Arrays and scalar ternaries retain the established generic path;
            # target typing is safe only for collections with nullable runtime
            # representations.
            branch_target = drawing_target or target_cpp_type
            condition = self._visit_expr(value_node.condition)
            true_value = self._visit_rhs_value(
                value_node.true_val,
                target_name,
                target_cpp_type=branch_target,
            )
            false_value = self._visit_rhs_value(
                value_node.false_val,
                target_name,
                target_cpp_type=branch_target,
            )
            return (
                f"(({condition}) ? ({true_value}) : ({false_value}))"
            )
        return self._visit_expr(value_node)

    def _unpinned_read(self, node: ASTNode) -> str:
        """A read of a request no data is pinned for
        (``external_requests``): the run stops with the request named, and
        the value the expression would have is only there for its type."""
        notes = node.annotations
        message = notes[UNPINNED_ANNOTATION]
        node.annotations = {k: v for k, v in notes.items() if k != UNPINNED_ANNOTATION}
        try:
            value = self._visit_expr(node)
        finally:
            node.annotations = notes
        return (f'([&]() {{ pine_runtime_error(std::string("{self._cpp_string_escape(message)}")); '
                f"return {value}; }}())")

    def _visit_ident(self, node: Identifier) -> str:
        name = node.name
        # Bare 'na' identifier → na<double>()
        if name == "na":
            return "na<double>()"
        pending_outer = getattr(self, "_pending_decl_outer_alias", {})
        if name in pending_outer:
            return pending_outer[name]
        # Function parameters that are series — read current value
        if name in self._current_func_series_params:
            return f"{self._safe_name(name)}[0]"
        # Function parameters are plain locals, never series members
        if name in self._current_func_param_types:
            return self._safe_name(name)
        if name in BAR_FIELDS:
            return BAR_FIELDS[name]
        if name in BAR_BUILTINS:
            return BAR_BUILTINS[name]
        # Inline known constants (literals). Input-backed names are NOT inlined
        # because strategy_set_input() can override them at runtime; emit the
        # variable name and let get_input_*() produce the current value.
        if (name in self._known_vars
                and name not in self._input_backed_vars
                and not self._known_var_is_lexically_shadowed(name)):
            val = self._known_vars[name]
            if isinstance(val, bool):
                return "true" if val else "false"
            if isinstance(val, (int, float)):
                return str(val)
            if isinstance(val, str):
                return f'std::string("{self._cpp_string_escape(val)}")'
        # TA runtime-reset lowering: an input-backed var renders as its
        # override-aware getter (not the member name), because the reset may
        # run before the input members are initialised (evaluate_security path).
        if (self._reset_input_getter_mode
                and name in self._input_backed_vars
                and name in self._input_var_to_call):
            call_node = self._input_var_to_call[name]
            func_name_i, namespace_i = self._resolve_callee(call_node.callee)
            title = self._get_input_title(call_node, var_name=name)
            return self._render_input_value(call_node, func_name_i, namespace_i, title)
        # Pine type name `color` used as a value (no variable) → int64 color constant.
        # Params handled above; symbol table does not retain function locals after analysis.
        if name == "color":
            sym = self.ctx.symbols.resolve(node.name)
            if (
                sym is None
                and name not in self.ctx.series_vars
                and name not in self._var_names
                and name not in self._global_member_vars
                and name not in self._current_func_locals
            ):
                return "(int64_t)pine_color::black"
        # Series var — read current value
        safe = self._call_site_var_name(node, self._safe_name(name))
        if self._binding_is_series(name, safe):
            return f"{safe}[0]"
        # Safety net: by here the name resolved to none of the builtins,
        # constants, parameters, or declared variables handled above, so
        # ``return safe`` would emit a bare identifier that is an *undeclared
        # C++ symbol* (e.g. ``x = ask;``, ``x = undefined_var;``). That is a
        # silent miscompile — g++ fails against generated C++ with no Pine
        # line. Reject loudly. (Like the call-site guard in visit_call.py,
        # this branch only fires on input that already failed to compile, so
        # the all-green corpus never exercises it.)
        if not self._ident_is_resolvable(name):
            hint = None
            if name in _REALTIME_ONLY_VARS:
                hint = (f"'{name}' is a realtime-only Pine v6 builtin with no "
                        f"value in a batch backtest.")
            self._codegen_error(
                node,
                f"Unknown variable '{name}' — not a PineForge builtin or a "
                f"declared variable.",
                hint=hint,
            )
        return safe

    def _ident_is_resolvable(self, name: str) -> bool:
        """True when a bare identifier maps to a builtin, constant, parameter,
        or any declared variable/type/enum/function — i.e. emitting it will not
        produce an undeclared C++ symbol. Deliberately generous: a missing
        source here only means a genuinely-unknown name slips through (the
        pre-existing behavior), whereas a wrong ``True`` is safe. Only a
        name in NONE of these is rejected."""
        if name == "na" or name == "color":
            return True
        if name in BAR_FIELDS or name in BAR_BUILTINS:
            return True
        if name in _BUILTIN_NAMESPACE_NAMES:
            return True
        if name in self._known_vars or name in self._input_backed_vars:
            return True
        if name in self._var_names or name in self._global_member_vars:
            return True
        if name in self._current_func_locals or name in self._current_loop_vars:
            return True
        if name in self._current_func_param_types or name in self._current_func_series_params:
            return True
        if name in self.ctx.series_vars:
            return True
        if (name in self._array_vars or name in self._map_vars
                or name in self._matrix_specs or name in self._udt_var_types):
            return True
        if name in self._enum_defs or name in self._udt_defs or name in self._func_names:
            return True
        # Names bound anywhere in the program (block-scoped locals the per-scope
        # sets above never see — e.g. a var declared inside an on_bar for-loop).
        if name in self._all_bound_names:
            return True
        # Catch-all: the analyzer's symbol table knows every declared name it
        # retained (globals, inputs, …). Anything it resolves is legitimate.
        if self.ctx.symbols.resolve(name) is not None:
            return True
        return False

    def _visit_mutable_expr(self, node: ASTNode) -> str:
        """Lower an assignment target with UDT journal capture enabled."""

        previous = getattr(self, "_udt_mutable_expr_depth", 0)
        self._udt_mutable_expr_depth = previous + 1
        try:
            return self._visit_expr(node)
        finally:
            self._udt_mutable_expr_depth = previous

    def _visit_member_access(self, node: MemberAccess) -> str:
        # UDT field whose type was a drawing primitive (label/line/box/
        # linefill/polyline/table/chart.point) — the field is dropped from
        # the emitted struct, so a raw ``recv.field`` would fail to compile.
        # Emit a labelled zero placeholder so any read site stays well-formed.
        # See: pineforge-codegen issue #10.
        if self._is_omitted_udt_field(node):
            return "/* drawing field omitted */ 0"

        # User-defined objects are numeric handles into a per-type arena.  The
        # arena lookup returns a real record lvalue, so this one lowering serves
        # reads, assignment targets, nested field chains and method receivers.
        owner_spec = self._type_spec_from_expr(node.object)
        if (
            owner_spec is not None
            and owner_spec.kind == "udt"
            and owner_spec.name in self._udt_defs
        ):
            owner = self._visit_expr(node.object)
            arena = self._udt_arena_member_name(owner_spec.name)
            field_spec = (
                self._udt_field_type_specs.get(owner_spec.name, {}).get(
                    node.member
                )
            )
            mutable_collection = (
                field_spec is not None
                and field_spec.kind in {"array", "map", "matrix"}
            )
            access = (
                "get"
                if getattr(self, "_udt_mutable_expr_depth", 0)
                or mutable_collection
                else "read"
            )
            return f"{arena}.{access}({owner}).{self._safe_name(node.member)}"
        if isinstance(node.object, Identifier):
            ns = node.object.name
            if ns == "alert" and node.member in ALERT_FREQ_VALUES:
                # TradingView's const string values (its own tape spells them).
                return f'std::string("{ALERT_FREQ_VALUES[node.member]}")'
            if ns == "strategy":
                # Direction constants
                if node.member == "long":
                    return "true"
                if node.member == "short":
                    return "false"
                # Position info
                if node.member == "position_size":
                    return "signed_position_size()"
                if node.member == "position_avg_price":
                    # Pine returns `na` when the position is flat
                    # (position_size == 0); the engine field is 0.0 when flat.
                    # Guard so the common `na(strategy.position_avg_price)`
                    # flat/in-position idiom is not silently defeated.
                    return ("(signed_position_size() == 0.0 ? na<double>() "
                            ": position_entry_price_)")
                if node.member == "position_entry_name":
                    return "position_entry_name()"
                # Trade counts
                if node.member == "opentrades":
                    return "((int)pyramid_entries_.size())"
                if node.member == "closedtrades":
                    return "((int)trades_.size())"
                if node.member == "wintrades":
                    return "count_wintrades()"
                if node.member == "losstrades":
                    return "count_losstrades()"
                if node.member == "eventrades":
                    return "eventrades()"
                # Equity and P&L
                if node.member == "equity":
                    return "(current_equity() + open_profit(current_bar_.close))"
                if node.member == "initial_capital":
                    return "initial_capital_"
                if node.member == "netprofit":
                    return "net_profit()"
                if node.member == "netprofit_percent":
                    return "((net_profit() / initial_capital_) * 100.0)"
                if node.member == "openprofit":
                    return "open_profit(current_bar_.close)"
                if node.member == "openprofit_percent":
                    # Pine: openPL / realizedEquity * 100. current_equity()
                    # is the engine's realized equity (initial capital +
                    # closed-trade net profit); guard the zero denominator
                    # like the engine's *_percent accessors do.
                    return ("((current_equity() != 0.0) ? "
                            "((open_profit(current_bar_.close) / current_equity()) * 100.0) : 0.0)")
                if node.member == "grossprofit":
                    return "gross_profit()"
                if node.member == "grossloss":
                    return "gross_loss()"
                if node.member == "grossprofit_percent":
                    return "grossprofit_percent()"
                if node.member == "grossloss_percent":
                    return "grossloss_percent()"
                if node.member == "max_contracts_held_all":
                    return "max_contracts_held_all()"
                if node.member == "max_contracts_held_long":
                    return "max_contracts_held_long()"
                if node.member == "max_contracts_held_short":
                    return "max_contracts_held_short()"
                if node.member == "max_drawdown":
                    return "max_drawdown_"
                if node.member == "max_runup":
                    return "max_runup_"
                if node.member == "max_drawdown_percent":
                    return "max_drawdown_percent()"
                if node.member == "max_runup_percent":
                    return "max_runup_percent()"
                if node.member == "avg_trade":
                    return "avg_trade()"
                if node.member == "avg_trade_percent":
                    return "avg_trade_percent()"
                if node.member == "avg_winning_trade":
                    return "avg_winning_trade()"
                if node.member == "avg_losing_trade":
                    return "avg_losing_trade()"
                if node.member == "avg_winning_trade_percent":
                    return "avg_winning_trade_percent()"
                if node.member == "avg_losing_trade_percent":
                    return "avg_losing_trade_percent()"
                if node.member == "margin_liquidation_price":
                    return "margin_liquidation_price()"
                # Qty type / commission constants
                if node.member == "fixed":
                    return "0"
                if node.member == "percent_of_equity":
                    return "1"
                if node.member == "cash":
                    return "2"
                if node.member == "account_currency":
                    return "syminfo_.currency"
                # Sub-namespaces (oca, direction, risk, commission) handled below
            if ns == "math":
                if node.member == "pi":
                    return "M_PI"
                if node.member == "e":
                    return "M_E"
                if node.member == "phi":
                    return "1.618033988749895"
                if node.member == "rphi":
                    return "0.6180339887498949"
                if node.member == "abs":
                    return "std::abs"
                if node.member == "max":
                    return "std::max"
                if node.member == "min":
                    return "std::min"
            if ns == "ta":
                if node.member == "tr":
                    return ("(std::isnan(_s_close[1]) ? "
                            "na<double>() : "
                            "std::max(current_bar_.high - current_bar_.low, "
                            "std::max(std::abs(current_bar_.high - _s_close[1]), "
                            "std::abs(current_bar_.low - _s_close[1]))))")
                # No-arg TA indicators used as properties (ta.obv, ta.accdist, etc.)
                if (
                    (node.member in TA_IMPLICIT_COMPUTE_FULL and node.member in TA_COMPUTE_ARGS and TA_COMPUTE_ARGS[node.member] == [])
                    or node.member == "vwap"
                ):
                    # Find the matching call site. Skip sites pruned as dead
                    # code (their owner function is never called): a dead site's
                    # member declaration is never emitted (see base.py
                    # ``_dead_ta_indices`` / emit_top.py member-decl guard), so
                    # binding a LIVE bare property read to one references an
                    # undeclared member. Regression: nightowlxtrader-azt — a live
                    # ``ta.vwap`` (top-level + inside request.security) resolved
                    # to dead ``f5``'s first-in-order ``_ta_vwap_10`` and emitted
                    # ``use of undeclared identifier '_ta_vwap_10'``. A live read
                    # must bind to a live site.
                    # The read's OWN site first (the analyzer keys the
                    # synthetic call-site on this MemberAccess node), so two
                    # bare reads are two sites, each advanced once per bar;
                    # the first-live-site scan below stays as the fallback.
                    _own = self._get_ta_site(node)
                    _candidates = (
                        [(self._ta_index_by_site_id.get(id(_own)), _own)]
                        if _own is not None else []
                    ) + list(enumerate(self.ctx.ta_call_sites))
                    for _i, site in _candidates:
                        if _i is None or _i in self._dead_ta_indices:
                            continue
                        if site.member_name.startswith(f"_ta_{node.member}_"):
                            if node.member == "vwap":
                                # Same implicit tail as TA_IMPLICIT_APPEND["vwap"]:
                                # the symbol clock keys the Daily anchor reset.
                                # The bare property is the VWAP of hlc3 (Pine
                                # v6 reference; lab tv notrade-session-vwap-f1d,
                                # 2026-09-05: TradingView's read equals hlc3 on
                                # a daily bar), never of the close.
                                _hlc3 = "((current_bar_.high + current_bar_.low + current_bar_.close) / 3.0)"
                                return (
                                    f"(history_advances_new_bar() ? {self._ta_member_name(site)}.compute("
                                    f"{_hlc3}, current_bar_.volume, current_bar_.timestamp"
                                    " PF_VWAP_SESSION_ANCHOR_ARGS(syminfo_.timezone, syminfo_.session)) "
                                    f": {self._ta_member_name(site)}.recompute({_hlc3}, "
                                    "current_bar_.volume, current_bar_.timestamp"
                                    " PF_VWAP_SESSION_ANCHOR_ARGS(syminfo_.timezone, syminfo_.session)))"
                                )
                            return (
                                f"(history_advances_new_bar() ? {self._ta_member_name(site)}.compute("
                                f"{TA_IMPLICIT_COMPUTE_FULL[node.member]}) : "
                                f"{self._ta_member_name(site)}.recompute("
                                f"{TA_IMPLICIT_COMPUTE_FULL[node.member]}))"
                            )
                    # No registered call site for this TA property read —
                    # the old fallback emitted std::string("<name>"), a
                    # silent type mismatch. Reject loudly instead.
                    self._codegen_error(
                        node,
                        f"ta.{node.member} property read could not be bound "
                        f"to a TA call site.",
                        hint=f"Use the function form ta.{node.member}(...) "
                             f"instead of the bare property read.",
                    )
                # Any other bare ta.<member> property read (e.g. ``x = ta.rsi``
                # without parentheses) has no value to bind — Pine v6 only
                # defines property forms for tr/obv/accdist/nvi/pvi/pvt/wad/
                # wvad/iii/vwap. The old fallthrough emitted
                # ``std::string("<member>")``. Reject loudly.
                self._codegen_error(
                    node,
                    f"ta.{node.member} is not readable as a bare property in "
                    f"PineForge.",
                    hint=f"Call the function form ta.{node.member}(...) with "
                         f"its arguments instead.",
                )
            if ns == "chart":
                # PineForge batch engine always runs on standard OHLCV bars.
                if node.member == "is_standard":
                    return "true"
                # All non-standard chart types are false in batch mode.
                if node.member in ("is_heikinashi", "is_kagi", "is_linebreak",
                                   "is_pnf", "is_range", "is_renko"):
                    return "false"
                # Cosmetic / chart-only reads with no batch-mode value (theme
                # colors, viewport bar times). The support checker warns on
                # these (COSMETIC_MEMBERS); emit a benign default (0 = na color /
                # epoch 0). They have no backtest-logic effect.
                if node.member in ("fg_color", "bg_color",
                                   "left_visible_bar_time",
                                   "right_visible_bar_time"):
                    return "0"
                # Defensive: support_checker (COSMETIC_MEMBERS / UNSUPPORTED_MEMBERS)
                # should already have warned/rejected any unhandled chart.* member.
                # Reaching here is a bug.
                raise ValueError(
                    f"codegen: unhandled chart.{node.member} — analyzer should have rejected. "
                    f"Add a handler above or extend UNSUPPORTED_MEMBERS."
                )
            if ns == "timeframe":
                if node.member == "period":
                    return 'script_tf_'
                if node.member == "main_period":
                    return 'main_period()'
                if node.member == "multiplier":
                    return 'tf_multiplier(script_tf_)'
                if node.member == "isintraday":
                    return 'tf_is_intraday(script_tf_)'
                if node.member == "isminutes":
                    return '(tf_is_intraday(script_tf_) && !tf_is_seconds(script_tf_))'
                if node.member == "isdaily":
                    return 'tf_is_daily(script_tf_)'
                if node.member == "isweekly":
                    return 'tf_is_weekly(script_tf_)'
                if node.member == "ismonthly":
                    return 'tf_is_monthly(script_tf_)'
                if node.member == "isdwm":
                    return '(tf_is_daily(script_tf_) || tf_is_weekly(script_tf_) || tf_is_monthly(script_tf_))'
                if node.member == "isseconds":
                    return 'tf_is_seconds(script_tf_)'
                if node.member == "in_seconds":
                    return 'tf_to_seconds(script_tf_)'
                if node.member == "isticks":
                    # Batch engine does not support tick-resolution data.
                    return "false"
                # Defensive: the if-chain above covers every Pine v6 timeframe.*
                # namespace variable. An unknown member is invalid Pine that
                # would otherwise emit a silent "0". Mirror the chart.* guard.
                raise ValueError(
                    f"codegen: unhandled timeframe.{node.member} — not a valid "
                    f"Pine v6 timeframe namespace variable. Add a handler above "
                    f"if this is a new builtin."
                )
            if ns == "barstate":
                if node.member == "isfirst":
                    return "(bar_index_ == 0)"
                if node.member == "islast":
                    return "barstate_islast_"
                if node.member == "isnew":
                    return "is_first_tick()"
                if node.member == "isconfirmed":
                    return "is_last_tick_"
                if node.member == "ishistory":
                    return "true"
                if node.member == "isrealtime":
                    return "false"
                if node.member == "islastconfirmedhistory":
                    return "barstate_islast_"
                return "false"
            if ns in ("backadjustment", "settlement_as_close"):
                # backadjustment.{on,off,inherit} / settlement_as_close.{on,off,inherit}
                # Emit as integer constants (accepted by engine, which ignores them).
                # Codegen silently drops these from request.security kwargs.
                # Unknown member falls back to "inherit" (2).
                return ON_OFF_INHERIT_MAP.get(node.member, "2")
            if ns == "adjustment":
                # adjustment.none / dividends / splits. Unknown member -> "none" (0).
                return ADJUSTMENT_MAP.get(node.member, "0")
            if ns == "dayofweek":
                return DAYOFWEEK_MAP.get(node.member, "0")
            if ns == "session":
                if node.member == "regular":
                    return 'std::string("regular")'
                if node.member == "extended":
                    return 'std::string("extended")'
                # session.isfirstbar / islastbar mark the chart's session day,
                # which the engine widens by the pre- and post-market bars an
                # extended-hours chart holds, and session.isfirstbar_regular /
                # islastbar_regular the regular session's day: the host's
                # session_is*bar_ and session_is*bar_regular_ facts
                # (tests/test_e2e_session_ismarket.py).
                if node.member in ("ismarket", "ispremarket", "ispostmarket"):
                    # A chart bar is in market when the kernel's in-session
                    # fact holds it (session_ismarket_): the bar read at its
                    # interval's first eligible instant, so a bar that opens
                    # in a break and holds the reopen is in market, as
                    # TradingView's TSE:7203 and CBOT:ZC1! 60-minute tapes
                    # flag it, and a D/W/M bar is
                    # (tests/test_e2e_session_windows.py). A run without a
                    # timeframe gets no such fact (script_tf_ is empty); there
                    # the session calendar answers at the bar's open
                    # (codegen/session_market.py). The time-of-day predicates
                    # test each instant's own weekday and window: ismarket
                    # missed the Sunday-evening open of a ":23456" session and
                    # every "0000-2400" bar (tests/test_e2e_session_ismarket.py).
                    # A bar in market is in neither extended session; off it,
                    # the engine's windows decide. A request.security payload
                    # runs on its own bars, whose timeframe the chart's facts
                    # do not describe, so it keeps the predicates at the
                    # security bar's time, with a warning.
                    args = "(syminfo_.session, syminfo_.timezone, current_bar_.timestamp)"
                    predicate = f"pine_session_{node.member}{args}"
                    if self._security_payload_depth:
                        if id(node) not in self._warned_security_session_sites:
                            self._warned_security_session_sites.add(id(node))
                            constant = ("always true" if node.member == "ismarket"
                                        else "always false")
                            # A bar that opens in a break and holds the reopen
                            # is in market (the chart's reading); pre- and
                            # post-market read a break as neither.
                            opens = (" (a bar that opens in a session break and "
                                     "holds the reopen reads as out of market)"
                                     if node.member == "ismarket" else "")
                            self._codegen_warning(
                                node,
                                f"session.{node.member} inside request.security keeps "
                                "the time-of-day predicate, which can differ from "
                                "TradingView: on an intraday chart it tests the "
                                f"security bar's open time{opens}, a session's day "
                                "mask on that instant's own weekday, and does not read "
                                f"\"2400\"; on a D/W/M chart it is {constant}. The "
                                "chart's own session flags read the engine's session "
                                "facts.",
                            )
                        return predicate
                    self._uses_session_market = True
                    ismarket = ("(script_tf_.empty() ? _pf_session_market_(syminfo_.session, "
                                "syminfo_.timezone, script_tf_, current_bar_.timestamp) "
                                ": session_ismarket_)")
                    if node.member == "ismarket":
                        return ismarket
                    return f"(!{ismarket} && {predicate})"
                if node.member == "isfirstbar":
                    return "session_isfirstbar_"
                if node.member == "islastbar":
                    return "session_islastbar_"
                if node.member == "isfirstbar_regular":
                    return "session_isfirstbar_regular_"
                if node.member == "islastbar_regular":
                    return "session_islastbar_regular_"
                return "false"
            if ns == "syminfo":
                _syminfo = SYMINFO_MEMBER_MAP.get(node.member)
                if _syminfo is not None:
                    return _syminfo
                # Defensive: support_checker rejects any syminfo.* member not in
                # SUPPORTED_SYMINFO (== frozenset(SYMINFO_MEMBER_MAP)). Reaching
                # here means the checker was bypassed or the two tables drifted.
                raise ValueError(
                    f"codegen: unhandled syminfo.{node.member} — analyzer should "
                    f"have rejected. Add it to SYMINFO_MEMBER_MAP."
                )
            if ns == "display":
                # plot_display (ints for C++; TV uses these in settings — backtest ignores)
                return DISPLAY_MAP.get(node.member, "0")
            if ns == "color":
                if node.member in COLOR_CONST_MAP:
                    return COLOR_CONST_MAP[node.member]
                return "0"
            # Drawing style/visual CONSTANT member reads (line.style_solid,
            # box.style_dashed, label.style_label_left, ...). These namespaces
            # left SKIP_NAMESPACES (their FuncCall forms now route to the arena),
            # but their constant members only ever feed dropped visual kwargs, so
            # they still lower to "0". The function names arrive as FuncCalls
            # (visit_call), never here. See spec §4.4.
            if ns in DRAWING_STYLE_NS:
                return "0"
            if ns in SKIP_NAMESPACES:
                return "0"
            if ns == "currency":
                return f'std::string("{node.member}")'
            if ns == "order":
                # order.ascending / order.descending. Unknown member -> "ascending".
                return ORDER_DIRECTION_MAP.get(node.member, 'std::string("ascending")')

        # Handle nested member access: strategy.oca.reduce, strategy.closedtrades.profit(), etc.
        if isinstance(node.object, MemberAccess):
            if isinstance(node.object.object, Identifier):
                outer_ns = node.object.object.name
                if outer_ns == "strategy":
                    sub = node.object.member
                    # strategy.commission.percent, strategy.oca.*, strategy.direction.*
                    if sub == "oca":
                        if node.member == "cancel":
                            return "1"
                        if node.member == "reduce":
                            return "2"
                        if node.member == "none":
                            return "0"
                        return "0"
                    if sub == "direction":
                        if node.member == "long":
                            return "1"
                        if node.member == "short":
                            return "-1"
                        if node.member == "all":
                            return "0"
                        return "0"
                    if sub == "commission":
                        if node.member == "percent":
                            return "0"
                        if node.member == "cash_per_order":
                            return "1"
                        if node.member == "cash_per_contract":
                            return "2"
                        return "0"
                    # strategy.closedtrades.first_index, strategy.opentrades.capital_held
                    if sub == "closedtrades" and node.member == "first_index":
                        # Hardcoded 0 is correct until the engine implements
                        # trade-list capping (Pine only advances first_index
                        # when the 9000-trade cap drops old trades).
                        return "0"
                    if sub == "opentrades" and node.member == "capital_held":
                        return "open_trades_capital_held()"
                    return "0"

        # Enum member access: EnumName.Member -> named int constant (matches emitted const)
        if isinstance(node.object, Identifier):
            name = node.object.name
            if name in self._enum_defs:
                members = self._enum_defs[name]
                if node.member in members:
                    return (
                        f"{self._safe_name(name)}_"
                        f"{self._safe_name(node.member)}"
                    )

        # Unknown member access — emit as string constant (e.g., enum values)
        obj = self._visit_expr(node.object)
        if isinstance(node.object, Identifier):
            name = node.object.name
            # If the variable is known (defined in symbol table or as a var),
            # member access on a scalar C++ type is invalid. This happens when
            # PineScript UDTs (type declarations) are used — we don't support
            # them yet, so emit a safe default.
            sym = self.ctx.symbols.resolve(name)
            if (
                sym is not None
                or name in self._var_names
                or name in self._current_loop_vars
                or name in self._current_func_param_types
                or name in self._current_func_locals
                or name in self._udt_var_types
            ):
                safe = self._call_site_var_name(
                    node.object, self._safe_name(name)
                )
                return f"{safe}.{node.member}"
            if name not in self.ctx.series_vars:
                # Unknown identifier — likely an enum value
                return f'std::string("{node.member}")'
        return f"{obj}.{node.member}"

    def _operand_na_kind(self, node, cpp_type: str) -> str | None:
        """Classify a relational operand by the ``na`` sentinel it can carry.

        Returns:

        * ``"int"``   — an ``int``/``int64_t`` expression that can hold the
          ``INT_MIN`` sentinel. Because that sentinel is a *finite* very-negative
          integer (not NaN), naive C++ diverges from Pine's falsy-on-na rule for
          EVERY relational — ordered (``<`` ``>`` ``<=`` ``>=``) and equality
          (``==`` ``!=``) alike.
        * ``"float"`` — a ``double`` expression that can hold NaN. IEEE already
          yields ``false`` for ``==`` ``<`` ``>`` ``<=`` ``>=`` against NaN
          (matching Pine's falsy), so the ONLY diverging float cell is ``!=``
          (IEEE ``NaN != x`` is true; Pine is falsy).
        * ``None``     — provably not na (numeric/bool literal, inlined
          compile-time constant) or a non-scalar type with no ``is_na`` overload;
          the naive emission is already correct.
        """
        if cpp_type not in _NA_SCALAR_CPP:
            return None
        # Literals are never na.
        if isinstance(node, (NumberLiteral, BoolLiteral)):
            return None
        # A bare ``na`` lowers to ``na<double>()`` — a real NaN, i.e. it IS na.
        if self._is_na_expr(node):
            return "float"
        # Inlined compile-time constants (non-input known vars) never hold na.
        if (isinstance(node, Identifier)
                and node.name in self._known_vars
                and node.name not in self._input_backed_vars
                and not self._known_var_is_lexically_shadowed(node.name)):
            return None
        return "int" if cpp_type in ("int", "int64_t") else "float"

    def _emit_na_relational(self, op: str, left: str, right: str) -> str:
        """Emit an na-aware relational: ``false`` when either operand is ``na``.

        Mirrors the ``nz()`` lambda idiom (``visit_call``): each operand is
        hoisted to a temporary so a stateful operand expression is evaluated
        exactly once (no double-step), then compared only when neither side is
        na. ``is_na`` resolves via the emitted ``using namespace pineforge;``
        (``double`` -> ``isnan``; integral -> ``== numeric_limits<T>::min()``).
        """
        return (f"([&]{{ auto _pna_l = ({left}); auto _pna_r = ({right}); "
                f"return !is_na(_pna_l) && !is_na(_pna_r) && "
                f"(_pna_l {op} _pna_r); }}())")

    def _emit_float_relational(self, op: str, left: str, right: str) -> str:
        """Emit Pine's na-aware float comparator with its fixed equality band.

        Pine evaluates each operand once, rejects ``na``, and converts an int
        to float when paired with a float. TradingView oracle probes pin a
        magnitude-independent comparison band: finite operands with
        ``abs(left - right) <= 1e-10`` compare equal. Every ordered operator is
        derived from that same predicate, so strict ``<``/``>`` are suppressed
        inside the band while ``<=``/``>=`` accept it.

        Exact equality is checked first so equal infinities remain equal;
        opposite infinities and finite/infinite pairs remain normally ordered.
        The subtraction can overflow only to infinity, which is safely outside
        the equality band. The preceding ``is_na`` guard keeps every NaN
        comparison falsy, including ``!=``.
        """
        compare = {
            "==": "_pfc_eq",
            "!=": "!_pfc_eq",
            "<": "(_pfc_l < _pfc_r) && !_pfc_eq",
            ">": "(_pfc_l > _pfc_r) && !_pfc_eq",
            "<=": "(_pfc_l < _pfc_r) || _pfc_eq",
            ">=": "(_pfc_l > _pfc_r) || _pfc_eq",
        }[op]
        return (
            f"([&]{{ auto _pna_l = ({left}); auto _pna_r = ({right}); "
            "double _pfc_l = static_cast<double>(_pna_l); "
            "double _pfc_r = static_cast<double>(_pna_r); "
            "bool _pfc_eq = (_pfc_l == _pfc_r) || "
            "(std::isfinite(_pfc_l) && std::isfinite(_pfc_r) && "
            "std::fabs(_pfc_l - _pfc_r) <= 1e-10); "
            f"return !is_na(_pna_l) && !is_na(_pna_r) && "
            f"({compare}); }}())"
        )

    def _lower_relational(self, op: str, left_node, right_node,
                          left_cpp: str, right_cpp: str) -> str:
        """Lower a Pine relational with Pine's na and float-precision rules.

        Shared by ``_visit_binop`` and the ``request.security`` expression
        builder so EVERY relational emission site honours Pine's rules. Any
        numeric comparison involving an inferred float gets the fixed-band
        wrapper for all six operators. Pure integer comparisons get the smaller
        KI-71 wrapper only when an operand can carry the INT_MIN ``na`` sentinel.
        Non-relational operators and nonnumeric operands fall through unchanged.
        """
        if op in _RELATIONAL_OPS:
            lt = self._infer_type(left_node)
            rt = self._infer_type(right_node)
            if lt in _NA_SCALAR_CPP and rt in _NA_SCALAR_CPP:
                if "double" in (lt, rt):
                    return self._emit_float_relational(op, left_cpp, right_cpp)
                lk = self._operand_na_kind(left_node, lt)
                rk = self._operand_na_kind(right_node, rt)
                int_na = "int" in (lk, rk)
                if int_na:
                    return self._emit_na_relational(op, left_cpp, right_cpp)
        return f"({left_cpp} {op} {right_cpp})"

    def _visit_binop(self, node: BinOp) -> str:
        # An int-literal-only ``+ - *`` tree whose exact value leaves int32 is
        # a 64-bit Pine int (``90 * 24 * 60 * 60 * 1000`` = 7 776 000 000);
        # C++ ``int`` literal arithmetic would wrap it. Fold it here and spell
        # the value as a 64-bit literal. In-range trees are emitted as before.
        folded = self._pure_int_literal_value(node)
        if folded is not None and not self._int_fits_int32(folded):
            return f"static_cast<int64_t>({folded}LL)"
        left = self._visit_expr(node.left)
        right = self._visit_expr(node.right)
        cpp_ops = {"and": "&&", "or": "||"}
        op = cpp_ops.get(node.op, node.op)
        if node.op in ("and", "or"):
            # Pine converts numeric operands to two-state booleans before
            # applying short-circuit logic.  C++ treats NaN and INT_MIN as
            # true, so both operands need the same na-aware truthiness rule.
            left = self._coerce_bool_expr(left, node.left)
            right = self._coerce_bool_expr(right, node.right)
            if self._pine_v5_body:
                # v5 evaluates both operands, left then right, whatever the
                # left one decides (TradingView: a stateful right operand
                # runs on every bar).
                return (f"([&]() -> bool {{ const bool _pf_v5_l = ({left}); "
                        f"const bool _pf_v5_r = ({right}); "
                        f"return _pf_v5_l {op} _pf_v5_r; }}())")
        if node.op == "+":
            lt = self._infer_type(node.left)
            rt = self._infer_type(node.right)
            if lt == "std::string" or rt == "std::string":
                def _as_string(rendered, inferred):
                    if inferred == "std::string":
                        return rendered
                    if inferred == "bool":
                        return f'(({rendered}) ? std::string("true") : std::string("false"))'
                    return f"std::to_string({rendered})"

                return f"({_as_string(left, lt)} + {_as_string(right, rt)})"
        # PineScript % works on floats — use std::fmod in C++
        if node.op == "%":
            return f"std::fmod((double)({left}), (double)({right}))"
        # Pine v6 always returns float for `/`, even on int/int operands
        # (breaking change from v5). C++ does int division on int operands,
        # so cast both sides to double to match Pine v6 semantics.
        # Ref: https://www.tradingview.com/pine-script-docs/concepts/operators/
        if node.op == "/":
            return f"((double)({left}) / (double)({right}))"
        if node.op in ("==", "!=") and self._pine_v5_body:
            self._refuse_v5_bool_na_observer(
                node, f"'{node.op}'", (node.left, node.right))
        return self._lower_relational(op, node.left, node.right, left, right)

    def _refuse_v5_bool_na_observer(self, node, what: str, operands) -> None:
        """A v5 bool can be na (a comparison with na, an na literal, a bool's
        history before the first bar); PineForge's bool cannot. It reads as
        false wherever v5 casts it to a bool (TradingView's tape of
        ``xc_v5_lib``), but ``na()``, ``nz()``, ``fixnan()``,
        ``str.tostring``/``str.format`` and ``==``/``!=`` of a bool tell the
        third state apart (a v5 probe's tape outside this repository:
        ``na(b)`` true, ``b == false`` na)."""
        if any(self._infer_type(o) == "bool" for o in operands if o is not None):
            library = (getattr(node.loc, "file", None) or "a v5 library")
            self._codegen_error(
                node,
                f"library '{library}' is //@version=5: {what} of a bool reads "
                "v5's third bool state (na), which PineForge's two-state bool "
                "does not keep",
            )

    def _visit_unaryop(self, node: UnaryOp) -> str:
        operand = self._visit_expr(node.operand)
        if node.op == "not":
            return f"!({self._coerce_bool_expr(operand, node.operand)})"
        return f"({node.op}{operand})"

    def _visit_session_history(self, node: Subscript, series_idx: str) -> str:
        """``session.<flag>[k]``: the value the flag had k bars ago at the top
        level of the script, k calls ago in a function body.

        TradingView's tapes (``tests/test_e2e_session_history.py``) read a
        top-level offset by bars, in a block and on a lazy operand too, and a
        function body's by the calls of its call site, whichever operand or
        block holds the read. A top-level read indexes the flag's Series,
        pushed on every chart bar (``_prescan_session_history``); a function
        body's indexes its emitted call site's Series, pushed at the function's
        entry (``_prepare_inline_history_members``). A request.security
        expression that reaches the read through its own operators builds it
        on the requested clock (security.py); one that reaches it through a
        call's argument or a variable has no such history here and is refused
        at the read. So is a read emitted in a function the analyzer lists in
        ``session_history_unsafe`` (a method, or a function a method, a
        request.security expression or a UDT field default reaches), and one
        with no registered Series: never shared state. The refusal waits for
        the C++ (``_settle_session_reads``): a read in an argument the codegen
        renders and leaves out refuses nothing. A read in a function this
        analysis did not clone (``session_uncloned``) asks for its clones
        instead.
        """
        flag = node.object.member
        where = next((n for n in (node, node.index, node.object)
                      if getattr(n, "loc", None) is not None), node)
        if self._security_payload_depth:
            return self._refused_session_read(
                node, where,
                f"session.{flag}[...] cannot be read here: PineForge keeps a session "
                "flag's history on the requested clock only for a read the "
                "request.security expression reaches through its own operators, "
                "not through a call's argument, a function or a variable.",
            )
        owner = self._session_call_owner.get(id(node))
        if owner is not None:
            why = (getattr(self.ctx, "session_history_unsafe", None) or {}).get(owner)
            if why:
                return self._refused_session_read(
                    node, where,
                    f"session.{flag}[...] cannot be read in {owner.split('.')[-1]}(), "
                    f"{why}: PineForge keeps a session flag's history in a function by "
                    "each of its call sites, and cannot tell this one apart.",
                )
            member = self._inline_history_member_by_key.get(
                ("session_call", owner, flag, self._current_instance_name))
            if member is not None:
                return f"{member}[{series_idx}]"
        elif flag in self._session_history_flags:
            return f"{self._session_history_member(flag)}[{series_idx}]"
        uncloned = getattr(self.ctx, "session_uncloned", None) or ()
        return self._refused_session_read(
            node, where,
            f"session.{flag}[...] is not supported here: PineForge keeps a session "
            "flag's history for the script's top level, a function body and a "
            "request.security expression that reads it.",
            owner if owner in uncloned else None,
        )

    def _refused_session_read(self, node: Subscript, where, message: str,
                              uncloned: str | None = None) -> str:
        """A stand-in for a ``session.<flag>[k]`` read PineForge keeps no
        history for: a name only this read spells, the refusal raised (or, in
        the uncloned function ``uncloned``, its clones asked for) if the
        emitted code holds it (``_settle_session_reads``)."""
        key = (id(node), message)
        name = self._refused_session_read_names.get(key)
        if name is None:
            name = self._allocate_generated_cpp_name(
                f"_refused_session_read_{len(self._refused_session_reads) + 1}",
                self._session_names_used)
            self._refused_session_read_names[key] = name
            self._refused_session_reads[name] = (node, where, message, uncloned)
        return name

    def _call_site_var_name(self, node: Identifier, safe: str) -> str:
        """``safe`` as this read names it under the active var remap.

        In a callable body the clone remap lists every callable's history and
        ``var`` members, for nested instances to compose, so it only renames a
        local binding. A read the analyzer resolved to a script-scope binding
        (also before a same-named local shadows it), a loop binder or a
        parameter keeps its own name: renaming ``src`` to another callable's
        ``src_cs1``, which nothing writes, read ``na``. Outside callables the
        remap holds block-scoped renames only, which a top-level branch
        binding resolved to the global scope still needs.
        """
        if not self._active_var_remap or safe not in self._active_var_remap:
            return safe
        if getattr(self, "_active_func_name", None) is not None and (
            self.ctx.identifier_binding_scopes.get(id(node)) == "global"
            or node.name in self._current_loop_vars
            or node.name in self._current_func_param_types
        ):
            return safe
        return self._active_var_remap[safe]

    def _history_offset_cpp(self, idx: str, index_node) -> str:
        """The ``int`` offset of a history read, safe right after a ``[``.

        A double index narrows through the na-preserving lambda
        ``[&](){ ... }()`` (``_coerce_int_slot``); spelled straight after a
        subscript's ``[`` it reads ``[[``, which C++ parses as an attribute,
        so a lambda offset is parenthesized. TradingView truncates a
        fractional index toward zero (``x[5.5]`` reads ``x[5]``), as the
        cast does.
        """
        idx_int = self._coerce_int_slot(idx, index_node, "int")
        if (idx_int == idx
                and not self._emitted_value_is_double(index_node)):
            idx_int = pine_index_int_cast(idx)
        if idx_int.startswith("["):
            idx_int = f"({idx_int})"
        return idx_int

    def _visit_subscript(self, node: Subscript) -> str:
        idx = self._visit_expr(node.index)
        # Series::operator[] accepts C++ int. A Pine int can be backed by an
        # int64_t timestamp slot; implicitly narrowing its na sentinel to int
        # turns it into zero on arm64 and reads the current bar. Preserve the
        # original value type before narrowing every dynamic series offset.
        series_idx = (
            idx if (isinstance(node.index, NumberLiteral)
                    and isinstance(node.index.value, int))
            else pine_index_int_cast(idx)
        )
        if isinstance(node.object, Identifier):
            name = node.object.name
            # A script variable read through history in a plain UDF body: the
            # call site's own history of it.
            if id(node) in self.ctx.func_global_history_nodes:
                member = self._function_global_history_member(name)
                if member is not None:
                    return f"{member}[{series_idx}]"
            # Function parameters that are series — src[N] → src[N]
            if name in self._current_func_series_params:
                return f"{self._safe_name(name)}[{series_idx}]"
            # Function parameters are scalars — src[0] → src, src[N>0] → src
            if name in self._current_func_param_types:
                return self._safe_name(name)
            if name in BAR_FIELDS or name in BAR_SERIES_PUSH:
                # Index matches Pine: [0] current bar, [k] k bars ago (runtime Series deque).
                return f"_s_{name}[{series_idx}]"
            # Apply per-call-site / exact block-member remap before deciding
            # whether the current lexical binding is a Series.
            safe = self._call_site_var_name(node.object, self._safe_name(name))
            if self._binding_is_series(name, safe):
                # Same Pine [k] semantics as Series in runtime/series.hpp
                return f"{safe}[{series_idx}]"
            spec = self._collection_spec_for_name(name)
            if spec is not None and spec.kind in ("array", "map"):
                self._codegen_warning(
                    node,
                    f"{spec.kind} history indexing uses the current collection "
                    "element in PineForge; the engine does not retain per-bar "
                    "collection IDs, so this result can differ from TradingView "
                    "and a missing index is not represented faithfully.",
                )
                return f"{self._collection_receiver_expr(name)}[{idx}]"
        # Handle strategy.* history access (e.g., strategy.position_size[1])
        if isinstance(node.object, MemberAccess):
            if isinstance(node.object.object, Identifier):
                ns = node.object.object.name
                if ns == "strategy":
                    # Strategy variables with history operator — use series tracking
                    member = node.object.member
                    series_name = f"_strat_{member}"
                    if series_name not in self._strategy_series_vars:
                        self._strategy_series_vars.add(series_name)
                    return f"{series_name}[{series_idx}]"
        if self._is_session_flag(node.object):
            return self._visit_session_history(node, series_idx)
        # History reference applied directly to an inline call result, e.g.
        # ``ta.highest(high, 10)[1]`` or ``f()[2]``. In Pine the call yields a
        # series, so ``[k]`` reads its value k bars ago — but the call lowers to
        # a freshly-computed C++ scalar, and ``scalar[k]`` is not subscriptable.
        # Materialize the result into a checkpoint-owned class-member
        # ``Series<T>`` that pushes (new bar) / updates (intrabar) the value
        # exactly once per evaluation — same semantics as every other series in
        # the strategy — and read ``[k]`` off it. The inner call is emitted once
        # so its own stateful indicator is not double-stepped.  The deterministic
        # member identity includes the emitted UDF variant; separate Pine call
        # sites never share history, and on_bar clears every synthetic member at
        # run-start even when this particular expression is conditional.
        if isinstance(node.object, FuncCall):
            # ``[k]`` on a hoisted top-level lazy-edge TA call: its Series was
            # pushed unconditionally before the statement, so ``[1]`` is the
            # previous chart bar in every run mode (``_lazy_edge_ta_hoist_plan``).
            hoisted_member = self._hoisted_hist_reads.get(id(node))
            if hoisted_member is not None:
                idx_int = self._history_offset_cpp(idx, node.index)
                return f"{hoisted_member}[{idx_int}]"
            inner = self._visit_expr(node.object)
            cpp_t = self._infer_type(node.object)
            if cpp_t not in ("double", "int", "int64_t", "bool"):
                cpp_t = "double"
            member = self._inline_history_member("hist_call", node)
            ta_site = self._get_ta_site(node.object)
            ta_name = (
                self._ta_name_from_site(ta_site)
                if ta_site is not None
                else ""
            )
            if (
                ta_site is not None
                and ta_name in {"highest", "lowest"}
                and self._ta_site_uses_precalc(ta_site)
            ):
                # Static chart Highest/Lowest sites already advance in the
                # full-bar precalculation pass, including when the source call
                # sits behind a Pine-v6 lazy boolean edge. Their direct history
                # operator must use that same chart-bar clock. Advancing only
                # ``_hist_call`` when the lazy RHS is reached changes ``[1]``
                # into "previous evaluation" and can permanently miss a first
                # qualifying signal (Filter V2's source-bound v6 oracle).
                #
                # Keep evaluation lazy: this branch only reads the immutable
                # precalculated result when the expression is reached. Dynamic
                # runs, UDF sites, and non-precalculated calls retain the
                # established call-local history fallback below.
                ta_mem = self._ta_member_name(ta_site)
                precalc = f"_precalc_{ta_mem}"
                idx_int = self._history_offset_cpp(idx, node.index)
                return (
                    f"([&]() -> {cpp_t} {{ "
                    f"if (_use_precalc) {{ "
                    f"auto _pf_hist_offset_raw = ({idx}); "
                    f"if (is_na(_pf_hist_offset_raw)) return na<{cpp_t}>(); "
                    f"long double _pf_hist_offset_numeric = "
                    f"static_cast<long double>(_pf_hist_offset_raw); "
                    f"if (bar_index_ < 0 || _pf_hist_offset_numeric < 0.0L || "
                    f"_pf_hist_offset_numeric > "
                    f"static_cast<long double>(bar_index_)) "
                    f"return na<{cpp_t}>(); "
                    f"std::size_t _pf_hist_offset = "
                    f"static_cast<std::size_t>(_pf_hist_offset_numeric); "
                    f"std::size_t _pf_hist_bar = "
                    f"static_cast<std::size_t>(bar_index_) - _pf_hist_offset; "
                    f"if (_pf_hist_bar >= {precalc}.size()) "
                    f"return na<{cpp_t}>(); "
                    f"return {precalc}[(std::size_t)_pf_hist_bar]; }} "
                    f"{cpp_t} _hv = ({inner}); "
                    f"if (history_advances_new_bar()) {member}.push(_hv); "
                    f"else {member}.update(_hv); "
                    f"return {member}[{idx_int}]; }}())"
                )
            idx_int = self._history_offset_cpp(idx, node.index)
            return (
                f"([&]() -> {cpp_t} {{ "
                f"{cpp_t} _hv = ({inner}); "
                f"if (history_advances_new_bar()) {member}.push(_hv); "
                f"else {member}.update(_hv); "
                f"return {member}[{idx_int}]; }}())"
            )
        # History on an operator expression (``(a > b)[1]``) reads the value
        # the expression had k bars ago: its prepass-registered synthetic
        # Series (``_is_compound_history_object``) is pushed once per
        # evaluation, like the inline call results above.
        compound_member = (
            self._inline_history_member_by_key.get(
                ("hist_call", id(node), self._current_instance_name)
            )
            if isinstance(node.object, (BinOp, UnaryOp, Ternary))
            else None
        )
        if compound_member is not None:
            cpp_t = self._infer_type(node.object)
            if cpp_t in ("double", "int", "int64_t", "bool"):
                inner = self._visit_expr(node.object)
                idx_int = self._history_offset_cpp(idx, node.index)
                return (
                    f"([&]() -> {cpp_t} {{ "
                    f"{cpp_t} _hv = ({inner}); "
                    f"if (history_advances_new_bar()) {compound_member}.push(_hv); "
                    f"else {compound_member}.update(_hv); "
                    f"return {compound_member}[{idx_int}]; }}())"
                )
        obj = self._visit_expr(node.object)
        # If subscripting a non-series variable (e.g., function parameter),
        # src[0] → src (current value), src[N>0] → src (can't access history)
        if isinstance(node.object, Identifier):
            name = node.object.name
            if (name not in BAR_FIELDS and name not in BAR_SERIES_PUSH
                    and name not in self.ctx.series_vars
                    and name not in self._var_names):
                return obj
        idx_int = self._history_offset_cpp(idx, node.index)
        return f"{obj}[{idx_int}]"
