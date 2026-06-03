"""Error / warning emission and diagnostic-string helpers for the analyzer.

The historic ``analyzer.py`` had a small cluster of helpers that
all dealt with one concern: turning a problem the analyzer noticed
into a ``Diagnostic`` (warning) or ``CompileError`` (fatal), with
useful source locations and rough printable forms of expressions.
This mixin collects them in one place. ``Analyzer`` mixes
``DiagnosticsHelper`` in alongside the other analyzer mixins.

Mixin contract -- host class must provide the following attributes:

- ``self._diagnostics`` (``list[Diagnostic]``): warning sink that
  ``_warn`` appends to. Errors are raised as ``CompileError`` and
  do not pass through this list.
- ``self._filename`` (``str``): file name used as the fallback
  ``SourceLocation`` when a caller does not pass one.
- ``self._symbols`` (``SymbolTable``): consulted by
  ``_warn_if_unknown_source_id`` to skip the warning when the
  identifier is a user-defined series variable.

The mixin avoids importing from ``base.py`` to stay free of import
cycles. ``BAR_FIELDS`` and ``tv_input_choices`` come from sibling
modules, mirroring how the host class consumes them.
"""

from __future__ import annotations

from ..ast_nodes import (
    ASTNode, BinOp, BoolLiteral, FuncCall, Identifier, MemberAccess,
    NaLiteral, NumberLiteral, StringLiteral, Subscript, Ternary, UnaryOp,
)
from ..errors import CompileError, Diagnostic, Level, Phase, SourceLocation
from .. import tv_input_choices as tv_in
from .tables import BAR_FIELDS


class DiagnosticsHelper:
    """Diagnostic emission + location + expression-stringification helpers.

    Mixed into ``Analyzer``; not meant to be instantiated standalone.
    The methods here are the only path the analyzer uses to record a
    warning or raise a compile error, so keeping them together makes
    the diagnostic surface easy to audit.
    """

    def _error(self, message: str, loc: SourceLocation | None = None) -> None:
        """Raise a compile error."""
        if loc is None:
            loc = SourceLocation(file=self._filename, line=1, col=1, end_col=1)
        diag = Diagnostic(
            level=Level.ERROR,
            phase=Phase.ANALYZER,
            location=loc,
            message=message,
        )
        raise CompileError([diag])

    def _warn(self, message: str, loc: SourceLocation | None = None) -> None:
        """Record a warning diagnostic."""
        if loc is None:
            loc = SourceLocation(file=self._filename, line=1, col=1, end_col=1)
        diag = Diagnostic(
            level=Level.WARNING,
            phase=Phase.ANALYZER,
            location=loc,
            message=message,
        )
        self._diagnostics.append(diag)

    def _input_diag_loc(self, node: FuncCall, expr: ASTNode | None) -> SourceLocation:
        if expr is not None and getattr(expr, "loc", None):
            return expr.loc
        if node.loc:
            return node.loc
        return SourceLocation(file=self._filename, line=1, col=1, end_col=1)

    def _warn_if_unknown_source_id(
        self, name: str, expr: ASTNode, node: FuncCall,
    ) -> None:
        if name in tv_in.INPUT_SOURCE_SERIES_IDS or name in BAR_FIELDS:
            return
        sym = self._symbols.resolve(name)
        if sym is not None and getattr(sym, "is_series", False):
            return
        loc = self._input_diag_loc(node, expr)
        self._warn(
            f"input defval '{name}' is not a known chart series (open, high, low, close, …); "
            "verify spelling or use a series variable.",
            loc,
        )

    def _expr_to_str(self, node: ASTNode) -> str:
        """Convert an expression node to a rough string representation."""
        if isinstance(node, NumberLiteral):
            return str(node.value)
        if isinstance(node, StringLiteral):
            return f'"{node.value}"'
        if isinstance(node, BoolLiteral):
            return "true" if node.value else "false"
        if isinstance(node, NaLiteral):
            return "na"
        if isinstance(node, Identifier):
            return node.name
        if isinstance(node, MemberAccess):
            return f"{self._expr_to_str(node.object)}.{node.member}"
        if isinstance(node, BinOp):
            return f"{self._expr_to_str(node.left)} {node.op} {self._expr_to_str(node.right)}"
        if isinstance(node, UnaryOp):
            return f"{node.op}{self._expr_to_str(node.operand)}"
        if isinstance(node, FuncCall):
            args = ", ".join(self._expr_to_str(a) for a in node.args)
            callee_str = self._expr_to_str(node.callee)
            return f"{callee_str}({args})"
        if isinstance(node, Subscript):
            return f"{self._expr_to_str(node.object)}[{self._expr_to_str(node.index)}]"
        if isinstance(node, Ternary):
            return f"{self._expr_to_str(node.condition)} ? {self._expr_to_str(node.true_val)} : {self._expr_to_str(node.false_val)}"
        return "<?>"
