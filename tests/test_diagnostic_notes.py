"""Real Python/browser interfaces for the explicitly bounded NOTE migration."""

import json
import copy
import hashlib
import re
from dataclasses import asdict, replace
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
PRODUCT_BEFORE = json.loads((FIXTURES / "product.before.json").read_text())


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


def test_catalog_legacy_bytes_only_have_the_declared_delta():
    path = Path(__file__).resolve().parents[1] / "pineforge_codegen" / "diagnostics_catalog.json"
    catalog = diagnostics_catalog()["codes"]
    restored = []
    for line in path.read_text().splitlines(keepends=True):
        match = re.match(r'  "(PF-[EW][0-9]{4})": ', line)
        if match:
            code = match[1]
            field = ', "user_message": ' + json.dumps(catalog[code]["user_message"], ensure_ascii=False)
            assert line.count(field) == 1
            line = line.replace(field, "")
            if code in NOTE_CODES:
                line = line.replace('"severity": "note"', '"severity": "warning"', 1)
        restored.append(line)
    assert "".join(restored).encode() == (FIXTURES / "catalog.before.json").read_bytes()


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
    # These legacy templates differ only in the argument's name; the existing
    # text classifier chooses the lower stable code at the specificity tie.
    expected_code = "PF-W1508" if code == "PF-W1509" else code
    expected_args = {"name": args["full"]} if code == "PF-W1509" else args
    assert classify("note", message, hint) == (expected_code, expected_args)
    assert classify("warning", message, hint)[0] != code
    assert classify("error", message, hint)[0] != code
    d = Diagnostic(Level.NOTE, Phase.ANALYZER, SourceLocation("例.pine", 3, 2, 8), message, hint)
    assert d.code == expected_code and d.args == expected_args
    assert d.user_message == diagnostics_catalog()["codes"][expected_code]["user_message"]
    assert render(d.user_message, expected_args)


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


def test_typed_warmup_handoff_keeps_identity_range_units_and_inputs():
    import runpy
    types = runpy.run_path(str(FIXTURES / "warmup_consumer.py"))
    fixture = json.loads((FIXTURES / "warmup_handoff.json").read_text())
    sites = fixture["sites"]
    assert {site["context"] for site in sites} == {"chart", "same_symbol_request", "other_symbol_request"}
    assert {site["length"]["kind"] for site in sites} == {"constant", "input", "unknown"}
    for site in sites:
        assert set(site) == types["Site"].__required_keys__
        assert set(site["source_range"]) == types["SourceRange"].__required_keys__
        assert site["startup_unit"] == "bars_of_evaluation_context_timeframe"
        assert site["required_startup_bars"] is None  # no invented convergence threshold
        assert site["recursive_adequacy"] == "unknown" and site["has_unbounded_state"]
        diagnostic = {"call_site_id": site["call_site_id"], "source_range": dict(site["source_range"])}
        assert types["same_site"](diagnostic, site)
        diagnostic["source_range"]["end_col"] += 1
        assert not types["same_site"](diagnostic, site)
        assert not types["same_site"]({"message": "This EMA may need more earlier bars to initialize."}, site)
    input_site = next(site for site in sites if site["length"]["kind"] == "input")
    assert input_site["length"]["input_name"] and input_site["length"]["expression"]
    assert input_site["length"]["value"] is None
    assert {row["scope"] for row in fixture["effective_inputs"]} == {"run", "trial", "study"}
    for row in fixture["effective_inputs"]:
        assert set(row) == types["ResolvedLength"].__required_keys__
        assert types["same_site"](row, input_site)
        assert row["searched_range"] if row["scope"] == "study" else row["submitted_value"]


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
    failure = None
    try:
        diagnostics = transpile_full(source)["diagnostics"]
        ok = True
    except CompileError as exc:
        diagnostics, ok = exc.diagnostics, False
        failure = exc
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
    baseline = PRODUCT_BEFORE["interfaces"][case["name"]]
    normalized = copy.deepcopy(envelope)
    python_rows = []
    for d, row in zip(diagnostics, normalized["diagnostics"]):
        row.pop("user_message")
        level = "warning" if d.code in NOTE_CODES else d.level.value
        row["severity"] = level
        python_rows.append({"severity": level, "phase": d.phase.value,
                            "location": asdict(d.location) if d.location else None,
                            "message": d.message, "hint": d.hint, "code": d.code, "args": d.args})
    assert python_rows == baseline["python"]
    assert json.dumps(normalized) == PRODUCT_BEFORE["wire"][case["name"]]
    if failure:
        text = PRODUCT_BEFORE["text"][case["name"]]
        assert str(failure) == text["error"]
        legacy_levels = [replace(d, level=Level.WARNING) if d.code in NOTE_CODES else d for d in diagnostics]
        assert CompileError(legacy_levels).format(source) == text["format"]
        if any(d.level == Level.NOTE for d in diagnostics):
            assert "note[ANALYZER]:" in failure.format(source)
