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


METHOD_ARGUMENT = f"session.ismarket[1] ? {LONG} : {ALL}"
METHOD_SOURCE = (
    HEADER
    + "type Foo\n    float v = 1\n"
    + "method m(Foo self) =>\n"
    + f"    {CALL}({METHOD_ARGUMENT})\n"
    + "    self.v > 0\n"
    + "foo = Foo.new()\n"
    + "x = foo.m()\n"
    + 'if x\n    strategy.entry("L", strategy.long)\n'
)


def test_method_session_history_reports_qualifier_before_pf_e5048():
    # This is the old REFUSED row in test_e2e_session_history.py. It now
    # points at session (argument start, column 34), not '[' (column 50).
    _refused(METHOD_SOURCE, METHOD_ARGUMENT, 6)


@pytest.mark.parametrize("body,line", [
    (f"chosen = close > open ? {LONG} : {ALL}\n{CALL}(chosen)\n", 4),
    (f"signal = close > open\nchosen = signal ? {LONG} : {ALL}\n{CALL}(chosen)\n", 5),
    (f"chosen = {LONG}\nchosen := close > open ? {LONG} : {ALL}\n{CALL}(chosen)\n", 5),
    (f"chosen = {LONG}\nif close > open\n    chosen := {ALL}\n{CALL}(chosen)\n", 6),
    (f"run() =>\n    chosen = close > open ? {LONG} : {ALL}\n"
     f"    {CALL}(chosen)\n    1\nx = run()\n", 5),
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
    argument = f"enabled ? {LONG} : {ALL}"
    source = (
        HEADER + f"{declaration} =>\n    {CALL}({argument})\n"
        + f"    1\nx = {invocation}\n"
    )
    _refused(source, argument, 4)


def test_method_on_expression_receiver_forwarding_series_is_accepted_by_unknown_evidence():
    # ACCEPTED BECAUSE THE EVIDENCE IS UNKNOWN, not because TradingView accepts
    # it: whether TradingView refuses a series receiver that a method forwards
    # into allow_entry_in's simple parameter is an open question (REPORT-Q2.md,
    # round 3, QUESTIONS FOR TV). This source was the "method" row of the test
    # above. The classifier refuses only what it proves, and it has no proof
    # here: the analyzer gives the expression receiver `close > open` no type,
    # so the typed-parameter check never runs for the call, and the classifier's
    # own receiver hint is unresolved for it too (its method key is not found).
    # When a later change gives this call evidence, move the source back into
    # the refused rows above: the last assertion then fails, which is the
    # signal to do so.
    argument = f"enabled ? {LONG} : {ALL}"
    source = (
        HEADER + f"method relay(bool enabled) =>\n    {CALL}({argument})\n"
        + "    1\nx = (close > open).relay()\n"
    )
    assert transpile(source, filename="direction.pine")

    from pineforge_codegen.analyzer.qualifiers import ParameterQualifiers
    from pineforge_codegen.analyzer.value_bindings import ValueBindings
    from pineforge_codegen.lexer import Lexer
    from pineforge_codegen.parser import Parser

    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    bindings = ValueBindings(ast, "direction.pine")
    bindings.resolve()
    qualifiers = ParameterQualifiers(ast, bindings.declaration_bindings, "direction.pine")
    assert qualifiers.active
    assert qualifiers.infer(ast.body[-1].value, {}) is None


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
                 f"run() =>\n    chosen = {LONG}\n    {CALL}(chosen)\n    1\nx = run()\n",
                 LONG, id="constant-local-shadows-series-global"),
    pytest.param("run(float close) =>\n"
                 f"    {CALL}(close > 0 ? {LONG} : {ALL})\n    1\nx = run(1.0)\n",
                 LONG, id="parameter-shadows-builtin"),
    pytest.param("isPositive(float value) => value > 0\n",
                 f"isPositive(1.0) ? {LONG} : {ALL}", id="simple-helper-return"),
    pytest.param("relay(bool enabled) =>\n"
                 f"    {CALL}(enabled ? {LONG} : {ALL})\n"
                 "    1\nx = relay(input.bool(true))\n",
                 LONG, id="forwarded-input"),
    pytest.param("method relay(bool enabled) =>\n"
                 f"    {CALL}(enabled ? {LONG} : {ALL})\n"
                 "    1\ngate = input.bool(true)\nx = gate.relay()\n",
                 LONG, id="forwarded-method-input"),
    pytest.param("keep(simple int amount) => amount\nx = keep(input.int(7))\n",
                 LONG, id="user-contract-alongside-builtin"),
    pytest.param(f"direction() => {LONG}\n", "direction()", id="unknown-helper-return"),
    # CLD's classifier has no strategy.position_size member evidence.
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


@pytest.mark.parametrize("declarations,argument", ACCEPTED)
def test_non_series_directions_keep_complete_cpp(declarations, argument, reference, tmp_path):
    source = _accepted_source(declarations, argument)
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before
    assert transpile(source) == before["cpp"]


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
    pine.write_text(METHOD_SOURCE, encoding="utf-8")
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


def test_literal_string_direction_is_not_rejected_as_series(reference, tmp_path):
    # The host lowering represents strategy.direction.* as integers.
    # Whether literal strings should lower to those integers is a separate gap.
    source = _accepted_source("", '"long"')
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before
    assert transpile(source) == before["cpp"]
