"""TradingView CE10123 uses the shared qualifier check before history lowering."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.diagnostic_codes import render_diagnostic
from pineforge_codegen.errors import CompileError, Level, Phase
from pineforge_codegen.signatures import SESSION_FLAG_MEMBERS
from tests._e2e import reference_codegen, transpile_json


CPP_REFERENCE = "2a4a86d7dcbc1309a288b3a321925a474b5ef63c"
HEADER = '//@version=6\nstrategy("Direction qualifier")\n'
LONG = "strategy.direction.long"
ALL = "strategy.direction.all"
CALL = "strategy.risk.allow_entry_in"
CODE = "PF-E2080"


def _refused(source, argument, line):
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="direction.pine")
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == CODE
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.location.file == "direction.pine"
    assert diagnostic.location.line == line
    assert diagnostic.location.col == source.splitlines()[line - 1].index(argument) + 1
    assert diagnostic.message == (
        f"Cannot call '{CALL}' with a series argument for simple parameter 'value'."
    )
    assert diagnostic.args == {
        "function_name": CALL,
        "name": "value",
        "argument_qualifier": "series",
        "parameter_qualifier": "simple",
    }
    assert render_diagnostic(CODE, diagnostic.args) == (
        diagnostic.message, diagnostic.hint,
    )


def test_top_level_series_direction_is_refused():
    argument = f"close > open ? {LONG} : {ALL}"
    _refused(HEADER + f"{CALL}({argument})\n", argument, 3)


SESSION_READ = "session.ismarket[1]"
METHOD_HISTORY_REFERENCE = (
    HEADER
    + "type Foo\n    float v = 1\n"
    + "method m(Foo self) =>\n"
    + f"    {CALL}({SESSION_READ} ? {LONG} : {ALL})\n"
    + "    self.v > 0\n"
    + "foo = Foo.new()\n"
    + "x = foo.m()\n"
    + 'if x\n    strategy.entry("L", strategy.long)\n'
)


def test_method_session_history_direction_is_refused_at_the_global_call():
    declarations = (
        "type Foo\n    bool enabled = true\n"
        f"method m(Foo self) => self.enabled and {SESSION_READ}\n"
        "foo = Foo.new()\n"
    )
    direction = f"foo.m() ? {LONG} : {ALL}"
    source = HEADER + declarations + f"{CALL}({direction})\n"
    _refused(source, direction, 3 + declarations.count("\n"))


def test_unresolved_method_receiver_reports_scope_before_qualifier():
    source = METHOD_HISTORY_REFERENCE.replace(
        "x = foo.m()\n", "read(receiver) => receiver.m()\nx = read(foo)\n",
    )
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="direction.pine")
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E2097"
    assert (diagnostic.location.line, diagnostic.location.col) == (6, 5)


@pytest.mark.parametrize("body,line", [
    (f"chosen = close > open ? {LONG} : {ALL}\n{CALL}(chosen)\n", 4),
    (f"signal = close > open\nchosen = signal ? {LONG} : {ALL}\n{CALL}(chosen)\n", 5),
    (f"chosen = {LONG}\nchosen := close > open ? {LONG} : {ALL}\n{CALL}(chosen)\n", 5),
    (f"chosen = {LONG}\nif close > open\n    chosen := {ALL}\n{CALL}(chosen)\n", 6),
    (f"run() =>\n    chosen = close > open ? {LONG} : {ALL}\n"
     f"    chosen\nchosen = run()\n{CALL}(chosen)\n", 7),
], ids=["declaration", "alias-chain", "reassignment", "controlled-write", "local"])
def test_series_direction_through_bound_variable_is_refused(body, line):
    _refused(HEADER + body, "chosen", line)


def test_history_direction_is_refused_at_argument_start():
    argument = "chosen[1]"
    source = HEADER + f"chosen = {LONG}\n{CALL}({argument})\n"
    _refused(source, argument, 4)


@pytest.mark.parametrize("series", [
    "open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4",
    "time", "time_close", "bar_index", "last_bar_index",
])
def test_bar_series_direction_is_refused(series):
    argument = f"{series} > 0 ? {LONG} : {ALL}"
    _refused(HEADER + f"{CALL}({argument})\n", argument, 3)


@pytest.mark.parametrize("flag", sorted(SESSION_FLAG_MEMBERS))
def test_every_session_flag_direction_is_refused(flag):
    argument = f"session.{flag} ? {LONG} : {ALL}"
    _refused(HEADER + f"{CALL}({argument})\n", argument, 3)


@pytest.mark.parametrize("declarations,condition", [
    ("", "math.abs(close) > 0"),
    ("", "ta.sma(close, 2) > 0"),
    ("", 'request.security(syminfo.tickerid, "D", close) > 0'),
    ("isPositive(x) => x > 0\n", "isPositive(close)"),
    ("isPositive(x) => x > 0\n", "isPositive(x=close)"),
    ("method positive(float self) => self > 0\n", "close.positive()"),
    ("", "not (close > open) and true"),
    ("", "barstate.islast"),
    ("source = input.source(close)\n", "source > 0"),
    ("series bool gate = true\n", "gate"),
], ids=[
    "math", "ta", "request", "function", "named-function", "method", "logical",
    "barstate", "input-source", "explicit-series",
])
def test_series_call_and_operator_results_are_refused(declarations, condition):
    argument = f"{condition} ? {LONG} : {ALL}"
    source = HEADER + declarations + f"{CALL}({argument})\n"
    _refused(source, argument, 3 + declarations.count("\n"))


@pytest.mark.parametrize("declaration,invocation", [
    ("relay(bool enabled)", "relay(close > open)"),
    ("relay(bool enabled)", "relay(enabled=close > open)"),
], ids=["function", "named-function"])
def test_forwarded_series_parameter_reaches_builtin_contract(declaration, invocation):
    argument = f"{invocation} ? {LONG} : {ALL}"
    _refused(HEADER + f"{declaration} => enabled\n{CALL}({argument})\n", argument, 4)


def test_method_on_expression_receiver_leaves_the_direction_qualifier_unknown():
    # The qualifier query cannot resolve the expression receiver's type, so
    # the returned direction has no proven series qualifier.
    source = (
        HEADER + f"method relay(bool enabled) => enabled ? {LONG} : {ALL}\n"
        + "chosen = (close > open).relay()\n"
        + f"{CALL}(chosen)\n"
    )
    from pineforge_codegen.analyzer.qualifiers import ParameterQualifiers
    from pineforge_codegen.analyzer.value_bindings import ValueBindings
    from pineforge_codegen.lexer import Lexer
    from pineforge_codegen.parser import Parser

    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    bindings = ValueBindings(ast, "direction.pine")
    bindings.resolve()
    qualifiers = ParameterQualifiers(ast, bindings.declaration_bindings, "direction.pine")
    assert qualifiers.active
    assert qualifiers.infer(ast.body[-2].value, {}) is None
    qualifiers.check_builtin(ast.body[-1].expr)


def test_named_series_direction_is_refused():
    argument = f"close > open ? {LONG} : {ALL}"
    _refused(HEADER + f"{CALL}(value={argument})\n", argument, 3)


def test_direct_analyzer_checks_simple_parameter():
    from pineforge_codegen.analyzer import Analyzer
    from pineforge_codegen.lexer import Lexer
    from pineforge_codegen.parser import Parser

    argument = f"close > open ? {LONG} : {ALL}"
    source = HEADER + f"{CALL}({argument})\n"
    ast = Parser(Lexer(source).tokenize(), source=source, filename="direct.pine").parse()
    with pytest.raises(CompileError) as caught:
        Analyzer(ast, filename="direct.pine").analyze()
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == CODE
    assert diagnostic.location.file == "direct.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (3, len(CALL) + 2)
    assert diagnostic.args["argument_qualifier"] == "series"
    assert diagnostic.args["parameter_qualifier"] == "simple"


ACCEPTED = [
    pytest.param("", LONG, id="direction-constant"),
    pytest.param("", f"true ? {LONG} : {ALL}", id="literal-condition"),
    pytest.param(f"const chosen = {LONG}\n", "chosen", id="const-direction"),
    pytest.param("useShort = input.bool(true)\n",
                 "useShort ? strategy.direction.short : strategy.direction.long",
                 id="issue-292-input"),
    pytest.param("useShort = input.bool(true)\nchooseLong = not useShort\n",
                 f"chooseLong ? {LONG} : {ALL}", id="input-expression"),
    pytest.param("", f'syminfo.currency == "USD" ? {LONG} : {ALL}', id="simple-syminfo"),
    pytest.param("", f"timeframe.isintraday ? {LONG} : {ALL}", id="simple-timeframe"),
    pytest.param("const bool useLong = true\n",
                 f"useLong ? {LONG} : {ALL}", id="const-expression"),
    pytest.param(f"chosen = {LONG}\nunused() =>\n"
                 f"    chosen = close > open ? {LONG} : {ALL}\n    chosen\n",
                 "chosen", id="unrelated-series-local"),
    pytest.param(f"chosen = close > open ? {LONG} : {ALL}\n"
                 f"run() =>\n    chosen = {LONG}\n    chosen\n",
                 "run()", id="constant-local-shadows-series-global"),
    pytest.param("run(float close) => close > 0\n",
                 f"run(1.0) ? {LONG} : {ALL}", id="parameter-shadows-builtin"),
    pytest.param("isPositive(float value) => value > 0\n",
                 f"isPositive(1.0) ? {LONG} : {ALL}", id="simple-helper-return"),
    pytest.param("relay(bool enabled) => enabled\ngate = input.bool(true)\n",
                 f"relay(gate) ? {LONG} : {ALL}", id="forwarded-input"),
    pytest.param("method relay(bool enabled) => enabled\ngate = input.bool(true)\n",
                 f"gate.relay() ? {LONG} : {ALL}", id="forwarded-method-input"),
    pytest.param("keep(simple int amount) => amount\nx = keep(input.int(7))\n",
                 LONG, id="user-contract-alongside-builtin"),
    pytest.param(f"direction() => {LONG}\n", "direction()", id="unknown-helper-return"),
    # The classifier has no strategy.position_size member evidence.
    # Keep that existing unknown-shape policy rather than extending inference.
    pytest.param("", f"strategy.position_size > 0 ? {LONG} : {ALL}",
                 id="unproved-strategy-member"),
]


def _accepted_source(declarations, argument):
    return HEADER + declarations + f'{CALL}({argument})\nstrategy.entry("L", strategy.long)\n'


@pytest.fixture(scope="module")
def reference():
    tree = reference_codegen(CPP_REFERENCE)
    if tree is None:
        pytest.skip("The integration-base C++ reference commit is unavailable")
    return tree


# What each accepted shape admits is held to the bare literal of the value it
# means, by value, in tests/test_e2e_risk_direction_values.py: the emitted C++
# of an older build is not the contract (the direction is lowered by its value
# now, and the C++ text of that lowering is not what the engine admits).


@pytest.mark.parametrize("declarations,argument", ACCEPTED)
def test_non_series_directions_compile(declarations, argument):
    from tests._compile import compile_cpp, skip_if_no_compile_env

    skip_if_no_compile_env()
    compile_cpp(transpile(_accepted_source(declarations, argument)), label="simple_direction")


def test_series_direction_was_accepted_by_reference(reference, tmp_path):
    pine = tmp_path / "strategy.pine"
    pine.write_text(HEADER + f"{CALL}(close > open ? {LONG} : {ALL})\n", encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before


def test_method_history_reference_failed_for_session_history(reference, tmp_path):
    pine = tmp_path / "strategy.pine"
    pine.write_text(METHOD_HISTORY_REFERENCE, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert not before["ok"]
    error, = (item for item in before["diagnostics"] if item["severity"] == "error")
    assert error["code"] == "PF-E5048"
    assert (error["line"], error["col"]) == (6, 50)


def test_unknown_helper_qualifier_stays_unknown():
    from pineforge_codegen.analyzer.qualifiers import ParameterQualifiers
    from pineforge_codegen.analyzer.value_bindings import ValueBindings
    from pineforge_codegen.lexer import Lexer
    from pineforge_codegen.parser import Parser

    source = _accepted_source(f"direction() => {LONG}\n", "direction()")
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    bindings = ValueBindings(ast, "direction.pine")
    bindings.resolve()
    qualifiers = ParameterQualifiers(ast, bindings.declaration_bindings, "direction.pine")
    call = ast.body[-2].expr
    assert qualifiers.infer(call.args[0], {}) is None
    qualifiers.check_builtin(call)


def test_bound_namespace_root_is_not_a_builtin_contract():
    from pineforge_codegen.analyzer.qualifiers import ParameterQualifiers
    from pineforge_codegen.analyzer.value_bindings import ValueBindings
    from pineforge_codegen.lexer import Lexer
    from pineforge_codegen.parser import Parser

    # Only exercise lexical matching here: other passes own the object's
    # member lookup. An authored root must not activate a namespace contract.
    source = HEADER + f"relay(strategy) =>\n    {CALL}(close > open)\n"
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    bindings = ValueBindings(ast, "direction.pine")
    bindings.resolve()
    qualifiers = ParameterQualifiers(ast, bindings.declaration_bindings, "direction.pine")
    assert not qualifiers.active
    qualifiers.check_builtin(ast.body[-1].body[-1].expr)


def test_literal_string_direction_is_not_rejected_as_series():
    # A string literal is a constant, so it is not refused as a series argument.
    # Which direction a literal string means is NOT pinned here: the host
    # lowering represents strategy.direction.* as integers, and whether a
    # literal string equals the constant is not measured against TradingView
    # (the older build lowered every such string to all directions; the
    # current lowering reads its text).
    assert transpile(_accepted_source("", '"long"'), filename="direction.pine")
