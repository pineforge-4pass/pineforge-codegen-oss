"""Real Python/browser interfaces for the explicitly bounded NOTE migration."""

import json
import copy
import hashlib
import re
from pathlib import Path

import pytest

from pineforge_codegen import diagnostics_catalog, transpile_full
from pineforge_codegen.errors import CompileError, Level
from pineforge_codegen.errors import Diagnostic, Phase, SourceLocation
from pineforge_codegen.diagnostic_codes import classify, parse_template, render, render_diagnostic


FIXTURES = Path(__file__).parent / "fixtures" / "diagnostic_notes"
CASES = json.loads((FIXTURES / "interfaces.json").read_text())["cases"]
DELTA = json.loads((FIXTURES / "delta.json").read_text())
NOTE_CODES = set(DELTA["severity_changes"])
BEFORE = json.loads((FIXTURES / "catalog.before.json").read_text())["codes"]


def _assert_catalog_delta(catalog):
    assert set(catalog) == set(BEFORE)
    changed = set()
    for code, old in BEFORE.items():
        entry = dict(catalog[code])
        template = entry.pop("user_message")
        assert isinstance(template, str) and template.strip() == template
        assert template.endswith(".") and "\n" not in template
        assert len(template) <= 180
        parts = parse_template(template)
        names = {p[0] for p in parts if isinstance(p, tuple)}
        assert names <= set(old["args"]), code
        assert any(isinstance(p, str) and p.strip(" .") for p in parts), code
        if entry["severity"] != old["severity"]:
            changed.add(code)
            expected = DELTA["severity_changes"][code]
            assert (old["severity"], entry["severity"]) == (expected["before"], expected["after"])
            assert template == expected["user_message"]
            entry["severity"] = old["severity"]
        assert entry == old, code
    assert changed == NOTE_CODES


def test_exact_catalog_delta_and_legacy_pin():
    catalog = diagnostics_catalog()["codes"]
    _assert_catalog_delta(catalog)
    legacy = json.loads((FIXTURES / "diagnostic_codes_pin.legacy.json").read_text())["codes"]
    current = json.loads((FIXTURES.parent / "diagnostic_codes_pin.json").read_text())["codes"]
    assert set(current) == set(legacy) == set(catalog)
    assert {code for code in legacy if legacy[code] != current[code]} == NOTE_CODES
    for code, entry in BEFORE.items():
        meaning = json.dumps([entry["severity"], entry["message"], entry.get("hint"),
                              sorted(entry["args"])], ensure_ascii=False)
        assert hashlib.sha256(meaning.encode()).hexdigest() == legacy[code]


@pytest.mark.parametrize("mutation", ["message", "hint", "explanation", "args", "severity", "user_message"])
def test_catalog_delta_rejects_malformed_candidate(mutation):
    bad = copy.deepcopy(diagnostics_catalog()["codes"])
    # A genuine warning outside the migration is the positive control's subject.
    entry = bad["PF-W1503"]
    entry[mutation] = ({"invented": {"kind": "text"}} if mutation == "args"
                       else "note" if mutation == "severity"
                       else "An invented {argument}." if mutation == "user_message"
                       else "changed")
    with pytest.raises((AssertionError, KeyError)):
        _assert_catalog_delta(bad)


@pytest.mark.parametrize("code", sorted(NOTE_CODES))
def test_stable_note_identity_uses_declared_severity(code):
    entry = diagnostics_catalog()["codes"][code]
    args = {name: (7 if spec["kind"] == "number" else
                   spec["values"][0] if spec["kind"] == "vocab" else
                   "台灣 <>& '\" {not_an_argument}") for name, spec in entry["args"].items()}
    message, hint = render_diagnostic(code, args)
    assert classify("note", message, hint) == (code, args)
    assert classify("warning", message, hint)[0] != code
    assert classify("error", message, hint)[0] != code
    d = Diagnostic(Level.NOTE, Phase.ANALYZER, SourceLocation("例.pine", 3, 2, 8), message, hint)
    assert d.code == code and d.args == args
    assert d.user_message == entry["user_message"]
    assert render(d.user_message, args)


def test_uncatalogued_note_retains_existing_nonfatal_identity():
    message, hint = "未知 '\" <x> {v}\nmessage", "hint 🐍"
    diagnostic = Diagnostic(Level.NOTE, Phase.ANALYZER, SourceLocation("x", 1, 1, 2), message, hint)
    assert diagnostic.code == "PF-W0000"
    assert diagnostic.level.value == "note"
    assert diagnostic.args == {"message": message, "hint": hint}
    entry = diagnostics_catalog()["codes"]["PF-W0000"]
    assert entry["severity"] == BEFORE["PF-W0000"]["severity"] == "warning"
    assert diagnostic.user_message == entry["user_message"]
    assert diagnostic.user_message != "{message}"
    namespace = {}
    path = Path(__file__).resolve().parents[1] / "gate" / "glue.py"
    exec(compile(path.read_text(), str(path), "exec"), namespace)
    wire = json.loads(json.dumps(namespace["_diagnostic_entries"]([diagnostic])))[0]
    assert wire["severity"] == "note" and wire["code"] == "PF-W0000"
    assert wire["message"] == message + " — " + hint
    assert wire["user_message"] == diagnostic.user_message


def test_receiver_compatibility_vectors():
    """Executable contract examples, not an app implementation or app E2E."""
    cases = json.loads((FIXTURES / "receiver.json").read_text())["cases"]
    for case in cases:
        severity = case["severity"] if case["severity"] in {"error", "warning", "note"} else "warning"
        assert severity == case["expected_severity"]
        assert render(case["user_message"], case["args"]) == case["expected_text"]


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
        assert entry["user_message"] == diagnostics_catalog()["codes"][diagnostic.code]["user_message"]
        assert entry["message"] == (diagnostic.message + " — " + diagnostic.hint
                                    if diagnostic.hint else diagnostic.message)
