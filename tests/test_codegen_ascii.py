"""Pine names are ASCII; Unicode strings and comments retain their bytes."""

from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.codegen.ascii import ascii_cpp
from pineforge_codegen.errors import CompileError, Level, Phase, SourceLocation
from pineforge_codegen.pine_spelling import pine_string_literal
from tests._cpp_tokens import code_only, regions, string_values


HEADER = '//@version=6\nstrategy("ASCII output")\n'
TRADE = 'if close > open\n    strategy.entry("L", strategy.long)\n'

IDENTIFIER_REFUSALS = (
    pytest.param("\u03b1", [
        # The two header lines put alpha at 3:1, with its one-character end at 2.
        ("no viable alternative at character '\u03b1'", 3, 1, 2),
        # "if " is three characters, putting alpha at 4:4 and its end at 5.
        ("no viable alternative at character '\u03b1'", 4, 4, 5),
    ], id="greek-alpha"),
    pytest.param("\u0441lass", [
        # Cyrillic es starts line 3 at column 1; the remaining "lass" is ASCII.
        ("no viable alternative at character '\u0441'", 3, 1, 2),
        # "if " puts Cyrillic es at 4:4; its one-character end is 5.
        ("no viable alternative at character '\u0441'", 4, 4, 5),
    ], id="cyrillic-es"),
    pytest.param("\U00010400", [
        # Deseret starts line 3 at column 1 and occupies one Python code point.
        ("no viable alternative at character '\U00010400'", 3, 1, 2),
        # "if " puts Deseret at 4:4; one Python code point ends at 5.
        ("no viable alternative at character '\U00010400'", 4, 4, 5),
    ], id="deseret"),
)

STRINGS = (
    # U+00E9 is C3 A9: two bytes plus the C++ terminator.
    ("\u00e9", r"\303\251", b"\xc3\xa9", 3),
    # "e" followed by U+0301 is 65 CC 81, without normalization.
    ("e\u0301", r"e\314\201", b"e\xcc\x81", 4),
    # U+202E between ASCII letters is 78 E2 80 AE 79.
    ("x\u202ey", r"x\342\200\256y", b"x\xe2\x80\xaey", 6),
    # U+00E9, NUL, "7" is C3 A9 00 37; the octal escape stops at three digits.
    ("\u00e9\0" + "7", r"\303\251\0007", b"\xc3\xa9\x007", 5),
    # U+1F427 is F0 9F 90 A7.
    ("\U0001f427", r"\360\237\220\247", b"\xf0\x9f\x90\xa7", 5),
    # Decimal digits following U+00E9 stay separate from both octal escapes.
    ("\u00e9" + "123", r"\303\251123", b"\xc3\xa9123", 6),
    # A literal backslash and "u202E" stay ordinary data before U+00E9.
    ("\\u202E\u00e9", r"\\u202E\303\251", b"\\u202E\xc3\xa9", 9),
)


@pytest.mark.parametrize("cpp,expected", (
    # Escaping the first-line comment leaves U+2603 before U+202E on line 3.
    ("// \u00e9\nvoid f() {\n  \u2603 \u202e;\n}\n",
     "non-ASCII C++ code point U+2603 at line 3"),
    # U+200B is a zero-width space outside any token, after one newline.
    ("int value = 0;\n\u200b\n",
     "non-ASCII C++ code point U+200B at line 2"),
    # U+202E is a directional override outside any token on line 2.
    ("void f() {\n  \u202e\n}\n",
     "non-ASCII C++ code point U+202E at line 2"),
    # U+00A0 between the type and name is a non-ASCII space on line 1.
    ("int\u00a0value = 1;\n",
     "non-ASCII C++ code point U+00A0 at line 1"),
))
def test_unhandled_non_ascii_code_is_an_emitter_invariant_error(cpp, expected):
    with pytest.raises(AssertionError) as error:
        ascii_cpp(cpp)
    assert str(error.value) == expected


@pytest.mark.parametrize("literal", (
    # U+00E9 is one wide code unit, not the two UTF-8 bytes C3 A9.
    'L"\u00e9"',
    # UTF-16 must retain U+00E9 as one code unit.
    'u"\u00e9"',
    # UTF-32 must retain U+00E9 as one code unit.
    'U"\u00e9"',
    # UTF-8 prefixed strings are outside the ordinary-string emitter contract.
    'u8"\u00e9"',
    # A raw string would read backslash-octal text as ordinary characters.
    'R"(\u00e9)"',
    # A custom raw delimiter does not change the literal's byte contract.
    'R"tag(\u00e9)tag"',
    # Wide raw strings have both the wide and raw-literal restrictions.
    'LR"(\u00e9)"',
    # UTF-16 raw strings also bypass escape decoding.
    'uR"(\u00e9)"',
    # UTF-32 raw strings also bypass escape decoding.
    'UR"(\u00e9)"',
    # A UTF-8 raw prefix must be recognized as a whole token.
    'u8R"(\u00e9)"',
    # Wide character literals cannot be rewritten as ordinary UTF-8 bytes.
    "L'\u00e9'",
    # UTF-8 character literals are also outside the emitter contract.
    "u8'\u00e9'",
))
def test_prefixed_literals_are_an_emitter_invariant_error(literal):
    with pytest.raises(AssertionError) as error:
        ascii_cpp("int value = 0;\n" + literal + ";\n")
    # The prefix follows exactly one newline, so only its line number is reported.
    assert str(error.value) == "unexpected prefixed C++ literal at line 2"


@pytest.mark.parametrize("literal", (
    # U+00E9 is C3 A9; two octal escapes would make a multicharacter constant.
    "'\u00e9'",
    # U+0301 is CC 81; even an ASCII base cannot make this a single-byte character.
    "'e\u0301'",
))
def test_non_ascii_character_literals_are_an_emitter_invariant_error(literal):
    with pytest.raises(AssertionError) as error:
        ascii_cpp("int value = 0;\nint letter = " + literal + ";\n")
    # The character literal starts on line 2, and its authored text stays private.
    assert str(error.value) == "non-ASCII C++ character literal at line 2"


@pytest.mark.parametrize("cpp,expected", (
    # The separator belongs to 1000; alpha (CE B1) remains a code identifier.
    ("constexpr int total = 1'000;\nint \u03b1 = total;\nchar mark = 'x';\n",
     "constexpr int total = 1'000;\nint pf_utf8_ceb1 = total;\nchar mark = 'x';\n"),
    # 0xA'B is 171; alpha (CE B1) and the escaped quote stay separate tokens.
    ("constexpr int total = 0xA'B;\nint \u03b1 = total;\nchar mark = '\\'';\n",
     "constexpr int total = 0xA'B;\nint pf_utf8_ceb1 = total;\nchar mark = '\\'';\n"),
))
def test_digit_separators_do_not_hide_code_between_character_literals(cpp, expected):
    assert ascii_cpp(cpp) == expected


_EMITTER_LITERAL_TOKENS = re.compile(
    r"(?P<comment>//[^\r\n]*|/\*.*?(?:\*/|\Z))"
    r"|(?P<number>(?:[0-9]|\.[0-9])(?:[eEpP][+-]|[\w.]|'[\w])*)"
    r"""|(?P<prefix>\b(?:u8|[LuU])["']|\b(?:u8|[LuU])?R")"""
    r"""|(?P<quoted>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')"""
    r"|(?P<type>\b(?:wchar_t|char16_t|char32_t)\b)",
    re.DOTALL,
)


def _emitter_literal_hits(
        source: str, *, literal_grammars: frozenset[str] = frozenset()
) -> set[tuple[str, str]]:
    """Inspect string templates, excluding documentation and regex grammars."""
    tree = ast.parse(source)
    excluded = set()
    keyword_table = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            excluded.add(node.value)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "re" and node.func.attr == "compile"):
            excluded.update(ast.walk(node))
        if isinstance(node, ast.JoinedStr):
            excluded.update(part for part in node.values if isinstance(part, ast.Constant))
        if isinstance(node, ast.Assign):
            targets = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if targets & literal_grammars:
                excluded.update(ast.walk(node.value))
            if "CPP_KEYWORDS" in targets:
                keyword_table.update(ast.walk(node.value))

    hits = set()
    for node in ast.walk(tree):
        if node in excluded:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(part.value if isinstance(part, ast.Constant) else "pf_value"
                           for part in node.values)
        else:
            continue
        owner = "CPP_KEYWORDS" if node in keyword_table else ""
        hits.update((owner, match.group()) for match in _EMITTER_LITERAL_TOKENS.finditer(text)
                    if match.lastgroup in {"prefix", "type"})
    return hits


def test_emitter_templates_use_only_ordinary_byte_literals():
    package = Path(__file__).resolve().parents[1] / "pineforge_codegen"
    # These checker tables recognize tokens; their strings are never emitted.
    literal_grammars = {
        "cpp_allowlist.py": frozenset({"_RAW_PREFIXES", "_QUOTED_PREFIXES"}),
    }
    hits = {
        (path.relative_to(package).as_posix(), owner, token)
        for path in sorted(package.rglob("*.py"))
        for owner, token in _emitter_literal_hits(
            path.read_text(encoding="utf-8"),
            literal_grammars=literal_grammars.get(path.relative_to(package).as_posix(),
                                                 frozenset()),
        )
    }
    # Only these three reserved keywords occur; no literal prefix is permitted.
    assert hits == {
        ("codegen/helpers.py", "CPP_KEYWORDS", "wchar_t"),
        ("codegen/helpers.py", "CPP_KEYWORDS", "char16_t"),
        ("codegen/helpers.py", "CPP_KEYWORDS", "char32_t"),
    }


def test_emitter_literal_scan_detects_prefixes_and_wide_types():
    source = (
        "def emit(name):\n"
        "    return (f'L\"{name}\"', 'u\"text\"', 'U\"text\"', 'u8\"text\"',\n"
        "            'R\"(text)\"',\n"
        "            \"int n = 1'000; auto text = u8R\\\"(x)\\\"; char c = 'x';\",\n"
        "            'wchar_t a; char16_t b; char32_t c;')\n"
    )
    # Six prefixes, including the one after a digit separator, and three types are code.
    assert _emitter_literal_hits(source) == {
        ("", 'L"'), ("", 'u"'), ("", 'U"'), ("", 'u8"'), ("", 'R"'),
        ("", 'u8R"'),
        ("", "wchar_t"), ("", "char16_t"), ("", "char32_t"),
    }


def _literal(cpp: str, spelling: str) -> tuple[str, str]:
    expected = '"' + spelling + '"'
    matches = [(cpp[start:end], value) for kind, start, end, value in regions(cpp)
               if kind == "string" and cpp[start:end] == expected]
    assert matches, expected
    return matches[0]


def _assert_identifier_refusal(
        source, expected, *, libraries=None, diagnostic_filename="ascii_names.pine"):
    with pytest.raises(CompileError) as error:
        transpile(source, filename="ascii_names.pine", libraries=libraries)
    assert len(error.value.diagnostics) == len(expected)
    for diagnostic, (message, line, col, end_col) in zip(error.value.diagnostics, expected):
        assert diagnostic.code == "PF-E0044"
        assert diagnostic.message == message
        assert diagnostic.level == Level.ERROR
        assert diagnostic.phase == Phase.LEXER
        assert diagnostic.location == SourceLocation(diagnostic_filename, line, col, end_col)
        assert diagnostic.hint is None


@pytest.mark.parametrize("name,expected", IDENTIFIER_REFUSALS)
def test_non_ascii_identifier_declarations_and_reads_are_refused(name, expected):
    source = (HEADER + f"{name} = close\nif {name} > open\n"
              '    strategy.entry("L", strategy.long)\n')
    _assert_identifier_refusal(source, expected)


@pytest.mark.parametrize("text,spelling,expected_bytes,array_size", STRINGS)
def test_unicode_string_preserves_its_compiled_bytes(text, spelling, expected_bytes, array_size):
    cpp = transpile(HEADER + f"s = {pine_string_literal(text)}\n"
                    "if str.length(s) > 0\n"
                    '    strategy.entry("L", strategy.long)\n')
    assert cpp.isascii()
    _token, decoded = _literal(cpp, spelling)
    assert decoded == text
    assert decoded.encode("utf-8") == expected_bytes


@pytest.mark.parametrize("text,spelling,expected_bytes,array_size", STRINGS)
def test_unicode_literal_compiler_decodes_the_authored_bytes(
        text, spelling, expected_bytes, array_size):
    from tests._compile import compile_cpp

    cpp = transpile(HEADER + f"s = {pine_string_literal(text)}\n" + TRADE)
    assert cpp.isascii()
    token, decoded = _literal(cpp, spelling)
    assert decoded == text
    checks = [
        f"constexpr char value[] = {token};",
        f"static_assert(sizeof(value) == {array_size});",
        *[f"static_assert(static_cast<unsigned char>(value[{index}]) == {byte});"
          for index, byte in enumerate(expected_bytes)],
    ]
    compile_cpp("\n".join(checks), label="ASCII UTF-8 literal")


def test_non_ascii_names_are_refused_beside_encoding_looking_ascii_names():
    source = (HEADER
              + "\u03b1 = close\n\u03b2 = open\n\u0430 = high\n"
              "pf_utf8_ceb1 = low\npf_utf8_ceb1_2 = hl2\npf_utf8_ceb2 = hlc3\n"
              "a = ohlc4\n"
              "plot(\u03b1 + \u03b2 + \u0430 + a + pf_utf8_ceb1"
              " + pf_utf8_ceb1_2 + pf_utf8_ceb2)\n")
    _assert_identifier_refusal(source, [
        # Alpha starts the first declaration after the two header lines: 3:1.
        ("no viable alternative at character '\u03b1'", 3, 1, 2),
        # Beta starts the next declaration at 4:1.
        ("no viable alternative at character '\u03b2'", 4, 1, 2),
        # Cyrillic a starts the third declaration at 5:1.
        ("no viable alternative at character '\u0430'", 5, 1, 2),
        # Seven declarations put plot on line 10; "plot(" puts alpha at column 6.
        ("no viable alternative at character '\u03b1'", 10, 6, 7),
        # "plot(α + " is nine code points, putting beta at 10:10.
        ("no viable alternative at character '\u03b2'", 10, 10, 11),
        # "plot(α + β + " is thirteen code points, putting Cyrillic a at 10:14.
        ("no viable alternative at character '\u0430'", 10, 14, 15),
    ])


def test_ascii_names_that_resemble_encodings_keep_their_spelling():
    source = (HEADER
              + "first = close\nsecond = open\nthird = high\n"
              "pf_utf8_ceb1 = low\npf_utf8_ceb1_2 = hl2\npf_utf8_ceb2 = hlc3\n"
              "a = ohlc4\n"
              "plot(first + second + third + a + pf_utf8_ceb1"
              " + pf_utf8_ceb1_2 + pf_utf8_ceb2)\n")
    cpp = transpile(source)
    assert cpp.isascii()
    # All seven authored ASCII names retain their own declaration and spelling.
    expected = {
        "first", "second", "third", "pf_utf8_ceb1", "pf_utf8_ceb1_2", "pf_utf8_ceb2", "a",
    }
    declarations = re.findall(r"\bdouble ([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|;)", code_only(cpp))
    assert expected <= set(declarations)
    for name in expected:
        assert declarations.count(name) == 1
    assert transpile(source) == cpp


def test_identifier_encoder_avoids_reserved_and_unused_names(monkeypatch):
    from pineforge_codegen.codegen import ascii as ascii_emission

    monkeypatch.setattr(ascii_emission, "CPP_RESERVED",
                        ascii_emission.CPP_RESERVED | {"pf_utf8_ceb1_3"})
    cpp = ascii_cpp("double \u03b1 = 0;\n",
                    reserved={"pf_utf8_ceb1", "pf_utf8_ceb1_2"})
    # CE B1's base, suffix 2 and suffix 3 are occupied, so suffix 4 is first free.
    assert cpp == "double pf_utf8_ceb1_4 = 0;\n"


def test_ascii_reserved_identifier_keeps_its_existing_escape():
    cpp = transpile(HEADER + "class = close\nplot(class)\n")
    assert cpp.isascii()
    # The existing C++ keyword escape wraps class in one underscore on each side.
    assert "double _class_" in code_only(cpp)


def test_non_ascii_name_is_refused_before_reserved_name_encoding():
    source = HEADER + "\u03b1 = close\nplot(\u03b1)\n"
    _assert_identifier_refusal(source, [
        # Two header lines precede the declaration's alpha at 3:1.
        ("no viable alternative at character '\u03b1'", 3, 1, 2),
        # "plot(" is five characters, putting the second alpha at 4:6.
        ("no viable alternative at character '\u03b1'", 4, 6, 7),
    ])


def test_non_ascii_callable_history_names_are_refused():
    source = (HEADER
              + "\u03b1(float \u03b2) =>\n"
              "    var float \u03b3 = \u03b2\n"
              "    \u03b3 := nz(\u03b3[1], \u03b2) + 1.0\n"
              "    \u03b3\n"
              "left = \u03b1(close)\nright = \u03b1(open)\nplot(left + right)\n")
    _assert_identifier_refusal(source, [
        # Two header lines put the function's alpha at 3:1.
        ("no viable alternative at character '\u03b1'", 3, 1, 2),
        # "α(float " is eight code points, putting the parameter beta at 3:9.
        ("no viable alternative at character '\u03b2'", 3, 9, 10),
        # Four spaces and "var float " are fourteen characters before gamma.
        ("no viable alternative at character '\u03b3'", 4, 15, 16),
        # "    var float γ = " is eighteen code points before beta at 4:19.
        ("no viable alternative at character '\u03b2'", 4, 19, 20),
        # The assignment's four-space indent puts gamma at 5:5.
        ("no viable alternative at character '\u03b3'", 5, 5, 6),
        # "    γ := nz(" is twelve code points before the history gamma.
        ("no viable alternative at character '\u03b3'", 5, 13, 14),
        # "    γ := nz(γ[1], " is eighteen code points before beta at 5:19.
        ("no viable alternative at character '\u03b2'", 5, 19, 20),
        # The return's four-space indent puts gamma at 6:5.
        ("no viable alternative at character '\u03b3'", 6, 5, 6),
        # "left = " is seven characters before the call's alpha at 7:8.
        ("no viable alternative at character '\u03b1'", 7, 8, 9),
        # "right = " is eight characters before the call's alpha at 8:9.
        ("no viable alternative at character '\u03b1'", 8, 9, 10),
    ])


def test_ascii_callable_history_names_keep_their_distinct_storage():
    cpp = transpile(HEADER
                    + "history_calc(float price) =>\n"
                    "    var float memory_value = price\n"
                    "    memory_value := nz(memory_value[1], price) + 1.0\n"
                    "    memory_value\n"
                    "left = history_calc(close)\nright = history_calc(open)\nplot(left + right)\n")
    assert cpp.isascii()
    code = code_only(cpp)
    # The two call sites retain memory_value and memory_value_cs1 in separate clones.
    assert "Series<double> memory_value" in code
    assert "Series<double> memory_value_cs1" in code
    assert "history_calc_cs0(" in code
    assert "history_calc_cs1(" in code


def test_a_unicode_name_in_a_string_keeps_its_value():
    cpp = transpile(HEADER
                    + 'price = close\ns = "\u03b1"\n'
                    "if price > open and str.length(s) > 0\n"
                    '    strategy.entry("L", strategy.long)\n')
    assert cpp.isascii()
    # The ASCII identifier stays price; alpha's UTF-8 bytes are octal 316 261.
    assert "double price" in code_only(cpp)
    assert _literal(cpp, r"\316\261")[1] == "\u03b1"
    assert _literal(cpp, r"\316\261")[1].encode("utf-8") == b"\xce\xb1"


def test_non_ascii_identifier_is_refused_beside_its_spelling_in_a_string():
    source = (HEADER
              + '\u03b1 = close\ns = "\u03b1"\n'
              "if \u03b1 > open and str.length(s) > 0\n"
              '    strategy.entry("L", strategy.long)\n')
    _assert_identifier_refusal(source, [
        # Two header lines put the declaration's alpha at 3:1.
        ("no viable alternative at character '\u03b1'", 3, 1, 2),
        # Line 4's quoted alpha is data; "if " puts the next identifier at 5:4.
        ("no viable alternative at character '\u03b1'", 5, 4, 5),
    ])


def test_non_ascii_type_field_and_method_names_are_refused():
    source = (HEADER
              + "type \u0394\n    float \u03b2\n"
              "method \u03b3(\u0394 self) => self.\u03b2\n"
              "point = \u0394.new(close)\nvalue = point.\u03b3()\nplot(value)\n")
    _assert_identifier_refusal(source, [
        # Two header lines and "type " put delta at 3:6.
        ("no viable alternative at character '\u0394'", 3, 6, 7),
        # Four spaces and "float " are ten characters before beta at 4:11.
        ("no viable alternative at character '\u03b2'", 4, 11, 12),
        # "method " is seven characters before gamma at 5:8.
        ("no viable alternative at character '\u03b3'", 5, 8, 9),
        # "method γ(" is nine code points before the receiver delta at 5:10.
        ("no viable alternative at character '\u0394'", 5, 10, 11),
        # "method γ(Δ self) => self." is twenty-five code points before beta.
        ("no viable alternative at character '\u03b2'", 5, 26, 27),
        # "point = " is eight characters before the constructor delta at 6:9.
        ("no viable alternative at character '\u0394'", 6, 9, 10),
        # "value = point." is fourteen characters before the method gamma.
        ("no viable alternative at character '\u03b3'", 7, 15, 16),
    ])


def test_ascii_type_field_and_method_names_keep_their_spelling():
    cpp = transpile(HEADER
                    + "type PointValue\n    float field_value\n"
                    "method read_value(PointValue self) => self.field_value\n"
                    "point = PointValue.new(close)\nvalue = point.read_value()\nplot(value)\n")
    assert cpp.isascii()
    code = code_only(cpp)
    # The type, field and method retain their complete authored ASCII components.
    assert "struct PointValue {" in code
    assert "double field_value =" in code
    assert "_udt_PointValue_read_value(" in code


@pytest.mark.parametrize("name,expected", IDENTIFIER_REFUSALS)
def test_non_ascii_identifier_sources_are_refused_before_cpp_compilation(name, expected):
    source = (HEADER + f"{name} = close\nif {name} > open\n"
              '    strategy.entry("L", strategy.long)\n')
    _assert_identifier_refusal(source, expected)


def test_non_ascii_library_export_call_is_refused_before_inlining():
    source = (HEADER + "import pftest/Words/1 as W\n"
              "s = W.\u03b1()\nif str.length(s) > 0\n"
              '    strategy.entry("L", strategy.long)\n')
    library = ('//@version=6\nlibrary("Words \u00e9")\n'
               'export \u03b1() => "\u00e9"\n')
    # TradingView measurement: α is refused; "s = W." puts it at line 4, column 7.
    _assert_identifier_refusal(source, [
        ("no viable alternative at character '\u03b1'", 4, 7, 8),
    ], libraries={"pftest/Words/1": library})


def test_ascii_library_export_keeps_its_unicode_literal_after_inlining():
    source = (HEADER + "import pftest/Words/1 as W\n"
              "s = W.word()\nif str.length(s) > 0\n"
              '    strategy.entry("L", strategy.long)\n')
    library = ('//@version=6\nlibrary("Words \u00e9")\n'
               'export word() => "\u00e9"\n')
    cpp = transpile(source, libraries={"pftest/Words/1": library})
    assert cpp.isascii()
    # The module's path supplies Words_v1__; the export's ASCII name stays word.
    assert "Words_v1__word(" in code_only(cpp)
    assert _literal(cpp, r"\303\251")[1] == "\u00e9"


def test_non_ascii_library_export_definition_is_refused():
    source = (HEADER + "import pftest/Words/1 as W\n"
              "s = W.word()\n" + TRADE)
    library = ('//@version=6\nlibrary("Words \u00e9")\n'
               'export \u03b1() => "\u00e9"\n')
    # TradingView measurement: α follows "export " on the library's third line.
    _assert_identifier_refusal(source, [
        ("no viable alternative at character '\u03b1'", 3, 8, 9),
    ], libraries={"pftest/Words/1": library}, diagnostic_filename="pftest/Words/1")


def test_bidi_text_in_an_emitted_comment_retains_its_byte_spelling():
    cpp = transpile(HEADER
                    + "type O\n    table t\n    float v\n"
                    "f(string s) => O.new(na, str.length(s))\n"
                    "// x\u202ey\n"
                    'f("x\u202ey").t := na\n' + TRADE)
    assert cpp.isascii()
    comments = [text for kind, _start, _end, text in regions(cpp)
                if kind == "comment" and "drawing field assignment omitted" in text]
    # U+202E is UTF-8 E2 80 AE, spelled as octal 342 200 256 inside the comment.
    assert comments == [
        r'/* drawing field assignment omitted: f(std::string("x\342\200\256y")).t := ... */'
    ]
    assert string_values(comments[0][2:-2]) == ["x\u202ey"]
    assert string_values(comments[0][2:-2])[0].encode("utf-8") == b"x\xe2\x80\xaey"


def test_unicode_input_metadata_retains_the_authored_default_title_and_choices():
    result = transpile_full(HEADER
                            + 's = input.string("\u00e9", "e\u0301",'
                            ' options=["\u00e9", "x\u202ey"])\n' + TRADE)
    assert result["cpp"].isascii()
    (setting,) = result["inputs"]
    # C3 A9, 65 CC 81 and 78 E2 80 AE 79 denote three distinct authored strings.
    assert setting["default"] == "\u00e9"
    assert setting["title"] == "e\u0301"
    assert setting["options"] == ["\u00e9", "x\u202ey"]
    assert setting["supported"] is True
    assert _literal(result["cpp"], r"\303\251")[1] == "\u00e9"
    assert _literal(result["cpp"], r"e\314\201")[1] == "e\u0301"
    assert _literal(result["cpp"], r"x\342\200\256y")[1] == "x\u202ey"


def test_every_already_ascii_fixture_output_keeps_its_bytes(monkeypatch):
    """Compare the unchanged lowering with its final serialized output."""
    from pineforge_codegen.codegen import base

    fixture_root = Path(__file__).parent / "fixtures"
    library_root = fixture_root / "pine_libraries"
    libraries = {
        path.relative_to(library_root).with_suffix("").as_posix(): path.read_text(encoding="utf-8")
        for path in library_root.rglob("*.pine")
    }
    compared = []
    encoded = []
    refused = []
    for path in sorted(fixture_root.rglob("*.pine")):
        source = path.read_text(encoding="utf-8")
        expected_error = None
        with monkeypatch.context() as original:
            original.setattr(base, "ascii_cpp", lambda cpp, **_kwargs: cpp, raising=False)
            try:
                expected = transpile(source, filename=str(path), libraries=libraries)
            except CompileError as error:
                expected_error = str(error)
        if expected_error is not None:
            with pytest.raises(CompileError) as actual:
                transpile(source, filename=str(path), libraries=libraries)
            assert str(actual.value) == expected_error, path
            refused.append(path.relative_to(fixture_root).as_posix())
        else:
            cpp = transpile(source, filename=str(path), libraries=libraries)
            assert cpp.isascii(), path
            if source.isascii() or expected.isascii():
                assert cpp.encode("utf-8") == expected.encode("utf-8"), path
                compared.append(path.relative_to(fixture_root).as_posix())
            else:
                encoded.append(path.relative_to(fixture_root).as_posix())
    # This existing accepted fixture reaches numeric, collection and string emission.
    assert "tail_f_tv/int_product.pine" in compared
    print(f"ASCII fixture outputs compared: {len(compared)}; "
          f"Unicode outputs encoded: {len(encoded)}; "
          f"unchanged refusals: {len(refused)}")


def test_population_counter_uses_literal_and_comment_boundaries():
    from scripts.count_non_ascii_pine import scan_source

    source = ('//@version=6\nstrategy("\u00e9")\n'
              'price = close // x\u202e\n// caf\u00e9\n'
              's = """//\U0001f427\n e\u0301"""\n')
    # Strings contribute 2 + 4 + 2 = 8 bytes; comments contribute 3 + 2 = 5.
    assert scan_source(source) == {
        "non_ascii_utf8_bytes": 13,
        "identifier": {"lexemes": 0, "utf8_bytes": 0},
        "string_literal": {"lexemes": 2, "utf8_bytes": 8},
        "comment": {"lexemes": 2, "utf8_bytes": 5},
        "other_utf8_bytes": 0,
        "lexer_error": None,
    }


def test_non_ascii_identifier_is_refused_among_unicode_strings_and_comments():
    source = ('//@version=6\nstrategy("\u00e9")\n'
              '\u03b1 = close // x\u202e\n// caf\u00e9\n'
              's = """//\U0001f427\n e\u0301"""\n')
    # TradingView measurement: only the identifier α is refused, at line 3, column 1.
    _assert_identifier_refusal(source, [
        ("no viable alternative at character '\u03b1'", 3, 1, 2),
    ])
