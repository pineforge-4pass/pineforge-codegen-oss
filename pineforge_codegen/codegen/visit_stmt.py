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
- ``self._in_ta_func_variant`` (``bool``): set during per-call-site
  function emission; gates the TA-hoist branch in ``_visit_if``.
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
  ``_ta_name_from_site``, ``_if_body_has_ta``, ``_hoist_if_body``.
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
    TupleAssign,
    TypeDecl,
    VarDecl,
    WhileStmt,
)
from ..symbols import TypeSpec
from .tables import (
    ARRAY_NEW_CTORS,
    TA_RETURNS_BOOL,
    TA_TUPLE_FIELDS,
    MATRIX_RETURNING_METHODS,
)

# Sentinel for "no block-scoped var remap was activated" so an empty dict
# saved-remap is still distinguishable from the no-op case.
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
            self._visit_var_decl(node, lines, pad)
        elif isinstance(node, Assignment):
            self._visit_assignment(node, lines, pad)
        elif isinstance(node, TupleAssign):
            self._visit_tuple_assign(node, lines, pad)
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
            if recv in getattr(self, "_matrix_specs", {}):
                return recv
        # matrix.concat(m, other, ...) — receiver is first arg
        if (isinstance(callee.object, Identifier)
                and callee.object.name == "matrix"
                and expr.args
                and isinstance(expr.args[0], Identifier)):
            recv = expr.args[0].name
            if recv in getattr(self, "_matrix_specs", {}):
                return recv
        return None

    def _visit_var_decl(self, node: VarDecl, lines: list[str], pad: str) -> None:
        # var/varip — handled as members in on_bar preamble
        if node.is_var or node.is_varip:
            return

        safe = self._safe_name(node.name)
        # Apply per-call-site var remap (for function-local vars)
        if self._active_var_remap and safe in self._active_var_remap:
            safe = self._active_var_remap[safe]
        # Global-scope non-var vars are class members — emit assignment, not declaration
        is_global_member = node.name in self._global_member_vars

        def remember_local_type(cpp_type: str | None) -> None:
            if cpp_type and not is_global_member and node.name in self._current_func_locals:
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
            if node.name in self.ctx.series_vars:
                lines.append(f"{pad}{safe}.push({cpp_val});")
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
                self._array_vars.add(node.name)
                spec = self._type_spec_from_expr(node.value) or self._array_spec_for_name(node.name)
                self._collection_types.setdefault(node.name, spec)
                init = self._visit_expr(node.value)
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
                spec = TypeSpec.matrix(elem_spec)
                self._matrix_specs[node.name] = spec
                self._collection_types[node.name] = spec
                cpp_type = self._type_spec_to_cpp(spec)
                if len(node.value.args) >= 2:
                    r = self._visit_expr(node.value.args[0])
                    c = self._visit_expr(node.value.args[1])
                    v = self._visit_expr(node.value.args[2]) if len(node.value.args) > 2 else self._default_for_spec(elem_spec)
                    init = f"{cpp_type}::new_({r}, {c}, {v})"
                else:
                    init = f"{cpp_type}::new_(0, 0, {self._default_for_spec(elem_spec)})"
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
                recv_spec = self._matrix_specs.get(self._extract_receiver_name(node.value))
                if recv_spec is None:
                    recv_spec = TypeSpec.matrix(TypeSpec.primitive("float"))
                self._matrix_specs[node.name] = recv_spec
                self._collection_types[node.name] = recv_spec
                init = self._visit_expr(node.value)
                cpp_type = self._type_spec_to_cpp(recv_spec)
                if is_global_member:
                    lines.append(f"{pad}{safe} = {init};")
                else:
                    lines.append(f"{pad}{cpp_type} {safe} = {init};")
                return
            if namespace == "map" and func_name == "new":
                self._map_vars.add(node.name)
                spec = self._type_spec_from_expr(node.value) or self._map_spec_for_name(node.name)
                self._collection_types.setdefault(node.name, spec)
                cpp_type = self._type_spec_to_cpp(spec)
                if is_global_member:
                    lines.append(f"{pad}{safe} = {cpp_type}();")
                else:
                    lines.append(f"{pad}{cpp_type} {safe};")
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
            ta_expr = f"(is_first_tick_ ? {ta_name}.compute({compute_args}) : {ta_name}.recompute({compute_args}))"
            if node.name in self.ctx.series_vars:
                lines.append(f"{pad}{safe}.push({ta_expr});")
            elif is_global_member:
                lines.append(f"{pad}{safe} = {ta_expr};")
            else:
                lines.append(f"{pad}{ret_type} {safe} = {ta_expr};")
            return

        # Non-var series variable — push instead of declare
        if node.name in self.ctx.series_vars:
            cpp_val = self._visit_expr(node.value)
            lines.append(f"{pad}{safe}.push({cpp_val});")
            return

        # If/switch expression: x = if cond ... else ...
        if isinstance(node.value, (IfStmt, SwitchStmt)):
            if not is_global_member:
                cpp_type = self._type_for_decl(node)
                default = self._default_for_type(cpp_type)
                lines.append(f"{pad}{cpp_type} {safe} = {default};")
            indent = len(pad) // 4
            self._visit_if_switch_expr(node.value, safe, lines, indent)
            return

        # UDT lvalue alias (BUG C): a local initialised from a user-defined-UDT
        # var/global lvalue (or a ternary/switch of such lvalues) and then
        # mutated through must ALIAS the global, not value-copy — Pine UDTs are
        # reference types. Emit a C++ reference (non-rebinding) or pointer
        # (rebinding) alias instead of the default copy.
        if not is_global_member:
            alias = self._udt_local_alias_kind(node)
            if alias is not None:
                kind, udt_t = alias
                if kind == "ref":
                    cpp_val = self._visit_rhs_value(node.value, node.name, target_cpp_type=udt_t)
                    lines.append(f"{pad}{udt_t}& {safe} = {cpp_val};")
                    return
                # Pointer alias: take address of each selected lvalue; subsequent
                # field access lowers to ``->`` and rebinds to ``&(other)``.
                self._udt_ptr_alias_locals.add(node.name)
                cpp_val = self._addr_of_udt_selection(node.value, node.name)
                lines.append(f"{pad}{udt_t}* {safe} = {cpp_val};")
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
                # Register the local's collection kind so subsequent
                # ``.size()/.get()/.unshift()`` dispatch resolves correctly.
                self._collection_types[node.name] = coll_spec
                if coll_spec.kind == "array":
                    self._array_vars.add(node.name)
                elif coll_spec.kind == "map":
                    self._map_vars.add(node.name)
                elif coll_spec.kind == "matrix":
                    self._matrix_specs[node.name] = coll_spec
                cpp_type = self._type_spec_to_cpp(coll_spec)
                cpp_val = self._visit_rhs_value(node.value, node.name, target_cpp_type=cpp_type)
                lines.append(f"{pad}{cpp_type}& {safe} = {cpp_val};")
                return

        # General declaration
        cpp_type = self._type_for_decl(node) if not is_global_member else None
        cpp_val = self._visit_rhs_value(node.value, node.name, target_cpp_type=cpp_type)
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

    def _visit_assignment(self, node: Assignment, lines: list[str], pad: str) -> None:
        if isinstance(node.value, FuncCall) and self._is_skip_expr(node.value):
            return

        # If/switch expression in assignment: x := if cond ...
        if isinstance(node.value, (IfStmt, SwitchStmt)):
            target_name = self._get_target_name(node.target)
            safe = self._safe_name(target_name) if target_name else self._visit_expr(node.target)
            indent = len(pad) // 4
            self._visit_if_switch_expr(node.value, safe, lines, indent)
            return

        # Get target name
        target_name = self._get_target_name(node.target)
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
            target_cpp = self._visit_expr(node.target)
            val_cpp = self._visit_expr(node.value)
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

        # Pointer-aliased UDT local (BUG C, rebinding case): ``p := other``
        # rebinds the pointer to the address of the newly selected UDT lvalue.
        if target_name in self._udt_ptr_alias_locals and node.op == ":=":
            lines.append(f"{pad}{safe} = {self._addr_of_udt_selection(node.value, target_name)};")
            return

        if target_name in self.ctx.series_vars:
            val_cpp = self._visit_expr(node.value)
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
            if node.op == ":=" and target_name in self._matrix_specs and isinstance(node.value, FuncCall):
                rhs_fn, rhs_ns = self._resolve_callee(node.value.callee)
                rhs_spec = None
                if rhs_ns == "matrix" and rhs_fn == "new":
                    targs = self._template_args_from_call(node.value) if hasattr(node.value, "annotations") else []
                    elem = self._type_spec_from_hint_name(targs[0]) if targs else TypeSpec.primitive("float")
                    rhs_spec = TypeSpec.matrix(elem)
                elif rhs_ns == "matrix" and rhs_fn in MATRIX_RETURNING_METHODS:
                    rcv = self._extract_receiver_name(node.value)
                    rhs_spec = self._matrix_specs.get(rcv)
                if rhs_spec is not None:
                    lhs_spec = self._matrix_specs[target_name]
                    if rhs_spec.element != lhs_spec.element:
                        self._codegen_error(
                            node,
                            f"matrix '{target_name}' element type mismatch on reassignment: "
                            f"expected {self._type_spec_to_cpp(lhs_spec)}, "
                            f"got {self._type_spec_to_cpp(rhs_spec)}",
                        )
            val_cpp = self._visit_rhs_value(node.value, target_name)
            if node.op == ":=":
                lines.append(f"{pad}{safe} = {val_cpp};")
            else:
                rhs = self._compound_assign_rhs(safe, node.op, val_cpp)
                if rhs is not None:
                    lines.append(f"{pad}{safe} = {rhs};")
                else:
                    lines.append(f"{pad}{safe} {node.op} {val_cpp};")
        else:
            val_cpp = self._visit_rhs_value(node.value, target_name)
            if node.op == ":=":
                lines.append(f"{pad}{safe} = {val_cpp};")
            else:
                rhs = self._compound_assign_rhs(safe, node.op, val_cpp)
                if rhs is not None:
                    lines.append(f"{pad}{safe} = {rhs};")
                else:
                    lines.append(f"{pad}{safe} {node.op} {val_cpp};")

    def _visit_tuple_assign(self, node: TupleAssign, lines: list[str], pad: str) -> None:
        site = self._get_ta_site(node.value)
        if site is not None:
            compute_args = self._ta_compute_args_for_site(site)
            ta_mem = self._ta_member_name(site)
            result_var = f"_result_{ta_mem}"
            lines.append(f"{pad}auto {result_var} = (is_first_tick_ ? {ta_mem}.compute({compute_args}) : {ta_mem}.recompute({compute_args}));")

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
                    if name in self.ctx.series_vars:
                        safe = self._safe_name(name)
                        lines.append(f"{pad}{safe}.push({field_expr});")
                    else:
                        lines.append(f"{pad}double {name} = {field_expr};")
            return

        # User-defined function returning a tuple: use C++17 structured bindings
        if isinstance(node.value, FuncCall):
            func_name, namespace = self._resolve_callee(node.value.callee)
            if namespace == "request" and func_name == "security":
                binding_names = ", ".join(n for n in node.names if n != "_")
                call_expr = self._visit_func_call(node.value)
                lines.append(f"{pad}auto [{binding_names}] = {call_expr};")
                return
            if func_name and namespace is None and func_name in self._func_names:
                binding_names = ", ".join(node.names)
                call_expr = self._visit_func_call(node.value)
                lines.append(f"{pad}auto [{binding_names}] = {call_expr};")
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
                if recv_spec is not None and recv_spec.kind == "udt" and recv_spec.name:
                    method_key = f"{recv_spec.name}.{callee.member}"
                    fi_u = self._func_info_map.get(method_key)
                    if (fi_u is not None
                            and getattr(fi_u, "is_udt_method", False)
                            and getattr(fi_u, "returns_tuple", False)):
                        binding_names = ", ".join(node.names)
                        call_expr = self._visit_func_call(node.value)
                        lines.append(f"{pad}auto [{binding_names}] = {call_expr};")
                        return

        lines.append(f"{pad}/* unsupported tuple assignment */")

    def _push_block_var_remap(self, node):
        """Activate block-scoped var renames for ``node`` (BUG 1). Returns the
        previous ``_active_var_remap`` to restore (or ``_NO_BLOCK_REMAP`` if this
        block owns no renames). Renames are MERGED over the inherited remap so
        nested blocks keep any enclosing func-clone / outer-block mapping."""
        renames = self._block_var_renames.get(id(node))
        if not renames:
            return _NO_BLOCK_REMAP
        saved = self._active_var_remap
        self._active_var_remap = {**saved, **renames}
        return saved

    def _pop_block_var_remap(self, saved) -> None:
        if saved is not _NO_BLOCK_REMAP:
            self._active_var_remap = saved

    def _visit_if(self, node: IfStmt, lines: list[str], indent: int) -> None:
        _blk_saved = self._push_block_var_remap(node)
        try:
            self._visit_if_body(node, lines, indent)
        finally:
            self._pop_block_var_remap(_blk_saved)

    def _visit_if_body(self, node: IfStmt, lines: list[str], indent: int) -> None:
        pad = "    " * indent

        # TA hoisting: inside per-call-site function variants, execute ALL
        # statements unconditionally (PineScript execution model) but wrap
        # the result assignment in the condition.
        if self._in_ta_func_variant and self._if_body_has_ta(node.body):
            cond = self._visit_expr(node.condition)
            self._hoist_if_body(node.body, cond, lines, pad, indent)
            # Handle else_body similarly
            if node.else_body:
                if len(node.else_body) == 1 and isinstance(node.else_body[0], IfStmt):
                    self._visit_if(node.else_body[0], lines, indent)
                else:
                    neg_cond = f"!({cond})"
                    self._hoist_if_body(node.else_body, neg_cond, lines, pad, indent)
            return

        cond = self._visit_expr(node.condition)
        lines.append(f"{pad}if ({cond}) {{")
        for s in node.body:
            self._visit_stmt(s, lines, indent + 1)
        lines.append(f"{pad}}}")
        if node.else_body:
            if len(node.else_body) == 1 and isinstance(node.else_body[0], IfStmt):
                lines[-1] = f"{pad}}} else"
                self._visit_if(node.else_body[0], lines, indent)
            else:
                lines[-1] = f"{pad}}} else {{"
                for s in node.else_body:
                    self._visit_stmt(s, lines, indent + 1)
                lines.append(f"{pad}}}")

    def _visit_for(self, node: ForStmt, lines: list[str], indent: int) -> None:
        pad = "    " * indent
        start = self._visit_expr(node.start)
        end = self._visit_expr(node.end)
        var = node.var  # new AST uses .var instead of .var_name
        if node.step is not None:
            # Explicit `by` step: unchanged from before — ascending compare
            # (matches every existing corpus use, all positive literal steps).
            step = self._visit_expr(node.step)
            lines.append(f"{pad}for (int {var} = {start}; {var} <= {end}; {var} += {step}) {{")
        else:
            # No `by` clause: Pine v6 auto-infers the loop direction from
            # start/end — descending (step -1) when start > end, else
            # ascending (step +1); see the Pine v6 `for` reference. start/end
            # are arbitrary runtime expressions (``for i = array.size(arr)-1
            # to 0`` — a common "iterate backward to safely remove an element
            # while iterating" idiom), so the direction can't always be
            # resolved at codegen time. Compute start/end into locals ONCE
            # (avoids re-evaluating a side-effecting expression, same class
            # of bug as nz()'s double-eval) and pick the comparison direction
            # at runtime from their relative order — this previously always
            # emitted an ascending `<=` loop, which never executes when
            # start > end (silently dropping the whole loop body).
            fid = self._for_counter
            self._for_counter += 1
            s_var, e_var = f"_for_start_{fid}", f"_for_end_{fid}"
            lines.append(f"{pad}int {s_var} = ({start}), {e_var} = ({end});")
            lines.append(
                f"{pad}for (int {var} = {s_var}; "
                f"({s_var} <= {e_var}) ? ({var} <= {e_var}) : ({var} >= {e_var}); "
                f"{var} += ({s_var} <= {e_var}) ? 1 : -1) {{"
            )
        # Register the loop counter so reads of it inside the body resolve (the
        # unknown-identifier guard in _visit_ident would otherwise flag it).
        saved_loop = self._current_loop_vars
        self._current_loop_vars = set(self._current_loop_vars)
        if var:
            self._current_loop_vars.add(var)
        _blk_saved = self._push_block_var_remap(node)
        try:
            for s in node.body:
                self._visit_stmt(s, lines, indent + 1)
        finally:
            self._pop_block_var_remap(_blk_saved)
        self._current_loop_vars = saved_loop
        lines.append(f"{pad}}}")

    def _loop_elem_is_writeback_udt(self, iterable) -> bool:
        """Whether a ``for x in coll`` loop variable must bind by reference.

        In Pine a ``for x in arr`` loop variable over an array of *user-defined
        objects* is a reference to the element — field writes (``x.f := v``)
        mutate the array in place — whereas over a primitive array it is a
        copy. So emit C++ ``auto&`` only for arrays whose element is a
        user-defined UDT struct. Primitive elements keep ``auto`` (Pine copy
        semantics: writing the loop var must NOT write back). Drawing handles
        (line/box/label/linefill/...) also keep ``auto``: their element type
        name is a builtin, not in ``_udt_defs``, and a handle copy already
        mutates the shared engine object. (Reassigning the loop var itself —
        ``x := ...`` — is not modelled by either form, but Pine forbids it for
        objects in practice and it does not occur in the corpus.)
        """
        spec = self._type_spec_from_expr(iterable)
        return (
            spec is not None
            and spec.kind == "array"
            and spec.element is not None
            and spec.element.kind == "udt"
            and spec.element.name in self._udt_defs
        )

    def _visit_for_in(self, node, lines: list[str], indent: int) -> None:
        pad = "    " * indent
        iterable = self._visit_expr(node.iterable)
        saved_loop = self._current_loop_vars
        self._current_loop_vars = set(self._current_loop_vars)
        if node.var:
            self._current_loop_vars.add(node.var)
        if node.vars:
            for v in node.vars:
                if v != "_":
                    self._current_loop_vars.add(v)
        if node.var:
            v_cpp = self._safe_name(node.var)
            ref = "&" if self._loop_elem_is_writeback_udt(node.iterable) else ""
            lines.append(f"{pad}for (auto{ref} {v_cpp} : {iterable}) {{")
        elif node.vars:
            bindings = ", ".join(node.vars)
            lines.append(f"{pad}for (auto [{bindings}] : {iterable}) {{")
        _blk_saved = self._push_block_var_remap(node)
        try:
            for s in node.body:
                self._visit_stmt(s, lines, indent + 1)
        finally:
            self._pop_block_var_remap(_blk_saved)
        lines.append(f"{pad}}}")
        self._current_loop_vars = saved_loop

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
                for s in case_body:
                    self._visit_stmt(s, lines, indent + 1)
                lines.append(f"{pad}}}")
        else:
            for i, (case_expr, case_body) in enumerate(node.cases):
                prefix = "if" if i == 0 else "else if"
                cond = self._visit_expr(case_expr)
                lines.append(f"{pad}{prefix} ({cond}) {{")
                for s in case_body:
                    self._visit_stmt(s, lines, indent + 1)
                lines.append(f"{pad}}}")

        if node.default_body:
            lines.append(f"{pad}else {{")
            for s in node.default_body:
                self._visit_stmt(s, lines, indent + 1)
            lines.append(f"{pad}}}")

    # ------------------------------------------------------------------
    # If/switch expression helpers
    # ------------------------------------------------------------------

    # _default_for_type lives on TypeInferer — see codegen/types.py.

    def _emit_body_with_assign(self, body: list, target: str,
                               lines: list[str], indent: int) -> None:
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
                    cpp = self._visit_expr(stmt.expr)
                    pad = "    " * indent
                    lines.append(f"{pad}{target} = {cpp};")
                elif isinstance(stmt, IfStmt):
                    # Nested if expression
                    self._visit_if_switch_expr(stmt, target, lines, indent)
                elif isinstance(stmt, SwitchStmt):
                    self._visit_if_switch_expr(stmt, target, lines, indent)
                else:
                    self._visit_stmt(stmt, lines, indent)
            else:
                self._visit_stmt(stmt, lines, indent)

    def _visit_if_switch_expr(self, node, target: str,
                              lines: list[str], indent: int) -> None:
        """Emit an if/switch used as an expression, assigning to target."""
        pad = "    " * indent
        if isinstance(node, IfStmt):
            cond = self._visit_expr(node.condition)
            lines.append(f"{pad}if ({cond}) {{")
            self._emit_body_with_assign(node.body, target, lines, indent + 1)
            lines.append(f"{pad}}}")
            if node.else_body:
                if len(node.else_body) == 1 and isinstance(node.else_body[0], IfStmt):
                    lines[-1] = f"{pad}}} else"
                    self._visit_if_switch_expr(node.else_body[0], target, lines, indent)
                else:
                    lines[-1] = f"{pad}}} else {{"
                    self._emit_body_with_assign(node.else_body, target, lines, indent + 1)
                    lines.append(f"{pad}}}")
        elif isinstance(node, SwitchStmt):
            if node.expr:
                expr_var = f"__switch_val_{self._switch_counter}"
                self._switch_counter += 1
                lines.append(f"{pad}auto {expr_var} = {self._visit_expr(node.expr)};")
                for i, (case_expr, case_body) in enumerate(node.cases):
                    prefix = "if" if i == 0 else "else if"
                    case_val = self._visit_expr(case_expr)
                    lines.append(f"{pad}{prefix} ({expr_var} == {case_val}) {{")
                    self._emit_body_with_assign(case_body, target, lines, indent + 1)
                    lines.append(f"{pad}}}")
            else:
                for i, (case_expr, case_body) in enumerate(node.cases):
                    prefix = "if" if i == 0 else "else if"
                    cond = self._visit_expr(case_expr)
                    lines.append(f"{pad}{prefix} ({cond}) {{")
                    self._emit_body_with_assign(case_body, target, lines, indent + 1)
                    lines.append(f"{pad}}}")
            if node.default_body:
                lines.append(f"{pad}else {{")
                self._emit_body_with_assign(node.default_body, target, lines, indent + 1)
                lines.append(f"{pad}}}")
