"""AST node definitions for PineScript v6."""

from dataclasses import dataclass, field
from typing import Any

from pineforge_codegen.errors import SourceLocation


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

@dataclass
class ASTNode:
    """Base class for all AST nodes.

    *loc* carries the source span that produced this node; it is always set by
    the new parser but defaults to None so that old code (Tasks 4-6 are still
    pending) does not immediately break.

    *annotations* is an open-ended dict used by later compiler phases (type
    inference, optimiser, etc.) to attach arbitrary metadata without touching
    the node definition.
    """
    loc: SourceLocation | None = field(default=None, compare=False)
    annotations: dict | None = field(default=None, compare=False)


# ---------------------------------------------------------------------------
# Top-level / structural nodes
# ---------------------------------------------------------------------------

@dataclass
class Program(ASTNode):
    body: list = field(default_factory=list)
    version: int | None = None


@dataclass
class StrategyDecl(ASTNode):
    args: list = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)


@dataclass
class ImportStmt(ASTNode):
    """import <path> — parsed for error reporting."""
    path: str = ""


# ---------------------------------------------------------------------------
# Declaration / assignment nodes
# ---------------------------------------------------------------------------

@dataclass
class VarDecl(ASTNode):
    """Variable declaration: [var|varip] [type] name = value"""
    name: str = ""
    value: Any = None
    is_var: bool = False
    is_varip: bool = False
    type_hint: str | None = None


@dataclass
class Assignment(ASTNode):
    """Reassignment / compound assignment: target <op> value.

    *op* is one of:  ``:=``  ``+=``  ``-=``  ``*=``  ``/=``  ``%=``
    *target* is an expression (Identifier, Subscript, MemberAccess, …).
    """
    target: Any = None
    op: str = ":="
    value: Any = None


@dataclass
class TupleAssign(ASTNode):
    """[a, b, c] = expr"""
    names: list[str] = field(default_factory=list)
    value: Any = None


# ---------------------------------------------------------------------------
# Control-flow nodes
# ---------------------------------------------------------------------------

@dataclass
class IfStmt(ASTNode):
    condition: Any = None
    body: list = field(default_factory=list)
    else_body: list = field(default_factory=list)


@dataclass
class ForStmt(ASTNode):
    """for var = start to end [by step]"""
    var: str = ""
    start: Any = None
    end: Any = None
    step: Any | None = None
    body: list = field(default_factory=list)


@dataclass
class ForInStmt(ASTNode):
    """for x in iterable  /  for [a, b] in iterable"""
    var: str | None = None          # single variable name, or None if destructured
    vars: list[str] | None = None   # destructured variable names [a, b]
    iterable: ASTNode | None = None
    body: list = field(default_factory=list)


@dataclass
class WhileStmt(ASTNode):
    condition: Any = None
    body: list = field(default_factory=list)


@dataclass
class SwitchStmt(ASTNode):
    expr: Any | None = None
    cases: list = field(default_factory=list)   # list of (expr|None, body_stmts)
    default_body: list = field(default_factory=list)


@dataclass
class BreakStmt(ASTNode):
    pass


@dataclass
class ContinueStmt(ASTNode):
    pass


# ---------------------------------------------------------------------------
# Function definition
# ---------------------------------------------------------------------------

@dataclass
class FuncDef(ASTNode):
    name: str = ""
    params: list[str] = field(default_factory=list)
    body: list = field(default_factory=list)
    is_single_expr: bool = False


# ---------------------------------------------------------------------------
# Expression wrapper (statement context)
# ---------------------------------------------------------------------------

@dataclass
class ExprStmt(ASTNode):
    expr: Any = None


# ---------------------------------------------------------------------------
# Expression nodes
# ---------------------------------------------------------------------------

@dataclass
class BinOp(ASTNode):
    """Binary operation.  Field order: left, op, right."""
    left: Any = None
    op: str = ""
    right: Any = None


@dataclass
class UnaryOp(ASTNode):
    op: str = ""
    operand: Any = None


@dataclass
class Ternary(ASTNode):
    condition: Any = None
    true_val: Any = None
    false_val: Any = None


@dataclass
class FuncCall(ASTNode):
    """Function / method call.

    *callee* is an expression node — typically an Identifier (``foo``) or a
    MemberAccess (``strategy.entry``).  The old ``name`` + ``namespace`` split
    is replaced by a single expression so that arbitrary call targets are
    representable.
    """
    callee: Any = None
    args: list = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)


@dataclass
class Subscript(ASTNode):
    """History referencing / index access: expr[offset]"""
    object: Any = None
    index: Any = None


@dataclass
class Identifier(ASTNode):
    name: str = ""


@dataclass
class MemberAccess(ASTNode):
    object: Any = None
    member: str = ""


@dataclass
class TypeAnnotation(ASTNode):
    """Type name used as an expression node (e.g. in cast syntax)."""
    type_name: str = ""


# ---------------------------------------------------------------------------
# Literal nodes
# ---------------------------------------------------------------------------

@dataclass
class NumberLiteral(ASTNode):
    value: int | float = 0


@dataclass
class StringLiteral(ASTNode):
    value: str = ""


@dataclass
class BoolLiteral(ASTNode):
    value: bool = False


@dataclass
class NaLiteral(ASTNode):
    pass


@dataclass
class ColorLiteral(ASTNode):
    """Hex colour literal: ``#rrggbb`` or ``#rrggbbaa``."""
    value: str = ""


@dataclass
class TupleLiteral(ASTNode):
    """[a, b, c] tuple literal used in function returns."""
    elements: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# User-Defined Type (UDT) nodes
# ---------------------------------------------------------------------------

@dataclass
class TypeField:
    """One field in a type declaration."""
    type_name: str = ""
    name: str = ""
    default: ASTNode | None = None

@dataclass
class TypeDecl(ASTNode):
    """type MyType\n    float field = default"""
    name: str = ""
    fields: list = field(default_factory=list)  # list of TypeField

@dataclass
class EnumDecl(ASTNode):
    """User-defined enum (derived type). Members are ordered; optional RHS per field.

    TradingView allows `member = <expr>` so field “payload” (titles, IANA strings, …)
    is separate from the member’s ordinal — the language type is still the enum, not
    the RHS type.
    """
    name: str = ""
    members: list = field(default_factory=list)  # list of str (declaration order)
    member_values: dict[str, ASTNode] = field(default_factory=dict)

@dataclass
class MethodDef(ASTNode):
    """method myMethod(self, param) => body"""
    name: str = ""
    type_name: str = ""       # the type this method belongs to
    params: list = field(default_factory=list)  # list of str (first is self)
    body: list = field(default_factory=list)
    is_single_expr: bool = False
