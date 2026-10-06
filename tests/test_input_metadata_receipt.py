"""Transpile-time settings agree with the native checked-settings receipt."""

import json
import runpy
from pathlib import Path

import pytest

from pineforge_codegen import transpile_full
from pineforge_codegen.ast_nodes import StringLiteral
from pineforge_codegen.codegen.checked_settings import _string_setting_arg, _string_setting_value
from pineforge_codegen.codegen.helpers import NamingHelper
from pineforge_codegen.errors import CompileError
from pineforge_codegen.pine_spelling import pine_string_literal
from tests._compile import run_emitted_tu
from tests.test_compile_corpus import _resolve_corpus_root
from tests.test_string_settings_constants import RECEIPT_DRIVER


ROOT = Path(__file__).resolve().parents[1]
GLUE = runpy.run_path(str(ROOT / "gate" / "glue.py"))["transpile_json"]

NUMERIC = "input_metadata_numeric.pine"
COMPUTED = "the receipt evaluates it at run time; the manifest publishes only a literal"

# Inputs whose receipt value the transpiler does not publish: source file name
# and input title -> field -> (the manifest's value, why). The pin asserts the
# documented value and that the receipt still differs, so a gap that is closed
# fails the pin until its entry is removed. Every other field of every input
# must equal the receipt's.
KNOWN_LIMITS = {
    (NUMERIC, "Negated bool"): {
        "default": (None, f"`not true`: {COMPUTED}")},
    (NUMERIC, "Tint"): {
        "default": ("color.red", "a color is published by its Pine spelling; "
                                 "the receipt holds the packed integer")},
    (NUMERIC, "Expression default"): {
        "default": (None, f"`LEN * 2`: {COMPUTED}")},
    (NUMERIC, "Expression option"): {
        "options": ([], f"`LEN * 2` among the options: {COMPUTED}")},
    (NUMERIC, "Expression bound"): {
        "min": (None, f"`LEN - 10`: {COMPUTED}"),
        "max": (None, f"`LEN * 2`: {COMPUTED}")},
    (NUMERIC, "Time from parts"): {
        "default": (None, "`timestamp(year, month, ...)` reads the symbol's time zone "
                          "when the receipt is read")},
    (NUMERIC, "Plain constant"): {
        "default": (None, "a plain `input()` is typed and published by a literal default only")},
}


def _sources():
    """Every source once: a copy of one already listed adds no case."""
    sources = [(path, False) for directory in (ROOT / "tests" / "fixtures",
                                              ROOT / "tests" / "gate-corpus")
               for path in sorted(directory.rglob("*.pine"))]
    corpus = _resolve_corpus_root()
    if corpus is not None:
        sources.extend((path, True) for path in sorted(corpus.rglob("strategy.pine")))
    unique, seen = [], set()
    for path, is_corpus in sources:
        text = path.read_text()
        if text not in seen:
            seen.add(text)
            unique.append((path, is_corpus))
    return unique


def _case_id(value):
    if not isinstance(value, Path):
        return None
    for base in (ROOT, _resolve_corpus_root()):
        if base is not None and base in value.parents:
            return str(value.relative_to(base))
    return str(value)


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


def _manifest_view(metadata):
    return {"supported": metadata["supported"], "default": metadata["default"],
            "options": metadata.get("options", []), "min": metadata.get("min"),
            "max": metadata.get("max"), "step": metadata.get("step")}


def _receipt_view(checked):
    number = {"int": int, "float": float}.get(checked["type"])
    return {"supported": checked["supported"], "default": _default(checked, checked["default"]),
            "options": [number(option) for option in checked["options"]] if number
            else checked["options"],
            "min": checked["min"], "max": checked["max"], "step": checked["step"]}


def _limits(path):
    limits = {}
    for (name, title), fields in KNOWN_LIMITS.items():
        if name == path.name:
            limits[title] = fields
    return limits


@pytest.mark.parametrize("path,is_corpus", _sources(), ids=_case_id)
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
    limits = _limits(path)
    seen = set()
    for metadata, checked in zip(full["inputs"], receipt["inputs"]):
        key = metadata["title"]
        manifest, expected = _manifest_view(metadata), _receipt_view(checked)
        for field, value in manifest.items():
            limit = limits.get(key, {}).get(field)
            if limit is None:
                assert value == expected[field], f"{key}.{field}"
                continue
            seen.add((key, field))
            documented, _reason = limit
            assert value == documented, f"{key}.{field}"
            assert value != expected[field], (
                f"{key}.{field}: the limit is closed, remove it from KNOWN_LIMITS")
    assert seen == {(title, field) for title, fields in limits.items() for field in fields}


def test_known_limits_name_inputs_of_a_pinned_source():
    for (name, title), fields in KNOWN_LIMITS.items():
        path = next(path for path, _is_corpus in _sources() if path.name == name)
        inputs = {entry["title"]: entry for entry in transpile_full(path.read_text())["inputs"]}
        assert title in inputs, (name, title)
        for field, (documented, reason) in fields.items():
            assert reason
            assert _manifest_view(inputs[title])[field] == documented, (title, field)


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


STRING_VALUES = [
    pytest.param("", "", id="empty"),
    pytest.param('quote " and slash \\', 'quote " and slash \\', id="quote-backslash"),
    pytest.param("line\nreturn\rtab\t", "line\nreturn\rtab\t", id="whitespace"),
    pytest.param("café 中文 😀\b\f", "café 中文 😀\b\f", id="unicode-controls"),
    pytest.param("x\0" + "70189", "x", id="nul-digits"),
    pytest.param("\0" + "0123456789", "", id="leading-nul-digits"),
    pytest.param("x\0", "x", id="trailing-nul"),
    pytest.param(r"x\00070189", r"x\00070189", id="literal-octal-text"),
    pytest.param(r"\n\r\t\u2603\x41\012", r"\n\r\t\u2603\x41\012",
                 id="literal-escape-text"),
    pytest.param("x\\\0" + "789", "x\\", id="backslash-before-nul"),
    pytest.param("x\0y\0z", "x", id="multiple-nuls"),
]


@pytest.mark.parametrize("value,expected", STRING_VALUES)
@pytest.mark.parametrize("wrapped", [False, True], ids=["literal", "std-string"])
def test_emitted_string_setting_decoder(value, expected, wrapped):
    literal = '"' + NamingHelper._cpp_string_escape(value) + '"'
    lowered = f"std::string({literal})" if wrapped else literal
    assert _string_setting_arg(StringLiteral(value=value), lowered) == lowered
    assert _string_setting_value(lowered) == expected


@pytest.mark.parametrize("lowered", [
    "0", "nullptr", 'std::string("x", 1)', '"x" + other', '"x" "y"',
    r'"\u2603"', r'"\x41"', r'"\012"', r'"\0"', r'"\a"',
    '"x\\"', '"x\ny"', '"x\ry"', '"x\0y"',
])
def test_string_setting_decoder_refuses_non_emitted_forms(lowered):
    assert _string_setting_arg(StringLiteral(value="x"), lowered) is None
    with pytest.raises(ValueError, match="not an emitted C\\+\\+ string literal"):
        _string_setting_value(lowered)


@pytest.mark.parametrize("value,expected", STRING_VALUES)
@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_all_string_input_forms_match_native_receipt(value, expected, opt):
    literal = pine_string_literal(value)
    forms = [
        ("string", "String default", ""),
        ("symbol", "Symbol default", ""),
        ("session", "Session default", ""),
        ("timeframe", "Timeframe default", ""),
        ("text_area", "Text area default", ""),
        ("string", "String option", f', options=[{literal}, "safe"]'),
    ]
    source = '//@version=6\nstrategy("literal settings")\n'
    source += "".join(f'choice_{index} = input.{kind}({literal}, "{title}"{options})\n'
                      for index, (kind, title, options) in enumerate(forms))
    full = transpile_full(source)
    envelope = json.loads(GLUE(source))
    assert envelope["ok"] is True
    assert envelope["inputs"] == full["inputs"]
    receipt = json.loads(run_emitted_tu(full["cpp"], RECEIPT_DRIVER, opt=opt,
                                       label="literal settings receipt"))
    assert len(full["inputs"]) == len(receipt["inputs"]) == len(forms)
    for metadata, checked, (kind, title, options) in zip(full["inputs"], receipt["inputs"], forms):
        assert metadata["title"] == checked["name"] == title
        assert metadata["kind"] == checked["kind"] == kind
        assert metadata["supported"] is True
        assert metadata["default"] == checked["default"] == checked["effective_value"] == expected
        assert metadata.get("options", []) == ([expected, "safe"] if options else [])
        assert _manifest_view(metadata) == _receipt_view(checked)
