"""Builtin join's proven receiver mismatch, independent of C++ storage.

The retained compiler response refuses array<bool>. String, int and float
have independent admissions; other domains stay open rather than inheriting
an exclusive array<float> whitelist from that one overload diagnostic.
"""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import Identifier
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.errors import CompileError, Level, Phase, SourceLocation
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.symbols import PineType, TypeSpec


HEADER = '//@version=6\nstrategy("join domains")\n'
FILENAME = "join_domains.pine"


def _analyze(body):
    source = HEADER + body
    program = Parser(
        Lexer(source, filename=FILENAME).tokenize(),
        source=source, filename=FILENAME,
    ).parse()
    return Analyzer(program, filename=FILENAME).analyze()


def _refusal(body, *, analyze_only=False):
    with pytest.raises(CompileError) as caught:
        if analyze_only:
            _analyze(body)
        else:
            transpile(HEADER + body, filename=FILENAME)
    diagnostic = next(d for d in caught.value.diagnostics if d.level == Level.ERROR)
    assert diagnostic.code == "PF-E1088"
    assert diagnostic.args["name"] == "array.join"
    assert "array<bool>" in diagnostic.message
    assert diagnostic.args["spelled"] == "CE10123"
    assert diagnostic.hint == "Use the parameters of TradingView's signature."
    return diagnostic


@pytest.mark.parametrize("initializer", [
    "array.from(true, false)",
    "array.new_bool(0)",
])
@pytest.mark.parametrize("call", [
    'array.join(items, "|")',
    'array.join(id=items, separator="|")',
    'array.join(separator="|", id=items)',
    'array.join(items, separator="|")',
    "array.join(items)",
    'items.join("|")',
    'items.join(separator="|")',
    "items.join()",
])
def test_bool_domain_refusal_is_located_on_the_receiver(initializer, call):
    statement = f"observed = {call}"
    diagnostic = _refusal(f"items = {initializer}\n{statement}\n")
    column = statement.index("items") + 1
    assert diagnostic.phase == Phase.ANALYZER
    assert diagnostic.location == SourceLocation(
        FILENAME, 4, column, column + len("items"),
    )


@pytest.mark.parametrize("body", [
    'observed = array.join(array.from(true, false), "|")\n',
    "render_flags(array<bool> flags) => flags.join()\n",
    "items = array.from(true, false)\nplot(str.length(items.join()))\n",
    "items = array.from(true, false)\nplot(str.length(string=items.join()))\n",
    "items = array.from(true, false)\nif false\n    observed = items.join()\n",
])
def test_analyzer_checks_join_domain_before_ignored_calls_can_disappear(body):
    assert _refusal(body, analyze_only=True).phase == Phase.ANALYZER


@pytest.mark.parametrize("initializer", [
    'array.from("alpha", "beta")',
    "array.from(1, -2)",
    "array.from(1.25, -2.5)",
    "array.new<color>(0)",
    "array.new<JoinItem>(0)",
])
def test_domain_guard_does_not_refuse_other_element_types(initializer):
    # Analyzer-only scope for color/UDT: this does not assert native compile
    # support or a TradingView value for either unmeasured domain.
    context = _analyze(
        "type JoinItem\n    int value = 0\n"
        f'items = {initializer}\nobserved = array.join(items, "|")\n'
    )
    assert context.symbols.resolve("observed").pine_type == PineType.STRING


def test_namespace_receiver_type_query_does_not_register_ta_again():
    source = HEADER + 'observed = array.join(array.from(ta.change(close)), "|")\n'
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    analyzer = Analyzer(program)
    analyzer.analyze()
    before = len(analyzer._ta_call_sites)
    assert before > 0
    join_call = program.body[-1].value
    assert analyzer._fixed_array_call_result(join_call, None) == TypeSpec.primitive("string")
    assert len(analyzer._ta_call_sites) == before


AUTHORED_JOIN = (
    'method join(array<bool> flags, string separator = ";") =>\n'
    "    str.length(str.repeat(separator, array.size(flags)))\n"
)


def test_authored_bool_join_keeps_its_declared_default():
    body = AUTHORED_JOIN + "items = array.from(true, false)\nobserved = items.join()\n"
    assert _analyze(body).symbols.resolve("observed").pine_type == PineType.INT
    cpp = transpile(HEADER + body, filename=FILENAME)
    assert 'std::string(";")' in cpp
    assert "_pf_join_parts" not in cpp


def test_authored_method_does_not_override_namespace_join_domain():
    body = AUTHORED_JOIN + 'items = array.from(true, false)\nobserved = array.join(items, "|")\n'
    assert _refusal(body).phase == Phase.ANALYZER


def test_codegen_checks_join_domain_when_analysis_is_bypassed():
    generator = CodeGen(_analyze("plot(close)\n"))
    location = SourceLocation(FILENAME, 7, 9, 14)
    receiver = Identifier(name="items", loc=location)
    with pytest.raises(CompileError) as caught:
        generator._array_method_expr(
            "items", "join", ['std::string("|")'],
            TypeSpec.array(TypeSpec.primitive("bool")), receiver,
        )
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E1088"
    assert diagnostic.phase == Phase.CODEGEN
    assert diagnostic.location == location
