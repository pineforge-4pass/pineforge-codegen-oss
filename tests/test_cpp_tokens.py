"""Check the C++ region oracle against its line-ending and literal rules."""

import pytest

from tests._cpp_tokens import assert_inert, assert_only_in_strings, code_only, regions, string_values


@pytest.mark.parametrize("line_end", ["\r", "\n", "\r\n"])
def test_line_comment_ends_at_each_compiler_line_ending(line_end):
    cpp = "// before" + line_end + "PFTOKEN;"
    assert regions(cpp) == [("comment", 0, len("// before"), "// before")]
    assert "PFTOKEN" in code_only(cpp)
    with pytest.raises(AssertionError, match="reached the C\\+\\+ as code"):
        assert_inert(cpp, "PFTOKEN")


@pytest.mark.parametrize("line_end", ["\r", "\n", "\r\n"])
def test_raw_line_end_breaks_a_quoted_literal(line_end):
    with pytest.raises(AssertionError, match="raw line break"):
        assert_inert('"before' + line_end + 'PFTOKEN"', "PFTOKEN")


@pytest.mark.parametrize("literal, value", [
    ('"x\\0007"', "x\0" + "7"),
    ('"\\07"', "\a"),
    ('"\\1234"', "S4"),
    ('"\\0"', "\0"),
    ('"\\000\\000"', "\0\0"),
])
def test_octal_escapes_consume_at_most_three_digits(literal, value):
    assert string_values(literal) == [value]


@pytest.mark.parametrize("prefix", ["", "L", "u", "U", "u8"])
def test_prefixed_character_literal_is_not_a_string(prefix):
    cpp = prefix + "'\"'"
    assert regions(cpp) == [("char", 0, len(cpp), '"')]
    assert string_values(cpp) == []
    assert '"' not in code_only(cpp)


@pytest.mark.parametrize("prefix", ["", "L", "u", "U", "u8"])
def test_raw_string_literal_keeps_its_quotes_and_line_breaks(prefix):
    value = 'a"\r\nPFTOKEN /* text */ \\b'
    cpp = prefix + 'R"tag(' + value + ')tag"'
    assert string_values(cpp) == [value]
    assert_inert(cpp, "PFTOKEN")
    assert_only_in_strings(cpp, "PFTOKEN")


def test_numeric_digit_separator_is_not_a_character_literal():
    assert regions("123'456") == []


def test_unterminated_raw_string_is_broken():
    assert regions('R"tag(PFTOKEN') == [("broken", 0, len('R"tag(PFTOKEN'), "PFTOKEN")]


def test_string_only_assertion_requires_the_marker_to_reach_a_literal():
    with pytest.raises(AssertionError, match="not emitted"):
        assert_only_in_strings('"other"', "PFTOKEN")
