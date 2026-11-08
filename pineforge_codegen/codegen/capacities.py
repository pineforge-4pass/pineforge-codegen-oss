"""Source-selected retention, independent of the number of executed bars."""

from ..ast_nodes import BoolLiteral, FuncCall, NaLiteral, NumberLiteral, Subscript, UnaryOp
from ..collection_history import history_annotation
from ..errors import CompileError, Diagnostic, Level, Phase, SourceLocation
from ..limits import syntax_children
from ..session_reads import _dropped_arguments
from ..symbols import PineType
from .constant_values import ConstantResolver


DEFAULT_DYNAMIC_HISTORY_CAPACITY = 5000
LEGACY_HISTORY_CAPACITY = 500


def validate_capacity_options(history_capacity, drawing_capacity, filename):
    for option, value, minimum in (
        ("history_capacity", history_capacity, 2),
        ("drawing_capacity", drawing_capacity, 1),
    ):
        if value is not None and (type(value) is not int or value < minimum):
            raise CompileError([Diagnostic(
                level=Level.ERROR, phase=Phase.CODEGEN,
                location=SourceLocation(filename, 1, 1, 1),
                message=f"Codegen option {option} must be an integer of at least {minimum}, or None.",
            )])


def literal_history_offset(index):
    """Truncate literal numeric offsets as the native history index does."""
    if isinstance(index, UnaryOp) and index.op == "-":
        value = literal_history_offset(index.operand)
        return -value if value is not None else None
    if isinstance(index, NumberLiteral):
        value = index.value
        if type(value) is int:
            return value
        if type(value) is float and value == value and abs(value) != float("inf"):
            return int(value)
    if isinstance(index, BoolLiteral):
        return int(index.value)
    return None


def capacity_nodes(roots):
    """Walk syntax once, excluding arguments that never execute."""
    stack = list(reversed(roots))
    while stack:
        node = stack.pop()
        yield node
        children = list(syntax_children(node))
        if isinstance(node, FuncCall):
            dropped = {id(value) for value in _dropped_arguments(node)}
            children = [child for child in children if id(child) not in dropped]
        stack.extend(children)


class CapacityPolicy:
    """CodeGen mixin for the capacities supplied to owning constructors."""

    def _prepare_history_capacities(self):
        self._inferred_history_capacity = None
        required = LEGACY_HISTORY_CAPACITY
        dynamic_bare = False
        resolver = None
        roots = [self.ctx.ast]
        roots.extend(
            pragma.expr_node for pragma in (self.ctx.pf_trace_pragmas or [])
            if pragma.expr_node is not None
        )
        for node in capacity_nodes(roots):
            if self._budget is not None:
                self._budget.check(node.loc, phase=Phase.CODEGEN)
            if not isinstance(node, Subscript):
                continue
            annotation = history_annotation(node)
            if annotation is not None:
                # Iteration uses the live collection; its written offset is
                # never evaluated as a historical read.
                if annotation["use"] == "loop":
                    continue
            else:
                spec = self._type_spec_from_expr(node.object)
                if spec is not None and spec.kind in ("array", "matrix", "map"):
                    continue
            offset = literal_history_offset(node.index)
            if offset is None and not isinstance(node.index, NaLiteral):
                if resolver is None:
                    resolver = ConstantResolver(self.ctx.ast, self._budget)
                proof = (resolver.expression(node.index)
                         if id(node.index) in resolver.order else None)
                if proof is not None and proof.pine_type in (
                        PineType.INT, PineType.FLOAT, PineType.BOOL):
                    value = proof.value
                    if type(value) in (int, bool):
                        offset = int(value)
                    elif value == value and abs(value) != float("inf"):
                        offset = int(value)
            if offset is not None:
                self._check_history_capacity_offset(offset, node.index)
                # Keep the existing evaluated-offset range guard independent
                # of retention. A capacity never makes an invalid index valid.
                if annotation is None and 0 <= offset <= 10000:
                    required = max(required, offset + 1)
            elif not isinstance(node.index, NaLiteral):
                dynamic_bare = True
        if self._max_bars_back_cap is None:
            if dynamic_bare:
                required = max(required, DEFAULT_DYNAMIC_HISTORY_CAPACITY)
            if required > LEGACY_HISTORY_CAPACITY:
                self._inferred_history_capacity = required
        # Series accepts int; never emit an implicit narrowing conversion for
        # a Python integer outside that constructor's representable domain.
        if self._history_capacity is not None and self._history_capacity > 2**31 - 1:
            capacity = self._history_capacity
            self._codegen_error(
                None,
                f"history_capacity={capacity} cannot be represented by the native Series constructor.",
            )
        if self._drawing_capacity is not None and self._drawing_capacity > 2**63 - 1:
            capacity = self._drawing_capacity
            self._codegen_error(
                None,
                f"drawing_capacity={capacity} cannot be represented by the native int64 limit argument.",
            )

    def _check_history_capacity_offset(self, offset, node):
        capacity = self._history_capacity
        if capacity is not None and offset is not None and offset >= capacity:
            self._codegen_error(
                node,
                f"History offset {offset} cannot fit history_capacity={capacity}.",
            )

    def _resolved_history_capacity(self):
        """An explicit Pine directive retains precedence over a fallback."""
        if self._history_capacity is not None:
            return max(self._history_capacity, self._max_bars_back_cap or 0)
        return self._max_bars_back_cap or self._inferred_history_capacity
