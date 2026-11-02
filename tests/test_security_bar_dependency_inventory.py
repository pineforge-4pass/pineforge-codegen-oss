"""Inventory coverage for chart-dependent request timeframe conditions."""

import json
import re
from collections import Counter

import pytest

from pineforge_codegen import signatures as sigs, transpile_full
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import VarDecl
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.codegen.security import (
    _SECURITY_IMPURE_CALLS,
    _SECURITY_PURE_CALL_NAMESPACES,
)
from pineforge_codegen.codegen.tables import BAR_BUILTINS, SECURITY_BAR_FIELDS
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.symbols import PineType


def _parse(pine):
    return Parser(Lexer(pine).tokenize(), source=pine).parse()


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
    if pine_type == PineType.NA:
        return f"na({expression})"
    if pine_type == PineType.BOOL:
        return expression
    if pine_type in (PineType.INT, PineType.FLOAT):
        return f"{expression} > 0"
    if pine_type == PineType.STRING:
        return f'{expression} != ""'
    raise ValueError(f"No scalar condition for {pine_type}: {expression}")


def _variable_condition(name, pine_type):
    return _scalar_condition(name, pine_type)


def _script(setup, condition):
    return ('//@version=6\nstrategy("Inventory condition")\n'
            + setup + f'tf = if {condition}\n    "60"\nelse\n    "240"\n'
            'value = request.security(syminfo.tickerid, tf, close)\n'
            'if value > close\n    strategy.entry("Long", strategy.long)\n')


def _condition_is_per_run_fixed(condition):
    # Partition live output by registrations; coverage below independently
    # requires every inventoried chart read to use the two-arm lowering.
    result = transpile_full(_script("", condition))
    assert all(diagnostic.code != "PF-E3110"
               for diagnostic in result["diagnostics"])
    count = result["cpp"].count("register_security_eval(")
    assert count in (1, 2)
    return count == 1


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


PER_RUN_FIXED_VARIABLES = {
    name for inventory in VARIABLE_INVENTORIES
    for name, pine_type in inventory.items()
    if name not in EXCLUDED
    and _condition_is_per_run_fixed(_variable_condition(name, pine_type))
}

# Keep duplicate memberships and partition the same inventories by one proof.
VARIABLE_LOWERING_ROWS = [
    (name, "", _variable_condition(name, pine_type))
    for inventory in VARIABLE_INVENTORIES
    for name, pine_type in sorted(inventory.items())
    if name not in PER_RUN_FIXED_VARIABLES and name not in EXCLUDED
]
# The chart predicates are Pine bools, but their helpers currently have a
# numeric C++ return type. Pin that family independently of the emitter's
# bool-conversion helper; bool-returning helpers retain their direct call.
NUMERIC_FIXED_MEMBERS = {
    "chart.is_kagi", "chart.is_linebreak", "chart.is_pnf", "chart.is_range",
    "chart.is_renko", "chart.is_standard", "chart.is_heikinashi",
}
NUMERIC_FIXED_CONDITION_CPP = (
    "[&](){ auto _pf_bool_v = (inventory_condition()); "
    "using _pf_bool_t = std::decay_t<decltype(_pf_bool_v)>; "
    "if constexpr (std::is_same_v<_pf_bool_t, bool>) { return _pf_bool_v; } "
    "else if constexpr (std::is_floating_point_v<_pf_bool_t> || "
    "std::is_integral_v<_pf_bool_t>) { "
    "return is_na(_pf_bool_v) ? false : (_pf_bool_v != 0); } "
    "else { return static_cast<bool>(_pf_bool_v); } }()"
)
ACCEPTANCE_ROWS = [
    # Keep the actual member read live in a helper so registration has an exact
    # call spelling without a second, manually maintained constant-value table.
    (name, f"inventory_condition() => {_variable_condition(name, pine_type)}\n",
     "inventory_condition()",
     NUMERIC_FIXED_CONDITION_CPP if name in NUMERIC_FIXED_MEMBERS
     else "inventory_condition()")
    for inventory in VARIABLE_INVENTORIES
    for name, pine_type in sorted(inventory.items())
    if name in PER_RUN_FIXED_VARIABLES and name not in EXCLUDED
]
LOWERING_ROWS = list(VARIABLE_LOWERING_ROWS)
# Loop bindings conservatively carry chart dependency even with literal bounds;
# closed arms now lower instead of refusing that conservative classification.
LOWERING_ROWS.append((
    "literal-loop-helper",
    'choose_side() =>\n'
    '    chosen_side = false\n'
    '    for sample_index = 0 to 1\n'
    '        chosen_side := sample_index > 0\n'
    '    chosen_side\n',
    "choose_side()",
))
CALL_LOWERING_ROWS = [
    _namespace_row(namespace, functions)
    for namespace, functions in sorted(CALL_NAMESPACES.items())
]
LOWERING_ROWS.extend(CALL_LOWERING_ROWS)


def test_bar_dependency_inventory_coverage():
    assert VARIABLE_INVENTORIES and all(VARIABLE_INVENTORIES)
    assert CALL_INVENTORIES and all(CALL_INVENTORIES.values())
    assert LOWERING_ROWS and ACCEPTANCE_ROWS
    members = Counter(
        name for inventory in VARIABLE_INVENTORIES for name in inventory
        if name not in EXCLUDED)
    # A variable and a function can share a spelling (ta.tr); function rows
    # cover call namespaces below and must not double-count variable coverage.
    lowered = Counter(name for name, _setup, _condition in VARIABLE_LOWERING_ROWS)
    accepted = Counter(name for name, _setup, _condition, _cpp in ACCEPTANCE_ROWS
                       if name in members)
    assert lowered + accepted == members
    assert lowered.keys().isdisjoint(accepted)
    assert accepted.keys() == PER_RUN_FIXED_VARIABLES
    row_names = {name for name, _setup, _condition in LOWERING_ROWS}
    for inventory in BAR_INVENTORIES:
        assert set(inventory) - EXCLUDED.keys() <= row_names
    for inventory in SIGNATURE_VARIABLE_INVENTORIES:
        assert set(inventory) - PER_RUN_FIXED_VARIABLES - EXCLUDED.keys() <= row_names
    call_rows = [name for name, _setup, _condition in CALL_LOWERING_ROWS]
    assert {name.rsplit(".", 1)[0] for name in call_rows} == set(CALL_NAMESPACES)
    assert len(CALL_NAMESPACES) >= 7
    # Session has variable predicates, not registered call signatures.
    assert {f"session.{member}" for member in sigs.SESSION_FLAG_MEMBERS} <= row_names
    for namespace, member in _SECURITY_IMPURE_CALLS:
        assert f"{namespace}.{member}" in row_names


def test_per_bar_inventory_members_cannot_enter_acceptance():
    accepted = {name for name, _setup, _condition, _cpp in ACCEPTANCE_ROWS}
    lowered = {name for name, _setup, _condition in LOWERING_ROWS}
    # Bare bar reads are unconditionally per-bar in the shared predicate.
    per_bar = set(BAR_BUILTINS) | set(SECURITY_BAR_FIELDS)
    # Direct strategy members and nested state receivers retain their reads;
    # namespace constants have no state receiver.
    per_bar.update(name for name in sigs.STRATEGY_VARIABLES
                   if name.rpartition(".")[0] == "strategy"
                   or name.rpartition(".")[0] in sigs.STRATEGY_VARIABLES)
    # Other inventoried namespace reads include TA series and session flags;
    # chart facts are the classifier's explicit exception.
    per_bar.update(name for name in sigs.BUILTIN_VARIABLES
                   if "." in name and name.partition(".")[0] != "chart")
    per_bar.update(sigs.BARSTATE_VARIABLES)
    per_bar.update(f"session.{member}" for member in sigs.SESSION_FLAG_MEMBERS)
    assert accepted.isdisjoint(per_bar)
    assert EXCLUDED.keys().isdisjoint(per_bar)
    assert per_bar <= lowered
    # Non-pure namespace representatives must also select per bar.
    for namespace, functions in CALL_NAMESPACES.items():
        name, _setup, _condition = _namespace_row(namespace, functions)
        assert name not in accepted
        assert name in lowered


def test_excluded_inventory_members_cannot_form_scalar_conditions():
    members = {name: pine_type for inventory in VARIABLE_INVENTORIES
               for name, pine_type in inventory.items()}
    assert EXCLUDED.keys() <= members.keys()
    for name, reason in EXCLUDED.items():
        assert reason.strip()
        with pytest.raises(ValueError, match="No scalar condition"):
            _variable_condition(name, members[name])


def _assert_lowering(pine, timeframes=("60", "240"), lower_tf=False):
    cpp = transpile_full(pine)["cpp"]
    # Closed literal arms register in source order; selection reads the
    # authored timeframe binding once, then compares both arms before stopping.
    register = ("register_security_lower_tf_eval" if lower_tf
                else "register_security_eval")
    registrations = [
        part.split(");", 1)[0]
        for part in cpp.split(register + "(")[1:]
    ]
    flags = "" if lower_tf else ", false, false"
    assert registrations == [
        f'{index}, "{timeframe}", input_tf_{flags}'
        for index, timeframe in enumerate(timeframes)
    ]
    assert all("current_bar_" not in part for part in registrations)
    setup = cpp.split("void configure_security_evaluators()", 1)[1].split(
        "\n    }\n", 1)[0]
    assert "sample_index" not in setup
    assert "sample_value" not in setup
    body = cpp.split("void on_source_bar(", 1)[1]
    assert re.search(r"const std::string _pf_selected_tf = tf(?:\[0\])?;", body)
    result = "_req_sec_lower_tf_" if lower_tf else "_req_sec_"
    branches = "".join(
        f'    if (_pf_selected_tf == "{timeframe}") return {result}{index};\n'
        for index, timeframe in enumerate(timeframes)
    )
    assert (branches +
        '    _PF_ENGINE_INVARIANT("Requested timeframe matched no registered timeframe", std::logic_error);\n'
        '}())'
    ) in body


@pytest.mark.parametrize("name,setup,condition", LOWERING_ROWS,
                         ids=[row[0] for row in LOWERING_ROWS])
def test_inventory_condition_selects_chart_dependency(name, setup, condition):
    # Every inventory condition retains its authored read in the chart body;
    # its two closed strings are the only values registration consumes.
    _assert_lowering(_script(setup, condition))


@pytest.mark.parametrize("name,setup,condition,expected_condition", ACCEPTANCE_ROWS,
                         ids=[row[0] for row in ACCEPTANCE_ROWS])
def test_fixed_inventory_condition_registration(
        name, setup, condition, expected_condition, monkeypatch):
    pine = _script(setup, condition)
    result = transpile_full(pine)
    assert all(diagnostic.code != "PF-E3110"
               for diagnostic in result["diagnostics"])
    # The live helper reads the inventory member; ordinary call emission keeps
    # its name, while scalar-if setup assigns both string arms in slot zero.
    registration = result["cpp"].split("register_security_eval(", 1)[1].split(
        ", input_tf_", 1)[0]
    assert registration == (
        '0, ([&]() -> std::string {\n'
        '    std::string __selection_value_0 = na<std::string>();\n'
        f'    if ({expected_condition}) {{\n'
        '        __selection_value_0 = std::string("60");\n'
        '    } else {\n'
        '        __selection_value_0 = std::string("240");\n'
        '    }\n'
        '    return __selection_value_0;\n'
        '}())'
    )
    # Preservation pin: fixed conditions keep all output identical when the
    # expansion hook is absent, including the original single registration.
    assert result["cpp"].count("register_security_eval(") == 1
    with monkeypatch.context() as previous:
        previous.setattr(CodeGen, "_expand_security_series_timeframes",
                         lambda self: None)
        assert result["cpp"] == transpile_full(pine)["cpp"]


@pytest.mark.parametrize("setup,condition", (
    # Each tuple element maps back to the TA call in global_expr_map.
    pytest.param("[a, b, _] = ta.macd(close, 12, 26, 9)\n", "a > 0",
                 id="tuple-global"),
    # Omitted arguments are expanded before the function body's dependencies.
    pytest.param("choose_side(float price = close) => price > 0\n",
                 "choose_side()", id="series-default"),
))
def test_indirect_condition_selects_chart_dependency(setup, condition):
    # Tuple provenance and omitted series defaults reach the shared predicate;
    # both select the same two closed literal arms at chart execution.
    _assert_lowering(_script(setup, condition))


def test_loop_bound_condition_selects_the_authored_timeframe():
    # The iterator's range reads bar_index, so its value depends on the bar.
    pine = ('//@version=6\nstrategy("Iterator condition")\n'
            'for sample_index = 0 to bar_index\n'
            '    tf = if sample_index > 0\n'
            '        "60"\n    else\n        "240"\n'
            '    value = request.security(syminfo.tickerid, tf, close)\n'
            '    if value > close\n'
            '        strategy.entry("Long", strategy.long)\n')
    _assert_lowering(pine)


def test_lower_timeframe_condition_selects_the_authored_array():
    pine = ('//@version=6\nstrategy("Lower timeframe condition")\n'
            'tf = if close > open\n    "1"\nelse\n    "5"\n'
            'values = request.security_lower_tf(syminfo.tickerid, tf, close)\n'
            'if array.size(values) > 0\n    strategy.entry("Long", strategy.long)\n')
    _assert_lowering(pine, timeframes=("1", "5"), lower_tf=True)


@pytest.mark.parametrize("setup,loop,condition", (
    # Literal bounds do not make an iterator available during registration.
    pytest.param("", "for sample_index = 0 to 1", "sample_index > 0",
                 id="literal-counted-loop"),
    # The element is bound by the loop, not by a global initializer.
    pytest.param("samples = array.from(close)\n", "for sample_value in samples",
                 "sample_value > 0", id="array-element"),
    # Both destructured positions are loop bindings, including the index.
    pytest.param("samples = array.from(close)\n",
                 "for [sample_index, sample_value] in samples",
                 "sample_index > 0", id="array-index"),
    pytest.param("samples = array.from(close)\n",
                 "for [sample_index, sample_value] in samples",
                 "sample_value > 0", id="array-indexed-element"),
))
def test_loop_binding_timeframe_condition_selects_closed_arms(setup, loop, condition):
    pine = ('//@version=6\nstrategy("Loop binding condition")\n'
            + setup + loop + '\n'
            f'    tf = if {condition}\n'
            '        "60"\n    else\n        "240"\n'
            '    value = request.security(syminfo.tickerid, tf, close)\n'
            '    if value > close\n'
            '        strategy.entry("Long", strategy.long)\n')
    # Loop bindings only choose at the request's execution, never in setup.
    _assert_lowering(pine)


@pytest.mark.parametrize("setup,loop,condition", (
    # A same-named fixed global must not hide the counted loop's binding.
    pytest.param("sample_index = input.int(1)\n",
                 "for sample_index = 0 to bar_index", "sample_index > 0",
                 id="counted-shadows-input"),
    # A same-named fixed global must not hide a for-in element either.
    pytest.param("sample_value = 1.0\nsamples = array.from(close)\n",
                 "for sample_value in samples", "sample_value > 0",
                 id="element-shadows-constant"),
))
def test_classifier_follows_exact_loop_binding(setup, loop, condition):
    pine = ('//@version=6\nstrategy("Binding identity")\n'
            + setup + loop + '\n'
            f'    condition_value = {condition}\n')
    ast = _parse(pine)
    generator = CodeGen(Analyzer(ast).analyze())
    value = ast.body[-1].body[0].value
    # Exact declaration provenance wins over the raw-name global expression.
    assert generator._security_tf_expr_is_per_bar(value)


def test_same_named_globals_and_ordinary_loop_locals_stay_fixed():
    pine = ('//@version=6\nstrategy("Fixed bindings")\n'
            'sample_index = input.int(1)\n'
            'for sample_index = 0 to bar_index\n'
            '    fixed_value = 2\n'
            '    condition_value = fixed_value > 0\n'
            'condition_value = sample_index > 0\n')
    ast = _parse(pine)
    generator = CodeGen(Analyzer(ast).analyze())
    loop = ast.body[-2]
    # Scope labels alone would mistake the ordinary local for an iterator.
    assert not generator._security_tf_expr_is_per_bar(loop.body[-1].value)
    # After the loop, the same spelling resolves to the fixed input again.
    assert not generator._security_tf_expr_is_per_bar(ast.body[-1].value)


def test_nested_local_shadow_of_iterator_stays_fixed():
    pine = ('//@version=6\nstrategy("Nested binding")\n'
            'for sample_index = 0 to bar_index\n'
            '    if true\n'
            '        sample_index = 2\n'
            '        condition_value = sample_index > 0\n')
    ast = _parse(pine)
    generator = CodeGen(Analyzer(ast).analyze())
    declaration = ast.body[-1].body[0].body[-1]
    assert isinstance(declaration, VarDecl)
    # The inner declaration, rather than the outer loop, binds this read.
    assert not generator._security_tf_expr_is_per_bar(declaration.value)
