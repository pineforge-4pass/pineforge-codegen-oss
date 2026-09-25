"""Portable limits for untrusted Pine source.

These checks run in both CPython and Pyodide.  A limit exists only to turn a
crash or a hang into a located ``CompileError``; where TradingView documents a
limit, ours is at least as large.  The structural limits are deterministic;
the elapsed-time limit is a cooperative last resort for work that remains
expensive below them.
"""

from __future__ import annotations

from dataclasses import fields
import sys
from time import monotonic

from .ast_nodes import ASTNode, TypeField
from .errors import CompileError, Diagnostic, Level, Phase, SourceLocation


# TradingView: "The size of the compilation request for a script cannot exceed
# 5MB" (Pine Script v6 User Manual, Limitations).  A source of 5 MiB characters
# is no larger than that request under either reading of "MB".  TradingView
# measures script size in compiled tokens, not source lines or statements, so
# there is no separate statement or per-statement budget: statements and
# argument lists are processed iteratively and are bounded by this one.
MAX_SOURCE_CHARS = 5 * 1024 * 1024

# Nesting of every kind: brackets, blocks, prefix operators, ``?:`` and
# ``else if`` chains, and the syntax tree that the recursive passes walk.
# TradingView documents no nesting limit.  The public corpus and the gate
# fixtures nest at most 10 levels.  Before this budget, Python's default
# recursion limit stopped the transpiler at about 500 levels of ``?:``,
# ``else if`` or operator chains (and 90 of brackets); Pyodide on Node's
# default stack dies with a fatal RangeError at about 2,000 levels of nested
# calls, blocks or ``else if`` branches, and on freeing a tree about 4,000
# levels deep.
MAX_NESTING_DEPTH = 512

# The parser's recursive descent spends up to 12 Python frames per nesting
# level (a nested call), the later passes a few per tree level; this is more
# than three times the most either needs.
RECURSION_HEADROOM = 40 * MAX_NESTING_DEPTH

# TradingView: "A two-minute limit is imposed on compilation time".
MAX_TRANSPILE_SECONDS = 120


def ensure_recursion_headroom() -> None:
    """Raise Python's recursion limit to cover the nesting budget.

    The limit is only ever raised, never lowered, so concurrent callers cannot
    undercut one another.  CPython 3.11+ and Pyodide keep Python-to-Python
    calls off the C stack.
    """
    if sys.getrecursionlimit() < RECURSION_HEADROOM:
        sys.setrecursionlimit(RECURSION_HEADROOM)


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


def syntax_children(node: ASTNode):
    """Yield the AST nodes directly under ``node``, without recursion.

    Lists, tuples, dicts and TypeField defaults are flattened.  Annotations
    are analysis metadata, not syntax children; excluding them also avoids
    re-walking call_arg_order aliases and any future back edges.
    """
    stack = [getattr(node, item.name) for item in fields(node)
             if item.name not in {"loc", "annotations"}]
    while stack:
        value = stack.pop()
        if isinstance(value, ASTNode):
            yield value
        elif isinstance(value, TypeField):
            stack.append(value.default)
        elif isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)


def iter_ast_nodes(root: ASTNode):
    """Walk syntax children without using Python's call stack."""
    stack: list[tuple[ASTNode, int]] = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        yield node, depth
        stack.extend((child, depth + 1) for child in syntax_children(node))


def check_ast_depth(root: ASTNode, filename: str) -> None:
    """Catch long left-associative chains before recursive analysis/codegen."""
    for node, depth in iter_ast_nodes(root):
        if depth > MAX_NESTING_DEPTH:
            raise limit_error(
                f"AST nesting depth exceeds {MAX_NESTING_DEPTH} nodes.",
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
