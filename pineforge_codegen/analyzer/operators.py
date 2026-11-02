"""Pine operator overloads over semantic types, independent of C++ storage."""

from ..symbols import TypeSpec


# Only argument-independent scalar results belong here. The signature
# registry also contains storage-oriented approximations of polymorphic calls.
_FIXED_RESULT_CALLS = {
    "str": frozenset({
        "tostring", "format", "format_time", "length", "contains", "startswith",
        "endswith", "pos", "substring", "replace", "replace_all", "lower",
        "upper", "trim", "tonumber", "repeat",
    }),
    "math": frozenset({
        "ceil", "floor", "round_to_mintick", "pow", "sqrt", "exp", "log",
        "log10", "random", "sin", "cos", "tan", "asin", "acos", "atan",
        "atan2", "todegrees", "toradians",
    }),
    "ta": frozenset({
        "sma", "ema", "rma", "wma", "hma", "vwma", "alma", "swma",
        "linreg", "rsi", "stoch", "cci", "mfi", "roc", "cmo", "tsi",
        "wpr", "cog", "bbw", "kcw", "atr", "tr", "stdev", "variance",
        "sar", "crossover", "crossunder", "cross", "rising", "falling",
        "highestbars", "lowestbars", "percentrank",
        "percentile_linear_interpolation", "dev", "barssince", "correlation",
        "rci",
    }),
}

_INPUT_RESULTS = {
    "int": "int", "float": "float", "bool": "bool", "string": "string",
    "color": "color", "source": "float", "price": "float", "time": "int",
    "timeframe": "string", "session": "string", "symbol": "string",
    "text_area": "string",
}
_BOOLEAN_TA_RESULTS = frozenset({
    "crossover", "crossunder", "cross", "rising", "falling",
})


def fixed_builtin_result(namespace: str, name: str, arity: int) -> TypeSpec | None:
    """Return a proven fixed result; polymorphic or unlisted calls stay open."""
    if namespace == "input":
        result = _INPUT_RESULTS.get(name)
        return TypeSpec.primitive(result) if result is not None else None
    if namespace == "table" and name in {"new", "copy"}:
        return reference_value_type(namespace)
    if name not in _FIXED_RESULT_CALLS.get(namespace, ()):
        return None
    if namespace == "ta":
        result = (
            "bool" if name in _BOOLEAN_TA_RESULTS
            else "int" if name in {"barssince", "highestbars", "lowestbars"}
            else "float"
        )
    elif namespace == "str":
        result = (
            "bool" if name in {"contains", "startswith", "endswith"}
            else "int" if name in {"length", "pos"}
            else "float" if name == "tonumber"
            else "string"
        )
    else:
        result = "int" if name in {"ceil", "floor"} else "float"
    return TypeSpec.primitive(result)


def reference_value_type(name: str | None) -> TypeSpec | None:
    """Semantic reference identity, independently of its emitted storage."""
    return (TypeSpec.udt(name) if name in {
        "line", "label", "box", "table", "linefill", "chart.point",
    } else None)


def source_builtin_result(
    namespace: str, name: str, arguments: tuple[TypeSpec | None, ...],
) -> TypeSpec | None:
    """Resolve source-dependent overloads from proven arguments, never storage."""
    # "number" is an operator-domain proof, not a runtime storage type:
    # the value is numeric while its int/float overload remains unresolved.
    numeric = {"int", "int64", "float", "number"}
    if namespace == "math" and name in {"abs", "sign", "max", "min"}:
        sources = arguments if name in {"max", "min"} else arguments[:1]
        if not sources or any(
            source is None or source.kind != "primitive" or source.name not in numeric
            for source in sources
        ):
            return TypeSpec.primitive("number")
        result = sources[0]
        for source in sources[1:]:
            result = join_value_types(result, source)
        return result
    if namespace == "math":
        return TypeSpec.primitive("number")
    if namespace == "ta":
        index = 1 if name == "valuewhen" else 0
        if len(arguments) <= index:
            return None
        source = arguments[index]
        if source is None or source.kind != "primitive":
            return None
        if name in {"change", "valuewhen"} and source.name in numeric | {"bool"}:
            return source
        if name in {"highest", "lowest"} and len(arguments) > 1 and source.name in numeric:
            # These extrema return float even for an integer source.
            return TypeSpec.primitive("float")
    return None


# Each signature is (operand domains, result domain). "same" is one bound,
# nominal type variable: collections and objects can be selected, but that
# does not give them arithmetic or equality operators.
_NUMERIC_BINARY = ((("number", "number"), "number"),)
_EQUALITY = tuple(
    ((domain, domain), "bool")
    for domain in ("number", "bool", "string", "color", "line", "label", "enum")
)
OPERATOR_OVERLOADS = {
    (1, "+"): ((("number",), "number"),),
    (1, "-"): ((("number",), "number"),),
    (1, "not"): ((("bool",), "bool"),),
    (2, "+"): _NUMERIC_BINARY + ((("string", "string"), "string"),),
    (2, "-"): _NUMERIC_BINARY,
    (2, "*"): _NUMERIC_BINARY,
    (2, "/"): ((("number", "number"), "float"),),
    (2, "%"): _NUMERIC_BINARY,
    (2, "=="): _EQUALITY,
    (2, "!="): _EQUALITY,
    **{
        (2, op): ((("number", "number"), "bool"),)
        for op in ("<", "<=", ">", ">=")
    },
    **{
        (2, op): ((("bool", "bool"), "bool"),)
        for op in ("and", "or")
    },
    (3, "?:"): (
        (("bool", "number", "number"), "number"),
        (("bool", "same", "same"), "same"),
    ),
}


def join_value_types(
    left: TypeSpec, right: TypeSpec, *, invariant: bool = False,
) -> TypeSpec | None:
    """Join values, retaining nominal identity and invariant type arguments."""
    if not invariant:
        if left == TypeSpec.primitive("na"):
            return None if right == TypeSpec.primitive("bool") else right
        if right == TypeSpec.primitive("na"):
            return None if left == TypeSpec.primitive("bool") else left
        if (left.kind == right.kind == "primitive"
                and left.name in {"int", "int64", "float", "number"}
                and right.name in {"int", "int64", "float", "number"}):
            return TypeSpec.primitive(
                "float" if "float" in {left.name, right.name}
                else "number" if "number" in {left.name, right.name} else "int",
            )
    if left.kind != right.kind:
        return None
    left_name = "int" if left.kind == "primitive" and left.name == "int64" else left.name
    right_name = "int" if right.kind == "primitive" and right.name == "int64" else right.name
    if left_name is not None and right_name is not None and left_name != right_name:
        return None
    arguments = {}
    for field in ("element", "key", "value"):
        first, second = getattr(left, field), getattr(right, field)
        if first is None or second is None:
            arguments[field] = first or second
        else:
            joined = join_value_types(first, second, invariant=True)
            if joined is None:
                return None
            arguments[field] = joined
    return TypeSpec(left.kind, name=left.name or right.name, **arguments)


def resolve_operator(
    op: str, operands: tuple[TypeSpec | None, ...],
) -> tuple[TypeSpec | None, int | None]:
    """Return a result type, or the first operand exhausting the overloads.

    None is an unresolved constraint, never a numeric default. Bare na can
    acquire a nullable domain; bool has no missing value in Pine v6. Direct
    equality with bare na has a separate syntax restriction in the analyzer.
    """
    # Legacy callable inference also uses non-value sentinels for a result
    # whose family is not established. They cannot constrain a value slot.
    operands = tuple(
        None if actual in {
            TypeSpec.primitive("unknown"), TypeSpec.primitive("void"),
        } else actual for actual in operands
    )
    if op not in {"==", "!="}:
        # Enum identities establish equality constraints; their other
        # operator contracts still have no proven domain here.
        operands = tuple(
            None if actual is not None and actual.kind == "enum" else actual
            for actual in operands
        )
    if op == "?:":
        condition, left, right = operands
        if condition is not None and condition != TypeSpec.primitive("bool"):
            return None, 0
        if left is None or right is None:
            return None, None
        # Storage families do not prove an incompatible selection. In
        # particular, packed colors and legacy float/bool results can share
        # a valid Pine value family. Leave those joins open.
        names = {left.name, right.name}
        if (left.kind == right.kind == "primitive"
                and ("color" in names and names <= {"color", "int", "int64", "float"}
                     or names == {"float", "bool"})):
            return None, None
        if (left.kind == right.kind and left.kind in {"array", "matrix", "map"}
                and left != right):
            # Element compatibility belongs to the existing typed slot check;
            # the selection itself establishes no common collection type.
            return None, None
    candidates = [
        (domains, result, None, False)
        for domains, result in OPERATOR_OVERLOADS[(len(operands), op)]
    ]
    for index, actual in enumerate(operands):
        remaining = []
        for domains, result, bound, missing in candidates:
            domain = domains[index]
            if actual is None:
                # An open slot keeps its candidates, but cannot erase a
                # proven peer that fits no signature at its own position.
                remaining.append((domains, result, bound, missing))
                continue
            is_na = actual == TypeSpec.primitive("na")
            if domain in {"same", "enum"}:
                if domain == "enum" and actual.kind != "enum":
                    continue
                if is_na:
                    if bound != TypeSpec.primitive("bool"):
                        remaining.append((domains, result, bound, True))
                elif not (missing and actual == TypeSpec.primitive("bool")):
                    joined = actual if bound is None else join_value_types(bound, actual)
                    if joined is not None:
                        remaining.append((domains, result, joined, missing))
                continue
            if is_na:
                matches = domain != "bool"
            elif domain == "number":
                matches = actual.kind == "primitive" and actual.name in {
                    "int", "int64", "float", "number",
                }
            elif domain in {"line", "label"}:
                matches = actual == TypeSpec.udt(domain)
            else:
                matches = actual == TypeSpec.primitive(domain)
            if matches:
                remaining.append((domains, result, bound, missing))
        if not remaining:
            return None, index
        candidates = remaining
    if any(actual is None for actual in operands):
        return None, None
    _domains, result, bound, _missing = candidates[0]
    if result == "same":
        return bound, None
    if result == "number":
        numeric = [
            actual for actual in operands
            if actual is not None and actual.name != "bool"
        ]
        result = (
            "float" if any(t.name in {"float", "na"} for t in numeric)
            else "number" if any(t.name == "number" for t in numeric)
            else "int"
        )
    return TypeSpec.primitive(result), None
