"""Pure block values used by request registration and requested evaluators."""

from pathlib import Path
import re

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import Identifier, SwitchStmt, VarDecl
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp, run_emitted_tu


FIXTURES = Path(__file__).parent / "fixtures" / "selection_values"
PROBES = (
    "requested-alias-if-block",
    "requested-alias-switch-block",
    "requested-timeframe-if-block",
    "requested-timeframe-switch-block",
    "requested-timeframe-switch-selector-block",
    "requested-alias-switch-selector-block",
    "requested-timeframe-if-nested-block",
)


def source(name):
    return (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")


def registration(cpp):
    return cpp.split("register_security_eval(", 1)[1].split(
        ", input_tf_", 1)[0]


@pytest.mark.parametrize("name", PROBES)
def test_request_block_translates(name):
    # Arm-local reads substitute to high/low or timeframe.period/"1D".
    assert isinstance(transpile_full(source(name))["cpp"], str)


@pytest.mark.parametrize("name", PROBES)
def test_request_block_has_no_empty_expression(name):
    # Every selected value has a leaf after substitution, including setup.
    assert not re.search(r"(?<![=!<>])=\s*;|\(\s*,|,\s*,|,\s*\)",
                         transpile(source(name)))


@pytest.mark.parametrize("name", PROBES)
def test_request_block_has_no_unknown_placeholder(name):
    # A supported arm leaves an expression, never the former sentinel.
    assert "/* unknown */" not in transpile(source(name))


@pytest.mark.parametrize("name", PROBES)
@pytest.mark.parametrize("local", ("base_tf", "alt_tf", "hi", "lo",
                                  "base_min", "span_v", "mid_v"))
def test_request_arm_local_is_not_a_member(name, local):
    # Isolate the generated class's member section, excluding setup lambdas.
    cpp = transpile(source(name))
    class_header = (
        "class GeneratedStrategy : public pineforge::source::PineStrategyHost {\n"
        "public:\n"
    )
    constructor = "    explicit GeneratedStrategy("
    assert class_header in cpp and constructor in cpp
    declarations = cpp.split(class_header, 1)[1].split(constructor, 1)[0]
    assert not re.search(rf"^    (?:std::string|double|int|Series<[^>]+>) +{local}\b",
                         declarations, re.MULTILINE)


@pytest.mark.parametrize("kind", ("if", "switch"))
@pytest.mark.parametrize("selected", ("true", "false"))
def test_timeframe_block_registers_like_expression_twin(kind, selected):
    # Removing the let bindings leaves exactly the taped single-arm selection.
    block = source(f"requested-timeframe-{kind}-block").replace(
        "input.bool(true,", f"input.bool({selected},")
    twin = source(f"security-timeframe-{kind}").replace(
        "input.bool(true,", f"input.bool({selected},")
    assert registration(transpile(block)) == registration(transpile(twin))


def registered_string(cpp):
    expression = registration(cpp).split(", ", 1)[1]
    driver = '''
#include <iostream>
class FrameProbe : public GeneratedStrategy {
public:
    std::string selected() {
        script_tf_ = "15";
        return ''' + expression + ''';
    }
};
int main() {
    FrameProbe strategy;
    std::cout << strategy.selected() << "\\n";
}
'''
    return run_emitted_tu(cpp, driver, opt="-O2", label="registered timeframe").strip()


@pytest.mark.parametrize("kind", ("if", "switch"))
@pytest.mark.parametrize("selected,expected", (("true", "15"), ("false", "1D")))
def test_registered_timeframe_string_matches_both_expression_arms(kind, selected, expected):
    block = source(f"requested-timeframe-{kind}-block").replace(
        "input.bool(true,", f"input.bool({selected},")
    twin = source(f"security-timeframe-{kind}").replace(
        "input.bool(true,", f"input.bool({selected},")
    # true selects timeframe.period ("15"); false selects "1D", the switch default.
    actual = registered_string(transpile(block))
    control = registered_string(transpile(twin))
    assert actual == control
    assert actual == expected


@pytest.mark.parametrize("selected,expected", ((1, "60"), (2, "15")))
def test_selector_switch_registered_string_matches_expression_twin(selected, expected):
    block = source("requested-timeframe-switch-selector-block").replace(
        "input.int(1,", f"input.int({selected},")
    twin = block.replace("        base_min = mult_n * 15\n"
                         "        str.tostring(base_min)",
                         "        str.tostring(mult_n * 15)")
    # The selected arm computes 4*15; selector 2 reaches the chart-timeframe default.
    actual = registered_string(transpile(block))
    control = registered_string(transpile(twin))
    assert actual == control
    assert actual == expected


@pytest.mark.parametrize("selected,expected", ((1, "60"), (2, "240"), (3, "15")))
def test_else_if_registered_string_matches_expression_twin(selected, expected):
    block = source("requested-timeframe-if-nested-block").replace(
        "input.int(1,", f"input.int({selected},")
    twin = block.replace('    first_tf = "60"\n    first_tf', '    "60"').replace(
        '    second_tf = "240"\n    second_tf', '    "240"')
    # The two conditions choose "60"/"240"; selector 3 reaches timeframe.period.
    actual = registered_string(transpile(block))
    control = registered_string(transpile(twin))
    assert actual == control
    assert actual == expected


@pytest.mark.parametrize("name", PROBES)
def test_request_block_compiles(name):
    # Both registration and evaluator consume complete scalar expressions.
    compile_cpp(transpile(source(name)))


@pytest.mark.parametrize("name", PROBES)
def test_request_block_construction_only_exposes_compile_errors(name):
    # Construction resolves timeframes before ordinary statement state exists.
    pine = source(name)
    ast = Parser(Lexer(pine).tokenize(), source=pine).parse()
    context = Analyzer(ast).analyze()
    try:
        generator = CodeGen(context)
    except CompileError:
        return
    assert isinstance(generator, CodeGen)


def test_registration_never_emits_statements(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("registration attempted ordinary statement emission")

    # Block arms normalize before construction can reach statement-only state.
    monkeypatch.setattr(CodeGen, "_visit_if_switch_expr", unexpected)
    for name in ("requested-timeframe-if-block", "requested-timeframe-switch-block"):
        pine = source(name)
        ast = Parser(Lexer(pine).tokenize(), source=pine).parse()
        CodeGen(Analyzer(ast).analyze())


def test_expression_arm_timeframe_if_registers_a_typed_lambda(monkeypatch):
    original = CodeGen._visit_if_switch_expr
    visits = []

    def record(self, *args, **kwargs):
        visits.append(args[0])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CodeGen, "_visit_if_switch_expr", record)
    pine = source("security-timeframe-if")
    ast = Parser(Lexer(pine).tokenize(), source=pine).parse()
    # Expression-arm ifs now register a typed lambda; main emitted an empty expression.
    CodeGen(Analyzer(ast).analyze())
    assert visits


def test_expression_arm_timeframe_if_registration_has_typed_assignments():
    # Slot 0 and typed assignments repair main's empty registration expression.
    expected = (
        '0, ([&]() -> std::string {\n'
        '    std::string __selection_value_0 = na<std::string>();\n'
        '    if (get_input_bool("Choose chart", true)) {\n'
        '        __selection_value_0 = script_tf_;\n'
        '    } else {\n'
        '        __selection_value_0 = std::string("1D");\n'
        '    }\n'
        '    return __selection_value_0;\n'
        '}())'
    )
    assert registration(transpile(source("security-timeframe-if"))) == expected


def test_scalar_timeframe_switch_registration_has_the_original_spelling():
    # Preservation pin: the default-first renderer builds the compact conditional.
    expected = (
        '0, ((((get_input_bool("Choose chart", true)) == (true))) ? '
        '(script_tf_) : (std::string("1D")))'
    )
    assert registration(transpile(source("security-timeframe-switch"))) == expected


def test_scalar_timeframe_switch_is_dispatched_before_substitution(monkeypatch):
    original = CodeGen._security_tf_switch_runtime_expr
    seen = []

    def record(self, node, resolving):
        # The raw selector is the input alias; substitution would replace it by a call.
        assert isinstance(node.expr, Identifier)
        assert node.expr.name == "pick"
        seen.append(node)
        return original(self, node, resolving)

    pine = source("security-timeframe-switch")
    ast = Parser(Lexer(pine).tokenize(), source=pine).parse()
    switch = next(stmt.value for stmt in ast.body
                  if isinstance(stmt, VarDecl) and isinstance(stmt.value, SwitchStmt))
    monkeypatch.setattr(CodeGen, "_security_tf_switch_runtime_expr", record)
    CodeGen(Analyzer(ast).analyze())
    assert seen
    assert all(node is switch for node in seen)


@pytest.mark.parametrize("position", ("timeframe-if", "timeframe-switch",
                                     "value-if", "value-switch"))
@pytest.mark.parametrize("initializer", ("close * 2", "ta.sma(close, 3)"))
def test_block_local_history_is_a_located_gap(position, initializer):
    setup = position.startswith("timeframe")
    result = "str.tostring(prior_v[1])" if setup else "prior_v[1]"
    fallback = '"240"' if setup else "240"
    if position.endswith("switch"):
        selection = (f"switch pick\n    true =>\n"
                     f"        prior_v = {initializer}\n        {result}\n"
                     f"    => {fallback}\n")
    else:
        selection = (f"if pick\n    prior_v = {initializer}\n    {result}\n"
                     f"else\n    {fallback}\n")
    request = ("request.security(syminfo.tickerid, chosen, close)" if setup else
               "request.security(syminfo.tickerid, timeframe.period, chosen)")
    pine = ('//@version=6\nstrategy("Local history")\npick = input.bool(true)\n'
            "chosen = " + selection + "value = " + request + "\nplot(value)\n")
    # prior_v[1] needs the local's previous-bar slot, never a substituted current value.
    with pytest.raises(CompileError) as caught:
        transpile_full(pine)
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == "PF-E3085"
    assert "history of a block-local" in diagnostic.message
    history_line = next(i for i, line in enumerate(pine.splitlines(), 1)
                        if "prior_v[1]" in line)
    assert diagnostic.location.line == history_line
    assert diagnostic.location.col == pine.splitlines()[history_line - 1].index("[") + 1


@pytest.mark.parametrize("position", ("timeframe", "value"))
@pytest.mark.parametrize("selected,expected", (("a", "60"), ("b", "240")))
def test_let_bound_string_selector_has_string_comparisons(position, selected, expected):
    setup = position == "timeframe"
    first, second = ('"60"', '"240"') if setup else ("60", "240")
    # The outer let is substituted into the nested switch's selector.
    selection = (
        f'if pick\n    choice_v = "{selected}"\n    switch choice_v\n'
        f'        "a" => {first}\n        => {second}\nelse\n    {second}\n'
        if setup else
        f'switch pick\n    true =>\n        choice_v = "{selected}"\n'
        f'        switch choice_v\n            "a" => {first}\n'
        f'            => {second}\n    => {second}\n'
    )
    request = ("request.security(syminfo.tickerid, chosen, close)" if setup else
               "request.security(syminfo.tickerid, timeframe.period, chosen)")
    pine = ('//@version=6\nstrategy("String selection")\npick = input.bool(true)\n'
            "chosen = " + selection + "value = " + request + "\nplot(value)\n")
    cpp = transpile(pine)
    # String inference forces a std::string selector even when its value is a literal.
    if setup:
        assert re.search(r'std::string __switch_val_\d+ = ', registration(cpp))
        assert registered_string(cpp) == expected
    else:
        evaluator = cpp.split("void _eval_security_0(", 1)[1].split(
            "void evaluate_security(", 1)[0]
        assert f'std::string(std::string("{selected}"))' in evaluator
        assert 'std::string("a")' in evaluator
        assert re.search(r"\?\s*\(60\)\s*:\s*\(240\)", evaluator)
    compile_cpp(cpp)


UNSAFE_ARMS = (
    # Calls are located at '('; history reads at '['; statements at their first word.
    ('    pick := false\n    "60"\n', 5, 5, "outer assignment"),
    ('    candidate = "1D"\n    for idx = 0 to 1\n        candidate := "60"\n'
     '    candidate\n', 6, 5, "ForStmt"),
    ('    candidate = str.tostring(math.random())\n    candidate\n',
     5, 41, "math.random"),
    ('    candidate = str.tostring(close[1])\n    candidate\n', 5, 35, "history"),
    ('    candidate = str.tostring(bar_index)\n    candidate\n', 5, 30, "bar_index"),
    ('    candidate = missing_value\n    candidate\n', 5, 17, "missing_value"),
)


@pytest.mark.parametrize("arm,line,col,construct", UNSAFE_ARMS)
def test_registration_unsafe_arm_is_a_located_refusal(arm, line, col, construct):
    pine = ('//@version=6\nstrategy("Setup selection")\npick = input.bool(true)\n'
            'tf = if pick\n' + arm + 'else\n    "1D"\n'
            'value = request.security(syminfo.tickerid, tf, close)\nplot(value)\n')
    # The first unsafe node, rather than a constructor traceback, owns the error.
    with pytest.raises(CompileError) as caught:
        transpile_full(pine)
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code != "PF-E0000"
    assert diagnostic.code.startswith("PF-E")
    assert diagnostic.location.line == line
    assert diagnostic.location.col == col
    assert construct in diagnostic.message


ARM_KINDS = {
    "declaration": 'candidate = 60\n{result}',
    "nested": 'candidate = 60\nif pick\n    {result}\nelse\n    {fallback}',
    "tuple": '[first_v, second_v] = pair()\n{tuple_result}',
    "reassignment": 'candidate = 30\ncandidate := candidate * 2\n{result}',
    "impure": 'candidate = math.random(1, 60)\n{result}',
    "history": 'candidate = close[1]\n{result}',
    "loop": 'candidate = 60\nfor idx = 0 to 1\n    candidate += idx\n{result}',
    "na": 'candidate = 60\nna',
    "mixed": 'candidate = 60\n{result}',
}


def variant(position, arm_kind):
    setup = position.startswith("timeframe")
    fallback = '"240"' if setup else "240"
    arm = ARM_KINDS[arm_kind].format(
        result="str.tostring(candidate)" if setup else "candidate",
        tuple_result="str.tostring(first_v + second_v)" if setup else "first_v + second_v",
        fallback=fallback,
    )
    is_switch = position.endswith("switch")
    indent = "        " if is_switch else "    "
    body = "\n".join(indent + line for line in arm.splitlines()) + "\n"
    fallback_body = (
        f"{indent}{fallback}\n" if arm_kind == "mixed" else
        f"{indent}fallback_v = {fallback}\n{indent}fallback_v\n"
    )
    selection = ("switch pick\n    true =>\n" + body + "    =>\n" + fallback_body
                 if is_switch else "if pick\n" + body + "else\n" + fallback_body)
    request = ("request.security(syminfo.tickerid, chosen, close)"
               if setup else
               "request.security(syminfo.tickerid, timeframe.period, chosen)")
    return ('//@version=6\nstrategy("Selection combinations")\n'
            'pair() => [30, 30]\npick = input.bool(true)\nchosen = '
            + selection + "value = " + request + "\nplot(value)\n")


@pytest.mark.parametrize("position", ("timeframe-if", "timeframe-switch",
                                     "value-if", "value-switch"))
@pytest.mark.parametrize("arm_kind", ARM_KINDS)
def test_selection_class_has_only_translation_or_one_located_error(position, arm_kind):
    """Real CodeGen: translate without empty values/placeholders or one located PF-E;
    a Python traceback is never an allowed result.
    """
    # Every arm has a final value; only its leaves/statements can prevent lowering.
    try:
        cpp = transpile_full(variant(position, arm_kind))["cpp"]
    except CompileError as error:
        assert len(error.diagnostics) == 1
        diagnostic = error.diagnostics[0]
        assert diagnostic.code.startswith("PF-E")
        assert diagnostic.code != "PF-E0000"
        assert diagnostic.location.line > 0
        assert diagnostic.location.col > 0
    else:
        assert "/* unknown */" not in cpp
        assert not re.search(r"=\s*;|,\s*,|,\s*\)", cpp)


@pytest.mark.parametrize("position", ("timeframe-if", "timeframe-switch",
                                     "value-if", "value-switch"))
@pytest.mark.parametrize("arm_kind", ("declaration", "nested", "tuple",
                                    "reassignment", "mixed"))
def test_pure_bindings_translate_in_both_request_positions(position, arm_kind):
    # Pure substitution leaves 60 (formatted at setup); value-if is a preservation pin.
    assert isinstance(transpile_full(variant(position, arm_kind))["cpp"], str)


def test_setup_substitution_preserves_earlier_binding_when_shadowed():
    pine = ('//@version=6\nstrategy("Local scope")\n'
            'candidate = input.int(60)\npick = input.bool(true)\n'
            'tf = if pick\n    saved_v = candidate\n    candidate = 240\n'
            '    str.tostring(saved_v)\nelse\n    "240"\n'
            'value = request.security(syminfo.tickerid, tf, close)\nplot(value)\n')
    # saved_v captures the input before candidate becomes the arm-local constant.
    assert "get_input_int(" in registration(transpile(pine))


@pytest.mark.parametrize("setup,condition,line", (
    ("", "close > open", 3),
    ("", "volume > 0", 3),
    ("", "bar_index > 3", 3),
    ("", "ta.sma(close, 3) > close", 3),
    ("", "close[1] > open", 3),
    ("", "time > 0", 3),
    ("", "barstate.isfirst", 3),
    ("prior_value = close - open\n", "prior_value > 0", 4),
))
def test_expression_only_setup_refuses_series_condition(setup, condition, line):
    """This refusal is labelled as a temporary located refusal of a shape
    that did not compile before this change; it is listed as a compile gap,
    and a TradingView value comparison will lift it.
    """
    pine = ('//@version=6\nstrategy("Expression condition")\n'
            + setup + f'tf = if {condition}\n    "60"\nelse\n    "240"\n'
            'value = request.security(syminfo.tickerid, tf, close)\nplot(value)\n')
    # Setup replay cannot prove these reads; expr_start places each at column 9.
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
    assert diagnostic.location.col == 9


@pytest.mark.parametrize("name", (
    "security-timeframe-if",
    "requested-timeframe-if-block",
    "requested-timeframe-if-nested-block",
))
def test_existing_setup_condition_sources_remain_accepted(name):
    # Preservation pin: input getters select the same already-pinned setup arms.
    cpp = transpile(source(name))
    assert 'get_input_' in registration(cpp)


def test_expression_only_constant_setup_condition_remains_accepted():
    pine = ('//@version=6\nstrategy("Constant condition")\n'
            'tf = if true\n    "60"\nelse\n    "240"\n'
            'value = request.security(syminfo.tickerid, tf, close)\nplot(value)\n')
    # Preservation pin: a bool literal needs no chart bar in the setup proof.
    assert "if (true)" in registration(transpile(pine))


def test_expression_only_series_ternary_keeps_its_registration():
    pine = ('//@version=6\nstrategy("Expression condition")\n'
            'tf = close > open ? "60" : "240"\n'
            'value = request.security(syminfo.tickerid, tf, close)\nplot(value)\n')
    # Preservation pin: scalar ternaries retain the ordinary substitution path.
    assert "current_bar_.close" in registration(transpile(pine))
