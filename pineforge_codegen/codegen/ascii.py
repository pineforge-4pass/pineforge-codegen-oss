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
_TOKENS = re.compile(
    r"(?P<comment>//[^\r\n]*|/\*.*?(?:\*/|\Z))"
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
    """
    if cpp.isascii():
        return cpp

    identifiers = {
        match.group()
        for match in _TOKENS.finditer(cpp)
        if match.lastgroup == "identifier"
    }
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
        return text if text.isascii() else _escape_utf8(text)

    return _TOKENS.sub(replace, cpp)
