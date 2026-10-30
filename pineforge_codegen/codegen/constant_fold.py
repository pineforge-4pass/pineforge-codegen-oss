"""Bounded numeric folding for constructor expressions, without Python execution.

``ast`` only parses syntax here. No expression-supplied object, attribute or
callable is resolved: even ``math.sin`` is a fixed spelling in a local table.
Unsupported or non-finite expressions remain on the existing runtime/error path.
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Mapping
from dataclasses import dataclass

from ..ast_nodes import (
    BinOp, BoolLiteral, FuncCall, Identifier, MemberAccess, NaLiteral,
    NumberLiteral, StringLiteral, Ternary, UnaryOp,
)
from ..limits import MAX_NESTING_DEPTH, MAX_SOURCE_CHARS
from ..signatures import get_param_names


_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "math.abs": abs,
    # Keep the existing folder's rounding behavior; this repair does not
    # change numeric semantics to match a different rounding convention.
    "math.round": round,
    # sqrt/ceil/floor previously took a failed import rewrite and did not
    # fold. Keep that domain unchanged: newly admitting sqrt under Python's
    # round, for example, can disagree with the existing C++ runtime round.
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


def _round_ties_up(value):
    lower = math.floor(value)
    return lower + int(value - lower >= 0.5)


def fold_constant_expression(node, known_values: Mapping[str, object],
                             named_constants: Mapping[str, object]):
    """Fold a Pine constant, returning (value, proven).

    All dependencies must be constant, even in an unselected arm. Evaluation
    then follows Pine's lazy boolean and conditional operators. Numeric
    operators and the existing math whitelist use the bounded numeric folder;
    declaration-only casts and rounding leave constructor folding unchanged.
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

    def visit(expr):
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
            operand = visit(expr.operand)
            if operand is None:
                return None
            result = fold_numeric_expression(f"{expr.op}value", {"value": numeric(operand)})
        elif isinstance(expr, Ternary):
            return visit(expr.true_val if boolean(expr.condition) else expr.false_val)
        elif isinstance(expr, BinOp):
            if expr.op == "and":
                return boolean(expr.left) and boolean(expr.right)
            if expr.op == "or":
                return boolean(expr.left) or boolean(expr.right)
            left, right = primitive(visit(expr.left)), primitive(visit(expr.right))
            if expr.op in _COMPARISONS:
                if left is None or right is None:
                    return False
                return _COMPARISONS[expr.op](left, right)
            if left is None or right is None:
                return None
            if expr.op == "+" and isinstance(left, str) and isinstance(right, str):
                if len(left) + len(right) > MAX_SOURCE_CHARS:
                    raise ValueError("constant string is too long")
                return left + right
            left, right = numeric(left), numeric(right)
            result = fold_numeric_expression(f"left {expr.op} right", {"left": left, "right": right})
        elif isinstance(expr, FuncCall):
            spelling = _pine_spelling(expr.callee)
            values = [primitive(visit(arg)) for arg in _constant_arguments(expr)]
            if spelling == "bool" and len(values) == 1:
                if values[0] is not None and type(values[0]) not in (int, float, bool):
                    raise ValueError("not a constant numeric cast")
                return bool(values[0])
            if any(value is None for value in values):
                return None
            args = [numeric(value) for value in values]
            if spelling in ("round", "math.round"):
                if not 1 <= len(args) <= 2 or (
                    len(args) == 2 and (type(args[1]) is not int
                                       or abs(args[1]) > _MAX_ROUND_DIGITS)
                ):
                    raise ValueError("unsupported constant rounding precision")
                # Pine rounds ties toward positive infinity.
                if len(args) == 1:
                    return _numeric(_round_ties_up(args[0]))
                factor = 10.0 ** args[1]
                return _numeric(_round_ties_up(args[0] * factor) / factor)
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
