"""Bounded numeric folding for constructor expressions, without Python execution.

``ast`` only parses syntax here. No expression-supplied object, attribute or
callable is resolved: even ``math.sin`` is a fixed spelling in a local table.
The numeric constructor folder rejects unsupported and non-finite expressions.
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext

from ..ast_nodes import (
    BinOp, BoolLiteral, FuncCall, Identifier, MemberAccess, NaLiteral,
    NumberLiteral, StringLiteral, Ternary, UnaryOp,
)
from ..intrinsic_binding import validate_intrinsic_keywords
from ..limits import MAX_NESTING_DEPTH, MAX_SOURCE_CHARS
from ..signatures import get_intrinsic, get_param_names


def _pine_remainder(left: int | float | bool, right: int | float | bool):
    """Return Pine's remainder, whose sign follows the dividend."""
    if type(left) in (int, bool) and type(right) in (int, bool):
        dividend = int(left)
        divisor = int(right)
        if divisor == 0:
            raise ZeroDivisionError("integer modulo by zero")
        remainder = abs(dividend) % abs(divisor)
        return -remainder if dividend < 0 else remainder
    return math.fmod(float(left), float(right))


_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: _pine_remainder,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _pine_round(value: int | float | bool, precision: int | bool | None = None):
    """Round like Pine, using decimal half-away-from-zero ties."""
    # An integral float's shortest repr can omit significant integer digits.
    if type(value) is float and value.is_integer():
        decimal_value = Decimal(value)
    else:
        decimal_value = Decimal(repr(int(value) if type(value) is bool else value))
    exponent = 0 if precision is None else -int(precision)
    quantum = Decimal(1).scaleb(exponent)
    # quantize needs enough precision for the complete result, including
    # trailing decimal places and large integral values.
    result_digits = max(
        len(decimal_value.as_tuple().digits),
        decimal_value.adjusted() - quantum.as_tuple().exponent + 1,
        1,
    )
    with localcontext() as context:
        context.prec = max(28, result_digits)
        rounded = decimal_value.quantize(quantum, rounding=ROUND_HALF_UP)
    return int(rounded) if precision is None else float(rounded)


_FUNCTIONS = {
    "abs": abs,
    "round": _pine_round,
    "math.abs": abs,
    "math.round": _pine_round,
    # sqrt/ceil/floor previously took a failed import rewrite and did not
    # fold. Keep that domain unchanged: newly admitting sqrt under a numeric
    # fold, for example, can disagree with the existing C++ runtime round.
    "math.pow": math.pow,
    "math.exp": math.exp,
    "math.log": math.log,
    "math.log10": math.log10,
    "math.sin": math.sin,
    "math.cos": math.cos,
    "math.tan": math.tan,
    "math.asin": math.asin,
    "math.acos": math.acos,
    "math.atan": math.atan,
}
_CONSTANTS = {"math.pi": math.pi, "math.e": math.e}

# Folding is optional. Bound parser work, tree traversal and integer growth;
# rejection retains the original expression for the caller's existing guard.
_MAX_SOURCE_LENGTH = 8192
_MAX_NODES = 512
_MAX_DEPTH = 64
_MAX_INTEGER_BITS = 1024
_MAX_ROUND_DIGITS = 1024


def _numeric(value: object) -> int | float | bool:
    if type(value) is bool:
        return value
    if type(value) is int and value.bit_length() <= _MAX_INTEGER_BITS:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError("not a bounded numeric constant")


def _spelling(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "math"):
        return f"math.{node.attr}"
    raise ValueError("not an allowed numeric spelling")


def fold_numeric_expression(
    expression: str, known_values: Mapping[str, object],
) -> int | float | bool | None:
    """Return a numeric constant, or None when the syntax cannot be folded.

    Names resolve only to caller-provided primitive numbers. Calls resolve
    only to the fixed functions above and accept positional numeric arguments.
    Attribute chains, indexing, containers, comprehensions, lambda expressions,
    arbitrary call targets and keyword/unpacked arguments are never evaluated.
    """
    if len(expression) > _MAX_SOURCE_LENGTH:
        return None

    def visit(node: ast.AST, depth: int = 0) -> int | float | bool:
        if depth > _MAX_DEPTH:
            raise ValueError("numeric expression is too deep")
        if isinstance(node, ast.Constant):
            return _numeric(node.value)
        if isinstance(node, ast.Name):
            return _numeric(known_values[node.id])
        if isinstance(node, ast.Attribute):
            return _CONSTANTS[_spelling(node)]
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _numeric(_UNARY[type(node.op)](visit(node.operand, depth + 1)))
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return _numeric(_BINARY[type(node.op)](
                visit(node.left, depth + 1), visit(node.right, depth + 1),
            ))
        if isinstance(node, ast.Call) and not node.keywords:
            spelling = _spelling(node.func)
            if spelling.split(".", 1)[0] in known_values:
                raise ValueError("numeric function name is shadowed")
            function = _FUNCTIONS[spelling]
            args = [visit(arg, depth + 1) for arg in node.args]
            if spelling in ("round", "math.round"):
                # CPython's integer round builds 10**(-ndigits). A small
                # integer operand can still request enormous work, so bound
                # this argument BEFORE dispatch, not only the returned value.
                if not 1 <= len(args) <= 2:
                    raise ValueError("unsupported round argument count")
                if len(args) == 2 and (
                    type(args[1]) not in (int, bool)
                    or abs(args[1]) > _MAX_ROUND_DIGITS
                ):
                    raise ValueError("round precision is not bounded")
            return _numeric(function(*args))
        raise ValueError("not numeric expression syntax")

    try:
        tree = ast.parse(expression, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > _MAX_NODES:
            return None
        return visit(tree.body)
    except (SyntaxError, ValueError, TypeError, KeyError, ArithmeticError,
            RecursionError):
        return None


@dataclass(frozen=True)
class NamedConstant:
    """Keep a declaration enum's spelling beside its Pine value."""

    spelling: str
    value: str | int


_COMPARISONS = {
    "==": operator.eq, "!=": operator.ne, "<": operator.lt,
    "<=": operator.le, ">": operator.gt, ">=": operator.ge,
}
_FLOAT_ARITHMETIC = {"+": operator.add, "-": operator.sub, "*": operator.mul}


def _compare(op, left, right):
    # Keep this in step with _emit_float_relational and the engine's
    # ta_compare_band.hpp. Missing operands make even != false.
    if left is None or right is None:
        return False
    if (type(left) in (int, float) and type(right) in (int, float)
            and (type(left) is float or type(right) is float)):
        left, right = float(left), float(right)
        if math.isnan(left) or math.isnan(right):
            return False
        if left == right or (math.isfinite(left) and math.isfinite(right)
                             and abs(left - right) <= 1e-10):
            return op in ("==", "<=", ">=")
    return _COMPARISONS[op](left, right)


def _float_arithmetic(op, left, right):
    left, right = float(left), float(right)
    if op == "/":
        if right != 0:
            return left / right
        if left == 0 or math.isnan(left):
            return math.nan
        sign = math.copysign(1.0, left) * math.copysign(1.0, right)
        return math.copysign(math.inf, sign)
    if op == "%":
        try:
            return math.fmod(left, right)
        except ValueError:
            return math.nan
    return _FLOAT_ARITHMETIC[op](left, right)


_DECLARATION_FUNCTIONS = {
    "int": int, "float": float, "bool": bool,
    "math.min": min, "math.max": max,
    "math.sqrt": math.sqrt, "math.ceil": math.ceil, "math.floor": math.floor,
    "math.sign": lambda value: (value > 0) - (value < 0),
    "math.avg": lambda values: sum(values) / len(values),
    "math.atan2": math.atan2,
    "math.todegrees": math.degrees, "math.toradians": math.radians,
}
_CONSTANT_CALLS = _FUNCTIONS.keys() | _DECLARATION_FUNCTIONS.keys()
NON_CONSTANT = object()


def _pine_spelling(node) -> str:
    if isinstance(node, Identifier):
        return node.name
    if isinstance(node, MemberAccess):
        return f"{_pine_spelling(node.object)}.{node.member}"
    raise ValueError("not a constant spelling")


def _constant_arguments(call):
    if not call.kwargs:
        return call.args
    spelling = _pine_spelling(call.callee)
    namespace, _, name = spelling.rpartition(".")
    if namespace == "math":
        validate_intrinsic_keywords(get_intrinsic(namespace, name), call.kwargs)
    names = get_param_names(namespace or None, name)
    if names is None or len(call.args) > len(names):
        raise ValueError("not a constant call signature")
    bound = dict(zip(names, call.args))
    for name, argument in call.kwargs.items():
        if name not in names or name in bound:
            raise ValueError("not a constant argument binding")
        bound[name] = argument
    last = max(names.index(name) for name in bound)
    return [bound[name] for name in names[:last + 1]]


def fold_constant_expression(node, known_values: Mapping[str, object],
                             named_constants: Mapping[str, object]):
    """Fold a Pine constant, returning (value, proven).

    All dependencies must be constant, even in an unselected arm. Evaluation
    then follows Pine's lazy boolean and conditional operators. Numeric
    operators and the existing math whitelist use the bounded numeric folder;
    declaration-only casts use their own whitelist and rounding uses the
    shared Pine rounding helper.
    NON_CONSTANT bindings shadow builtins without becoming constant values.
    """
    def constant(expr, depth=0):
        if depth > MAX_NESTING_DEPTH:
            return False
        if isinstance(expr, (NumberLiteral, StringLiteral, BoolLiteral, NaLiteral)):
            return True
        if isinstance(expr, Identifier):
            return known_values.get(expr.name, NON_CONSTANT) is not NON_CONSTANT
        if isinstance(expr, MemberAccess):
            spelling = _pine_spelling(expr)
            return spelling.split(".", 1)[0] not in known_values and (
                spelling in named_constants or spelling in _CONSTANTS)
        if isinstance(expr, UnaryOp):
            return expr.op in ("+", "-", "not") and constant(expr.operand, depth + 1)
        if isinstance(expr, BinOp):
            return expr.op in ("+", "-", "*", "/", "%", "and", "or", *_COMPARISONS) and (
                constant(expr.left, depth + 1) and constant(expr.right, depth + 1))
        if isinstance(expr, Ternary):
            return (constant(expr.condition, depth + 1)
                    and constant(expr.true_val, depth + 1)
                    and constant(expr.false_val, depth + 1))
        if isinstance(expr, FuncCall):
            spelling = _pine_spelling(expr.callee)
            return (spelling in _CONSTANT_CALLS
                    and spelling.split(".", 1)[0] not in known_values
                    and all(constant(arg, depth + 1) for arg in _constant_arguments(expr)))
        return False

    def primitive(value):
        return value.value if isinstance(value, NamedConstant) else value

    def boolean(expr):
        value = primitive(visit(expr))
        if type(value) is not bool:
            raise ValueError("not a constant bool")
        return value

    def numeric(value):
        value = primitive(value)
        if type(value) not in (int, float):
            raise ValueError("not a constant number")
        return _numeric(value)

    def arithmetic_operand(value):
        # The comparison path allows nonfinite floats but keeps the same
        # integer size bound as other arithmetic.
        return value if type(value) is float else numeric(value)

    # Keep type inspection separate from evaluation so an unselected arm
    # contributes its numeric type without being evaluated. Memoize by
    # syntax identity so a chain of nested selections is still linear.
    float_types = {}

    def has_float_type(expr):
        key = id(expr)
        if key not in float_types:
            float_types[key] = infer_float_type(expr)
        return float_types[key]

    def infer_float_type(expr):
        if isinstance(expr, NumberLiteral):
            return type(expr.value) is float
        if isinstance(expr, (Identifier, MemberAccess)):
            return type(primitive(visit(expr))) is float
        if isinstance(expr, NaLiteral):
            return True
        if isinstance(expr, UnaryOp):
            return expr.op in ("+", "-") and has_float_type(expr.operand)
        if isinstance(expr, BinOp):
            return expr.op in ("/", "%") or (expr.op in ("+", "-", "*") and (
                has_float_type(expr.left) or has_float_type(expr.right)))
        if isinstance(expr, Ternary):
            return has_float_type(expr.true_val) or has_float_type(expr.false_val)
        if isinstance(expr, FuncCall):
            spelling = _pine_spelling(expr.callee)
            if spelling in ("round", "math.round"):
                return len(_constant_arguments(expr)) == 2
            if spelling in ("abs", "math.abs"):
                return any(has_float_type(arg) for arg in _constant_arguments(expr))
            return spelling not in ("int", "bool", "math.ceil", "math.floor")
        return False

    def visit(expr, *, comparison_operand=False):
        if isinstance(expr, (NumberLiteral, StringLiteral, BoolLiteral)):
            return expr.value
        if isinstance(expr, NaLiteral):
            return None
        if isinstance(expr, Identifier):
            return known_values[expr.name]
        if isinstance(expr, MemberAccess):
            spelling = _pine_spelling(expr)
            return named_constants[spelling] if spelling in named_constants else _CONSTANTS[spelling]
        if isinstance(expr, UnaryOp):
            if expr.op == "not":
                return not boolean(expr.operand)
            operand = visit(expr.operand, comparison_operand=comparison_operand)
            if operand is None:
                return None
            if comparison_operand and type(operand) is float:
                return operand if expr.op == "+" else -operand
            result = fold_numeric_expression(f"{expr.op}value", {"value": numeric(operand)})
        elif isinstance(expr, Ternary):
            arm = expr.true_val if boolean(expr.condition) else expr.false_val
            value = visit(arm, comparison_operand=comparison_operand)
            if comparison_operand and type(value) is int and has_float_type(expr):
                return float(value)
            return value
        elif isinstance(expr, BinOp):
            if expr.op == "and":
                return boolean(expr.left) and boolean(expr.right)
            if expr.op == "or":
                return boolean(expr.left) or boolean(expr.right)
            comparing = expr.op in _COMPARISONS
            ieee = comparing or comparison_operand
            left = primitive(visit(expr.left, comparison_operand=ieee))
            right = primitive(visit(expr.right, comparison_operand=ieee))
            if comparing:
                return _compare(expr.op, left, right)
            if left is None or right is None:
                return None
            if expr.op == "+" and isinstance(left, str) and isinstance(right, str):
                if len(left) + len(right) > MAX_SOURCE_CHARS:
                    raise ValueError("constant string is too long")
                return left + right
            if (comparison_operand
                    and type(left) in (int, float) and type(right) in (int, float)
                    and (expr.op in ("/", "%") or float in (type(left), type(right)))):
                # Comparisons consume IEEE values directly; the general
                # numeric folder still only returns finite results.
                return _float_arithmetic(
                    expr.op, arithmetic_operand(left), arithmetic_operand(right),
                )
            left, right = numeric(left), numeric(right)
            result = fold_numeric_expression(f"left {expr.op} right", {"left": left, "right": right})
        elif isinstance(expr, FuncCall):
            spelling = _pine_spelling(expr.callee)
            values = [
                primitive(visit(arg, comparison_operand=comparison_operand))
                for arg in _constant_arguments(expr)
            ]
            if spelling == "bool" and len(values) == 1:
                if values[0] is not None and type(values[0]) not in (int, float, bool):
                    raise ValueError("not a constant numeric cast")
                if comparison_operand and type(values[0]) is float and math.isnan(values[0]):
                    return False
                return bool(values[0])
            if any(value is None for value in values):
                return None
            if comparison_operand and len(values) == 1 and type(values[0]) is float:
                if spelling == "float":
                    return values[0]
                # At the lower endpoint the cast succeeds but produces the
                # sentinel that the relational wrapper also rejects.
                if spelling == "int" and not -2**63 < values[0] < 2**63:
                    return None
            if comparison_operand and values and spelling in ("math.min", "math.max", "math.sign"):
                args = [float(arithmetic_operand(value)) for value in values]
                if any(math.isnan(value) for value in args):
                    return math.nan
                if spelling == "math.sign" and len(args) == 1:
                    return float(_DECLARATION_FUNCTIONS[spelling](args[0]))
                if spelling != "math.sign":
                    return _DECLARATION_FUNCTIONS[spelling](args)
            args = [numeric(value) for value in values]
            if spelling in ("round", "math.round"):
                if not 1 <= len(args) <= 2 or (
                    len(args) == 2 and (type(args[1]) is not int
                                       or abs(args[1]) > _MAX_ROUND_DIGITS)
                ):
                    raise ValueError("unsupported constant rounding precision")
                return _numeric(_pine_round(*args))
            if spelling in _DECLARATION_FUNCTIONS:
                if spelling in ("math.min", "math.max", "math.avg"):
                    if not args:
                        raise ValueError("missing constant arguments")
                    return _numeric(_DECLARATION_FUNCTIONS[spelling](args))
                return _numeric(_DECLARATION_FUNCTIONS[spelling](*args))
            bindings = {f"arg{index}": value for index, value in enumerate(args)}
            result = fold_numeric_expression(f"{spelling}({','.join(bindings)})", bindings)
        else:
            raise ValueError("not a constant expression")
        if result is None:
            raise ValueError("not a bounded constant expression")
        return result

    try:
        if constant(node):
            return visit(node), True
    except (ValueError, TypeError, KeyError, ArithmeticError, RecursionError):
        pass
    return None, False
