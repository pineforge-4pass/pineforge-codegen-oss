"""Lower-timeframe tuple elements retain independent array types."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.symbols import TypeSpec
from tests._compile import compile_cpp


@pytest.mark.parametrize("helper", [False, True], ids=["inline", "helper"])
@pytest.mark.parametrize("named", [False, True], ids=["positional", "named"])
def test_lower_timeframe_tuple_collection_types_are_per_element(helper, named):
    payload = '[close, bar_index, close > open, "sample", color.blue]'
    declaration = ""
    if helper:
        declaration = (
            "pack(float price, int ordinal, bool rising) =>\n"
            '    [price, ordinal, rising, "sample", color.blue]\n'
        )
        payload = "pack(close, bar_index, close > open)"
    arguments = (
        f'expression = {payload}, timeframe = "15", symbol = syminfo.tickerid'
        if named else f'syminfo.tickerid, "15", {payload}'
    )
    source = (
        '//@version=6\nstrategy("typed lower tuple")\n' + declaration
        + f"[prices, positions, rising, labels, colors] = request.security_lower_tf({arguments})\n"
        + "plot(array.size(prices) + array.size(positions) + array.size(rising)"
        + " + array.size(labels) + array.size(colors))\n"
    )
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    context = Analyzer(ast).analyze()
    for name, inner in zip(
        ("prices", "positions", "rising", "labels", "colors"),
        ("float", "int64", "bool", "string", "color"),
    ):
        assert context.collection_types[name] == TypeSpec.array(TypeSpec.primitive(inner))
    compile_cpp(transpile(source, check_support=True), label="lower-tuple-per-element-types")


def test_lower_timeframe_tuple_arrays_keep_lexical_method_dispatch():
    source = """//@version=6
strategy("local lower tuple")
readSize() =>
    [prices, rising] = request.security_lower_tf(syminfo.tickerid, "15", [close, close > open])
    prices.size() + rising.size()
plot(readSize())
"""
    compile_cpp(transpile(source, check_support=True), label="lower-tuple-lexical-array-dispatch")
