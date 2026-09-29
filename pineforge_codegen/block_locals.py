"""Declarations in the script's top-level blocks that cannot share a member.

The codegen holds every declaration of the script's top level -- a direct one
and one inside a top-level ``if`` / loop / ``switch`` block -- in one class
member per name. Pine scopes a block's declaration to its block, so two blocks
may declare one name with types one member cannot hold: a counter ``k = 0`` in
one block and ``k = array.get(names, i)`` (a string) in another, a script of
the fruit-fly probes' shape, did not compile. The codegen names each
declaration whose type cannot share the member of the name
(``CodeGen.block_locals_needing_names``, by ``decl_key``); ``transpile()``
gives each of them, and every read of it in its block, a name of its own
(``k__pfblk1``) and runs again (``pineforge_codegen._generate``). Scripts
whose declarations share their members keep their C++ byte for byte.
"""

from __future__ import annotations

from .ast_nodes import (
    ASTNode, ArgOrder, Assignment, ExprStmt, ForInStmt, ForStmt, FuncCall,
    Identifier, IfStmt, Program, SwitchStmt, TupleAssign, VarDecl, WhileStmt,
)

_BLOCKS = (IfStmt, ForStmt, ForInStmt, WhileStmt, SwitchStmt)


def decl_key(node: VarDecl) -> tuple | None:
    """A declaration's identity across the passes of one transpile: where
    it is written."""
    loc = node.loc
    return None if loc is None else (loc.file, loc.line, loc.col)


def block_declarations(body: list):
    """(declaration, in a block) for every declaration of the script's top
    level, in source order: the direct ones and those in its blocks, the
    blocks of an ``if`` / ``switch`` value included."""
    for stmt in body:
        yield from _declarations(stmt, False)


def _declarations(node, nested: bool):
    if isinstance(node, list):
        for item in node:
            yield from _declarations(item, nested)
        return
    if isinstance(node, VarDecl):
        yield node, nested
        yield from _declarations(node.value, nested)
    elif isinstance(node, IfStmt):
        yield from _declarations(node.body, True)
        yield from _declarations(node.else_body, True)
    elif isinstance(node, (ForStmt, ForInStmt, WhileStmt)):
        yield from _declarations(node.body, True)
    elif isinstance(node, SwitchStmt):
        for _value, body in node.cases:
            yield from _declarations(body, True)
        yield from _declarations(node.default_body, True)
    elif isinstance(node, (Assignment, ExprStmt, TupleAssign)):
        value = node.expr if isinstance(node, ExprStmt) else node.value
        yield from _declarations(value, nested)


def rename_block_locals(program: Program, keys: frozenset) -> None:
    """Give each top-level block declaration ``keys`` names a name of its own,
    and every read of it in its block (a nested block's declaration of the
    same name, or a loop variable of it, shadows it there)."""
    if not keys:
        return
    renamer = _Renamer(program, keys)
    renamer.block(program.body, {})


class _Renamer:
    def __init__(self, program: Program, keys: frozenset) -> None:
        self._keys = keys
        self._taken = _names(program)
        self._next = 0

    def _alloc(self, name: str) -> str:
        while True:
            self._next += 1
            new = f"{name}__pfblk{self._next}"
            if new not in self._taken:
                self._taken.add(new)
                return new

    def block(self, stmts: list, visible: dict) -> None:
        names = dict(visible)
        for stmt in stmts:
            self.stmt(stmt, names)

    def stmt(self, stmt, names: dict) -> None:
        if isinstance(stmt, VarDecl):
            self.expr(stmt.value, names)
            if decl_key(stmt) in self._keys:
                new = self._alloc(stmt.name)
                names[stmt.name] = new
                stmt.name = new
            else:
                names.pop(stmt.name, None)
        elif isinstance(stmt, TupleAssign):
            self.expr(stmt.value, names)
            for bound in stmt.names:
                names.pop(bound, None)
        elif isinstance(stmt, Assignment):
            self.expr(stmt.target, names)
            self.expr(stmt.value, names)
        elif isinstance(stmt, ExprStmt):
            self.expr(stmt.expr, names)
        elif isinstance(stmt, _BLOCKS):
            self.block_node(stmt, names)

    def block_node(self, node, names: dict) -> None:
        if isinstance(node, IfStmt):
            self.expr(node.condition, names)
            self.block(node.body, names)
            self.block(node.else_body, names)
        elif isinstance(node, ForStmt):
            for part in (node.start, node.end, node.step):
                self.expr(part, names)
            inner = dict(names)
            inner.pop(node.var, None)
            self.block(node.body, inner)
        elif isinstance(node, ForInStmt):
            self.expr(node.iterable, names)
            inner = dict(names)
            for bound in [node.var, *(node.vars or ())]:
                inner.pop(bound, None)
            self.block(node.body, inner)
        elif isinstance(node, WhileStmt):
            self.expr(node.condition, names)
            self.block(node.body, names)
        elif isinstance(node, SwitchStmt):
            self.expr(node.expr, names)
            for value, body in node.cases:
                self.expr(value, names)
                self.block(body, names)
            self.block(node.default_body, names)

    def expr(self, node, names: dict) -> None:
        if not names or node is None:
            return
        if isinstance(node, list):
            for item in node:
                self.expr(item, names)
            return
        if not isinstance(node, ASTNode):
            return
        if isinstance(node, Identifier):
            node.name = names.get(node.name, node.name)
            return
        if isinstance(node, _BLOCKS):
            self.block_node(node, names)
            return
        for key, value in vars(node).items():
            if key in ("loc", "annotations"):
                continue
            if key == "callee" and isinstance(node, FuncCall) and isinstance(value, Identifier):
                continue  # a function's name, not a variable's
            if isinstance(value, dict):
                self.expr(list(value.values()), names)
            elif isinstance(value, (ASTNode, list)):
                self.expr(value, names)


def _names(root) -> set[str]:
    """Every identifier and declared name ``root`` spells."""
    found: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, (list, tuple)):
            stack.extend(node)
        elif isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, ArgOrder):
            continue
        elif isinstance(node, ASTNode):
            for key, value in vars(node).items():
                if key in ("loc", "annotations"):
                    continue
                if isinstance(value, str) and key in ("name", "var"):
                    found.add(value)
                elif key in ("params", "names", "vars") and isinstance(value, list):
                    found.update(v for v in value if isinstance(v, str))
                else:
                    stack.append(value)
    return found
