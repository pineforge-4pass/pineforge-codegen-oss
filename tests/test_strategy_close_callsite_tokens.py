"""Callsite-token coverage for authored ``strategy.close`` statements."""

import re

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser


_CLOSE_TOKEN = re.compile(r"strategy_close\([^;\n]*, ([0-9]+ULL)\)")


def _close_tokens(cpp: str) -> list[str]:
    return _CLOSE_TOKEN.findall(cpp)


def test_direct_close_sites_have_distinct_deterministic_location_tokens():
    source = """//@version=6
strategy("T")
if bar_index == 1
    strategy.close("A")
    strategy.close("B")
"""

    first = _close_tokens(transpile(source))
    second = _close_tokens(transpile(source))
    # FuncCall.loc points at each opening parenthesis: lines 4/5, column 19.
    expected = [f"{(line << 32) | 19}ULL" for line in (4, 5)]

    assert first == expected
    assert second == expected
    assert len(set(first)) == 2
    assert "0ULL" not in first


def test_close_inside_loop_emits_one_nonzero_inner_site_token():
    source = """//@version=6
strategy("T")
for i = 0 to 2
    strategy.close("L" + str.tostring(i))
"""

    tokens = _close_tokens(transpile(source))

    assert tokens == [f"{(4 << 32) | 19}ULL"]


def test_close_inside_shared_udf_clones_keeps_one_inner_site_token():
    source = """//@version=6
strategy("T")
close_one(string id) =>
    avg = ta.sma(close, 2)
    strategy.close(id)
    avg
if bar_index == 2
    close_one("A")
    close_one("B")
"""

    tokens = _close_tokens(transpile(source))

    # Stateful-callable isolation emits two UDF bodies. Both must carry the
    # authored inner close statement's location, not the outer invocation.
    assert len(tokens) == 2
    assert set(tokens) == {f"{(5 << 32) | 19}ULL"}


def test_source_less_synthetic_close_uses_compatibility_token_zero():
    source = """//@version=6
strategy("T")
strategy.close("Long")
"""
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    close_call = ast.body[-1].expr
    close_call.loc = None

    cpp = CodeGen(Analyzer(ast).analyze()).generate()

    assert _close_tokens(cpp) == ["0ULL"]
