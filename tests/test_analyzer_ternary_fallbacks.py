"""Analyzer ownership and state requirements for ternary fallbacks."""

import gc
import weakref

import pytest

from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import BoolLiteral, NaLiteral, NumberLiteral, Ternary
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.symbols import PineType


def _analyzer() -> Analyzer:
    source = '//@version=6\nstrategy("ternary types")\n'
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    analyzer = Analyzer(ast)
    analyzer.analyze()
    return analyzer


def test_temporary_ternary_fallback_survives_until_later_type_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = _analyzer()

    def analyze_temporary():
        node = Ternary(
            condition=BoolLiteral(value=True),
            true_val=NaLiteral(),
            false_val=NumberLiteral(value=1),
        )
        assert analyzer._visit_Ternary(node) == PineType.NA
        return weakref.ref(node)

    reference = analyze_temporary()
    gc.collect()
    retained = reference()
    assert retained is not None

    queried = []
    type_spec_from_expr = analyzer._type_spec_from_expr

    def record_query(node, **kwargs):
        if isinstance(node, Ternary):
            assert node is reference()
            queried.append(node)
        return type_spec_from_expr(node, **kwargs)

    monkeypatch.setattr(analyzer, "_type_spec_from_expr", record_query)
    assert analyzer._type_spec_from_expr(retained) is None
    assert queried
    assert all(node is retained for node in queried)


def test_ternary_type_query_requires_the_fallback_registry() -> None:
    analyzer = _analyzer()
    node = Ternary(
        condition=BoolLiteral(value=True),
        true_val=NaLiteral(),
        false_val=NumberLiteral(value=1),
    )
    del analyzer._ternary_int_fallbacks

    with pytest.raises(AttributeError, match="_ternary_int_fallbacks"):
        analyzer._type_spec_from_expr(node)
