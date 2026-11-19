"""The C++ spelling of a packed Pine color literal."""

from ..color_constants import (
    COLOR_PARAMETERS as COLOR_PARAMETERS,
    color_argb_digits,
    color_arguments as color_arguments,
    literal_color_value as literal_color_value,
)


def color_literal_to_int64(value: str) -> str:
    """Lower #RRGGBB / #RRGGBBAA to the existing 0xAARRGGBBLL spelling."""
    digits = color_argb_digits(value)
    return f"0x{digits}LL" if digits is not None else "0"
