"""Shared argument binding for missing drawing-reference casts."""

from .ast_nodes import FuncCall, Identifier, NaLiteral
from .signatures import FuncSig, Param, merge_kwargs_to_positional
from .symbols import PineType


_REFERENCE_CAST_KINDS = frozenset({"line", "box", "label", "linefill", "polyline"})
_REFERENCE_CAST_SIGNATURE = FuncSig(
    params=[Param("x", PineType.NA)], return_type=PineType.UNKNOWN,
)


def na_reference_cast_kind(node) -> str | None:
    """Return the reference kind for a valid ``kind(na)`` / ``kind(x=na)``.

    Bind the same one-slot signature in analysis, default initialization and
    expression emission. Malformed or non-NA calls retain normal diagnostics.
    """
    if (not isinstance(node, FuncCall)
            or not isinstance(node.callee, Identifier)
            or node.callee.name not in _REFERENCE_CAST_KINDS):
        return None
    names = [param.name for param in _REFERENCE_CAST_SIGNATURE.params]
    if (len(node.args) > len(names)
            or set(node.kwargs) - set(names)
            or any(name in node.kwargs for name in names[:len(node.args)])):
        return None
    arguments = merge_kwargs_to_positional(
        _REFERENCE_CAST_SIGNATURE, node.args, node.kwargs,
    )
    if len(arguments) != len(names):
        return None
    value = arguments[0]
    if (isinstance(value, NaLiteral)
            or (isinstance(value, Identifier) and value.name == "na")):
        return node.callee.name
    return None
