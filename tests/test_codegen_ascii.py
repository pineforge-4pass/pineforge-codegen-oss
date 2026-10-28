"""Unicode names and text retain their values in ASCII-only emitted C++."""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.errors import CompileError
from pineforge_codegen.pine_spelling import pine_string_literal
from tests._cpp_tokens import code_only, regions, string_values


HEADER = '//@version=6\nstrategy("ASCII output")\n'
TRADE = 'if close > open\n    strategy.entry("L", strategy.long)\n'

NAMES = (
    # U+03B1 is UTF-8 CE B1.
    ("\u03b1", "pf_utf8_ceb1", b"\xce\xb1"),
    # Cyrillic U+0441 followed by "lass" is D1 81 6C 61 73 73.
    ("\u0441lass", "pf_utf8_d1816c617373", b"\xd1\x81lass"),
    # Deseret U+10400 is UTF-8 F0 90 90 80.
    ("\U00010400", "pf_utf8_f0909080", b"\xf0\x90\x90\x80"),
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


def _literal(cpp: str, spelling: str) -> tuple[str, str]:
    expected = '"' + spelling + '"'
    matches = [(cpp[start:end], value) for kind, start, end, value in regions(cpp)
               if kind == "string" and cpp[start:end] == expected]
    assert matches, expected
    return matches[0]


@pytest.mark.parametrize("name,emitted,expected_bytes", NAMES)
def test_unicode_identifier_has_an_invertible_ascii_spelling(name, emitted, expected_bytes):
    cpp = transpile(HEADER + f"{name} = close\nif {name} > open\n"
                    '    strategy.entry("L", strategy.long)\n')
    assert cpp.isascii()
    code = code_only(cpp)
    tokens = re.findall(rf"\b{emitted}\b", code)
    assert len(tokens) >= 2
    decoded = bytes.fromhex(tokens[0].removeprefix("pf_utf8_"))
    assert decoded == expected_bytes
    assert decoded.decode("utf-8") == name


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


def test_distinct_unicode_names_avoid_authored_ascii_encodings():
    source = (HEADER
              + "\u03b1 = close\n\u03b2 = open\n\u0430 = high\n"
              "pf_utf8_ceb1 = low\npf_utf8_ceb1_2 = hl2\npf_utf8_ceb2 = hlc3\n"
              "a = ohlc4\n"
              "plot(\u03b1 + \u03b2 + \u0430 + a + pf_utf8_ceb1"
              " + pf_utf8_ceb1_2 + pf_utf8_ceb2)\n")
    cpp = transpile(source)
    assert cpp.isascii()
    code = code_only(cpp)
    # CE B1 needs suffix 3, CE B2 needs suffix 2, and Cyrillic D0 B0 is distinct.
    expected = {
        "pf_utf8_ceb1_3", "pf_utf8_ceb2_2", "pf_utf8_d0b0",
        "pf_utf8_ceb1", "pf_utf8_ceb1_2", "pf_utf8_ceb2", "a",
    }
    declarations = re.findall(r"\bdouble ([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|;)", code)
    assert expected <= set(declarations)
    for name in expected:
        assert declarations.count(name) == 1
    assert transpile(source) == cpp


def test_unicode_encoding_avoids_reserved_names_and_unused_authored_names(monkeypatch):
    source = HEADER + "\u03b1 = close\nplot(\u03b1)\n"
    assert transpile(source).isascii()
    from pineforge_codegen.codegen import ascii as ascii_emission

    # CE B1's base, suffix 2 and suffix 3 are occupied; suffix 4 is available.
    monkeypatch.setattr(ascii_emission, "CPP_RESERVED",
                        ascii_emission.CPP_RESERVED | {"pf_utf8_ceb1_3"})
    original = CodeGen._generate_cpp

    def reserve_unused(self):
        self._safe_name_occupied.update({"pf_utf8_ceb1", "pf_utf8_ceb1_2"})
        return original(self)

    monkeypatch.setattr(CodeGen, "_generate_cpp", reserve_unused)
    cpp = transpile(source)
    assert cpp.isascii()
    assert "double pf_utf8_ceb1_4" in code_only(cpp)


def test_unicode_callable_history_names_keep_their_distinct_storage():
    cpp = transpile(HEADER
                    + "\u03b1(float \u03b2) =>\n"
                    "    var float \u03b3 = \u03b2\n"
                    "    \u03b3 := nz(\u03b3[1], \u03b2) + 1.0\n"
                    "    \u03b3\n"
                    "left = \u03b1(close)\nright = \u03b1(open)\nplot(left + right)\n")
    assert cpp.isascii()
    code = code_only(cpp)
    # The first call keeps gamma; the second uses gamma_cs1. Both functions are cloned.
    assert "Series<double> pf_utf8_ceb3" in code
    assert "Series<double> pf_utf8_ceb35f637331" in code
    assert "pf_utf8_ceb15f637330(" in code
    assert "pf_utf8_ceb15f637331(" in code


def test_a_unicode_name_in_a_string_keeps_its_value():
    cpp = transpile(HEADER
                    + '\u03b1 = close\ns = "\u03b1"\n'
                    "if \u03b1 > open and str.length(s) > 0\n"
                    '    strategy.entry("L", strategy.long)\n')
    assert cpp.isascii()
    # The identifier uses hex CE B1; its text value uses octal 316 261.
    assert "double pf_utf8_ceb1" in code_only(cpp)
    assert _literal(cpp, r"\316\261")[1] == "\u03b1"
    assert _literal(cpp, r"\316\261")[1].encode("utf-8") == b"\xce\xb1"


def test_unicode_type_and_field_names_are_encoded_as_complete_tokens():
    cpp = transpile(HEADER
                    + "type \u0394\n    float \u03b2\n"
                    "method \u03b3(\u0394 self) => self.\u03b2\n"
                    "point = \u0394.new(close)\nvalue = point.\u03b3()\nplot(value)\n")
    assert cpp.isascii()
    code = code_only(cpp)
    # Delta is CE 94, beta is CE B2, and the method token is "_udt_" + delta + "_" + gamma.
    assert "struct pf_utf8_ce94 {" in code
    assert "double pf_utf8_ceb2 =" in code
    assert "pf_utf8_5f7564745fce945fceb3(" in code


@pytest.mark.parametrize("name,emitted,expected_bytes", NAMES)
def test_encoded_identifier_declarations_and_uses_compile(name, emitted, expected_bytes):
    from tests._compile import compile_cpp

    cpp = transpile(HEADER + f"{name} = close\nif {name} > open\n"
                    '    strategy.entry("L", strategy.long)\n')
    assert cpp.isascii()
    assert emitted in code_only(cpp)
    compile_cpp(cpp, label="ASCII identifier")


def test_unicode_library_export_and_literal_are_encoded_after_inlining():
    source = (HEADER + "import pftest/Words/1 as W\n"
              "s = W.\u03b1()\nif str.length(s) > 0\n"
              '    strategy.entry("L", strategy.long)\n')
    library = ('//@version=6\nlibrary("Words \u00e9")\n'
               'export \u03b1() => "\u00e9"\n')
    cpp = transpile(source, libraries={"pftest/Words/1": library})
    assert cpp.isascii()
    # Words_v1__ followed by U+03B1 is 57 6F 72 64 73 5F 76 31 5F 5F CE B1.
    assert "pf_utf8_576f7264735f76315f5fceb1(" in code_only(cpp)
    assert _literal(cpp, r"\303\251")[1] == "\u00e9"


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
              '\u03b1 = close // x\u202e\n// caf\u00e9\n'
              's = """//\U0001f427\n e\u0301"""\n')
    # Identifier CE B1 = 2; strings C3 A9 + F0 9F 90 A7 + CC 81 = 8; comments = 3 + 2.
    assert scan_source(source) == {
        "non_ascii_utf8_bytes": 15,
        "identifier": {"lexemes": 1, "utf8_bytes": 2},
        "string_literal": {"lexemes": 2, "utf8_bytes": 8},
        "comment": {"lexemes": 2, "utf8_bytes": 5},
        "other_utf8_bytes": 0,
        "lexer_error": None,
    }
