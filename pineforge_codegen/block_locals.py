"""Declarations in the script's top-level blocks that cannot share a member.

The codegen initially holds declarations of the script's top level -- direct
ones and ones inside top-level ``if`` / loop / ``switch`` blocks -- in one
class member per name. Pine scopes a block's declaration to its block, so two
blocks may declare one name with types one member cannot hold: a counter
``k = 0`` in one block and a string in another. A block's ``var`` / ``varip``
also needs separate storage from a plain declaration in a sibling block,
which would overwrite its persistent value. The codegen selects declarations
that need their own member
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
    """(declaration, in a block) for every declaration the codegen holds in a
    class member of the script's top level, in source order: the direct ones
    and those in its statement blocks (``if`` / loop / ``switch``, nested
    too), as ``CodeGen`` hoists them. An ``if`` or ``switch`` value's block
    keeps its declarations to itself."""
    for decl, block in _block_declarations(body):
        yield decl, block is not None


def _block_declarations(body: list):
    for stmt in body:
        yield from _declarations(stmt, None)


def _declarations(node, block: int | None):
    if isinstance(node, VarDecl):
        yield node, block
        return
    if isinstance(node, IfStmt):
        bodies = (node.body, node.else_body)
    elif isinstance(node, (ForStmt, ForInStmt, WhileStmt)):
        bodies = (node.body,)
    elif isinstance(node, SwitchStmt):
        bodies = [body for _value, body in node.cases] + [node.default_body]
    else:
        return
    for body in bodies:
        owner = id(body) if block is None else block
        for stmt in body:
            yield from _declarations(stmt, owner)


def persistent_plain_block_keys(body: list) -> frozenset:
    """Persistent declarations sharing a name with a plain sibling block.

    Nested statements keep their outermost body's identity: a declaration
    shadowing an ancestor in that body alone does not select either one.
    Direct script declarations, including tuple bindings, keep their route.
    """
    decls = list(_block_declarations(body))
    direct = {decl.name for decl, block in decls if block is None}
    for stmt in body:
        if isinstance(stmt, TupleAssign):
            direct.update(stmt.names)
    plain_blocks: dict[str, set[int]] = {}
    for decl, block in decls:
        if block is not None and not (decl.is_var or decl.is_varip):
            plain_blocks.setdefault(decl.name, set()).add(block)
    needing: set = set()
    for decl, block in decls:
        if (block is None or decl.name in direct
                or not (decl.is_var or decl.is_varip)):
            continue
        siblings = plain_blocks.get(decl.name, set())
        if len(siblings) > 1 or (siblings and block not in siblings):
            key = decl_key(decl)
            if key is not None:
                needing.add(key)
    return frozenset(needing)


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
