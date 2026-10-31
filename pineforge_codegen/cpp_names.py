"""ASCII fragments for generated identifiers, before collision allocation."""

from __future__ import annotations


def cpp_identifier_fragment(text: str) -> str:
    """Keep ASCII identifier spellings; encode other Unicode code points.

    Keywords are safe inside the caller's prefixed identifier. The caller
    still allocates the complete name against authored and generated names.
    """
    return "".join(
        char if ("a" <= char <= "z" or "A" <= char <= "Z"
                 or (index > 0 and "0" <= char <= "9") or char == "_")
        else f"_u{ord(char):06x}_"
        for index, char in enumerate(text)
    ) or "pf_empty"
