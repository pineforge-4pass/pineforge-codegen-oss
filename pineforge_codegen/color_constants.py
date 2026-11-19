"""Packed Pine v6 colors shared by analysis and emission."""

import math
from collections.abc import Callable

from .ast_nodes import (
    ASTNode, ColorLiteral, FuncCall, Identifier, MemberAccess, NumberLiteral, UnaryOp,
)


# Pine v6 r,g,b,t readouts in tests/fixtures/color_tv/w11-color-v6-eth15,
# n0/n1/n2. The native receipt test checks the corresponding engine symbols.
COLOR_CONSTANT_VALUES = {
    "red": 0xFFF23645,
    "green": 0xFF4CAF50,
    "blue": 0xFF2962FF,
    "white": 0xFFFFFFFF,
    "black": 0xFF363A45,
    "yellow": 0xFFFDD835,
    "orange": 0xFFFF9800,
    "purple": 0xFF9C27B0,
    "aqua": 0xFF00BCD4,
    "gray": 0xFF787B86,
    "lime": 0xFF00E676,
    "maroon": 0xFF880E4F,
    "navy": 0xFF311B92,
    "olive": 0xFF808000,
    "silver": 0xFFB2B5BE,
    "teal": 0xFF089981,
    "fuchsia": 0xFFE040FB,
}

COLOR_PARAMETERS = {
    "new": ("color", "transp"),
    "rgb": ("red", "green", "blue", "transp"),
    "r": ("color",), "g": ("color",), "b": ("color",), "t": ("color",),
}


def color_arguments(name, call):
    """Bind the builder's positional and keyword arguments."""
    bound = list(call.args)
    for index, parameter in enumerate(COLOR_PARAMETERS.get(name, ())):
        if index >= len(call.args) and parameter in call.kwargs:
            bound += [None] * (index + 1 - len(bound))
            bound[index] = call.kwargs[parameter]
    return bound


def color_argb_digits(value: str) -> str | None:
    """Reorder a Pine hex literal, preserving the case of its digits."""
    h = value.lstrip("#").strip()
    if len(h) == 6:
        argb = "ff" + h
    elif len(h) == 8:
        argb = h[6:] + h[:6]
    else:
        return None
    try:
        int(argb, 16)
    except ValueError:
        return None
    return argb


def _signed_number(node):
    if isinstance(node, NumberLiteral):
        return node.value
    if isinstance(node, UnaryOp) and node.op in ("+", "-"):
        value = _signed_number(node.operand)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value if node.op == "+" else -value
    return None


def literal_color_value(
    node: ASTNode | None, *, is_bound: Callable[[Identifier], bool] | None = None,
) -> int | None:
    """Read packed literal colors.

    With a binding query, require a real builtin and a complete, unambiguous
    call before using the result as a type proof for generic input.
    """
    if isinstance(node, ColorLiteral):
        digits = color_argb_digits(node.value)
        if digits is not None:
            return int(digits, 16)
        return None if is_bound is not None else 0
    member = node.callee if isinstance(node, FuncCall) else node
    if (not isinstance(member, MemberAccess)
            or not isinstance(member.object, Identifier)
            or member.object.name != "color"
            or (is_bound is not None and is_bound(member.object))):
        return None
    name = member.member
    if not isinstance(node, FuncCall):
        return COLOR_CONSTANT_VALUES.get(name)
    if is_bound is not None:
        parameters = COLOR_PARAMETERS.get(name, ())
        if (len(node.args) > len(parameters)
                or any(key not in parameters[len(node.args):] for key in node.kwargs)):
            return None
    arguments = color_arguments(name, node)
    if any(argument is None for argument in arguments):
        return None
    if name == "new" and len(arguments) == 2:
        base = literal_color_value(arguments[0], is_bound=is_bound)
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
