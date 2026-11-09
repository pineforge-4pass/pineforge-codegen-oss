"""The existing packed-color encoding, shared by defaults and constant proofs."""

import math

from ..ast_nodes import (
    ColorLiteral, FuncCall, Identifier, MemberAccess, NumberLiteral, UnaryOp,
)
from .tables import _COLOR_CONSTANTS


COLOR_PARAMETERS = {
    "new": ("color", "transp"),
    "rgb": ("red", "green", "blue", "transp"),
    "r": ("color",), "g": ("color",), "b": ("color",), "t": ("color",),
}


def color_arguments(name, call):
    """Keep the getter's existing positional/keyword binding unchanged."""
    bound = list(call.args)
    for index, parameter in enumerate(COLOR_PARAMETERS.get(name, ())):
        if index >= len(call.args) and parameter in call.kwargs:
            bound += [None] * (index + 1 - len(bound))
            bound[index] = call.kwargs[parameter]
    return bound


def color_literal_to_int64(value: str) -> str:
    """Lower #RRGGBB / #RRGGBBAA to the existing 0xAARRGGBBLL spelling."""
    h = value.lstrip("#").strip()
    if len(h) == 6:
        rr, gg, bb, aa = h[0:2], h[2:4], h[4:6], "ff"
    elif len(h) == 8:
        rr, gg, bb, aa = h[0:2], h[2:4], h[4:6], h[6:8]
    else:
        return "0"
    try:
        int(rr + gg + bb + aa, 16)
    except ValueError:
        return "0"
    return f"0x{aa}{rr}{gg}{bb}LL"


def _signed_number(node):
    if isinstance(node, NumberLiteral):
        return node.value
    if isinstance(node, UnaryOp) and node.op in ("+", "-"):
        value = _signed_number(node.operand)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value if node.op == "+" else -value
    return None


def literal_color_value(node) -> int | None:
    """Read literal colors exactly as the input getter's original evaluator."""
    if isinstance(node, ColorLiteral):
        return int(color_literal_to_int64(node.value).removesuffix("LL"), 16)
    if isinstance(node, MemberAccess) and isinstance(node.object, Identifier):
        constant = _COLOR_CONSTANTS.get(node.member) if node.object.name == "color" else None
        return constant[1] if constant is not None else None
    if not (isinstance(node, FuncCall) and isinstance(node.callee, MemberAccess)
            and isinstance(node.callee.object, Identifier)
            and node.callee.object.name == "color"):
        return None
    name = node.callee.member
    arguments = color_arguments(name, node)
    if any(argument is None for argument in arguments):
        return None
    if name == "new" and len(arguments) == 2:
        base = literal_color_value(arguments[0])
        transparency = _signed_number(arguments[1])
    elif name == "rgb" and len(arguments) in (3, 4):
        channels = [_signed_number(argument) for argument in arguments[:3]]
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   and (not isinstance(value, float) or math.isfinite(value))
                   and -(1 << 63) < value < (1 << 63) for value in channels):
            return None
        red, green, blue = (int(value) & 0xFF for value in channels)
        base = red << 16 | green << 8 | blue
        transparency = _signed_number(arguments[3]) if len(arguments) == 4 else 0
    else:
        return None
    if (base is None or not isinstance(transparency, (int, float))
            or isinstance(transparency, bool)
            or (isinstance(transparency, float) and not math.isfinite(transparency))):
        return None
    transparency = min(100, max(0, transparency))
    alpha = math.floor(255.0 * (100.0 - transparency) / 100.0 + 0.5)
    return alpha << 24 | (base & 0xFFFFFF)
