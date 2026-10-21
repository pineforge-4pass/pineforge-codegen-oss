#!/usr/bin/env python3
"""Read-only corpus counter for timezone-only calendar built-ins and ``var`` + ``array.from`` with numeric constants.

Scans Pine files in the repository's gate corpus (``tests/gate-corpus/``) and
fixtures (``tests/fixtures/``), plus the sibling engine corpus via
``PINEFORGE_ENGINE_CORPUS`` (or ``PINEFORGE_CORPUS_DIR`` as a fallback, then
``../pineforge-engine/corpus``), without modifying any files or invoking native
builds. Exits nonzero with a diagnostic on stderr when any corpus directory is
not found.

Counts:
  (A) Calendar built-in calls (``hour``, ``minute``, ``second``, ``dayofmonth``,
      ``dayofweek``, ``month``, ``year``, ``weekofyear``) using the timezone-only
      short form (``timezone=...`` keyword argument with no positional ``time``
      argument).
  (B) ``var`` declarations initialized with ``array.from(...)`` whose arguments
      are numeric constants (numeric literals, unary-signed numeric literals, or
      identifiers bound to numeric constants).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tunaxis_codegen.ast_nodes import (
    ASTNode,
    BinOp,
    FuncCall,
    Identifier,
    MemberAccess,
    NumberLiteral,
    UnaryOp,
    VarDecl,
)
from tunaxis_codegen.lexer import Lexer
from tunaxis_codegen.parser import Parser


CALENDAR_BUILTINS = frozenset({
    "hour",
    "minute",
    "second",
    "dayofmonth",
    "dayofweek",
    "month",
    "year",
    "weekofyear",
})


def _iter_ast(node: object):
    if isinstance(node, ASTNode):
        yield node
        for val in node.__dict__.values():
            yield from _iter_ast(val)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _iter_ast(item)
    elif isinstance(node, dict):
        for val in node.values():
            yield from _iter_ast(val)


def _is_numeric_const_expr(node: ASTNode | None, numeric_consts: set[str]) -> bool:
    if node is None:
        return False
    if isinstance(node, NumberLiteral):
        return True
    if isinstance(node, UnaryOp) and node.op in ("-", "+"):
        return _is_numeric_const_expr(node.operand, numeric_consts)
    if isinstance(node, BinOp) and node.op in ("+", "-", "*", "/", "%"):
        return (
            _is_numeric_const_expr(node.left, numeric_consts)
            and _is_numeric_const_expr(node.right, numeric_consts)
        )
    if isinstance(node, Identifier):
        return node.name in numeric_consts
    return False


def _is_array_from_call(node: ASTNode | None) -> bool:
    if not isinstance(node, FuncCall):
        return False
    callee = node.callee
    return (
        isinstance(callee, MemberAccess)
        and isinstance(callee.object, Identifier)
        and callee.object.name == "array"
        and callee.member == "from"
    )


def _is_timezone_only_calendar_call(node: ASTNode) -> bool:
    if not isinstance(node, FuncCall):
        return False
    if not isinstance(node.callee, Identifier):
        return False
    if node.callee.name not in CALENDAR_BUILTINS:
        return False
    return len(node.args) == 0 and "timezone" in (node.kwargs or {}) and "time" not in (node.kwargs or {})


def scan_pine_file(path: Path) -> tuple[int, int, bool]:
    """Return ``(count_a, count_b, parse_ok)`` for one Pine file."""
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tokens = Lexer(src, filename=str(path)).tokenize()
        ast = Parser(tokens, source=src, filename=str(path)).parse()
    except Exception:
        return 0, 0, False

    numeric_consts: set[str] = set()
    for node in _iter_ast(ast):
        if (
            isinstance(node, VarDecl)
            and not node.is_var
            and not node.is_varip
            and _is_numeric_const_expr(node.value, numeric_consts)
        ):
            numeric_consts.add(node.name)

    count_a = 0
    count_b = 0
    for node in _iter_ast(ast):
        if _is_timezone_only_calendar_call(node):
            count_a += 1
        if (
            isinstance(node, VarDecl)
            and (node.is_var or node.is_varip)
            and _is_array_from_call(node.value)
            and bool(node.value.args)
            and all(_is_numeric_const_expr(arg, numeric_consts) for arg in node.value.args)
        ):
            count_b += 1

    return count_a, count_b, True


def collect_corpus_Summary(root: Path) -> dict:
    if not root.is_dir():
        print(f"error: corpus directory not found: {root}", file=sys.stderr)
        sys.exit(1)
    pine_files = sorted(p for p in root.rglob("*.pine") if "data" not in p.parts)
    parsed = 0
    files_a: list[str] = []
    files_b: list[str] = []
    occ_a = 0
    occ_b = 0
    for p in pine_files:
        ca, cb, ok = scan_pine_file(p)
        if ok:
            parsed += 1
        rel = str(p.relative_to(root))
        if ca > 0:
            files_a.append(rel)
            occ_a += ca
        if cb > 0:
            files_b.append(rel)
            occ_b += cb
    return {
        "exists": True,
        "total_files": len(pine_files),
        "parsed_files": parsed,
        "files_with_a": len(files_a),
        "occurrences_a": occ_a,
        "files_a": files_a,
        "files_with_b": len(files_b),
        "occurrences_b": occ_b,
        "files_b": files_b,
    }


def main() -> None:
    gate_corpus_dir = REPO_ROOT / "tests" / "gate-corpus"
    fixtures_dir = REPO_ROOT / "tests" / "fixtures"
    env_corpus = (
        os.environ.get("PINEFORGE_ENGINE_CORPUS")
        or os.environ.get("PINEFORGE_CORPUS_DIR")
    )
    engine_corpus_dir = (
        Path(env_corpus).expanduser()
        if env_corpus
        else (REPO_ROOT.parent / "pineforge-engine" / "corpus")
    )

    missing = [
        (name, path)
        for name, path in (
            ("gate_corpus", gate_corpus_dir),
            ("tests_fixtures", fixtures_dir),
            ("engine_corpus", engine_corpus_dir),
        )
        if not path.is_dir()
    ]
    if missing:
        for name, path in missing:
            print(
                f"error: corpus directory not found for {name}: {path} "
                "(set PINEFORGE_ENGINE_CORPUS or PINEFORGE_CORPUS_DIR)",
                file=sys.stderr,
            )
        sys.exit(1)

    report = {
        "gate_corpus": collect_corpus_Summary(gate_corpus_dir),
        "tests_fixtures": collect_corpus_Summary(fixtures_dir),
        "engine_corpus": collect_corpus_Summary(engine_corpus_dir),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
