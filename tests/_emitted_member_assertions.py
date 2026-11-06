"""Assertions for scalar storage in emitted strategy members."""

import re


def assert_int_member(cpp: str, name: str) -> str:
    """Require one exact int member declaration and return its full line."""
    return assert_member_type(cpp, name, "int")


def assert_member_type(cpp: str, name: str, expected_type: str) -> str:
    """Require one exact member declaration of ``expected_type`` and return its
    full line."""
    members = re.search(
        r"(?ms)^class GeneratedStrategy : public pineforge::source::PineStrategyHost \{\n"
        r"public:\n(.*?)(?=^    (?:struct\b|explicit GeneratedStrategy\())",
        cpp,
    )
    assert members is not None, "GeneratedStrategy member section not found"
    # Stop before nested checkpoint storage or methods, and exclude comments.
    member_text = re.sub(r"/\*.*?\*/|//[^\n]*", "", members.group(1), flags=re.S)
    declarations = list(re.finditer(
        rf"(?m)^    (?P<type>\S+) {re.escape(name)} = [^;\n]+;$",
        member_text,
    ))
    assert len(declarations) == 1, (
        f"Expected one declaration of {name} in GeneratedStrategy, "
        f"got {len(declarations)}"
    )
    declaration = declarations[0]
    actual_type = declaration.group("type")
    assert actual_type == expected_type, (
        f"Expected {expected_type} {name}, got {actual_type} {name} "
        "in GeneratedStrategy"
    )
    return declaration.group(0)
