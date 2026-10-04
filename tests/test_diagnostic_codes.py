"""Stable diagnostic codes: the catalog, its pin, and every emitted diagnostic.

Every diagnostic carries a ``code`` and named ``args``; the catalog
(``pineforge_codegen/diagnostics_catalog.json``) gives each code its severity,
English ICU MessageFormat templates and a one-line explanation, and the
rendered templates are the diagnostic's ``message`` and ``hint`` byte for
byte. ``tests/fixtures/diagnostic_codes_pin.json`` pins what each code means:
a code is never removed or repurposed, and a new code needs a catalog entry
(``scripts/gen_diagnostics_catalog.py --write`` adds both).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

import pytest

from pineforge_codegen import diagnostics_catalog, transpile, transpile_full
from pineforge_codegen.diagnostic_codes import (
    UNCATALOGUED, classify, escape_literal, parse_template, render, render_diagnostic,
)
from pineforge_codegen.errors import CompileError, Diagnostic, Level, Phase, SourceLocation

ROOT = Path(__file__).resolve().parent.parent
PIN = ROOT / "tests" / "fixtures" / "diagnostic_codes_pin.json"
FIXTURES = ROOT / "tests" / "fixtures"
KINDS = {"identifier", "type", "keyword", "number", "vocab", "text"}
CATALOG = diagnostics_catalog()["codes"]


def _placeholders(template: str | None) -> set[str]:
    if template is None:
        return set()
    return {part[0] for part in parse_template(template) if isinstance(part, tuple)}


def pin_digest(entry: dict) -> str:
    """What a code means: its severity, templates and argument names."""
    meaning = json.dumps([entry["severity"], entry["message"], entry.get("hint"),
                          sorted(entry.get("args", {}))], ensure_ascii=False)
    return hashlib.sha256(meaning.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------

def test_catalog_entries_are_well_formed():
    assert diagnostics_catalog()["schema"] == "pineforge-diagnostics-catalog/v1"
    for code, entry in CATALOG.items():
        assert re.fullmatch(r"PF-[EW][0-9]{4}", code), code
        assert entry["severity"] == {"E": "error", "W": "warning"}[code[3]], code
        assert entry["area"], code
        explanation = entry["explanation"]
        assert explanation and "\n" not in explanation and len(explanation) <= 300, code
        names = _placeholders(entry["message"]) | _placeholders(entry.get("hint"))
        assert names == set(entry["args"]), code
        for name, spec in entry["args"].items():
            assert spec["kind"] in KINDS, (code, name)
            if spec["kind"] == "vocab":
                assert spec["values"] and all(isinstance(v, str) for v in spec["values"]), (code, name)
            else:
                assert "values" not in spec, (code, name)


def test_catalog_codes_are_pinned():
    """Removing or repurposing a code fails; a new code must be pinned too."""
    pinned = json.loads(PIN.read_text(encoding="utf-8"))["codes"]
    removed = sorted(set(pinned) - set(CATALOG))
    assert not removed, f"codes are never removed: {removed}"
    repurposed = sorted(c for c in pinned if pin_digest(CATALOG[c]) != pinned[c])
    assert not repurposed, (
        f"a changed meaning needs a new code, the old one kept: {repurposed}")
    unpinned = sorted(set(CATALOG) - set(pinned))
    assert not unpinned, (
        f"new codes {unpinned} are not pinned: run scripts/gen_diagnostics_catalog.py --write")


def test_catalog_api_returns_a_copy():
    first = diagnostics_catalog()
    first["codes"].clear()
    assert diagnostics_catalog()["codes"] == CATALOG


def test_switch_arm_warnings_twin_support_checker_errors():
    """The support checker warns, inside a switch arm, what it refuses
    elsewhere: PF-W1nnn (nnn < 500) is PF-E1nnn's text as a warning."""
    for code, entry in CATALOG.items():
        if code.startswith("PF-E1"):
            twin = CATALOG["PF-W" + code[4:]]
            assert (twin["message"], twin.get("hint")) == (entry["message"], entry.get("hint"))
            assert twin["severity"] == "warning"


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "gen_diagnostics_catalog", ROOT / "scripts" / "gen_diagnostics_catalog.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_spelled_template_has_a_code():
    """A diagnostic text the source spells needs a catalog entry."""
    generator = _load_generator()
    have = {(e["severity"], e["message"], e.get("hint")) for e in CATALOG.values()}
    missing = [
        f"[{t['severity']}] {t['message']!r} (hint {t['hint']!r}) at {', '.join(t['sites'])}"
        for t in generator.extract()
        if (t["severity"], t["message"], t["hint"]) not in have
    ]
    assert not missing, (
        "templates without a code (run scripts/gen_diagnostics_catalog.py --write):\n"
        + "\n".join(missing))


# ---------------------------------------------------------------------------
# Rendering and classification
# ---------------------------------------------------------------------------

def test_icu_quoting():
    assert render("it''s '{'x'}' {a}", {"a": "b"}) == "it's {x} b"
    assert render("'{0}' and ''{name}''", {"name": "len"}) == "{0} and 'len'"
    assert render("a lone ' stays", {}) == "a lone ' stays"
    for text in ("{0,number,#}", "it's", "'{'", "''", "a}b{c"):
        assert render(escape_literal(text), {}) == text
    assert render("{n} bars", {"n": 1234}) == "1234 bars"


def test_classify_reads_arguments_raw():
    code, args = classify("error", "Undefined variable: 'rsiLen'")
    assert CATALOG[code]["message"] == "Undefined variable: ''{name}''"
    assert args == {"name": "rsiLen"}
    assert render_diagnostic(code, args) == ("Undefined variable: 'rsiLen'", None)


def test_uncatalogued_text_keeps_a_code():
    diagnostic = Diagnostic(Level.ERROR, Phase.ANALYZER,
                            SourceLocation("t.pine", 1, 1, 1), "boom")
    assert diagnostic.code == UNCATALOGUED["error"]
    assert diagnostic.args == {"message": "boom"}


def test_number_arguments_are_numbers():
    with pytest.raises(CompileError) as err:
        transpile("//@version=6\nstrategy('t')\nplot(close)\n" + "x = " + "(" * 600 + "1" + ")" * 600)
    diagnostic = err.value.diagnostics[0]
    entry = CATALOG[diagnostic.code]
    numbers = [n for n, spec in entry["args"].items() if spec["kind"] == "number"]
    assert numbers and all(isinstance(diagnostic.args[n], int) for n in numbers)
    assert render(entry["message"], diagnostic.args) == diagnostic.message


def _lazy_regex_patterns() -> list:
    """Each template as a lazy-group regex over the message, a NUL and the
    hint (the matcher before it became linear), most specific first."""
    import re as _re
    patterns = []
    for code, entry in sorted(CATALOG.items(), key=lambda kv: (-_specificity(kv[1]), kv[0])):
        if code in UNCATALOGUED.values():
            continue
        parts = parse_template(entry["message"])
        if entry.get("hint") is not None:
            parts = parts + ["\x00"] + parse_template(entry["hint"])
        pieces, seen = [], set()
        for part in parts:
            if isinstance(part, str):
                pieces.append(_re.escape(part))
            elif part[0] in seen:
                pieces.append(f"(?P={part[0]})")
            else:
                seen.add(part[0])
                pieces.append(f"(?P<{part[0]}>.*?)")
        patterns.append((code, entry["severity"], entry.get("hint") is not None,
                         _re.compile("".join(pieces), _re.DOTALL)))
    return patterns


def _lazy_regex_classify(patterns: list, severity: str, message: str, hint: str | None):
    subject = message if hint is None else message + "\x00" + hint
    for code, template_severity, has_hint, pattern in patterns:
        if template_severity != severity or has_hint != (hint is not None):
            continue
        found = pattern.fullmatch(subject)
        if found is not None:
            return code, found.groupdict()
    return UNCATALOGUED[severity], None


def _specificity(entry: dict) -> int:
    parts = parse_template(entry["message"])
    if entry.get("hint") is not None:
        parts = parts + ["\x00"] + parse_template(entry["hint"])
    return sum(len(part) for part in parts if isinstance(part, str))


def _raw(args: dict) -> dict:
    return {name: str(value) for name, value in args.items()}


def test_classification_is_the_lazy_regex_split():
    """Every template rendered with plain arguments and with arguments holding
    its own literals reads back as a lazy-regex fullmatch reads it: the same
    code and the same arguments, a name used twice included."""
    import random
    rng = random.Random(20261004)
    patterns = _lazy_regex_patterns()
    checked = 0
    for code, entry in sorted(CATALOG.items()):
        if code in UNCATALOGUED.values():
            continue
        literals = [part for template in (entry["message"], entry.get("hint")) if template
                    for part in parse_template(template) if isinstance(part, str) and part] or ["x"]
        for trial in range(4):
            args = {}
            for name in entry["args"]:
                if trial < 2:
                    args[name] = rng.choice(["x", "a[1]", "ta.sma(close, 14)", "obj.field[1]", "it's"])
                else:
                    args[name] = "".join(rng.choice(["q", rng.choice(literals)[:rng.randint(1, 6)],
                                                     rng.choice(literals)]) for _ in range(2))
            message = render(entry["message"], args)
            hint = render(entry.get("hint"), args)
            expected_code, expected_args = _lazy_regex_classify(patterns, entry["severity"], message, hint)
            got_code, got_args = classify(entry["severity"], message, hint)
            assert got_code == expected_code, (code, message, hint)
            if expected_args is not None:
                assert _raw(got_args) == expected_args, (code, message, hint)
            checked += 1
    assert checked > 1000


def test_classification_is_linear_on_crafted_text():
    """Arguments flooded with the template's own separators and a hint no
    template has: a lazy-regex fullmatch backtracked over every split (23 s
    for 3.4 KB of one template's text); the split stays linear."""
    import time
    worst = 0.0
    for code, entry in CATALOG.items():
        if code in UNCATALOGUED.values():
            continue
        parts = parse_template(entry["message"])
        if sum(isinstance(part, tuple) for part in parts) < 3:
            continue
        flooded = []
        for index, part in enumerate(parts):
            if isinstance(part, str):
                flooded.append(part)
            else:
                after = next((p for p in parts[index + 1:] if isinstance(p, str)), "")
                flooded.append((after or "z") * 2000)
        started = time.monotonic()
        classify(entry["severity"], "".join(flooded),
                 None if entry.get("hint") is None else "a hint no template has")
        worst = max(worst, time.monotonic() - started)
    assert worst < 2.0


# ---------------------------------------------------------------------------
# Every emitted diagnostic renders back to its text
# ---------------------------------------------------------------------------

def _corpus_dir() -> Path | None:
    env = os.environ.get("PINEFORGE_ENGINE_CORPUS")
    candidates = [Path(env)] if env else []
    candidates.append(ROOT.parent / "pineforge-engine" / "corpus")
    return next((c for c in candidates if c.is_dir()), None)


def _scripts() -> list[Path]:
    scripts = sorted(FIXTURES.rglob("*.pine"))
    corpus = _corpus_dir()
    if corpus is not None:
        scripts += sorted(corpus.rglob("*.pine"))
    return scripts


def _assert_coded(diagnostic: Diagnostic, where: str) -> None:
    code, args = diagnostic.code, diagnostic.args
    assert code not in UNCATALOGUED.values(), f"{where}: no template renders {diagnostic.message!r}"
    entry = CATALOG[code]
    assert entry["severity"] == diagnostic.level.value, where
    assert render(entry["message"], args) == diagnostic.message, where
    assert render(entry.get("hint"), args) == diagnostic.hint, where
    for name, spec in entry["args"].items():
        value = args[name]
        assert isinstance(value, (int, float) if spec["kind"] == "number" else str) \
            or (spec["kind"] == "number" and isinstance(value, str)), (where, name)


@pytest.mark.parametrize("path", _scripts(), ids=lambda p: str(p.relative_to(p.parents[2])))
def test_emitted_diagnostics_render_their_text(path: Path):
    source = path.read_text(encoding="utf-8")
    try:
        diagnostics = transpile_full(source)["diagnostics"]
    except CompileError as err:
        diagnostics = err.diagnostics
    for diagnostic in diagnostics:
        _assert_coded(diagnostic, str(path))


def test_glue_envelope_carries_codes():
    namespace: dict = {}
    glue = (ROOT / "gate" / "glue.py").read_text(encoding="utf-8")
    exec(compile(glue.replace('"/codegen"', repr(str(ROOT))), "glue.py", "exec"), namespace)
    source = ("//@version=6\nstrategy('t')\nx = bar_index\n"
              "if close > open\n    strategy.entry('L', strategy.long)\n")
    envelope = json.loads(namespace["transpile_json"](source))
    assert envelope["ok"] and envelope["diagnostics"]
    for entry in envelope["diagnostics"]:
        spec = CATALOG[entry["code"]]
        message = render(spec["message"], entry["args"])
        hint = render(spec.get("hint"), entry["args"])
        assert entry["message"] == (message + " — " + hint if hint else message)
    failed = json.loads(namespace["transpile_json"]("//@version=6\nstrategy('t')\ny = nope + 1\n"))
    assert not failed["ok"]
    assert all(entry["code"].startswith("PF-E") for entry in failed["diagnostics"])
