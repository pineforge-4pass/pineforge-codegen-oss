"""Bind strategy declaration settings and prove their constant values once."""

from collections import Counter

from ..ast_nodes import EnumDecl, FuncDef, Identifier, StrategyDecl, StringLiteral, TypeDecl, VarDecl
from ..errors import CompileError, Diagnostic, Level, Phase, SourceLocation
from ..limits import iter_ast_nodes
from ..signatures import MATH_CONSTANTS, STRATEGY_PARAMETERS
from .constant_fold import NON_CONSTANT, NamedConstant, fold_constant_expression
from .tables import DAYOFWEEK_MAP, NAME_ECHO_STRING_MEMBERS


DECLARATION_VALUES = "pf_strategy_declaration_values"
DECLARATION_CONSTANTS = "pf_strategy_declaration_constants"
DECLARATION_ONLY_CONSTANT = "pf_strategy_declaration_only_constant"
_NAMED_CONSTANTS = {
    f"strategy.{name}": NamedConstant(f"strategy.{name}", name.rsplit(".", 1)[-1])
    for name in (
        "fixed", "cash", "percent_of_equity", "commission.percent",
        "commission.cash_per_order", "commission.cash_per_contract",
        "long", "short", "oca.none", "oca.cancel", "oca.reduce",
        "direction.all", "direction.long", "direction.short",
    )
}
_NAMED_CONSTANTS.update({
    f"{namespace}.{member}": NamedConstant(f"{namespace}.{member}", member)
    for namespace in ("currency", "format")
    for member in NAME_ECHO_STRING_MEMBERS[namespace]
})
_NAMED_CONSTANTS.update({
    f"scale.{member}": NamedConstant(f"scale.{member}", member)
    for member in ("left", "right", "none")
})
_NAMED_CONSTANTS.update({f"math.{name}": value for name, value in MATH_CONSTANTS.items()})
_NAMED_CONSTANTS.update({f"dayofweek.{name}": int(value) for name, value in DAYOFWEEK_MAP.items()})
_ENUM_PARAMETERS = {
    "default_qty_type": "strategy.",
    "commission_type": "strategy.commission.",
}


def _argument_error(node, filename, message):
    raise CompileError([Diagnostic(
        level=Level.ERROR,
        phase=Phase.ANALYZER,
        location=node.loc or SourceLocation(file=filename, line=1, col=1, end_col=1),
        message=message,
    )])


def strategy_arguments(node, filename="<input>"):
    """Bind positionals before keywords; a parameter can be supplied once."""
    expected_count, actual_count = len(STRATEGY_PARAMETERS), len(node.args)
    if actual_count > expected_count:
        _argument_error(
            node.args[expected_count], filename,
            f"strategy(): too many positional arguments (expected {expected_count}, got {actual_count})",
        )
    arguments = dict(zip(STRATEGY_PARAMETERS, node.args))
    for name, argument in node.kwargs.items():
        if name in arguments:
            _argument_error(
                argument, filename,
                f"strategy(): argument '{name}' passed both positionally and by keyword",
            )
        arguments[name] = argument
    return arguments


def declaration_only_constants(program):
    """Find constant carriers whose reads all end in declaration arguments.

    Walk syntax once and propagate escapes back through initializer dependencies.
    Runtime reads, including reads from a callable, retain the ordinary checks.
    """
    declarations = [node for node in program.body if isinstance(node, VarDecl)]
    counts = Counter(node.name for node in declarations)
    seeds = {node.name for statement in program.body if isinstance(statement, StrategyDecl)
             for node, _depth in iter_ast_nodes(statement)
             if isinstance(node, Identifier) and node.name in counts}
    if not seeds:
        return set()
    known_values, candidates = {}, {}
    for statement in program.body:
        if isinstance(statement, VarDecl):
            value, proven = fold_constant_expression(statement.value, known_values, _NAMED_CONSTANTS)
            if (proven and counts[statement.name] == 1
                    and not statement.is_var and not statement.is_varip
                    and (statement.annotations or {}).get("qualifier") not in ("series", "simple")):
                candidates[statement.name] = statement
                known_values[statement.name] = value
            else:
                known_values[statement.name] = NON_CONSTANT
        elif isinstance(statement, (FuncDef, EnumDecl, TypeDecl)):
            known_values[statement.name] = NON_CONSTANT
    dependencies, escapes = {}, set()
    for statement in program.body:
        reads = {node.name for node, _depth in iter_ast_nodes(statement)
                 if isinstance(node, Identifier) and node.name in candidates}
        if isinstance(statement, VarDecl) and candidates.get(statement.name) is statement:
            dependencies[statement.name] = reads
        elif not isinstance(statement, StrategyDecl):
            escapes.update(reads)
    allowed = set()
    pending = list(seeds.intersection(candidates))
    while pending:
        name = pending.pop()
        if name in allowed:
            continue
        allowed.add(name)
        pending.extend(dependencies.get(name, ()))
    for name, reads in dependencies.items():
        if name not in allowed:
            escapes.update(reads)
    pending = list(escapes)
    while pending:
        for name in dependencies.get(pending.pop(), ()):
            if name not in escapes:
                escapes.add(name)
                pending.append(name)
    return {id(candidates[name]) for name in allowed - escapes}


def resolve_strategy_declarations(program, reassigned_names, filename, budget=None):
    """Resolve only preceding, nonpersistent, never-reassigned declarations.

    Analysis supplies lexical reassignment information after visiting the
    program. Folding still proceeds in source order so a later declaration
    cannot supply a value to an earlier strategy setting.
    """
    known_values = {}
    parameters = {}
    declaration_only = declaration_only_constants(program)
    last_declaration = max(
        (index for index, node in enumerate(program.body) if isinstance(node, StrategyDecl)),
        default=-1,
    )
    for index, statement in enumerate(program.body):
        if index > last_declaration:
            break
        if budget is not None and index % 128 == 0:
            budget.check(statement.loc, Phase.ANALYZER)
        if isinstance(statement, VarDecl):
            value, proven = fold_constant_expression(statement.value, known_values, _NAMED_CONSTANTS)
            if (not proven or statement.is_var or statement.is_varip
                    or statement.name in reassigned_names
                    or (statement.annotations or {}).get("qualifier") in ("series", "simple")):
                value = NON_CONSTANT
            elif statement.type_hint == "float" and type(value) is int:
                value = float(value)
            if (id(statement) in declaration_only and isinstance(value, NamedConstant)
                    and value.spelling.startswith("scale.")):
                statement.annotations = {
                    **(statement.annotations or {}), DECLARATION_ONLY_CONSTANT: True,
                }
            known_values[statement.name] = value
        elif isinstance(statement, (FuncDef, EnumDecl, TypeDecl)):
            known_values[statement.name] = NON_CONSTANT
        elif isinstance(statement, StrategyDecl):
            values = {}
            constants = {}
            for name, argument in strategy_arguments(statement, filename).items():
                value, proven = fold_constant_expression(argument, known_values, _NAMED_CONSTANTS)
                if not proven:
                    _argument_error(
                        argument, filename,
                        f"strategy(): setting '{name}' must be a compile-time constant",
                    )
                if isinstance(value, str) and not isinstance(argument, StringLiteral):
                    prefix = _ENUM_PARAMETERS.get(name)
                    if prefix is not None:
                        value = _NAMED_CONSTANTS.get(prefix + value, value)
                constants[name] = value
                values[name] = value.spelling if isinstance(value, NamedConstant) else value
            statement.annotations = {
                **(statement.annotations or {}),
                DECLARATION_VALUES: values,
                DECLARATION_CONSTANTS: constants,
            }
            parameters.update(values)
    return parameters
