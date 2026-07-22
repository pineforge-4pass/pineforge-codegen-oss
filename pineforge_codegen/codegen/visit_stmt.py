"""Statement-level visitors for the codegen.

``StmtVisitor`` holds the statement-level visitors that recurse into a
function body or top-level program. ``_visit_stmt`` is the central
dispatcher: it inspects the AST node kind and delegates to one of the
statement-kind handlers (``_visit_var_decl``, ``_visit_assignment``,
``_visit_tuple_assign``, ``_visit_if``, ``_visit_for``,
``_visit_for_in``, ``_visit_while``, ``_visit_switch``) or emits a
trivial ``break;`` / ``continue;`` / expression statement directly.
The two if/switch-as-expression helpers (``_emit_body_with_assign``
and ``_visit_if_switch_expr``) live alongside the other visitors
because they recurse back into ``_visit_stmt`` for nested control
flow.

These visitors were extracted from ``base.py``'s ``CodeGen`` class as
step 8 of the codegen package refactor; behaviour is preserved
verbatim. The mixin owns no state of its own — it reads/writes only
attributes already established on the host class (``CodeGen``).

Mixin contract — host class must provide the following attributes
(all set by ``CodeGen.__init__`` or other mixins):

- ``self.ctx`` (``AnalyzerContext``): symbol table source. Reads
  ``ctx.series_vars`` to decide between ``Series<T>::push`` /
  ``Series<T>::update`` and a plain assignment.
- ``self._var_names`` (``set[str]``): names declared at module scope
  (used to drive the assignment lowering).
- ``self._global_member_vars`` (``set[str]``): non-``var`` global
  declarations emitted as class members (assignment-only path in
  ``_visit_var_decl``).
- ``self._array_vars`` / ``self._map_vars`` (``set[str]``) and
  ``self._matrix_specs`` (``dict[str, TypeSpec]``): collection-typed
  variables; ``_visit_var_decl``
  registers new entries when it sees ``array.new`` / ``map.new`` /
  ``matrix.new``.
- ``self._collection_types`` (``dict[str, TypeSpec]``):
  ``_visit_var_decl`` populates it from inferred specs.
- ``self._active_var_remap`` (``dict[str, str]``): per-call-site
  rename map for cloned function-local var/series names.
- ``self._current_loop_vars`` (``set[str]``): for-in iterator names;
  saved/restored around ``_visit_for_in`` bodies so member-access
  resolution can distinguish iterators from enum constants.
- ``self._switch_counter`` (``int``): monotonically incremented by
  ``_visit_switch`` / ``_visit_if_switch_expr`` to mint fresh
  ``__switch_val_<n>`` temporary names.
- ``self._func_names`` (``set[str]``): user-defined function names;
  consulted by ``_visit_tuple_assign`` to spot tuple-returning calls.

Sibling-mixin methods consumed via ``self``:

- ``NamingHelper`` (``codegen/helpers.py``): ``_safe_name``,
  ``_resolve_callee``, ``_get_target_name``.
- ``TypeInferer`` (``codegen/types.py``): ``_type_for_decl``,
  ``_type_spec_to_cpp``, ``_default_for_type``,
  ``_type_spec_from_expr``, ``_array_spec_for_name``,
  ``_map_spec_for_name``.
- ``TaSiteHelper`` (``codegen/ta.py``): ``_get_ta_site``,
  ``_ta_member_name``, ``_ta_compute_args_for_site``,
  ``_ta_name_from_site``.
- ``InputHelper`` (``codegen/input.py``): ``_is_input_call``,
  ``_get_input_default``, ``_get_input_title``,
  ``_input_type_to_getter``,
  ``_enforce_enum_declared_before_input_enum``.
- ``CodeGen.base``: ``_visit_expr``, ``_visit_func_call``,
  ``_is_skip_expr`` (still on the host class — the expression
  visitors and the skip-expression predicate are extracted in later
  refactor steps).

The mixin avoids importing from ``base.py`` to stay free of cycles;
all tables it needs come from ``codegen/tables.py`` and all AST
classes from ``..ast_nodes``.
"""

from __future__ import annotations

from ..ast_nodes import (
    ASTNode,
    Assignment,
    BreakStmt,
    ContinueStmt,
    EnumDecl,
    ExprStmt,
    ForInStmt,
    ForStmt,
    FuncCall,
    FuncDef,
    Identifier,
    IfStmt,
    ImportStmt,
    MemberAccess,
    MethodDef,
    StrategyDecl,
    SwitchStmt,
    Ternary,
    TupleAssign,
    TupleLiteral,
    TypeDecl,
    VarDecl,
    WhileStmt,
)
from ..symbols import PineType, TypeSpec, method_receiver_type_name
from .tables import (
    ARRAY_NEW_CTORS,
    DRAWING_TYPE_TO_CPP,
    TA_RETURNS_BOOL,
    TA_TUPLE_FIELDS,
    MATRIX_RETURNING_METHODS,
)

# Sentinel for "no block-scoped overlay was activated" so an empty saved map
# is still distinguishable from the no-op case.
_NO_BLOCK_REMAP = object()


class StmtVisitor:
    """Statement-level visitor methods shared across the codegen.

    Mixed into ``CodeGen``; not intended to be instantiated standalone.
    See the module docstring for the full host-class state contract."""

    # ------------------------------------------------------------------
    # Statement visitors
    # ------------------------------------------------------------------

    def _visit_stmt(self, node: ASTNode, lines: list[str], indent: int) -> None:
        pad = "    " * indent

        if isinstance(node, StrategyDecl):
            return
        if isinstance(node, ImportStmt):
            return
        if isinstance(node, FuncDef):
            return  # handled separately as class methods
        if isinstance(node, TypeDecl):
            return  # handled in struct emission
        if isinstance(node, EnumDecl):
            return  # handled in enum constant emission
        if isinstance(node, MethodDef):
            return  # handled as class method via FuncInfo
        if isinstance(node, VarDecl):
            is_callable_binding = (
                getattr(self, "_active_func_name", None) is not None
                and id(node) in self._callable_collection_bindings
            )
            activation_spec = (
                self._callable_collection_bindings.get(id(node))
                if is_callable_binding
                else None
            )
            if is_callable_binding and activation_spec is None:
                inferred_spec = self._type_spec_from_expr(node.value)
                if (inferred_spec is not None
                        and inferred_spec.kind in {"array", "map", "matrix"}):
                    activation_spec = inferred_spec
            previous_pending_alias = self._pending_decl_outer_alias
            if (is_callable_binding
                    and not node.is_var
                    and not node.is_varip
                    and self._collection_spec_for_name(node.name) is not None
                    and any(
                        isinstance(part, Identifier) and part.name == node.name
                        for part in self._walk_ast(node.value)
                    )):
                safe_name = self._safe_name(node.name)
                if (node.name in self._global_collection_types
                        and node.name not in self._current_func_param_specs
                        and node.name not in self._current_func_collection_specs
                        and node.name not in self._current_loop_var_specs):
                    outer_expr = f"this->{safe_name}"
                else:
                    outer_expr = self._active_var_remap.get(safe_name, safe_name)
                occupied_alias_names = (
                    set(self._all_bound_names)
                    | set(self._collection_shadow_tmp_names)
                    | set(self._current_func_param_types)
                    | {
                        self._safe_name(param)
                        for param in self._current_func_param_types
                    }
                )
                while True:
                    alias = (
                        f"_pf_outer_{safe_name}_"
                        f"{self._collection_shadow_tmp_counter}"
                    )
                    self._collection_shadow_tmp_counter += 1
                    if alias not in occupied_alias_names:
                        break
                self._collection_shadow_tmp_names.add(alias)
                lines.append(f"{pad}auto& {alias} = {outer_expr};")
                self._pending_decl_outer_alias = {
                    **previous_pending_alias,
                    node.name: alias,
                }
            try:
                self._visit_var_decl(node, lines, pad)
            finally:
                self._pending_decl_outer_alias = previous_pending_alias
            if is_callable_binding:
                self._activate_callable_collection_binding(
                    node.name,
                    activation_spec,
                )
            metadata = getattr(
                self.ctx, "var_member_metadata_by_node", {}
            ).get(id(node))
            if metadata is not None:
                member_name = metadata[1]
                if member_name != node.name:
                    # The declaration RHS was emitted against the inherited
                    # binding.  Only now activate the collision-safe member for
                    # later statements in this exact lexical block.
                    exact_storage = self._safe_name(member_name)
                    # A callable variant may already map the exact member to a
                    # per-call-site clone (``x__blk1`` -> ``x__blk1_cs1``).
                    # Preserve that outer clone mapping when exposing the raw
                    # lexical spelling after the declaration.
                    exact_storage = self._active_var_remap.get(
                        exact_storage, exact_storage
                    )
                    self._active_var_remap = dict(self._active_var_remap)
                    self._active_var_remap[self._safe_name(node.name)] = (
                        exact_storage
                    )
                drawing_info = self._drawing_var_decl_info_by_node.get(id(node))
                if (drawing_info is not None
                        and drawing_info.get("is_callable_scoped")):
                    raw_safe = self._safe_name(node.name)
                    storage = self._active_var_remap.get(
                        raw_safe, self._safe_name(member_name)
                    )
                    if storage.startswith("this->"):
                        storage = storage[len("this->"):]
                    self._active_var_remap = dict(self._active_var_remap)
                    self._active_var_remap[raw_safe] = f"this->{storage}"
                self._lexical_series_bindings[node.name] = (
                    self._safe_name(member_name)
                    in self._series_var_member_names
                )
            else:
                self._lexical_series_bindings[node.name] = (
                    self._decl_binding_is_series(id(node), node.name)
                )
            if id(node) not in self._direct_program_var_decl_nodes:
                self._lexical_known_var_tombstones.add(node.name)
            decl_spec = getattr(
                self.ctx, "var_member_type_specs_by_node", {}
            ).get(id(node))
            if decl_spec is None:
                decl_spec = (
                    self._type_spec_from_hint_name(node.type_hint)
                    if node.type_hint
                    else self._type_spec_from_expr(node.value)
                )
            self._lexical_drawing_types[node.name] = (
                DRAWING_TYPE_TO_CPP.get(decl_spec.name)
                if decl_spec is not None and decl_spec.kind == "udt"
                else None
            )
            self._lexical_udt_types[node.name] = (
                decl_spec.name
                if (decl_spec is not None
                    and decl_spec.kind == "udt"
                    and decl_spec.name in self._udt_defs)
                else None
            )
            if (
                node.name == "map"
                and getattr(self, "_block_map_visibility_depth", 0) > 0
            ):
                # The declaration RHS was emitted against the outer lexical
                # state. Only subsequent statements in this exact block see
                # the new ``map`` value; block pop restores sibling/outside
                # visibility.
                self._block_map_binding_visible = True
        elif isinstance(node, Assignment):
            self._visit_assignment(node, lines, pad)
        elif isinstance(node, TupleAssign):
            self._visit_tuple_assign(node, lines, pad)
            tuple_cpp_types = self._tuple_binding_cpp_types(node)
            if getattr(self, "_active_func_name", None) is not None:
                for name in node.names:
                    if name and name != "_":
                        self._activate_callable_collection_binding(name, None)
            scalar_names = [
                name
                for name in node.names
                if name
                and name != "_"
                and not self._decl_binding_is_series(id(node), name)
            ]
            if any(
                self._safe_name(name) in self._active_var_remap
                for name in scalar_names
            ):
                # Tuple declarations are lexical bindings.  A scalar element
                # must shadow any same-spelled Series storage inherited from a
                # broad callable call-site remap; otherwise a later read can
                # silently resolve to an unrelated ``x_csN`` member even
                # though the structured binding declared a local ``x``.
                self._active_var_remap = dict(self._active_var_remap)
                for name in scalar_names:
                    self._active_var_remap.pop(self._safe_name(name), None)
            for index, name in enumerate(node.names):
                if name and name != "_":
                    # A supported tuple destructure creates fresh lexical
                    # bindings.  Tuple elements are primitive on the current
                    # supported surface, so install an explicit tombstone: an
                    # unrelated same-named global/callable UDT must not target-
                    # type a later ``name := na`` as ``State{}``.
                    self._lexical_udt_types[name] = None
                    if (getattr(self, "_active_func_name", None) is not None
                            and not self._decl_binding_is_series(
                                id(node), name
                            )):
                        self._current_func_local_types[name] = (
                            tuple_cpp_types[index]
                            if index < len(tuple_cpp_types)
                            else "double"
                        )
                    self._lexical_series_bindings[name] = (
                        self._decl_binding_is_series(id(node), name)
                    )
                    if id(node) not in self._direct_program_tuple_decl_nodes:
                        self._lexical_known_var_tombstones.add(name)
        elif isinstance(node, IfStmt):
            self._visit_if(node, lines, indent)
        elif isinstance(node, ForStmt):
            self._visit_for(node, lines, indent)
        elif isinstance(node, ForInStmt):
            self._visit_for_in(node, lines, indent)
        elif isinstance(node, WhileStmt):
            self._visit_while(node, lines, indent)
        elif isinstance(node, SwitchStmt):
            self._visit_switch(node, lines, indent)
        elif isinstance(node, BreakStmt):
            lines.append(f"{pad}break;")
        elif isinstance(node, ContinueStmt):
            lines.append(f"{pad}continue;")
        elif isinstance(node, ExprStmt):
            # Intercept strategy.risk.* calls
            if isinstance(node.expr, FuncCall) and isinstance(node.expr.callee, MemberAccess):
                c = node.expr.callee
                if (isinstance(c.object, MemberAccess) and isinstance(c.object.object, Identifier)
                        and c.object.object.name == "strategy" and c.object.member == "risk"
                        and node.expr.args):
                    risk_func = c.member
                    _RISK_MEMBER_MAP = {
                        "max_intraday_filled_orders": ("max_intraday_filled_orders_", "int"),
                        "max_drawdown": ("risk_max_drawdown_", "double"),
                        "max_intraday_loss": ("risk_max_intraday_loss_", "double"),
                        "max_position_size": ("risk_max_position_size_", "double"),
                        "max_cons_loss_days": ("risk_max_cons_loss_days_", "int"),
                    }
                    if risk_func == "allow_entry_in":
                        val = self._visit_expr(node.expr.args[0])
                        if val == "1":
                            lines.append(f"{pad}risk_direction_ = RiskDirection::LONG_ONLY;")
                        elif val == "-1":
                            lines.append(f"{pad}risk_direction_ = RiskDirection::SHORT_ONLY;")
                        else:
                            lines.append(f"{pad}risk_direction_ = RiskDirection::BOTH;")
                        return
                    if risk_func in _RISK_MEMBER_MAP:
                        member, cast_type = _RISK_MEMBER_MAP[risk_func]
                        val = self._visit_expr(node.expr.args[0])
                        lines.append(f"{pad}{member} = ({cast_type})({val});")
                        # Handle percent_of_equity flag for max_drawdown / max_intraday_loss
                        if risk_func in ("max_drawdown", "max_intraday_loss") and len(node.expr.args) >= 2:
                            arg2 = node.expr.args[1]
                            is_pct = (isinstance(arg2, MemberAccess)
                                      and isinstance(arg2.object, Identifier)
                                      and arg2.object.name == "strategy"
                                      and arg2.member == "percent_of_equity")
                            if is_pct:
                                pct_flag = "risk_max_drawdown_is_pct_" if risk_func == "max_drawdown" else "risk_max_intraday_loss_is_pct_"
                                lines.append(f"{pad}{pct_flag} = true;")
                        return
            if self._is_skip_expr(node.expr):
                return
            # matrix.concat / m.concat as a statement: engine concat returns a
            # new matrix and is marked [[nodiscard]]. Pine semantics is mutate
            # the first argument. Capture the result back into the receiver so
            # we get the mutation AND avoid the warning.
            recv_for_concat = self._concat_receiver_name(node.expr)
            if recv_for_concat is not None:
                cpp = self._visit_expr(node.expr)
                target = self._safe_name(recv_for_concat)
                lines.append(f"{pad}{target} = {cpp};")
                return
            cpp = self._visit_expr(node.expr)
            if cpp.startswith("/* "):
                return
            # Never emit a bare invalid C++ token (e.g. type names leaked as statements).
            stripped = cpp.strip()
            if stripped == "color" or stripped.startswith("(int64_t)pine_color::"):
                return
            lines.append(f"{pad}{cpp};")

    def _concat_receiver_name(self, expr) -> str | None:
        """If ``expr`` is a Pine ``matrix.concat`` call (in either method
        form ``m.concat(other, ...)`` or namespaced form
        ``matrix.concat(m, other, ...)``) on a known matrix variable,
        return the receiver variable name. Otherwise return None.

        Engine ``PineGenericMatrix::concat`` is ``[[nodiscard]]`` and Pine
        semantics is mutate-receiver, so the statement form must be lowered
        to ``recv = recv.concat(...);``.
        """
        if not isinstance(expr, FuncCall) or not isinstance(expr.callee, MemberAccess):
            return None
        callee = expr.callee
        if callee.member != "concat":
            return None
        # m.concat(other, ...) — receiver is callee.object
        if isinstance(callee.object, Identifier):
            recv = callee.object.name
            recv_spec = self._collection_spec_for_name(recv)
            if recv_spec is not None and recv_spec.kind == "matrix":
                return recv
        # matrix.concat(m, other, ...) — receiver is first arg
        if (isinstance(callee.object, Identifier)
                and callee.object.name == "matrix"
                and expr.args
                and isinstance(expr.args[0], Identifier)):
            recv = expr.args[0].name
            recv_spec = self._collection_spec_for_name(recv)
            if recv_spec is not None and recv_spec.kind == "matrix":
                return recv
        return None

    def _nullable_collection_target_cpp_type(
        self,
        *,
        name: str | None = None,
        target_node=None,
        type_hint: str | None = None,
    ) -> str | None:
        """Return the exact nullable collection type for an RHS target.

        Declarations need the explicit hint before their lexical collection
        binding is activated; reassignments can use the active name registry,
        while UDT fields are resolved from the target expression itself.
        Arrays are deliberately excluded until their runtime representation
        can distinguish ``na`` from a valid empty ID.
        """
        spec = self._type_spec_from_hint_name(type_hint) if type_hint else None
        if spec is None and target_node is not None:
            spec = self._type_spec_from_expr(target_node)
        if spec is None and name is not None:
            spec = self._collection_spec_for_name(name)
        if spec is None or spec.kind not in {"map", "matrix"}:
            return None
        return self._type_spec_to_cpp(spec)

    def _visit_var_decl(self, node: VarDecl, lines: list[str], pad: str) -> None:
        member_meta = getattr(
            self.ctx, "var_member_metadata_by_node", {}
        ).get(id(node))
        if member_meta is not None:
            declaration_is_series = (
                self._safe_name(member_meta[1])
                in self._series_var_member_names
            )
        else:
            declaration_is_series = (
                self._decl_binding_is_series(id(node), node.name)
            )
        # Primitive runtime var/varip initializers execute at the declaration
        # site under a dedicated once flag. This preserves source order (a
        # preceding input/plain/TA declaration is already available) and Pine's
        # first-entry semantics for declarations nested in conditional blocks.
        # Series, aggregates, constructor constants, and function-local vars
        # retain their specialized initialization paths.
        if node.is_var or node.is_varip:
            info = self._runtime_scalar_var_init_by_node.get(id(node))
            if info is not None:
                member_name = info["member_name"]
                target = self._safe_name(member_name)
                if self._active_var_remap and target in self._active_var_remap:
                    target = self._active_var_remap[target]
                flag = self._runtime_var_init_flags.get(
                    (info["node_id"], target),
                    info["flag"],
                )
                target_expr = (
                    f"this->{target}"
                    if info.get("is_callable_scoped")
                    else target
                )
                flag_expr = (
                    f"this->{flag}"
                    if info.get("is_callable_scoped")
                    else flag
                )
                previous_input_name = self._current_input_var_name
                self._current_input_var_name = node.name
                try:
                    type_spec = info.get("type_spec")
                    target_cpp_type = info.get("drawing_cpp")
                    if (
                        target_cpp_type is None
                        and type_spec is not None
                        and type_spec.kind in {"map", "matrix"}
                    ):
                        target_cpp_type = self._type_spec_to_cpp(type_spec)
                    if (
                        target_cpp_type is not None
                        and type_spec is not None
                        and type_spec.kind in {"map", "matrix"}
                        and isinstance(node.value, (IfStmt, SwitchStmt))
                    ):
                        lines.append(f"{pad}if (!{flag_expr}) {{")
                        self._visit_if_switch_expr(
                            node.value,
                            target_expr,
                            lines,
                            len(pad) // 4 + 1,
                            target_cpp_type=target_cpp_type,
                        )
                        # The flag belongs after the complete selection.  If it
                        # were emitted in an individual branch, unmatched paths
                        # or later arms could retry a Pine ``var`` initializer,
                        # violating first-reach / one-time semantics.
                        lines.append(f"{pad}    {flag_expr} = true;")
                        lines.append(f"{pad}}}")
                        return
                    if target_cpp_type is not None:
                        init_cpp = self._visit_rhs_value(
                            node.value,
                            member_name,
                            target_cpp_type=target_cpp_type,
                        )
                    else:
                        init_cpp = self._visit_expr(node.value)
                finally:
                    self._current_input_var_name = previous_input_name
                if info.get("drawing_cpp") is None:
                    init_cpp = self._typed_na_init(
                        init_cpp, member_name, info["ptype"]
                    )
                lines.append(f"{pad}if (!{flag_expr}) {{")
                # A history-referenced persistent primitive is a Series<T> too,
                # not just a drawing handle.  The per-bar carry has already
                # advanced it before this declaration-site one-shot runs, so
                # replace the current slot rather than assigning a scalar to
                # the Series object (or pushing a duplicate bar).
                if info.get("is_series"):
                    lines.append(f"{pad}    {target_expr}.update({init_cpp});")
                else:
                    lines.append(f"{pad}    {target_expr} = {init_cpp};")
                lines.append(f"{pad}    {flag_expr} = true;")
                lines.append(f"{pad}}}")
            return

        safe = self._safe_name(node.name)
        # Apply per-call-site var remap (for function-local vars)
        if self._active_var_remap and safe in self._active_var_remap:
            safe = self._active_var_remap[safe]
        # Only a declaration in the script body can bind a hoisted global
        # member.  A callable-local declaration may legally shadow that global
        # with the same raw name; treating it as the member emits an assignment
        # into unrelated storage (and can assign a scalar into Series<T> when
        # the global was promoted for an indirect history call).
        is_global_member = (
            getattr(self, "_active_func_name", None) is None
            and node.name in self._global_member_vars
            and (
                id(node) in self._ordinary_global_var_decl_nodes
                or (
                    node.name not in self._direct_program_binding_names
                    and node.name not in self._callable_state_raw_names
                )
            )
        )

        def remember_local_type(cpp_type: str | None) -> None:
            if cpp_type and not is_global_member:
                self._current_func_local_types[node.name] = cpp_type

        # Check if it is a static (non-series) global member variable already evaluated inside _inputs_initialized_ block
        is_static_global_input = False
        if is_global_member and isinstance(node.value, FuncCall) and self._is_input_call(node.value):
            func_name_i, namespace_i = self._resolve_callee(node.value.callee)
            is_static_global_input = (
                not self._is_source_input(node.value)
                and node.name not in self._array_vars
                and node.name not in getattr(self, "_matrix_specs", {})
                and node.name not in getattr(self, "_map_vars", {})
                and not node.is_var
                and not node.is_varip
            )

        if is_static_global_input:
            # Skip, already evaluated in _inputs_initialized_ block!
            return

        # input() call — emit runtime get_input_*() lookup
        if isinstance(node.value, FuncCall) and self._is_input_call(node.value):
            func_name_i, namespace_i = self._resolve_callee(node.value.callee)

            if namespace_i == "input" and func_name_i == "enum":
                self._enforce_enum_declared_before_input_enum(node.value)
            title = self._get_input_title(node.value, var_name=node.name)
            cpp_val = self._render_input_value(node.value, func_name_i, namespace_i, title)
            if declaration_is_series:
                self._emit_history_series_write(lines, pad, safe, cpp_val)
            elif is_global_member:
                lines.append(f"{pad}{safe} = {cpp_val};")
            else:
                cpp_type = self._type_for_decl(node)
                lines.append(f"{pad}{cpp_type} {safe} = {cpp_val};")
            return

        # Array variable declarations: array.new<T>(), array.from(),
        # array.new_float() etc., plus array-returning copy/slice.
        if isinstance(node.value, FuncCall):
            func_name, namespace = self._resolve_callee(node.value.callee)
            if namespace == "array" and func_name in ARRAY_NEW_CTORS | {"new", "from", "copy", "slice"}:
                captured = self._callable_collection_bindings.get(id(node))
                spec = (
                    captured
                    if captured is not None and captured.kind == "array"
                    else self._type_spec_from_expr(node.value)
                        or self._array_spec_for_name(node.name)
                )
                init = self._visit_expr(node.value)
                self._array_vars.add(node.name)
                self._collection_types.setdefault(node.name, spec)
                cpp_type = self._type_spec_to_cpp(spec)
                if is_global_member:
                    lines.append(f"{pad}{safe} = {init};")
                else:
                    lines.append(f"{pad}{cpp_type} {safe} = {init};")
                return

        # Map variable declarations: map.new<K,V>()
        if isinstance(node.value, FuncCall):
            func_name, namespace = self._resolve_callee(node.value.callee)
            if namespace == "matrix" and func_name == "new":
                targs = self._template_args_from_call(node.value) if hasattr(node.value, "annotations") else []
                elem_spec = self._type_spec_from_hint_name(targs[0]) if targs else TypeSpec.primitive("float")
                captured = self._callable_collection_bindings.get(id(node))
                spec = (
                    captured
                    if captured is not None and captured.kind == "matrix"
                    else TypeSpec.matrix(elem_spec)
                )
                elem_spec = spec.element or elem_spec
                cpp_type = self._type_spec_to_cpp(spec)
                if len(node.value.args) >= 2:
                    r = self._visit_expr(node.value.args[0])
                    c = self._visit_expr(node.value.args[1])
                    v = self._visit_expr(node.value.args[2]) if len(node.value.args) > 2 else self._default_for_spec(elem_spec)
                    init = f"{cpp_type}::new_({r}, {c}, {v})"
                else:
                    init = f"{cpp_type}::new_(0, 0, {self._default_for_spec(elem_spec)})"
                self._matrix_specs[node.name] = spec
                self._collection_types[node.name] = spec
                if is_global_member:
                    lines.append(f"{pad}{safe} = {init};")
                else:
                    lines.append(f"{pad}{cpp_type} {safe} = {init};")
                return
            # ``var inv = matrix.inv(m)`` — RHS is a matrix-returning method
            # (inv / pinv / transpose / copy / submatrix / concat / diff /
            # mult / pow / eigenvectors / kron). Without this branch the LHS
            # falls through to the analyzer's default ``double`` and clang
            # rejects ``double = PineMatrix``. The RHS expression itself is
            # already lowered to the right ``m.inv()`` form by visit_call.
            if namespace == "matrix" and func_name in MATRIX_RETURNING_METHODS:
                recv_name = self._extract_receiver_name(node.value)
                recv_spec = (
                    self._collection_spec_for_name(recv_name)
                    if recv_name is not None
                    else None
                )
                if recv_spec is None or recv_spec.kind != "matrix":
                    recv_spec = TypeSpec.matrix(TypeSpec.primitive("float"))
                captured = self._callable_collection_bindings.get(id(node))
                if captured is not None and captured.kind == "matrix":
                    recv_spec = captured
                init = self._visit_expr(node.value)
                self._matrix_specs[node.name] = recv_spec
                self._collection_types[node.name] = recv_spec
                cpp_type = self._type_spec_to_cpp(recv_spec)
                if is_global_member:
                    lines.append(f"{pad}{safe} = {init};")
                else:
                    lines.append(f"{pad}{cpp_type} {safe} = {init};")
                return
            if namespace == "map" and func_name == "new":
                captured = self._callable_collection_bindings.get(id(node))
                spec = (
                    captured
                    if captured is not None and captured.kind == "map"
                    else self._type_spec_from_expr(node.value)
                        or self._map_spec_for_name(node.name)
                )
                self._map_vars.add(node.name)
                self._collection_types.setdefault(node.name, spec)
                cpp_type = self._type_spec_to_cpp(spec)
                init = f"{cpp_type}::new_()"
                if is_global_member:
                    lines.append(f"{pad}{safe} = {init};")
                else:
                    lines.append(f"{pad}{cpp_type} {safe} = {init};")
                return

        # Visual/drawing function assignments (line.new, label.new, box.new,
        # table.new, ...) are no-ops in a backtest, but the assigned variable may
        # still be referenced later (e.g. pushed into an array<line>, or used as a
        # handle by sibling set_* calls). Emit a default-valued local declaration
        # so those references compile; the value is inert. Global members are
        # already declared at class scope, so only locals need this. (Previously
        # only `table` results were declared, which dropped loop-local line/label
        # handles and produced "use of undeclared identifier".)
        if isinstance(node.value, FuncCall) and self._is_skip_expr(node.value):
            if not is_global_member:
                cpp_type = self._type_for_decl(node)
                default = "0" if cpp_type in ("int", "double") else ('std::string("")' if cpp_type == "std::string" else "false")
                lines.append(f"{pad}{cpp_type} {safe} = {default};")
            return

        # TA call
        site = self._get_ta_site(node.value)
        if site is not None:
            compute_args = self._ta_compute_args_for_site(site)
            ret_type = "bool" if self._ta_name_from_site(site) in TA_RETURNS_BOOL else "double"
            ta_name = self._ta_member_name(site)
            ta_expr = (
                f"(history_advances_new_bar() ? {ta_name}.compute({compute_args}) "
                f": {ta_name}.recompute({compute_args}))"
            )
            if declaration_is_series:
                self._emit_history_series_write(lines, pad, safe, ta_expr)
            elif is_global_member:
                lines.append(f"{pad}{safe} = {ta_expr};")
            else:
                lines.append(f"{pad}{ret_type} {safe} = {ta_expr};")
            return

        # Non-var series variable — push instead of declare
        if declaration_is_series:
            target_cpp_type = self._type_for_decl(node)
            cpp_val = self._visit_rhs_value(
                node.value,
                node.name,
                target_cpp_type=(
                    target_cpp_type
                    if target_cpp_type in DRAWING_TYPE_TO_CPP.values()
                    else None
                ),
            )
            self._emit_history_series_write(lines, pad, safe, cpp_val)
            return

        # If/switch expression: x = if cond ... else ...
        if isinstance(node.value, (IfStmt, SwitchStmt)):
            cpp_type = self._type_for_decl(node) if not is_global_member else None
            selection_cpp_type = (
                cpp_type
                if (cpp_type is not None
                    and (self._is_nullable_collection_cpp_type(cpp_type)
                         or cpp_type in DRAWING_TYPE_TO_CPP.values()
                         or cpp_type in self._udt_defs))
                else self._nullable_collection_target_cpp_type(
                    name=node.name,
                    type_hint=node.type_hint,
                )
            )
            if selection_cpp_type is None:
                selection_cpp_type = self._udt_target_cpp_type(
                    target_name=node.name,
                    type_hint=node.type_hint,
                )
            if selection_cpp_type is None:
                selection_cpp_type = self._drawing_target_cpp_type(
                    node.name,
                    cpp_type,
                )
            if not is_global_member:
                default = self._default_for_type(cpp_type)
                lines.append(f"{pad}{cpp_type} {safe} = {default};")
                remember_local_type(cpp_type)
            indent = len(pad) // 4
            self._visit_if_switch_expr(
                node.value,
                safe,
                lines,
                indent,
                target_cpp_type=selection_cpp_type,
            )
            return

        # Collection lvalue alias (BUG 2): a local bound to an existing array /
        # map / matrix lvalue (or a ternary/switch selecting same-typed ones)
        # and later MUTATED through must ALIAS the member, not value-copy — Pine
        # collections are reference types. Proven: jevondijefferson-big-breakout
        # does ``array<orderBlock> orderBlocks = internal ? internalOrderBlocks
        # : swingOrderBlocks`` then ``orderBlocks.unshift(ob)`` in three helpers;
        # the value-copy left the member arrays empty. Emit a non-rebinding C++
        # reference instead.
        if not is_global_member:
            coll_spec = self._collection_lvalue_selection_spec(node.value)
            if coll_spec is not None and self._collection_local_must_alias(node):
                cpp_type = self._type_spec_to_cpp(coll_spec)
                cpp_val = self._visit_rhs_value(node.value, node.name, target_cpp_type=cpp_type)
                # Register the local's collection kind so subsequent
                # ``.size()/.get()/.unshift()`` dispatch resolves correctly.
                self._collection_types[node.name] = coll_spec
                if coll_spec.kind == "array":
                    self._array_vars.add(node.name)
                elif coll_spec.kind == "map":
                    self._map_vars.add(node.name)
                elif coll_spec.kind == "matrix":
                    self._matrix_specs[node.name] = coll_spec
                # PineMap is itself a shared-ID handle. Copying it by value is
                # the correct alias operation and, unlike a C++ reference,
                # keeps a later local rebind local. Arrays/matrices still need
                # their established reference alias route.
                ref = "" if coll_spec.kind == "map" else "&"
                lines.append(f"{pad}{cpp_type}{ref} {safe} = {cpp_val};")
                return

        # General declaration
        cpp_type = self._type_for_decl(node) if not is_global_member else None
        target_cpp_type = cpp_type or self._nullable_collection_target_cpp_type(
            name=node.name,
            type_hint=node.type_hint,
        )
        if target_cpp_type is None:
            target_cpp_type = self._udt_target_cpp_type(
                target_name=node.name,
                type_hint=node.type_hint,
            )
        cpp_val = self._visit_rhs_value(
            node.value,
            node.name,
            target_cpp_type=target_cpp_type,
        )
        if is_global_member:
            lines.append(f"{pad}{safe} = {cpp_val};")
        else:
            remember_local_type(cpp_type)
            lines.append(f"{pad}{cpp_type} {safe} = {cpp_val};")

    @staticmethod
    def _compound_assign_rhs(target_read: str, op: str, val_cpp: str) -> str | None:
        """RHS for a compound assignment that must NOT lower to the C++
        compound operator.

        Pine v6 ``/`` is always-float (int/int included) and ``%`` is
        fmod-like — the binary-operator lowering in visit_expr casts both
        sides to double / uses std::fmod. ``a /= b`` and ``a %= b`` must
        match those semantics (C++ ``/=`` would do integer division on int
        operands; ``%=`` does not even compile for doubles). Returns None
        for operators where the native C++ compound form is correct
        (+=, -=, *=).
        """
        if op == "/=":
            return f"((double)({target_read}) / (double)({val_cpp}))"
        if op == "%=":
            return f"std::fmod((double)({target_read}), (double)({val_cpp}))"
        return None

    def _matrix_rhs_specs(self, value: ASTNode | None) -> list[TypeSpec]:
        """Return every known concrete matrix type in an RHS selection.

        ``_type_spec_from_expr`` intentionally returns ``None`` for a
        selection whose concrete arms disagree.  Reassignment validation must
        still inspect those arms individually: otherwise
        ``matrix<int> := cond ? matrix<int> : matrix<float>`` evades the type
        check precisely because the complete selection has no unified type.
        Explicit and implicit ``na`` arms contribute no concrete type.
        """
        if self._selection_node_is_na(value):
            return []
        if isinstance(value, Ternary):
            return (
                self._matrix_rhs_specs(value.true_val)
                + self._matrix_rhs_specs(value.false_val)
            )
        if isinstance(value, IfStmt):
            return (
                self._matrix_rhs_specs(
                    self._selection_terminal_expr(value.body)
                )
                + self._matrix_rhs_specs(
                    self._selection_terminal_expr(value.else_body)
                )
            )
        if isinstance(value, SwitchStmt):
            specs: list[TypeSpec] = []
            for _case_expr, case_body in value.cases:
                specs.extend(
                    self._matrix_rhs_specs(
                        self._selection_terminal_expr(case_body)
                    )
                )
            specs.extend(
                self._matrix_rhs_specs(
                    self._selection_terminal_expr(value.default_body)
                )
            )
            return specs
        spec = self._type_spec_from_expr(value)
        return [spec] if spec is not None and spec.kind == "matrix" else []

    def _validate_matrix_reassignment(
        self,
        node: Assignment,
        target_name: str | None,
    ) -> None:
        """Reject any known matrix RHS whose element type changes the LHS.

        This check belongs to the lexical target rather than any particular
        storage representation. A matrix may be a persistent ``var``, a plain
        global member, a callable local, or a UDT field, but Pine forbids
        changing its declared element type in every case.
        """
        if node.op != ":=":
            return
        target_spec = self._type_spec_from_expr(node.target)
        if target_spec is None and target_name is not None:
            target_spec = self._collection_spec_for_name(target_name)
        if target_spec is None or target_spec.kind != "matrix":
            return
        target_label = target_name
        if target_label is None and isinstance(node.target, MemberAccess):
            target_label = node.target.member
        target_label = target_label or "<expression>"
        for rhs_spec in self._matrix_rhs_specs(node.value):
            if rhs_spec.element == target_spec.element:
                continue
            self._codegen_error(
                node,
                f"matrix '{target_label}' element type mismatch on reassignment: "
                f"expected {self._type_spec_to_cpp(target_spec)}, "
                f"got {self._type_spec_to_cpp(rhs_spec)}",
            )

    def _visit_assignment(self, node: Assignment, lines: list[str], pad: str) -> None:
        if isinstance(node.value, FuncCall) and self._is_skip_expr(node.value):
            return

        # Validate against the active lexical target before any syntax-specific
        # early return. In particular, if/switch expressions and UDT-field
        # targets otherwise bypass the ordinary identifier assignment path.
        target_name = self._get_target_name(node.target)
        self._validate_matrix_reassignment(node, target_name)

        # If/switch expression in assignment: x := if cond ...
        if isinstance(node.value, (IfStmt, SwitchStmt)):
            safe = (
                self._safe_name(target_name)
                if target_name
                else self._visit_mutable_expr(node.target)
            )
            selection_cpp_type = self._nullable_collection_target_cpp_type(
                name=target_name,
                target_node=node.target if target_name is None else None,
            )
            if selection_cpp_type is None:
                selection_cpp_type = self._udt_target_cpp_type(
                    target_name=target_name,
                    target_node=node.target if target_name is None else None,
                )
            if selection_cpp_type is None:
                selection_cpp_type = self._drawing_target_cpp_type(
                    target_name,
                    None,
                )
            indent = len(pad) // 4
            self._visit_if_switch_expr(
                node.value,
                safe,
                lines,
                indent,
                target_cpp_type=selection_cpp_type,
            )
            return

        # Get target name
        if target_name is None:
            # Assignment to a UDT field that was dropped from the emitted
            # struct because it had a drawing-only type (label/line/box/
            # linefill/polyline/table/chart.point). The struct has no such
            # member, so emit a placeholder comment instead of a real C++
            # assignment. We intentionally do NOT visit the RHS here: drawing
            # constructors (label.new / line.new / ...) live in
            # SKIP_NAMESPACES, so they have no observable side effects in
            # the backtest runtime. See: pineforge-codegen issue #10.
            if self._is_omitted_udt_field(node.target):
                recv = self._visit_expr(node.target.object)
                lines.append(
                    f"{pad}/* drawing field assignment omitted: "
                    f"{recv}.{node.target.member} {node.op} ... */"
                )
                return
            # General expression target (e.g., member access)
            target_cpp = self._visit_mutable_expr(node.target)
            target_cpp_type = self._nullable_collection_target_cpp_type(
                target_node=node.target,
            )
            if target_cpp_type is None:
                target_cpp_type = self._udt_target_cpp_type(
                    target_node=node.target,
                )
            val_cpp = self._visit_rhs_value(
                node.value, target_cpp_type=target_cpp_type
            )
            if node.op == ":=":
                lines.append(f"{pad}{target_cpp} = {val_cpp};")
            else:
                rhs = self._compound_assign_rhs(target_cpp, node.op, val_cpp)
                if rhs is not None:
                    lines.append(f"{pad}{target_cpp} = {rhs};")
                else:
                    lines.append(f"{pad}{target_cpp} {node.op} {val_cpp};")
            return

        safe = self._safe_name(target_name)
        # Apply per-call-site var remap (for function-local vars)
        if self._active_var_remap and safe in self._active_var_remap:
            safe = self._active_var_remap[safe]

        if self._binding_is_series(target_name, safe):
            val_cpp = self._visit_rhs_value(
                node.value,
                target_name,
                target_cpp_type=self._drawing_target_cpp_type(
                    target_name,
                    None,
                ),
            )
            if node.op == ":=":
                lines.append(f"{pad}{safe}.update({val_cpp});")
            else:
                rhs = self._compound_assign_rhs(f"{safe}[0]", node.op, val_cpp)
                if rhs is not None:
                    # x /= y → x.update((double)x[0] / (double)y); x %= y → fmod
                    lines.append(f"{pad}{safe}.update({rhs});")
                else:
                    # Compound assignment: x += y → x.update(x[0] + y)
                    op_char = node.op[0]  # e.g., "+" from "+="
                    lines.append(f"{pad}{safe}.update({safe}[0] {op_char} {val_cpp});")
        elif target_name in self._var_names:
            # A bare-``na`` reassignment must adopt the target's declared scalar
            # type (``x := na`` -> ``na<int>()`` not ``na<double>()``); otherwise
            # a double NaN is stored into an int/int64_t/bool member (UB, defeats
            # is_na<T>()). Only computed for bare na — every other RHS is
            # unaffected.
            tct = self._nullable_collection_target_cpp_type(name=target_name)
            if tct is None:
                tct = self._udt_target_cpp_type(target_name=target_name)
            if tct is None and self._is_na_expr(node.value):
                tct = self._na_reassign_cpp_type(target_name)
            val_cpp = self._visit_rhs_value(node.value, target_name, target_cpp_type=tct)
            if node.op == ":=":
                lines.append(f"{pad}{safe} = {val_cpp};")
            else:
                rhs = self._compound_assign_rhs(safe, node.op, val_cpp)
                if rhs is not None:
                    lines.append(f"{pad}{safe} = {rhs};")
                else:
                    lines.append(f"{pad}{safe} {node.op} {val_cpp};")
        else:
            tct = self._nullable_collection_target_cpp_type(name=target_name)
            if tct is None:
                tct = self._udt_target_cpp_type(target_name=target_name)
            if tct is None and self._is_na_expr(node.value):
                tct = self._na_reassign_cpp_type(target_name)
            val_cpp = self._visit_rhs_value(node.value, target_name, target_cpp_type=tct)
            if node.op == ":=":
                lines.append(f"{pad}{safe} = {val_cpp};")
            else:
                rhs = self._compound_assign_rhs(safe, node.op, val_cpp)
                if rhs is not None:
                    lines.append(f"{pad}{safe} = {rhs};")
                else:
                    lines.append(f"{pad}{safe} {node.op} {val_cpp};")

    def _visit_tuple_assign(self, node: TupleAssign, lines: list[str], pad: str) -> None:
        is_top_level = any(id(node) == id(stmt) for stmt in self.ctx.ast.body)
        global_targets = (
            set(getattr(self.ctx, "ordinary_global_binding_names", set()))
            & self._qualified_func_var_raw_names
            if is_top_level
            else set()
        )
        security_result_fields: list[str] | None = None

        # Exact supported request.security tuples are authoritative Program
        # globals: a later UDF reads their class-member storage, so a local
        # structured binding would shadow the members with fresh values while
        # leaving the UDF-visible state stale. Keep this narrow to security
        # tuples whose analyzer metadata proves a supported helper family, an
        # exact direct literal, or a known TA tuple result. Ordinary
        # non-security heterogeneous tuples retain their established local
        # destructuring behavior.
        if is_top_level and isinstance(node.value, FuncCall):
            func_name, namespace = self._resolve_callee(node.value.callee)
            if namespace == "request" and func_name == "security":
                param_names = [
                    "symbol", "timeframe", "expression", "gaps",
                    "lookahead", "ignore_invalid_symbol", "currency",
                ]
                all_args = list(node.value.args)
                for idx, param_name in enumerate(param_names):
                    if param_name in node.value.kwargs:
                        while len(all_args) <= idx:
                            all_args.append(None)
                        all_args[idx] = node.value.kwargs[param_name]
                expr_node = all_args[2] if len(all_args) > 2 else None
                info = next(
                    (
                        item
                        for item in self._security_calls
                        if not item.get("is_lower_tf_array")
                        and item.get("expr_node") is expr_node
                    ),
                    None,
                )
                if info is not None:
                    tuple_size = info.get("tuple_size", 0)
                    tuple_types = tuple(
                        info.get("tuple_element_types", ()) or ()
                    )
                    numeric_tuple = (
                        tuple_size >= 2
                        and len(tuple_types) == tuple_size
                        and all(
                            item in (PineType.INT, PineType.FLOAT)
                            for item in tuple_types
                        )
                    )
                    bool_tuple = (
                        tuple_size >= 2
                        and len(tuple_types) == tuple_size
                        and all(item == PineType.BOOL for item in tuple_types)
                    )
                    exact_direct_tuple = (
                        isinstance(expr_node, TupleLiteral)
                        and tuple_size >= 2
                        and len(tuple_types) == tuple_size
                        and all(
                            item in (
                                PineType.INT,
                                PineType.FLOAT,
                                PineType.BOOL,
                            )
                            for item in tuple_types
                        )
                    )
                    ta_site = self._get_ta_site(expr_node)
                    known_ta_tuple = (
                        ta_site is not None
                        and self._ta_name_from_site(ta_site) in TA_TUPLE_FIELDS
                    )
                    if known_ta_tuple:
                        security_result_fields = TA_TUPLE_FIELDS[
                            self._ta_name_from_site(ta_site)
                        ]
                    if (
                        numeric_tuple
                        or bool_tuple
                        or exact_direct_tuple
                        or known_ta_tuple
                    ):
                        global_targets.update(
                            name for name in node.names if name != "_"
                        )

        def emit_call_tuple(call_expr: str) -> None:
            """Destructure once, routing exact history elements to Series.

            A structured binding always creates scalar C++ locals, which would
            shadow the class Series storage required by a later ``x[n]`` read.
            It also cannot assign an existing top-level class member. Materialize
            the tuple once whenever either case applies; ordinary lexical scalar
            elements remain locals, Series elements advance their remapped
            members, and top-level scalars assign their class storage.
            """
            series_names = {
                name
                for name in node.names
                if name != "_"
                and self._decl_binding_is_series(id(node), name)
            }
            if not series_names and not global_targets.intersection(node.names):
                binding_names = ", ".join(node.names)
                lines.append(f"{pad}auto [{binding_names}] = {call_expr};")
                return

            temp = f"_tuple_result_{self._tuple_assign_counter}"
            self._tuple_assign_counter += 1
            lines.append(f"{pad}auto {temp} = {call_expr};")
            for idx, name in enumerate(node.names):
                if name == "_":
                    continue
                value = (
                    f"{temp}.{security_result_fields[idx]}"
                    if (
                        security_result_fields is not None
                        and idx < len(security_result_fields)
                    )
                    else f"std::get<{idx}>({temp})"
                )
                safe = self._safe_name(name)
                if name in series_names:
                    if safe in self._active_var_remap:
                        safe = self._active_var_remap[safe]
                    self._emit_history_series_write(
                        lines, pad, safe, value
                    )
                elif name in global_targets:
                    lines.append(f"{pad}{safe} = {value};")
                else:
                    lines.append(f"{pad}auto {safe} = {value};")

        site = self._get_ta_site(node.value)
        if site is not None:
            compute_args = self._ta_compute_args_for_site(site)
            ta_mem = self._ta_member_name(site)
            result_var = f"_result_{ta_mem}"
            lines.append(
                f"{pad}auto {result_var} = (history_advances_new_bar() ? "
                f"{ta_mem}.compute({compute_args}) : "
                f"{ta_mem}.recompute({compute_args}));"
            )

            ta_name = self._ta_name_from_site(site)
            fields = TA_TUPLE_FIELDS.get(ta_name, [f"field{i}" for i in range(len(node.names))])

            for i, name in enumerate(node.names):
                if name == "_":
                    continue
                if i < len(fields):
                    field_expr = f"{result_var}.{fields[i]}"
                    # A history-referenced destructured name (e.g.
                    # ``[v, dir] = ta.supertrend(...)`` with ``dir[1]`` used
                    # later) is tracked in ``series_vars`` and declared as a
                    # ``Series<T>`` class member. Pushing into that member keeps
                    # its history buffer advancing so ``dir[n]`` resolves; a
                    # fresh ``double`` local would shadow the member and make
                    # ``dir[n]`` a scalar subscript (clang error). Non-series
                    # destructured names keep the plain scalar declaration.
                    if self._decl_binding_is_series(id(node), name):
                        safe = self._safe_name(name)
                        if safe in self._active_var_remap:
                            safe = self._active_var_remap[safe]
                        self._emit_history_series_write(
                            lines, pad, safe, field_expr
                        )
                    elif name in global_targets:
                        lines.append(
                            f"{pad}{self._safe_name(name)} = {field_expr};"
                        )
                    else:
                        lines.append(f"{pad}double {name} = {field_expr};")
            return

        # User-defined function returning a tuple: use C++17 structured bindings
        if isinstance(node.value, FuncCall):
            func_name, namespace = self._resolve_callee(node.value.callee)
            if namespace == "request" and func_name == "security":
                call_expr = self._visit_func_call(node.value)
                emit_call_tuple(call_expr)
                return
            if func_name and namespace is None and func_name in self._func_names:
                call_expr = self._visit_func_call(node.value)
                emit_call_tuple(call_expr)
                return

            # UDT instance method returning a tuple: ``[a, b, c] = receiver.method(...)``.
            # The plain-function branch above misses this because _resolve_callee
            # returns ``("method", "receiver")`` for ``recv.method(...)``, not
            # ``(key, None)``. We resolve the receiver's UDT type and look up
            # the method-key ``TypeName.methodName`` in the FuncInfo map; when
            # its FuncInfo carries ``returns_tuple=True`` we know
            # ``_visit_func_call`` has already lowered the call as
            # ``_udt_TypeName_method(receiver, ...)`` returning
            # ``std::tuple<...>``, so structured bindings drop in.
            # Probe: data/validation/udt-method-probe-17-tuple-return-destructure.
            callee = node.value.callee
            if isinstance(callee, MemberAccess):
                recv_spec = self._type_spec_from_expr(callee.object)
                receiver_name = method_receiver_type_name(recv_spec)
                if receiver_name is not None:
                    method_key = f"{receiver_name}.{callee.member}"
                    fi_u = self._func_info_map.get(method_key)
                    if (fi_u is not None
                            and getattr(fi_u, "is_udt_method", False)
                            and getattr(fi_u, "returns_tuple", False)):
                        call_expr = self._visit_func_call(node.value)
                        emit_call_tuple(call_expr)
                        return

        lines.append(f"{pad}/* unsupported tuple assignment */")

    def _tuple_binding_cpp_types(self, node: TupleAssign) -> list[str]:
        """Exact supported tuple element types for later lexical operations."""
        count = len(node.names)
        if not isinstance(node.value, FuncCall):
            return ["double"] * count
        func_name, namespace = self._resolve_callee(node.value.callee)
        fi = None
        if namespace is None:
            fi = self._func_info_map.get(func_name)
        elif isinstance(node.value.callee, MemberAccess):
            recv_spec = self._type_spec_from_expr(node.value.callee.object)
            receiver_name = method_receiver_type_name(recv_spec)
            if receiver_name is not None:
                fi = self._func_info_map.get(
                    f"{receiver_name}.{node.value.callee.member}"
                )
        if (fi is not None
                and fi.node is not None
                and getattr(fi, "returns_tuple", False)):
            return self._infer_tuple_types(fi.node, count)
        return ["double"] * count

    def _push_block_var_remap(self, owner):
        """Activate exact lexical metadata for one branch/loop body.

        Persistent-var renames use copy-on-write and activate only after their
        declarations. Collection registries likewise use copy-on-write and
        declarations activate their bindings in
        source order, and block exit restores the outer state. Thus a sibling
        block can reuse the same raw name without either pre-shadowing earlier
        statements or controlling dispatch in the other branch.
        """
        previous_map_visible = getattr(
            self, "_block_map_binding_visible", False
        )
        previous_map_depth = getattr(self, "_block_map_visibility_depth", 0)
        self._block_map_visibility_depth = previous_map_depth + 1
        saved_drawing_types = self._lexical_drawing_types
        self._lexical_drawing_types = dict(saved_drawing_types)
        saved_udt_types = self._lexical_udt_types
        self._lexical_udt_types = dict(saved_udt_types)
        saved_series_bindings = self._lexical_series_bindings
        self._lexical_series_bindings = dict(saved_series_bindings)
        saved_known_tombstones = self._lexical_known_var_tombstones
        self._lexical_known_var_tombstones = set(
            saved_known_tombstones
        )

        renames = self._block_var_renames.get(id(owner))
        collection_specs = self._block_collection_types.get(id(owner))
        if not renames and collection_specs is None:
            return (
                _NO_BLOCK_REMAP,
                None,
                previous_map_visible,
                previous_map_depth,
                saved_drawing_types,
                saved_udt_types,
                saved_series_bindings,
                saved_known_tombstones,
            )
        saved_remap = self._active_var_remap
        if renames:
            # Do not pre-shadow an outer binding at block entry.  _visit_stmt
            # installs each exact rename immediately after that VarDecl's RHS.
            self._active_var_remap = dict(saved_remap)

        saved_collections = None
        if collection_specs is not None:
            saved_collections = (
                self._current_func_collection_specs,
                self._current_func_collection_shadows,
                self._collection_types,
                self._array_vars,
                self._map_vars,
                self._matrix_specs,
            )
            self._current_func_collection_specs = dict(
                self._current_func_collection_specs
            )
            self._current_func_collection_shadows = set(
                self._current_func_collection_shadows
            )
            self._collection_types = dict(self._collection_types)
            self._array_vars = set(self._array_vars)
            self._map_vars = set(self._map_vars)
            self._matrix_specs = dict(self._matrix_specs)
        return (
            saved_remap,
            saved_collections,
            previous_map_visible,
            previous_map_depth,
            saved_drawing_types,
            saved_udt_types,
            saved_series_bindings,
            saved_known_tombstones,
        )

    def _pop_block_var_remap(self, saved) -> None:
        (
            saved_remap,
            saved_collections,
            previous_map_visible,
            previous_map_depth,
            saved_drawing_types,
            saved_udt_types,
            saved_series_bindings,
            saved_known_tombstones,
        ) = saved
        try:
            if saved_remap is not _NO_BLOCK_REMAP:
                self._active_var_remap = saved_remap
                if saved_collections is not None:
                    (
                        self._current_func_collection_specs,
                        self._current_func_collection_shadows,
                        self._collection_types,
                        self._array_vars,
                        self._map_vars,
                        self._matrix_specs,
                    ) = saved_collections
        finally:
            self._lexical_drawing_types = saved_drawing_types
            self._lexical_udt_types = saved_udt_types
            self._lexical_series_bindings = saved_series_bindings
            self._lexical_known_var_tombstones = saved_known_tombstones
            self._block_map_binding_visible = previous_map_visible
            self._block_map_visibility_depth = previous_map_depth

    def _visit_block_statements(self, body: list, lines: list[str],
                                indent: int) -> None:
        """Emit one lexical branch/loop body with its exact var remap."""
        saved = self._push_block_var_remap(body)
        try:
            for stmt in body:
                self._visit_stmt(stmt, lines, indent)
        finally:
            self._pop_block_var_remap(saved)

    def _emit_block_with_assign(
        self,
        body: list,
        target: str,
        lines: list[str],
        indent: int,
        target_cpp_type: str | None = None,
    ) -> None:
        """Expression-body counterpart of :meth:`_visit_block_statements`."""
        saved = self._push_block_var_remap(body)
        try:
            self._emit_body_with_assign(
                body,
                target,
                lines,
                indent,
                target_cpp_type=target_cpp_type,
            )
        finally:
            self._pop_block_var_remap(saved)

    def _visit_if(self, node: IfStmt, lines: list[str], indent: int) -> None:
        self._visit_if_body(node, lines, indent)

    def _visit_if_body(self, node: IfStmt, lines: list[str], indent: int) -> None:
        pad = "    " * indent

        cond = self._visit_expr(node.condition)
        lines.append(f"{pad}if ({cond}) {{")
        self._visit_block_statements(node.body, lines, indent + 1)
        lines.append(f"{pad}}}")
        if node.else_body:
            if len(node.else_body) == 1 and isinstance(node.else_body[0], IfStmt):
                lines[-1] = f"{pad}}} else"
                self._visit_if(node.else_body[0], lines, indent)
            else:
                lines[-1] = f"{pad}}} else {{"
                self._visit_block_statements(
                    node.else_body, lines, indent + 1
                )
                lines.append(f"{pad}}}")

    def _visit_for(self, node: ForStmt, lines: list[str], indent: int) -> None:
        pad = "    " * indent
        start = self._visit_expr(node.start)
        end = self._visit_expr(node.end)
        step = self._visit_expr(node.step) if node.step is not None else "1"
        var = self._safe_name(node.var)  # new AST uses .var instead of .var_name

        # Pine infers loop direction from the initial ``from``/``to`` values for
        # both implicit and explicit ``by`` loops. The ``by`` value is a positive
        # magnitude; descending loops subtract it. ``to`` can change during the
        # loop, so refresh the cached end expression after each iteration while
        # keeping the initial direction and step fixed.
        fid = self._for_counter
        self._for_counter += 1
        s_var = f"_for_start_{fid}"
        e_var = f"_for_end_{fid}"
        step_var = f"_for_step_{fid}"
        down_var = f"_for_down_{fid}"
        end_mentions_binder = bool(
            node.var
            and any(
                isinstance(part, Identifier) and part.name == node.var
                for part in self._walk_ast(node.end)
            )
        )
        end_eval = f"_for_end_eval_{fid}" if end_mentions_binder else None
        if end_eval is not None:
            # The ``to`` expression is authored outside the loop-binder scope,
            # but its refresh executes inside the generated C++ ``for`` where
            # the binder would shadow a same-named outer member/parameter. A
            # pre-binder lambda preserves the authored lexical binding while
            # still reevaluating the expression after every iteration.
            lines.append(f"{pad}auto {end_eval} = [&]() {{ return ({end}); }};")
        lines.append(f"{pad}int {s_var} = ({start});")
        end_expr = f"{end_eval}()" if end_eval is not None else f"({end})"
        lines.append(f"{pad}int {e_var} = {end_expr};")
        lines.append(f"{pad}int {step_var} = ({step});")
        lines.append(f"{pad}if ({step_var} < 0) {step_var} = -{step_var};")
        lines.append(f"{pad}if ({step_var} == 0) {step_var} = 1;")
        lines.append(f"{pad}const bool {down_var} = ({s_var} > {e_var});")
        lines.append(
            f"{pad}for (int {var} = {s_var}; "
            f"({down_var} ? ({var} >= {e_var}) : ({var} <= {e_var})); "
            f"{var} += ({down_var} ? -{step_var} : {step_var}), "
            f"{e_var} = {end_expr}) {{"
        )
        # Register the loop counter so reads of it inside the body resolve (the
        # unknown-identifier guard in _visit_ident would otherwise flag it).
        saved_loop = self._current_loop_vars
        saved_loop_specs = self._current_loop_var_specs
        self._current_loop_vars = set(self._current_loop_vars)
        self._current_loop_var_specs = dict(self._current_loop_var_specs)
        if node.var:
            self._current_loop_vars.add(node.var)
            self._current_loop_var_specs[node.var] = TypeSpec.primitive("int")
        _blk_saved = self._push_block_var_remap(node)
        if node.var:
            # The loop counter is a fresh primitive lexical binding.  Keep it
            # from inheriting a same-spelled outer/raw UDT or drawing type.
            self._lexical_drawing_types[node.var] = None
            self._lexical_udt_types[node.var] = None
            self._lexical_series_bindings[node.var] = False
            self._lexical_known_var_tombstones.add(node.var)
        try:
            for s in node.body:
                self._visit_stmt(s, lines, indent + 1)
        finally:
            self._pop_block_var_remap(_blk_saved)
        self._current_loop_vars = saved_loop
        self._current_loop_var_specs = saved_loop_specs
        lines.append(f"{pad}}}")

    def _visit_for_in(self, node, lines: list[str], indent: int) -> None:
        pad = "    " * indent
        iterable = self._visit_expr(node.iterable)
        saved_loop = self._current_loop_vars
        saved_loop_specs = self._current_loop_var_specs
        self._current_loop_vars = set(self._current_loop_vars)
        self._current_loop_var_specs = dict(self._current_loop_var_specs)
        iterable_spec = self._type_spec_from_expr(node.iterable)
        elem_spec = (
            iterable_spec.element
            if iterable_spec is not None and iterable_spec.kind == "array"
            else None
        )
        if node.var:
            self._current_loop_vars.add(node.var)
            if elem_spec is not None:
                self._current_loop_var_specs[node.var] = elem_spec
        if node.vars:
            tuple_specs: list[TypeSpec | None] = []
            if iterable_spec is not None and iterable_spec.kind == "map":
                tuple_specs = [iterable_spec.key, iterable_spec.value]
            for idx, v in enumerate(node.vars):
                if v != "_":
                    self._current_loop_vars.add(v)
                    if (idx < len(tuple_specs)
                            and tuple_specs[idx] is not None):
                        self._current_loop_var_specs[v] = tuple_specs[idx]
        map_pair_loop = (
            iterable_spec is not None
            and iterable_spec.kind == "map"
            and node.vars is not None
            and len(node.vars) == 2
        )
        if node.var:
            v_cpp = self._safe_name(node.var)
            # User UDT elements are numeric object-ID handles.  Copying the
            # loop binding preserves Pine semantics: field writes still reach
            # the shared arena record, while rebinding the loop variable does
            # not overwrite the array slot.  Primitive elements use the same
            # value-binding rule.
            lines.append(f"{pad}for (auto {v_cpp} : {iterable}) {{")
        elif map_pair_loop:
            # Pine map iteration exposes insertion-ordered ``[key, value]``
            # pairs. PineMap intentionally keeps its storage private, so take
            # one handle copy (which aliases the same map ID), iterate keys(),
            # and obtain each value through the public API. Binding the
            # iterable once also preserves single-evaluation semantics for an
            # arbitrary map-producing expression.
            key_name, value_name = node.vars
            authored_names = (
                set(self._all_bound_names)
                | set(self._func_names)
                | set(self._udt_defs)
            )
            occupied_names = authored_names | {
                self._safe_name(name) for name in authored_names
            } | {
                self._func_safe_name(name) for name in self._func_names
            }
            # Parameters are not statement bindings, so they are absent from
            # ``_all_bound_names``.  Include both their authored and C++-safe
            # spellings before minting loop temporaries; otherwise a legal UDF
            # or method parameter named ``__pf_map_iter_0`` (or, for ``_`` key
            # loops, ``__pf_map_key_0``) is redeclared and then shadowed by the
            # generated loop machinery.
            active_params = set(self._current_func_param_types)
            occupied_names.update(active_params)
            occupied_names.update(
                self._safe_name(name) for name in active_params
            )
            while True:
                fid = self._for_counter
                self._for_counter += 1
                map_token = f"__pf_map_iter_{fid}"
                internal_key = f"__pf_map_key_{fid}"
                generated_names = {map_token}
                if key_name == "_":
                    generated_names.add(internal_key)
                if not (generated_names & occupied_names):
                    break
            key_cpp = (
                self._safe_name(key_name)
                if key_name != "_"
                else internal_key
            )
            lines.append(f"{pad}auto {map_token} = {iterable};")
            lines.append(f"{pad}for (auto {key_cpp} : {map_token}.keys()) {{")
            if value_name != "_":
                value_cpp = self._safe_name(value_name)
                lines.append(
                    f"{pad}    auto {value_cpp} = {map_token}.get({key_cpp});"
                )
        elif node.vars:
            bindings = ", ".join(node.vars)
            lines.append(f"{pad}for (auto [{bindings}] : {iterable}) {{")
        _blk_saved = self._push_block_var_remap(node)
        loop_binding_names = (
            [node.var] if node.var else list(node.vars or [])
        )
        loop_binding_specs = (
            [elem_spec]
            if node.var
            else [
                tuple_specs[index] if index < len(tuple_specs) else None
                for index in range(len(loop_binding_names))
            ]
        )
        for index, name in enumerate(loop_binding_names):
            if name and name != "_":
                spec = (
                    loop_binding_specs[index]
                    if index < len(loop_binding_specs)
                    else None
                )
                drawing_name = (
                    spec.name
                    if (spec is not None
                        and spec.kind == "udt"
                        and spec.name in DRAWING_TYPE_TO_CPP)
                    else None
                )
                self._lexical_drawing_types[name] = (
                    DRAWING_TYPE_TO_CPP[drawing_name]
                    if drawing_name is not None
                    else None
                )
                self._lexical_udt_types[name] = (
                    spec.name
                    if (spec is not None
                        and spec.kind == "udt"
                        and spec.name in self._udt_defs)
                    else None
                )
                self._lexical_series_bindings[name] = False
                self._lexical_known_var_tombstones.add(name)
        try:
            for s in node.body:
                self._visit_stmt(s, lines, indent + 1)
        finally:
            self._pop_block_var_remap(_blk_saved)
        lines.append(f"{pad}}}")
        self._current_loop_vars = saved_loop
        self._current_loop_var_specs = saved_loop_specs

    def _visit_while(self, node: WhileStmt, lines: list[str], indent: int) -> None:
        pad = "    " * indent
        cond = self._visit_expr(node.condition)
        lines.append(f"{pad}while ({cond}) {{")
        _blk_saved = self._push_block_var_remap(node)
        try:
            for s in node.body:
                self._visit_stmt(s, lines, indent + 1)
        finally:
            self._pop_block_var_remap(_blk_saved)
        lines.append(f"{pad}}}")

    def _visit_switch(self, node: SwitchStmt, lines: list[str], indent: int) -> None:
        pad = "    " * indent
        if node.expr:
            expr_var = f"__switch_val_{self._switch_counter}"
            self._switch_counter += 1
            lines.append(f"{pad}auto {expr_var} = {self._visit_expr(node.expr)};")
            for i, (case_expr, case_body) in enumerate(node.cases):
                prefix = "if" if i == 0 else "else if"
                case_val = self._visit_expr(case_expr)
                lines.append(f"{pad}{prefix} ({expr_var} == {case_val}) {{")
                self._visit_block_statements(case_body, lines, indent + 1)
                lines.append(f"{pad}}}")
        else:
            for i, (case_expr, case_body) in enumerate(node.cases):
                prefix = "if" if i == 0 else "else if"
                cond = self._visit_expr(case_expr)
                lines.append(f"{pad}{prefix} ({cond}) {{")
                self._visit_block_statements(case_body, lines, indent + 1)
                lines.append(f"{pad}}}")

        if node.default_body:
            lines.append(f"{pad}else {{")
            self._visit_block_statements(
                node.default_body, lines, indent + 1
            )
            lines.append(f"{pad}}}")

    # ------------------------------------------------------------------
    # If/switch expression helpers
    # ------------------------------------------------------------------

    # _default_for_type lives on TypeInferer — see codegen/types.py.

    def _emit_body_with_assign(
        self,
        body: list,
        target: str,
        lines: list[str],
        indent: int,
        target_cpp_type: str | None = None,
    ) -> None:
        """Emit a body block where the last expression becomes an assignment."""
        if not body:
            return
        for i, stmt in enumerate(body):
            if i == len(body) - 1:
                # Last statement — try to turn into assignment
                if isinstance(stmt, ExprStmt):
                    # Check if it's a skip expr
                    if self._is_skip_expr(stmt.expr):
                        return
                    # A void drawing setter / delete / visual-noop cannot be the
                    # branch's value (it lowers to a void C++ call) — emit it as
                    # a statement and leave ``target`` at its default.
                    if self._call_is_void(stmt.expr):
                        self._visit_stmt(stmt, lines, indent)
                        return
                    cpp = self._visit_rhs_value(
                        stmt.expr,
                        target_cpp_type=target_cpp_type,
                    )
                    pad = "    " * indent
                    lines.append(f"{pad}{target} = {cpp};")
                elif isinstance(stmt, IfStmt):
                    # Nested if expression
                    self._visit_if_switch_expr(
                        stmt,
                        target,
                        lines,
                        indent,
                        target_cpp_type=target_cpp_type,
                    )
                elif isinstance(stmt, SwitchStmt):
                    self._visit_if_switch_expr(
                        stmt,
                        target,
                        lines,
                        indent,
                        target_cpp_type=target_cpp_type,
                    )
                else:
                    self._visit_stmt(stmt, lines, indent)
            else:
                self._visit_stmt(stmt, lines, indent)

    def _visit_if_switch_expr(
        self,
        node,
        target: str,
        lines: list[str],
        indent: int,
        target_cpp_type: str | None = None,
    ) -> None:
        """Emit an if/switch used as an expression, assigning to target."""
        pad = "    " * indent
        nullable_collection_target = self._is_nullable_collection_cpp_type(
            target_cpp_type
        )

        def emit_implicit_na_fallback() -> None:
            """Reset a nullable ID when an expression has no matching arm."""
            lines.append(f"{pad}else {{")
            lines.append(
                f"{pad}    {target} = {target_cpp_type}{{}};"
            )
            lines.append(f"{pad}}}")

        if isinstance(node, IfStmt):
            cond = self._visit_expr(node.condition)
            lines.append(f"{pad}if ({cond}) {{")
            self._emit_block_with_assign(
                node.body,
                target,
                lines,
                indent + 1,
                target_cpp_type=target_cpp_type,
            )
            lines.append(f"{pad}}}")
            if node.else_body:
                if len(node.else_body) == 1 and isinstance(node.else_body[0], IfStmt):
                    lines[-1] = f"{pad}}} else"
                    self._visit_if_switch_expr(
                        node.else_body[0],
                        target,
                        lines,
                        indent,
                        target_cpp_type=target_cpp_type,
                    )
                else:
                    lines[-1] = f"{pad}}} else {{"
                    self._emit_block_with_assign(
                        node.else_body,
                        target,
                        lines,
                        indent + 1,
                        target_cpp_type=target_cpp_type,
                    )
                    lines.append(f"{pad}}}")
            elif nullable_collection_target:
                # A Pine if-expression without an else evaluates to ``na`` on
                # the unmatched path.  This assignment is essential for
                # non-var globals and reassignments: retaining the prior bar's
                # map/matrix ID would turn the expression into implicit state.
                emit_implicit_na_fallback()
        elif isinstance(node, SwitchStmt):
            if node.expr:
                expr_var = f"__switch_val_{self._switch_counter}"
                self._switch_counter += 1
                lines.append(f"{pad}auto {expr_var} = {self._visit_expr(node.expr)};")
                for i, (case_expr, case_body) in enumerate(node.cases):
                    prefix = "if" if i == 0 else "else if"
                    case_val = self._visit_expr(case_expr)
                    lines.append(f"{pad}{prefix} ({expr_var} == {case_val}) {{")
                    self._emit_block_with_assign(
                        case_body,
                        target,
                        lines,
                        indent + 1,
                        target_cpp_type=target_cpp_type,
                    )
                    lines.append(f"{pad}}}")
            else:
                for i, (case_expr, case_body) in enumerate(node.cases):
                    prefix = "if" if i == 0 else "else if"
                    cond = self._visit_expr(case_expr)
                    lines.append(f"{pad}{prefix} ({cond}) {{")
                    self._emit_block_with_assign(
                        case_body,
                        target,
                        lines,
                        indent + 1,
                        target_cpp_type=target_cpp_type,
                    )
                    lines.append(f"{pad}}}")
            if node.default_body:
                if node.cases:
                    lines.append(f"{pad}else {{")
                    self._emit_block_with_assign(
                        node.default_body,
                        target,
                        lines,
                        indent + 1,
                        target_cpp_type=target_cpp_type,
                    )
                    lines.append(f"{pad}}}")
                else:
                    self._emit_block_with_assign(
                        node.default_body,
                        target,
                        lines,
                        indent,
                        target_cpp_type=target_cpp_type,
                    )
            elif nullable_collection_target:
                if node.cases:
                    emit_implicit_na_fallback()
                else:
                    lines.append(
                        f"{pad}{target} = {target_cpp_type}{{}};"
                    )
