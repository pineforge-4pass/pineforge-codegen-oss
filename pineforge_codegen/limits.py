"""Portable limits for untrusted Pine source.

These checks run in both CPython and Pyodide.  The structural limits are
deterministic; the elapsed-time limit is a cooperative last resort for work
that remains expensive below the structural limits.
"""

from __future__ import annotations

from dataclasses import fields
from time import monotonic

from .ast_nodes import ASTNode, TypeField
from .errors import CompileError, Diagnostic, Level, Phase, SourceLocation


MAX_SOURCE_CHARS = 128 * 1024
MAX_EXPRESSION_TOKENS = 256
MAX_EXPRESSION_CHARS = 4096
MAX_DELIMITER_DEPTH = 32
MAX_BLOCK_DEPTH = 32
MAX_AST_DEPTH = 64
MAX_STATEMENTS = 1024
MAX_TRANSPILE_SECONDS = 30


def limit_error(message: str, location: SourceLocation, phase: Phase) -> CompileError:
    return CompileError([Diagnostic(
        level=Level.ERROR, phase=phase, location=location, message=message,
    )])


def check_source_size(source: str, filename: str) -> None:
    """Reject before pragma extraction or lexing can scan an oversized input."""
    if len(source) <= MAX_SOURCE_CHARS:
        return
    prefix = source[:MAX_SOURCE_CHARS]
    line = prefix.count("\n") + 1
    col = MAX_SOURCE_CHARS - prefix.rfind("\n")
    raise limit_error(
        f"Source size exceeds {MAX_SOURCE_CHARS} characters.",
        SourceLocation(filename, line, col, col + 1), Phase.LEXER,
    )


def iter_ast_nodes(root: ASTNode):
    """Walk syntax children without using Python's call stack.

    Annotations are analysis metadata, not syntax children.  Excluding them
    also avoids re-walking call_arg_order aliases and any future back edges.
    """
    stack: list[tuple[object, int]] = [(root, 1)]
    while stack:
        value, depth = stack.pop()
        if isinstance(value, ASTNode):
            yield value, depth
            for item in fields(value):
                if item.name not in {"loc", "annotations"}:
                    stack.append((getattr(value, item.name), depth + 1))
        elif isinstance(value, TypeField):
            stack.append((value.default, depth))
        elif isinstance(value, dict):
            stack.extend((child, depth) for child in value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend((child, depth) for child in value)


def check_ast_depth(root: ASTNode, filename: str) -> None:
    """Catch long left-associative chains before recursive analysis/codegen."""
    for node, depth in iter_ast_nodes(root):
        if depth > MAX_AST_DEPTH:
            raise limit_error(
                f"AST nesting depth exceeds {MAX_AST_DEPTH} nodes.",
                node.loc or SourceLocation(filename, 1, 1, 2), Phase.PARSER,
            )


class TimeBudget:
    """A cooperative wall-clock guard shared by the transpiler passes."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.deadline = monotonic() + MAX_TRANSPILE_SECONDS
        self.last_location = SourceLocation(filename, 1, 1, 2)

    def check(self, location: SourceLocation | None = None,
              phase: Phase = Phase.PARSER) -> None:
        if location is not None:
            self.last_location = location
        if monotonic() >= self.deadline:
            raise limit_error(
                f"Transpilation time exceeds {MAX_TRANSPILE_SECONDS} seconds.",
                self.last_location, phase,
            )
