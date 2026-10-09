from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Level(Enum):
    ERROR = "error"
    WARNING = "warning"
    NOTE = "note"


class Phase(Enum):
    LEXER = "LEXER"
    PARSER = "PARSER"
    ANALYZER = "ANALYZER"
    CODEGEN = "CODEGEN"


@dataclass
class SourceLocation:
    file: str
    line: int
    col: int
    end_col: int


@dataclass
class Diagnostic:
    level: Level
    phase: Phase
    location: SourceLocation
    message: str
    hint: str | None = None

    # The stable code (``PF-E1203`` / ``PF-W0412``) and named arguments of the
    # catalog template the English ``message`` and ``hint`` render from
    # (``diagnostic_codes``, ``diagnostics_catalog.json``). Both are read off
    # the text when first asked for, after the transpile: the text and the
    # C++ never depend on them.
    @property
    def code(self) -> str:
        return self._coded()[0]

    @property
    def args(self) -> dict:
        return self._coded()[1]

    @property
    def user_message(self) -> str:
        """The catalog's short ICU template, with values supplied by ``args``.

        This is intentionally not rendered English: receivers translate by
        stable code or render the template with the existing named arguments.
        """
        from .diagnostic_codes import _user_message_template
        return _user_message_template(self.code)

    def _coded(self) -> tuple[str, dict]:
        severity = getattr(self.level, "value", self.level)
        key = (severity, self.message, self.hint)
        cached = self.__dict__.get("_pf_coded")
        if cached is None or cached[0] != key:
            from .diagnostic_codes import classify
            cached = (key, classify(severity, self.message, self.hint))
            self.__dict__["_pf_coded"] = cached
        return cached[1]


class CompileError(Exception):
    def __init__(self, diagnostics: list[Diagnostic]):
        self.diagnostics = diagnostics
        # Build a plain-text summary for the base Exception message. Each
        # diagnostic is prefixed with its ``file:line:col`` so the location is
        # reachable from ``str(err)`` alone — a bare
        # ``except CompileError as e: print(e)`` must not swallow the line
        # number. (The rich rustc-style rendering is still available via
        # :meth:`format`.)
        messages = []
        for d in diagnostics:
            loc = d.location
            if loc is not None:
                messages.append(f"{loc.file}:{loc.line}:{loc.col}: {d.message}")
            else:
                messages.append(d.message)
        super().__init__("; ".join(messages))

    def format(self, source: str) -> str:
        """Format diagnostics with source context, rustc-style."""
        lines = source.splitlines()
        parts: list[str] = []

        for d in self.diagnostics:
            loc = d.location
            level_str = d.level.value          # "error", "warning" or "note"
            phase_str = d.phase.value          # "ANALYZER", etc.

            # Header: error[ANALYZER]: message
            header = f"{level_str}[{phase_str}]: {d.message}"

            # Arrow line: --> file:line:col  (rustc-style)
            arrow = f"  --> {loc.file}:{loc.line}:{loc.col}"

            # Gutter width based on line number digits
            gutter_width = len(str(loc.line))
            gutter = " " * gutter_width

            separator = f"   {gutter}|"

            # Source line (1-based indexing)
            source_line = ""
            if 1 <= loc.line <= len(lines):
                source_line = lines[loc.line - 1]

            # Build the underline: spaces up to col, then ^ for the span
            # col is 1-based
            underline_start = loc.col - 1  # 0-based
            underline_len = max(1, loc.end_col - loc.col)
            underline = " " * underline_start + "^" * underline_len

            code_line = f" {loc.line} | {source_line}"
            point_line = f"   {gutter}| {underline}"

            block = "\n".join([header, arrow, separator, code_line, point_line])

            # Optional hint
            if d.hint:
                block += f"\n   {gutter}= hint: {d.hint}"

            parts.append(block)

        return "\n\n".join(parts)
