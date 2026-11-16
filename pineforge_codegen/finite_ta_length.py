"""Bounded lowering for finite-choice ``ta.highest``/``ta.lowest`` lengths.

Pine permits a ``series int`` length for its rolling extrema.  PineForge's
runtime extrema objects intentionally keep one fixed constructor length, so a
series-selected length cannot be represented by mutating or rebuilding one
object: after a shorter window has discarded history, a later longer window
cannot recover it.

The small, exact subset handled here needs no runtime ABI change.  When a
top-level extrema declaration chooses between two bar-invariant lengths, emit
one fixed extrema history for each choice, advance both on every bar, and
select the value for the current condition.  For example::

    n = regime ? fastInput : slowInput
    lo = ta.lowest(low, n)

is lowered in the AST to the semantic equivalent::

    pf_ta_choice_1_selected = n
    pf_ta_choice_1_true = ta.lowest(low, fastInput)
    pf_ta_choice_1_false = ta.lowest(low, slowInput)
    lo = pf_ta_choice_1_selected == fastInput ?
        pf_ta_choice_1_true : pf_ta_choice_1_false

The transform is deliberately narrow:

* only direct top-level variable declarations;
* only ``ta.highest`` and ``ta.lowest`` positional call forms;
* exactly one ternary (inline or through a top-level length alias), with a
  direct comparison operator as its condition;
* each branch must be a positive integer literal or a direct immutable
  ``input.int`` alias whose declared domain is provably positive; and
* the source must be a plain identifier (the one-argument default-source form
  is also accepted).

The authored length is snapshotted exactly once at the original call site.
Aliases and every transitive selector/arm alias must be ordinary immutable
declarations: ``var``/``varip`` dependencies and any name reassigned anywhere
in the program stay unsupported.  Direct inline ``input.int`` arms are also
left unsupported so lowering cannot duplicate their declarations.

Everything else retains the existing loud unsupported-length diagnostic.  In
particular, this is not a claim of arbitrary series-length support for EMA,
SMA, pivots, UDF-local sites, request.security evaluators, or unbounded
run-time lengths.
"""

from __future__ import annotations

import copy
from dataclasses import replace

from .ast_nodes import (
    Assignment,
    BinOp,
    FuncCall,
    FuncDef,
    Identifier,
    MemberAccess,
    MethodDef,
    NumberLiteral,
    Program,
    Subscript,
    Ternary,
    TupleAssign,
    TupleLiteral,
    UnaryOp,
    VarDecl,
)


_EXTREMA_NAMES = frozenset({"highest", "lowest"})
_COMPARISON_OPS = frozenset({"==", "!=", "<", "<=", ">", ">="})
_ARITHMETIC_OPS = frozenset({"+", "-", "*", "/", "%"})
_BAR_IDENTIFIERS = frozenset({
    "open", "high", "low", "close", "volume",
    "hl2", "hlc3", "ohlc4", "hlcc4",
    "time", "time_close", "bar_index", "last_bar_index", "timenow",
})


def _call_name(node: FuncCall) -> tuple[str | None, str | None]:
    callee = node.callee
    if not isinstance(callee, MemberAccess):
        return None, None
    if not isinstance(callee.object, Identifier):
        return None, None
    return callee.object.name, callee.member


def _is_input_int_call(node) -> bool:
    return (
        isinstance(node, FuncCall)
        and _call_name(node) == ("input", "int")
    )


def _is_positive_int_literal(node) -> bool:
    return (
        isinstance(node, NumberLiteral)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
        and node.value > 0
    )


def _is_positive_bounded_input_int(node) -> bool:
    """Whether an input owns a provably positive run-time domain."""

    if not _is_input_int_call(node) or not node.args:
        return False
    if not _is_positive_int_literal(node.args[0]):
        return False

    minval = node.kwargs.get("minval")
    if _is_positive_int_literal(minval):
        return True

    options = node.kwargs.get("options")
    return (
        isinstance(options, TupleLiteral)
        and bool(options.elements)
        and all(_is_positive_int_literal(item) for item in options.elements)
    )


def _is_fixed_int_length(
    node,
    definitions: dict[str, tuple[int, VarDecl]],
    *,
    before_index: int,
    ambiguous_names: frozenset[str] = frozenset(),
) -> bool:
    """Whether ``node`` is one bar-invariant integer length choice.

    The only admitted leaves are a positive literal or one direct, unique,
    top-level ``input.int`` alias whose entire selectable domain is positive.
    This deliberately excludes alias chains and unconstrained inputs.
    """

    if _is_positive_int_literal(node):
        return True
    # A direct inline input call would be copied into both fixed histories.
    if _is_input_int_call(node):
        return False
    if (
        not isinstance(node, Identifier)
        or node.name in ambiguous_names
    ):
        return False
    found = definitions.get(node.name)
    if found is None:
        return False
    def_index, declaration = found
    if def_index >= before_index or declaration.value is None:
        return False
    if declaration.type_hint not in (None, "int"):
        return False
    return _is_positive_bounded_input_int(declaration.value)


def _is_numeric_expr(
    node,
    definitions: dict[str, tuple[int, VarDecl]],
    *,
    before_index: int,
    assigned_names: set[str],
    ambiguous_names: frozenset[str],
    authored_names: frozenset[str],
    seen: frozenset[str] = frozenset(),
) -> bool:
    """Prove the small numeric expression subset used by Wellman's regime."""

    if isinstance(node, NumberLiteral):
        return not isinstance(node.value, bool)
    if isinstance(node, Identifier):
        if node.name in _BAR_IDENTIFIERS:
            return node.name not in authored_names
        if node.name in seen or node.name in ambiguous_names:
            return False
        found = definitions.get(node.name)
        if found is None:
            return False
        def_index, declaration = found
        if (
            def_index >= before_index
            or declaration.value is None
            or declaration.is_var
            or declaration.is_varip
            or declaration.name in assigned_names
            # Wellman's selector aliases are float-valued.  An explicit int
            # alias can mask a float-to-int annotation error, so integer length
            # inputs are admitted only by the separate fixed-length proof.
            or declaration.type_hint not in (None, "float")
        ):
            return False
        return _is_numeric_expr(
            declaration.value,
            definitions,
            before_index=def_index,
            assigned_names=assigned_names,
            ambiguous_names=ambiguous_names,
            authored_names=authored_names,
            seen=seen | {node.name},
        )
    if isinstance(node, UnaryOp):
        return node.op in {"+", "-"} and _is_numeric_expr(
            node.operand,
            definitions,
            before_index=before_index,
            assigned_names=assigned_names,
            ambiguous_names=ambiguous_names,
            authored_names=authored_names,
            seen=seen,
        )
    if isinstance(node, BinOp):
        return node.op in _ARITHMETIC_OPS and all(
            _is_numeric_expr(
                child,
                definitions,
                before_index=before_index,
                assigned_names=assigned_names,
                ambiguous_names=ambiguous_names,
                authored_names=authored_names,
                seen=seen,
            )
            for child in (node.left, node.right)
        )
    if isinstance(node, Ternary):
        return _is_numeric_comparison(
            node.condition,
            definitions,
            before_index=before_index,
            assigned_names=assigned_names,
            ambiguous_names=ambiguous_names,
            authored_names=authored_names,
            seen=seen,
        ) and all(
            _is_numeric_expr(
                child,
                definitions,
                before_index=before_index,
                assigned_names=assigned_names,
                ambiguous_names=ambiguous_names,
                authored_names=authored_names,
                seen=seen,
            )
            for child in (node.true_val, node.false_val)
        )
    if isinstance(node, FuncCall):
        namespace, name = _call_name(node)
        if namespace == "input" and name in {"int", "float"}:
            return True
        # Keep the TA proof intentionally evidence-bound to Wellman's ATRs.
        if not (
            namespace == "ta"
            and name == "atr"
            and not node.kwargs
            and len(node.args) == 1
        ):
            return False
        length = node.args[0]
        if not _is_fixed_int_length(
            length,
            definitions,
            before_index=before_index,
            ambiguous_names=ambiguous_names,
        ):
            return False
        if isinstance(length, Identifier):
            _def_index, declaration = definitions[length.name]
            if (
                declaration.is_var
                or declaration.is_varip
                or length.name in assigned_names
            ):
                return False
        return True
    return False


def _is_numeric_comparison(
    node,
    definitions: dict[str, tuple[int, VarDecl]],
    *,
    before_index: int,
    assigned_names: set[str],
    ambiguous_names: frozenset[str],
    authored_names: frozenset[str],
    seen: frozenset[str] = frozenset(),
) -> bool:
    """Whether ``node`` is a comparator over two proven numeric operands."""

    return (
        isinstance(node, BinOp)
        and node.op in _COMPARISON_OPS
        and _is_numeric_expr(
            node.left,
            definitions,
            before_index=before_index,
            assigned_names=assigned_names,
            ambiguous_names=ambiguous_names,
            authored_names=authored_names,
            seen=seen,
        )
        and _is_numeric_expr(
            node.right,
            definitions,
            before_index=before_index,
            assigned_names=assigned_names,
            ambiguous_names=ambiguous_names,
            authored_names=authored_names,
            seen=seen,
        )
    )


def _resolve_choice(
    node,
    definitions: dict[str, tuple[int, VarDecl]],
    *,
    before_index: int,
    seen: frozenset[str] = frozenset(),
    ambiguous_names: frozenset[str] = frozenset(),
    aliases: tuple[VarDecl, ...] = (),
) -> tuple[Ternary, int, tuple[VarDecl, ...]] | None:
    """Resolve one inline or top-level-aliased ternary length expression."""

    if isinstance(node, Ternary):
        return node, before_index, aliases
    if (
        not isinstance(node, Identifier)
        or node.name in seen
        or node.name in ambiguous_names
    ):
        return None
    found = definitions.get(node.name)
    if found is None:
        return None
    def_index, declaration = found
    if def_index >= before_index or declaration.value is None:
        return None
    return _resolve_choice(
        declaration.value,
        definitions,
        before_index=def_index,
        seen=seen | {node.name},
        ambiguous_names=ambiguous_names,
        aliases=aliases + (declaration,),
    )


def _depends_on_series(
    node,
    definitions: dict[str, tuple[int, VarDecl]],
    *,
    before_index: int,
    seen: frozenset[str] = frozenset(),
    ambiguous_names: frozenset[str] = frozenset(),
) -> bool:
    """Conservatively identify a condition that is definitely bar-varying.

    A stable input/timeframe selector was already supported by the ordinary
    one-object runtime-reset route.  Expanding it would be semantically valid
    but would churn generated output and state for no benefit, so this pass is
    enabled only when a series dependency is visible through simple top-level
    aliases.
    """

    if isinstance(node, Identifier):
        if node.name in _BAR_IDENTIFIERS:
            return True
        if node.name in seen or node.name in ambiguous_names:
            return False
        found = definitions.get(node.name)
        if found is None:
            return False
        def_index, declaration = found
        if def_index >= before_index or declaration.value is None:
            return False
        return _depends_on_series(
            declaration.value,
            definitions,
            before_index=def_index,
            seen=seen | {node.name},
            ambiguous_names=ambiguous_names,
        )
    if isinstance(node, Subscript):
        return True
    if isinstance(node, (BinOp,)):
        return _depends_on_series(
            node.left, definitions, before_index=before_index, seen=seen,
            ambiguous_names=ambiguous_names,
        ) or _depends_on_series(
            node.right, definitions, before_index=before_index, seen=seen,
            ambiguous_names=ambiguous_names,
        )
    if isinstance(node, UnaryOp):
        return _depends_on_series(
            node.operand, definitions, before_index=before_index, seen=seen,
            ambiguous_names=ambiguous_names,
        )
    if isinstance(node, Ternary):
        return any(
            _depends_on_series(
                child, definitions, before_index=before_index, seen=seen,
                ambiguous_names=ambiguous_names,
            )
            for child in (node.condition, node.true_val, node.false_val)
        )
    if isinstance(node, MemberAccess):
        if isinstance(node.object, Identifier):
            if node.object.name in {"barstate", "strategy"}:
                return True
            if node.object.name in {"input", "timeframe", "syminfo", "math"}:
                return False
        return _depends_on_series(
            node.object, definitions, before_index=before_index, seen=seen,
            ambiguous_names=ambiguous_names,
        )
    if isinstance(node, FuncCall):
        namespace, _name = _call_name(node)
        if namespace in {"ta", "strategy", "request"}:
            return True
        if namespace == "input":
            return False
        if namespace in {"math", "timeframe"}:
            return any(
                _depends_on_series(
                    arg, definitions, before_index=before_index, seen=seen,
                    ambiguous_names=ambiguous_names,
                )
                for arg in node.args
            )
        # Bare time/session functions and other calls are not admitted merely
        # because their result might be dynamic.  The bounded route requires a
        # dependency it can prove, keeping unknown UDF semantics unchanged.
        return False
    return False


def _plain_extrema_call(node) -> tuple[str, object, int] | None:
    """Return ``(name, source, length_index)`` for the bounded call form."""

    if not isinstance(node, FuncCall) or node.kwargs:
        return None
    namespace, name = _call_name(node)
    if namespace != "ta" or name not in _EXTREMA_NAMES:
        return None
    if len(node.args) == 1:
        # ta.highest(length) / ta.lowest(length): the analyzer supplies the
        # native high/low source after this transform.
        return name, None, 0
    if len(node.args) != 2 or not isinstance(node.args[0], Identifier):
        return None
    expected_source = "low" if name == "lowest" else "high"
    if node.args[0].name != expected_source:
        return None
    return name, node.args[0], 1


def _all_bound_names(program: Program) -> set[str]:
    """Collect authored binders so generated top-level names cannot collide."""

    names: set[str] = set()

    def walk(node) -> None:
        if node is None:
            return
        if isinstance(node, VarDecl):
            names.add(node.name)
        if isinstance(node, (FuncDef, MethodDef)):
            names.add(node.name)
        params = getattr(node, "params", None)
        if isinstance(params, list):
            names.update(name for name in params if isinstance(name, str))
        var = getattr(node, "var", None)
        if isinstance(var, str):
            names.add(var)
        vars_ = getattr(node, "vars", None)
        if isinstance(vars_, list):
            names.update(name for name in vars_ if isinstance(name, str))
        tuple_names = getattr(node, "names", None)
        if isinstance(tuple_names, list):
            names.update(name for name in tuple_names if isinstance(name, str))
        if not hasattr(node, "__dict__"):
            return
        for value in vars(node).values():
            if isinstance(value, list):
                for child in value:
                    if isinstance(child, tuple):
                        for item in child:
                            walk(item)
                    else:
                        walk(child)
            elif isinstance(value, dict):
                for child in value.values():
                    walk(child)
            else:
                walk(value)

    walk(program)
    return names


def _assigned_names(program: Program) -> set[str]:
    """Return top-level-scope names explicitly written anywhere in a bar.

    Assignments nested in control flow still mutate the surrounding Pine
    variable.  Callable bodies are included conservatively as well: even an
    authored-invalid attempt to mutate a global dependency must not become
    executable merely because this lowering ran first.
    """

    names: set[str] = set()

    def walk(node) -> None:
        if node is None:
            return
        if isinstance(node, Assignment) and isinstance(node.target, Identifier):
            names.add(node.target.name)
        if isinstance(node, TupleAssign):
            names.update(node.names)
        if not hasattr(node, "__dict__"):
            return
        for value in vars(node).values():
            if isinstance(value, list):
                for child in value:
                    if isinstance(child, tuple):
                        for item in child:
                            walk(item)
                    else:
                        walk(child)
            elif isinstance(value, dict):
                for child in value.values():
                    walk(child)
            else:
                walk(value)

    for statement in program.body:
        walk(statement)
    return names


def _dependency_declarations(
    node,
    definitions: dict[str, tuple[int, VarDecl]],
    *,
    before_index: int,
    ambiguous_names: frozenset[str] = frozenset(),
    authored_names: frozenset[str] = frozenset(),
) -> tuple[dict[str, VarDecl], bool]:
    """Resolve all visible top-level aliases transitively used by ``node``."""

    dependencies: dict[str, VarDecl] = {}
    valid = True

    def walk(current, limit: int) -> None:
        nonlocal valid
        if current is None:
            return
        if isinstance(current, Identifier):
            if current.name in ambiguous_names:
                valid = False
                return
            found = definitions.get(current.name)
            if found is None:
                if current.name in authored_names:
                    # A tuple/loop/callable/other authored binder is not a
                    # visible unique top-level VarDecl in this bounded model.
                    # It must not be mistaken for an external Pine builtin.
                    valid = False
                return
            def_index, declaration = found
            if def_index >= limit:
                # A known global exists, but not yet at this authored lexical
                # point.  Treat it as an invalid forward reference rather than
                # silently compiling against a first-bar default.
                valid = False
                return
            if current.name in dependencies:
                return
            dependencies[current.name] = declaration
            walk(declaration.value, def_index)
            return
        if isinstance(current, (FuncDef, MethodDef)):
            return
        if not hasattr(current, "__dict__"):
            return
        for value in vars(current).values():
            if isinstance(value, list):
                for child in value:
                    if isinstance(child, tuple):
                        for item in child:
                            walk(item, limit)
                    else:
                        walk(child, limit)
            elif isinstance(value, dict):
                for child in value.values():
                    walk(child, limit)
            else:
                walk(value, limit)

    walk(node, before_index)
    return dependencies, valid


def expand_finite_choice_extrema_lengths(program: Program) -> Program:
    """Return ``program`` with the bounded finite-choice extrema lowering.

    The parser's original tree is left untouched.  This matters because the
    support checker runs on the authored program and because callers may reuse
    a parsed AST in tests or diagnostics.
    """

    if not isinstance(program, Program):
        return program

    definition_occurrences: dict[str, list[tuple[int, VarDecl]]] = {}
    for index, statement in enumerate(program.body):
        if isinstance(statement, VarDecl):
            definition_occurrences.setdefault(statement.name, []).append(
                (index, statement)
            )
    definitions = {
        name: occurrences[-1]
        for name, occurrences in definition_occurrences.items()
    }
    ambiguous_names = frozenset(
        name
        for name, occurrences in definition_occurrences.items()
        if len(occurrences) != 1
    )
    top_level_tuple_names = frozenset(
        name
        for statement in program.body
        if isinstance(statement, TupleAssign)
        for name in statement.names
    )
    used_names = _all_bound_names(program)
    authored_names = frozenset(used_names)
    if authored_names.intersection({"ta", "input"} | _BAR_IDENTIFIERS):
        # Namespace or bar-series spellings shadowed by any authored binder
        # cannot be proven to denote Pine's native values in this pre-analysis
        # pass.  Fail closed for the whole bounded transform.
        return program
    assigned_names = _assigned_names(program)
    counter = 0

    def fresh_choice_names() -> tuple[str, str, str]:
        nonlocal counter
        while True:
            counter += 1
            candidates = tuple(
                f"pf_ta_length_choice_{counter}_{suffix}"
                for suffix in ("selected", "true", "false")
            )
            if not any(candidate in used_names for candidate in candidates):
                used_names.update(candidates)
                return candidates

    body: list = []
    for index, statement in enumerate(program.body):
        if not isinstance(statement, VarDecl):
            body.append(statement)
            continue
        if (
            statement.name in ambiguous_names
            or statement.name in top_level_tuple_names
            or statement.is_var
            or statement.is_varip
            or statement.type_hint not in (None, "float")
        ):
            # Rolling extrema return float; lowering must not erase an authored
            # duplicate/persistent result binder or incompatible result
            # annotation via the synthetic per-bar selection.
            body.append(statement)
            continue

        matched = _plain_extrema_call(statement.value)
        if matched is None:
            body.append(statement)
            continue
        _name, _source, length_index = matched
        if _source is not None and _source.name in authored_names:
            # The bounded two-argument route is only for Pine's native low or
            # high series, never any authored binder shadowing that spelling.
            body.append(statement)
            continue
        call = statement.value
        length_node = call.args[length_index]
        resolved_choice = _resolve_choice(
            length_node,
            definitions,
            before_index=index,
            ambiguous_names=ambiguous_names,
        )
        if resolved_choice is None:
            body.append(statement)
            continue
        choice, choice_index, choice_aliases = resolved_choice
        if any(
            declaration.type_hint not in (None, "int")
            for declaration in choice_aliases
        ):
            body.append(statement)
            continue
        if not _is_numeric_comparison(
            choice.condition,
            definitions,
            before_index=choice_index,
            assigned_names=assigned_names,
            ambiguous_names=ambiguous_names,
            authored_names=authored_names,
        ):
            body.append(statement)
            continue
        if not _depends_on_series(
            choice.condition,
            definitions,
            before_index=choice_index,
            ambiguous_names=ambiguous_names,
        ):
            body.append(statement)
            continue
        if not _is_fixed_int_length(
            choice.true_val,
            definitions,
            before_index=choice_index,
            ambiguous_names=ambiguous_names,
        ) or not _is_fixed_int_length(
            choice.false_val,
            definitions,
            before_index=choice_index,
            ambiguous_names=ambiguous_names,
        ):
            body.append(statement)
            continue

        dependencies, dependencies_valid = _dependency_declarations(
            length_node,
            definitions,
            before_index=index,
            ambiguous_names=ambiguous_names,
            authored_names=authored_names,
        )
        if not dependencies_valid or any(
            declaration.is_var
            or declaration.is_varip
            or name in assigned_names
            for name, declaration in dependencies.items()
        ):
            body.append(statement)
            continue

        selected_name, true_name, false_name = fresh_choice_names()

        true_args = copy.deepcopy(call.args)
        false_args = copy.deepcopy(call.args)
        true_args[length_index] = copy.deepcopy(choice.true_val)
        false_args[length_index] = copy.deepcopy(choice.false_val)
        true_call = replace(call, args=true_args, kwargs={})
        false_call = replace(call, args=false_args, kwargs={})
        true_decl = VarDecl(
            name=true_name,
            value=true_call,
            loc=call.loc,
        )
        false_decl = VarDecl(
            name=false_name,
            value=false_call,
            loc=call.loc,
        )
        selected_length_decl = VarDecl(
            name=selected_name,
            value=copy.deepcopy(length_node),
            loc=call.loc,
        )
        selected = Ternary(
            condition=BinOp(
                left=Identifier(name=selected_name, loc=call.loc),
                op="==",
                right=copy.deepcopy(choice.true_val),
                loc=call.loc,
            ),
            true_val=Identifier(name=true_name, loc=call.loc),
            false_val=Identifier(name=false_name, loc=call.loc),
            loc=call.loc,
        )

        body.extend((
            selected_length_decl,
            true_decl,
            false_decl,
            replace(statement, value=selected),
        ))

    return replace(program, body=body)
