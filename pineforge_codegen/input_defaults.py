"""Static scalar overloads of the generic Pine ``input()``."""

from collections.abc import Callable

from .ast_nodes import ASTNode, BoolLiteral, FuncCall, Identifier, NumberLiteral, StringLiteral
from .color_constants import literal_color_value
from .symbols import PineType


_NUMBER_TYPES = {bool: PineType.BOOL, int: PineType.INT, float: PineType.FLOAT}


def plain_input_default(call: FuncCall) -> ASTNode | None:
    """Bind the first input parameter, ``defval``."""
    if call.args and call.args[0] is not None:
        return call.args[0]
    return call.kwargs.get("defval")


def plain_input_type(
    default: ASTNode | None, *, is_bound: Callable[[Identifier], bool],
) -> PineType | None:
    """Classify literals and proven color builders, leaving expressions alone."""
    if isinstance(default, BoolLiteral):
        return PineType.BOOL
    if isinstance(default, NumberLiteral):
        return _NUMBER_TYPES.get(type(default.value))
    if isinstance(default, StringLiteral):
        return PineType.STRING
    if literal_color_value(default, is_bound=is_bound) is not None:
        return PineType.COLOR
    return None
