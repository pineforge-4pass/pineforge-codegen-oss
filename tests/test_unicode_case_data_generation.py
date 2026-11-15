"""Unit targets for the offline UCD parser and its pinned serialization."""

import ast
from pathlib import Path

import pytest

from scripts import gen_unicode_case_data as gen


def _row(point, name, category, *, upper="", lower=""):
    fields = [f"{point:04X}", name, category, "0", "L",
              "", "", "", "", "N", "", "", upper, lower, ""]
    return ";".join(fields) + "\n"


def _inputs():
    # Synthetic UCD records test parsing, not the casing of a Pine script.
    ordinary = (
        _row(0x31, "TEST DECIMAL", "Nd")
        + _row(0x41, "TEST CAPITAL", "Lu", lower="0061")
        + _row(0x61, "TEST SMALL", "Ll", upper="0041")
    )
    special = (
        "0061; 0061; 0042 0043; 0042 0043;\n"
        "0041; 0062; 0041; 0041; Final_Sigma;\n"
        "0061; ; 0049; 0049; tr After_I;\n"
    )
    properties = (
        "0041..0042 ; Cased\n"
        "002E ; Case_Ignorable\n"
        "0042 ; Case_Ignorable\n"
    )
    return ordinary, special, properties


def test_ucd_parser_combines_simple_full_and_contextual_data_without_locale_tailoring():
    data = gen.parse_ucd(*_inputs())
    assert data.lower == ((0x41, 1, 0x61),)
    assert data.upper == ((0x61, 2, 0x42, 0x43),)
    assert data.lower_final_sigma == ((0x41, 1, 0x62),)
    assert data.context == (
        (0x2E, 0x2E, 2), (0x31, 0x31, 4),
        (0x41, 0x41, 1), (0x42, 0x42, 3),
    )
    assert data.excluded_locale_rules == ("0061: tr After_I",)


def test_unicode_data_first_last_ranges_apply_properties_to_the_whole_interval():
    flags = bytearray(0x110000)
    records = (_row(0x100, "<TEST, First>", "Nd")
               + _row(0x105, "<TEST, Last>", "Nd"))
    gen._unicode_data(records, flags)
    assert gen._context_ranges(flags) == ((0x100, 0x105, 4),)
    with pytest.raises(gen.DataError, match="unterminated range"):
        gen._unicode_data(_row(0x100, "<TEST, First>", "Nd"), flags)
    with pytest.raises(gen.DataError, match="inconsistent range"):
        gen._unicode_data(records.replace("<TEST, Last>", "<OTHER, Last>"), flags)


@pytest.mark.parametrize("name,size,digest", gen.INPUT_PINS)
def test_input_identity_rejects_truncated_and_same_size_wrong_content(name, size, digest):
    with pytest.raises(gen.DataError, match="size/SHA-256"):
        gen._verified_text(name, b"")
    with pytest.raises(gen.DataError, match="size/SHA-256"):
        gen._verified_text(name, b"\n" * size)


def test_unknown_root_condition_is_an_error_instead_of_a_dropped_override():
    ordinary, special, properties = _inputs()
    with pytest.raises(gen.DataError, match="unsupported root condition"):
        gen.parse_ucd(ordinary, special.replace("Final_Sigma", "Future_Context"), properties)


def test_generated_module_round_trips_tables_and_ignores_irrelevant_record_order():
    ordinary, special, properties = _inputs()
    data = gen.parse_ucd(ordinary, special, properties)
    encoded = gen.render_module(data)
    # These two source sections are sets, unlike ordered UnicodeData records.
    reordered = gen.parse_ucd(
        ordinary, "\n".join(reversed(special.splitlines())) + "\n",
        "002E ; Case_Ignorable\n0042 ; Case_Ignorable\n0041..0042 ; Cased\n",
    )
    assert gen.render_module(reordered) == encoded
    tree = ast.parse(encoded.decode("ascii"))
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body if isinstance(node, ast.Assign)
    }
    assert assignments["LOWER_ROWS"] == data.lower
    assert assignments["UPPER_ROWS"] == data.upper
    assert assignments["LOWER_FINAL_SIGMA_ROWS"] == data.lower_final_sigma
    assert assignments["CONTEXT_RANGES"] == data.context
    assert assignments["SOURCE_SHA256"] == tuple(
        (name, digest) for name, _, digest in gen.INPUT_PINS
    )


@pytest.mark.parametrize("filename", [
    "/no-access/.ssh/UnicodeData.txt",
    "/no-access/.aws/UnicodeData.txt",
    "/no-access/.ssh/UnicodeData.txt",
    "/no-access/.config/zsh/keys/UnicodeData.txt",
    "/no-access/.config/pineforge/UnicodeData.txt",
    "/no-access/private.env/UnicodeData.txt",
])
def test_sensitive_paths_are_refused_by_name_before_any_filesystem_query(monkeypatch, filename):
    def no_filesystem_query(*args, **kwargs):
        pytest.fail("protected name reached a filesystem query")

    monkeypatch.setattr(Path, "is_symlink", no_filesystem_query)
    with pytest.raises(gen.DataError, match="protected file path"):
        gen._checked_path(filename, "UnicodeData.txt")
