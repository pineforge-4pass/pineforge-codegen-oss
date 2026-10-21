"""A Pine library source parsed as a module.

A library is a script that starts with ``library()`` instead of
``strategy()``. What an importing script can reach is what it ``export``s:
functions, methods, user-defined types, enums and (since June 2025)
``export const`` variables. Everything else at its top level is private: the
helpers and constants the exports use, and example code (plots, inputs) that
only runs when the library itself is on a chart. A library may import other
libraries, and keeps its own ``//@version``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ast_nodes import (
    EnumDecl, FuncDef, ImportStmt, MethodDef, Program, StrategyDecl,
    TupleAssign, TypeDecl, VarDecl,
)
from .errors import CompileError, Diagnostic, Level, Phase, SourceLocation
from .lexer import Lexer
from .limits import TimeBudget, check_ast_depth, check_source_size
from .parser import Parser

# The Pine versions whose rules the inliner implements (v5 through
# ``library_v5``; v6 lowers as the script does).
SUPPORTED_LIBRARY_VERSIONS = (5, 6)


@dataclass
class LibraryModule:
    path: str                      # ``user/name/version``
    pine_version: int
    program: Program
    title: str | None
    imports: dict[str, ImportStmt] = field(default_factory=dict)
    functions: dict[str, list[FuncDef]] = field(default_factory=dict)
    methods: dict[str, list[MethodDef]] = field(default_factory=dict)
    types: dict[str, TypeDecl] = field(default_factory=dict)
    enums: dict[str, EnumDecl] = field(default_factory=dict)
    # Top-level variables: name -> the VarDecl or TupleAssign binding it.
    globals: dict[str, object] = field(default_factory=dict)
    exports: set[str] = field(default_factory=set)

    @property
    def name(self) -> str:
        return self.path.split("/")[1]

    def exported(self, node) -> bool:
        return bool((getattr(node, "annotations", None) or {}).get("exported"))


def _module_error(path: str, message: str, loc: SourceLocation | None = None) -> CompileError:
    return CompileError([Diagnostic(
        level=Level.ERROR, phase=Phase.PARSER,
        location=loc or SourceLocation(file=path, line=1, col=1, end_col=1),
        message=message,
    )])


def normalize_library_text(text: str) -> str:
    """A leading byte-order mark dropped and every line end spelled ``\\n``:
    TradingView ends a line at ``\\r\\n``, ``\\r`` or ``\\n``, and pinned
    library sources keep the bytes pine-facade served (CRLF for some)."""
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_library_module(path: str, text: str, *,
                         budget: TimeBudget | None = None) -> LibraryModule:
    """Parse ``text``, the source of library ``path`` (``user/name/version``)."""
    text = normalize_library_text(text)
    check_source_size(text, path)
    tokens = Lexer(text, filename=path, budget=budget).tokenize()
    program = Parser(tokens, source=text, filename=path, budget=budget,
                     library=True).parse()
    check_ast_depth(program, path)
    if program.version not in SUPPORTED_LIBRARY_VERSIONS:
        found = ("no //@version directive" if program.version is None
                 else f"//@version={program.version}")
        raise _module_error(
            path, f"library '{path}' has {found}; PineForge inlines v5 and v6 "
                  "libraries only")
    decls = [stmt for stmt in program.body if isinstance(stmt, StrategyDecl)]
    kinds = [(d.annotations or {}).get("decl_kind") for d in decls]
    if kinds != ["library"]:
        found = ", ".join(f"{k}()" for k in kinds) or "no declaration"
        raise _module_error(
            path, f"'{path}' is not a library: it has {found}, not one library() "
                  "declaration", decls[0].loc if decls else None)
    title = None
    decl = decls[0]
    if decl.args and hasattr(decl.args[0], "value") and isinstance(decl.args[0].value, str):
        title = decl.args[0].value
    elif "title" in decl.kwargs and isinstance(getattr(decl.kwargs["title"], "value", None), str):
        title = decl.kwargs["title"].value
    module = LibraryModule(path=path, pine_version=program.version,
                           program=program, title=title)
    for stmt in program.body:
        if isinstance(stmt, ImportStmt):
            alias = stmt.alias or stmt.name
            if stmt.version is None or alias is None:
                raise _module_error(
                    path, f"library '{path}' has a malformed import: "
                          f"'{stmt.path}'", stmt.loc)
            module.imports[alias] = stmt
        elif isinstance(stmt, FuncDef):
            module.functions.setdefault(stmt.name, []).append(stmt)
        elif isinstance(stmt, MethodDef):
            module.methods.setdefault(stmt.name, []).append(stmt)
        elif isinstance(stmt, TypeDecl):
            module.types[stmt.name] = stmt
        elif isinstance(stmt, EnumDecl):
            module.enums[stmt.name] = stmt
        elif isinstance(stmt, VarDecl):
            module.globals.setdefault(stmt.name, stmt)
        elif isinstance(stmt, TupleAssign):
            for name in stmt.names:
                if name != "_":
                    module.globals.setdefault(name, stmt)
        if module.exported(stmt):
            name = getattr(stmt, "name", None)
            if isinstance(name, str) and not isinstance(stmt, MethodDef):
                module.exports.add(name)
    return module
