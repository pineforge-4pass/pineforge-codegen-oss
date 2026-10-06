"""Malformed library versions use the existing import diagnostic at the gate."""

import json

import pytest

from gate.glue import transpile_json
from pineforge_codegen.ast_nodes import ImportStmt
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser

PRELUDE = '//@version=6\nstrategy("version probe")\n'
VERSIONS = ["²", "1²", "①", "١", "１", "𝟡"]


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("suffix", ["", " as S", " as ta"])
def test_non_ascii_import_version_keeps_its_unparsed_spelling(version, suffix):
    source = PRELUDE + f"import user/lib/{version}{suffix}\n"
    program = Parser(Lexer(source).tokenize(), source=source).parse()
    (node,) = [statement for statement in program.body if isinstance(statement, ImportStmt)]
    assert node.version is None
    assert node.user is None
    assert node.name is None
    assert version in node.path


@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("suffix", ["", " as S", " as ta"])
def test_non_ascii_import_version_is_refused_with_the_existing_gate_diagnostic(version, suffix):
    control = json.loads(transpile_json(PRELUDE + "import user/lib/1.0\n"))
    result = json.loads(transpile_json(PRELUDE + f"import user/lib/{version}{suffix}\n"))
    assert result["ok"] is False
    (diagnostic,) = result["diagnostics"]
    assert diagnostic["code"] == control["diagnostics"][0]["code"]
    assert diagnostic["severity"] == "error"
    assert diagnostic["line"] == 3
    assert "Import is not supported" in diagnostic["message"]
    assert version in diagnostic["message"]
