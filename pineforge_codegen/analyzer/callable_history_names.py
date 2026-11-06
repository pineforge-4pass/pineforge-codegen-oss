"""Private storage spellings for colliding direct callable history locals."""

from collections import defaultdict

from ..ast_nodes import (
    ForInStmt, ForStmt, FuncCall, FuncDef, Identifier, MemberAccess, MethodDef,
    TupleAssign, VarDecl,
)
from ..limits import iter_ast_nodes
from ..errors import Phase


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
        if len(group) < 2:
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
