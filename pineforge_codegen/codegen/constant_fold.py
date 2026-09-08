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
    "math.sqrt": math.sqrt,
    "math.ceil": math.ceil,
    "math.floor": math.floor,
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
