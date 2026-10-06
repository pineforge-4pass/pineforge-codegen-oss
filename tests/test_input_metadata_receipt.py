"""Transpile-time settings agree with the native checked-settings receipt."""

import json
import runpy
from pathlib import Path

import pytest

from pineforge_codegen import transpile_full
from pineforge_codegen.errors import CompileError
from tests._compile import run_emitted_tu
from tests.test_compile_corpus import _resolve_corpus_root
from tests.test_string_settings_constants import RECEIPT_DRIVER


ROOT = Path(__file__).resolve().parents[1]
GLUE = runpy.run_path(str(ROOT / "gate" / "glue.py"))["transpile_json"]


def _sources():
    sources = [(path, False) for directory in (ROOT / "tests" / "fixtures",
                                              ROOT / "tests" / "gate-corpus")
               for path in sorted(directory.rglob("*.pine"))]
    corpus = _resolve_corpus_root()
    if corpus is not None:
        sources.extend((path, True) for path in sorted(corpus.rglob("strategy.pine")))
    return sources


def _default(entry, value):
    if entry["type"] == "bool":
        assert value in ("true", "false")
        return value == "true"
    if entry["type"] == "int":
        return int(value) if value != "na" else None
    if entry["type"] == "float":
        return float(value) if value != "na" else None
    if entry["type"] == "enum":
        choices = dict(zip(entry["options"], entry["option_values"]))
        return next((name for name, encoded in choices.items() if encoded == value), value)
    return value


@pytest.mark.parametrize("path,is_corpus", _sources(),
                         ids=lambda value: str(value) if isinstance(value, Path) else None)
def test_input_metadata_matches_receipt_for_every_source(path, is_corpus):
    source = path.read_text()
    try:
        full = transpile_full(source)
    except CompileError:
        assert not is_corpus, f"public corpus source refused: {path}"
        assert json.loads(GLUE(source))["ok"] is False
        return
    envelope = json.loads(GLUE(source))
    assert envelope["ok"] is True
    assert envelope["inputs"] == full["inputs"]
    if not full["inputs"]:
        assert "_pf_settings_inputs() const {\n        return {\n        };" in full["cpp"]
        return
    receipt = json.loads(run_emitted_tu(full["cpp"], RECEIPT_DRIVER, opt="-O0",
                                       label=str(path)))
    assert [entry["title"] for entry in full["inputs"]] == [
        entry["name"] for entry in receipt["inputs"]]
    for metadata, checked in zip(full["inputs"], receipt["inputs"]):
        key = metadata["title"]
        assert metadata["supported"] is checked["supported"], key
        assert metadata["default"] == _default(checked, checked["default"]), key
        assert metadata.get("options", []) == checked["options"], key


def test_public_corpus_input_metadata_inventory_is_available():
    corpus = _resolve_corpus_root()
    if corpus is None:
        pytest.skip("input receipt corpus pin requires PINEFORGE_ENGINE_CORPUS")
    assert list(corpus.rglob("strategy.pine"))


def test_unrepresentable_strings_match_receipt_in_both_envelopes():
    source = (ROOT / "tests" / "fixtures" / "input_metadata.pine").read_text()
    inputs = transpile_full(source)["inputs"]
    assert inputs == json.loads(GLUE(source))["inputs"]
    by_key = {entry["title"]: entry for entry in inputs}
    for key in ("Size", "Position"):
        assert by_key[key]["supported"] is False
        assert by_key[key]["default"] == ""
        assert by_key[key]["options"] == []
    assert by_key["Mixed"]["supported"] is False
    assert by_key["Mixed"]["default"] == "literal"
    assert by_key["Mixed"]["options"] == []
    assert all(entry["supported"] is True for entry in inputs
               if entry["title"] not in ("Size", "Position", "Mixed"))
