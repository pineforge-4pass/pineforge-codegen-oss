from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Level(Enum):
    ERROR = "error"
    WARNING = "warning"


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
            level_str = d.level.value          # "error" or "warning"
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
