"""Give provable scalar overloads distinct identities before helper lowering.

The final declaration keeps the source name, including for calls whose types
are unknown. Earlier declarations get stable names shared by every subsequent
pass. Groups with repeated base signatures, incomplete scalar hints, or calls
before the final declaration keep their established lookup.
"""

from __future__ import annotations

from bisect import bisect_left

from .analyzer import Analyzer
from .analyzer.tables import BUILTIN_VARS
from .ast_nodes import (
    ASTNode, Assignment, ColorLiteral, ForInStmt, ForStmt, FuncCall,
    FuncDef, Identifier, IfStmt, MemberAccess, MethodDef, Program, SwitchStmt,
    TupleAssign, VarDecl, WhileStmt,
)
from .errors import Phase, SourceLocation
from .limits import TimeBudget, syntax_children
from .symbols import PineType, Symbol, SymbolTable


_SCALAR_TYPES = {
    "float": PineType.FLOAT,
    "int": PineType.INT,
    "bool": PineType.BOOL,
    "string": PineType.STRING,
    "color": PineType.COLOR,
}


def _nodes(root: ASTNode):
    """Include parameter defaults, which are stored outside syntax children."""
    stack = [root]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        yield node
        stack.extend(syntax_children(node))
        if isinstance(node, (FuncDef, MethodDef)):
            stack.extend(
                value for value in
                (node.annotations or {}).get("param_defaults", ())
                if isinstance(value, ASTNode)
            )


class _ScalarProof:
    """Lexical type facts using the analyzer's certain-type helpers only."""

    _STRING_RETURNING_STR_FUNCTIONS = Analyzer._STRING_RETURNING_STR_FUNCTIONS
    _is_certainly_string = Analyzer._is_certainly_string

    def __init__(self, program: Program, function_names: set[str]) -> None:
        self._symbols = SymbolTable()
        self._condition_reassigned_names = {
            node.target.name for node in _nodes(program)
            if isinstance(node, Assignment) and isinstance(node.target, Identifier)
        }
        self._function_names = function_names
        for name, pine_type in BUILTIN_VARS.items():
            self.define(name, pine_type, declared=True)

    def define(self, name: str, pine_type: PineType, *, declared=False) -> None:
        symbol = Symbol(
            name=name, pine_type=pine_type, is_series=False, is_var=False,
            is_const=False, const_value=None, scope=self._symbols.current_scope.name,
            loc=SourceLocation("<input>", 0, 0, 0),
        )
        symbol._pf_certain_type = pine_type
        symbol._pf_certain_declared = declared
        symbol._pf_certain_string = pine_type == PineType.STRING
        self._symbols.define(symbol)

    def _certain_scalar_type(self, node: ASTNode) -> PineType:
        if isinstance(node, ColorLiteral):
            return PineType.COLOR
        if isinstance(node, FuncCall):
            callee = node.callee
            if isinstance(callee, Identifier):
                if callee.name in self._function_names:
                    return PineType.UNKNOWN
                if callee.name == "color":
                    return PineType.COLOR
            elif isinstance(callee, MemberAccess):
                if not isinstance(callee.object, Identifier):
                    return PineType.UNKNOWN
                # Numeric math overloads can return int or float. Their legacy
                # broad float classification cannot prove an exact match.
                if callee.object.name != "str":
                    return PineType.UNKNOWN
                if self._symbols.resolve("str") is not None:
                    return PineType.UNKNOWN
                if self._is_certainly_string(node):
                    return PineType.STRING
        return Analyzer._certain_scalar_type(self, node)


class _Binder:
    def __init__(self, program: Program, groups: dict[str, list[FuncDef]],
                 identities: dict[int, str], budget: TimeBudget | None) -> None:
        self.groups = groups
        self.identities = identities
        self.budget = budget
        self.visits = 0
        self.proof = _ScalarProof(program, {
            node.name for node in _nodes(program)
            if isinstance(node, (FuncDef, MethodDef))
        })
        self.default_types: dict[int, tuple[PineType, ...]] = {}
        self.parameter_slots = {
            id(definition): {name: index for index, name in enumerate(definition.params)}
            for definitions in groups.values() for definition in definitions
        }

    def _select(self, call: FuncCall, definitions: list[FuncDef]) -> None:
        matches: list[FuncDef] = []
        actual_types = {
            id(value): self.proof._certain_scalar_type(value)
            for value in (*call.args, *call.kwargs.values())
        }
        for definition in definitions:
            names = definition.params
            if len(call.args) > len(names):
                continue
            bound = dict(enumerate(call.args))
            slots = self.parameter_slots[id(definition)]
            for name, value in call.kwargs.items():
                if name not in slots or slots[name] in bound:
                    break
                bound[slots[name]] = value
            else:
                defaults = (definition.annotations or {}).get("param_defaults", ())
                if any(
                    index not in bound
                    and (index >= len(defaults) or defaults[index] is None)
                    for index in range(len(names))
                ):
                    continue
                types = [
                    actual_types[id(bound[index])]
                    if index in bound else self.default_types[id(definition)][index]
                    for index in range(len(names))
                ]
                # No inferred return type, bare na, or partially typed call is
                # enough evidence to replace the final declaration's lookup.
                if any(value not in _SCALAR_TYPES.values() for value in types):
                    return
                hints = (definition.annotations or {})["param_type_hints"]
                if types == [_SCALAR_TYPES[hint] for hint in hints]:
                    matches.append(definition)
        if len(matches) == 1:
            call.callee.name = self.identities[id(matches[0])]

    def _block(self, body: list[ASTNode], bindings=()) -> None:
        self.proof._symbols.enter_scope("overload_block")
        try:
            for name, pine_type in bindings:
                self.proof.define(name, pine_type, declared=True)
            for statement in body:
                self.visit(statement)
        finally:
            self.proof._symbols.exit_scope()

    def visit(self, node: ASTNode | None) -> None:
        if node is None:
            return
        self.visits += 1
        if self.budget is not None and self.visits % 256 == 0:
            self.budget.check(node.loc, phase=Phase.ANALYZER)
        if isinstance(node, (FuncDef, MethodDef)):
            defaults = (node.annotations or {}).get("param_defaults", ())
            for value in defaults:
                self.visit(value)
            self.default_types[id(node)] = tuple(
                self.proof._certain_scalar_type(value)
                if value is not None else PineType.UNKNOWN
                for value in defaults
            )
            hints = (node.annotations or {}).get("param_type_hints", ())
            bindings = [
                (name, _SCALAR_TYPES.get(hints[index], PineType.UNKNOWN)
                 if index < len(hints) else PineType.UNKNOWN)
                for index, name in enumerate(node.params)
            ]
            self._block(node.body, bindings)
        elif isinstance(node, VarDecl):
            self.visit(node.value)
            declared = node.type_hint in _SCALAR_TYPES
            pine_type = (
                _SCALAR_TYPES[node.type_hint] if declared
                else self.proof._certain_scalar_type(node.value)
            )
            self.proof.define(node.name, pine_type, declared=declared)
        elif isinstance(node, TupleAssign):
            self.visit(node.value)
            for name in node.names:
                self.proof.define(name, PineType.UNKNOWN)
        elif isinstance(node, IfStmt):
            self.visit(node.condition)
            self._block(node.body)
            self._block(node.else_body)
        elif isinstance(node, WhileStmt):
            self.visit(node.condition)
            self._block(node.body)
        elif isinstance(node, ForStmt):
            self.visit(node.start)
            self.visit(node.end)
            self.visit(node.step)
            self._block(node.body, [(node.var, PineType.UNKNOWN)])
        elif isinstance(node, ForInStmt):
            self.visit(node.iterable)
            self._block(node.body, [
                (name, PineType.UNKNOWN) for name in (node.vars or [node.var])
                if name is not None
            ])
        elif isinstance(node, SwitchStmt):
            self.visit(node.expr)
            for value, body in node.cases:
                self.visit(value)
                self._block(body)
            self._block(node.default_body)
        elif isinstance(node, FuncCall):
            for value in node.args:
                self.visit(value)
            for value in node.kwargs.values():
                self.visit(value)
            if isinstance(node.callee, Identifier):
                definitions = self.groups.get(node.callee.name)
                if definitions is not None:
                    self._select(node, definitions)
            else:
                self.visit(node.callee)
        else:
            for child in syntax_children(node):
                self.visit(child)


def bind_scalar_overloads(program: Program, *,
                          budget: TimeBudget | None = None) -> Program:
    """Resolve complete, distinct scalar signatures before name-keyed passes.

    Forward and recursive groups retain the previous behavior: every call must
    occur in a statement after the group's final definition. Qualifier-only
    distinctions and duplicate signatures also stay on their original path,
    including the existing duplicate-signature diagnostic.
    """
    groups: dict[str, list[FuncDef]] = {}
    last_definition: dict[str, int] = {}
    for index, statement in enumerate(program.body):
        if isinstance(statement, FuncDef):
            groups.setdefault(statement.name, []).append(statement)
            last_definition[statement.name] = index
    groups = {
        name: definitions for name, definitions in groups.items()
        if len(definitions) > 1
        and all(
            len((definition.annotations or {}).get("param_type_hints", ()))
            == len(definition.params)
            and all(hint in _SCALAR_TYPES for hint in
                    (definition.annotations or {}).get("param_type_hints", ()))
            for definition in definitions
        )
        and len({
            tuple((definition.annotations or {})["param_type_hints"])
            for definition in definitions
        }) == len(definitions)
    }
    if not groups:
        return program
    for index, statement in enumerate(program.body):
        for node in _nodes(statement):
            if (isinstance(node, FuncCall) and isinstance(node.callee, Identifier)
                    and node.callee.name in groups
                    and index <= last_definition[node.callee.name]):
                groups.pop(node.callee.name)
            elif (isinstance(node, FuncDef) and node is not statement
                  and node.name in groups):
                groups.pop(node.name)
    if not groups:
        return program

    # Reserve each authored name's whole suffix family, including call-site
    # variants, without materializing every prefix of a long identifier.
    occupied: set[str] = set()
    for node in _nodes(program):
        name = getattr(node, "name", None)
        if isinstance(name, str):
            occupied.add(name)
        occupied.update(getattr(node, "params", ()))
        if isinstance(node, TupleAssign):
            occupied.update(node.names)
        if isinstance(node, ForStmt):
            occupied.add(node.var)
        if isinstance(node, ForInStmt):
            occupied.update(node.vars or ([node.var] if node.var else []))
    ordered_names = sorted(occupied)
    allocated: set[str] = set()

    def collides(identity: str) -> bool:
        family = identity + "_"
        index = bisect_left(ordered_names, family)
        return (
            identity in occupied or identity in allocated
            or (index < len(ordered_names) and ordered_names[index].startswith(family))
        )

    identities: dict[int, str] = {}
    for name, definitions in groups.items():
        for ordinal, definition in enumerate(definitions[:-1], 1):
            stem = f"pf_overload_{name}_{ordinal}"
            identity = stem
            suffix = 2
            while collides(identity):
                identity = f"{stem}_{suffix}"
                suffix += 1
            allocated.add(identity)
            identities[id(definition)] = identity
        identities[id(definitions[-1])] = name
    binder = _Binder(program, groups, identities, budget)
    for statement in program.body:
        binder.visit(statement)
    for definitions in groups.values():
        # Calls without an exact selection still use the final name. Its
        # signature alone cannot prove a mismatch for the original group.
        fallback = definitions[-1]
        fallback.annotations = {
            **(fallback.annotations or {}), "scalar_overload_fallback": True,
        }
        for definition in definitions:
            definition.name = identities[id(definition)]
    return program
