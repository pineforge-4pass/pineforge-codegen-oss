"""The C++ of an array's or a matrix's history (``a[k]``, ``m[k]``).

The analyzer annotates every such read with how its value is used and lists
the variables whose history the script reads (``ctx.collection_history``;
pineforge_codegen/collection_history.py). Such a variable keeps its own
``std::vector`` or matrix member, so every other operation on it keeps its
C++; beside it, ``_pf_collection_hist_<name>`` keeps the copies its
executions left. Each execution of the declaration -- each bar, for a top
level ``var`` -- opens a slot (``open``) and the end of the bar closes it with
a copy (``close``), so ``a[1]`` is the array as the previous bar left it,
TradingView's read-only history (fixtures/array_history_tv).
"""

from __future__ import annotations

from ..collection_history import (
    COLLECTION_HISTORY_ARRAY_VALUE_CPP,
    COLLECTION_HISTORY_CLASS_CPP,
    COLLECTION_HISTORY_CPP,
    COLLECTION_HISTORY_GENERIC_MATRIX_CPP,
    COLLECTION_HISTORY_MATRIX_CPP,
    history_annotation,
    literal_offset,
)
from ..ast_nodes import Assignment, FuncCall, FuncDef, Identifier, MemberAccess, MethodDef, Ternary, VarDecl
from ..limits import iter_ast_nodes
from ..symbols import TypeSpec


class CollectionHistoryEmitter:
    """CodeGen mixin: the history members of array and matrix variables."""

    def _array_history_value_names(self) -> dict[str, TypeSpec]:
        cached = getattr(self, "_array_history_value_specs", None)
        if cached is not None:
            return cached
        bindings = {}
        specs = {}
        callable_depth = None
        for node, depth in iter_ast_nodes(self.ctx.ast):
            if callable_depth is not None:
                if depth > callable_depth:
                    continue
                callable_depth = None
            if isinstance(node, (FuncDef, MethodDef)):
                callable_depth = depth
                continue
            if isinstance(node, VarDecl):
                name, value = node.name, node.value
            elif isinstance(node, Assignment) and isinstance(node.target, Identifier):
                name, value = node.target.name, node.value
            else:
                continue
            annotation = history_annotation(value)
            if annotation is not None and annotation["use"] == "reference":
                specs[name] = annotation["spec"]
            else:
                values = [value]
                while values:
                    source_value = values.pop()
                    if isinstance(source_value, Identifier):
                        bindings.setdefault(source_value.name, []).append(name)
                    elif isinstance(source_value, Ternary):
                        values.extend((source_value.true_val, source_value.false_val))
        pending = list(specs)
        while pending:
            source = pending.pop()
            for name in bindings.get(source, ()):
                if name not in specs:
                    specs[name] = specs[source]
                    pending.append(name)
        for name, spec in specs.items():
            self._array_vars.add(name)
            self._collection_types[name] = spec
        self._array_history_value_specs = specs
        return specs

    def _array_history_value_cpp_type(self, name: str) -> str | None:
        spec = self._array_history_value_names().get(name)
        if spec is None:
            return None
        spec = self._widen_array_spec_for_name(name, spec)
        return f"_PFArrayHistoryValue<{self._type_spec_to_cpp(spec)}>"

    def _array_history_value_expr_cpp_type(self, value) -> str | None:
        values = [value]
        while values:
            source = values.pop()
            if isinstance(source, Identifier):
                if (self._collection_name_is_lexically_shadowed(source.name)
                        or source.name in self._current_func_param_types):
                    continue
                cpp_type = self._array_history_value_cpp_type(source.name)
                if cpp_type is not None:
                    return cpp_type
            elif isinstance(source, Ternary):
                values.extend((source.true_val, source.false_val))
        return None

    def _legacy_array_history_element(self, receiver: str, spec: TypeSpec,
                                      offset: str, mutable: bool = False) -> str:
        element_cpp = self._type_spec_to_cpp(spec.element)
        if self._array_history_value_names():
            receiver = f"_pf_array_id({receiver})"
        if mutable:
            return (
                f"([&]() -> decltype(auto) {{ "
                f"auto& _pf_history_array = {receiver}; "
                f"const int _pf_history_offset = {offset}; "
                f"if (_pf_history_offset < 0 || "
                f"static_cast<size_t>(_pf_history_offset) >= _pf_history_array.size()) "
                f'pine_runtime_error("Array history element index is out of bounds."); '
                f"return _pf_history_array.at(_pf_history_offset); }}())"
            )
        return (
            f"([&]() -> {element_cpp} {{ "
            f"const auto& _pf_history_array = {receiver}; "
            f"const int _pf_history_offset = {offset}; "
            f"if (_pf_history_offset < 0 || "
            f"static_cast<size_t>(_pf_history_offset) >= _pf_history_array.size()) "
            f"return na<{element_cpp}>(); "
            f"return _pf_history_array[_pf_history_offset]; }}())"
        )

    def _collection_history_variables(self) -> list:
        """The declarations whose history the script reads, in key order."""
        history = getattr(self.ctx, "collection_history", None) or {}
        return [history[key] for key in sorted(history)]

    def _collection_history_member(self, variable) -> str:
        """``_pf_collection_hist_<name>``; a later declaration of the same
        name (a sibling block's) gets ``_pf_collection_hist_<ordinal>_<name>``,
        which no Pine name (none starts with a digit) can spell: ``x``'s
        second declaration beside a variable ``x_1``."""
        prefix = f"{variable.ordinal}_" if variable.ordinal else ""
        return f"_pf_collection_hist_{prefix}{self._safe_name(variable.name)}"

    def _collection_history_of(self, annotation):
        history = getattr(self.ctx, "collection_history", None) or {}
        return history.get(annotation.get("member"))

    def _collection_history_cpp_type(self, variable) -> str:
        """The history's element type: the variable member's own C++ type,
        whatever its declaration spells (a wide int array included)."""
        return f"decltype({self._safe_name(variable.name)})"

    def _collection_history_capacity(self, variable) -> str:
        """The ring's size: the largest literal offset read plus one, or the
        Series default (a ``max_bars_back`` cap) for a dynamic offset."""
        if variable.capacity is None:
            return self._series_decl_suffix()
        return f"{{{max(2, variable.capacity)}}}"

    def _emit_collection_history_helper(self, lines: list[str]) -> None:
        variables = self._collection_history_variables()
        if not variables and not self._array_history_value_names():
            return
        float_spec = TypeSpec.primitive("float")
        matrices = [v.spec for v in variables if v.kind == "matrix"]
        lines.append(COLLECTION_HISTORY_CPP.strip("\n"))
        lines.append(COLLECTION_HISTORY_ARRAY_VALUE_CPP.strip("\n"))
        if any(spec.element == float_spec for spec in matrices):
            lines.append(COLLECTION_HISTORY_MATRIX_CPP.strip("\n"))
        if any(spec.element != float_spec for spec in matrices):
            lines.append(COLLECTION_HISTORY_GENERIC_MATRIX_CPP.strip("\n"))
        lines.append(COLLECTION_HISTORY_CLASS_CPP.strip("\n"))
        lines.append("")

    def _collection_history_open(self, variable) -> str:
        """The statement opening a declaration's slot."""
        return (f"{self._collection_history_member(variable)}.open("
                f"{self._safe_name(variable.name)}, history_advances_new_bar());")

    def _collection_history_close(self, variable) -> str:
        return (f"{self._collection_history_member(variable)}.close("
                f"{self._safe_name(variable.name)});")

    def _collection_history_decl_statements(self, node) -> list[str]:
        """What an execution of a declaration does first: close the history
        of every other declaration writing the same member (a sibling
        block's ``x``, which keeps the array its block left), then open its
        own slot when it is not a ``var`` (a block's on the bars it runs)."""
        statements = []
        own = None
        for variable in self._collection_history_variables():
            if id(node) in variable.closed_by:
                statements.append(self._collection_history_close(variable))
            elif variable.decl_node_id == id(node):
                own = variable
        if own is not None and not (getattr(node, "is_var", False)
                                    or getattr(node, "is_varip", False)):
            statements.append(self._collection_history_open(own))
        return statements

    def _emit_collection_history_var_opens(self, lines: list[str], pad: str) -> None:
        """A top-level ``var``'s slot opens on every bar, after its first
        bar's initialization."""
        for variable in self._collection_history_variables():
            if variable.is_var:
                lines.append(f"{pad}{self._collection_history_open(variable)}")

    def _emit_collection_history_closes(self, lines: list[str], pad: str) -> None:
        """The bar's end: every open slot keeps a copy of its variable."""
        for variable in self._collection_history_variables():
            lines.append(f"{pad}{self._collection_history_close(variable)}")

    @staticmethod
    def _roots_at_collection_history(value) -> bool:
        """Whether ``value`` is an array's or a matrix's history or a
        built-in's call on one (``m[1]``, ``(m[1]).copy()``,
        ``matrix.transpose(m[1])``)."""
        if history_annotation(value) is not None:
            return True
        if not isinstance(value, FuncCall):
            return False
        callee = value.callee
        if isinstance(callee, MemberAccess) and history_annotation(callee.object) is not None:
            return True
        first = value.args[0] if value.args else None
        return (isinstance(callee, MemberAccess)
                and isinstance(callee.object, Identifier)
                and callee.object.name in ("array", "matrix")
                and first is not None and history_annotation(first) is not None)

    def _collection_history_offset_cpp(self, node) -> str:
        offset = literal_offset(node)
        if offset is not None:
            return str(offset)
        return self._history_offset_cpp(self._visit_expr(node.index), node.index)

    def _lower_collection_history(self, node) -> str | None:
        """The C++ of an annotated array or matrix history read, by its use:
        a ``const`` copy a built-in reads (``at``), a copy for a variable or a
        parameter (``value``), the receiver of a built-in that changes it
        (``changed``: the run stops past a zero offset), the variable itself
        for a for...in loop (TradingView iterates the current array) and at a
        zero offset. None for any other read."""
        annotation = history_annotation(node)
        if annotation is None:
            return None
        current = self._visit_expr(node.object)
        use = annotation["use"]
        offset = literal_offset(node)
        if use == "reference" and offset == 0:
            cpp_type = self._type_spec_to_cpp(annotation["spec"])
            return f"_PFArrayHistoryValue<{cpp_type}>({current})"
        if use == "loop" or offset == 0:
            return current
        member = self._collection_history_member(self._collection_history_of(annotation))
        index = self._collection_history_offset_cpp(node)
        dynamic = offset is None
        if use == "change":
            return f"{member}.changed({index}, {current})"
        if use == "reference":
            return (f"{member}.reference({index}, {current})" if dynamic
                    else f"{member}.reference({index})")
        if use == "copy":
            return (f"{member}.value({index}, {current})" if dynamic
                    else f"{member}.value({index})")
        return (f"{member}.at({index}, {current})" if dynamic
                else f"{member}.at({index})")

    def _collection_history_na(self, node) -> str | None:
        """``na(a[k])``: whether the history holds no copy there (an array
        variable itself is never na in PineForge; a matrix may be)."""
        annotation = history_annotation(node)
        if annotation is None or annotation["use"] != "na":
            return None
        current = self._visit_expr(node.object)
        offset = literal_offset(node)
        if offset == 0:
            nullable_array = (isinstance(node.object, Identifier)
                              and node.object.name in self._array_history_value_names())
            return (f"is_na({current})" if annotation["kind"] == "matrix"
                    or nullable_array else "false")
        member = self._collection_history_member(self._collection_history_of(annotation))
        index = self._collection_history_offset_cpp(node)
        if offset is None:
            return f"{member}.is_na({index}, {current})"
        return f"{member}.is_na({index})"

    def _matrix_history_method_expr(self, receiver, method: str, spec, node) -> str:
        """A matrix method called on a matrix's history, lowered as the
        variable's own method call is (visit_call: a matrix variable's
        method)."""
        from .tables import MATRIX_METHOD_KWARGS, MATRIX_METHODS, _merge_kwargs

        recv = self._visit_expr(receiver)
        self._check_matrix_method_allowed(method, spec, node)
        param_names = MATRIX_METHOD_KWARGS.get(method)
        raw_args = (
            _merge_kwargs(node.args, node.kwargs, param_names, lambda a: a)
            if param_names and node.kwargs else list(node.args)
        )
        args = self._matrix_bool_value_args(
            method, [self._visit_expr(a) for a in raw_args], raw_args, spec)
        try:
            return self._matrix_method_expr(recv, method, args, raw_args, node)
        except IndexError:
            self._codegen_error(
                node,
                f"matrix.{method}: wrong number of arguments",
                hint="Check Pine v6 matrix method signature (positional vs keyword).",
            )
