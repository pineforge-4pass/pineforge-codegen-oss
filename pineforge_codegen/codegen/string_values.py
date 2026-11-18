"""Representation facts used to select string lowerings.

Complete UTF-8 strings can use native concatenation and boolean searches.
Values that can contain isolated UTF-16 halves need the unit helpers. Follow
declaration identities, history and helper arguments without expanding call
paths; an unknown producer keeps the unit lowering.
"""

from collections import defaultdict, deque

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
        # The callable whose body holds each node. A script statement, a
        # trace expression and a default (evaluated where its call is
        # written) have none.
        self.owners = {}
        # A plain function's parameter receives every value from a written
        # call or a declared default, so only these definitions can prove one.
        self.functions = {
            id(function.node) for function in codegen._func_info_map.values()
            if function.node is not None and not function.is_udt_method
        }
        pending = [(codegen.ctx.ast, None)]
        # A trace expression is emitted too, and can call a helper.
        pending.extend(
            (pragma.expr_node, None)
            for pragma in getattr(codegen.ctx, "pf_trace_pragmas", None) or ()
            if pragma.expr_node is not None
        )
        for function in codegen.ctx.func_infos:
            if function.node is not None:
                pending.append((function.node, None))
            pending.extend(
                (value, None) for value in self._defaults(function) if value is not None)
        while pending:
            node, owner = pending.pop()
            if id(node) in self.nodes:
                continue
            self._check(node)
            self.nodes[id(node)] = node
            self.owners[id(node)] = owner
            if isinstance(node, (FuncDef, MethodDef)):
                owner = node
            pending.extend((child, owner) for child in syntax_children(node))

        declarations = {}
        parameters = defaultdict(list)
        writes, unknown_writes = defaultdict(list), set()
        for node in self.nodes.values():
            self._check(node)
            if isinstance(node, VarDecl):
                declarations[id(node), node.name] = node
            elif isinstance(node, Assignment) and isinstance(node.target, Identifier):
                bindings = self._bindings(node.target)
                if bindings is None or node.op not in (":=", "+="):
                    unknown_writes.add(node.target.name)
                else:
                    for binding in bindings:
                        writes[binding].append(node.value)
            elif isinstance(node, FuncCall) and isinstance(node.callee, Identifier):
                function = codegen._func_info_map.get(node.callee.name)
                if function is None or function.node is None or function.is_udt_method:
                    continue
                names = function.node.params
                defaults = self._defaults(function)
                # A surplus argument or a keyword naming no later parameter
                # cannot be placed: this call then proves no parameter.
                placed = len(node.args) <= len(names) and all(
                    keyword in names[len(node.args):] for keyword in node.kwargs
                )
                for index, name in enumerate(names):
                    argument = None
                    if placed:
                        argument = (
                            node.args[index] if index < len(node.args)
                            else node.kwargs.get(name)
                        )
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

    def _bindings(self, node):
        """Every binding an identifier can name in the emitted tree, or None.

        The lexical resolver is exact for the tree it walked: a declaration,
        a loop, or the callable that declares the name as a parameter. The
        analyzer's first-visit snapshot can name another declaration, because
        a callable's nested blocks share its symbol scope; such a use depends
        on both. None leaves the value unknown.
        """
        ctx = self.codegen.ctx
        visited = id(node) in ctx.identifier_decl_bindings
        snapshot = ctx.identifier_decl_bindings.get(id(node))
        lexical = ctx.identifier_lexical_bindings.get(id(node))
        if lexical is None:
            # A builtin, or a use outside the resolver's walk.
            return None if snapshot is None else (snapshot,)
        owner = self.owners.get(id(node))
        declaration = self.nodes.get(lexical[0])
        holder = self.owners.get(lexical[0])
        if (declaration is None or lexical[1] != node.name
                or (holder is not None and holder is not owner)):
            # Not a binding this use can see in this tree.
            return None
        if isinstance(declaration, (FuncDef, MethodDef)):
            # A parameter is proved only for the plain function holding the
            # use: its values are that definition's written arguments.
            if (declaration is not owner or id(declaration) not in self.functions
                    or node.name not in declaration.params):
                return None
        elif visited and snapshot is None:
            # The analyzer resolved no declaration where the resolver did.
            return None
        if snapshot is None or snapshot == lexical:
            return (lexical,)
        return (lexical, snapshot)

    def _names_value(self, node):
        """Whether an identifier names an authored value, not a namespace."""
        ctx = self.codegen.ctx
        return (ctx.identifier_lexical_bindings.get(id(node)) is not None
                or ctx.identifier_decl_bindings.get(id(node)) is not None)

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
            bindings = self._bindings(node)
            if bindings is not None:
                return list(bindings)
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
                and not self._names_value(callee.object)):
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
