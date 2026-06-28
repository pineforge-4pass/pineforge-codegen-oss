"""Pure-utility codegen helpers.

Mixin holding stateless / near-stateless name-mangling and AST-walk
helpers used everywhere in the codegen. Lives here so the heavier
visitor / emitter mixins can depend on it without owning its
implementation. Keep this module free of imports from any other
``codegen/*`` submodule so it stays at the bottom of the dependency
graph.

Mixin contract: ``NamingHelper`` reads at most one piece of host state,
``self._all_member_names`` (used by ``_func_safe_name``). The class
mixing this in (``CodeGen``) sets that attribute in its constructor.
"""

from __future__ import annotations

from ..ast_nodes import (
    Identifier, MemberAccess,
    VarDecl, Assignment, TupleAssign, ForStmt, ForInStmt,
)


# C++ reserved names that conflict with PineScript identifiers when used
# verbatim as variable names. Carried forward from the historic codegen.py
# table; intentionally narrower than the full C++ keyword set because Pine
# already reserves keywords like ``if``/``else``/``for``/``return`` so they
# can never reach codegen as identifier strings. Lives here (not base.py)
# so the mixin can read it without forcing an import cycle on ``base``.
CPP_RESERVED = {
    "exp", "log", "abs", "max", "min", "and", "or", "not",
    "int", "float", "bool", "string", "short", "long", "new", "delete",
    "class", "struct", "return", "void", "auto", "const", "static",
}


# Bare C++ identifiers that ``strategy.*`` (and a few other) read-only
# accessors lower to as zero-arg free-function calls — e.g.
# ``strategy.grossprofit`` -> ``gross_profit()`` (see codegen/visit_expr.py
# and codegen/emit_top.py). A user variable emitted with one of these names
# becomes a class member that shadows the engine accessor, so the codegen
# would emit ``gross_profit = gross_profit();`` and clang rejects the call
# ("called object type 'double' is not a function"). Escaping such user
# identifiers in ``_safe_name`` keeps the two namespaces disjoint; the
# accessor call strings are emitted verbatim and never routed through
# ``_safe_name``, so they are unaffected. Keep in sync with the accessor
# lowerings if new bare-call accessors are added.
BUILTIN_ACCESSOR_NAMES = {
    "signed_position_size", "position_entry_name",
    "count_wintrades", "count_losstrades", "eventrades",
    "net_profit", "gross_profit", "gross_loss",
    "grossprofit_percent", "grossloss_percent",
    "max_contracts_held_all", "max_contracts_held_long",
    "max_contracts_held_short", "max_drawdown_percent", "max_runup_percent",
    "avg_trade", "avg_trade_percent", "avg_winning_trade", "avg_losing_trade",
    "avg_winning_trade_percent", "avg_losing_trade_percent",
    "margin_liquidation_price", "open_profit", "current_equity",
    "open_trades_capital_held",
}


class NamingHelper:
    """Identifier escaping, callee resolution, and a generic AST walker.

    Mixed into ``CodeGen``; not meant to be instantiated standalone.
    Methods that need shared state (``_all_member_names``) document the
    contract explicitly so substitution is safe."""

    # Set by CodeGen.__init__; declared here only as documentation of the
    # mixin contract. Stays a plain set; the host class owns the value.
    _all_member_names: set[str]

    @staticmethod
    def _cpp_string_escape(s: str) -> str:
        """Escape a Python string for embedding inside a C++ string literal."""
        return (
            s.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
        )

    def _safe_name(self, name: str) -> str:
        """Rename identifiers that collide with C++ reserved words."""
        if name in CPP_RESERVED or name in BUILTIN_ACCESSOR_NAMES:
            return f"_{name}_"
        return name

    def _func_safe_name(self, name: str) -> str:
        """Prefix function names that collide with class members (series vars or var members)."""
        safe = self._safe_name(name)
        if safe in self._all_member_names:
            return f"_fn_{safe}"
        return safe

    def _resolve_callee(self, callee) -> tuple[str | None, str | None]:
        """Extract ``(func_name, namespace)`` from a callee expression.

        - ``foo()``                        -> ``("foo", None)``
        - ``ns.foo()``                     -> ``("foo", "ns")``
        - ``strategy.risk.max_orders(...)`` -> ``("max_orders", "strategy")``
          (only the outermost root namespace is reported; nested chains
           collapse to the leftmost identifier — historical behavior).
        - anything else                    -> ``(None, None)``"""
        if isinstance(callee, Identifier):
            return callee.name, None
        if isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier):
            return callee.member, callee.object.name
        if isinstance(callee, MemberAccess) and isinstance(callee.object, MemberAccess):
            if isinstance(callee.object.object, Identifier):
                return callee.member, callee.object.object.name
        return None, None

    def _get_target_name(self, target) -> str | None:
        """Return the bare name of an assignment target, or ``None`` for non-trivial LHS."""
        if isinstance(target, Identifier):
            return target.name
        return None

    def _walk_ast(self, node):
        """Yield every node in the subtree rooted at ``node`` (including ``node``).

        Walks every attribute that historically holds an AST child or list
        of children: ``body``/``else_body``/``cases``/``default_body``,
        the standard binary/unary/ternary slots, ``args``, ``kwargs``,
        and ``TypeDecl.fields[*].default``. Order is depth-first; the
        visit order itself is not part of the public contract — only the
        set of yielded nodes."""
        if node is None:
            return
        yield node
        for attr in ("body", "else_body"):
            children = getattr(node, attr, None)
            if isinstance(children, list):
                for child in children:
                    yield from self._walk_ast(child)
        if hasattr(node, "cases") and isinstance(node.cases, list):
            for expr, stmts in node.cases:
                if expr is not None:
                    yield from self._walk_ast(expr)
                for child in stmts:
                    yield from self._walk_ast(child)
        if hasattr(node, "default_body") and isinstance(node.default_body, list):
            for child in node.default_body:
                yield from self._walk_ast(child)
        for attr in ("value", "target", "condition", "true_val", "false_val",
                     "left", "right", "object", "operand", "callee", "index",
                     "expr"):
            child = getattr(node, attr, None)
            if child is not None:
                yield from self._walk_ast(child)
        args = getattr(node, "args", None)
        if isinstance(args, list):
            for a in args:
                yield from self._walk_ast(a)
        kwargs = getattr(node, "kwargs", None)
        if isinstance(kwargs, dict):
            for v in kwargs.values():
                yield from self._walk_ast(v)
        fields = getattr(node, "fields", None)
        if isinstance(fields, list):
            for f in fields:
                if hasattr(f, "default") and f.default is not None:
                    yield from self._walk_ast(f.default)

    def _collect_binding_names(self, stmts) -> set[str]:
        """Return every name bound by a statement in ``stmts`` (recursively):
        ``var``/plain declarations, assignment targets, tuple-assign names, and
        ``for`` loop variables. Used to teach the unknown-identifier guard
        about ordinary function-local scalars, which are emitted inline and are
        otherwise tracked nowhere (``func_var_members`` only carries vars that
        become persistent struct members)."""
        names: set[str] = set()
        for stmt in stmts or []:
            for n in self._walk_ast(stmt):
                if isinstance(n, VarDecl) and n.name:
                    names.add(n.name)
                elif isinstance(n, TupleAssign):
                    names.update(x for x in n.names if x)
                elif isinstance(n, Assignment) and isinstance(n.target, Identifier):
                    names.add(n.target.name)
                elif isinstance(n, ForStmt) and n.var:
                    names.add(n.var)
                elif isinstance(n, ForInStmt):
                    if n.var:
                        names.add(n.var)
                    if n.vars:
                        names.update(x for x in n.vars if x)
        return names
