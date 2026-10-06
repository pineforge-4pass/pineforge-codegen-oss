"""Argument-dependent result types of numeric matrix operations."""

from .ast_nodes import FuncCall


def matrix_sum_has_rhs(call: FuncCall, *, namespace: bool = False) -> bool:
    return len(call.args) > int(namespace) or "id2" in call.kwargs
