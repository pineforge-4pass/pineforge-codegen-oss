"""Private spellings for callable locals and colliding history storage."""

from collections import defaultdict
import re

from ..ast_nodes import (
    ForInStmt, ForStmt, FuncCall, FuncDef, Identifier, MemberAccess, MethodDef,
    TupleAssign, VarDecl,
)
from ..limits import iter_ast_nodes
from ..errors import Phase
from .tables import BUILTIN_VARS


# The annotation key of the map from a private declaration spelling to the
# spelling the script authored; the code generator and the analyzer read it.
AUTHORED_LOCAL_NAMES = "authored_local_names"
_CLONE_SUFFIX = re.compile(r"_cs|__ni")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def _builtin_callable_local_names() -> set[str]:
    """Bare builtin variable spellings that can declare callable locals."""
    from .. import signatures as sigs
    from ..codegen.tables import BAR_BUILTINS, BAR_FIELDS, TIME_FIELD_EXPRS
    from ..codegen.visit_expr import _REALTIME_ONLY_VARS
    from ..lexer import KEYWORDS

    return (
        set(BUILTIN_VARS) | set(BAR_BUILTINS) | set(BAR_FIELDS)
        | set(TIME_FIELD_EXPRS) | set(_REALTIME_ONLY_VARS)
        | {name for name in sigs.BUILTIN_VARIABLES if "." not in name}
    ) - KEYWORDS.keys()


def separate_builtin_callable_locals(program, bindings) -> bool:
    """Give builtin-shadowing body declarations ordinary, private names.

    Lexical bindings exclude parameters, globals and uses preceding a
    declaration. Reuse that inventory for every declaration kind and block
    depth, before any type or storage inventory consumes the raw spelling.
    """
    from .value_bindings import _declared_names

    builtin_names = _builtin_callable_local_names()
    colliding = set()
    for scope in bindings.scopes:
        if bindings.budget is not None:
            bindings.budget.check(phase=Phase.ANALYZER)
        if scope.callable is None:
            continue
        for name, declarations in scope.declarations.items():
            if bindings.budget is not None:
                bindings.budget.check(phase=Phase.ANALYZER)
            if name not in builtin_names:
                continue
            for declaration in declarations:
                if bindings.budget is not None:
                    bindings.budget.check(phase=Phase.ANALYZER)
                if declaration.kind in ("local", "loop"):
                    colliding.add((declaration.declaration, name))
    if not colliding:
        return False

    private_clone = re.compile(
        r"((?:" + "|".join(re.escape(name) for name in sorted(builtin_names))
        + r")__pfhist[0-9]+)(?:_cs|__ni)"
    )
    taken = set()
    clone_bases = set()
    declarations = []
    # One source-ordered syntax walk reserves authored names before allocation.
    for node, _ in iter_ast_nodes(program):
        if bindings.budget is not None:
            bindings.budget.check(node.loc, Phase.ANALYZER)
        spellings = []
        for attr in ("name", "var", "member"):
            value = getattr(node, attr, None)
            if isinstance(value, str):
                spellings.append(value)
        for attr in ("params", "names", "vars", "members"):
            spellings.extend(value for value in (getattr(node, attr, None) or [])
                             if isinstance(value, str))
        for spelling in spellings:
            taken.add(spelling)
            clone = private_clone.match(spelling)
            if clone is not None:
                clone_bases.add(clone[1])
        local_names = [
            name for name in _declared_names(node)
            if (id(node), name) in colliding
        ]
        if local_names:
            declarations.append((node, local_names))

    names = {}
    sequence = 0
    for node, local_names in declarations:
        for name in local_names:
            while True:
                if bindings.budget is not None:
                    bindings.budget.check(node.loc, Phase.ANALYZER)
                sequence += 1
                storage = f"{name}__pfhist{sequence}"
                # Reserve both directions of the existing clone namespace
                # rule without scanning all authored names per declaration.
                if (storage not in taken and storage not in clone_bases
                        and not any(
                            storage[:match.start()] in taken
                            for match in _CLONE_SUFFIX.finditer(storage)
                        )):
                    break
            taken.add(storage)
            names[(id(node), name)] = storage

    bindings.rename_declarations(names)
    authored = dict((program.annotations or {}).get(AUTHORED_LOCAL_NAMES, {}))
    for node, local_names in declarations:
        if bindings.budget is not None:
            bindings.budget.check(node.loc, Phase.ANALYZER)
        originals = {names[(id(node), name)]: name for name in local_names}
        node.annotations = {
            **(node.annotations or {}), AUTHORED_LOCAL_NAMES: originals,
        }
        authored.update(originals)
        if isinstance(node, VarDecl):
            node.name = names[(id(node), node.name)]
        elif isinstance(node, TupleAssign):
            node.names = [names.get((id(node), name), name) for name in node.names]
        elif isinstance(node, ForStmt):
            node.var = names[(id(node), node.var)]
        elif isinstance(node, ForInStmt):
            if node.var:
                node.var = names.get((id(node), node.var), node.var)
            if node.vars:
                node.vars = [names.get((id(node), name), name) for name in node.vars]
    program.annotations = {
        **(program.annotations or {}), AUTHORED_LOCAL_NAMES: authored,
    }
    return True


def authored_callable_text(program, text):
    """Restore source names only in diagnostics and input binding keys."""
    names = (program.annotations or {}).get(AUTHORED_LOCAL_NAMES, {})
    if not names or text is None:
        return text

    def authored(match):
        spelling = match[0]
        if spelling in names:
            return names[spelling]
        for suffix in _CLONE_SUFFIX.finditer(spelling):
            original = names.get(spelling[:suffix.start()])
            if original is not None:
                return original + spelling[suffix.start():]
        return spelling

    return _IDENTIFIER.sub(authored, text)


def restore_callable_diagnostics(program, diagnostics) -> bool:
    """Keep private declaration spellings out of analyzer diagnostics."""
    if not (program.annotations or {}).get(AUTHORED_LOCAL_NAMES):
        return False
    changed = False
    for diagnostic in diagnostics:
        message = authored_callable_text(program, diagnostic.message)
        hint = authored_callable_text(program, diagnostic.hint)
        changed |= message != diagnostic.message or hint != diagnostic.hint
        diagnostic.message = message
        diagnostic.hint = hint
    return changed


def separate_callable_histories(program, bindings, primitive_histories) -> bool:
    """Name apart declaration-owned scalar Series before rebuilding inventories.

    Existing call-site and nested-call cloning then operate on disjoint names.
    Only direct nonpersistent FuncDef declarations are migrated. If a history
    group also contains a callable block/tuple/loop declaration, leave the
    whole group to the existing declaration-exact storage diagnostic.
    """
    if not primitive_histories:
        return False
    nodes = [node for node, _ in iter_ast_nodes(program)]
    declarations = defaultdict(list)
    taken = set()
    for node in nodes:
        if bindings.budget is not None:
            bindings.budget.check(node.loc, Phase.ANALYZER)
        for attr in ("name", "var", "member"):
            value = getattr(node, attr, None)
            if isinstance(value, str):
                taken.add(value)
        for attr in ("params", "names", "vars", "members"):
            taken.update(v for v in (getattr(node, attr, None) or [])
                         if isinstance(v, str))
        if isinstance(node, VarDecl):
            declarations[node.name].append(node)
        elif isinstance(node, TupleAssign):
            for name in node.names:
                declarations[name].append(node)
        elif isinstance(node, ForStmt):
            declarations[node.var].append(node)
        elif isinstance(node, ForInStmt):
            for name in (node.vars or [node.var]):
                declarations[name].append(node)

    direct = {}
    other_safe_declarations = {
        id(node) for node in program.body if isinstance(node, VarDecl)
    }
    for func in program.body:
        if not isinstance(func, (FuncDef, MethodDef)):
            continue
        if isinstance(func, FuncDef):
            for node in func.body:
                if isinstance(node, VarDecl) and (node.is_var or node.is_varip):
                    other_safe_declarations.add(id(node))
                if (isinstance(node, VarDecl)
                        and not node.is_var and not node.is_varip
                        and (id(node), node.name) in primitive_histories
                        # Untitled input keys still use the authored binding.
                        # Keep this shape behind the existing storage gate.
                        and not any(
                            isinstance(child, FuncCall)
                            and (
                                isinstance(child.callee, Identifier)
                                and child.callee.name == "input"
                                or isinstance(child.callee, MemberAccess)
                                and isinstance(child.callee.object, Identifier)
                                and child.callee.object.name == "input"
                            )
                            for child, _ in iter_ast_nodes(node)
                        )):
                    direct[id(node)] = node

    names = {}
    sequence = 0
    # Function and declaration source order determines allocation, never id().
    for node in direct.values():
        group = declarations[node.name]
        # Builtin history and a callable's local history are separate bindings
        # even when there is only one authored declaration of that spelling.
        if len(group) < 2 and node.name not in BUILTIN_VARS:
            continue
        if any(id(other) not in other_safe_declarations and id(other) not in direct
               for other in group):
            continue
        while True:
            sequence += 1
            storage = f"{node.name}__pfhist{sequence}"
            # Cloning appends _csN / __niN. Protect the derived namespace too.
            if not any(
                storage == used
                or used.startswith((storage + "_cs", storage + "__ni"))
                or storage.startswith((used + "_cs", used + "__ni"))
                for used in taken
            ):
                break
        taken.add(storage)
        names[(id(node), node.name)] = storage

    if not names:
        return False
    bindings.rename_declarations(names)
    for node in direct.values():
        key = (id(node), node.name)
        if key in names:
            node.annotations = {
                **(node.annotations or {}), "authored_history_name": node.name,
            }
            node.name = names[key]
    return True
