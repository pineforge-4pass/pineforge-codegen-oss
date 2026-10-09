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
from tests import _e2e


FIXTURES = Path(__file__).parent / "fixtures" / "diagnostic_notes"
CASES = json.loads((FIXTURES / "interfaces.json").read_text())["cases"]
DELTA = json.loads((FIXTURES / "delta.json").read_text())
NOTE_CODES = set(DELTA["severity_changes"])
BEFORE = json.loads((FIXTURES / "catalog.before.json").read_text())["codes"]
PRODUCT_BEFORE = json.loads((FIXTURES / "product.before.json").read_text())
# The support checker warns, inside a switch arm, what it refuses elsewhere:
# PF-W1nnn is PF-E1nnn there. Read off the live catalog, so a twin added later
# is covered by the sentence tests below without an edit here.
TWINS = sorted("PF-W" + code[4:] for code in diagnostics_catalog()["codes"] if code.startswith("PF-E1"))


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


# -- Warning twins: the sentence says what the warning does ---------------------------
#
# A problem the support checker finds in a switch arm is accepted with a warning
# (``SupportChecker._err``): PF-W1nnn is PF-E1nnn's template as a warning, and the
# generator creates a twin as a copy of the error's entry. Authoring both with one
# sentence is the easy mistake -- the first authoring copied each error's sentence
# into its twin, and it said PineForge refused a call in a script that had just
# transpiled. Each twin needs a sentence of its own.

REFUSAL = re.compile(r"\b(?:refus|reject)\w*", re.IGNORECASE)
TRADINGVIEW_REFUSAL = re.compile(r"\bTradingView (?:refus|reject)\w*", re.IGNORECASE)


def _asserts_refusal(template):
    """Whether a sentence says something is refused. What TradingView refuses is a
    fact about TradingView (``a call TradingView rejects``) and may stay."""
    return bool(REFUSAL.search(TRADINGVIEW_REFUSAL.sub("", template)))


def _assert_twin_sentence(catalog, twin_code):
    error_code = "PF-E" + twin_code[4:]
    twin, error = catalog[twin_code], catalog[error_code]
    sentence = twin["user_message"]
    assert twin["severity"] == "warning", twin_code
    assert sentence != error["user_message"], f"{twin_code} copies the sentence of {error_code}"
    assert not _asserts_refusal(sentence), (twin_code, sentence)
    assert re.search(r"\bwarns?\b", sentence), (twin_code, sentence)
    if "inside a switch arm" in twin["explanation"]:
        # The catalog's own meaning of the twin: it warns and the arm keeps its lowering.
        assert "switch arm" in sentence and "lowering" in sentence, (twin_code, sentence)
    else:
        # A warning everywhere (bar_index, last_bar_index, timenow): no arm to name.
        assert "switch arm" not in sentence, (twin_code, sentence)


def test_every_switch_arm_twin_is_covered():
    catalog = diagnostics_catalog()["codes"]
    assert len(TWINS) >= 97 and len(set(TWINS)) == len(TWINS)
    assert all(code in catalog for code in TWINS)


@pytest.mark.parametrize("twin_code", TWINS)
def test_switch_arm_twin_sentence_is_its_own_and_is_not_a_refusal(twin_code):
    _assert_twin_sentence(diagnostics_catalog()["codes"], twin_code)


def test_no_nonfatal_sentence_claims_a_refusal():
    """A warning or a note means the script transpiled."""
    for code, entry in diagnostics_catalog()["codes"].items():
        if entry["severity"] != "error":
            assert not _asserts_refusal(entry["user_message"]), (code, entry["user_message"])


@pytest.mark.parametrize("sentence, refuses", [
    ("PineForge cannot load external seed data feeds, so request.seed is refused.", True),
    ("PineForge has no implementation of this ta.* function, so the call is refused instead "
     "of compiling to a silent stub.", True),
    ("A call whose arguments match none of TradingView''s signatures is refused, "
     "as TradingView refuses it.", True),
    ("The timeframe string of request.security is not a valid Pine timeframe, "
     "so PineForge rejects it.", True),
    ("TradingView rejects a method call written straight after a history index, x[k].method().", False),
    ("The missing str.repeat count produces an empty string for a call TradingView rejects.", False),
    ("PineForge cannot load external seed data feeds, so request.seed cannot return any. "
     "In a switch arm PineForge only warns and keeps the arm''s lowering.", False),
])
def test_refusal_wording_detector(sentence, refuses):
    assert _asserts_refusal(sentence) is refuses


@pytest.mark.parametrize("twin_code", ["PF-W1003", "PF-W1013", "PF-W1027", "PF-W1090", "PF-W1096"])
def test_twin_check_rejects_the_sentence_copied_from_the_error(twin_code):
    catalog = diagnostics_catalog()["codes"]
    catalog[twin_code]["user_message"] = catalog["PF-E" + twin_code[4:]]["user_message"]
    with pytest.raises(AssertionError):
        _assert_twin_sentence(catalog, twin_code)


@pytest.mark.parametrize("twin_code, sentence", [
    # refusal wording behind a correct switch-arm clause
    ("PF-W1027", "request.seed is refused. In a switch arm PineForge only warns and keeps the arm''s lowering."),
    # neither a warning nor a switch-arm clause
    ("PF-W1027", "PineForge cannot load external seed data feeds, so request.seed cannot return any."),
    # a switch-arm clause that does not say it only warns
    ("PF-W1027", "PineForge has no seed data. In a switch arm PineForge keeps the arm''s lowering."),
    # a switch-arm clause on a warning that is not about a switch arm
    ("PF-W1067", "PineForge warns that bar_index differs. In a switch arm PineForge only warns "
                 "and keeps the arm''s lowering."),
])
def test_twin_check_rejects_other_false_sentences(twin_code, sentence):
    catalog = diagnostics_catalog()["codes"]
    catalog[twin_code]["user_message"] = sentence
    with pytest.raises(AssertionError):
        _assert_twin_sentence(catalog, twin_code)


HEAD = '//@version=6\nstrategy("T", overlay = true)\n'
TAIL = 'if x > 0\n    strategy.entry("L", strategy.long)\n'
# The accepted-arm shapes tests/test_tail_f_rules.py pins at the Python interface.
ARM_SEED = (HEAD + 'k = bar_index % 2\nx = switch k\n'
            '    0 => request.seed("seed_crypto_santiment", "BTC", close)\n'
            '    => 1.0\n' + TAIL)
ARM_TEXT_CONSTANT = (HEAD + 'm = close > open ? "L" : "R"\nta_ = switch m\n'
                     '    "L" => text.align_left\n    "R" => text.align_right\n'
                     'if barstate.islast\n    label.new(bar_index, close, "x", textalign = ta_)\n'
                     'x = 1\n' + TAIL)
OUTSIDE_ARM_SEED = HEAD + 'x = request.seed("seed_crypto_santiment", "BTC", close)\n' + TAIL
ARM_CASES = {
    "request.seed": (ARM_SEED, "PF-W1027", "request.seed"),
    "text constant": (ARM_TEXT_CONSTANT, "PF-W1078", "text.align_left"),
}


@pytest.mark.parametrize("name", sorted(ARM_CASES))
def test_an_accepted_switch_arm_warns_with_a_sentence_that_says_so(name):
    source, code, needle = ARM_CASES[name]
    catalog = diagnostics_catalog()["codes"]
    out = transpile_full(source)  # a refusal would raise CompileError
    assert out["cpp"]
    found = [d for d in out["diagnostics"] if d.code == code]
    assert found and any(needle in d.message for d in found), \
        [(d.code, d.message) for d in out["diagnostics"]]
    sentence = catalog[code]["user_message"]
    assert "switch arm" in sentence and re.search(r"\bwarns?\b", sentence)
    assert not _asserts_refusal(sentence)
    for d in found:
        assert d.level == Level.WARNING
        assert d.user_message == sentence
    envelope = json.loads(_glue()(source))
    assert envelope["ok"] is True
    wire = [e for e in envelope["diagnostics"] if e["code"] == code]
    assert len(wire) == len(found)
    assert all(e["severity"] == "warning" and e["user_message"] == sentence for e in wire)
    # Nothing an accepted script carries says that something was refused.
    assert not [e["code"] for e in envelope["diagnostics"] if _asserts_refusal(e["user_message"])]


def test_the_same_call_outside_a_switch_arm_keeps_the_error_sentence():
    catalog = diagnostics_catalog()["codes"]
    with pytest.raises(CompileError) as refused:
        transpile_full(OUTSIDE_ARM_SEED)
    errors = [d for d in refused.value.diagnostics if d.code == "PF-E1027"]
    assert errors, [(d.code, d.message) for d in refused.value.diagnostics]
    assert all(d.level == Level.ERROR for d in errors)
    assert errors[0].user_message == catalog["PF-E1027"]["user_message"]
    assert errors[0].user_message != catalog["PF-W1027"]["user_message"]
    envelope = json.loads(_glue()(OUTSIDE_ARM_SEED))
    assert envelope["ok"] is False
    assert any(e["code"] == "PF-E1027" and e["severity"] == "error" for e in envelope["diagnostics"])


ARRAY_HISTORY_IN_REQUEST = (
    '//@version=6\nstrategy("shape", overlay = true, max_lines_count = 500)\n'
    "a = array.from(close)\n"
    'r = request.security(syminfo.tickerid, "60", (a[1]).size())\n'
    "if r > 0\n"
    '    strategy.entry("L", strategy.long)\n')


def test_array_history_in_a_request_is_described_as_array_history_not_references():
    """PF-E6062 is the array and matrix history scope check (collection_history.py,
    tests/test_array_history.py ``request_security_size``); the history of a
    user-defined type or drawing reference in a request is PF-E3043's."""
    catalog = diagnostics_catalog()["codes"]
    with pytest.raises(CompileError) as refused:
        transpile_full(ARRAY_HISTORY_IN_REQUEST)
    found = [d for d in refused.value.diagnostics if d.code == "PF-E6062"]
    assert found, [(d.code, d.message) for d in refused.value.diagnostics]
    assert all("inside a request.security expression" in d.message for d in found)
    sentence = catalog["PF-E6062"]["user_message"]
    assert all(d.level == Level.ERROR and d.user_message == sentence for d in found)
    assert "array" in sentence and "matrix" in sentence and "request.security" in sentence
    assert "user-defined" not in sentence and "drawing" not in sentence
    assert sentence != catalog["PF-E3043"]["user_message"]
    envelope = json.loads(_glue()(ARRAY_HISTORY_IN_REQUEST))
    assert envelope["ok"] is False
    assert {e["user_message"] for e in envelope["diagnostics"] if e["code"] == "PF-E6062"} == {sentence}


# -- A call PineForge skips: the sentence names the call and says it does nothing -----
#
# PF-W1508 (a bare call) and PF-W1509 (the same template, spelled for a namespaced one)
# first said "{name} is not drawn in backtests.". The support checker emits the note for
# alert() and alertcondition() as well as plot() and table.new(), and an alert draws
# nothing on a chart, so the sentence described a drawing that never existed.
# "{name}() does nothing in a backtest." holds for every call the note covers. `name` is
# the call's bare or dotted name and never carries parentheses (the checker spells
# "{name}(...)" itself, SupportChecker._visit_FuncCall), so the template's "()" is the
# only pair a rendered sentence has.

# The head the sentence was amended on: the reference for the C++ and the diagnostic
# fields. A squash landing drops this commit from history, so the test then needs a new
# pin (v1.4.0's 8663272 is the candidate: no analyzer or code-generation file differs
# from it) or has to go.
NO_EFFECT_PARENT = "5a252e738452c0043c35f181578e8333ba7cdbf5"
NO_EFFECT_TEMPLATES = {"PF-W1508": "{name}() does nothing in a backtest.",
                       "PF-W1509": "{full}() does nothing in a backtest."}
# (script, the name its note carries): the two calls the old sentence was wrong for, and
# plot and table.new, which the note always covered.
NO_EFFECT_CASES = [
    pytest.param(HEAD + 'alert("go", alert.freq_once_per_bar)\n', "alert", id="alert"),
    pytest.param(HEAD + 'alertcondition(close > open, "up", "close is above open")\n',
                 "alertcondition", id="alertcondition"),
    pytest.param(HEAD + "plot(close)\n", "plot", id="plot"),
    pytest.param(HEAD + "t = table.new(position.top_right, 1, 1)\n", "table.new", id="table_new"),
]


def _but(mapping, key):
    return {k: v for k, v in mapping.items() if k != key}


@pytest.mark.parametrize("source, name", NO_EFFECT_CASES)
def test_a_skipped_call_note_names_the_call_and_says_it_does_nothing(source, name):
    template = NO_EFFECT_TEMPLATES["PF-W1508"]
    sentence = f"{name}() does nothing in a backtest."
    out = transpile_full(source)
    notes = [d for d in out["diagnostics"] if d.code == "PF-W1508"]
    assert len(notes) == 1, [(d.code, d.message) for d in out["diagnostics"]]
    note = notes[0]
    # Identity, level, arguments and the English text are what they were.
    assert note.level == Level.NOTE
    assert note.args == {"name": name}
    assert note.message == f"{name}(...) has no effect in PineForge backtests (visual only)."
    assert note.hint is None
    # user_message is the raw catalog template; a receiver renders it with args as text.
    assert note.user_message == template
    assert render(note.user_message, note.args) == sentence
    envelope = json.loads(_glue()(source))
    assert envelope["ok"] is True
    wire = [e for e in envelope["diagnostics"] if e["code"] == "PF-W1508"]
    assert len(wire) == 1
    assert wire[0]["severity"] == "note"
    assert wire[0]["args"] == {"name": name}
    assert wire[0]["message"] == note.message
    assert wire[0]["user_message"] == template
    assert render(wire[0]["user_message"], wire[0]["args"]) == sentence


def test_the_alias_template_is_the_same_sentence_over_its_own_argument():
    """PF-W1509 is never classified (the text classifier picks PF-W1508 where the two
    templates tie), but it is a catalog entry a receiver can be handed."""
    catalog = diagnostics_catalog()["codes"]
    for code, argument in (("PF-W1508", "name"), ("PF-W1509", "full")):
        entry = catalog[code]
        assert entry["severity"] == "note" and list(entry["args"]) == [argument], code
        assert entry["user_message"] == NO_EFFECT_TEMPLATES[code], code
        assert "drawn" not in entry["user_message"], code
        assert render(entry["user_message"], {argument: "table.new"}) == \
            "table.new() does nothing in a backtest."
    assert (catalog["PF-W1509"]["user_message"].replace("{full}", "{name}")
            == catalog["PF-W1508"]["user_message"])


@pytest.mark.parametrize("source, name", NO_EFFECT_CASES)
def test_the_sentence_is_the_only_change_to_the_parent_heads_output(tmp_path, source, name):
    parent = _e2e.reference_codegen(NO_EFFECT_PARENT)
    if parent is None:
        pytest.fail(f"required reference {NO_EFFECT_PARENT} unavailable; restore git history")
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    ours, before = _e2e.transpile_json(pine), _e2e.transpile_json(pine, parent)
    assert ours["ok"] is True and before["ok"] is True
    # The generated C++ and every other key of the envelope are the parent's...
    assert ours["cpp"] and ours["cpp"] == before["cpp"]
    assert _but(ours, "diagnostics") == _but(before, "diagnostics")
    # ...and so is every diagnostic field, but the sentence of the PF-W1508 notes.
    assert len(ours["diagnostics"]) == len(before["diagnostics"])
    for now, then in zip(ours["diagnostics"], before["diagnostics"]):
        assert _but(now, "user_message") == _but(then, "user_message")
        assert (now["user_message"] != then["user_message"]) == (now["code"] == "PF-W1508"), (now, then)
    assert [d["args"] for d in ours["diagnostics"] if d["code"] == "PF-W1508"] == [{"name": name}]
