"""Resolve the Pine libraries a script imports.

TradingView links a published library into every script that imports it;
PineForge has no linker, so ``transpile()`` must read each library's source
itself. Every import the script names is resolved (``pine_libraries``: from
``libraries=`` or the script's own requests manifest), verified and read as a
module (``library_modules``), with every import of the libraries it reads.
A source that cannot be used is refused by the import's name. Inlining the
libraries comes next: until then each import keeps its refusal.

With no library sources configured the program is returned unchanged: every
import keeps the refusal it has today.
"""

from __future__ import annotations

from collections.abc import Mapping

from .ast_nodes import ASTNode, ImportStmt, MemberAccess, Identifier, Program, TypeField
from .errors import CompileError, Diagnostic, Level, Phase, SourceLocation
from .library_modules import LibraryModule, parse_library_module
from .limits import TimeBudget
from .pine_libraries import LibraryResolveError, LibraryResolver, library_resolver
from .support_checker import (
    BUILTIN_NAMESPACE_IMPORT_MEMBERS,
    import_is_builtin_namespace_no_op,
)


def _walk(root):
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, (list, tuple)):
            stack.extend(node)
        elif isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, TypeField):
            stack.append(node.default)
        elif isinstance(node, ASTNode):
            yield node
            for key, value in vars(node).items():
                if key not in ("loc", "annotations"):
                    stack.append(value)


def _referenced(program: Program, aliases: set[str]) -> set[str]:
    """The aliases ``program`` names with a member that is not a built-in."""
    found: set[str] = set()
    for node in _walk(program):
        if isinstance(node, MemberAccess) and isinstance(node.object, Identifier) \
                and node.object.name in aliases:
            builtin = BUILTIN_NAMESPACE_IMPORT_MEMBERS.get(node.object.name)
            if builtin is None or node.member not in builtin:
                found.add(node.object.name)
        for key in ("type_hint", "type_name"):
            hint = getattr(node, key, None)
            if isinstance(hint, str):
                found.update(a for a in aliases if f"{a}." in hint)
        notes = node.annotations or {}
        for hint in notes.get("param_type_hints") or ():
            if isinstance(hint, str):
                found.update(a for a in aliases if f"{a}." in hint)
    return found


def resolve_library_modules(program: Program, resolver: LibraryResolver,
                            handled: list[ImportStmt], filename: str,
                            budget: TimeBudget | None) -> dict[str, LibraryModule]:
    """Every library the script links, read as a module: an import the script
    names (it must resolve), one whose source is at hand (it may bind by
    method), and every import of those libraries (TradingView links them)."""
    modules: dict[str, LibraryModule] = {}
    aliases = {stmt.alias or stmt.name for stmt in handled}
    referenced = _referenced(program, aliases)
    pending = [stmt for stmt in handled
               if (stmt.alias or stmt.name) in referenced or resolver.pinned(stmt.path)]
    while pending:
        stmt = pending.pop(0)
        if stmt.path in modules:
            continue
        try:
            source = resolver.load(stmt.path)
        except LibraryResolveError as exc:
            raise CompileError([Diagnostic(
                level=Level.ERROR, phase=Phase.PARSER,
                location=stmt.loc or SourceLocation(file=filename, line=1, col=1, end_col=1),
                message=f"Import is not supported: '{stmt.path}': {exc}",
            )]) from None
        module = parse_library_module(stmt.path, source.text, budget=budget)
        modules[stmt.path] = module
        pending.extend(module.imports.values())
    return modules


def inline_libraries(program: Program, source: str, *,
                     libraries: Mapping[str, str] | None,
                     filename: str = "<input>",
                     budget: TimeBudget | None = None) -> Program:
    """Resolve the libraries ``program`` imports (see module doc)."""
    handled = [
        stmt for stmt in program.body
        if isinstance(stmt, ImportStmt) and stmt.version is not None
        and not import_is_builtin_namespace_no_op(program, stmt)
    ]
    if not handled:
        return program
    resolver = library_resolver(source, libraries)
    if not resolver.configured:
        if resolver.reason:
            for stmt in handled:
                stmt.annotations = {**(stmt.annotations or {}),
                                    "unresolved_reason": resolver.reason}
        return program
    resolve_library_modules(program, resolver, handled, filename, budget)
    return program
