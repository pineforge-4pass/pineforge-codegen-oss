"""Persistence and qualifiers occupy separate positions in the declaration AST."""

import pytest

from pineforge_codegen.ast_nodes import VarDecl
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser


@pytest.mark.parametrize("storage", ["var", "varip"])
@pytest.mark.parametrize(
    "qualifier,hint,initializer,expected_hint",
    [
        ("const", "string", '"x"', "string"),
        ("const", "float", "1.5", "float"),
        ("const", "int", "3", "int"),
        ("const", "bool", "true", "bool"),
        ("const", "color", "color.blue", "color"),
        ("simple", "int", "3", "int"),
        ("series", "float", "close", "float"),
        ("series", "float[]", "array.new_float(0)", "array<float>"),
        ("series", "array<float>", "array.new_float(0)", "array<float>"),
        ("series", "Sample", "Sample.new()", "Sample"),
        ("series", "lib.Sample", "lib.newSample()", "lib.Sample"),
    ],
)
def test_persistence_then_qualifier_keeps_the_declaration(
    storage, qualifier, hint, initializer, expected_hint,
):
    source = f"{storage} {qualifier} {hint} held = {initializer}\n"
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    assert len(program.body) == 1
    declaration = program.body[0]
    assert isinstance(declaration, VarDecl)
    assert declaration.name == "held"
    assert declaration.type_hint == expected_hint
    assert (declaration.is_var, declaration.is_varip) == (
        storage == "var", storage == "varip",
    )
    assert declaration.annotations["qualifier"] == qualifier
    assert (declaration.loc.line, declaration.loc.col) == (1, 1)
    ((name, location),) = declaration.annotations["declaration_names"]
    assert name == "held"
    assert (location.line, location.col) == (1, source.index("held") + 1)


@pytest.mark.parametrize("storage", ["var", "varip"])
@pytest.mark.parametrize("word", ["const", "simple", "series"])
@pytest.mark.parametrize("hint", ["", "int ", "Sample "])
def test_contextual_qualifier_words_in_the_name_slot_are_names(storage, word, hint):
    source = f"{storage} {hint}{word} = 3\n"
    declaration = Parser(Lexer(source).tokenize(), source=source).parse().body[0]
    assert isinstance(declaration, VarDecl)
    assert declaration.name == word
    assert declaration.type_hint == (hint.strip() or None)
    assert "qualifier" not in declaration.annotations


@pytest.mark.parametrize("storage", ["var", "varip"])
@pytest.mark.parametrize("word", ["const", "simple", "series"])
def test_contextual_qualifier_word_as_a_type_keeps_its_type(storage, word):
    source = f"{storage} {word} held = {word}.new()\n"
    declaration = Parser(Lexer(source).tokenize(), source=source).parse().body[0]
    assert declaration.type_hint == word
    assert declaration.name == "held"
    assert "qualifier" not in declaration.annotations


@pytest.mark.parametrize("storage", ["var", "varip"])
@pytest.mark.parametrize("header", ["if close > open", "make() =>"])
def test_local_persistent_qualified_declaration_keeps_storage(storage, header):
    source = f"{header}\n    {storage} const float held = 1.5\n"
    declaration = Parser(Lexer(source).tokenize(), source=source).parse().body[0].body[0]
    assert isinstance(declaration, VarDecl)
    assert declaration.annotations["qualifier"] == "const"
    assert (declaration.is_var, declaration.is_varip) == (
        storage == "var", storage == "varip",
    )
    assert (declaration.loc.line, declaration.loc.col) == (2, 5)


@pytest.mark.parametrize("storage", ["var", "varip"])
def test_library_persistent_const_keeps_constant_provenance(storage):
    source = f"{storage} const float held = 1.5\n"
    program = Parser(Lexer(source).tokenize(), source=source, library=True).parse()
    declaration = program.body[0]
    assert declaration.annotations["qualifier"] == "const"
    assert declaration.annotations["declared_const"] is True
