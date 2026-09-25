"""Pre-lex pragma extraction for PineForge.

This module recognises ``// @pf-trace name=expr`` line comments in Pine
source and lifts them into a structured list the codegen consumes when
emitting the per-bar instrumentation hook at the bottom of every
``on_bar()``.

Why a pre-pass?
    The :class:`Lexer` strips both block (``/* ... */``) and line
    (``//``) comments before the parser ever sees them, so by the time
    we have an AST the original pragma text is gone. We instead walk
    the raw source once, regex-match each pragma line, then run the
    expression body through the same :class:`Lexer` /
    :class:`Parser` machinery used for normal Pine expressions. This
    keeps a single source of truth for Pine syntax and ensures pragma
    expressions support the full grammar (logical operators, member
    access, function calls, ternaries, ...).

Pragma syntax (kept deliberately strict so unrelated comments are
untouched)::

    // @pf-trace <id>=<expr>

* ``//`` followed by at least one space, then ``@pf-trace``.
* ``<id>`` matches ``[A-Za-z_][A-Za-z0-9_]*`` (the trace label).
* ``<expr>`` is any Pine expression evaluated at script-top scope.

Multiple pragmas may appear on consecutive (or non-consecutive) lines;
their source order is preserved in the output so the codegen emits the
matching ``trace(...)`` calls in the same order. Block comments and
line comments not starting with ``// @pf-trace`` are ignored. Mid-line
trailing pragmas (``x = 1  // @pf-trace ...``) are intentionally NOT
recognised — pragmas must occupy the whole line so they are
unambiguous to read by humans and trivial to grep.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import CompileError, SourceLocation
from .lexer import Lexer
from .limits import TimeBudget, check_ast_depth
from .parser import ParseError, Parser


# Anchored to start/end of line; lexical string spans are excluded below.
# ``\s+`` after
# ``//`` requires at least one space before ``@pf-trace`` (the spec is
# ``// @pf-trace ``, distinct from Pine's ``//@version=N``).
_PRAGMA_RE = re.compile(
    r"^\s*//\s+@pf-trace\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$"
)


class _StringSpanLexer(Lexer):
    """Use the Pine lexer itself to locate lines inside string literals."""

    def __init__(self, source: str, filename: str = "<input>",
                 budget: TimeBudget | None = None) -> None:
        super().__init__(source, filename=filename, budget=budget)
        self.string_lines: set[int] = set()

    def _record_string_lines(self, start_line: int) -> None:
        if self.line > start_line:
            self.string_lines.update(range(start_line + 1, self.line + 1))

    def _read_multiline(self, quote: str, start_line: int, start_col: int) -> None:
        super()._read_multiline(quote, start_line, start_col)
        self._record_string_lines(start_line)

    def _read_quoted(self, quote: str, start_line: int, start_col: int) -> None:
        super()._read_quoted(quote, start_line, start_col)
        self._record_string_lines(start_line)


@dataclass
class PfTracePragma:
    """One ``// @pf-trace name=expr`` annotation extracted from Pine source.

    Attributes:
        name: The trace label (left-hand side of ``=``); matches
            ``[A-Za-z_][A-Za-z0-9_]*`` so it can be safely embedded in
            a C++ string literal without escaping.
        expr_source: Raw Pine expression text (right-hand side of
            ``=``), retained for diagnostics and the test harness.
        expr_node: AST node parsed from ``expr_source`` via the
            standard Pine expression parser. Codegen feeds this through
            ``_visit_expr`` to obtain the C++ form.
        line: 1-based source line where the pragma was found.
    """

    name: str
    expr_source: str
    expr_node: Any
    line: int


def extract_pf_trace_pragmas(source: str, *, filename: str = "<input>",
                            budget: TimeBudget | None = None) -> list[PfTracePragma]:
    """Scan ``source`` for ``// @pf-trace`` line comments.

    Returns the pragmas in source order. The expression on the
    right-hand side of ``=`` is run through the project's own
    :class:`Lexer` followed by :meth:`Parser._parse_expression` so the
    full Pine expression grammar is supported (binary / unary
    operators, ternaries, member access, function calls, subscripts).

    Args:
        source: Raw Pine source text.

    Returns:
        A list of :class:`PfTracePragma` entries in source order. Empty
        list when no pragmas are present (the common case for legacy
        scripts) — callers should treat this as the zero-overhead
        path.
    """
    candidates = [(lineno, match)
                  for lineno, raw in enumerate(source.splitlines(), start=1)
                  if (match := _PRAGMA_RE.match(raw)) is not None]
    if not candidates:
        return []
    lexer = _StringSpanLexer(source, filename=filename, budget=budget)
    try:
        lexer.tokenize()
    except CompileError:
        # This lexical pass only finds string spans. The main Lexer run owns
        # syntax diagnostics; extraction itself has historically accepted
        # arbitrary source text, including malformed block comments.
        pass
    pragmas: list[PfTracePragma] = []
    for lineno, m in candidates:
        if lineno in lexer.string_lines:
            continue
        name = m.group(1)
        expr_source = m.group(2)
        # Reuse the full Pine-source -> AST pipeline by lexing + parsing
        # the expression body in isolation. ``Parser._parse_expression``
        # is the same entry the statement parser uses for RHS values,
        # so anything legal in ``x = <expr>`` is legal here.
        try:
            tokens = Lexer(expr_source, filename=filename, budget=budget).tokenize()
            parser = Parser(tokens, source=expr_source, filename=filename,
                            budget=budget)
            try:
                expr_node = parser._parse_expression()
                parser._expect_statement_end()
            except ParseError as error:
                parser._raise_syntax_error(error)
            check_ast_depth(expr_node, filename)
        except CompileError as exc:
            for diagnostic in exc.diagnostics:
                loc = diagnostic.location
                diagnostic.location = SourceLocation(
                    filename, loc.line + lineno - 1, loc.col, loc.end_col,
                )
            raise CompileError(exc.diagnostics) from exc
        pragmas.append(
            PfTracePragma(
                name=name,
                expr_source=expr_source,
                expr_node=expr_node,
                line=lineno,
            )
        )
    return pragmas
