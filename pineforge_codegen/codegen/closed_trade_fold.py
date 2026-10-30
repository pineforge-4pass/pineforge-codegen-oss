"""Recognise closed-trade reductions whose settled prefix can be reused.

Planning reads syntax only. Unproved shapes keep their ordinary loop, and
names are mapped only after all of a candidate's proof checks have passed.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite

from ..ast_nodes import (
    ASTNode, Assignment, BinOp, EnumDecl, ForInStmt, ForStmt, FuncCall,
    FuncDef, Identifier, IfStmt, ImportStmt, MemberAccess, MethodDef,
    NumberLiteral, Program, StringLiteral, Subscript, TupleAssign, TypeDecl,
    UnaryOp, VarDecl,
)
from ..limits import syntax_children


__all__ = ("FoldPlan", "plan_closed_trade_folds")

_ACCESSORS = {"entry_id": "std::string", "profit": "double"}
_NUMBER_COMPARISONS = frozenset({">", ">=", "<", "<=", "==", "!="})


@dataclass(frozen=True)
class FoldPlan:
    """One direct top-level guard and its closed-trade loop."""

    index: int
    for_node_id: int
    accumulators: tuple[tuple[str, str], ...]
    locals: tuple[tuple[str, str, str], ...]
    stem: str

    # The private slot owns the lookup key's node without changing the record's
    # public fields, constructor, equality or ordering.
    __slots__ = (
        "index", "for_node_id", "accumulators", "locals", "stem", "_for_node",
    )


def _number(node: object) -> bool:
    return (
        isinstance(node, NumberLiteral)
        and (
            type(node.value) is int
            or (type(node.value) is float and isfinite(node.value))
        )
    )


def _number_is(node: object, value: int) -> bool:
    return _number(node) and node.value == value


def _closed_count(node: object) -> bool:
    return (
        isinstance(node, MemberAccess)
        and node.member == "closedtrades"
        and isinstance(node.object, Identifier)
        and node.object.name == "strategy"
    )


def _request_call(node: object) -> bool:
    if not isinstance(node, FuncCall):
        return False
    callee = node.callee
    while isinstance(callee, MemberAccess):
        callee = callee.object
    return isinstance(callee, Identifier) and callee.name == "request"


class _ProgramIndex:
    """Name facts collected once, including callable parameter defaults."""

    def __init__(self, program: Program) -> None:
        self.bindings: dict[str, set[int]] = defaultdict(set)
        self.writes: dict[str, set[int]] = defaultdict(set)
        self.reads: Counter[str] = Counter()
        self.first_use: dict[str, int] = {}
        self.remote_reads: set[str] = set()
        self.history_reads: set[str] = set()
        self.names: set[str] = set()
        self.ambiguous_namespace = False
        self.accessor_definition = False
        self.declarations = {
            stmt.name: (position, stmt)
            for position, stmt in enumerate(program.body)
            if isinstance(stmt, VarDecl)
        }

        pending = [
            (stmt, position, None, False, False)
            for position, stmt in enumerate(program.body)
        ]
        while pending:
            node, position, parent, remote, history = pending.pop()
            if not isinstance(node, ASTNode):
                continue
            for attr in ("name", "var", "member", "alias"):
                name = getattr(node, attr, None)
                if isinstance(name, str):
                    self.names.add(name)
            for attr in ("names", "vars", "params", "members"):
                names = getattr(node, attr, None)
                if isinstance(names, (list, tuple)):
                    self.names.update(name for name in names if isinstance(name, str))
            if isinstance(node, FuncCall):
                self.names.update(node.kwargs)

            if isinstance(node, Identifier):
                self.reads[node.name] += 1
                self.first_use[node.name] = min(
                    position, self.first_use.get(node.name, position),
                )
                if remote:
                    self.remote_reads.add(node.name)
                if history:
                    self.history_reads.add(node.name)
                if node.name == "strategy" and not (
                    isinstance(parent, MemberAccess) and parent.object is node
                ):
                    self.ambiguous_namespace = True
            elif isinstance(node, VarDecl):
                self.bindings[node.name].add(id(node))
            elif isinstance(node, TupleAssign):
                for name in node.names:
                    self.bindings[name].add(id(node))
            elif isinstance(node, (FuncDef, MethodDef)):
                self.bindings[node.name].add(id(node))
                for name in node.params:
                    self.bindings[name].add(id(node))
                if node.name in _ACCESSORS:
                    self.accessor_definition = True
            elif isinstance(node, ForStmt):
                self.bindings[node.var].add(id(node))
            elif isinstance(node, ForInStmt):
                for name in node.vars or ([node.var] if node.var else ()):
                    self.bindings[name].add(id(node))
            elif isinstance(node, ImportStmt):
                if node.alias:
                    self.bindings[node.alias].add(id(node))
            elif isinstance(node, (TypeDecl, EnumDecl)):
                self.bindings[node.name].add(id(node))
                if isinstance(node, TypeDecl):
                    self.names.update(field.name for field in node.fields)
                else:
                    self.names.update(node.members)

            if isinstance(node, Assignment):
                target = node.target
                while isinstance(target, (MemberAccess, Subscript)):
                    target = target.object
                if isinstance(target, Identifier):
                    self.writes[target.name].add(id(node))

            remote = remote or isinstance(node, (FuncDef, MethodDef)) or _request_call(node)
            pending.extend(
                (
                    child, position, node, remote,
                    history or (isinstance(node, Subscript) and child is node.object),
                )
                for child in syntax_children(node)
            )
            # Defaults are source expressions stored outside the syntax fields.
            # Other annotations may alias arguments or point back into the AST.
            if isinstance(node, (FuncDef, MethodDef)):
                pending.extend(
                    (default, position, node, True, False)
                    for default in ((node.annotations or {}).get("param_defaults") or ())
                    if isinstance(default, ASTNode)
                )

    def blocked(self) -> bool:
        return (
            self.ambiguous_namespace
            or bool(self.bindings.get("strategy"))
            or self.accessor_definition
            or any(name.startswith("_pf_ctf") for name in self.names)
        )


def _guarded_loop(node: object) -> ForStmt | None:
    if (
        not isinstance(node, IfStmt)
        or node.else_body
        or len(node.body) != 1
        or not isinstance(node.condition, BinOp)
        or node.condition.op != ">"
        or not _closed_count(node.condition.left)
        or not _number_is(node.condition.right, 0)
    ):
        return None
    loop = node.body[0]
    if (
        not isinstance(loop, ForStmt)
        or not _number_is(loop.start, 0)
        or not isinstance(loop.end, BinOp)
        or loop.end.op != "-"
        or not _closed_count(loop.end.left)
        or not _number_is(loop.end.right, 1)
        or loop.step is not None
        or not loop.body
    ):
        return None
    return loop


def _accessor(node: object, counter: str) -> str | None:
    if (
        isinstance(node, FuncCall)
        and not node.kwargs
        and len(node.args) == 1
        and isinstance(node.args[0], Identifier)
        and node.args[0].name == counter
        and isinstance(node.callee, MemberAccess)
        and _closed_count(node.callee.object)
        and node.callee.member in _ACCESSORS
    ):
        return node.callee.member
    return None


def _comparison_local(node: object, visible: dict[str, str]) -> str | None:
    if not isinstance(node, BinOp):
        return None
    local, literal = node.left, node.right
    if isinstance(literal, Identifier):
        local, literal = literal, local
    if not isinstance(local, Identifier):
        return None
    accessor = visible.get(local.name)
    if accessor == "entry_id":
        if node.op in ("==", "!=") and isinstance(literal, StringLiteral):
            return local.name
    elif accessor == "profit":
        if node.op in _NUMBER_COMPARISONS and _number(literal):
            return local.name
    return None


class _Body:
    def __init__(self) -> None:
        self.locals: dict[str, tuple[VarDecl, str]] = {}
        self.reads: Counter[str] = Counter()
        self.updates: dict[str, set[int]] = defaultdict(set)
        self.counter_reads = 0


def _match_body(loop: ForStmt) -> _Body | None:
    result = _Body()
    visible: dict[str, str] = {}
    # Scope exits remove only that scope's declarations; nested guards never
    # copy the environment or expose a conditional local to a later sibling.
    frames = [(iter(loop.body), [])]
    while frames:
        statements, declared_here = frames[-1]
        try:
            stmt = next(statements)
        except StopIteration:
            for name in declared_here:
                del visible[name]
            frames.pop()
            continue
        if isinstance(stmt, VarDecl):
            accessor = _accessor(stmt.value, loop.var)
            if (
                accessor is None
                or stmt.is_var
                or stmt.is_varip
                or stmt.name in result.locals
                or stmt.name == loop.var
                or stmt.type_hint not in (
                    None, "string" if accessor == "entry_id" else "float",
                )
            ):
                return None
            result.locals[stmt.name] = (stmt, accessor)
            visible[stmt.name] = accessor
            declared_here.append(stmt.name)
            result.counter_reads += 1
        elif isinstance(stmt, IfStmt):
            name = _comparison_local(stmt.condition, visible)
            if name is None or stmt.else_body or not stmt.body:
                return None
            result.reads[name] += 1
            frames.append((iter(stmt.body), []))
        elif isinstance(stmt, Assignment):
            if (
                stmt.op != "+="
                or not isinstance(stmt.target, Identifier)
                or stmt.target.name in result.locals
                or stmt.target.name == loop.var
            ):
                return None
            if isinstance(stmt.value, Identifier) and visible.get(stmt.value.name) == "profit":
                result.reads[stmt.value.name] += 1
            elif not _number(stmt.value):
                return None
            result.updates[stmt.target.name].add(id(stmt))
        else:
            return None
    return result if result.updates else None


def _literal_accumulator(node: VarDecl) -> bool:
    return (
        not node.is_var
        and not node.is_varip
        and node.type_hint in (None, "int", "float")
        and (
            _number(node.value)
            or (
                isinstance(node.value, UnaryOp)
                and node.value.op == "-"
                and _number(node.value.operand)
            )
        )
    )


def plan_closed_trade_folds(
    program: Program, safe_name: Callable[[str], str],
) -> tuple[FoldPlan, ...]:
    """Return proved folds numbered in source order without changing syntax.

    Each accumulator belongs to one loop, so disjoint folds keep independent
    cursors and prefix values.
    """
    if not isinstance(program, Program):
        return ()
    candidates = [
        (position, loop)
        for position, guard in enumerate(program.body)
        if (loop := _guarded_loop(guard)) is not None
    ]
    if not candidates:
        return ()
    index = _ProgramIndex(program)
    if index.blocked():
        return ()
    plans = []
    for position, loop in candidates:
        body = _match_body(loop)
        if body is None:
            continue
        if (
            index.bindings.get(loop.var) != {id(loop)}
            or index.writes.get(loop.var)
            or index.reads[loop.var] != body.counter_reads
        ):
            continue
        if any(
            index.bindings.get(name) != {id(declaration)}
            or index.writes.get(name)
            or index.reads[name] != body.reads[name]
            for name, (declaration, _accessor_name) in body.locals.items()
        ):
            continue

        declarations = []
        for name, updates in body.updates.items():
            entry = index.declarations.get(name)
            if entry is None:
                break
            declaration_position, declaration = entry
            if (
                declaration_position >= position
                or not _literal_accumulator(declaration)
                or index.bindings.get(name) != {id(declaration)}
                or index.writes.get(name) != updates
                or index.first_use.get(name, declaration_position) < declaration_position
                or name in index.remote_reads
                or name in index.history_reads
            ):
                break
            declarations.append((declaration_position, name))
        else:
            declarations.sort(key=lambda entry: entry[0])
            plan = FoldPlan(
                index=len(plans),
                for_node_id=id(loop),
                accumulators=tuple(
                    (name, safe_name(name)) for _position, name in declarations
                ),
                locals=tuple(
                    (name, accessor, _ACCESSORS[accessor])
                    for name, (_declaration, accessor) in body.locals.items()
                ),
                stem=f"_pf_ctf{len(plans)}_",
            )
            object.__setattr__(plan, "_for_node", loop)
            plans.append(plan)
    return tuple(plans)
