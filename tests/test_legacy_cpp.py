"""Keep rename reversal from masking literal or identifier drift."""

import pytest

from tests._legacy_cpp import without_source_rename


@pytest.mark.parametrize(
    ("cpp", "expected"),
    (
        ("pf_safe_source", "source"),
        (
            "pf_safe_source_2 prefix_pf_safe_source pf_safe_source_suffix "
            "pf_safe_source2 2pf_safe_source source pf_safe_native_calendar",
            "pf_safe_source_2 prefix_pf_safe_source pf_safe_source_suffix "
            "pf_safe_source2 2pf_safe_source source pf_safe_native_calendar",
        ),
        (
            'f(pf_safe_source); std::string("pf_safe_source source");',
            'f(source); std::string("pf_safe_source source");',
        ),
        (
            r'f("pf_safe_source \"pf_safe_source\"", pf_safe_source);',
            r'f("pf_safe_source \"pf_safe_source\"", source);',
        ),
        (
            'u8R"tag("pf_safe_source"\npf_safe_source)tag"; pf_safe_source;',
            'u8R"tag("pf_safe_source"\npf_safe_source)tag"; source;',
        ),
        (
            "char quote = '\"'; char c = 'p'; f(pf_safe_source);",
            "char quote = '\"'; char c = 'p'; f(source);",
        ),
        (
            "// pf_safe_source\n/* pf_safe_source */ f(pf_safe_source);",
            "// pf_safe_source\n/* pf_safe_source */ f(source);",
        ),
    ),
)
def test_without_source_rename_changes_only_complete_code_identifiers(
    cpp: str, expected: str,
) -> None:
    assert without_source_rename(cpp) == expected
