"""Real Python/browser interfaces for the explicitly bounded NOTE migration."""

import json
from pathlib import Path

import pytest

from pineforge_codegen import diagnostics_catalog, transpile_full
from pineforge_codegen.errors import CompileError, Level


FIXTURES = Path(__file__).parent / "fixtures" / "diagnostic_notes"
CASES = json.loads((FIXTURES / "interfaces.json").read_text())["cases"]
DELTA = json.loads((FIXTURES / "delta.json").read_text())


def _glue():
    namespace = {}
    path = Path(__file__).resolve().parents[1] / "gate" / "glue.py"
    exec(compile(path.read_text(), str(path), "exec"), namespace)
    return namespace["transpile_json"]


def test_note_enum_and_catalog_short_sentences():
    assert "NOTE" in Level.__members__, "missing NOTE severity"
    assert Level.NOTE.value == "note"
    catalog = diagnostics_catalog()["codes"]
    assert catalog
    for code, entry in catalog.items():
        assert entry.get("user_message"), f"missing user_message: {code}"


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_real_interfaces_keep_notes_and_short_sentences(case):
    source = case["source"]
    try:
        diagnostics = transpile_full(source)["diagnostics"]
        ok = True
    except CompileError as exc:
        diagnostics, ok = exc.diagnostics, False
    envelope = json.loads(_glue()(source))
    assert ok == envelope["ok"] == case["ok"]
    assert len(diagnostics) == len(envelope["diagnostics"])
    for code, control in case["controls"].items():
        selected = [d for d in diagnostics if d.code == code]
        assert selected, f"real product control did not emit {code}"
        assert all(d.level.value == control["after"] for d in selected)
    if not ok:
        assert any(d.level == Level.ERROR for d in diagnostics)
    for diagnostic, entry in zip(diagnostics, envelope["diagnostics"]):
        assert entry["code"] == diagnostic.code
        assert entry["severity"] == diagnostic.level.value
        assert entry["args"] == diagnostic.args
        assert entry["user_message"] == diagnostic.user_message
        assert entry["message"] == (diagnostic.message + " — " + diagnostic.hint
                                    if diagnostic.hint else diagnostic.message)
