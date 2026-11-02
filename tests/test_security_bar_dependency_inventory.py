"""Inventory coverage for chart-dependent request timeframe conditions."""

import json

import pytest

from pineforge_codegen import signatures as sigs, transpile_full
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.codegen.security import (
    _SECURITY_IMPURE_CALLS,
    _SECURITY_PURE_CALL_NAMESPACES,
)
from pineforge_codegen.codegen.tables import BAR_BUILTINS, SECURITY_BAR_FIELDS
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.symbols import PineType


def _parse(pine):
    return Parser(Lexer(pine).tokenize(), source=pine).parse()


def _variable_node(name):
    return _parse(f"probe_value = {name}\n").body[0].value


# Use the same positive stability proof as the member-access classifier.
# Constructing its empty-script context neither generates nor compiles C++.
_STABILITY_PROOF = CodeGen(Analyzer(_parse(
    '//@version=6\nstrategy("Inventory stability")\n'
)).analyze())

BAR_INVENTORIES = (
    {name: sigs.BUILTIN_VARIABLES.get(name, PineType.FLOAT)
     for name in BAR_BUILTINS},
    {name: sigs.BUILTIN_VARIABLES.get(name, PineType.FLOAT)
     for name in SECURITY_BAR_FIELDS},
)
SIGNATURE_VARIABLE_INVENTORIES = (
    sigs.STRATEGY_VARIABLES,
    sigs.BUILTIN_VARIABLES,
    sigs.BARSTATE_VARIABLES,
)
VARIABLE_INVENTORIES = BAR_INVENTORIES + SIGNATURE_VARIABLE_INVENTORIES

# No current inventory member needs an exception to scalar construction.
EXCLUDED = {}


def _scalar_condition(expression, pine_type):
    if pine_type == PineType.BOOL:
        return expression
    if pine_type in (PineType.INT, PineType.FLOAT):
        return f"{expression} > 0"
    if pine_type == PineType.STRING:
        return f'{expression} != ""'
    raise ValueError(f"No scalar condition for {pine_type}: {expression}")


def _variable_condition(name, pine_type):
    return _scalar_condition(name, pine_type)


def _call_is_per_bar(name):
    namespace, separator, member = name.rpartition(".")
    if not separator:
        return name not in CodeGen._SECURITY_TF_REPLAY_CALLS
    return not (
        (namespace in _SECURITY_PURE_CALL_NAMESPACES
         and (namespace, member) not in _SECURITY_IMPURE_CALLS)
        or (namespace == "timeframe"
            and member in CodeGen._SECURITY_TF_REPLAY_TIMEFRAME_CALLS)
    )


# Names come from the registered signatures, including qualified functions
# stored in BUILTIN_FUNCTIONS rather than in a namespace-specific table.
CALL_INVENTORIES = {
    table_name: table
    for table_name, table in vars(sigs).items()
    if table_name.endswith("_FUNCTIONS") and isinstance(table, dict)
}
CALL_NAMESPACES = {}
for _table in CALL_INVENTORIES.values():
    for _function in _table.values():
        _namespace, _separator, _member = _function.name.rpartition(".")
        if _separator and _call_is_per_bar(_function.name):
            CALL_NAMESPACES.setdefault(_namespace, {})[_function.name] = _function


def _argument(namespace, parameter):
    if namespace == "map" and parameter.name in ("id", "id2"):
        return "source_map"
    if parameter.default is not None:
        return json.dumps(parameter.default)
    if parameter.pine_type == PineType.BOOL:
        return "true"
    if parameter.pine_type == PineType.INT:
        return "2"
    if parameter.pine_type == PineType.FLOAT:
        return "1.0"
    if parameter.pine_type == PineType.STRING:
        if parameter.name == "symbol":
            return "syminfo.tickerid"
        if parameter.name in ("from", "to"):
            return '"USD"'
        return '"60"'
    raise ValueError(f"No argument for {namespace}.{parameter.name}")


def _namespace_row(namespace, functions):
    # One scalar-returning overload per namespace; do not mistake a tuple,
    # collection constructor or side-effect-only call for a scalar condition.
    candidates = [
        (function, signature)
        for function in functions.values()
        for signature in function.signatures
        if not signature.returns_tuple
        and signature.return_type in (
            PineType.BOOL, PineType.INT, PineType.FLOAT, PineType.STRING)
        and (namespace != "map"
             or signature.return_type in (PineType.BOOL, PineType.INT))
    ]
    assert candidates, f"No scalar signature in {namespace}"
    function, signature = min(
        candidates,
        key=lambda item: (
            sum(parameter.default is None for parameter in item[1].params),
            len(item[1].params), item[0].name,
        ),
    )
    arguments = ", ".join(_argument(namespace, parameter)
                          for parameter in signature.params)
    setup = ('source_map = map.new<string, float>()\n'
             if namespace == "map" else "")
    return (function.name, setup,
            _scalar_condition(f"{function.name}({arguments})",
                              signature.return_type))


STABLE_VARIABLES = {
    name for inventory in SIGNATURE_VARIABLE_INVENTORIES for name in inventory
    if _STABILITY_PROOF._expr_is_stable(_variable_node(name))
}

# Keep duplicate memberships: the count guard covers each inventory in full.
# The positive proof removes only names it actually establishes as stable.
REFUSAL_ROWS = [
    (name, "", _variable_condition(name, pine_type))
    for inventory in BAR_INVENTORIES
    for name, pine_type in sorted(inventory.items())
    if name not in EXCLUDED
]
REFUSAL_ROWS.extend(
    (name, "", _variable_condition(name, pine_type))
    for inventory in SIGNATURE_VARIABLE_INVENTORIES
    for name, pine_type in sorted(inventory.items())
    if name not in STABLE_VARIABLES and name not in EXCLUDED
)
REFUSAL_ROWS.extend(
    _namespace_row(namespace, functions)
    for namespace, functions in sorted(CALL_NAMESPACES.items())
)


def test_bar_dependency_inventory_coverage():
    assert VARIABLE_INVENTORIES and all(VARIABLE_INVENTORIES)
    assert CALL_INVENTORIES and all(CALL_INVENTORIES.values())
    assert REFUSAL_ROWS
    assert len(REFUSAL_ROWS) >= sum(
        len(inventory) for inventory in VARIABLE_INVENTORIES)
    row_names = {name for name, _setup, _condition in REFUSAL_ROWS}
    for inventory in BAR_INVENTORIES:
        assert set(inventory) - EXCLUDED.keys() <= row_names
    for inventory in SIGNATURE_VARIABLE_INVENTORIES:
        assert set(inventory) - STABLE_VARIABLES - EXCLUDED.keys() <= row_names
    call_rows = [name for name in row_names if name in {
        function.name for functions in CALL_NAMESPACES.values()
        for function in functions.values()
    }]
    assert {name.rsplit(".", 1)[0] for name in call_rows} == set(CALL_NAMESPACES)
    assert len(CALL_NAMESPACES) >= 7
    # Session has variable predicates, not registered call signatures.
    assert {f"session.{member}" for member in sigs.SESSION_FLAG_MEMBERS} <= row_names
    for namespace, member in _SECURITY_IMPURE_CALLS:
        assert f"{namespace}.{member}" in row_names


def test_excluded_inventory_members_cannot_form_scalar_conditions():
    members = {name: pine_type for inventory in VARIABLE_INVENTORIES
               for name, pine_type in inventory.items()}
    assert EXCLUDED.keys() <= members.keys()
    for name, reason in EXCLUDED.items():
        assert reason.strip()
        with pytest.raises(ValueError, match="No scalar condition"):
            _variable_condition(name, members[name])


def _assert_refusal(pine, line, column):
    with pytest.raises(CompileError) as caught:
        transpile_full(pine)
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E3110"
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.CODEGEN
    assert diagnostic.args == {}
    assert diagnostic.message == (
        "A requested timeframe cannot be chosen by a condition that reads "
        "a chart series value."
    )
    assert diagnostic.location.line == line
    assert diagnostic.location.col == column


def _script(setup, condition):
    return ('//@version=6\nstrategy("Inventory condition")\n'
            + setup + f'tf = if {condition}\n    "60"\nelse\n    "240"\n'
            'value = request.security(syminfo.tickerid, tf, close)\n'
            'if value > close\n    strategy.entry("Long", strategy.long)\n')


@pytest.mark.parametrize("name,setup,condition", REFUSAL_ROWS,
                         ids=[row[0] for row in REFUSAL_ROWS])
def test_inventory_condition_refuses_chart_dependency(name, setup, condition):
    # Each selected inventory read lacks a stability proof; scalar-if setup
    # diagnoses the condition's first token, after the eight-character prefix.
    _assert_refusal(_script(setup, condition), 3 + setup.count("\n"), 9)


@pytest.mark.parametrize("setup,condition", (
    # Each tuple element maps back to the TA call in global_expr_map.
    pytest.param("[a, b, _] = ta.macd(close, 12, 26, 9)\n", "a > 0",
                 id="tuple-global"),
    # Omitted arguments are expanded before the function body's dependencies.
    pytest.param("choose_side(float price = close) => price > 0\n",
                 "choose_side()", id="series-default"),
))
def test_indirect_condition_refuses_chart_dependency(setup, condition):
    _assert_refusal(_script(setup, condition), 3 + setup.count("\n"), 9)


def test_loop_bound_condition_refuses_chart_dependency():
    # The iterator's range reads bar_index, so its value depends on the bar.
    # Four spaces of block indentation put the first condition token at 13.
    pine = ('//@version=6\nstrategy("Iterator condition")\n'
            'for sample_index = 0 to bar_index\n'
            '    tf = if sample_index > 0\n'
            '        "60"\n    else\n        "240"\n'
            '    value = request.security(syminfo.tickerid, tf, close)\n'
            '    if value > close\n'
            '        strategy.entry("Long", strategy.long)\n')
    _assert_refusal(pine, 4, 13)
