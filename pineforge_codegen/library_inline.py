"""Inline the Pine libraries a script imports.

TradingView links a published library into every script that imports it;
PineForge has no linker, so ``transpile()`` inlines each library's source
into the importing script before the support check, and the rest of the
pipeline sees one program:

- Resolution (``pine_libraries``): the source of every import the script
  uses, from ``libraries=`` or the probe's own requests manifest.
- Modules (``library_modules``): each library parsed on its own, with its own
  ``//@version``, following its own imports.
- Reachability: only the exports the script reaches, and what they reach in
  turn, are inlined. Library example code (plots, inputs) and every unused
  export are dropped, so an unused export PineForge cannot lower does not
  refuse the script.
- Hygiene: every inlined top-level name gets a module-qualified name, and so
  does every local and parameter of an inlined function or method; an
  importing script and several libraries can then share any spelling. A
  keyword argument follows its parameter's new name.
- ``alias.f(...)``, ``alias.T.new(...)``, ``alias.E.member``, ``alias.C`` and
  ``alias.T`` in a type resolve into the library; an exported method is
  called with method syntax and keeps its name (methods bind by receiver
  type). An alias equal to a built-in namespace (``import TradingView/ta/7``
  has none, so it is ``ta``) reads a member the built-in namespace has as the
  built-in, as TradingView's ``usedLibs`` shows, and any other member from
  the library.
- An inlined function is an ordinary user function of the program, so its
  state is per call site exactly as a user function's.
- A v5 library is lowered with v5 rules (``library_v5``).

With no library sources configured the program is returned unchanged: every
import keeps the refusal it has today.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from .ast_nodes import (
    ASTNode, ArgOrder, Assignment, BinOp, BreakStmt, ContinueStmt, EnumDecl,
    ExprStmt, ForInStmt, ForStmt, FuncCall, FuncDef, Identifier, IfStmt,
    ImportStmt, MemberAccess, MethodDef, Program, StrategyDecl, Subscript,
    SwitchStmt, Ternary, TupleAssign, TupleLiteral, TypeAnnotation, TypeDecl,
    TypeField, UnaryOp, VarDecl, WhileStmt,
)
from .errors import CompileError, Diagnostic, Level, Phase, SourceLocation
from .library_modules import LibraryModule, parse_library_module
from .limits import TimeBudget
from .pine_libraries import LibraryResolveError, LibraryResolver, library_resolver
from .support_checker import (
    BUILTIN_NAMESPACE_IMPORT_MEMBERS,
    import_is_builtin_namespace_no_op,
    import_spelling,
)

# Built-in namespaces. An import alias equal to ``ta``, ``math`` or ``str``
# reads the namespace's built-in members as built-ins; an alias equal to any
# other built-in namespace is refused (the member split is unknown there).
_BUILTIN_NAMESPACES = frozenset({
    "ta", "math", "str", "array", "map", "matrix", "color", "input",
    "request", "strategy", "syminfo", "timeframe", "barstate", "session",
    "chart", "label", "line", "box", "table", "linefill", "polyline", "log",
    "runtime", "ticker", "dividends", "earnings", "splits", "currency",
    "display", "extend", "format", "hline", "location", "order", "plot",
    "position", "scale", "shape", "size", "text", "xloc", "yloc", "alert",
    "barmerge", "font", "adjustment", "backadjustment", "settlement_as_close",
    "dayofweek", "footprint", "volume_row", "timenow", "time",
})

_TYPE_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")

# Statement nodes that open a block scope.
_BLOCK_STATEMENTS = (IfStmt, ForStmt, ForInStmt, WhileStmt, SwitchStmt)


def _error(message: str, loc: SourceLocation | None, filename: str,
           hint: str | None = None) -> Diagnostic:
    return Diagnostic(
        level=Level.ERROR, phase=Phase.PARSER,
        location=loc or SourceLocation(file=filename, line=1, col=1, end_col=1),
        message=message, hint=hint,
    )


def _callee_alias(node) -> tuple[str, str] | None:
    """``alias.member`` as (alias, member) when ``node`` spells one."""
    if isinstance(node, MemberAccess) and isinstance(node.object, Identifier):
        return node.object.name, node.member
    return None


class _LinkError(Exception):
    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


class _Module:
    """One loaded library and the names it is inlined under."""

    def __init__(self, lib: LibraryModule, prefix: str) -> None:
        self.lib = lib
        self.prefix = prefix
        self.names: dict[str, str] = {}          # top-level name -> new name
        self.method_names: dict[str, str] = {}   # method name -> emitted name
        self.targets: dict[str, str] = {}        # own alias -> module path
        self.included: dict[int, object] = {}    # reachable top-level nodes
        self.deps: list[str] = []                # modules its code references

    @property
    def path(self) -> str:
        return self.lib.path


class _Linker:
    def __init__(self, program: Program, resolver: LibraryResolver, filename: str,
                 budget: TimeBudget | None) -> None:
        self._program = program
        self._resolver = resolver
        self._filename = filename
        self._budget = budget
        self._modules: dict[str, _Module] = {}
        self._load_order: list[str] = []
        self._queue: list[tuple[_Module, object]] = []
        self._allocated: set[str] = set()
        self._taken: set[str] = set()
        self._local_names: dict[tuple[str, str, str], str] = {}
        self._main_targets: dict[str, str] = {}   # main alias -> module path
        self._main_deps: list[str] = []
        self._main_methods: set[str] = set()

    # ------------------------------------------------------------------
    # Names
    # ------------------------------------------------------------------

    def _collect_names(self, root) -> None:
        stack = [root]
        while stack:
            node = stack.pop()
            if isinstance(node, (list, tuple)):
                stack.extend(node)
                continue
            if isinstance(node, dict):
                stack.extend(node.values())
                continue
            if isinstance(node, TypeField):
                self._taken.add(node.name)
                self._taken.update(_TYPE_TOKEN_RE.findall(node.type_name or ""))
                stack.append(node.default)
                continue
            if not isinstance(node, ASTNode):
                continue
            for key, value in vars(node).items():
                if key == "loc":
                    continue
                if key == "annotations":
                    notes = value or {}
                    for hint in notes.get("param_type_hints") or ():
                        if isinstance(hint, str):
                            self._taken.update(_TYPE_TOKEN_RE.findall(hint))
                    stack.extend(d for d in notes.get("param_defaults") or () if d is not None)
                    continue
                if isinstance(value, str):
                    if key in ("name", "var", "type_hint", "type_name", "member"):
                        self._taken.update(_TYPE_TOKEN_RE.findall(value))
                    continue
                if key in ("params", "names", "vars") and isinstance(value, list):
                    self._taken.update(v for v in value if isinstance(v, str))
                    continue
                stack.append(value)

    def _alloc(self, base: str) -> str:
        name = base
        n = 2
        while name in self._allocated or name in self._taken:
            name = f"{base}_{n}"
            n += 1
        self._allocated.add(name)
        return name

    def _local_name(self, mod: _Module, callable_name: str, name: str) -> str:
        key = (mod.path, callable_name, name)
        if key not in self._local_names:
            self._local_names[key] = self._alloc(f"{mod.prefix}{callable_name}__{name}")
        return self._local_names[key]

    def _prefix(self, lib: LibraryModule) -> str:
        base = f"{lib.name}_v{lib.path.rsplit('/', 1)[1]}"
        prefix = f"{base}__"
        n = 2
        taken_prefixes = {m.prefix for m in self._modules.values()}
        while prefix in taken_prefixes or any(t.startswith(prefix) for t in self._taken):
            prefix = f"{base}_{n}__"
            n += 1
        return prefix

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self, path: str, at: ASTNode) -> _Module:
        mod = self._modules.get(path)
        if mod is not None:
            return mod
        try:
            source = self._resolver.load(path)
        except LibraryResolveError as exc:
            raise _LinkError(_error(
                f"Import is not supported: '{path}': {exc}", at.loc, self._filename))
        lib = parse_library_module(path, source.text, budget=self._budget)
        before = set(self._taken)
        self._collect_names(lib.program)
        clash = sorted((self._taken - before) & self._allocated)
        if clash:
            raise _LinkError(_error(
                f"library '{path}' spells '{clash[0]}', a name PineForge gave "
                "to an inlined definition", at.loc, self._filename))
        mod = _Module(lib, self._prefix(lib))
        self._modules[path] = mod
        self._load_order.append(path)
        for alias, stmt in lib.imports.items():
            mod.targets[alias] = stmt.path
        # Top-level names, in source order (deterministic allocation).
        for stmt in lib.program.body:
            if isinstance(stmt, (FuncDef, TypeDecl, EnumDecl)):
                if stmt.name not in mod.names:
                    mod.names[stmt.name] = self._alloc(mod.prefix + stmt.name)
            elif isinstance(stmt, VarDecl):
                if stmt.name not in mod.names:
                    mod.names[stmt.name] = self._alloc(mod.prefix + stmt.name)
            elif isinstance(stmt, TupleAssign):
                for name in stmt.names:
                    if name != "_" and name not in mod.names:
                        mod.names[name] = self._alloc(mod.prefix + name)
            elif isinstance(stmt, MethodDef):
                if stmt.name not in mod.method_names:
                    exported = any(lib.exported(m) for m in lib.methods[stmt.name])
                    mod.method_names[stmt.name] = (
                        stmt.name if exported else self._alloc(mod.prefix + stmt.name))
        return mod

    def _module_of(self, owner: _Module | None, alias: str, at: ASTNode) -> _Module:
        """The module ``alias`` names in ``owner`` (None: the main script)."""
        targets = self._main_targets if owner is None else owner.targets
        path = targets[alias]
        mod = self._load(path, at)
        deps = self._main_deps if owner is None else owner.deps
        if path not in deps:
            deps.append(path)
        return mod

    # ------------------------------------------------------------------
    # Reachability
    # ------------------------------------------------------------------

    def _include(self, mod: _Module, node) -> None:
        if id(node) not in mod.included:
            mod.included[id(node)] = node
            self._queue.append((mod, node))

    def _include_function(self, mod: _Module, name: str, at: ASTNode) -> str:
        defs = mod.lib.functions[name]
        if len(defs) > 1:
            raise _LinkError(_error(
                f"library '{mod.path}' defines '{name}' {len(defs)} times "
                "(overloads); PineForge does not inline an overloaded library "
                "function", at.loc, self._filename))
        self._include(mod, defs[0])
        return mod.names[name]

    def _include_methods(self, mod: _Module, name: str, exported_only: bool) -> bool:
        found = False
        for method in mod.lib.methods.get(name, ()):
            if exported_only and not mod.lib.exported(method):
                continue
            self._include(mod, method)
            found = True
        return found

    def _include_global(self, mod: _Module, name: str) -> str:
        self._include(mod, mod.lib.globals[name])
        return mod.names[name]

    def _include_type(self, mod: _Module, name: str) -> str:
        node = mod.lib.types.get(name) or mod.lib.enums.get(name)
        self._include(mod, node)
        return mod.names[name]

    def _export(self, owner: _Module | None, alias: str, member: str, at: ASTNode):
        """(kind, module, new name) of ``alias.member`` read from ``owner``,
        or None when it is the built-in namespace's member."""
        builtin = BUILTIN_NAMESPACE_IMPORT_MEMBERS.get(alias)
        if builtin is not None and member in builtin:
            return None
        mod = self._module_of(owner, alias, at)
        lib = mod.lib
        if member in lib.functions and any(lib.exported(f) for f in lib.functions[member]):
            return "function", mod, self._include_function(mod, member, at)
        if member in lib.methods and any(lib.exported(m) for m in lib.methods[member]):
            self._include_methods(mod, member, exported_only=True)
            return "method", mod, mod.method_names[member]
        if member in lib.types and lib.exported(lib.types[member]):
            return "type", mod, self._include_type(mod, member)
        if member in lib.enums and lib.exported(lib.enums[member]):
            return "type", mod, self._include_type(mod, member)
        if member in lib.globals and lib.exported(lib.globals[member]):
            return "const", mod, self._include_global(mod, member)
        known = (member in lib.functions or member in lib.methods
                 or member in lib.types or member in lib.enums or member in lib.globals)
        raise _LinkError(_error(
            f"library '{mod.path}' does not export '{member}'"
            + ("" if known else f": it has no '{member}'"),
            at.loc, self._filename))

    # ------------------------------------------------------------------
    # Types spelled in strings
    # ------------------------------------------------------------------

    def _rewrite_type(self, hint: str | None, owner: _Module | None,
                      at: ASTNode | None) -> str | None:
        if not isinstance(hint, str) or not hint:
            return hint
        targets = self._main_targets if owner is None else owner.targets

        def one(match: re.Match) -> str:
            token = match.group(0)
            head, _, rest = token.partition(".")
            if rest and head in targets and "." not in rest:
                found = self._export(owner, head, rest, at or self._program)
                if found is None or found[0] != "type":
                    raise _LinkError(_error(
                        f"'{token}' is not a type of library '{targets[head]}'",
                        getattr(at, "loc", None), self._filename))
                return found[2]
            if owner is not None and not rest and (
                    token in owner.lib.types or token in owner.lib.enums):
                return self._include_type(owner, token)
            return token

        return _TYPE_TOKEN_RE.sub(one, hint)

    # ------------------------------------------------------------------
    # Library code: renaming with scopes
    # ------------------------------------------------------------------

    def _process(self, mod: _Module, node) -> None:
        if isinstance(node, FuncDef):
            self._process_callable(mod, node, node.name)
            node.name = mod.names[node.name]
        elif isinstance(node, MethodDef):
            self._process_callable(mod, node, node.name)
            node.type_name = self._rewrite_type(node.type_name, mod, node)
            node.name = mod.method_names[node.name]
        elif isinstance(node, TypeDecl):
            node.name = mod.names[node.name]
            for fld in node.fields:
                fld.type_name = self._rewrite_type(fld.type_name, mod, node)
                if fld.default is not None:
                    fld.default = self._expr(mod, fld.default, None)
        elif isinstance(node, EnumDecl):
            node.name = mod.names[node.name]
            node.member_values = {k: self._expr(mod, v, None)
                                  for k, v in node.member_values.items()}
        elif isinstance(node, VarDecl):
            node.type_hint = self._rewrite_type(node.type_hint, mod, node)
            node.value = self._expr(mod, node.value, None)
            node.name = mod.names[node.name]
        elif isinstance(node, TupleAssign):
            node.value = self._expr(mod, node.value, None)
            node.names = [n if n == "_" else mod.names[n] for n in node.names]

    def _process_callable(self, mod: _Module, node, callable_name: str) -> None:
        notes = dict(node.annotations or {})
        hints = list(notes.get("param_type_hints") or ())
        notes["param_type_hints"] = [self._rewrite_type(h, mod, node) for h in hints]
        defaults = list(notes.get("param_defaults") or ())
        notes["param_defaults"] = [None if d is None else self._expr(mod, d, None)
                                   for d in defaults]
        notes["library"] = mod.path
        notes["pine_version"] = mod.lib.pine_version
        node.annotations = notes
        scope = _Scope(self, mod, callable_name)
        node.params = [scope.declare(p) for p in node.params]
        self._stmts(mod, node.body, scope)

    def _stmts(self, mod: _Module, stmts: list, scope: "_Scope") -> None:
        for index, stmt in enumerate(stmts):
            stmts[index] = self._stmt(mod, stmt, scope)

    def _stmt(self, mod: _Module, stmt, scope: "_Scope"):
        if isinstance(stmt, VarDecl):
            stmt.type_hint = self._rewrite_type(stmt.type_hint, mod, stmt)
            stmt.value = self._expr(mod, stmt.value, scope)
            stmt.name = scope.declare(stmt.name)
            return stmt
        if isinstance(stmt, TupleAssign):
            stmt.value = self._expr(mod, stmt.value, scope)
            stmt.names = [n if n == "_" else scope.declare(n) for n in stmt.names]
            return stmt
        if isinstance(stmt, Assignment):
            stmt.value = self._expr(mod, stmt.value, scope)
            stmt.target = self._expr(mod, stmt.target, scope)
            return stmt
        if isinstance(stmt, ExprStmt):
            stmt.expr = self._expr(mod, stmt.expr, scope)
            return stmt
        if isinstance(stmt, (BreakStmt, ContinueStmt)):
            return stmt
        if isinstance(stmt, _BLOCK_STATEMENTS):
            return self._block_node(mod, stmt, scope)
        if isinstance(stmt, ASTNode):
            raise _LinkError(_error(
                f"library '{mod.path}': a {type(stmt).__name__} inside a "
                "function is not supported", stmt.loc, self._filename))
        return stmt

    def _block_node(self, mod: _Module, node, scope: "_Scope"):
        if isinstance(node, IfStmt):
            node.condition = self._expr(mod, node.condition, scope)
            self._stmts(mod, node.body, scope.child())
            self._stmts(mod, node.else_body, scope.child())
        elif isinstance(node, ForStmt):
            node.start = self._expr(mod, node.start, scope)
            node.end = self._expr(mod, node.end, scope)
            if node.step is not None:
                node.step = self._expr(mod, node.step, scope)
            body = scope.child()
            node.var = body.declare(node.var) if node.var else node.var
            self._stmts(mod, node.body, body)
        elif isinstance(node, ForInStmt):
            node.iterable = self._expr(mod, node.iterable, scope)
            body = scope.child()
            if node.var:
                node.var = body.declare(node.var)
            if node.vars:
                node.vars = [v if v == "_" else body.declare(v) for v in node.vars]
            self._stmts(mod, node.body, body)
        elif isinstance(node, WhileStmt):
            node.condition = self._expr(mod, node.condition, scope)
            self._stmts(mod, node.body, scope.child())
        elif isinstance(node, SwitchStmt):
            if node.expr is not None:
                node.expr = self._expr(mod, node.expr, scope)
            cases = []
            for case in node.cases:
                value, body = case
                if value is not None:
                    value = self._expr(mod, value, scope)
                self._stmts(mod, body, scope.child())
                cases.append((value, body))
            node.cases = cases
            self._stmts(mod, node.default_body, scope.child())
        return node

    def _expr(self, mod: _Module, node, scope: "_Scope | None"):
        """``node`` with library names resolved; a replacement node when the
        spelling itself changes (``alias.f`` -> ``f``)."""
        if node is None or not isinstance(node, ASTNode):
            return node
        if isinstance(node, Identifier):
            local = scope.lookup(node.name) if scope is not None else None
            if local is not None:
                node.name = local
            elif node.name in mod.lib.globals:
                node.name = self._include_global(mod, node.name)
            elif node.name in mod.lib.types or node.name in mod.lib.enums:
                node.name = self._include_type(mod, node.name)
            return node
        if isinstance(node, MemberAccess):
            parts = _callee_alias(node)
            if parts is not None and not self._is_local(scope, parts[0]):
                alias, member = parts
                if alias in mod.targets:
                    found = self._export(mod, alias, member, node)
                    if found is None:
                        return node
                    kind, _target, name = found
                    if kind in ("type", "const"):
                        return self._named(name, node)
                    raise _LinkError(_error(
                        f"'{alias}.{member}' of library '{mod.targets[alias]}' is "
                        f"a {kind}, not a value", node.loc, self._filename))
            node.object = self._expr(mod, node.object, scope)
            self._rewrite_template_args(node, mod)
            return node
        if isinstance(node, FuncCall):
            return self._call(mod, node, scope)
        if isinstance(node, TypeAnnotation):
            node.type_name = self._rewrite_type(node.type_name, mod, node)
            return node
        if isinstance(node, _BLOCK_STATEMENTS):
            return self._block_node(mod, node, scope)
        if isinstance(node, (BinOp, UnaryOp, Ternary, Subscript, TupleLiteral)):
            for key, value in list(vars(node).items()):
                if key in ("loc", "annotations"):
                    continue
                if isinstance(value, ASTNode):
                    setattr(node, key, self._expr(mod, value, scope))
                elif isinstance(value, list):
                    setattr(node, key, [self._expr(mod, v, scope) for v in value])
            return node
        return node

    @staticmethod
    def _is_local(scope: "_Scope | None", name: str) -> bool:
        return scope is not None and scope.lookup(name) is not None

    @staticmethod
    def _named(name: str, like: ASTNode) -> Identifier:
        ident = Identifier(name=name)
        ident.loc = like.loc
        return ident

    def _rewrite_template_args(self, node: ASTNode, owner: _Module | None) -> None:
        notes = node.annotations or {}
        args = notes.get("template_args")
        if args:
            node.annotations = dict(notes, template_args=[
                self._rewrite_type(a, owner, node) for a in args])

    def _remap_kwargs(self, mod: _Module, node: FuncCall, callable_name: str,
                      params: set[str]) -> None:
        if node.kwargs:
            node.kwargs = {
                (self._local_name(mod, callable_name, k) if k in params else k): v
                for k, v in node.kwargs.items()
            }

    def _function_params(self, mod: _Module, name: str) -> set[str]:
        return {p for f in mod.lib.functions.get(name, ()) for p in f.params}

    def _method_params(self, mod: _Module, name: str) -> set[str]:
        return {p for m in mod.lib.methods.get(name, ()) for p in m.params[1:]}

    def _call_args(self, owner: _Module | None, node: FuncCall, scope) -> None:
        """Resolve ``node``'s arguments; keep its written argument order."""
        replaced: dict[int, ASTNode] = {}
        args = []
        for arg in node.args:
            new = self._any_expr(owner, arg, scope)
            if new is not arg:
                replaced[id(arg)] = new
            args.append(new)
        kwargs = {}
        for key, arg in node.kwargs.items():
            new = self._any_expr(owner, arg, scope)
            if new is not arg:
                replaced[id(arg)] = new
            kwargs[key] = new
        node.args, node.kwargs = args, kwargs
        notes = node.annotations or {}
        order = notes.get("call_arg_order")
        if replaced and isinstance(order, ArgOrder):
            node.annotations = dict(notes, call_arg_order=ArgOrder(
                replaced.get(id(n), n) for n in order))

    def _any_expr(self, owner: _Module | None, node, scope):
        if owner is None:
            return self._main_expr(node)
        return self._expr(owner, node, scope)

    def _as_method_call(self, node: FuncCall, member: str, at: ASTNode) -> FuncCall:
        """``alias.m(recv, ...)`` -> ``recv.m(...)``."""
        if not node.args:
            raise _LinkError(_error(
                f"library method '{member}' called without its receiver as the "
                "first positional argument", at.loc, self._filename))
        receiver, rest = node.args[0], node.args[1:]
        callee = MemberAccess(object=receiver, member=member)
        callee.loc = node.callee.loc
        node.callee = callee
        node.args = rest
        notes = node.annotations or {}
        order = notes.get("call_arg_order")
        if isinstance(order, ArgOrder):
            node.annotations = dict(notes, call_arg_order=ArgOrder(
                n for n in order if n is not receiver))
        return node

    def _call(self, mod: _Module, node: FuncCall, scope) -> FuncCall:
        self._call_args(mod, node, scope)
        callee = node.callee
        if isinstance(callee, Identifier) and not self._is_local(scope, callee.name):
            name = callee.name
            if name in mod.lib.functions:
                self._remap_kwargs(mod, node, name, self._function_params(mod, name))
                callee.name = self._include_function(mod, name, node)
                return node
            if name in mod.lib.methods:
                # A method called as a function: ``m(recv, ...)``.
                self._include_methods(mod, name, exported_only=False)
                self._remap_kwargs(mod, node, name, self._method_params(mod, name))
                return self._as_method_call(node, mod.method_names[name], node)
            return node
        parts = _callee_alias(callee)
        if parts is not None and not self._is_local(scope, parts[0]):
            alias, member = parts
            if alias in mod.targets:
                found = self._export(mod, alias, member, node)
                if found is None:
                    return node
                kind, target, name = found
                if kind == "function":
                    self._remap_kwargs(target, node, member, self._function_params(target, member))
                    node.callee = self._named(name, callee)
                    return node
                if kind == "method":
                    self._remap_kwargs(target, node, member, self._method_params(target, member))
                    return self._as_method_call(node, name, node)
                raise _LinkError(_error(
                    f"'{alias}.{member}' of library '{mod.targets[alias]}' is a "
                    f"{kind}, not a function", node.loc, self._filename))
            if alias in mod.lib.types or alias in mod.lib.enums:
                callee.object = self._named(self._include_type(mod, alias), callee.object)
                self._rewrite_template_args(callee, mod)
                return node
            if alias in _BUILTIN_NAMESPACES:
                self._rewrite_template_args(callee, mod)
                return node
        if isinstance(callee, MemberAccess):
            callee.object = self._expr(mod, callee.object, scope)
            self._rewrite_template_args(callee, mod)
            self._method_call(mod, node, callee.member)
        return node

    def _method_call(self, owner: _Module | None, node: FuncCall, member: str) -> None:
        """``recv.member(...)``: the library methods it may bind to (methods
        bind by receiver type, so every candidate is inlined)."""
        if owner is not None and member in owner.lib.methods:
            self._include_methods(owner, member, exported_only=False)
            self._remap_kwargs(owner, node, member, self._method_params(owner, member))
            node.callee.member = owner.method_names[member]
            return
        if owner is None:
            # The script's unloaded imports are unpinned and unnamed: linked
            # by nothing.
            candidates = [self._modules[p] for p in self._main_targets.values()
                          if p in self._modules]
        else:
            # TradingView links every import of a linked library.
            candidates = [self._load(path, node) for path in owner.targets.values()]
        exporters = []
        for mod in candidates:
            if self._include_methods(mod, member, exported_only=True):
                exporters.append(mod)
                deps = self._main_deps if owner is None else owner.deps
                if mod.path not in deps:
                    deps.append(mod.path)
        if exporters and node.kwargs:
            if len(exporters) > 1:
                raise _LinkError(_error(
                    f"method '{member}' is exported by {len(exporters)} imported "
                    "libraries; a keyword argument cannot be bound to one",
                    node.loc, self._filename))
            target = exporters[0]
            self._remap_kwargs(target, node, member, self._method_params(target, member))

    # ------------------------------------------------------------------
    # The importing script
    # ------------------------------------------------------------------

    def _main_expr(self, node):
        if node is None or not isinstance(node, ASTNode):
            return node
        if isinstance(node, MemberAccess):
            parts = _callee_alias(node)
            if parts is not None and parts[0] in self._main_targets:
                alias, member = parts
                found = self._export(None, alias, member, node)
                if found is None:
                    return node
                kind, _mod, name = found
                if kind in ("type", "const"):
                    return self._named(name, node)
                raise _LinkError(_error(
                    f"'{alias}.{member}' of library '{self._main_targets[alias]}' "
                    f"is a {kind}, not a value", node.loc, self._filename))
            node.object = self._main_expr(node.object)
            self._rewrite_template_args(node, None)
            return node
        if isinstance(node, FuncCall):
            self._call_args(None, node, None)
            callee = node.callee
            parts = _callee_alias(callee)
            if parts is not None and parts[0] in self._main_targets:
                alias, member = parts
                found = self._export(None, alias, member, node)
                if found is None:
                    self._rewrite_template_args(callee, None)
                    return node
                kind, target, name = found
                if kind == "function":
                    self._remap_kwargs(target, node, member, self._function_params(target, member))
                    node.callee = self._named(name, callee)
                    return node
                if kind == "method":
                    self._remap_kwargs(target, node, member, self._method_params(target, member))
                    return self._as_method_call(node, name, node)
                raise _LinkError(_error(
                    f"'{alias}.{member}' of library '{self._main_targets[alias]}' is "
                    f"a {kind}, not a function", node.loc, self._filename))
            if isinstance(callee, MemberAccess):
                callee.object = self._main_expr(callee.object)
                self._rewrite_template_args(callee, None)
                if not (isinstance(callee.object, Identifier)
                        and callee.object.name in _BUILTIN_NAMESPACES):
                    self._method_call(None, node, callee.member)
            else:
                node.callee = self._main_expr(callee)
            return node
        if isinstance(node, TypeAnnotation):
            node.type_name = self._rewrite_type(node.type_name, None, node)
            return node
        self._main_children(node)
        return node

    def _main_children(self, node: ASTNode) -> None:
        if isinstance(node, (FuncDef, MethodDef)):
            notes = dict(node.annotations or {})
            if "param_type_hints" in notes:
                notes["param_type_hints"] = [
                    self._rewrite_type(h, None, node) for h in notes["param_type_hints"]]
            if "param_defaults" in notes:
                notes["param_defaults"] = [
                    None if d is None else self._main_expr(d)
                    for d in notes["param_defaults"]]
            node.annotations = notes
            if isinstance(node, MethodDef):
                node.type_name = self._rewrite_type(node.type_name, None, node)
        if isinstance(node, VarDecl):
            node.type_hint = self._rewrite_type(node.type_hint, None, node)
        if isinstance(node, TypeDecl):
            for fld in node.fields:
                fld.type_name = self._rewrite_type(fld.type_name, None, node)
                if fld.default is not None:
                    fld.default = self._main_expr(fld.default)
            return
        if isinstance(node, SwitchStmt):
            if node.expr is not None:
                node.expr = self._main_expr(node.expr)
            node.cases = [
                (None if value is None else self._main_expr(value),
                 [self._main_expr(s) for s in body])
                for value, body in node.cases]
            node.default_body = [self._main_expr(s) for s in node.default_body]
            return
        for key, value in list(vars(node).items()):
            if key in ("loc", "annotations"):
                continue
            if isinstance(value, ASTNode):
                setattr(node, key, self._main_expr(value))
            elif isinstance(value, list):
                setattr(node, key, [self._main_expr(v) if isinstance(v, ASTNode) else v
                                    for v in value])
            elif isinstance(value, dict):
                setattr(node, key, {k: self._main_expr(v) if isinstance(v, ASTNode) else v
                                    for k, v in value.items()})

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    def link(self, handled: list[ImportStmt]) -> Program:
        self._collect_names(self._program)
        declared = self._declared_main_names()
        for stmt in handled:
            alias = stmt.alias or stmt.name
            if alias in self._main_targets:
                raise _LinkError(_error(
                    f"import alias '{alias}' is used by two imports", stmt.loc,
                    self._filename))
            if alias in _BUILTIN_NAMESPACES and alias not in BUILTIN_NAMESPACE_IMPORT_MEMBERS:
                raise _LinkError(_error(
                    f"Import is not supported: '{import_spelling(stmt)}': its alias "
                    f"'{alias}' is a built-in namespace other than ta, math or str",
                    stmt.loc, self._filename))
            if alias in declared:
                raise _LinkError(_error(
                    f"import alias '{alias}' is also declared as a variable, "
                    "parameter or function", stmt.loc, self._filename))
            self._main_targets[alias] = stmt.path
        # A library whose alias the script never names is still linked when
        # its source is at hand (its exported methods bind by receiver type);
        # otherwise TradingView links nothing for it and it is dropped.
        referenced = self._referenced_aliases()
        for stmt in handled:
            alias = stmt.alias or stmt.name
            if alias in referenced or self._resolver.pinned(stmt.path):
                self._module_of(None, alias, stmt)
        body = [self._main_expr(s) if not isinstance(s, ImportStmt) else s
                for s in self._program.body]
        while self._queue:
            mod, node = self._queue.pop(0)
            self._process(mod, node)
        self._finish()
        handled_ids = {id(s) for s in handled}
        definitions = self._definitions()
        merged: list = []
        inserted = False
        first_use = self._first_library_use(body, handled_ids)
        for index, stmt in enumerate(body):
            if not inserted and (id(stmt) in handled_ids or index == first_use):
                merged.extend(definitions)
                inserted = True
            if id(stmt) in handled_ids:
                continue
            merged.append(stmt)
        if not inserted:
            merged.extend(definitions)
        program = Program(body=merged, version=self._program.version)
        program.loc = self._program.loc
        program.annotations = self._program.annotations
        return program

    def _finish(self) -> None:
        """Checks that need every reachable definition."""
        # Methods bind by receiver type and name: two with one key would
        # leave one of them silently unreachable.
        owners: dict[str, str] = {}
        for stmt in self._program.body:
            if isinstance(stmt, MethodDef):
                owners.setdefault(f"{stmt.type_name}.{stmt.name}", "the script")
        for path in self._load_order:
            for stmt in self._ordered_included(self._modules[path]):
                if not isinstance(stmt, MethodDef):
                    continue
                key = f"{stmt.type_name}.{stmt.name}"
                other = owners.setdefault(key, f"library '{path}'")
                if other != f"library '{path}'":
                    raise _LinkError(_error(
                        f"method '{stmt.name}' of library '{path}' has the same "
                        f"receiver type as a method of {other}", stmt.loc,
                        self._filename))
        from .library_v5 import lower_v5_modules
        lower_v5_modules(
            [(self._modules[p].lib, self._ordered_included(self._modules[p]))
             for p in self._load_order if self._modules[p].included],
            self._filename,
        )

    def _ordered_included(self, mod: _Module) -> list:
        return [stmt for stmt in mod.lib.program.body if id(stmt) in mod.included]

    def _definitions(self) -> list:
        """Every reachable library definition, a module after the modules its
        code references, each module's in its source order."""
        order: list[str] = []
        visiting: set[str] = set()

        def visit(path: str) -> None:
            if path in order or path in visiting:
                return
            visiting.add(path)
            for dep in self._modules[path].deps:
                visit(dep)
            visiting.discard(path)
            order.append(path)

        for path in self._main_deps:
            visit(path)
        for path in self._load_order:
            visit(path)
        out: list = []
        for path in order:
            out.extend(self._ordered_included(self._modules[path]))
        return out

    def _first_library_use(self, body: list, handled_ids: set[int]) -> int | None:
        """Index of the first statement before every handled import that
        reads a library name (None: the first import comes first)."""
        names = {n for m in self._modules.values() for n in m.names.values()}
        names.update(n for m in self._modules.values() for n in m.method_names.values())
        for index, stmt in enumerate(body):
            if id(stmt) in handled_ids:
                return None
            if isinstance(stmt, StrategyDecl):
                continue
            found: list[str] = []
            self._scan_identifiers(stmt, found)
            if names.intersection(found):
                return index
        return None

    @staticmethod
    def _scan_identifiers(root, out: list) -> None:
        stack = [root]
        while stack:
            node = stack.pop()
            if isinstance(node, (list, tuple)):
                stack.extend(node)
            elif isinstance(node, dict):
                stack.extend(node.values())
            elif isinstance(node, ASTNode):
                if isinstance(node, Identifier):
                    out.append(node.name)
                elif isinstance(node, MemberAccess):
                    out.append(node.member)
                for key, value in vars(node).items():
                    if key not in ("loc", "annotations"):
                        stack.append(value)

    def _declared_main_names(self) -> set[str]:
        names: set[str] = set()
        for node in self._walk(self._program):
            if isinstance(node, (VarDecl, FuncDef, TypeDecl, EnumDecl)):
                names.add(node.name)
            if isinstance(node, (FuncDef, MethodDef)):
                names.update(node.params)
            if isinstance(node, TupleAssign):
                names.update(node.names)
            if isinstance(node, ForStmt) and node.var:
                names.add(node.var)
            if isinstance(node, ForInStmt):
                if node.var:
                    names.add(node.var)
                names.update(node.vars or ())
        return names

    def _referenced_aliases(self) -> set[str]:
        """Main aliases the script names with a library member."""
        found: set[str] = set()
        for node in self._walk(self._program):
            parts = _callee_alias(node)
            if parts is not None and parts[0] in self._main_targets:
                alias, member = parts
                builtin = BUILTIN_NAMESPACE_IMPORT_MEMBERS.get(alias)
                if builtin is None or member not in builtin:
                    found.add(alias)
            for hint in self._type_strings(node):
                for token in _TYPE_TOKEN_RE.findall(hint):
                    head, _, rest = token.partition(".")
                    if rest and head in self._main_targets:
                        found.add(head)
        return found

    @staticmethod
    def _type_strings(node) -> list[str]:
        out = []
        for key in ("type_hint", "type_name"):
            value = getattr(node, key, None)
            if isinstance(value, str):
                out.append(value)
        notes = getattr(node, "annotations", None) or {}
        for key in ("param_type_hints", "template_args"):
            out.extend(h for h in notes.get(key) or () if isinstance(h, str))
        if isinstance(node, TypeDecl):
            out.extend(f.type_name for f in node.fields if isinstance(f.type_name, str))
        return out

    @staticmethod
    def _walk(root):
        stack = [root]
        while stack:
            node = stack.pop()
            if isinstance(node, (list, tuple)):
                stack.extend(node)
                continue
            if isinstance(node, dict):
                stack.extend(node.values())
                continue
            if isinstance(node, TypeField):
                stack.append(node.default)
                continue
            if not isinstance(node, ASTNode):
                continue
            yield node
            for key, value in vars(node).items():
                if key == "loc":
                    continue
                if key == "annotations":
                    stack.extend(d for d in (value or {}).get("param_defaults") or ()
                                 if d is not None)
                    continue
                stack.append(value)


class _Scope:
    """The locals and parameters visible at one point of an inlined body."""

    def __init__(self, linker: _Linker, mod: _Module, callable_name: str,
                 parent: "_Scope | None" = None) -> None:
        self._linker = linker
        self._mod = mod
        self._callable = callable_name
        self._parent = parent
        self._names: dict[str, str] = {}

    def child(self) -> "_Scope":
        return _Scope(self._linker, self._mod, self._callable, self)

    def declare(self, name: str) -> str:
        new = self._linker._local_name(self._mod, self._callable, name)
        self._names[name] = new
        return new

    def lookup(self, name: str) -> str | None:
        scope: _Scope | None = self
        while scope is not None:
            if name in scope._names:
                return scope._names[name]
            scope = scope._parent
        return None


def inline_libraries(program: Program, source: str, *,
                     libraries: Mapping[str, str] | None,
                     filename: str = "<input>",
                     budget: TimeBudget | None = None) -> Program:
    """``program`` with the libraries it imports inlined (see module doc)."""
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
    linker = _Linker(program, resolver, filename, budget)
    try:
        return linker.link(handled)
    except _LinkError as exc:
        raise CompileError([exc.diagnostic]) from None
