"""ASCII spelling of the completed C++ translation unit.

Keep the compiler's internal names and string values intact until emission is
complete: generated history, callable and library names can contain an authored
name, and settings metadata reads the emitter's original string spelling.
"""

from __future__ import annotations

from collections.abc import Iterable
import re

from .helpers import BUILTIN_ACCESSOR_NAMES, CPP_RESERVED, is_emitter_temporary


# These are the tokens emitted by codegen, including ordinary character
# literals. Comments and quoted text must be consumed before identifiers:
# a name inside either is data, even when it spells a bound identifier.
# Consume preprocessing numbers before character literals so a digit separator
# cannot pair with a later apostrophe and hide the intervening code.
_TOKENS = re.compile(
    r"(?P<comment>//[^\r\n]*|/\*.*?(?:\*/|\Z))"
    r"|(?P<number>(?:[0-9]|\.[0-9])(?:[eEpP][+-]|[\w.]|'[\w])*)"
    r"""|(?P<prefixed>(?:u8|[LuU])?R(?=")|(?:u8|[LuU])(?=["']))"""
    r"""|(?P<quoted>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')"""
    r"|(?P<identifier>[^\W\d]\w*)",
    re.DOTALL,
)


def _escape_utf8(text: str) -> str:
    """Spell non-ASCII UTF-8 bytes as fixed-width C++ octal escapes.

    Three digits terminate each escape even before an authored digit. ASCII
    escapes already emitted for quotes, backslashes, whitespace and NUL remain
    byte-identical. In comments the same spelling makes the text visible
    without introducing a comment delimiter or a line splice.
    """
    return "".join(
        ch if ch.isascii() else "".join(f"\\{byte:03o}" for byte in ch.encode("utf-8"))
        for ch in text
    )


def ascii_cpp(cpp: str, *, reserved: Iterable[str] = ()) -> str:
    """Encode non-ASCII tokens without changing any existing ASCII token.

    UTF-8 hex is injective. Reserve the complete translation unit's identifiers,
    all authored names (including unused ones), and engine/emitter names before
    allocating encodings in sorted order. A suffix avoids an ASCII author
    deliberately spelling an encoding; allocated names are reserved too.

    Prefixed literals and non-ASCII character literals have no byte-preserving
    rewrite here. Those forms, or any non-ASCII code left after substitution,
    violate emitter invariants and raise AssertionError.
    """
    if cpp.isascii():
        return cpp

    identifiers = set()
    for match in _TOKENS.finditer(cpp):
        text = match.group()
        if match.lastgroup == "prefixed":
            line = cpp.count("\n", 0, match.start()) + 1
            raise AssertionError(f"unexpected prefixed C++ literal at line {line}")
        if (match.lastgroup == "quoted" and text.startswith("'")
                and not text.isascii()):
            line = cpp.count("\n", 0, match.start()) + 1
            raise AssertionError(f"non-ASCII C++ character literal at line {line}")
        if match.lastgroup == "identifier":
            identifiers.add(text)
    occupied = identifiers | set(reserved) | CPP_RESERVED | BUILTIN_ACCESSOR_NAMES
    names: dict[str, str] = {}
    for name in sorted(identifiers):
        if name.isascii():
            continue
        base = f"pf_utf8_{name.encode('utf-8').hex()}"
        candidate = base
        suffix = 2
        while candidate in occupied or is_emitter_temporary(candidate):
            candidate = f"{base}_{suffix}"
            suffix += 1
        names[name] = candidate
        occupied.add(candidate)

    def replace(match: re.Match[str]) -> str:
        text = match.group()
        if match.lastgroup == "identifier":
            return names.get(text, text)
        if match.lastgroup == "number":
            return text
        return text if text.isascii() else _escape_utf8(text)

    result = _TOKENS.sub(replace, cpp)
    if not result.isascii():
        first = next(index for index, ch in enumerate(result) if not ch.isascii())
        line = result.count("\n", 0, first) + 1
        raise AssertionError(
            f"non-ASCII C++ code point U+{ord(result[first]):04X} at line {line}"
        )
    return result
