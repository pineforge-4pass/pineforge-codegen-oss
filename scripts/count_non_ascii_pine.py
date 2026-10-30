#!/usr/bin/env python3
"""Count scripts containing non-ASCII identifiers, string literals or comments.

Only explicitly named roots are read. Each population defaults to recursive
``*.pine`` discovery; ``--pattern NAME GLOB`` can restrict it to strategy files.
``--expect NAME COUNT`` makes a missing or incomplete population fail visibly.
The single JSON document on stdout includes totals and per-file byte counts.
This measures source spelling, without transpiling or compiling a script.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pineforge_codegen.errors import CompileError
from pineforge_codegen.lexer import Lexer


CATEGORIES = ("identifier", "string_literal", "comment")


class _PopulationLexer(Lexer):
    """Observe raw source regions using the production lexer's boundaries."""

    def __init__(self, source: str, filename: str):
        self.counts = {kind: {"lexemes": 0, "utf8_bytes": 0} for kind in CATEGORIES}
        super().__init__(source, filename=filename)

    def _record(self, kind: str, start: int) -> None:
        raw = self.source[start:self.pos]
        byte_count = sum(len(ch.encode("utf-8")) for ch in raw if not ch.isascii())
        if byte_count:
            self.counts[kind]["lexemes"] += 1
            self.counts[kind]["utf8_bytes"] += byte_count

    def _read_ident(self, start_line: int, start_col: int) -> None:
        start = self.pos
        super()._read_ident(start_line, start_col)
        self._record("identifier", start)

    def _read_quoted(self, quote: str, start_line: int, start_col: int) -> None:
        start = self.pos
        super()._read_quoted(quote, start_line, start_col)
        self._record("string_literal", start)

    def _read_multiline(self, quote: str, start_line: int, start_col: int) -> None:
        start = self.pos
        super()._read_multiline(quote, start_line, start_col)
        self._record("string_literal", start)

    def _skip_line(self) -> None:
        # Full-line comments (including pragmas) take this lexer path.
        start = self.pos
        super()._skip_line()
        self._record("comment", start)

    def _skip_comment(self) -> None:
        # Comments after an expression take this separate lexer path.
        start = self.pos
        super()._skip_comment()
        self._record("comment", start)


def scan_source(source: str, filename: str = "<input>") -> dict:
    """Count non-ASCII source bytes by lexical region, including failed lexes."""
    total = sum(len(ch.encode("utf-8")) for ch in source if not ch.isascii())
    counts = {kind: {"lexemes": 0, "utf8_bytes": 0} for kind in CATEGORIES}
    error = None
    lexer = None
    try:
        lexer = _PopulationLexer(source, filename)
        lexer.tokenize()
    except CompileError as exc:
        error = str(exc)
    if lexer is not None:
        counts = lexer.counts
    other = total - sum(row["utf8_bytes"] for row in counts.values())
    return {
        "non_ascii_utf8_bytes": total,
        **counts,
        "other_utf8_bytes": other,
        "lexer_error": error,
    }


def count_population(name: str, root: Path, pattern: str,
                     expected: int | None = None) -> dict:
    """Summarize exactly the files discovered under one explicitly named root."""
    if not root.is_dir():
        raise ValueError(f"population root is not a directory: {root}")
    files = []
    errors = []
    for path in sorted(root.rglob(pattern)):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        try:
            source = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append({"path": relative, "error": str(exc)})
            files.append({"path": relative, "read_error": str(exc)})
        else:
            files.append({"path": relative, **scan_source(source, str(path))})

    by_category = {}
    for kind in CATEGORIES:
        affected = [row for row in files if row.get(kind, {}).get("utf8_bytes", 0)]
        by_category[kind] = {
            "scripts": len(affected),
            "lexemes": sum(row[kind]["lexemes"] for row in affected),
            "utf8_bytes": sum(row[kind]["utf8_bytes"] for row in affected),
            "paths": [row["path"] for row in affected],
        }
    return {
        "name": name,
        "root": str(root),
        "pattern": pattern,
        "total_scripts": len(files),
        "expected_scripts": expected,
        "population_matches": bool(files) and (expected is None or len(files) == expected),
        "non_ascii_scripts": sum(bool(row.get("non_ascii_utf8_bytes", 0)) for row in files),
        **by_category,
        "other": {
            "scripts": sum(bool(row.get("other_utf8_bytes", 0)) for row in files),
            "utf8_bytes": sum(row.get("other_utf8_bytes", 0) for row in files),
            "paths": [row["path"] for row in files if row.get("other_utf8_bytes", 0)],
        },
        "lexer_errors": [row["path"] for row in files if row.get("lexer_error")],
        "read_errors": errors,
        "files": files,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", nargs=2, action="append", required=True,
                        metavar=("NAME", "ROOT"))
    parser.add_argument("--pattern", nargs=2, action="append", default=[],
                        metavar=("NAME", "GLOB"))
    parser.add_argument("--expect", nargs=2, action="append", default=[],
                        metavar=("NAME", "COUNT"))
    args = parser.parse_args(argv)
    names = [name for name, _root in args.population]
    if len(names) != len(set(names)):
        parser.error("population names must be unique")
    patterns = dict(args.pattern)
    try:
        expected = {name: int(value) for name, value in args.expect}
    except ValueError:
        parser.error("expected population sizes must be integers")
    unknown = (set(patterns) | set(expected)) - set(names)
    if unknown:
        parser.error(f"unknown populations: {', '.join(sorted(unknown))}")
    populations = []
    for name, root in args.population:
        try:
            row = count_population(name, Path(root), patterns.get(name, "*.pine"),
                                   expected.get(name))
        except ValueError as exc:
            parser.error(str(exc))
        populations.append(row)
    print(json.dumps({"schema": "pine-non-ascii/v1", "populations": populations},
                     ensure_ascii=True, indent=2))
    return int(any(not row["population_matches"] or row["read_errors"]
                   or row["lexer_errors"] or row["other"]["scripts"]
                   for row in populations))


if __name__ == "__main__":
    raise SystemExit(main())
