#!/usr/bin/env python3
"""List calendar timezone arguments in every .pine file under the given roots.

Usage: python scripts/list_timezone_literals.py ROOT [ROOT ...]

Print one JSON document with a ``scripts`` array (path and timezone call sites)
and the sorted ``literal_zones`` union. Each site has ``function``, ``line`` and
``kind``; a literal also has ``value``. Explicit arguments use the argument's
source line, while an omitted exchange timezone uses the call's line.
``timestamp(dateString)`` is excluded because it has no timezone argument.

Constants use the repository's expression folder after lexical substitution
of stable declarations. Inputs, reassigned names, persistent storage, helper
results and exchange timezone defaults remain ``dynamic``. No input default
or runtime symbol metadata is treated as a constant. Calls in all parsed
bodies are listed, including branches and helpers that might never execute.

A failed read or parse is included as an ``error`` on that script and makes
the exit status nonzero; its empty sites do not establish absence of zones.
No engine, transpilation or native execution is needed.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pineforge_codegen.ast_nodes import (
    BinOp, BoolLiteral, ExprStmt, FuncCall, FuncDef, Identifier, MethodDef,
    NaLiteral, NumberLiteral, Program, StringLiteral, SwitchStmt, Ternary, UnaryOp,
)
from pineforge_codegen.errors import CompileError
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.limits import (
    check_ast_depth, ensure_recursion_headroom, iter_ast_nodes,
)
from pineforge_codegen.parser import Parser
from pineforge_codegen.request_discovery import _Fold, _fold
from pineforge_codegen.security_contexts import ScriptIndex


TIME_FIELDS = frozenset({
    "year", "month", "dayofmonth", "dayofweek",
    "hour", "minute", "second", "weekofyear",
})
CALENDAR_FUNCTIONS = TIME_FIELDS | {"timestamp"}


class _Constants:
    """Close lexical names before using the existing bounded value folder."""

    def __init__(self, index: ScriptIndex) -> None:
        self.index = index
        self.bindings: dict[tuple, object] = {}

    def value(self, node):
        # Closed trees contain no identifiers or calls: the folder cannot
        # substitute input defaults or need a code generator.
        return _fold(_Fold(None), self._closed(node))

    def _closed(self, node, depth: int = 0):
        if depth > 256:
            return NaLiteral()
        if isinstance(node, (StringLiteral, NumberLiteral, BoolLiteral)):
            return node
        if isinstance(node, Identifier):
            binding = self.index.refs.get(id(node))
            declaration = self.index.decls.get(binding)
            if declaration is None or binding in self.index.unstable:
                return NaLiteral()
            if binding not in self.bindings:
                # The temporary unknown also terminates cyclic aliases.
                self.bindings[binding] = NaLiteral()
                self.bindings[binding] = self._closed(declaration.value, depth + 1)
            return self.bindings[binding]
        if isinstance(node, BinOp):
            return replace(node, left=self._closed(node.left, depth + 1),
                           right=self._closed(node.right, depth + 1))
        if isinstance(node, UnaryOp):
            return replace(node, operand=self._closed(node.operand, depth + 1))
        if isinstance(node, Ternary):
            return replace(node, condition=self._closed(node.condition, depth + 1),
                           true_val=self._closed(node.true_val, depth + 1),
                           false_val=self._closed(node.false_val, depth + 1))
        if isinstance(node, SwitchStmt):
            def arm(body):
                return [
                    ExprStmt(expr=self._closed(stmt.expr, depth + 1))
                    if isinstance(stmt, ExprStmt) else NaLiteral()
                    for stmt in body
                ]

            return replace(
                node,
                expr=None if node.expr is None else self._closed(node.expr, depth + 1),
                cases=[
                    (None if condition is None else self._closed(condition, depth + 1),
                     arm(body))
                    for condition, body in node.cases
                ],
                default_body=arm(node.default_body),
            )
        return NaLiteral()


def scan_pine_file(path: Path) -> list[dict]:
    """Every explicit or implicit calendar zone, without executing the script."""
    ensure_recursion_headroom()
    lexer = Lexer(path.read_text(encoding="utf-8"), filename=str(path))
    program = Parser(
        lexer.tokenize(), source=lexer.source, filename=str(path),
    ).parse()
    check_ast_depth(program, str(path))
    # Parameter defaults are syntax stored in annotations by the parser.
    defaults = [
        ExprStmt(expr=default)
        for node, _depth in iter_ast_nodes(program)
        if isinstance(node, (FuncDef, MethodDef))
        for default in (node.annotations or {}).get("param_defaults", ())
        if default is not None
    ]
    indexed = Program(body=[*program.body, *defaults])
    index = ScriptIndex(indexed)
    constants = _Constants(index)
    calls = [
        node for node, _depth in iter_ast_nodes(indexed)
        if isinstance(node, FuncCall) and isinstance(node.callee, Identifier)
        and node.callee.name in CALENDAR_FUNCTIONS
    ]
    calls.sort(key=lambda node: (node.loc.line, node.loc.col))
    zones = []
    for call in calls:
        function = call.callee.name
        if function == "timestamp" and (
            (len(call.args) == 1 and not call.kwargs) or "dateString" in call.kwargs
        ):
            continue
        zone = call.kwargs.get("timezone")
        if zone is None and function in TIME_FIELDS and len(call.args) >= 2:
            zone = call.args[1]
        if zone is None and function == "timestamp" and call.args:
            first = call.args[0]
            value = constants.value(first)
            # A numeric first argument selects the exchange-zone overload.
            # An unresolved first argument remains dynamic under either
            # overload, so no string or exchange value is guessed for it.
            if not isinstance(value, (int, float, bool)):
                zone = first
        value = constants.value(zone)
        location = zone.loc if zone is not None and zone.loc is not None else call.loc
        entry = {"function": function, "line": location.line, "kind": "dynamic"}
        if isinstance(value, str):
            entry.update(kind="literal", value=value)
        zones.append(entry)
    return zones


def collect_timezone_literals(roots: list[Path]) -> dict:
    """Collect overlapping roots once, in deterministic path order."""
    files: dict[Path, Path] = {}
    for root in roots:
        if not root.is_dir() and not (root.is_file() and root.suffix == ".pine"):
            raise ValueError(f"not a directory or .pine file: {root}")
        paths = [root] if root.is_file() else sorted(root.rglob("*.pine"))
        for path in paths:
            if path.is_file():
                files.setdefault(path.resolve(), path)
    scripts = []
    literals: set[str] = set()
    for path in sorted(files.values(), key=str):
        entry = {"path": str(path), "zones": []}
        try:
            entry["zones"] = scan_pine_file(path)
        except (CompileError, OSError, UnicodeError) as error:
            entry["error"] = str(error)
        literals.update(zone["value"] for zone in entry["zones"] if zone["kind"] == "literal")
        scripts.append(entry)
    return {"scripts": scripts, "literal_zones": sorted(literals)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        inventory = collect_timezone_literals(args.roots)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(inventory, indent=2, sort_keys=True))
    return int(any("error" in script for script in inventory["scripts"]))


if __name__ == "__main__":
    raise SystemExit(main())
