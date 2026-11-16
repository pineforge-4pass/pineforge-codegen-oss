"""Representation facts used to select string lowerings.

Complete UTF-8 strings can use native concatenation and boolean searches.
Values that can contain isolated UTF-16 halves need the unit helpers. Follow
declaration identities, history and helper arguments without expanding call
paths; an unknown producer keeps the unit lowering.
"""

from collections import defaultdict, deque

from ..analyzer.value_bindings import ValueBindings
from ..ast_nodes import (
    Assignment, BinOp, BoolLiteral, ColorLiteral, ExprStmt, FuncCall, FuncDef,
    Identifier, IfStmt, MemberAccess, MethodDef, NaLiteral, NumberLiteral,
    StringLiteral, Subscript, SwitchStmt, Ternary, VarDecl,
)
from ..errors import Phase
from ..limits import syntax_children


class StringValues:
    """Conservative facts shared by chart and requested expression emission."""

    def __init__(self, codegen):
        self.codegen = codegen
        self.nodes = {}
        pending = [codegen.ctx.ast]
        for function in codegen.ctx.func_infos:
            if function.node is not None:
                pending.append(function.node)
            pending.extend(value for value in self._defaults(function) if value is not None)
        while pending:
            node = pending.pop()
            if id(node) in self.nodes:
                continue
            self._check(node)
            self.nodes[id(node)] = node
            pending.extend(syntax_children(node))

        declarations = {}
        parameters = defaultdict(list)
        writes, unknown_writes = defaultdict(list), set()
        for node in self.nodes.values():
            self._check(node)
            if isinstance(node, VarDecl):
                declarations[id(node), node.name] = node
            elif isinstance(node, Assignment) and isinstance(node.target, Identifier):
                binding = self._binding(node.target)
                if binding is None:
                    unknown_writes.add(node.target.name)
                elif node.op in (":=", "+="):
                    writes[binding].append(node.value)
                else:
                    unknown_writes.add(node.target.name)
            elif isinstance(node, FuncCall) and isinstance(node.callee, Identifier):
                function = codegen._func_info_map.get(node.callee.name)
                if function is None or function.node is None or function.is_udt_method:
                    continue
                for index, name in enumerate(function.node.params):
                    argument = (
                        node.args[index] if index < len(node.args)
                        else node.kwargs.get(name)
                    )
                    defaults = self._defaults(function)
                    if argument is None and index < len(defaults):
                        argument = defaults[index]
                    parameters[id(function.node), name].append(argument)

        dependencies = {}
        for key, node in self.nodes.items():
            self._check(node)
            dependencies[key] = self._dependencies(node)
        for binding, declaration in declarations.items():
            dependencies[binding] = (
                None if declaration.name in unknown_writes
                else [id(declaration.value), *(id(value) for value in writes[binding])]
            )
        for binding, arguments in parameters.items():
            dependencies[binding] = (
                None if binding[1] in unknown_writes or any(value is None for value in arguments)
                else [id(value) for value in (*arguments, *writes[binding])]
            )

        # Each fact is a conjunction of its dependencies. Propagate unknown
        # leaves backwards once, including through forwarding/recursive edges.
        # This visits the syntax graph, never the exponentially many call paths.
        reverse = defaultdict(list)
        unknown = set()
        for key, sources in dependencies.items():
            if sources is None:
                unknown.add(key)
                continue
            for source in sources:
                reverse[source].append(key)
                if source not in dependencies:
                    unknown.add(source)
        queue = deque(unknown)
        while queue:
            source = queue.popleft()
            self._check(self.nodes.get(source))
            for dependent in reverse[source]:
                if dependent not in unknown:
                    unknown.add(dependent)
                    queue.append(dependent)
        self.canonical = dependencies.keys() - unknown

    def _check(self, node):
        if self.codegen._budget is not None:
            self.codegen._budget.check(getattr(node, "loc", None), Phase.CODEGEN)

    @staticmethod
    def _defaults(function):
        return function.param_defaults or (
            (function.node.annotations or {}).get("param_defaults", ())
            if function.node is not None else ()
        )

    def _binding(self, node):
        bindings = self.codegen.ctx.identifier_decl_bindings
        return (bindings[id(node)] if id(node) in bindings
                else ValueBindings.declaration_of(node))

    def _dependencies(self, node):
        if isinstance(node, StringLiteral):
            return ([] if not any(0xD800 <= ord(char) < 0xE000 for char in node.value)
                    else None)
        if isinstance(node, (NumberLiteral, BoolLiteral, ColorLiteral)):
            # Numeric/bool literals produce complete ASCII text.
            return []
        if isinstance(node, NaLiteral):
            # Bare na still needs its consuming slot's scalar family. Let
            # the typed writer handle it before proving a native string join.
            return None
        if isinstance(node, Identifier):
            binding = self._binding(node)
            if binding is not None:
                return [binding]
            # A runtime builtin is not a string producer. In particular, do
            # not borrow its numeric chart type to prove a formatter/helper
            # result in a different binding context.
            return None
        if isinstance(node, (VarDecl, ExprStmt)):
            return [id(node.value if isinstance(node, VarDecl) else node.expr)]
        if isinstance(node, Subscript):
            # History does not change a scalar's representation. Collection
            # producers remain unknown, so this cannot prove their elements.
            return [id(node.object)]
        if isinstance(node, BinOp) and node.op == "+":
            return [id(node.left), id(node.right)]
        if isinstance(node, Ternary):
            return [id(node.true_val), id(node.false_val)]
        if isinstance(node, IfStmt):
            return [id(body[-1]) for body in (node.body, node.else_body) if body]
        if isinstance(node, SwitchStmt):
            return [id(body[-1]) for _, body in node.cases if body] + (
                [id(node.default_body[-1])] if node.default_body else []
            )
        if isinstance(node, (FuncDef, MethodDef)):
            # Effectful helpers keep the ordered unit-search lowering.
            if (not node.body
                    or any(not isinstance(statement, (VarDecl, Assignment))
                           for statement in node.body[:-1])):
                return None
            return [id(node.body[-1])]
        if not isinstance(node, FuncCall):
            return None
        callee = node.callee
        if isinstance(callee, Identifier):
            function = self.codegen._func_info_map.get(callee.name)
            if function is not None and function.node is not None and not function.is_udt_method:
                return [id(function.node)]
            if callee.name == "string":
                return [id(value) for value in (*node.args, *node.kwargs.values())]
        if (isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier)
                and self._binding(callee.object) is None):
            if (callee.object.name == "str"
                    and callee.member in {"tostring", "upper", "lower", "trim", "repeat",
                                          "format", "replace", "replace_all"}):
                return [id(value) for value in (*node.args, *node.kwargs.values())]
            if callee.object.name == "request" and callee.member == "security":
                expression = node.args[2] if len(node.args) > 2 else node.kwargs.get("expression")
                return [id(expression)]
        return None

    def is_canonical(self, node) -> bool:
        return node is not None and id(node) in self.canonical
