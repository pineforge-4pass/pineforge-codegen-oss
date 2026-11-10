"""Resolve authored value uses by lexical scope and declaration position.

The type analyzer and emitter keep inventories of storage names. Those
inventories cannot establish whether a declaration is visible at a Pine use.
Keep the authored binding on the read itself so a copied request argument
retains its call-site environment.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..ast_nodes import (
    ASTNode, Assignment, EnumDecl, ForInStmt, ForStmt, FuncCall, FuncDef,
    Identifier, IfStmt, MemberAccess, MethodDef, Program, StrategyDecl, SwitchStmt,
    TupleAssign, TypeDecl, VarDecl, WhileStmt,
)
from ..errors import Phase
from ..limits import TimeBudget, iter_ast_nodes, syntax_children
from .diagnostics import DiagnosticsHelper
from .tables import BUILTIN_VARS


_VALUE_USE = "pf_authored_value_use"


@dataclass(frozen=True)
class BindingInfo:
    """Describe an identifier's binding kind and declaring callable."""

    kind: str
    callable: str | None


@dataclass(frozen=True)
class _Binding:
    name: str
    declaration: int
    position: int
    kind: str
    callable: str | None = None


@dataclass(frozen=True)
class _ValueUse:
    binding: _Binding | None
    refusal: str | None = None


@dataclass
class _Scope:
    parent: _Scope | None
    kind: str
    callable: str | None = None
    declarations: dict[str, list[_Binding]] = field(default_factory=dict)

    def resolve(self, name: str, position: int) -> _Binding | None:
        scope = self
        while scope is not None:
            # Field labels are inventoried for default diagnostics, but are
            # never values available to another field's initializer.
            if scope.kind != "fields":
                for binding in reversed(scope.declarations.get(name, ())):
                    if binding.position < position:
                        return binding
            scope = scope.parent
        return None

    def enclosing(self, kind: str) -> _Scope | None:
        scope = self
        while scope is not None:
            if scope.kind == kind:
                return scope
            scope = scope.parent
        return None

    def inside(self, kind: str) -> bool:
        return self.enclosing(kind) is not None


def _declared_names(node: ASTNode):
    if isinstance(node, (FuncDef, MethodDef)):
        return tuple(node.params)
    if isinstance(node, VarDecl):
        return (node.name,)
    if isinstance(node, TupleAssign):
        return tuple(name for name in node.names if name != "_")
    if isinstance(node, ForStmt):
        return (node.var,)
    if isinstance(node, ForInStmt):
        return tuple(name for name in (node.vars or [node.var]) if name and name != "_")
    return ()


class ValueBindings(DiagnosticsHelper):
    """One scope walk for reads, receivers, and reassignment targets.

    A declaration is visible only at later positions in its own scope and
    descendant scopes. Inventorying the whole tree lets diagnostics retain
    their established wording; it never makes an inaccessible binding visible.
    Frozen use annotations contain no scope/tree references, so lowering can
    copy them without copying the lexical environment.
    """

    def __init__(self, program: Program, filename: str,
                 budget: TimeBudget | None = None) -> None:
        self.program = program
        self._filename = filename
        self.budget = budget
        self.position = 0
        self.scopes: list[_Scope] = []
        self.uses: list[tuple[Identifier, _Scope, int, str]] = []
        # Current-tree identities for numeric source queries. Authored-use
        # annotations can retain pre-lowering identities through a copy.
        self.declaration_bindings: dict[int, tuple[int, str] | None] = {}

    def _scope(self, parent: _Scope | None, kind: str,
               callable_name: str | None = None) -> _Scope:
        if callable_name is None and parent is not None:
            callable_name = parent.callable
        scope = _Scope(parent, kind, callable=callable_name)
        self.scopes.append(scope)
        return scope

    def _declare(self, scope: _Scope, name: str, node: ASTNode, kind: str) -> None:
        if name == "_":
            return
        self.position += 1
        scope.declarations.setdefault(name, []).append(
            _Binding(name, id(node), self.position, kind, scope.callable),
        )

    def binding_of(self, node: Identifier) -> BindingInfo | None:
        """Return the kind and declaring callable recorded on this identifier."""
        use = (node.annotations or {}).get(_VALUE_USE)
        if not isinstance(use, _ValueUse) or use.binding is None:
            return None
        return BindingInfo(use.binding.kind, use.binding.callable)

    @staticmethod
    def declaration_of(node: Identifier) -> tuple[int, str] | None:
        """Read an authored variable declaration without visiting its use."""
        use = (node.annotations or {}).get(_VALUE_USE)
        if (not isinstance(use, _ValueUse) or use.binding is None
                or use.binding.kind not in ("global", "local")):
            return None
        return use.binding.declaration, use.binding.name

    def check_use(self, node: Identifier) -> None:
        use = (node.annotations or {}).get(_VALUE_USE)
        if (not isinstance(use, _ValueUse) or use.binding is not None
                or use.refusal is None):
            return
        if use.refusal == "write":
            self._error(f"Undefined variable: '{node.name}'", node.loc)
        if use.refusal == "late_global":
            self._error(
                f"Global variable '{node.name}' is read before its declaration "
                "in a function.",
                node.loc,
            )
        if use.refusal == "outside":
            self._error(
                f"Block-local variable '{node.name}' is read outside its block.",
                node.loc,
            )
        self._error(
            f"Variable '{node.name}' is not declared in the current lexical scope before this use.",
            node.loc,
        )

    def rename_declarations(self, names: dict[tuple[int, str], str]) -> None:
        """Rename only uses resolved to these exact current-tree declarations.

        Keep copied authored-use metadata consistent for the next analysis.
        Initializers and earlier global reads retain their exact bindings;
        parameter uses change only when their declaring callable is selected.
        """
        for node, _scope, _position, _role in self.uses:
            key = self.declaration_bindings.get(id(node))
            if key not in names:
                continue
            if self.budget is not None:
                self.budget.check(node.loc, Phase.ANALYZER)
            node.name = names[key]
            self.declaration_bindings[id(node)] = (key[0], node.name)
            use = (node.annotations or {}).get(_VALUE_USE)
            if isinstance(use, _ValueUse) and use.binding is not None:
                node.annotations[_VALUE_USE] = replace(
                    use, binding=replace(
                        use.binding, name=node.name, declaration=key[0],
                    ),
                )

    def _missing(self, name: str, scope: _Scope, position: int,
                 role: str) -> str | None:
        # These distinctions choose an existing diagnostic, not visibility.
        # Every caller has already failed the same _Scope.resolve lookup.
        if role == "write":
            return "write"
        if role == "qualifier":
            # An unavailable value leaves the namespace/type handler in charge.
            return None
        fields = scope.enclosing("fields")
        declarations = [
            binding for owner in self.scopes
            if owner.kind != "builtin"
            and (owner.kind != "fields" or owner is fields)
            for binding in owner.declarations.get(name, ())
        ]
        if not declarations:
            # Wholly unknown values/type qualifiers keep the analyzer's and
            # emitter's existing fallback, warning or specialized diagnostic.
            # The measured scope rule concerns an authored declaration that
            # is unavailable at this use.
            return None
        if scope.inside("default"):
            return "unbound"
        globals_ = self.global_scope.declarations.get(name, ())
        if scope.inside("callable") and any(b.position > position for b in globals_):
            return "late_global"
        # Retain PF-E2054 for inaccessible loop binders, including a use before
        # their loop, and PF-E2075 for a later ordinary declaration.
        if any(binding.kind == "loop" for binding in declarations):
            return "outside"
        if any(binding.position > position for binding in declarations):
            return "unbound"
        return "outside"

    def resolve(self) -> None:
        if self.program.version != 6:
            return  # Analyzer owns missing/unsupported-version diagnostics.
        # Import after Analyzer has loaded; reuse its namespace inventory.
        from .base import _BUILTIN_NAMESPACE_NAMES

        # Keep qualifiers until declarations are known: a visible value binds
        # normally, and a missing value leaves namespace/type handling intact.
        self.namespaces = _BUILTIN_NAMESPACE_NAMES | {
            node.name for node, _ in iter_ast_nodes(self.program)
            if isinstance(node, (TypeDecl, EnumDecl))
        }
        self.builtins = self._scope(None, "builtin")
        self.builtins.declarations = {
            name: [_Binding(name, 0, 0, "builtin")]
            for name in BUILTIN_VARS
        }
        self.global_scope = self._scope(self.builtins, "global")
        self._body(self.program.body, self.global_scope)
        for node, scope, position, role in self.uses:
            if self.budget is not None:
                self.budget.check(node.loc, Phase.ANALYZER)
            binding = scope.resolve(node.name, position)
            self.declaration_bindings[id(node)] = (
                (binding.declaration, binding.name)
                if binding is not None and binding.kind != "builtin" else None
            )
            origin = (node.annotations or {}).get(_VALUE_USE)
            if not isinstance(origin, _ValueUse):
                origin = _ValueUse(
                    binding,
                    None if binding is not None else self._missing(
                        node.name, scope, position, role,
                    ),
                )
                node.annotations = {**(node.annotations or {}), _VALUE_USE: origin}
            self.check_use(node)

    def _body(self, body: list, scope: _Scope) -> None:
        for stmt in body:
            self._visit(stmt, scope)

    def _block(self, body: list, parent: _Scope) -> None:
        self._body(body, self._scope(parent, "block"))

    def _default(self, value: ASTNode | None, parent: _Scope) -> None:
        # Defaults keep main's handling of preceding globals. Parameters and
        # fields do not introduce value bindings in the default's environment.
        if value is not None:
            self._visit(value, self._scope(parent, "default"))

    def _visit(self, node: ASTNode | None, scope: _Scope) -> None:
        if node is None:
            return
        self.position += 1
        if self.budget is not None:
            self.budget.check(node.loc, Phase.ANALYZER)
        if (node.annotations or {}).get("pine_version") == 5:
            # Inlined v5 code keeps its policy; its exported globals still
            # introduce bindings the v6 script can read.
            if isinstance(node, (VarDecl, TupleAssign)):
                for name in _declared_names(node):
                    self._declare(scope, name, node, scope.kind)
            return
        if isinstance(node, Identifier):
            self.uses.append((node, scope, self.position, "value"))
        elif isinstance(node, StrategyDecl):
            # Strategy settings have their own source-ordered constant
            # resolver and diagnostics; they are not runtime value reads.
            return
        elif isinstance(node, (FuncDef, MethodDef)):
            if scope.kind != "global":
                # Preserve the structural definition errors ahead of value
                # resolution in a body that cannot legally be a callable.
                if isinstance(node, FuncDef):
                    if scope.inside("callable"):
                        self._error(
                            f"Function '{node.name}' cannot be defined inside a function.",
                            node.loc,
                        )
                    self._error(
                        f"Function '{node.name}' cannot be defined inside a local block.",
                        node.loc,
                    )
                return
            for value in (node.annotations or {}).get("param_defaults", ()):
                self._default(value, scope)
            local = self._scope(scope, "callable", node.name)
            for name in node.params:
                self._declare(local, name, node, "parameter")
            self._body(node.body, local)
        elif isinstance(node, TypeDecl):
            fields = self._scope(scope, "fields")
            for item in node.fields:
                self._default(item.default, fields)
                self._declare(fields, item.name, node, "field")
        elif isinstance(node, (VarDecl, TupleAssign)):
            self._visit(node.value, scope)
            for name in _declared_names(node):
                self._declare(
                    scope, name, node, "global" if scope.kind == "global" else "local",
                )
        elif isinstance(node, Assignment):
            # Locate the first offending authored use, including a write to a
            # declaration whose storage survived its lexical scope.
            if isinstance(node.target, Identifier):
                self.position += 1
                self.uses.append((node.target, scope, self.position, "write"))
            else:
                self._visit(node.target, scope)
            self._visit(node.value, scope)
        elif isinstance(node, IfStmt):
            self._visit(node.condition, scope)
            self._block(node.body, scope)
            self._block(node.else_body, scope)
        elif isinstance(node, ForStmt):
            self._visit(node.start, scope)
            local = self._scope(scope, "block")
            for name in _declared_names(node):
                self._declare(local, name, node, "loop")
            self._visit(node.end, local)
            self._visit(node.step, local)
            self._body(node.body, local)
        elif isinstance(node, ForInStmt):
            self._visit(node.iterable, scope)
            local = self._scope(scope, "block")
            for name in _declared_names(node):
                self._declare(local, name, node, "loop")
            self._body(node.body, local)
        elif isinstance(node, WhileStmt):
            self._visit(node.condition, scope)
            self._block(node.body, scope)
        elif isinstance(node, SwitchStmt):
            self._visit(node.expr, scope)
            for expr, body in node.cases:
                self._visit(expr, scope)
                self._block(body, scope)
            self._block(node.default_body, scope)
        elif isinstance(node, FuncCall):
            # A bare callee is a callable label, not a value-variable read.
            if not isinstance(node.callee, Identifier):
                self._visit(node.callee, scope)
            order = (node.annotations or {}).get("call_arg_order")
            for arg in order if order is not None else [*node.args, *node.kwargs.values()]:
                self._visit(arg, scope)
        elif isinstance(node, MemberAccess):
            if (isinstance(node.object, Identifier)
                    and node.object.name in self.namespaces):
                self.position += 1
                self.uses.append((node.object, scope, self.position, "qualifier"))
            else:
                self._visit(node.object, scope)
        else:
            for child in reversed(list(syntax_children(node))):
                self._visit(child, scope)
