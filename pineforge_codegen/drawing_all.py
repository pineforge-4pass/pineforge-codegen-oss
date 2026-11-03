"""Recognize direct reads of the live, read-only drawing collections."""

from dataclasses import replace

from .ast_nodes import FuncCall, Identifier, MemberAccess
from .symbols import TypeSpec


DRAWING_ALL_KINDS = frozenset({"line", "box", "label", "linefill", "polyline"})
DRAWING_ALL_READS = frozenset({"size", "first", "last", "get"})


def drawing_all_kind(node):
    if (isinstance(node, MemberAccess) and node.member == "all"
            and isinstance(node.object, Identifier)
            and node.object.name in DRAWING_ALL_KINDS):
        return node.object.name
    return None


def drawing_all_call(node):
    """Return (kind, method, receiver, call without the functional receiver)."""
    if not isinstance(node, FuncCall) or not isinstance(node.callee, MemberAccess):
        return None
    callee = node.callee
    receiver = callee.object
    kind = drawing_all_kind(receiver)
    if kind is not None:
        return kind, callee.member, receiver, node
    if isinstance(receiver, Identifier) and receiver.name == "array":
        receiver = node.args[0] if node.args else node.kwargs.get("id")
        kind = drawing_all_kind(receiver)
        if kind is not None:
            return kind, callee.member, receiver, replace(
                node, args=node.args[1:] if node.args else [],
                kwargs={k: v for k, v in node.kwargs.items()
                        if k != "id" or node.args},
            )
    return None


def drawing_all_result_spec(node):
    call = drawing_all_call(node)
    if call is None or call[0] == "polyline" or call[1] not in DRAWING_ALL_READS:
        return None
    return (TypeSpec.primitive("int") if call[1] == "size"
            else TypeSpec.udt(call[0]))


def drawing_all_refusal(kind):
    if kind == "polyline":
        return "polyline.all requires an engine polyline handle and live drawing arena."
    return (f"Drawing collection '{kind}.all' only supports direct "
            "size(), first(), last() and get() reads.")
