"""Function-call dispatch visitors for the codegen.

``CallVisitor`` mixin holds the function-call dispatcher and the
per-namespace dispatch helpers (``strategy.*``, ``color.*``, ``str.*``,
``math.*``, ``fixnan``).

``_visit_func_call`` is the central entry point for any
:class:`FuncCall` AST node. It first handles UDT-method calls and
``obj.method(args)`` style receivers, then resolves the callee to a
``(func_name, namespace)`` pair and dispatches by namespace:

* ``strategy.*``  -> ``_visit_strategy_call`` (entry / exit / close /
  cancel / order / closedtrades.* / opentrades.* / convert_to_*).
* ``ta.*``        -> ``ta.tr`` is inlined; other sites resolve to the
  ``member.compute(...)`` / ``member.recompute(...)`` form via the
  ``TaSiteHelper``; ``ta.pivot_point_levels`` is a free function.
* ``input`` / ``input.*`` -> runtime ``get_input_*()`` getters via
  ``InputHelper``.
* ``str.*``       -> ``_visit_str_call`` (tostring / substring / format
  / format_time / replace + the ``STR_FUNC_MAP`` shortcuts).
* ``math.*``      -> ``_visit_math_call`` (round-to-mintick, todegrees /
  toradians, random, n-ary avg / max / min, ``MATH_FUNC_MAP``).
* ``color.*``     -> ``_visit_color_call`` (new / r / g / b / t / rgb /
  from_gradient).
* ``array.*`` / ``map.*`` / ``matrix.*`` -> functional and method-syntax
  forms, delegating to ``TypeInferer``'s ``_array_method_expr`` /
  ``_map_method_expr`` and the ``MATRIX_METHODS`` table.
* ``request.security``, ``ticker.*``, ``runtime.*``, ``log.*``,
  ``timeframe.*``, ``time`` / ``time_close`` / ``timestamp``, type
  casts ``int`` / ``float`` / ``bool`` / ``string``.
* UDT constructors (``TypeName.new(...)``) and copies.

Anything left over is treated as a generic user-defined or unknown
function call: the visitor merges kwargs by parameter name (using
``FuncInfo`` for user functions or the ``signatures`` registry for
intrinsics), passes ``Series<T>`` references for series-typed params,
and emits ``namespace::func(args)`` (or the per-call-site variant
``func_csN`` for functions cloned by the analyzer's call-site
splitter).

``_visit_fixnan`` allocates a fresh persistent state member each time
it is called (``_prev_fixnan_<n>``).

``_resolve_func_args`` is a small helper that merges positional args
and kwargs into a ``{param_name: arg_node}`` dict using the parameter
ordering from the ``signatures`` registry; used by
``_visit_strategy_call`` to resolve ``strategy.entry`` /
``strategy.exit`` / ... by keyword.

These visitors were extracted from ``base.py``'s ``CodeGen`` class as
step 10 of the codegen package refactor; behaviour is preserved
verbatim. The mixin owns no state of its own — it reads/writes only
attributes already established on the host class (``CodeGen``).

Mixin contract — host class must provide the following attributes
(all set by ``CodeGen.__init__`` or other mixins):

- ``self.ctx`` (``AnalyzerContext``): symbol table source. The
  visitors read ``ctx.symbols.resolve`` (``_visit_str_call`` enum
  branch), ``ctx.series_vars`` (series-arg lowering),
  ``ctx.func_series_vars`` (per-function series-param indices),
  ``ctx.func_call_cs_map`` and ``ctx.func_call_site_counts``
  (per-call-site variant naming).
- ``self._var_names`` (``set[str]``): names declared at module scope;
  consulted by the ``obj.method`` receiver-detection branch.
- ``self._global_member_vars`` (``set[str]``): non-``var`` global
  declarations emitted as class members (same branch).
- ``self._current_func_param_types`` (``dict[str, ...]``): parameters
  of the function currently being emitted (treated as locals for the
  receiver guard).
- ``self._current_loop_vars`` (``set[str]``): for-in iterator names
  (also receivers of ``.method()`` calls).
- ``self._current_input_var_name`` (``str | None``): contextual var
  name used as the title-fallback for ``input(...)`` calls.
- ``self._array_vars`` / ``self._map_vars`` (``set[str]``) and
  ``self._matrix_specs`` (``dict[str, TypeSpec]``): collection-typed
  variables; gate the receiver-method branches in ``_visit_func_call``.
- ``self._udt_defs`` (``dict[str, list]``), ``self._udt_var_types``
  (``dict[str, str]``), ``self._udt_param_udt`` (``dict[str, str]``),
  ``self._udt_field_type_specs`` (``dict[str, dict[str, TypeSpec]]``):
  UDT type info for ``TypeName.new(...)`` constructors,
  ``TypeName.copy(...)``, and the ``obj.method()`` UDT-method dispatch.
- ``self._enum_member_strings`` (``dict[str, list[str]]``): enum -->
  display-string table; used by ``_visit_str_call`` to render
  ``str.tostring(enumVar)`` as the field title rather than the int
  index.
- ``self._func_info_map`` (``dict[str, FuncInfo]``): user-defined
  function lookup; drives kwarg merging, UDT method dispatch, and the
  series-arg classification.
- ``self._func_names`` (``set[str]``): user-defined function names;
  controls when ``_func_safe_name`` is applied and when the
  per-call-site variant naming kicks in.
- ``self._security_calls`` (``list[dict]``): normalized
  ``request.security`` records; matched by ``expr_node`` identity to
  bind ``_req_sec_<id>`` result names.
- ``self._active_call_site_idx`` (``int | None``): set during
  per-call-site function emission; controls ``_csN`` variant naming
  for sub-function calls.
- ``self._active_var_remap`` (``dict[str, str]``): per-call-site
  rename map for cloned function-local var/series names; consulted by
  the series-arg lowering helper.
- ``self._fixnan_counter`` (``int``): monotonically incremented by
  ``_visit_fixnan`` to mint fresh ``_prev_fixnan_<n>`` member names.
- ``self._random_call_counter`` (``int``): monotonically incremented
  by ``_visit_math_call`` for ``math.random`` so each call gets a
  unique site id (used to seed the runtime PRNG).

Sibling-mixin methods consumed via ``self``:

- ``NamingHelper`` (``codegen/helpers.py``): ``_safe_name``,
  ``_resolve_callee``, ``_func_safe_name``.
- ``TypeInferer`` (``codegen/types.py``): ``_type_spec_to_cpp``,
  ``_type_spec_from_expr``, ``_type_spec_from_hint_name``,
  ``_default_for_spec``, ``_array_method_expr``,
  ``_map_method_expr``, ``_array_spec_for_name``,
  ``_map_spec_for_name``.
- ``TaSiteHelper`` (``codegen/ta.py``): ``_get_ta_site``,
  ``_ta_member_name``, ``_ta_compute_args_for_site``.
- ``InputHelper`` (``codegen/input.py``): ``_is_input_call_by_name``,
  ``_get_input_default``, ``_get_input_title``,
  ``_input_type_to_getter``,
  ``_enforce_enum_declared_before_input_enum``.
- ``TopLevelEmitter`` (``codegen/emit_top.py``):
  ``_emit_udt_method_cpp_name``.
- ``ExprVisitor`` (``codegen/visit_expr.py``): ``_visit_expr``.
- ``CodeGen.base``: ``_codegen_error``.

The mixin avoids importing from ``base.py`` to stay free of cycles;
all tables it needs come from ``codegen/tables.py``, AST classes from
``..ast_nodes``, and PineScript signatures from ``.. import signatures``.
"""

from __future__ import annotations

from ..ast_nodes import (
    ASTNode,
    FuncCall,
    Identifier,
    MemberAccess,
    NaLiteral,
    TupleLiteral,
    StringLiteral,
    VarDecl,
)
from ..symbols import TypeSpec
from .. import signatures as sigs
from .drawing import ALL_DRAWING_METHODS
from .tables import (
    ARRAY_DRAWING_NEW_CTORS,
    ARRAY_METHODS,
    BAR_FIELDS,
    BAR_SERIES_PUSH,
    CHECKED_ARRAY_METHOD_KWARGS,
    DRAWING_NS,
    DRAWING_TYPE_TO_CPP,
    MAP_METHODS,
    MAP_METHOD_KWARGS,
    MATH_FUNC_MAP,
    MATRIX_METHODS,
    MATRIX_METHOD_KWARGS,
    MATRIX_NUMERIC_ONLY,
    MATRIX_SORT_ALLOWED_GENERIC_ELEMS,
    SKIP_FUNC_NAMES,
    SKIP_NAMESPACES,
    SKIP_VAR_TYPES,
    STR_FUNC_MAP,
    TIME_FIELD_EXPRS,
    _math_minmax_na_expr,
    _merge_kwargs,
    _merge_kwargs_with_defaults,
    tz_time_field_lambda,
)


def _parse_pine_datestring_ms(text: str) -> int | None:
    """Parse a Pine ``timestamp(dateString)`` literal to Unix milliseconds.

    Pine v6 accepts ISO-8601 strings ("2025-01-01", "2011-10-10T14:48:00",
    with optional offset) and the "DD MMM YYYY hh:mm:ss ±HHMM" /
    "MMM DD YYYY ..." forms. A dateString without a time zone is GMT+0 per
    the Pine reference. Returns None when the string cannot be parsed.
    """
    import re
    from datetime import datetime, timezone, timedelta

    txt = text.strip()
    # Pine dateStrings may carry a trailing timezone WORD ("2024-01-01 00:00 UTC",
    # "1 Jan 2020 09:30 GMT+2") that neither fromisoformat nor the strptime forms
    # below can read. Peel it off and fold it into an explicit offset (UTC/GMT
    # with an optional ±H[:MM] suffix; the bare word is +00:00).
    tzoff = None
    m = re.search(r"\s+(?:UTC|GMT)([+-]\d{1,2})?(?::?(\d{2}))?$", txt, re.I)
    if m:
        txt = txt[: m.start()].strip()
        h = int(m.group(1) or 0)
        mm = int(m.group(2) or 0)
        tzoff = timezone(timedelta(hours=h, minutes=(mm if h >= 0 else -mm)))
    dt = None
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        for fmt in (
            "%d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M %z",
            "%d %b %Y %H:%M:%S", "%d %b %Y %H:%M", "%d %b %Y",
            "%b %d %Y %H:%M:%S %z", "%b %d %Y %H:%M %z",
            "%b %d %Y %H:%M:%S", "%b %d %Y %H:%M", "%b %d %Y",
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        ):
            try:
                dt = datetime.strptime(txt, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=(tzoff or timezone.utc))
    return int(dt.timestamp() * 1000)


class CallVisitor:
    """Function-call dispatch visitor methods shared across the codegen.

    Mixed into ``CodeGen``; not intended to be instantiated standalone.
    See the module docstring for the full host-class state contract."""

    # ------------------------------------------------------------------
    # Function-call dispatch
    # ------------------------------------------------------------------

    def _udt_method_call_emit_name(self, fi, node: FuncCall) -> str:
        """Resolve a UDT method call through the ordinary UDF clone graph."""
        base = self._emit_udt_method_cpp_name(fi)
        dispatch = self._instance_dispatch.get(
            (self._current_instance_name, id(node))
        )
        if dispatch is not None:
            return dispatch

        cs_info = self.ctx.func_call_cs_map.get(id(node))
        if self._active_call_site_idx is not None and cs_info is not None:
            return f"{base}_cs{self._active_call_site_idx}"
        if cs_info is not None and cs_info[0] == fi.name:
            return f"{base}_cs{cs_info[1]}"
        if (self._active_call_site_idx is not None
                and self.ctx.func_call_site_counts.get(fi.name, 0) > 1):
            return f"{base}_cs{self._active_call_site_idx}"
        return base

    def _array_init_value_expr(self, elem_spec: TypeSpec | None, value_node) -> str:
        if isinstance(value_node, NaLiteral):
            if elem_spec is not None and elem_spec.kind == "udt":
                return self._default_for_spec(elem_spec)
            cpp_type = self._type_spec_to_cpp(elem_spec)
            if cpp_type in ("double", "int", "int64_t", "bool", "std::string"):
                return f"na<{cpp_type}>()"
            return self._default_for_spec(elem_spec)
        return self._visit_expr(value_node)

    def _array_method_args(
        self, method: str, arg_nodes: list, spec: TypeSpec | None,
    ) -> list[str]:
        elem_spec = (
            spec.element
            if spec is not None and spec.kind == "array" and spec.element is not None
            else TypeSpec.primitive("float")
        )
        value_arg_indexes = {
            "set": {1},
            "push": {0},
            "unshift": {0},
            "insert": {1},
            "fill": {0},
            "includes": {0},
            "indexof": {0},
            "lastindexof": {0},
            "binary_search": {0},
            "binary_search_leftmost": {0},
            "binary_search_rightmost": {0},
        }.get(method, set())
        return [
            self._array_init_value_expr(elem_spec, arg)
            if idx in value_arg_indexes
            else self._visit_expr(arg)
            for idx, arg in enumerate(arg_nodes)
        ]

    def _array_method_arg_nodes(self, method: str, node: FuncCall) -> list:
        """Merge checked-array method kwargs into Pine signature order."""
        param_names = CHECKED_ARRAY_METHOD_KWARGS.get(method)
        if param_names is None:
            return list(node.args)
        return _merge_kwargs(node.args, node.kwargs, param_names, lambda arg: arg)

    def _array_function_arg_nodes(self, method: str, node: FuncCall) -> list:
        """Merge ``array.method(id=..., ...)`` arguments in signature order."""
        param_names = CHECKED_ARRAY_METHOD_KWARGS.get(method)
        if param_names is None:
            return list(node.args)
        return _merge_kwargs(
            node.args, node.kwargs, ["id", *param_names], lambda arg: arg
        )

    def _map_identifier_is_visible_binding(self, node: FuncCall) -> bool:
        """Whether ``map`` is a lexical value at this exact source position.

        Global collection registries are intentionally pre-populated before
        code emission, so raw membership in ``_var_names`` / collection maps
        cannot answer this question: a later ``map`` declaration must not
        capture an earlier built-in ``map.*`` call.  Callable/block overlays,
        by contrast, are activated in source order and therefore take first
        refusal.  For a direct top-level binding, compare the containing
        top-level statement indexes; the binding becomes visible only after
        its declaration statement, never inside its own RHS.
        """
        name = "map"
        if (
            name in getattr(self, "_current_func_param_types", {})
            or name in getattr(self, "_current_func_collection_specs", {})
            or name in getattr(self, "_current_func_collection_shadows", set())
            or name in getattr(self, "_current_func_local_types", {})
            or name in getattr(self, "_current_loop_vars", set())
            or getattr(self, "_block_map_binding_visible", False)
        ):
            return True

        top_index_by_node = getattr(self, "_top_level_index_by_ast_node", None)
        top_index_by_span = getattr(self, "_top_level_index_by_source_span", None)
        map_decl_index = getattr(self, "_top_level_map_decl_index", None)
        if top_index_by_node is None or top_index_by_span is None:
            top_index_by_node = {}
            top_index_by_span = {}
            ambiguous_spans = set()
            map_decl_index = None
            for index, statement in enumerate(self.ctx.ast.body):
                for candidate in self._walk_ast(statement):
                    top_index_by_node[id(candidate)] = index
                    loc = getattr(candidate, "loc", None)
                    if loc is not None:
                        span = (
                            type(candidate), loc.file, loc.line, loc.col, loc.end_col,
                        )
                        previous = top_index_by_span.get(span)
                        if previous is None:
                            top_index_by_span[span] = index
                        elif previous != index:
                            ambiguous_spans.add(span)
                if (
                    map_decl_index is None
                    and isinstance(statement, VarDecl)
                    and statement.name == name
                ):
                    map_decl_index = index
            for span in ambiguous_spans:
                top_index_by_span.pop(span, None)
            self._top_level_index_by_ast_node = top_index_by_node
            self._top_level_index_by_source_span = top_index_by_span
            self._top_level_map_decl_index = map_decl_index

        if map_decl_index is None:
            return False
        call_index = top_index_by_node.get(id(node))
        if call_index is None:
            # Setup-time rewrites (notably request.security timeframe input
            # substitution) may clone an expression.  Those clones retain the
            # original source span, so recover their containing top-level
            # statement from the unmodified AST instead of treating every
            # synthetic node as if all globals were already visible.
            loc = getattr(node, "loc", None)
            if loc is not None:
                call_index = top_index_by_span.get(
                    (type(node), loc.file, loc.line, loc.col, loc.end_col)
                )
        if call_index is None:
            # With no identity or source ancestry there is no evidence that a
            # later global binding was visible.  Fail closed to the built-in
            # namespace; synthetic lexical receivers must preserve provenance.
            return False
        return map_decl_index < call_index

    def _map_call_arg_nodes(
        self,
        method: str,
        node: FuncCall,
        *,
        functional: bool,
        allow_keywords: bool,
    ) -> list:
        """Validate and bind one established map-call form.

        Map lowerings index directly into their argument arrays.  Before this
        guard, malformed calls therefore either reached a raw ``IndexError``
        or, for keyword-only ``map.*`` functional calls, silently lowered to
        ``0``.  Keep routing policy separate from signature validation: only
        the typed-map UDF-parameter method lane currently accepts canonical
        keywords; all other lanes retain their established positional-only
        semantics and fail closed when keywords are supplied.
        """
        if method == "new":
            param_names: list[str] = []
        else:
            method_params = MAP_METHOD_KWARGS.get(method)
            if method_params is None:
                return list(node.args)
            param_names = (["id", *method_params] if functional
                           else list(method_params))

        signature = f"map.{method}({', '.join(param_names)})"
        unknown = sorted(set(node.kwargs) - set(param_names))
        if unknown:
            name = unknown[0]
            hint = f"Expected {signature}."
            if method == "put_all" and name == "from":
                hint = f"Use 'id2=' for the source map. Expected {signature}."
            self._codegen_error(
                node,
                f"map.{method}: unknown keyword argument '{name}'",
                hint=hint,
            )

        if len(node.args) > len(param_names):
            self._codegen_error(
                node,
                (
                    f"map.{method}: too many positional arguments "
                    f"(expected {len(param_names)}, got {len(node.args)})"
                ),
                hint=f"Expected {signature}.",
            )

        for name in param_names[:len(node.args)]:
            if name in node.kwargs:
                self._codegen_error(
                    node,
                    (
                        f"map.{method}: argument '{name}' passed both "
                        "positionally and by keyword"
                    ),
                    hint=f"Expected {signature}; bind each argument once.",
                )

        bound: list = [None] * len(param_names)
        for index, arg in enumerate(node.args):
            bound[index] = arg
        for name, value in node.kwargs.items():
            bound[param_names.index(name)] = value

        missing = [
            name for name, value in zip(param_names, bound) if value is None
        ]
        if missing:
            self._codegen_error(
                node,
                f"map.{method}: missing required argument '{missing[0]}'",
                hint=f"Expected {signature}.",
            )

        if node.kwargs and not allow_keywords:
            form = "functional" if functional else "receiver-method"
            self._codegen_error(
                node,
                (
                    f"map.{method}: keyword arguments are not supported "
                    f"for this {form} call form"
                ),
                hint=f"Use the established positional form: {signature}.",
            )

        return bound

    def _map_param_method_expr(
        self, map_expr: str, method: str, arg_nodes: list, spec: TypeSpec,
    ) -> str:
        """Lower one map operation with receiver-first, ordered single use.

        C++17 sequences a member-call receiver before its arguments but does
        not order sibling arguments. Pine requires receiver, key, then value.
        Nested immediately-invoked lambdas bind the receiver first and every
        argument in source/signature order, while retaining lvalue aliases and
        extending temporary receiver lifetime through the operation.
        """
        args = [self._visit_expr(arg) for arg in arg_nodes]
        occupied = "\n".join((map_expr, *args))
        counter = getattr(self, "_map_param_arg_counter", 0)
        bindings: list[tuple[str, str]] = []
        for arg in args:
            while True:
                token = f"__pf_map_param_arg_{counter}"
                counter += 1
                if token not in occupied:
                    break
            bindings.append((token, arg))
        self._map_param_arg_counter = counter

        receiver_counter = getattr(self, "_map_receiver_counter", 0)
        while True:
            receiver_token = f"__pf_map_receiver_{receiver_counter}"
            receiver_counter += 1
            if receiver_token not in occupied:
                break
        self._map_receiver_counter = receiver_counter

        lowered = self._map_method_expr(
            receiver_token, method, [token for token, _arg in bindings], spec
        )
        for token, arg in reversed(bindings):
            lowered = (
                f"[&](auto&& {token})->decltype(auto){{ "
                f"return {lowered}; }}(({arg}))"
            )
        return (
            f"[&](auto&& {receiver_token})->decltype(auto){{ "
            f"return {lowered}; }}(({map_expr}))"
        )

    def _expr_contains_map_operation(
        self,
        node,
        visiting_callables: frozenset[str] = frozenset(),
        lexical_specs: dict[str, TypeSpec | None] | None = None,
    ) -> bool:
        """Whether an expression directly or transitively operates on a map.

        C++17 does not order sibling function arguments.  We keep ordinary
        calls byte-identical and only stage calls whose argument tree can
        observe map identity/state (including a user helper whose body performs
        such an operation).
        """
        if not isinstance(node, ASTNode):
            return False
        lexical_specs = lexical_specs or {}
        if isinstance(node, FuncCall):
            callee = node.callee
            if isinstance(callee, MemberAccess):
                if (
                    isinstance(callee.object, Identifier)
                    and callee.object.name == "map"
                    and callee.member in (set(MAP_METHODS) | {"new"})
                ):
                    return True
                receiver_spec = self._map_effect_type_spec(
                    callee.object, lexical_specs
                )
                if (
                    receiver_spec is not None
                    and receiver_spec.kind == "map"
                    and callee.member in MAP_METHODS
                ):
                    return True

            info_key, func_info = self._map_effect_callable_info(
                node, lexical_specs
            )
            if (
                func_info is not None
                and getattr(func_info, "node", None) is not None
                and info_key not in visiting_callables
            ):
                next_visiting = visiting_callables | {info_key}
                child_specs = self._map_effect_callable_specs(
                    func_info,
                    node,
                    lexical_specs,
                )
                if any(
                    self._expr_contains_map_operation(
                        child,
                        next_visiting,
                        child_specs,
                    )
                    for child in func_info.node.body
                ):
                    return True

        for value in vars(node).values():
            if isinstance(value, ASTNode):
                if self._expr_contains_map_operation(
                    value, visiting_callables, lexical_specs
                ):
                    return True
            elif isinstance(value, list):
                if any(
                    self._expr_contains_map_operation(
                        item, visiting_callables, lexical_specs
                    )
                    for item in value
                    if isinstance(item, ASTNode)
                ):
                    return True
            elif isinstance(value, dict):
                if any(
                    self._expr_contains_map_operation(
                        item, visiting_callables, lexical_specs
                    )
                    for item in value.values()
                    if isinstance(item, ASTNode)
                ):
                    return True
        return False

    def _map_effect_type_spec(
        self,
        node,
        lexical_specs: dict[str, TypeSpec | None],
    ) -> TypeSpec | None:
        """Resolve an expression type in the callable being inspected.

        The ordinary codegen resolver describes the function currently being
        emitted, not a transitive helper whose AST is being audited for effects.
        Respect that helper's parameter/local bindings first, then fall back to
        the established expression inference for globals and constructors.
        """
        if isinstance(node, Identifier) and node.name in lexical_specs:
            return lexical_specs[node.name]
        if isinstance(node, MemberAccess):
            owner = self._map_effect_type_spec(node.object, lexical_specs)
            if owner is not None and owner.kind == "udt" and owner.name:
                return (self._udt_field_type_specs.get(owner.name) or {}).get(
                    node.member
                )
        if isinstance(node, FuncCall):
            _key, info = self._map_effect_callable_info(node, lexical_specs)
            return_spec = getattr(info, "return_type_spec", None)
            if return_spec is not None:
                return return_spec
        return self._type_spec_from_expr(node)

    def _map_effect_callable_info(
        self,
        node: FuncCall,
        lexical_specs: dict[str, TypeSpec | None],
    ):
        """Resolve a plain UDF or UDT method for transitive effect analysis."""
        callee = node.callee
        if isinstance(callee, Identifier):
            key = callee.name
            return key, self._func_info_map.get(key)
        if isinstance(callee, MemberAccess):
            receiver_spec = self._map_effect_type_spec(
                callee.object, lexical_specs
            )
            if (receiver_spec is not None
                    and receiver_spec.kind == "udt"
                    and receiver_spec.name):
                key = f"{receiver_spec.name}.{callee.member}"
                return key, self._func_info_map.get(key)
        return "", None

    def _map_effect_callable_specs(
        self,
        func_info,
        call: FuncCall,
        caller_specs: dict[str, TypeSpec | None],
    ) -> dict[str, TypeSpec | None]:
        """Build the inspected callee's lexical TypeSpec environment.

        Declared and already-inferred parameter specs are authoritative.  Any
        unresolved slot is filled from this exact call's argument in the
        caller's lexical environment, which also lets an untyped map flow
        through multiple helper layers before analyzer call-site inference has
        materialized every nested parameter.
        """
        func_node = func_info.node
        params = list(func_node.params) if func_node is not None else []
        declared_specs = list(getattr(func_info, "param_type_specs", ()) or ())
        result: dict[str, TypeSpec | None] = {
            param: declared_specs[index] if index < len(declared_specs) else None
            for index, param in enumerate(params)
        }

        is_method = bool(getattr(func_info, "is_udt_method", False))
        positional = (
            [call.callee.object, *call.args]
            if is_method and isinstance(call.callee, MemberAccess)
            else list(call.args)
        )
        for index, actual in enumerate(positional):
            if index >= len(params) or result[params[index]] is not None:
                continue
            result[params[index]] = self._map_effect_type_spec(
                actual, caller_specs
            )

        keyword_offset = 1 if is_method else 0
        for name, actual in call.kwargs.items():
            if name not in params[keyword_offset:]:
                continue
            if result[name] is None:
                result[name] = self._map_effect_type_spec(
                    actual, caller_specs
                )

        # Direct callable locals already carry analyzer-owned lexical
        # provenance.  They override same-named parameters/globals exactly as
        # they do during real function emission.
        result.update(self._func_collection_types.get(func_info.name, {}))
        return result

    def _ordered_user_call_expr(
        self,
        call_head: str,
        arg_nodes: list,
        arg_exprs: list[str],
        *,
        source_order_nodes: list | None = None,
        force_stage: bool = False,
    ) -> str:
        """Emit a user call with deterministic argument evaluation when needed.

        ``arg_nodes``/``arg_exprs`` are in destination parameter order.
        ``source_order_nodes`` records the caller's written order, allowing
        named arguments to be evaluated as written while still occupying their
        declared parameter slots.
        """
        raw = f"{call_head}({', '.join(arg_exprs)})"
        has_map_effect = any(
            self._expr_contains_map_operation(node) for node in arg_nodes
        )
        if not force_stage and (len(arg_exprs) < 2 or not has_map_effect):
            return raw

        occupied = "\n".join((call_head, *arg_exprs))
        counter = getattr(self, "_ordered_call_arg_counter", 0)
        tokens: list[str] = []
        for _ in arg_exprs:
            while True:
                token = f"__pf_call_arg_{counter}"
                counter += 1
                if token not in occupied:
                    break
            tokens.append(token)
        self._ordered_call_arg_counter = counter

        remaining = list(range(len(arg_nodes)))
        evaluation_order: list[int] = []
        for source_node in source_order_nodes or arg_nodes:
            for index in remaining:
                if arg_nodes[index] is source_node:
                    evaluation_order.append(index)
                    remaining.remove(index)
                    break
        evaluation_order.extend(remaining)

        lowered = f"{call_head}({', '.join(tokens)})"
        for index in reversed(evaluation_order):
            lowered = (
                f"[&](auto&& {tokens[index]})->decltype(auto){{ "
                f"return {lowered}; }}(({arg_exprs[index]}))"
            )
        return lowered

    def _visit_func_call(self, node: FuncCall) -> str:
        callee = node.callee
        if isinstance(callee, MemberAccess):
            recv_spec = self._type_spec_from_expr(callee.object)
            if (
                recv_spec is not None
                and recv_spec.kind == "map"
                and not isinstance(callee.object, (Identifier, MemberAccess))
                and callee.member in MAP_METHODS
            ):
                # Returned, constructed and selected map IDs are ordinary
                # receivers too. Parenthesize the expression so ternaries and
                # other compound producers remain one C++ receiver expression.
                arg_nodes = self._map_call_arg_nodes(
                    callee.member,
                    node,
                    functional=False,
                    allow_keywords=False,
                )
                receiver = f"({self._visit_expr(callee.object)})"
                return self._map_param_method_expr(
                    receiver,
                    callee.member,
                    arg_nodes,
                    recv_spec,
                )
            if recv_spec is not None and recv_spec.kind == "udt" and recv_spec.name:
                mk = f"{recv_spec.name}.{callee.member}"
                fi_u = self._func_info_map.get(mk)
                if fi_u is not None and getattr(fi_u, "is_udt_method", False):
                    fn_cpp = self._udt_method_call_emit_name(fi_u, node)
                    recv_e = self._visit_expr(callee.object)
                    receiver_root = callee.object
                    while isinstance(receiver_root, MemberAccess):
                        receiver_root = receiver_root.object
                    stage_receiver = not isinstance(receiver_root, Identifier)
                    param_names = list(fi_u.node.params[1:]) if fi_u.node else []
                    # Drop the leading ``self`` slot from param_defaults so the
                    # parallel array lines up with ``param_names`` (rest of
                    # the signature). Probe: udt-method-probe-04-default-param.
                    param_defaults = list(getattr(fi_u, "param_defaults", []) or [])[1:]
                    rest_nodes = _merge_kwargs_with_defaults(
                        node.args, node.kwargs, param_names,
                        param_defaults, lambda x: x,
                    )
                    rest = [self._visit_expr(a) for a in rest_nodes]
                    return self._ordered_user_call_expr(
                        fn_cpp,
                        [callee.object, *rest_nodes],
                        [recv_e, *rest],
                        source_order_nodes=[
                            callee.object,
                            *node.args,
                            *node.kwargs.values(),
                        ],
                        # Generated UDT methods accept ``T&``. A constructor or
                        # function-return receiver is a C++ rvalue; bind it to a
                        # named forwarding-reference lambda parameter first so
                        # the method sees a valid lvalue for the full call.
                        force_stage=stage_receiver,
                    )

        # Drawing method dispatch (spec §4.3 / L.1). A KNOWN drawing method on a
        # receiver that resolves to a drawing udt — gated on the METHOD NAME
        # FIRST so a user method (egoigor's ``ln.slope()``, already routed by the
        # block above) is never captured here. This single check covers all
        # receiver shapes (identifier ``ln.set_x2(v)``, obj.field
        # ``d.fld.set_y2(v)``, and arbitrary-expr ``d.upln.get(0).delete()``), so
        # it precedes the obj.field.method / identifier branches below AND the
        # generic ``delete`` -> ``_delete_`` rewrites + _resolve_callee.
        if isinstance(callee, MemberAccess) and callee.member in ALL_DRAWING_METHODS:
            recv_spec = self._type_spec_from_expr(callee.object)
            if (recv_spec is not None and recv_spec.kind == "udt"
                    and recv_spec.name in DRAWING_TYPE_TO_CPP):
                return self._emit_drawing_method(
                    recv_spec.name, callee.member, callee.object,
                    list(node.args), node,
                )

        # Array method on an arbitrary expression receiver, e.g.
        # ``make_array().get(-1)``, ``m.row(0).last()``, or
        # ``(cond ? a : b).get(-1)``. Identifier and member receivers have
        # dedicated paths below; other receiver shapes used to fall through to
        # ``None(...)`` despite carrying a known array spec.
        if (isinstance(callee, MemberAccess)
                and not isinstance(callee.object, (Identifier, MemberAccess))
                and callee.member in ARRAY_METHODS):
            recv_spec = self._type_spec_from_expr(callee.object)
            if recv_spec is not None and recv_spec.kind == "array":
                recv = self._visit_expr(callee.object)
                arg_nodes = self._array_method_arg_nodes(callee.member, node)
                args = self._array_method_args(callee.member, arg_nodes, recv_spec)
                return self._array_method_expr(recv, callee.member, args, recv_spec)

        # chart.point.now/new/from_index/from_time/copy — REAL data (a ChartPoint
        # aggregate). Routed here BEFORE the obj.field.method receiver logic,
        # which would otherwise mis-treat ``chart.point`` as a receiver object
        # and raise on the ``chart.point`` member read (chart ∈ SKIP_NAMESPACES).
        if self._is_chart_point_callee(callee):
            cp_func, _cp_ns = self._resolve_callee(callee)
            return self._emit_chart_point(cp_func, node)

        # obj.field.method(args) — must not lower to namespace::method (loses receiver chain).
        if isinstance(callee, MemberAccess):
            obj = callee.object
            if isinstance(obj, MemberAccess):
                root = obj.object
                root_is_builtin_namespace = (
                    isinstance(root, Identifier)
                    and root.name in (
                    "strategy", "ta", "math", "input", "str", "timeframe", "syminfo",
                    "barstate", "color", "request", "runtime", "array", "matrix", "map",
                    )
                )
                if (
                    root_is_builtin_namespace
                    and root.name == "map"
                    and self._map_identifier_is_visible_binding(node)
                ):
                    root_is_builtin_namespace = False
                if not root_is_builtin_namespace:
                    recv_spec = self._type_spec_from_expr(obj)
                    recv = self._visit_expr(obj)
                    meth = callee.member
                    if (
                        recv_spec is not None
                        and recv_spec.kind == "map"
                        and meth in MAP_METHODS
                    ):
                        arg_nodes = self._map_call_arg_nodes(
                            meth,
                            node,
                            functional=False,
                            allow_keywords=False,
                        )
                        return self._map_param_method_expr(
                            recv,
                            meth,
                            arg_nodes,
                            recv_spec,
                        )
                    raw_args = [self._visit_expr(a) for a in node.args]
                    if recv_spec is not None and recv_spec.kind == "array" and meth in ARRAY_METHODS:
                        arg_nodes = self._array_method_arg_nodes(meth, node)
                        return self._array_method_expr(
                            recv,
                            meth,
                            self._array_method_args(meth, arg_nodes, recv_spec),
                            recv_spec,
                        )
                    args = ", ".join(raw_args)
                    if meth == "delete":
                        meth = "_delete_"
                    return f"{recv}.{meth}({args})"
            # obj.method() where obj is a user var/param — not namespace::method
            if isinstance(obj, Identifier):
                oname = obj.name
                receiver_is_visible = (
                    oname != "map"
                    or self._map_identifier_is_visible_binding(node)
                )
                if receiver_is_visible and (
                    oname in self._var_names
                    or oname in self._current_func_param_types
                    or oname in self._current_loop_vars
                    or oname in self._global_member_vars
                ):
                    meth_raw = callee.member
                    # Function parameters have lexical precedence over the
                    # global collection registries.  Route a declared/inferred
                    # ``array<T>`` parameter before consulting same-named
                    # global map/array/matrix entries, and retain its element
                    # TypeSpec for checked reads and mutations.
                    param_spec = getattr(
                        self, "_current_func_param_specs", {}
                    ).get(oname)
                    active_local_shadow = (
                        oname in getattr(
                            self, "_current_func_collection_specs", {}
                        )
                        or oname in getattr(
                            self, "_current_func_collection_shadows", set()
                        )
                    )
                    if (
                        param_spec is not None
                        and param_spec.kind == "array"
                        and not active_local_shadow
                        and meth_raw in ARRAY_METHODS
                    ):
                        arr = self._collection_receiver_expr(oname)
                        arg_nodes = self._array_method_arg_nodes(meth_raw, node)
                        margs = self._array_method_args(
                            meth_raw, arg_nodes, param_spec
                        )
                        return self._array_method_expr(
                            arr, meth_raw, margs, param_spec
                        )
                    if (
                        param_spec is not None
                        and param_spec.kind == "map"
                        and not active_local_shadow
                        and meth_raw in MAP_METHODS
                    ):
                        m = self._collection_receiver_expr(oname)
                        arg_nodes = self._map_call_arg_nodes(
                            meth_raw,
                            node,
                            functional=False,
                            allow_keywords=True,
                        )
                        return self._map_param_method_expr(
                            m, meth_raw, arg_nodes, param_spec
                        )
                    recv_spec = self._collection_spec_for_name(oname)
                    # Resolve the lexical TypeSpec once and route by its exact
                    # kind.  Raw membership sets can contain a same-named
                    # binding from another scope; overlapping methods such as
                    # ``get`` must never use those as a tie-breaker.
                    if (
                        recv_spec is not None
                        and recv_spec.kind == "map"
                        and meth_raw in MAP_METHODS
                    ):
                        m = self._collection_receiver_expr(oname)
                        arg_nodes = self._map_call_arg_nodes(
                            meth_raw,
                            node,
                            functional=False,
                            allow_keywords=False,
                        )
                        return self._map_param_method_expr(
                            m, meth_raw, arg_nodes, recv_spec
                        )
                    if (
                        recv_spec is not None
                        and recv_spec.kind == "array"
                        and meth_raw in ARRAY_METHODS
                    ):
                        arr = self._collection_receiver_expr(oname)
                        arg_nodes = self._array_method_arg_nodes(meth_raw, node)
                        margs = self._array_method_args(
                            meth_raw, arg_nodes, recv_spec
                        )
                        return self._array_method_expr(arr, meth_raw, margs, recv_spec)
                    if (
                        recv_spec is not None
                        and recv_spec.kind == "matrix"
                        and meth_raw in MATRIX_METHODS
                    ):
                        arr = self._collection_receiver_expr(oname)
                        self._check_matrix_method_allowed(meth_raw, recv_spec, node)
                        param_names = MATRIX_METHOD_KWARGS.get(meth_raw)
                        if param_names and node.kwargs:
                            margs = _merge_kwargs(
                                node.args, node.kwargs, param_names, self._visit_expr
                            )
                        else:
                            margs = [self._visit_expr(a) for a in node.args]
                        fn = MATRIX_METHODS[meth_raw]
                        try:
                            return fn(arr, margs)
                        except IndexError:
                            self._codegen_error(
                                node,
                                f"matrix.{meth_raw}: wrong number of arguments",
                                hint="Check Pine v6 matrix method signature (positional vs keyword).",
                            )
                    safe_o = self._safe_name(oname)
                    udt_t = self._udt_var_types.get(oname) or self._udt_var_types.get(safe_o)
                    if udt_t is None:
                        udt_t = self._udt_param_udt.get(oname) or self._udt_param_udt.get(safe_o)
                    if udt_t is not None:
                        mk = f"{udt_t}.{meth_raw}"
                        fi_u = self._func_info_map.get(mk)
                        if fi_u is not None and getattr(fi_u, "is_udt_method", False):
                            fn_cpp = self._udt_method_call_emit_name(fi_u, node)
                            recv_e = self._visit_expr(obj)
                            param_names = list(fi_u.node.params[1:]) if fi_u.node else []
                            # Drop the leading ``self`` slot so param_defaults
                            # lines up with ``param_names``. Probe:
                            # udt-method-probe-04-default-param.
                            param_defaults = list(getattr(fi_u, "param_defaults", []) or [])[1:]
                            rest_nodes = _merge_kwargs_with_defaults(
                                node.args, node.kwargs, param_names,
                                param_defaults, lambda x: x,
                            )
                            rest = [self._visit_expr(a) for a in rest_nodes]
                            return self._ordered_user_call_expr(
                                fn_cpp,
                                [obj, *rest_nodes],
                                [recv_e, *rest],
                                source_order_nodes=[
                                    obj,
                                    *node.args,
                                    *node.kwargs.values(),
                                ],
                            )
                    args = ", ".join(self._visit_expr(a) for a in node.args)
                    recv = self._visit_expr(obj)
                    meth = meth_raw
                    if meth == "delete":
                        meth = "_delete_"
                    return f"{recv}.{meth}({args})"

        func_name, namespace = self._resolve_callee(callee)

        # na(x) -> is_na(x)
        if func_name == "na" and namespace is None:
            args = ", ".join(self._visit_expr(a) for a in node.args)
            return f"is_na({args})"

        # nz(x) / nz(x, y)
        #
        # x's emitted C++ source is substituted into the surrounding
        # expression, so it must be evaluated EXACTLY ONCE: when x is a
        # stateful call (e.g. a ta.* site lowered to `.compute()`/
        # `.recompute()`), naively embedding {x} twice — once for the
        # is_na() check, once for the non-na branch — invokes that call
        # twice per bar, silently corrupting the indicator's internal state
        # (e.g. nz(ta.sma(v, 50), v) becomes an effective 25-bar SMA: every
        # bar is pushed into the ring buffer twice). An immediately-invoked
        # lambda hoists x into a local `auto` so it is computed once and
        # both branches read the same value; `[&]` is safe here since the
        # lambda is called synchronously and discarded, never escaping.
        if func_name == "nz" and namespace is None:
            x = self._visit_expr(node.args[0])
            y = self._visit_expr(node.args[1]) if len(node.args) > 1 else "0.0"
            return f"([&]{{ auto _nz_v = ({x}); return is_na(_nz_v) ? ({y}) : _nz_v; }}())"

        # fixnan(x) -> persistent state
        if func_name == "fixnan" and namespace is None:
            return self._visit_fixnan(node)

        # strategy.* calls
        if namespace == "strategy":
            return self._visit_strategy_call(func_name, node)

        # ta.tr(handle_na) is dispatched through the standard TA-class path
        # below: the analyzer assigns it a ``ta::TR`` call site (with
        # ``handle_na`` threaded into the constructor); the property form
        # ``ta.tr`` (no parens) stays inline in ``visit_expr`` so its
        # legacy ``handle_na = true`` semantics remain bit-identical.

        # ta.* calls -> member.compute(...)
        site = self._get_ta_site(node)
        if site is not None:
            compute_args = self._ta_compute_args_for_site(site)
            ta_mem = self._ta_member_name(site)
            uses_precalc = self._ta_site_uses_precalc(site)
            if getattr(self, "_precalc_loop_active", False) and uses_precalc:
                return f"_precalc_{ta_mem}[i]"
            if self._is_lazy_saturated_roc3_site(site):
                return self._lazy_saturated_roc3_expr(site)
            if uses_precalc:
                return (
                    f"(_use_precalc ? _precalc_{ta_mem}[bar_index_] : "
                    f"(history_advances_new_bar() ? {ta_mem}.compute({compute_args}) "
                    f": {ta_mem}.recompute({compute_args})))"
                )
            return (
                f"(history_advances_new_bar() ? {ta_mem}.compute({compute_args}) "
                f": {ta_mem}.recompute({compute_args}))"
            )

        # math.* calls
        if namespace == "math":
            return self._visit_math_call(func_name, node)

        # input() / input.* calls -> runtime get_input_*()
        if self._is_input_call_by_name(func_name, namespace):
            if namespace == "input" and func_name == "enum":
                self._enforce_enum_declared_before_input_enum(node)
            title = self._get_input_title(node, var_name=self._current_input_var_name)
            return self._render_input_value(node, func_name, namespace, title)

        # strategy() declaration
        if func_name == "strategy" and namespace is None:
            return "/* strategy declaration */"

        # str.* calls
        if namespace == "str":
            return self._visit_str_call(func_name, node)

        # Map method syntax: m.put(key, val) where namespace is the map variable name
        namespace_spec = (
            self._collection_spec_for_name(namespace)
            if namespace is not None
            else None
        )
        if (
            namespace == "map"
            and not self._map_identifier_is_visible_binding(node)
        ):
            # A later global named ``map`` is already present in the flat
            # collection registry, but is not yet a lexical receiver here.
            namespace_spec = None
        if (
            namespace_spec is not None
            and namespace_spec.kind == "map"
            and func_name in MAP_METHODS
        ):
            m = self._collection_receiver_expr(namespace)
            arg_nodes = self._map_call_arg_nodes(
                func_name,
                node,
                functional=False,
                allow_keywords=False,
            )
            return self._map_param_method_expr(
                m, func_name, arg_nodes, namespace_spec
            )

        # map.method(m, args...) — functional form
        if namespace == "map":
            if func_name == "new" or func_name in MAP_METHODS:
                # This dispatch point comes after lexical receiver routing, so
                # a parameter/local/global named ``map`` retains precedence
                # over the built-in namespace.
                self._map_call_arg_nodes(
                    func_name,
                    node,
                    functional=True,
                    allow_keywords=False,
                )
            if func_name == "new":
                spec = self._type_spec_from_expr(node) or TypeSpec.map(TypeSpec.primitive("string"), TypeSpec.primitive("float"))
                return f"{self._type_spec_to_cpp(spec)}::new_()"
            if func_name in MAP_METHODS and node.args:
                m = self._visit_expr(node.args[0])
                spec = self._type_spec_from_expr(node.args[0]) if node.args else None
                return self._map_param_method_expr(
                    m, func_name, list(node.args[1:]), spec
                )
            return "0"

        if (
            namespace_spec is not None
            and namespace_spec.kind == "matrix"
            and func_name in MATRIX_METHODS
        ):
            arr = self._collection_receiver_expr(namespace)
            self._check_matrix_method_allowed(func_name, namespace_spec, node)
            param_names = MATRIX_METHOD_KWARGS.get(func_name)
            if param_names and node.kwargs:
                args = _merge_kwargs(node.args, node.kwargs, param_names, self._visit_expr)
            else:
                args = [self._visit_expr(a) for a in node.args]
            fn = MATRIX_METHODS[func_name]
            try:
                return fn(arr, args)
            except IndexError:
                self._codegen_error(
                    node,
                    f"matrix.{func_name}: wrong number of arguments",
                    hint="Check Pine v6 matrix method signature (positional vs keyword).",
                )

        # Array method syntax: arr.push(val) where namespace is the array variable name
        if (
            namespace_spec is not None
            and namespace_spec.kind == "array"
            and func_name in ARRAY_METHODS
        ):
            arr = self._collection_receiver_expr(namespace)
            spec = namespace_spec
            arg_nodes = self._array_method_arg_nodes(func_name, node)
            args = self._array_method_args(func_name, arg_nodes, spec)
            return self._array_method_expr(arr, func_name, args, spec)

        # Array operations — emit proper C++ vector operations
        if namespace == "array":
            if func_name in ("new", "new_float", "new_int", "new_bool", "new_string") or func_name in ARRAY_DRAWING_NEW_CTORS:
                spec = self._type_spec_from_expr(node) or TypeSpec.array(TypeSpec.primitive("float"))
                cpp_type = self._type_spec_to_cpp(spec)
                elem_spec = spec.element if spec.element is not None else TypeSpec.primitive("float")
                init_default = self._default_for_spec(elem_spec)
                if node.args:
                    size_arg = self._visit_expr(node.args[0])
                    if len(node.args) > 1:
                        init_val = self._array_init_value_expr(elem_spec, node.args[1])
                    else:
                        init_val = init_default
                    return f"{cpp_type}((size_t)({size_arg}), {init_val})"
                return f"{cpp_type}()"
            if func_name == "from":
                spec = self._type_spec_from_expr(node) or TypeSpec.array(TypeSpec.primitive("float"))
                elems = ", ".join(self._visit_expr(a) for a in node.args)
                return f"{self._type_spec_to_cpp(spec)}{{{elems}}}"
            # Method calls: array.method(arr, args...)
            if func_name in ARRAY_METHODS and (node.args or node.kwargs):
                all_nodes = self._array_function_arg_nodes(func_name, node)
                if not all_nodes:
                    return "0"
                arr = self._visit_expr(all_nodes[0])
                spec = self._type_spec_from_expr(all_nodes[0])
                rest = self._array_method_args(func_name, all_nodes[1:], spec)
                return self._array_method_expr(arr, func_name, rest, spec)
            return "0"

        # color.* calls
        if namespace == "color":
            return self._visit_color_call(func_name, node)

        # Bare color(...) cast (cosmetic). The engine has no color-cast helper
        # and colors have no backtest-logic effect, so emit a benign default
        # color (0 = na color, matching the color.new / from_gradient
        # fallbacks). The support checker warns on this construct.
        if namespace is None and func_name == "color" and func_name not in self._func_names:
            return "0"

        # Drawing-objects-as-data namespace-functional form (spec §4.3 form 1):
        # line.new(...) / line.get_y2(ln) / box.set_top(b, v) / linefill.new(...)
        # MUST precede the SKIP_NAMESPACES early-return (these namespaces were
        # removed from SKIP_NAMESPACES). chart.point.* resolves to namespace
        # "chart", so it is matched by callee shape instead.
        if namespace in DRAWING_NS:
            return self._emit_drawing_namespace_call(namespace, func_name, node)
        if self._is_chart_point_callee(callee):
            return self._emit_chart_point(func_name, node)

        # Skip visual/unsupported namespace calls
        if namespace in SKIP_NAMESPACES or namespace in SKIP_VAR_TYPES:
            return "0"
        if func_name in SKIP_FUNC_NAMES and namespace is None:
            return "0"
        # max_bars_back(var, num): a history-depth DIRECTIVE, not a value.
        # Its effect is captured in CodeGen._compute_max_bars_back_cap (which
        # sizes every Series<T> ring buffer), so the call itself emits nothing.
        if func_name == "max_bars_back" and namespace is None:
            return "0"

        # request.* calls
        if namespace == "request":
            if func_name == "security":
                param_names = ["symbol", "timeframe", "expression", "gaps", "lookahead", "ignore_invalid_symbol", "currency"]
                all_args = list(node.args)
                for i, pname in enumerate(param_names):
                    if pname in node.kwargs:
                        while len(all_args) <= i:
                            all_args.append(None)
                        all_args[i] = node.kwargs[pname]
                
                # Find matching security call ID. A request.security whose
                # timeframe is a UDF parameter called from multiple sites with
                # multiple distinct literal timeframes is registered as N
                # CLONES (one SecurityCallInfo per call site, same source
                # expr_node identity, distinct sec_id/callsite_idx — see
                # Analyzer._check_mixed_callsite_security_tf). All clones
                # match the identity check below identically, so when more
                # than one matches, disambiguate by which call-site clone's
                # function body is currently being emitted
                # (self._active_call_site_idx, set by _emit_func_def while
                # walking that exact clone's body).
                candidates = [
                    item for item in self._security_calls
                    if not item.get("is_lower_tf_array")
                    and (exprn := item["expr_node"]) is not None
                    and (len(all_args) > 2 and exprn is all_args[2])
                ]
                chosen = None
                if len(candidates) == 1:
                    chosen = candidates[0]
                elif len(candidates) > 1:
                    chosen = next(
                        (c for c in candidates
                         if c.get("callsite_idx") == self._active_call_site_idx),
                        candidates[0],
                    )
                sec_id = chosen["sec_id"] if chosen else None
                expr_node = chosen["expr_node"] if chosen else None

                if sec_id is not None and expr_node is not None:
                    if isinstance(expr_node, TupleLiteral):
                        parts = []
                        for i, el in enumerate(expr_node.elements):
                            parts.append(f"_req_sec_{sec_id}_{i}")
                        return f"std::make_tuple({', '.join(parts)})"
                    return f"_req_sec_{sec_id}"

                # Fallback
                return "na<double>()"
            if func_name == "security_lower_tf":
                # ``request.security_lower_tf`` is matched against the
                # registered SecurityCallInfo by AST identity of the
                # ``expression`` argument (3rd positional or kwarg). The
                # codegen lowers the call to the per-sec_id accumulator
                # vector — its element type and clear/push semantics are
                # set up by the security mixin.
                ltf_param_names = [
                    "symbol", "timeframe", "expression",
                    "ignore_invalid_symbol", "currency",
                    "ignore_invalid_timeframe", "calc_bars_count",
                ]
                ltf_all_args = list(node.args)
                for i, pname in enumerate(ltf_param_names):
                    if pname in node.kwargs:
                        while len(ltf_all_args) <= i:
                            ltf_all_args.append(None)
                        ltf_all_args[i] = node.kwargs[pname]
                ltf_expr_node = ltf_all_args[2] if len(ltf_all_args) > 2 else None
                for item in self._security_calls:
                    if not item.get("is_lower_tf_array"):
                        continue
                    if item["expr_node"] is ltf_expr_node:
                        return f"_req_sec_lower_tf_{item['sec_id']}"
                return "std::vector<double>{}"
            # All other request.* functions
            return "na<double>()"

        # ticker.* calls
        if namespace == "ticker":
            # ticker.inherit(symbol, ...) / ticker.standard(symbol) — passthrough,
            # and ticker.heikinashi(symbol) — same-symbol HA: emit the symbol
            # argument unchanged. The runtime HA candle transform is applied by
            # the engine via register_security_eval's heikinashi flag, so the
            # ticker value itself just needs to be the (string) chart symbol.
            if func_name in ("inherit", "standard", "heikinashi"):
                if node.args:
                    return self._visit_expr(node.args[0])
                if "symbol" in node.kwargs:
                    return self._visit_expr(node.kwargs["symbol"])
            # All other ticker.* calls are hard-rejected by support_checker;
            # emit empty string as safe fallback if they somehow reach codegen.
            return 'std::string("")'

        # runtime.error() and other runtime.* calls
        if namespace == "runtime":
            if func_name == "error":
                rt_args = [self._visit_expr(a) for a in node.args]
                msg_arg = rt_args[0] if rt_args else '""'
                return f'pine_runtime_error({msg_arg})'
            return '"" /* unsupported runtime */'

        # year(time) / month(time) / dayofmonth(time) / dayofweek(time) /
        # hour(time[, tz]) / minute(time[, tz]) / second(time[, tz]) /
        # weekofyear(time[, tz]).
        #
        # Pine v6 exposes these names as BOTH variables (current bar) AND
        # functions (arbitrary timestamp). Both forms now share the same
        # timezone-aware emission: the variable form is wired by
        # ``BAR_BUILTINS`` in codegen/tables.py to
        # ``tz_time_field_lambda(..., current_bar_.timestamp,
        # syminfo_.timezone)`` and the function form below uses the same
        # builder, so the numbers agree across both forms.
        #
        # Timezone handling (per Pine v6 reference docs):
        # - Bare form ``hour(time)`` defaults its tz argument to
        #   ``syminfo.timezone`` — the SYMBOL/EXCHANGE timezone, NOT the
        #   chart's display timezone. For the corpus' ETH-USDT crypto data
        #   this is ``"UTC"`` (the ``SymInfo`` constructor default), which
        #   keeps the lambda on the cheap ``gmtime_r`` fast path —
        #   value-identical to the engine's ``_bar_hour()`` /
        #   ``_decompose_bar_time()`` (engine.hpp) UTC helpers the variable
        #   form used to bind to.
        #
        #   Pre-fix the harness's ``strategy_set_chart_timezone`` clobbered
        #   ``syminfo_.timezone`` with the chart display TZ, which silently
        #   shifted ``hour(time)``-bucketed accumulators by the
        #   chart-vs-exchange offset (Asia/Taipei vs UTC = +8h). That fix
        #   now lives entirely in ``BacktestEngine::set_chart_timezone``
        #   (engine.hpp), which writes to a dedicated ``chart_timezone_``
        #   slot and leaves ``syminfo_.timezone`` at its constructor
        #   default. This codegen still reads ``syminfo_.timezone``,
        #   matching TV semantics, with no emit-time changes.
        # - Two-arg form ``hour(time, tz)`` always overrides syminfo with
        #   the explicit tz argument. Same setenv+localtime_r block as the
        #   1-arg fallback.
        if (
            namespace is None
            and func_name in TIME_FIELD_EXPRS
            and (node.args or node.kwargs)
        ):
            params = sigs.get_param_names(None, func_name)
            args = _merge_kwargs(node.args, node.kwargs, params, self._visit_expr)
            ts_arg = args[0] if args else "current_bar_.timestamp"
            tz_arg = args[1] if len(args) > 1 else None
            field_expr = TIME_FIELD_EXPRS[func_name]
            if tz_arg is None:
                # 1-arg form — fall back to ``syminfo.timezone`` per TV
                # docs (the EXCHANGE TZ, default "UTC" for the corpus'
                # crypto data; NOT the chart display TZ — that lives in
                # ``chart_timezone_`` on the engine and is intentionally
                # ignored here). UTC / "" / "Etc/UTC" stay on the cheap
                # gmtime_r path; anything else takes the same
                # mutex-guarded setenv+localtime_r block as the 2-arg
                # form.
                tz_arg = "syminfo_.timezone"
            # Route through the engine's cached pine_<field>() (session_time.hpp),
            # same as the bare variable forms (BAR_BUILTINS) — value-identical but
            # free of the per-call setenv+tzset churn (KI-35). field_expr is unused
            # now (the engine applies the Pine offsets internally).
            del field_expr
            return f"pine_{func_name}((int64_t)({ts_arg}), {tz_arg})"

        # time(timeframe) or time(timeframe, session[, tz])
        if func_name == "time" and namespace is None and (node.args or node.kwargs):
            args = _merge_kwargs(node.args, node.kwargs, sigs.get_param_names(None, "time"), self._visit_expr)
            tf_e = args[0] if len(args) > 0 else 'script_tf_'
            sess = args[1] if len(args) > 1 else 'std::string("")'
            tz_e = args[2] if len(args) > 2 else 'std::string("")'
            return (
                f"pine_time(current_bar_.timestamp, {tf_e}, {sess}, {tz_e}, script_tf_)"
            )
        # time_close(timeframe) or time_close(tf, session, tz)
        if func_name == "time_close" and namespace is None and (node.args or node.kwargs):
            args = _merge_kwargs(node.args, node.kwargs, sigs.get_param_names(None, "time_close"), self._visit_expr)
            tf_e = args[0] if len(args) > 0 else 'script_tf_'
            sess = args[1] if len(args) > 1 else 'std::string("")'
            tz_e = args[2] if len(args) > 2 else 'std::string("")'
            return (
                f"pine_time_close(current_bar_.timestamp, {tf_e}, {sess}, {tz_e}, script_tf_)"
            )

        # timestamp(year, month, day, hour, minute) → Unix ms
        if func_name == "timestamp" and namespace is None:
            is_tz_first = False
            if node.args:
                first_arg_spec = self._type_spec_from_expr(node.args[0])
                if first_arg_spec is not None and first_arg_spec.kind == "primitive" and first_arg_spec.name == "string":
                    is_tz_first = True
                elif isinstance(node.args[0], StringLiteral):
                    is_tz_first = True
                elif self._infer_type(node.args[0]) == "std::string":
                    is_tz_first = True

            if is_tz_first:
                # A single string argument is the timestamp(dateString)
                # overload, NOT the timezone-first form. It used to fall
                # through with year=1970 defaults — silently wrong. Pine
                # dateString is a const string, so parse it at transpile
                # time (common as the input.time defval); reject loudly when
                # it is not a literal or does not parse.
                if len(node.args) == 1:
                    if isinstance(node.args[0], StringLiteral):
                        ms = _parse_pine_datestring_ms(node.args[0].value)
                        if ms is None:
                            self._codegen_error(
                                node,
                                f"timestamp(dateString): could not parse "
                                f"'{node.args[0].value}'.",
                                hint="Supported forms: ISO-8601 "
                                     "(\"2025-01-01[THH:MM:SS][±HH:MM]\") and "
                                     "\"DD MMM YYYY [hh:mm[:ss]] [±HHMM]\" / "
                                     "\"MMM DD YYYY ...\"; no time zone = "
                                     "GMT+0.",
                            )
                        return f"{ms}LL"
                    self._codegen_error(
                        node,
                        "timestamp(dateString) requires a literal string in "
                        "PineForge (Pine v6 dateString is a const string).",
                        hint="Use a string literal, or timestamp(year, month, "
                             "day[, hour, minute, second]).",
                    )
                # timezone-first form requires year, month, and day.
                if len(node.args) < 4:
                    self._codegen_error(
                        node,
                        "timestamp(timezone, ...) requires year, month, and "
                        "day arguments.",
                        hint="Pine v6 signature: timestamp(timezone, year, "
                             "month, day[, hour, minute, second]).",
                    )
                args = [self._visit_expr(a) for a in node.args]
                tz = args[0]
                yr = args[1] if len(args) > 1 else "1970"
                mo = args[2] if len(args) > 2 else "1"
                dy = args[3] if len(args) > 3 else "1"
                hr = args[4] if len(args) > 4 else "0"
                mn = args[5] if len(args) > 5 else "0"
                sc = args[6] if len(args) > 6 else "0"
                return (
                    f"[&]() -> int64_t {{ "
                    f"std::string _tz = pineforge::normalize_timezone_for_posix(({tz})); "
                    f"int _yr = ({yr}); int _mo = ({mo}); int _dy = ({dy}); "
                    f"int _hr = ({hr}); int _min = ({mn}); int _sc = ({sc}); "
                    f"static thread_local std::string _last_tz; "
                    f"static thread_local int _last_yr = -1, _last_mo = -1, _last_dy = -1, _last_hr = -1, _last_min = -1, _last_sc = -1; "
                    f"static thread_local int64_t _last_res = -1; "
                    f"if (_last_res != -1 && _last_tz == _tz && _last_yr == _yr && _last_mo == _mo && _last_dy == _dy && _last_hr == _hr && _last_min == _min && _last_sc == _sc) {{ "
                    f"return _last_res; "
                    f"}} "
                    f"struct tm t = {{}}; "
                    f"t.tm_year = _yr - 1900; t.tm_mon = _mo - 1; "
                    f"t.tm_mday = _dy; t.tm_hour = _hr; t.tm_min = _min; t.tm_sec = _sc; "
                    f"t.tm_isdst = -1; "
                    f"int64_t _res; "
                    f"if (_tz.empty() || _tz == \"UTC\" || _tz == \"Etc/UTC\") {{ "
                    f"_res = (int64_t)timegm(&t) * 1000; "
                    f"}} else {{ "
                    f"static std::mutex _pf_ts_mu; "
                    f"std::lock_guard<std::mutex> _pf_ts_mu_lock(_pf_ts_mu); "
                    f"const char* _old = std::getenv(\"TZ\"); "
                    f"std::string _old_tz = _old ? _old : \"\"; bool _had_old = (_old != nullptr); "
                    f"::setenv(\"TZ\", _tz.c_str(), 1); ::tzset(); "
                    f"_res = (int64_t)mktime(&t) * 1000; "
                    f"if (_had_old) {{ ::setenv(\"TZ\", _old_tz.c_str(), 1); }} "
                    f"else {{ ::unsetenv(\"TZ\"); }} ::tzset(); "
                    f"}} "
                    f"_last_tz = _tz; _last_yr = _yr; _last_mo = _mo; _last_dy = _dy; _last_hr = _hr; _last_min = _min; _last_sc = _sc; "
                    f"_last_res = _res; "
                    f"return _res; "
                    f"}}()"
                )
            else:
                # Numeric form requires year, month, and day (hour/minute/
                # second default to 0). Anything shorter used to emit "0".
                merged = _merge_kwargs(
                    node.args, node.kwargs,
                    sigs.get_param_names(None, "timestamp"),
                    lambda a: a,
                )
                if len(merged) < 3 or any(a is None for a in merged[:3]):
                    self._codegen_error(
                        node,
                        f"timestamp(...) with {len(merged)} argument(s) is "
                        f"not supported — year, month, and day are required.",
                        hint="Pine v6 signature: timestamp(year, month, day"
                             "[, hour, minute, second]); the dateString "
                             "overload is not supported in PineForge.",
                    )
                args = [self._visit_expr(a) for a in merged]
                yr = args[0]
                mo = args[1] if len(args) > 1 else "1"
                dy = args[2] if len(args) > 2 else "1"
                hr = args[3] if len(args) > 3 else "0"
                mn = args[4] if len(args) > 4 else "0"
                sc = args[5] if len(args) > 5 else "0"
                return (
                    f"[&]() -> int64_t {{ "
                    f"int _yr = ({yr}); int _mo = ({mo}); int _dy = ({dy}); "
                    f"int _hr = ({hr}); int _min = ({mn}); int _sc = ({sc}); "
                    f"static thread_local int _last_yr = -1, _last_mo = -1, _last_dy = -1, _last_hr = -1, _last_min = -1, _last_sc = -1; "
                    f"static thread_local int64_t _last_res = -1; "
                    f"if (_last_res != -1 && _last_yr == _yr && _last_mo == _mo && _last_dy == _dy && _last_hr == _hr && _last_min == _min && _last_sc == _sc) {{ "
                    f"return _last_res; "
                    f"}} "
                    f"struct tm t = {{}}; "
                    f"t.tm_year = _yr - 1900; t.tm_mon = _mo - 1; "
                    f"t.tm_mday = _dy; t.tm_hour = _hr; t.tm_min = _min; t.tm_sec = _sc; "
                    f"int64_t _res = (int64_t)timegm(&t) * 1000; "
                    f"_last_yr = _yr; _last_mo = _mo; _last_dy = _dy; _last_hr = _hr; _last_min = _min; _last_sc = _sc; "
                    f"_last_res = _res; "
                    f"return _res; "
                    f"}}()"
                )

        # barssince() — unsupported. Defensive: support_checker rejects bare
        # barssince(...) with a hint to use ta.barssince(...). Reaching here
        # means the checker was bypassed.
        if func_name == "barssince" and namespace is None:
            raise ValueError(
                "codegen: bare barssince(...) is not supported — analyzer should "
                "have rejected. Use ta.barssince(...)."
            )

        # Type cast functions: int(x), float(x), bool(x), string(x)
        if func_name == "int" and namespace is None and node.args:
            # Pine int(na) → na (int form). Evaluate once, propagate na via
            # the engine's int sentinel instead of collapsing NaN to 0.
            x = self._visit_expr(node.args[0])
            return (f"[&](){{ double _pf_v = (double)({x}); "
                    f"return is_na(_pf_v) ? na<int>() : (int)_pf_v; }}()")
        if func_name == "float" and namespace is None and node.args:
            return f"(double)({self._visit_expr(node.args[0])})"
        if func_name == "bool" and namespace is None and node.args:
            # Pine v6 bools are two-state. Explicit bool(int/float) treats na
            # like false, while a raw C++ cast would make NaN truthy.
            x = self._visit_expr(node.args[0])
            return (
                f"[&](){{ auto _pf_v = ({x}); "
                f"using _pf_t = std::decay_t<decltype(_pf_v)>; "
                f"if constexpr (std::is_same_v<_pf_t, bool>) {{ return _pf_v; }} "
                f"else {{ return is_na(_pf_v) ? false : (bool)_pf_v; }} }}()"
            )
        if func_name == "string" and namespace is None and node.args:
            # Pine string(x) cast — same emission as str.tostring(x), with
            # string passthrough and TV-style "true"/"false" for bools
            # (std::to_string would reject strings / render bools as 0/1).
            arg = node.args[0]
            inferred = self._infer_type(arg)
            if inferred == "std::string":
                return self._visit_expr(arg)
            if inferred == "bool":
                visited = self._visit_expr(arg)
                return f'(({visited}) ? std::string("true") : std::string("false"))'
            return self._visit_str_call("tostring", node)

        # ta.pivot_point_levels — free function, not a stateful indicator
        if namespace == "ta" and func_name == "pivot_point_levels":
            if node.kwargs:
                args = _merge_kwargs(
                    node.args,
                    node.kwargs,
                    sigs.get_param_names("ta", "pivot_point_levels"),
                    self._visit_expr,
                )
            else:
                args = [self._visit_expr(a) for a in node.args]
            if len(args) >= 4:
                return f'ta::pivot_point_levels({", ".join(args[:4])})'
            if 1 <= len(args) <= 3:
                # Pine overload (type, anchor, developing). Per Pine v6
                # semantics, `developing=false` (the default) means the pivot
                # is computed from the LAST CLOSED period's HLC. With
                # `anchor=true` constant, the "period" is one bar, so we
                # consume the PREVIOUS bar's HLC via `_s_high[1]`, etc. The
                # analyzer registers high/low/close in `series_bar_fields` so
                # those `Series<double>` members are guaranteed to exist.
                # Previously we passed `current_bar_.high/low/close` which
                # produced TV-shifted-by-one-bar values for every level.
                return (
                    f"ta::pivot_point_levels({args[0]}, _s_high[1], "
                    f"_s_low[1], _s_close[1])"
                )
            return f'ta::pivot_point_levels({", ".join(args)})'

        # Unknown ta.* calls — safe fallback
        if namespace == "ta":
            return f"na<double>() /* unsupported: ta.{func_name} */"

        if namespace == "syminfo":
            if func_name == "prefix":
                return "_pf_derive_prefix(syminfo_.tickerid)"
            if func_name == "ticker":
                return "syminfo_.ticker"
            return f"na<double>() /* unsupported: syminfo.{func_name} */"

        # str.* fallback now handled by _visit_str_call above

        # matrix.* calls
        if namespace == "matrix":
            if func_name == "new":
                targs = self._template_args_from_call(node)
                elem_spec = self._type_spec_from_hint_name(targs[0]) if targs else TypeSpec.primitive("float")
                args_e = [self._visit_expr(a) for a in node.args]
                rows = args_e[0] if args_e else "0"
                cols = args_e[1] if len(args_e) > 1 else "0"
                if elem_spec.kind == "primitive" and elem_spec.name == "float":
                    init = args_e[2] if len(args_e) > 2 else "0.0"
                    return f"PineMatrix::new_({rows}, {cols}, {init})"
                cpp_t = self._type_spec_to_cpp(elem_spec)
                init = args_e[2] if len(args_e) > 2 else self._default_for_spec(elem_spec)
                return f"PineGenericMatrix<{cpp_t}>::new_({rows}, {cols}, {init})"
            if func_name in MATRIX_METHODS and node.args:
                from ..ast_nodes import Identifier as _Ident
                if func_name in MATRIX_NUMERIC_ONLY:
                    if not isinstance(node.args[0], _Ident):
                        self._codegen_error(node, f"matrix.{func_name} receiver must be a variable reference")
                    recv_name = node.args[0].name
                    recv_spec = self._collection_spec_for_name(recv_name)
                    if recv_spec is None or recv_spec.kind != "matrix":
                        self._codegen_error(node, f"matrix.{func_name}: receiver '{recv_name}' is not a known matrix variable")
                    self._check_matrix_method_allowed(func_name, recv_spec, node)
                if func_name == "sort":
                    if isinstance(node.args[0], _Ident):
                        recv_name = node.args[0].name
                        recv_spec = self._collection_spec_for_name(recv_name)
                        if recv_spec is not None and recv_spec.kind == "matrix":
                            self._check_matrix_method_allowed(func_name, recv_spec, node)
                obj = self._visit_expr(node.args[0])
                param_names = MATRIX_METHOD_KWARGS.get(func_name)
                if param_names:
                    rest = _merge_kwargs(node.args[1:], node.kwargs, param_names, self._visit_expr)
                else:
                    rest = [self._visit_expr(a) for a in node.args[1:]]
                fn = MATRIX_METHODS[func_name]
                try:
                    return fn(obj, rest)
                except IndexError:
                    self._codegen_error(
                        node,
                        f"matrix.{func_name}: wrong number of arguments",
                        hint="Check Pine v6 matrix method signature (positional vs keyword).",
                    )
            return "0.0"

        # log.* calls (log.error, log.warning, log.info)
        if namespace == "log":
            log_funcs = {"info": "pine_log_info", "warning": "pine_log_warning", "error": "pine_log_error"}
            if func_name in log_funcs:
                log_args = [self._visit_expr(a) for a in node.args]
                msg_arg = log_args[0] if log_args else '""'
                return f'{log_funcs[func_name]}({msg_arg})'
            return '"" /* unsupported log */'

        # timeframe.* calls (e.g., timeframe.change) — not supported in single-TF backtest
        if namespace == "timeframe":
            if func_name == "change":
                tf_arg = self._visit_expr(node.args[0]) if node.args else 'script_tf_'
                return f'tf_change(prev_bar_timestamp_, current_bar_.timestamp, {tf_arg})'
            if func_name == "in_seconds":
                tf_arg = self._visit_expr(node.args[0]) if node.args else 'script_tf_'
                return f'tf_to_seconds({tf_arg})'
            # Defensive: support_checker.NOT_YET_FUNC should already have rejected
            # any unhandled timeframe.* call. Reaching here implies the checker was
            # bypassed.
            raise ValueError(
                f"codegen: unhandled timeframe.{func_name} — analyzer should have "
                f"rejected this. Either add a handler above or extend NOT_YET_FUNC."
            )

        # UDT constructor: TypeName.new(field=val, ...)
        if namespace in self._udt_defs and func_name == "new":
            fields = self._udt_defs[namespace]
            field_names = [f.name for f in fields]
            init_nodes = {}
            for i, a in enumerate(node.args):
                if i < len(field_names):
                    init_nodes[field_names[i]] = a
            for k, v in node.kwargs.items():
                init_nodes[k] = v
            field_inits = []
            field_specs = self._udt_field_type_specs.get(namespace, {})
            for f in fields:
                value_node = None
                if f.name in init_nodes:
                    value_node = init_nodes[f.name]
                elif f.default:
                    value_node = f.default
                if value_node is not None:
                    # Fix narrowing: brace-init (``T{.field = v}``) disallows
                    # narrowing. Pine ``int`` UDT fields are emitted as
                    # ``int64_t`` (see base.py) but are initialised from
                    # ``na<double>()`` / doubles in places, so cast to the
                    # field's type. ``na<double>()`` for an int field → 0.
                    f_cpp_type = self._type_spec_to_cpp(field_specs.get(f.name) or self._type_spec_from_hint_name(f.type_name))
                    if f_cpp_type == "int":
                        f_cpp_type = "int64_t"
                    val = self._visit_rhs_value(
                        value_node,
                        target_cpp_type=(
                            f_cpp_type
                            if f_cpp_type.startswith("PineMap<")
                            else None
                        ),
                    )
                    if f_cpp_type == "int64_t":
                        if "na<double>" in val:
                            val = val.replace("na<double>()", "na<int64_t>()")
                        else:
                            val = f"(int64_t)({val})"
                    elif (f_cpp_type.startswith("PineMap<")
                          and val == "na<double>()"):
                        val = f"{f_cpp_type}{{}}"
                    field_inits.append(f".{f.name} = {val}")
            # Mark the constructed object non-na (the struct's ``__pf_na`` is the
            # last declared field, so this designator stays in declaration order).
            # A bare default-constructed UDT keeps ``__pf_na = true`` (na); only a
            # real ``.new(...)`` flips it false so ``na(obj)`` reports correctly.
            field_inits.append(".__pf_na = false")
            return f"{namespace}{{{', '.join(field_inits)}}}"

        # UDT copy: TypeName.copy(obj)
        if namespace in self._udt_defs and func_name == "copy":
            if node.args:
                return self._visit_expr(node.args[0])
            return f"{namespace}{{}}"

        # Safety net before the generic emitter. Every builtin namespace and
        # bare builtin that codegen knows how to emit has been dispatched (and
        # returned) above; SKIP_NAMESPACES / SKIP_FUNC_NAMES returned "0";
        # user-defined functions live in ``self._func_names`` and UDT
        # constructors/copies were handled via ``self._udt_defs``. Anything
        # still here would be written out verbatim — ``made_up(...)`` or
        # ``qux::frobnicate(...)`` — i.e. an *undeclared C++ symbol*. That is a
        # silent miscompile: the support checker did not reject it, so the user
        # would otherwise only see a cryptic g++ error pointing at generated
        # C++ instead of their Pine line. Reject loudly with the offending
        # node's location. (Note: any script that reached this branch already
        # failed to compile, so the all-green corpus never exercises it — this
        # only converts garbage output into a clean diagnostic.)
        # ``func_name is None`` means the callee is a complex expression the
        # resolver does not reduce to a simple ``name`` / ``ns.name`` — e.g. a
        # chained method call ``m.transpose().copy()`` whose receiver is itself
        # a FuncCall. Those are handled by the existing generic/chained logic
        # below; do not treat them as unknown builtins.
        if namespace is None and func_name is not None:
            if func_name not in self._func_names:
                self._codegen_error(
                    node,
                    f"Unknown function '{func_name}(...)' — not a PineForge "
                    f"builtin or a user-defined function.",
                    hint="Check the spelling; the function may not be supported "
                         "by PineForge, or needs its namespace (e.g. math./str.).",
                )
        elif namespace is not None and namespace not in self._udt_defs:
            self._codegen_error(
                node,
                f"Unknown call '{namespace}.{func_name}(...)' — '{namespace}' is "
                f"not a PineForge-supported namespace or a user-defined type.",
                hint="Check the spelling; this namespace may not be supported "
                     "by PineForge.",
            )

        # Generic function call (user-defined or unknown)
        # Determine which params are series (need Series<double> arg, not scalar)
        _func_series_param_indices: set[int] = set()
        fi_lookup = self._func_info_map.get(func_name)
        if fi_lookup and fi_lookup.node:
            func_sv = self.ctx.func_series_vars.get(fi_lookup.name, set())
            for p_idx, p_name in enumerate(fi_lookup.node.params):
                if p_name in func_sv:
                    _func_series_param_indices.add(p_idx)

        def _visit_arg_for_series(arg_node, arg_idx):
            """Visit a function argument, returning Series ref for series params."""
            if arg_idx in _func_series_param_indices:
                if isinstance(arg_node, Identifier):
                    aname = arg_node.name
                    # Bar field: pass _s_close instead of current_bar_.close
                    if aname in BAR_FIELDS or aname in BAR_SERIES_PUSH:
                        return f"_s_{aname}"
                    # Series var: pass the Series object directly
                    if aname in self.ctx.series_vars:
                        safe = self._safe_name(aname)
                        if self._active_var_remap and safe in self._active_var_remap:
                            safe = self._active_var_remap[safe]
                        return safe
                expr_cpp = self._visit_expr(arg_node)
                cpp_t = self._infer_type(arg_node)
                if cpp_t not in ("double", "int", "bool"):
                    cpp_t = "double"
                member = self._inline_history_member(
                    "series_arg", node, arg_idx=arg_idx
                )
                return (
                    f"([&]() -> const Series<{cpp_t}>& {{ "
                    f"{cpp_t} _sv = ({expr_cpp}); "
                    f"if (history_advances_new_bar()) {member}.push(_sv); "
                    f"else {member}.update(_sv); "
                    f"return {member}; }}())"
                )
            # A concrete map specialization learned through an untyped
            # wrapper chain also target-types its call arguments.  In
            # particular, ``choose(cond, value, na)`` must pass a typed null
            # PineMap handle rather than the generic ``na<double>()``.  Every
            # non-map destination remains on the byte-identical expression
            # path below.
            if fi_lookup is not None:
                param_specs = getattr(fi_lookup, "param_type_specs", []) or []
                if arg_idx < len(param_specs):
                    param_spec = param_specs[arg_idx]
                    if param_spec is not None and param_spec.kind == "map":
                        return self._visit_rhs_value(
                            arg_node,
                            target_cpp_type=self._type_spec_to_cpp(param_spec),
                        )
            return self._visit_expr(arg_node)

        ordered_arg_nodes: list = []
        if node.kwargs:
            # Try to resolve kwargs using FuncInfo params for user-defined functions
            fi = self._func_info_map.get(func_name)
            if fi and fi.node and fi.node.params:
                param_names = list(fi.node.params)  # params is list[str]
                # Merge kwargs then visit with series awareness
                merged = _merge_kwargs(node.args, node.kwargs, param_names, lambda a: a)
                ordered_arg_nodes = list(merged)
                all_args = [_visit_arg_for_series(a, i) for i, a in enumerate(merged)]
            elif sigs.is_intrinsic_function(namespace, func_name):
                # Known intrinsic — use signature registry for kwargs resolution
                param_names = sigs.get_param_names(namespace, func_name)
                merged = _merge_kwargs(
                    node.args, node.kwargs, param_names, lambda a: a
                )
                ordered_arg_nodes = list(merged)
                all_args = [self._visit_expr(a) for a in merged]
            else:
                # Unknown function: positional args + kwargs values as fallback
                ordered_arg_nodes = [*node.args, *node.kwargs.values()]
                all_args = [
                    _visit_arg_for_series(a, i)
                    for i, a in enumerate(node.args)
                ]
                all_args.extend(
                    self._visit_expr(v) for v in node.kwargs.values()
                )
        else:
            ordered_arg_nodes = list(node.args)
            all_args = [_visit_arg_for_series(a, i) for i, a in enumerate(node.args)]
        # Drawing-style/visual CONSTANT passed positionally into a user function's
        # ``string`` parameter: ``label.style_*`` / ``size.*`` / other
        # DRAWING_STYLE_NS members lower to the bare token ``"0"`` (they only ever
        # feed dropped visual kwargs). Bound to a ``std::string`` parameter, that
        # ``0`` constructs ``std::string((char const*)0)`` at the call site -> a
        # null-pointer ``strlen`` crash at runtime. Coerce such args to
        # ``std::string("")`` so the (inert, visual-only) value is a valid empty
        # string. Only touches user functions with a known string param and an
        # arg that is exactly such a drawing-style constant read.
        if namespace is None and func_name in self._func_names:
            self._coerce_drawing_style_string_args(func_name, node.args, all_args)
        # Default args (parser does not store defaults): isInSession(sess, res = timeframe.period)
        if namespace is None and func_name in self._func_names:
            fi = self._func_info_map.get(func_name)
            if fi and fi.node and fi.name == "isInSession" and len(fi.node.params) >= 2 and len(all_args) == 1:
                # Mirror Pine default `timeframe.period` instead of hard-coding 15m.
                all_args.append("script_tf_")
                ordered_arg_nodes.append(None)
        prefix = f"{namespace}::" if namespace else ""
        # Use safe name for user-defined functions to avoid member name collision
        emit_name = self._func_safe_name(func_name) if func_name in self._func_names else func_name
        # Per-call-site variant: if this function has TA/series calls, call the correct variant
        cs_info = self.ctx.func_call_cs_map.get(id(node))
        dispatch_key = (self._current_instance_name, id(node))
        if dispatch_key in self._instance_dispatch:
            # Context-sensitive (call-path) dispatch: the instance pre-pass
            # resolved this nested stateful-helper call to the clone bound to
            # THIS enclosing path's members (see _build_func_instances). This
            # is authoritative — it supersedes the textual-cs threading below,
            # which conflates a callee's own call sites with the enclosing
            # function's call sites for helpers reached through >1 path.
            emit_name = self._instance_dispatch[dispatch_key]
        elif self._active_call_site_idx is not None and cs_info is not None:
            # Inside a per-call-site variant: override the cs_map index with
            # the parent's active call-site index. This ensures sub-functions
            # called from ma_cs6() use their _cs6 variant, not _cs0.
            fname, _ = cs_info
            emit_name = f"{self._func_safe_name(fname)}_cs{self._active_call_site_idx}"
        elif cs_info is not None:
            fname, cs_idx = cs_info
            emit_name = f"{self._func_safe_name(fname)}_cs{cs_idx}"
        elif (self._active_call_site_idx is not None
              and func_name in self._func_names
              and self.ctx.func_call_site_counts.get(func_name, 0) > 1):
            # Inside a per-call-site variant: propagate call-site index to
            # sub-functions that also have variants (for state isolation)
            emit_name = f"{self._func_safe_name(func_name)}_cs{self._active_call_site_idx}"
        call_head = f"{prefix}{emit_name}"
        if namespace is None and func_name in self._func_names:
            return self._ordered_user_call_expr(
                call_head,
                ordered_arg_nodes,
                all_args,
                source_order_nodes=[*node.args, *node.kwargs.values()],
            )
        return f"{call_head}({', '.join(all_args)})"

    def _coerce_drawing_style_string_args(self, func_name, arg_nodes, all_args) -> None:
        """In-place coerce positional args bound to a ``std::string`` user-function
        parameter that lowered to the bare token ``"0"`` from a drawing-style /
        visual constant (``label.style_*`` etc.). Such a literal ``0`` binds as
        ``std::string((char const*)0)`` and segfaults on first use. Replace with
        ``std::string("")`` (the value is visual-only and inert in a backtest)."""
        from .tables import DRAWING_STYLE_NS
        fi = self._func_info_map.get(func_name)
        if not fi or not getattr(fi, "node", None) or not fi.node.params:
            return
        specs = getattr(fi, "param_type_specs", []) or []
        for i, arg in enumerate(arg_nodes):
            if i >= len(all_args) or all_args[i] != "0":
                continue
            # Only when the destination parameter is a string.
            spec = specs[i] if i < len(specs) else None
            is_string_param = spec is not None and getattr(spec, "kind", None) == "primitive" \
                and getattr(spec, "name", None) == "string"
            if not is_string_param:
                continue
            # Only when the source really is a drawing-style/visual constant read
            # (so we never silently turn a numeric ``0`` into an empty string).
            if (isinstance(arg, MemberAccess) and isinstance(arg.object, Identifier)
                    and arg.object.name in DRAWING_STYLE_NS):
                all_args[i] = 'std::string("")'

    def _visit_fixnan(self, node: FuncCall) -> str:
        """Emit fixnan with persistent state member."""
        # Variant-aware lookup keyed off the analyzer-tracked site:
        #   * function-owned site -> dispatch through the active per-call-site
        #     remap so each emitted variant (cs0/cs1/__ni{N}) references its
        #     OWN previous-value member.
        #   * top-level site (owner_func is None) -> use ``site.member_name``
        #     directly. The declarations come from ``ctx.fixnan_sites`` keyed
        #     by these member names, so referencing anything else (e.g. the
        #     legacy monotonic counter) would either dangle a declaration or
        #     silently alias another site's state. In particular, when a
        #     function-owned fixnan is analyzed BEFORE a top-level one, the
        #     counter would restart at 1 and collide with the function's
        #     ``_prev_fixnan_1`` -- corrupting both. Using the site's own
        #     member name keeps declaration and reference in lockstep.
        #   * unmapped site (node not in the site map, e.g. a fixnan reached
        #     only through a path the analyzer didn't register) -> fall back
        #     to the legacy monotonic counter so emission still produces a
        #     referenceable member.
        site = self._fixnan_site_map.get(id(node))
        if site is not None:
            if site.member_name in self._func_fixnan_members:
                member = self._active_fixnan_remap.get(
                    site.member_name, site.member_name
                )
            else:
                member = site.member_name
        else:
            self._fixnan_counter += 1
            member = f"_prev_fixnan_{self._fixnan_counter}"
        x = self._visit_expr(node.args[0])
        return f"(is_na({x}) ? {member} : ({member} = {x}))"

    def _visit_strategy_call(self, func_name: str, node: FuncCall) -> str:
        if func_name in ("convert_to_account", "convert_to_symbol"):
            p = self._resolve_func_args(node, f"strategy.{func_name}")
            v = self._visit_expr(p.get("value")) if p.get("value") is not None else "0.0"
            return f"({v})"
        if func_name == "default_entry_qty":
            p = self._resolve_func_args(node, "strategy.default_entry_qty")
            fp = self._visit_expr(p.get("fill_price")) if p.get("fill_price") is not None else "0.0"
            return f"calc_qty({fp})"

        if func_name == "entry":
            p = self._resolve_func_args(node, "strategy.entry")
            entry_id = self._visit_expr(p.get("id")) if "id" in p else '""'
            direction = self._visit_expr(p.get("direction")) if "direction" in p else "true"
            stop = p.get("stop")
            limit = p.get("limit")
            qty = p.get("qty")
            comment = p.get("comment")
            oca_name = p.get("oca_name")
            oca_type = p.get("oca_type")
            qty_type = p.get("qty_type")
            comment_val = self._visit_expr(comment) if comment else '""'
            oca_name_val = self._visit_expr(oca_name) if oca_name else '""'
            oca_type_val = self._visit_expr(oca_type) if oca_type else "0"
            qty_type_val = self._visit_expr(qty_type) if qty_type else "-1"
            qty_val = self._visit_expr(qty) if qty else "na<double>()"
            if stop is not None or limit is not None or qty is not None or oca_name is not None or oca_type is not None or qty_type is not None:
                limit_val = self._visit_expr(limit) if limit else "na<double>()"
                stop_val = self._visit_expr(stop) if stop else "na<double>()"
                # pineforge-engine v0.2 dropped the vestigial `market_price`
                # third positional from `BacktestEngine::strategy_entry`
                # (the runtime never read it; fill price always came from
                # current_bar_.close inside the function body). Codegen now
                # matches the new signature: (id, direction, limit, stop,
                # qty, comment, oca_name, oca_type, qty_type).
                return f"strategy_entry({entry_id}, {direction}, {limit_val}, {stop_val}, {qty_val}, {comment_val}, {oca_name_val}, {oca_type_val}, {qty_type_val})"
            return f"strategy_entry({entry_id}, {direction}, na<double>(), na<double>(), na<double>(), {comment_val})"

        if func_name == "close":
            p = self._resolve_func_args(node, "strategy.close")
            close_id = self._visit_expr(p.get("id")) if "id" in p else '""'
            comment = self._visit_expr(p.get("comment")) if p.get("comment") is not None else '""'
            qty = self._visit_expr(p.get("qty")) if p.get("qty") is not None else "na<double>()"
            qty_pct = self._visit_expr(p.get("qty_percent")) if p.get("qty_percent") is not None else "na<double>()"
            immediately = self._visit_expr(p.get("immediately")) if p.get("immediately") is not None else "false"
            return f"strategy_close({close_id}, {comment}, {qty}, {qty_pct}, {immediately})"

        if func_name == "close_all":
            p = self._resolve_func_args(node, "strategy.close_all")
            comment = self._visit_expr(p.get("comment")) if p.get("comment") is not None else '""'
            immediately = self._visit_expr(p.get("immediately")) if p.get("immediately") is not None else "false"
            # The engine's ID-less strategy_close path closes the complete
            # position. Reuse it so close_all preserves the Pine order comment
            # and same-tick fill flag instead of silently discarding both.
            return f'strategy_close("", {comment}, na<double>(), na<double>(), {immediately})'

        if func_name == "exit":
            p = self._resolve_func_args(node, "strategy.exit")
            exit_id = self._visit_expr(p.get("id")) if "id" in p else '""'
            from_id = self._visit_expr(p.get("from_entry")) if "from_entry" in p else '""'

            limit_n = p.get("limit")
            stop_n = p.get("stop")
            profit_n = p.get("profit")
            loss_n = p.get("loss")
            trail_pts_n = p.get("trail_points")
            trail_off_n = p.get("trail_offset")
            trail_pr_n = p.get("trail_price")
            qty_pct_n = p.get("qty_percent")
            qty_n = p.get("qty")
            comment_n = p.get("comment")
            oca_name_n = p.get("oca_name")

            has_price_exit = any(x is not None for x in
                                [limit_n, stop_n, profit_n, loss_n,
                                 trail_pts_n, trail_off_n, trail_pr_n])
            if has_price_exit:
                limit_val = self._visit_expr(limit_n) if limit_n else "na<double>()"
                stop_val = self._visit_expr(stop_n) if stop_n else "na<double>()"
                trail_pts = self._visit_expr(trail_pts_n) if trail_pts_n else "na<double>()"
                trail_off = self._visit_expr(trail_off_n) if trail_off_n else "na<double>()"
                trail_pr = self._visit_expr(trail_pr_n) if trail_pr_n else "na<double>()"
                qty_pct = self._visit_expr(qty_pct_n) if qty_pct_n else "100.0"
                qty_val = self._visit_expr(qty_n) if qty_n else "na<double>()"
                comment = self._visit_expr(comment_n) if comment_n is not None else '""'
                oca_val = self._visit_expr(oca_name_n) if oca_name_n is not None else '""'
                profit_ticks = "na<double>()"
                loss_ticks = "na<double>()"

                if profit_n and not limit_n:
                    profit_ticks = self._visit_expr(profit_n)
                if loss_n and not stop_n:
                    loss_ticks = self._visit_expr(loss_n)

                return (f"strategy_exit({exit_id}, {from_id}, {limit_val}, {stop_val}, "
                        f"{trail_pts}, {trail_off}, {trail_pr}, {qty_pct}, {comment}, "
                        f"{qty_val}, {oca_val}, {profit_ticks}, {loss_ticks})")
            close_comment = self._visit_expr(comment_n) if comment_n is not None else '""'
            return f"strategy_close({exit_id}, {close_comment})"

        if func_name == "cancel":
            p = self._resolve_func_args(node, "strategy.close")  # same shape: id first
            cancel_id = self._visit_expr(p.get("id")) if "id" in p else '""'
            return f"strategy_cancel({cancel_id})"

        if func_name == "cancel_all":
            return "strategy_cancel_all()"

        if func_name == "order":
            p = self._resolve_func_args(node, "strategy.order")
            order_id = self._visit_expr(p.get("id")) if "id" in p else '""'
            direction = self._visit_expr(p.get("direction")) if "direction" in p else "true"
            qty = self._visit_expr(p.get("qty")) if "qty" in p else "0"
            limit_arg = self._visit_expr(p.get("limit")) if "limit" in p else "na<double>()"
            stop_arg = self._visit_expr(p.get("stop")) if "stop" in p else "na<double>()"
            oca_name = self._visit_expr(p.get("oca_name")) if "oca_name" in p else '""'
            oca_type = self._visit_expr(p.get("oca_type")) if "oca_type" in p else "0"
            return f"strategy_order({order_id}, {direction}, {qty}, {limit_arg}, {stop_arg}, {oca_name}, {oca_type})"

        if func_name == "risk":
            return "/* skip */"

        # strategy.closedtrades.*(idx) / strategy.opentrades.*(idx)
        # These come through as func_name="profit" etc. with nested callee
        if isinstance(node.callee, MemberAccess):
            inner = node.callee.object
            if isinstance(inner, MemberAccess) and inner.member in ("closedtrades", "opentrades"):
                idx = self._visit_expr(node.args[0]) if node.args else "0"
                is_open = inner.member == "opentrades"
                # Open trades have no exit metadata in Pine
                if is_open and func_name in (
                    "exit_price", "exit_time", "exit_comment", "exit_id", "exit_bar_index",
                ):
                    if func_name == "exit_bar_index":
                        return "na<int>()"
                    if func_name == "exit_time":
                        return "0"
                    if func_name == "exit_price":
                        return "na<double>()"
                    if func_name in ("exit_comment", "exit_id"):
                        return "std::string()"

                prefix = "open_trade_" if is_open else "closed_trade_"
                suffix_map = {
                    "profit": "profit",
                    "profit_percent": "profit_percent",
                    "commission": "commission",
                    "direction": "direction",
                    "entry_bar_index": "entry_bar_index",
                    "exit_bar_index": "exit_bar_index",
                    "entry_comment": "entry_comment",
                    "exit_comment": "exit_comment",
                    "entry_id": "entry_id",
                    "exit_id": "exit_id",
                    "entry_price": "entry_price",
                    "exit_price": "exit_price",
                    "entry_time": "entry_time",
                    "exit_time": "exit_time",
                    "size": "size",
                    "max_runup": "max_runup",
                    "max_runup_percent": "max_runup_percent",
                    "max_drawdown": "max_drawdown",
                    "max_drawdown_percent": "max_drawdown_percent",
                }
                fn = suffix_map.get(func_name, "profit")
                return f"{prefix}{fn}({idx})"

        # Defensive: support_checker rejects unknown strategy.* calls (name not
        # in sigs.STRATEGY_FUNCTIONS) and unknown strategy.closedtrades.* /
        # strategy.opentrades.* accessors (not in the side-specific accessor
        # whitelists). Reaching here means the checker was bypassed or drifted.
        raise ValueError(
            f"codegen: unhandled strategy.{func_name}(...) — analyzer should "
            f"have rejected. Add a handler above or extend STRATEGY_FUNCTIONS."
        )

    def _visit_color_call(self, func_name: str, node) -> str:
        """Emit color.* calls as integer representations."""
        args = [self._visit_expr(a) for a in node.args]
        if func_name == "new":
            if len(args) >= 2:
                return f'pine_color::new_color({args[0]}, (int)({args[1]}))'
            return "0"
        if func_name in ("r", "g", "b", "t"):
            if args:
                return f'pine_color::{func_name}({args[0]})'
            return "0"
        if func_name == "rgb":
            if len(args) >= 4:
                return f"pine_color::new_color(((int64_t)({args[0]}) << 16 | (int64_t)({args[1]}) << 8 | (int64_t)({args[2]})), (int)({args[3]}))"
            elif len(args) >= 3:
                return f"pine_color::new_color(((int64_t)({args[0]}) << 16 | (int64_t)({args[1]}) << 8 | (int64_t)({args[2]})), 0)"
            return "0"
        if func_name == "from_gradient":
            return "0"
        return "0"

    def _visit_str_call(self, func_name: str, node) -> str:
        args = _merge_kwargs(node.args, node.kwargs,
                             sigs.get_param_names("str", func_name), self._visit_expr)

        if func_name == "tostring":
            # Pine: str.tostring(enumVar) → field title / IANA string, not the int index
            val_arg = node.args[0] if node.args else node.kwargs.get("value")
            if isinstance(val_arg, Identifier):
                sym = self.ctx.symbols.resolve(val_arg.name)
                if sym is not None and sym.enum_type_name:
                    et = sym.enum_type_name
                    tbl = self._enum_member_strings.get(et)
                    if tbl:
                        var = self._safe_name(val_arg.name)
                        n = len(tbl)
                        return (
                            f"pine_enum_str_at({et}_str_values, {n}, {var})"
                        )
            if len(args) >= 2:
                return f"pine_str_tostring({args[0]}, {args[1]}, syminfo_mintick_)"
            if len(args) >= 1:
                return f"std::to_string({args[0]})"
            return 'std::string("")'

        if func_name == "substring":
            if len(args) == 3:
                return f"{args[0]}.substr({args[1]}, {args[2]} - {args[1]})"
            elif len(args) == 2:
                return f"{args[0]}.substr({args[1]})"
            return 'std::string("")'

        if func_name == "format":
            # str.format is variadic: signature has only ``formatStr``; the
            # remaining args are placeholder substitutions. The runtime
            # ``pine_str_format(fmt, vector<string>)`` requires every arg
            # already converted to ``std::string``. We previously gated the
            # ``std::to_string`` wrap on a source-text-prefix heuristic
            # (``"`` / ``std::string`` / ``pine_str``), which mis-classified
            # any string-typed bare identifier or string-returning helper
            # call (e.g. ``str.tostring(x)`` bound to a variable). The type
            # check below uses the analyzer's inferred PineType instead so
            # ``std::string`` args pass through unchanged.
            if node.args:
                fmt_arg = self._visit_expr(node.args[0])
                rest = []
                for orig in node.args[1:]:
                    visited = self._visit_expr(orig)
                    inferred = self._infer_type(orig)
                    if inferred == "std::string":
                        rest.append(visited)
                        continue
                    # Booleans render as 0/1 via std::to_string; force the
                    # TV-style "true"/"false" output so backtest logs and
                    # alert messages line up with the TradingView side.
                    if inferred == "bool":
                        rest.append(
                            f'({visited} ? std::string("true") : std::string("false"))'
                        )
                        continue
                    rest.append(f'std::to_string({visited})')
                if rest:
                    vec = "{" + ", ".join(rest) + "}"
                    return f'pine_str_format({fmt_arg}, {vec})'
                return fmt_arg
            return 'std::string("")'

        if func_name == "format_time":
            ts = args[0] if args else "0"
            fmt = args[1] if len(args) > 1 else '"yyyy-MM-dd"'
            tz = args[2] if len(args) > 2 else '"UTC"'
            return f'pine_str_format_time({ts}, {fmt}, {tz})'

        if func_name == "replace":
            if len(args) >= 4:
                # 4-arg form: replace the Nth occurrence (0-based, per Pine
                # spec). Out-of-range / negative occurrence → original string.
                return (
                    f'[&](){{ std::string s={args[0]}; std::string t={args[1]}; '
                    f'std::string r={args[2]}; int _occ=(int)({args[3]}); '
                    f'if(t.empty()||_occ<0) return s; '
                    f'size_t p=0; int _i=0; '
                    f'while((p=s.find(t,p))!=std::string::npos){{ '
                    f'if(_i==_occ){{ s.replace(p,t.length(),r); break; }} '
                    f'p+=t.length(); _i++; }} return s; }}()'
                )
            if len(args) >= 3:
                return f'[&](){{ std::string s={args[0]}; auto p=s.find({args[1]}); if(p!=std::string::npos) s.replace(p,{args[1]}.length(),{args[2]}); return s; }}()'
            return 'std::string("")'

        if func_name in STR_FUNC_MAP and STR_FUNC_MAP[func_name] is not None:
            return STR_FUNC_MAP[func_name](args)

        return f'std::string("") /* unsupported: str.{func_name} */'

    def _visit_math_call(self, func_name: str, node: FuncCall) -> str:
        args = _merge_kwargs(node.args, node.kwargs, sigs.get_param_names("math", func_name), self._visit_expr)
        # Handle special cases first
        if func_name == "round" and len(args) == 2:
            return f"(std::round({args[0]} * std::pow(10.0, {args[1]})) / std::pow(10.0, {args[1]}))"
        if func_name == "round_to_mintick":
            # Engine method (engine.hpp): NaN- and mintick<=0-guarded,
            # unlike the previous inlined unguarded std::round.
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
        if func_name in ("min", "max"):
            return _math_minmax_na_expr(func_name, args)
        if func_name in MATH_FUNC_MAP:
            mapped = MATH_FUNC_MAP[func_name]
            if "{0}" in mapped:
                return mapped.format(*args)
            return f"{mapped}({', '.join(args)})"
        # Unknown math.* — safe fallback
        return f"0.0 /* unsupported: math.{func_name} */"

    # ------------------------------------------------------------------
    # Arg/kwarg resolution (PineScript parameter signatures)
    # ------------------------------------------------------------------

    def _resolve_func_args(self, node: FuncCall, sig_key: str) -> dict:
        """Merge positional args and kwargs into a dict keyed by parameter name.

        Uses the PineScript parameter ordering from signatures registry.
        """
        # sig_key is like "strategy.entry" -> namespace="strategy", func_name="entry"
        parts = sig_key.split(".", 1)
        if len(parts) == 2:
            param_names = sigs.get_param_names(parts[0], parts[1]) or []
        else:
            param_names = sigs.get_param_names(None, sig_key) or []
        result: dict = {}
        # Map positional args by parameter name
        for i, arg in enumerate(node.args):
            if i < len(param_names):
                result[param_names[i]] = arg
        # kwargs override positional
        result.update(node.kwargs)
        return result
