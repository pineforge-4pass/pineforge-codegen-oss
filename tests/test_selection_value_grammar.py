"""Grammar slots and typed lowering of block selection values."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import ASTNode, Identifier, VarDecl
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.errors import CodegenInvariantError, CompileError, Level, Phase
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._compile import compile_cpp, run_emitted_tu


FIXTURES = Path(__file__).parent / "fixtures" / "selection_values"
ROOT = Path(__file__).resolve().parent.parent


def _source(name: str) -> str:
    return (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")


@pytest.fixture
def protocol():
    namespace = {}
    glue = (ROOT / "gate" / "glue.py").read_text(encoding="utf-8")
    exec(compile(glue.replace('"/codegen"', repr(str(ROOT))), "glue.py", "exec"),
         namespace)
    return lambda source: json.loads(namespace["transpile_json"](source))


# These locations count from the first authored compound keyword, not from
# TradingView's later recovery token. Each fixture has its own refusal tape.
REFUSED = [
    # Five callable comma tails share eight spaces and a 25-character prefix.
    ("price-close", 6, 34, "if", "CE10013"),
    ("price-high", 6, 34, "if", "CE10013"),
    ("price-hl2", 6, 34, "if", "CE10013"),
    ("price-low", 6, 34, "if", "CE10013"),
    ("price-open", 6, 34, "if", "CE10013"),
    # Four spaces plus "0 => " / "=> " put the keyword at column 10 / 8.
    ("nested-case", 6, 10, "switch", "CE10013"),
    ("nested-default", 8, 8, "switch", "CE10013"),
    # The assignment's "value := " contributes nine more characters.
    ("inline-assignment-switch", 6, 19, "switch", "CE10013"),
    # Four spaces plus the case prefix precede the compound.
    ("inline-case-if", 5, 10, "if", "CE10013"),
    ("inline-case-switch", 6, 10, "switch", "CE10013"),
    # The comma sequence's complete simple statement precedes the compound.
    ("inline-comma-if", 6, 34, "if", "CE10013"),
    ("inline-comma-switch", 5, 29, "switch", "CE10013"),
    # The declaration's "chosen = " also contributes nine characters.
    ("inline-declaration-switch", 5, 19, "switch", "CE10013"),
    # Four spaces plus "=> " precede each default-arm compound.
    ("inline-default-if", 6, 8, "if", "CE10013"),
    ("inline-default-switch", 8, 8, "switch", "CE10013"),
    # Reject the outer inline if before descending into its valid block.
    ("inline-if-with-switch", 5, 10, "if", "CE10013"),
    # Function/method arms have eight spaces plus "0 => ".
    ("inline-method-switch", 6, 14, "switch", "CE10013"),
    ("inline-unreached-function", 6, 14, "switch", "CE10013"),
    # A discarded result obeys the same grammar before statement emission.
    ("inline-unused-switch", 6, 10, "switch", "CE10013"),
]


@pytest.mark.parametrize(
    "name,line,col,keyword,tape", REFUSED,
    ids=[f"{row[0]}-{row[4]}" for row in REFUSED],
)
def test_inline_compounds_stop_before_emission(
    monkeypatch, protocol, name, line, col, keyword, tape,
):
    def no_codegen(*args, **kwargs):
        pytest.fail("A grammar refusal must happen before code generation")

    monkeypatch.setattr(CodeGen, "__init__", no_codegen)
    source = _source(name)
    for check_support in (True, False):
        with pytest.raises(CompileError) as caught:
            transpile(source, filename="selection.pine", check_support=check_support)
        diagnostic, = caught.value.diagnostics
        assert diagnostic.level is Level.ERROR
        assert diagnostic.phase is Phase.PARSER
        assert diagnostic.code == "PF-E0037"
        assert diagnostic.args == {"token_type": keyword.upper(), "token": keyword}
        assert diagnostic.location.file == "selection.pine"
        assert (diagnostic.location.line, diagnostic.location.col) == (line, col)
        assert diagnostic.location.end_col == col + len(keyword)
    envelope = protocol(source)
    assert not envelope["ok"]
    assert "cpp" not in envelope
    diagnostic, = envelope["diagnostics"]
    assert (diagnostic["code"], diagnostic["line"], diagnostic["col"]) == (
        "PF-E0037", line, col,
    )


SLOT_REFUSALS = [
    # "for i = " places a structured start at column 9.
    ("for-start-if", 5, 9, "if", "CE10161"),
    ("for-start-switch", 5, 9, "switch", "CE10161"),
    # "for i = 0 to " places a structured end at column 14.
    ("for-end-if", 5, 14, "if", "CE10161"),
    ("for-end-switch", 5, 14, "switch", "CE10161"),
    # Four spaces plus "float value = " put a field default at column 19.
    ("udt-default-if", 5, 19, "if", "CE10133"),
    ("udt-default-switch", 5, 19, "switch", "CE10133"),
    # Four spaces plus "first = " put an enum title at column 13.
    ("enum-title-switch", 5, 13, "switch", "CE10133"),
    # The false operand starts after the question, true operand and colon.
    # CE10150 here names the authored if/switch keyword in expression position
    # on line 4; it is not an identifier-name error.
    ("ternary-if", 4, 36, "if", "CE10150"),
    ("ternary-switch", 4, 36, "switch", "CE10150"),
]


@pytest.mark.parametrize(
    "name,line,col,keyword,tape", SLOT_REFUSALS,
    ids=[f"{row[0]}-{row[4]}" for row in SLOT_REFUSALS],
)
def test_expression_only_parent_slots_refuse_blocks(protocol, name, line, col, keyword, tape):
    # Each parent production admits an expression; the compound's own token
    # is the first point that violates that production.
    envelope = protocol(_source(name))
    assert not envelope["ok"] and "cpp" not in envelope
    diagnostic, = envelope["diagnostics"]
    assert (diagnostic["code"], diagnostic["line"], diagnostic["col"]) == (
        "PF-E0037", line, col,
    )
    assert diagnostic["args"] == {"token_type": keyword.upper(), "token": keyword}


def test_delimited_switch_ce10156_keeps_its_existing_parser_refusal(protocol):
    # Green before and after: delimiter depth suppresses the newline, so the
    # switch header sees the first case's 0 at line 5, column 5 instead.
    envelope = protocol(_source("delimited-switch"))
    assert not envelope["ok"] and "cpp" not in envelope
    diagnostic, = envelope["diagnostics"]
    assert (diagnostic["code"], diagnostic["line"], diagnostic["col"]) == (
        "PF-E0025", 5, 5,
    )


# Except the if timeframe alias, these are acceptance/compile preservation
# pins, green before and after. Sources and tape names are identical.
ACCEPTED = [
    "block-assignment-switch", "block-case-if", "block-case-switch",
    "block-collection-switch", "block-comma-if", "block-comma-switch",
    "block-declaration-switch", "block-default-if", "block-default-switch",
    "block-function-switch", "block-if-with-switch", "block-method-switch",
    "block-no-default-switch", "block-side-effects-switch", "block-tuple-switch",
    "block-unreached-function", "block-var-switch",
    "header-function-if", "header-function-switch", "header-method-switch",
    "security-if-alias", "security-switch-alias",
    "security-timeframe-if", "security-timeframe-switch",
    "comma-simple-arm", "literal-scalar-twins", "udt-default-literal",
    "for-literal-bounds",
]


def _assert_value_output(cpp: str) -> None:
    assert "/* unknown */" not in cpp
    assert "/* null */" not in cpp
    assert not re.search(r"(?<![=!<>])=(?:\s|/\*.*?\*/)*;", cpp, re.DOTALL)


@pytest.mark.parametrize("name", ACCEPTED)
def test_accepted_block_values_translate(name):
    # The block parser leaves bare selections; typed assignment recursion
    # emits the terminal value without a scalar placeholder.
    _assert_value_output(transpile(_source(name)))


@pytest.mark.parametrize("name", ACCEPTED)
def test_accepted_block_values_compile(name):
    compile_cpp(transpile(_source(name)), label=name)


def test_unmeasured_typed_blocks_remain_accepted():
    # This source uses ordinary identifiers; nested bool and string tails
    # must keep their types through the recursive value assignments.
    cpp = transpile(_source("block-typed-values"))
    _assert_value_output(cpp)
    compile_cpp(cpp, label="typed nested blocks")


VALUE_ROWS = [
    # Outer residue 0 selects inner parity 1/2; all other residues yield 3.
    ("block-case-if", "value", "1 3 3 2 3 3"),
    ("block-case-switch", "value", "1 3 3 2 3 3"),
    ("block-assignment-switch", "value", "1 3 3 2 3 3"),
    ("block-declaration-switch", "value", "1 3 3 2 3 3"),
    ("block-comma-switch", "value", "1 3 3 2 3 3"),
    ("block-function-switch", "value", "1 3 3 2 3 3"),
    ("block-method-switch", "value", "1 3 3 2 3 3"),
    ("block-collection-switch", "array.get(values, 0)", "1 3 3 2 3 3"),
    # Default residues use parity after the outer cases 0 and 1.
    ("block-default-switch", "value", "1 2 3 1 2 4"),
    ("block-default-if", "value", "1 3 2 1 2 3"),
    # n=0 reaches the deepest zero case; n=3 takes the odd branch.
    ("block-if-with-switch", "value", "1 4 4 3 4 4"),
    # An unmatched inner switch yields na rather than its previous value.
    ("block-no-default-switch", "value", "1 3 3 na 3 3"),
    # Each selected tuple's two elements sum to 11, 22 or 33.
    ("block-tuple-switch", "value + other", "11 33 33 22 33 33"),
    # The first declaration selects 1 and its one-shot latch retains it.
    ("block-var-switch", "value", "1 1 1 1 1 1"),
    # Exactly one selected push occurs on each of the six bars.
    ("block-side-effects-switch", "array.size(values)", "1 2 3 4 5 6"),
    # The callable is never reached; its ternary always chooses 4.
    ("block-unreached-function", "value", "4 4 4 4 4 4"),
    # A close of 100 gives x+1.125 on even bars and -x on odd bars.
    ("block-comma-if", "value", "101.125 100 101.125 100 101.125 100"),
    # Header compounds and simple comma arms select by bar parity.
    ("header-function-if", "value", "1 2 1 2 1 2"),
    ("header-function-switch", "value", "1 2 1 2 1 2"),
    ("header-method-switch", "value", "1 2 1 2 1 2"),
    ("comma-simple-arm", "value", "1 2 1 2 1 2"),
]


@pytest.mark.parametrize("name,observation,expected", VALUE_ROWS,
                         ids=[row[0] for row in VALUE_ROWS])
def test_block_selection_values_and_effects(name, observation, expected):
    # Add only an observable read after the byte-exact taped strategy.
    source = _source(name) + f"observed = {observation}\n"
    cpp = transpile(source)
    driver = r'''
#include <iostream>
int main() {
    Bar bars[6];
    for (int i = 0; i < 6; ++i)
        bars[i] = Bar{100.0, 101.0, 99.0, 100.0, 1.0,
                      1743465600000LL + i * 900000LL};
    for (int count = 1; count <= 6; ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars, count);
        if (!strategy.last_error().empty()) return 1;
        if (is_na(strategy.observed)) std::cout << "na";
        else std::cout << strategy.observed;
        std::cout << " ";
    }
}
'''
    assert run_emitted_tu(cpp, driver, opt="-O2", label=name).split() == expected.split()


def _generator(source: str) -> tuple[CodeGen, object]:
    ast = Parser(Lexer(source).tokenize(), source=source, filename="value.pine").parse()
    return CodeGen(Analyzer(ast, filename="value.pine").analyze()), ast


def test_missing_value_lowering_raises_an_internal_invariant():
    source = '//@version=6\nstrategy("value")\nvalue = close\n'
    gen, ast = _generator(source)
    declaration = next(stmt for stmt in ast.body if isinstance(stmt, VarDecl))
    unsupported = ASTNode(loc=declaration.value.loc)
    # The old dispatcher returned a comment for this otherwise unhandled kind.
    with pytest.raises(CodegenInvariantError) as caught:
        gen._visit_expr(unsupported)
    assert caught.value.node is unsupported


@pytest.mark.parametrize("translate", [transpile, transpile_full])
def test_translation_boundary_reports_the_internal_invariant(monkeypatch, protocol, translate):
    original = CodeGen._visit_expr

    def missing_lowering(self, node):
        if isinstance(node, Identifier) and node.name == "close":
            node = ASTNode(loc=node.loc)
        # Ordinary resolution retries must not swallow a lowering invariant.
        try:
            return original(self, node)
        except Exception:
            return "0.0"

    monkeypatch.setattr(CodeGen, "_visit_expr", missing_lowering)
    source = '//@version=6\nstrategy("value")\nvalue = close\n'
    # The unsupported value inherits close's actual authored location.
    with pytest.raises(CompileError) as caught:
        translate(source, filename="value.pine")
    diagnostic, = caught.value.diagnostics
    assert diagnostic.phase is Phase.CODEGEN
    assert diagnostic.code == "PF-E5083"
    assert diagnostic.args == {"node_kind": "ASTNode"}
    assert (diagnostic.location.file, diagnostic.location.line,
            diagnostic.location.col) == ("value.pine", 3, 9)
    envelope = protocol(source)
    assert not envelope["ok"] and "cpp" not in envelope
    assert envelope["diagnostics"][0]["code"] == "PF-E5083"


@pytest.mark.parametrize("translate", [transpile, transpile_full])
def test_unlocated_missing_lowering_reports_the_missing_location(monkeypatch, translate):
    original = CodeGen._visit_expr

    def missing_lowering(self, node):
        if isinstance(node, Identifier) and node.name == "close":
            node = ASTNode()
        return original(self, node)

    monkeypatch.setattr(CodeGen, "_visit_expr", missing_lowering)
    source = '//@version=6\nstrategy("value")\nvalue = close\n'
    # An unlocated internal value cannot truthfully be attributed to line 1.
    with pytest.raises(RuntimeError, match="AST node 'ASTNode' has no source location"):
        translate(source, filename="value.pine")


@pytest.mark.parametrize(
    "branches,call,expected_type,expected",
    [
        # A typed numeric argument evaluates the selected branch on each bar.
        ("1.25\nelse\n    2.5", "math.abs(chosen)", "double", "1.25 2.5"),
        # A collection element consumes exactly the same numeric value.
        ("1.25\nelse\n    2.5", "array.get(array.from(chosen), 0)",
         "double", "1.25 2.5"),
        # An array-valued selection keeps its element type and selected ID.
        ("array.from(1.25)\nelse\n    array.from(2.5)", "array.get(chosen, 0)",
         "std::vector<double>", "1.25 2.5"),
        # String consumers must receive a string, never a numeric temporary.
        ('"one"\nelse\n    "second"', "str.length(chosen)",
         "std::string", "3 6"),
        # Boolean consumers keep the selected bool without numeric conversion.
        ("true\nelse\n    false", "chosen ? 7 : 9", "bool", "7 9"),
        # A ternary's selected numeric operand evaluates its block lazily.
        ("1.25\nelse\n    2.5", "bar_index == 0 ? chosen : 9.0",
         "double", "1.25 9"),
    ],
    ids=["numeric-argument", "collection-element", "array-argument", "string-argument",
         "bool-condition", "ternary-branch"],
)
@pytest.mark.parametrize("selection_kind", ["if", "switch"])
def test_substituted_block_values_share_the_typed_expression_lowering(
    branches, call, expected_type, expected, selection_kind,
):
    if selection_kind == "if":
        selection = "if bar_index % 2 == 0\n    " + branches
    else:
        first, last = branches.split("\nelse\n    ")
        selection = f"switch bar_index % 2\n    0 => {first}\n    => {last}"
    source = (
        '//@version=6\nstrategy("substituted block")\n'
        'chosen = ' + selection
        + '\nobserved = ' + call + '\n'
    )
    gen, ast = _generator(source)
    selected = next(stmt.value for stmt in ast.body
                    if isinstance(stmt, VarDecl) and stmt.name == "chosen")
    observed = next(stmt for stmt in ast.body
                    if isinstance(stmt, VarDecl) and stmt.name == "observed")

    def expand(value):
        # Model alias substitution after grammar validation and analysis.
        # No authored compound is being admitted into a refused syntax slot.
        if isinstance(value, Identifier) and value.name == "chosen":
            return selected
        if isinstance(value, ASTNode):
            for name, item in vars(value).items():
                if name not in ("loc", "annotations"):
                    setattr(value, name, expand(item))
        elif isinstance(value, list):
            return [expand(item) for item in value]
        elif isinstance(value, dict):
            return {name: expand(item) for name, item in value.items()}
        return value

    observed.value = expand(observed.value)
    expr = gen._visit_expr(selected)
    assert f"-> {expected_type}" in expr
    cpp = gen.generate()
    _assert_value_output(cpp)
    driver = r'''
#include <iostream>
int main() {
    Bar bars[2] = {
        {100.0, 101.0, 99.0, 100.0, 1.0, 1743465600000LL},
        {100.0, 101.0, 99.0, 100.0, 1.0, 1743466500000LL}
    };
    for (int count : {1, 2}) {
        GeneratedStrategy strategy;
        strategy.run(bars, count);
        if (!strategy.last_error().empty()) return 1;
        std::cout << strategy.observed << " ";
    }
}
'''
    assert run_emitted_tu(cpp, driver, opt="-O2",
                         label="substituted block value").split() == expected.split()


def test_timeframe_if_reads_inputs_before_the_first_bar():
    cpp = transpile(_source("security-timeframe-if"))
    setup = cpp.split("void configure_security_evaluators() override", 1)[1]
    setup = setup.split("void ", 1)[0]
    # Before on_bar, pick's member has not received its input; its getter is
    # the only correct condition for the std::string-valued setup lambda.
    assert "-> std::string" in setup
    assert 'get_input_bool("Choose chart"' in setup
    assert not re.search(r"\bif \(pick\)", setup)
    assert "script_tf_" in setup and 'std::string("1D")' in setup


def test_foreign_symbol_if_uses_the_same_setup_value_lowering():
    source = (
        '//@version=6\nstrategy("selected symbol")\n'
        'choose = input.bool(true, "Choose symbol")\n'
        'symbol = if choose\n    "BINANCE:ETHUSDT"\n'
        'else\n    "BINANCE:BTCUSDT"\n'
        'value = request.security(symbol, "60", close)\nplot(value)\n'
    )
    cpp = transpile(source)
    _assert_value_output(cpp)
    # A display-only request becomes na; exercise the registration consumer
    # itself with the analyzed symbol rather than assuming a feed is emitted.
    gen, ast = _generator(source)
    symbol = next(stmt.value for stmt in ast.body
                  if isinstance(stmt, VarDecl) and stmt.name == "symbol")
    lines = []
    gen._emit_foreign_security_registration(
        {"sec_id": 0, "symbol_node": symbol},
        'std::string("60")', "false", "false", lines,
    )
    # The consumer renders its symbol through the same setup-time adapter.
    registration = "\n".join(lines).split("const std::string _pf_symbol = ", 1)[1]
    registration = registration.split("_pf_symbol_is_chart", 1)[0]
    assert "-> std::string" in registration
    assert 'get_input_bool("Choose symbol"' in registration
    assert "BINANCE:ETHUSDT" in registration and "BINANCE:BTCUSDT" in registration
    compile_cpp(cpp, label="selected foreign symbol")
