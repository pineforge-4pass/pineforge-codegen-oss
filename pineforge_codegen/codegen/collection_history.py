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
    COLLECTION_HISTORY_CLASS_CPP,
    COLLECTION_HISTORY_CPP,
    COLLECTION_HISTORY_GENERIC_MATRIX_CPP,
    COLLECTION_HISTORY_MATRIX_CPP,
    history_annotation,
    literal_offset,
)
from ..ast_nodes import FuncCall, Identifier, MemberAccess
from ..symbols import TypeSpec


class CollectionHistoryEmitter:
    """CodeGen mixin: the history members of array and matrix variables."""

    def _collection_history_variables(self) -> list:
        """The variables whose history the script reads, in name order."""
        history = getattr(self.ctx, "collection_history", None) or {}
        return [history[name] for name in sorted(history)]

    def _collection_history_member(self, name: str) -> str:
        return f"_pf_collection_hist_{self._safe_name(name)}"

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
        if not variables:
            return
        float_spec = TypeSpec.primitive("float")
        matrices = [v.spec for v in variables if v.kind == "matrix"]
        lines.append(COLLECTION_HISTORY_CPP.strip("\n"))
        if any(spec.element == float_spec for spec in matrices):
            lines.append(COLLECTION_HISTORY_MATRIX_CPP.strip("\n"))
        if any(spec.element != float_spec for spec in matrices):
            lines.append(COLLECTION_HISTORY_GENERIC_MATRIX_CPP.strip("\n"))
        lines.append(COLLECTION_HISTORY_CLASS_CPP.strip("\n"))
        lines.append("")

    def _collection_history_open(self, name: str) -> str | None:
        """The statement opening ``name``'s slot, when the script reads its
        history, else None."""
        history = getattr(self.ctx, "collection_history", None) or {}
        if name not in history:
            return None
        return (f"{self._collection_history_member(name)}.open("
                f"{self._safe_name(name)}, history_advances_new_bar());")

    def _collection_history_decl_open(self, node) -> str | None:
        """The statement a non-``var`` declaration of such a variable opens
        its slot with (each execution: a block's on the bars it runs)."""
        if node.is_var or node.is_varip:
            return None
        for variable in self._collection_history_variables():
            if id(node) in variable.decl_node_ids:
                return self._collection_history_open(variable.name)
        return None

    def _emit_collection_history_var_opens(self, lines: list[str], pad: str) -> None:
        """A top-level ``var``'s slot opens on every bar, after its first
        bar's initialization."""
        for variable in self._collection_history_variables():
            if variable.is_var:
                lines.append(f"{pad}{self._collection_history_open(variable.name)}")

    def _emit_collection_history_closes(self, lines: list[str], pad: str) -> None:
        """The bar's end: every open slot keeps a copy of its variable."""
        for variable in self._collection_history_variables():
            lines.append(
                f"{pad}{self._collection_history_member(variable.name)}.close("
                f"{self._safe_name(variable.name)});")

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
        if use == "loop" or offset == 0:
            return current
        member = self._collection_history_member(annotation["var"])
        index = self._collection_history_offset_cpp(node)
        dynamic = offset is None
        if use == "change":
            return f"{member}.changed({index}, {current})"
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
            return f"is_na({current})" if annotation["kind"] == "matrix" else "false"
        member = self._collection_history_member(annotation["var"])
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
            return MATRIX_METHODS[method](recv, args)
        except IndexError:
            self._codegen_error(
                node,
                f"matrix.{method}: wrong number of arguments",
                hint="Check Pine v6 matrix method signature (positional vs keyword).",
            )
