"""Pine builtin constants: semantic types and exact scalar values.

Visual arguments can be omitted by a renderer without changing the values of
the constants themselves. Nominal builtin enums keep their identity even when
the runtime adapter stores their payload in a primitive C++ type.
"""

from dataclasses import dataclass

from .symbols import PineType, TypeSpec


STRING_CONSTANT_VALUES = {
    "size": {name: name for name in ("auto", "tiny", "small", "normal", "large", "huge")},
    "position": {
        f"{vertical}_{horizontal}": f"{vertical}_{horizontal}"
        for vertical in ("top", "middle", "bottom")
        for horizontal in ("left", "center", "right")
    },
    "line": {
        "style_solid": "sol", "style_dotted": "dot", "style_dashed": "dsh",
        "style_arrow_left": "al", "style_arrow_right": "ar", "style_arrow_both": "ab",
    },
    "label": {
        "style_none": "n", "style_xcross": "xcr", "style_cross": "cr",
        "style_triangleup": "tup", "style_triangledown": "tdn", "style_flag": "flg",
        "style_circle": "cir", "style_arrowup": "aup", "style_arrowdown": "adn",
        "style_label_up": "lup", "style_label_down": "ldn",
        "style_label_left": "llf", "style_label_right": "lrg",
        "style_label_lower_left": "llwlf", "style_label_lower_right": "llwrg",
        "style_label_upper_left": "luplf", "style_label_upper_right": "luprg",
        "style_label_center": "lcn", "style_square": "sq",
        "style_diamond": "dia", "style_text_outline": "to",
    },
    "shape": {
        "arrowdown": "shape_arrow_down", "arrowup": "shape_arrow_up",
        "circle": "shape_circle", "cross": "shape_cross", "diamond": "shape_diamond",
        "flag": "shape_flag", "labeldown": "shape_label_down", "labelup": "shape_label_up",
        "square": "shape_square", "triangledown": "shape_triangle_down",
        "triangleup": "shape_triangle_up", "xcross": "shape_xcross",
    },
}

# Storage is an adapter detail, not an implicit Pine conversion to string.
BUILTIN_ENUM_STORAGE = {"sort_order": PineType.STRING}
ENUM_CONSTANT_VALUES = {
    "order": ("sort_order", {"ascending": "ascending", "descending": "descending"}),
}


def is_builtin_enum(spec: TypeSpec | None) -> bool:
    return (spec is not None and spec.kind == "enum"
            and spec.name in BUILTIN_ENUM_STORAGE)


def contains_builtin_enum(spec: TypeSpec | None) -> bool:
    if spec is None:
        return False
    return is_builtin_enum(spec) or any(
        contains_builtin_enum(child) for child in (spec.element, spec.key, spec.value)
    )


@dataclass(frozen=True)
class BuiltinConstant:
    type_spec: TypeSpec
    value: str

    @property
    def storage_type(self) -> PineType:
        if is_builtin_enum(self.type_spec):
            return BUILTIN_ENUM_STORAGE[self.type_spec.name]
        return PineType.STRING


def builtin_constant(namespace: str, member: str) -> BuiltinConstant | None:
    values = STRING_CONSTANT_VALUES.get(namespace, {})
    if member in values:
        return BuiltinConstant(TypeSpec.primitive("string"), values[member])
    enum = ENUM_CONSTANT_VALUES.get(namespace)
    if enum is not None:
        type_name, values = enum
        if member in values:
            return BuiltinConstant(TypeSpec("enum", name=type_name), values[member])
    return None
