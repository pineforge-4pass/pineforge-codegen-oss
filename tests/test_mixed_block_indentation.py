"""Mixed indentation preserves nested block structure and accepted wraps."""

import pytest

from pineforge_codegen.ast_nodes import IfStmt, VarDecl
from pineforge_codegen.lexer import Lexer, TokenType
from pineforge_codegen.parser import Parser


@pytest.mark.parametrize(
    "outer,inner",
    [("\t", "\t    "), ("    ", "    \t"), ("\t", "\t\t"), ("    ", "        ")],
)
def test_mixed_complete_levels_keep_nested_ast_and_dedent(outer, inner):
    source = (
        "if close > open\n" + outer + "if high > close\n"
        + inner + "observed = close\n"
        + "after = open\n"
    )
    tokens = Lexer(source).tokenize()
    assert [token.line for token in tokens if token.type == TokenType.INDENT] == [2, 3]
    assert [token.line for token in tokens if token.type == TokenType.DEDENT] == [4, 4]
    program = Parser(tokens, source=source).parse()
    outer_node, after = program.body
    assert isinstance(outer_node, IfStmt)
    (inner_node,) = outer_node.body
    assert isinstance(inner_node, IfStmt)
    (observed,) = inner_node.body
    assert isinstance(observed, VarDecl)
    assert observed.name == "observed"
    assert isinstance(after, VarDecl)
    assert after.name == "after"


@pytest.mark.parametrize("extra_spaces", [1, 2, 3])
def test_previously_accepted_tab_first_incomplete_widths_keep_their_block(extra_spaces):
    source = "if close > open\n\t" + " " * extra_spaces + "observed = close\n"
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    (declaration,) = program.body[0].body
    assert declaration.name == "observed"
