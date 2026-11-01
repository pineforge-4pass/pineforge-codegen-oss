"""Enum variables use the engine's declared string-table lookup."""

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.cpp_allowlist import check_generated_cpp
from tests._compile import compile_cpp


_ENUM_SOURCE = """//@version=6
strategy("Enum text")
enum Mode
    fast = "Fast mode"
    slow
"""


@pytest.mark.parametrize(
    "declaration",
    (
        # input.enum preserves Mode's identity and fast's ordinal zero.
        'e = input.enum(Mode.fast, "Mode")',
        # A direct member initializer has the same enum identity and ordinal.
        "e = Mode.fast",
    ),
    ids=("enum-input", "enum-member-variable"),
)
def test_enum_variable_to_string_transpiles_and_compiles(declaration: str) -> None:
    cpp = transpile(
        _ENUM_SOURCE + declaration + "\ntext = str.tostring(e)\n"
        'if text == "Fast mode"\n    strategy.entry("L", strategy.long)\n'
    )
    # Two members supply two table entries; e is the lookup's ordinal argument.
    assert "::pineforge::source::pine_enum_str_at(Mode_str_values, 2, e)" in cpp
    # Explicit titles win; a member without a title uses its name.
    assert (
        'static const std::string Mode_str_values[] = '
        '{std::string("Fast mode"), std::string("slow")};'
    ) in cpp
    # The helper's self-contained header must precede every call site.
    assert cpp.startswith("#include <pineforge/source/pine_policy_support.hpp>\n")
    # The fixed include and qualified helper introduce no allowlist findings.
    assert check_generated_cpp(cpp) == []
    compile_cpp(cpp, label=declaration)


def test_enum_without_string_conversion_needs_no_lookup_header() -> None:
    cpp = transpile(
        _ENUM_SOURCE + 'e = input.enum(Mode.fast, "Mode")\n'
        'if e == Mode.fast\n    strategy.entry("L", strategy.long)\n'
    )
    # Comparing enum ordinals needs neither the lookup header nor its function.
    assert "#include <pineforge/source/pine_policy_support.hpp>" not in cpp
    assert "pine_enum_str_at(" not in cpp
