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
    SKIP_VAR_TYPES,
    TA_RETURNS_BOOL,
    TA_TUPLE_FIELDS,
    MATRIX_RETURNING_METHODS,
)


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

        # Check if it is a static (non-series) global member variable already evaluated inside _inputs_initialized_ block
        is_static_global_input = False
        if is_global_member and isinstance(node.value, FuncCall) and self._is_input_call(node.value):
            func_name_i, namespace_i = self._resolve_callee(node.value.callee)
            is_static_global_input = (
                func_name_i != "source"
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
            if namespace == "array" and func_name in ("new", "new_float", "new_int", "new_bool", "new_string", "from", "copy", "slice"):
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

        # Skip visual function assignments — but still emit declaration for
        # table function results since the var may be used later
        if isinstance(node.value, FuncCall) and self._is_skip_expr(node.value):
            func_name, namespace = self._resolve_callee(node.value.callee)
            if namespace in SKIP_VAR_TYPES:
                # Emit var with default value so references don't fail
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

        # General declaration
        cpp_val = self._visit_expr(node.value)
        if is_global_member:
            lines.append(f"{pad}{safe} = {cpp_val};")
        else:
            cpp_type = self._type_for_decl(node)
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
            val_cpp = self._visit_expr(node.value)
            if node.op == ":=":
                lines.append(f"{pad}{safe} = {val_cpp};")
            else:
                rhs = self._compound_assign_rhs(safe, node.op, val_cpp)
                if rhs is not None:
                    lines.append(f"{pad}{safe} = {rhs};")
                else:
                    lines.append(f"{pad}{safe} {node.op} {val_cpp};")
        else:
            val_cpp = self._visit_expr(node.value)
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
                    lines.append(f"{pad}double {name} = {result_var}.{fields[i]};")
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

    def _visit_if(self, node: IfStmt, lines: list[str], indent: int) -> None:
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
        step = self._visit_expr(node.step) if node.step else "1"
        var = node.var  # new AST uses .var instead of .var_name
        lines.append(f"{pad}for (int {var} = {start}; {var} <= {end}; {var} += {step}) {{")
        # Register the loop counter so reads of it inside the body resolve (the
        # unknown-identifier guard in _visit_ident would otherwise flag it).
        saved_loop = self._current_loop_vars
        self._current_loop_vars = set(self._current_loop_vars)
        if var:
            self._current_loop_vars.add(var)
        for s in node.body:
            self._visit_stmt(s, lines, indent + 1)
        self._current_loop_vars = saved_loop
        lines.append(f"{pad}}}")

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
            lines.append(f"{pad}for (auto {v_cpp} : {iterable}) {{")
        elif node.vars:
            bindings = ", ".join(node.vars)
            lines.append(f"{pad}for (auto [{bindings}] : {iterable}) {{")
        for s in node.body:
            self._visit_stmt(s, lines, indent + 1)
        lines.append(f"{pad}}}")
        self._current_loop_vars = saved_loop

    def _visit_while(self, node: WhileStmt, lines: list[str], indent: int) -> None:
        pad = "    " * indent
        cond = self._visit_expr(node.condition)
        lines.append(f"{pad}while ({cond}) {{")
        for s in node.body:
            self._visit_stmt(s, lines, indent + 1)
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
