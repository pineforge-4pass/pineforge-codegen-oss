"""Static scalar overloads of the generic Pine ``input()``."""

from collections.abc import Callable

from . import signatures as sigs
from .ast_nodes import (
    ASTNode, BoolLiteral, FuncCall, Identifier, NumberLiteral, StringLiteral, TupleLiteral,
)
from .color_constants import literal_color_value
from .symbols import PineType


_NUMBER_TYPES = {bool: PineType.BOOL, int: PineType.INT, float: PineType.FLOAT}
PLAIN_INPUT_STRING_DEFAULT = "pf_plain_input_string_default"


def input_param_names(call: FuncCall, member: str | None) -> list[str] | None:
    """Use the numeric dropdown overload when the call supplies its list."""
    if member is None:
        return sigs.get_param_names(None, "input")
    intrinsic = sigs.INPUT_FUNCTIONS.get(member)
    if intrinsic is None:
        return None
    for signature in intrinsic.signatures[1:]:
        names = [param.name for param in signature.params]
        if "options" in names:
            slot = names.index("options")
            if ("options" in call.kwargs
                    or (len(call.args) > slot and isinstance(call.args[slot], TupleLiteral))):
                return names
    return intrinsic.param_names


def plain_input_default(call: FuncCall) -> ASTNode | None:
    """Bind the first input parameter, ``defval``."""
    if call.args and call.args[0] is not None:
        return call.args[0]
    return call.kwargs.get("defval")


def resolved_plain_input_default(call: FuncCall) -> ASTNode | None:
    """Use the string value proved by analysis, or the authored default."""
    default = plain_input_default(call)
    value = (call.annotations or {}).get(PLAIN_INPUT_STRING_DEFAULT)
    if isinstance(value, str) and default is not None:
        return StringLiteral(value=value, loc=default.loc)
    return default


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
