# tests/test_lexer.py
from pineforge_codegen.lexer import Lexer, TokenType

def test_simple_assignment():
    tokens = Lexer("x = 14\n").tokenize()
    types = [t.type for t in tokens]
    assert types == [TokenType.IDENT, TokenType.EQUALS, TokenType.NUMBER,
                     TokenType.NEWLINE, TokenType.EOF_TOKEN]

def test_indent_dedent():
    src = "if true\n    x = 1\ny = 2\n"
    tokens = Lexer(src).tokenize()
    types = [t.type for t in tokens]
    assert TokenType.INDENT in types
    assert TokenType.DEDENT in types

def test_scientific_notation():
    tokens = Lexer("x = 1.5e-3\n").tokenize()
    num = [t for t in tokens if t.type == TokenType.NUMBER][0]
    assert num.value == "1.5e-3"

def test_leading_dot_float():
    tokens = Lexer("x = .5\n").tokenize()
    num = [t for t in tokens if t.type == TokenType.NUMBER][0]
    assert num.value == "0.5" or num.value == ".5"

def test_color_literal():
    tokens = Lexer('x = #FF00FF\n').tokenize()
    color = [t for t in tokens if t.type == TokenType.COLOR][0]
    assert color.value == "#FF00FF"

def test_percent_equals():
    tokens = Lexer("x %= 2\n").tokenize()
    assert any(t.type == TokenType.PERCENT_EQUALS for t in tokens)

def test_paren_suppresses_newline():
    src = "f(a,\n    b)\nx = 1\n"
    tokens = Lexer(src).tokenize()
    types = [t.type for t in tokens]
    newline_count = types.count(TokenType.NEWLINE)
    assert newline_count == 2

def test_keywords():
    tokens = Lexer("if else for while var\n").tokenize()
    types = [t.type for t in tokens if t.type not in (TokenType.NEWLINE, TokenType.EOF_TOKEN)]
    assert types == [TokenType.IF, TokenType.ELSE, TokenType.FOR,
                     TokenType.WHILE, TokenType.VAR]

def test_end_col_on_tokens():
    tokens = Lexer("hello = 42\n").tokenize()
    ident = tokens[0]
    assert hasattr(ident, 'end_col')
    assert ident.end_col == ident.col + len("hello")

def test_import_keyword():
    tokens = Lexer("import foo\n").tokenize()
    assert tokens[0].type == TokenType.IMPORT
