"""Assign input override keys before the compiler copies or spells calls."""

from .ast_nodes import ArgOrder, BinOp, Identifier, StringLiteral
from .errors import Phase
from .pine_spelling import global_input_calls


_ASSIGNED = "pf_input_keys_assigned"


def assign_input_keys(program, budget=None) -> None:
    """Normalize later explicit duplicate titles to ``title#<ordinal>``.

    Only the internal title slot changes: it already carries the opaque key
    through getters, TA argument spellings, request copies and descriptors.
    Unique titles and untitled calls retain their exact AST and emitted text.
    Resolve the same literal/concatenation/immutable-global title family as
    codegen; leave unproved titles for its existing located diagnostic.
    Reserve every proved authored title before choosing unused suffixes.
    """
    if (program.annotations or {}).get(_ASSIGNED):
        return
    index = None

    def title_value(node, seen=frozenset()):
        nonlocal index
        if isinstance(node, StringLiteral):
            return node.value
        if isinstance(node, BinOp) and node.op == "+":
            left = title_value(node.left, seen)
            right = title_value(node.right, seen)
            return None if left is None or right is None else left + right
        if isinstance(node, Identifier):
            if index is None:
                from .security_contexts import ScriptIndex
                index = ScriptIndex(program)
            binding = index.refs.get(id(node))
            if (binding is None or binding[0] != "global"
                    or binding in seen or binding in index.unstable):
                return None
            declaration = index.decls.get(binding)
            if declaration is not None:
                return title_value(declaration.value, seen | {binding})
        return None

    titled_calls = []
    authored_titles = set()
    for statement in program.body:
        if budget is not None:
            budget.check(statement.loc, Phase.ANALYZER)
        for call in global_input_calls(statement):
            title = call.args[1] if len(call.args) > 1 else call.kwargs.get("title")
            if title is None:
                continue
            value = title_value(title)
            if value is None:
                continue
            titled_calls.append((call, title, value))
            authored_titles.add(value)

    ordinals = {}
    assigned_keys = set()
    for call, title, value in titled_calls:
        if budget is not None:
            budget.check(call.loc, Phase.ANALYZER)
        ordinal = ordinals.get(value, 0) + 1
        if ordinal == 1:
            ordinals[value] = ordinal
            assigned_keys.add(value)
            continue
        key_value = f"{value}#{ordinal}"
        while key_value in authored_titles or key_value in assigned_keys:
            ordinal += 1
            key_value = f"{value}#{ordinal}"
        ordinals[value] = ordinal
        assigned_keys.add(key_value)
        key = StringLiteral(value=key_value, loc=title.loc)
        if len(call.args) > 1:
            call.args = [*call.args[:1], key, *call.args[2:]]
        else:
            call.kwargs = {**call.kwargs, "title": key}
        order = (call.annotations or {}).get("call_arg_order")
        if isinstance(order, ArgOrder):
            call.annotations = {
                **call.annotations,
                "call_arg_order": ArgOrder(key if arg is title else arg for arg in order),
            }
    program.annotations = {**(program.annotations or {}), _ASSIGNED: True}
