"""Library versions reach either the lexer refusal or the import diagnostic."""

import json

import pytest

from gate.glue import transpile_json
from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import ImportStmt
from pineforge_codegen.errors import CompileError, Level, Phase, SourceLocation
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser

PRELUDE = '//@version=6\nstrategy("version probe")\n'
VERSIONS = ["1.0", pytest.param("9" * 4301, id="over-limit")]
NON_ASCII_VERSIONS = [
    # TradingView measurement: ² follows the 16 ASCII characters in "import user/lib/".
    pytest.param("²", "no viable alternative at character '²'", 17, 18, id="superscript-two"),
    # The ASCII digit at column 17 puts ² at column 18.
    pytest.param("1²", "no viable alternative at character '²'", 18, 19,
                 id="ascii-one-superscript-two"),
    # Circled one follows the same 16 ASCII path characters.
    pytest.param("①", "no viable alternative at character '①'", 17, 18, id="circled-one"),
    # Arabic-Indic one follows the same 16 ASCII path characters.
    pytest.param("١", "no viable alternative at character '١'", 17, 18, id="arabic-indic-one"),
    # Fullwidth one follows the same 16 ASCII path characters.
    pytest.param("１", "no viable alternative at character '１'", 17, 18, id="fullwidth-one"),
    # Double-struck nine starts at 17; this lexer advances one Python code point.
    pytest.param("𝟡", "no viable alternative at character '𝟡'", 17, 18,
                 id="double-struck-nine"),
]


def _assert_lexer_refusal(error, message, col, end_col):
    (diagnostic,) = error.diagnostics
    assert diagnostic.code == "PF-E0044"
    assert diagnostic.message == message
    assert diagnostic.level == Level.ERROR
    assert diagnostic.phase == Phase.LEXER
    # PRELUDE contains exactly two lines; the import is on line 3.
    assert diagnostic.location == SourceLocation("versions.pine", 3, col, end_col)
    assert diagnostic.hint is None


@pytest.mark.parametrize("version,message,col,end_col", NON_ASCII_VERSIONS)
@pytest.mark.parametrize("suffix", ["", " as S", " as ta"])
def test_non_ascii_import_version_is_refused_before_parsing(version, message, col, end_col):
    source = PRELUDE + f"import user/lib/{version}{suffix}\n"
    with pytest.raises(CompileError) as error:
        Parser(Lexer(source, filename="versions.pine").tokenize(), source=source).parse()
    _assert_lexer_refusal(error.value, message, col, end_col)


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("suffix", ["", " as S", " as ta"])
def test_malformed_import_version_keeps_its_unparsed_spelling(version, suffix):
    source = PRELUDE + f"import user/lib/{version}{suffix}\n"
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    (node,) = [statement for statement in program.body if isinstance(statement, ImportStmt)]
    assert node.version is None
    assert node.user is None
    assert node.name is None
    assert version in node.path


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("suffix", ["", " as S", " as ta"])
def test_malformed_import_version_is_refused_with_the_existing_gate_diagnostic(version, suffix):
    control = json.loads(transpile_json(PRELUDE + "import user/lib/1.0\n"))
    result = json.loads(transpile_json(PRELUDE + f"import user/lib/{version}{suffix}\n"))
    assert result["ok"] is False
    (diagnostic,) = result["diagnostics"]
    assert diagnostic["code"] == control["diagnostics"][0]["code"]
    assert diagnostic["code"] == "PF-E2040"
    assert diagnostic["severity"] == "error"
    assert diagnostic["line"] == 3
    assert "Import is not supported" in diagnostic["message"]
    assert version in diagnostic["message"]


@pytest.mark.parametrize("version,message,col,end_col", NON_ASCII_VERSIONS)
@pytest.mark.parametrize("suffix", ["", " as S", " as ta"])
def test_non_ascii_import_version_is_refused_before_the_import_gate(
        version, message, col, end_col):
    source = PRELUDE + f"import user/lib/{version}{suffix}\n"
    with pytest.raises(CompileError) as error:
        transpile(source, filename="versions.pine")
    _assert_lexer_refusal(error.value, message, col, end_col)

    result = json.loads(transpile_json(source))
    assert result["ok"] is False
    (diagnostic,) = result["diagnostics"]
    assert diagnostic["code"] == "PF-E0044"
    assert diagnostic["message"] == message
    assert diagnostic["severity"] == "error"
    # The JSON protocol retains the source position of the line 3 import.
    assert diagnostic["line"] == 3
    assert diagnostic["col"] == col
    assert diagnostic["endCol"] == end_col
