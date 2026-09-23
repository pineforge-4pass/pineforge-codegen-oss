"""String-literal handling of the ctor-arg spelling helpers (#132).

An inline input's title travels inside the Pine spelling of a TA
constructor argument; these pin that its text never reads as code.
"""

from __future__ import annotations

import pytest

from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from pineforge_codegen.pine_spelling import (
    input_call_spans,
    pine_string_literal,
    spell_input_call,
    sub_identifiers,
)


def _parse(text: str):
    return Parser(Lexer(text).tokenize(), source=text)._parse_expression()


@pytest.mark.parametrize("value", ['plain', 'say "hi"', 'back\\slash', "it's", 'a(b, c)'])
def test_string_literal_reads_back_as_its_value(value: str) -> None:
    assert _parse(pine_string_literal(value)).value == value


def test_call_span_ignores_parens_and_commas_inside_strings() -> None:
    text = 'input.int(9, "Fast (EMA, bars)") * 2 + input(3)'
    spans = input_call_spans(text)
    assert [text[a:b] for a, b in spans] == ['input.int(9, "Fast (EMA, bars)")', 'input(3)']


def test_call_span_skips_strings_members_and_longer_names() -> None:
    text = '"input(1)" + x.input(2) + get_input_int(3) + input2(4)'
    assert input_call_spans(text) == []


def test_identifier_substitution_leaves_string_contents_alone() -> None:
    text = 'input.int(9, "len and m") + m'
    renamed = sub_identifiers(text, lambda m: "(2 * 3)" if m.group(0) == "m" else m.group(0))
    assert renamed == 'input.int(9, "len and m") + (2 * 3)'


@pytest.mark.parametrize("call", [
    'input.int(9, "fast")',
    'input.int(defval=9, title="fast")',
    'input.int(9, "fast", minval=-1, maxval=200, step=1, group="G", '
    'tooltip="t (x)", inline="i", confirm=false, display=display.none)',
    'input.int(9, "fast", options=[5, 9, 21])',
    'input.float(0.5, "say \\"hi\\"")',
    'input(9)',
])
def test_input_call_spelling_reparses_to_the_same_call(call: str) -> None:
    node = _parse(call)
    assert _parse(spell_input_call(node)) == node
