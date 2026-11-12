"""Repairs that let a test compare the current C++ with that of an older codegen.

A test that compares a script's translation with the one a reference commit
gives passes only when the known, deliberate differences are the whole
difference. Each repair here restores one of them in the older text.
"""

from __future__ import annotations

from pineforge_codegen.codegen.tv_string_units import TV_STRING_UNITS_CPP

_CAST = "(int)"
_LENGTH = ".length()"
_SYMINFO_HELPERS = "// --- syminfo derivation helpers (PineForge G2) ---"
# What an operand may be made of outside brackets: a name, a member, a scope.
_PLAIN = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:->"
)


def _operand_end(cpp: str, start: int) -> int | None:
    """The index of the ``.length()`` that ends the operand starting at ``start``.

    The operand is a name or a member chain with calls and subscripts, or a
    bracketed expression; anything else (an operator, a space, a separator
    outside brackets) is not the operand of a ``str.length`` call."""
    depth = 0
    index = start
    while index < len(cpp):
        char = cpp[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth < 0:
                return None
        elif depth == 0:
            if index > start and cpp.startswith(_LENGTH, index):
                return index
            if char not in _PLAIN:
                return None
        index += 1
    return None


def _spell_lengths(cpp: str) -> tuple[str, int]:
    """``cpp`` with each ``(int)operand.length()`` spelled ``_pf_u16_length(operand)``."""
    parts: list[str] = []
    at = 0
    sites = 0
    while True:
        start = cpp.find(_CAST, at)
        if start < 0:
            break
        operand_start = start + len(_CAST)
        end = _operand_end(cpp, operand_start)
        if end is None:
            parts.append(cpp[at:operand_start])
            at = operand_start
            continue
        operand, inner = _spell_lengths(cpp[operand_start:end])
        parts.append(cpp[at:start] + "_pf_u16_length(" + operand + ")")
        sites += 1 + inner
        at = end + len(_LENGTH)
    parts.append(cpp[at:])
    return "".join(parts), sites


def with_utf16_units(cpp: str) -> str:
    """The C++ of a codegen older than the UTF-16 unit helpers, spelled as the
    current one spells a script that calls ``str.length``.

    The older emission counted ``str.length`` through ``(int)text.length()`` and
    carried no unit helpers. The calls become calls of the helper and the helper
    block is added before the syminfo helpers, where the current emitter puts
    it. A text without such a call is returned as it is."""
    spelled, sites = _spell_lengths(cpp)
    if not sites:
        return cpp
    assert spelled.count(_SYMINFO_HELPERS) == 1, spelled
    return spelled.replace(_SYMINFO_HELPERS, TV_STRING_UNITS_CPP + "\n\n" + _SYMINFO_HELPERS)
