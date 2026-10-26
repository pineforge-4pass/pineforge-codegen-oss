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


@pytest.mark.parametrize(
    "use",
    (
        "result = source;",
        "f(obj.source);",
        "f(ptr->source);",
        "f(other::source::value);",
        "pineforge::source::PineStrategyHost source;",
        "f(pineforge::source::unknown_member);",
    ),
    ids=("bare", "member", "pointer-member", "other-namespace",
         "engine-type-user-name", "unknown-engine-member"),
)
def test_without_source_rename_rejects_partial_rename(use: str) -> None:
    cpp = "double pf_safe_source = 1.0;\n" + use
    with pytest.raises(AssertionError, match="partial source rename"):
        without_source_rename(cpp)


def test_without_source_rename_reverses_complete_rename() -> None:
    cpp = 'double pf_safe_source = 1.0;\nf(pf_safe_source, "source"); // source\n'
    assert without_source_rename(cpp) == (
        'double source = 1.0;\nf(source, "source"); // source\n'
    )


@pytest.mark.parametrize(
    "cpp",
    (
        "double source = 1.0;\nf(source);\n",
        'double source = 1.0;\nf(source, "pf_safe_source"); // pf_safe_source\n',
    ),
    ids=("no-escaped-spelling", "inert-escaped-spellings"),
)
def test_without_source_rename_leaves_unrenamed_text_unchanged(cpp: str) -> None:
    assert without_source_rename(cpp) == cpp


@pytest.mark.parametrize(
    "spelling",
    (
        "#include <pineforge/source/pine_strategy_host.hpp>",
        "#include <pineforge/source/pine_ta_length.hpp>",
        "pineforge::source::PineStrategyHost",
        "pineforge::source::PineStrategyConfig",
        "pineforge::source::StrategyOverrides",
        "pineforge::source::SeriesHighest",
        "pineforge::source::SeriesLowest",
        "pineforge::source::SeriesHighestBars",
        "pineforge::source::SeriesLowestBars",
        "pineforge::source::PineSupertrend",
        "pineforge::source::FirstCallBound",
        "pineforge::source::ta_number",
        "pineforge::source::simple_ta_length",
    ),
)
def test_without_source_rename_preserves_emitter_source_spelling(spelling: str) -> None:
    cpp = spelling + "\ndouble pf_safe_source = 1.0;\nf(pf_safe_source);\n"
    expected = spelling + "\ndouble source = 1.0;\nf(source);\n"
    assert without_source_rename(cpp) == expected


def test_without_source_rename_engine_spelling_cannot_hide_partial_rename() -> None:
    cpp = (
        "#include <pineforge/source/pine_strategy_host.hpp>\n"
        "pineforge::source::PineStrategyConfig cfg;\n"
        "double pf_safe_source = 1.0;\n"
        "f(source);\n"
    )
    with pytest.raises(AssertionError, match="partial source rename"):
        without_source_rename(cpp)
