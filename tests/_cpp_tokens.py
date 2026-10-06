"""Where a script's text lands in the generated C++: code, a comment or a
string literal (its value read back through the C++ escapes).

A string a script spells may reach the C++ only as the value of a string
literal, or as comment text: never as code.
"""

from __future__ import annotations

_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'",
                   "?": "?", "a": "\a", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def regions(cpp: str) -> list[tuple[str, int, int, str]]:
    """``(kind, start, end, value)`` for every comment and string or character
    literal of ``cpp``; ``kind`` is ``comment``, ``string``, ``char`` or
    ``broken`` (a literal a raw line break ends: no compiler reads it as one).
    ``value`` is a string literal's value, else the region's text."""
    out = []
    i, n = 0, len(cpp)
    while i < n:
        if cpp.startswith("//", i):
            j = cpp.find("\n", i)
            j = n if j < 0 else j
            out.append(("comment", i, j, cpp[i:j]))
            i = j
            continue
        if cpp.startswith("/*", i):
            j = cpp.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(("comment", i, j, cpp[i:j]))
            i = j
            continue
        c = cpp[i]
        if c == '"' or (c == "'" and not (i > 0 and (cpp[i - 1].isalnum() or cpp[i - 1] == "_"))):
            j = i + 1
            value = []
            kind = "string" if c == '"' else "char"
            while j < n:
                d = cpp[j]
                if d == "\\" and j + 1 < n:
                    value.append(_SIMPLE_ESCAPES.get(cpp[j + 1], cpp[j + 1]))
                    j += 2
                    continue
                if d == c:
                    j += 1
                    break
                if d in "\r\n":
                    kind = "broken"
                    break
                value.append(d)
                j += 1
            else:
                kind = "broken"
            out.append((kind, i, j, "".join(value)))
            i = j
            continue
        i += 1
    return out


def code_only(cpp: str) -> str:
    """``cpp`` with every comment and well-formed literal blanked out."""
    parts = []
    last = 0
    for kind, start, end, _value in regions(cpp):
        if kind == "broken":
            continue
        parts.append(cpp[last:start])
        parts.append(" ")
        last = end
    parts.append(cpp[last:])
    return "".join(parts)


def assert_inert(cpp: str, marker: str) -> None:
    """``marker`` (a word inside a script's string) never lands in code, and
    no literal of the C++ is broken by a raw line break."""
    broken = [cpp[start:end][:120] for kind, start, end, _ in regions(cpp) if kind == "broken"]
    assert not broken, f"a string literal ends at a raw line break: {broken}"
    code = code_only(cpp)
    assert marker not in code, (
        "script text reached the C++ as code: "
        + code[max(code.find(marker) - 160, 0):code.find(marker) + 40])
    assert '"' not in code, "a stray quote is left in the C++ code"


def string_values(cpp: str) -> list[str]:
    """The value of every string literal of ``cpp``."""
    return [value for kind, _start, _end, value in regions(cpp) if kind == "string"]


def assert_only_in_strings(cpp: str, marker: str) -> None:
    """Every occurrence of ``marker`` sits inside a well-formed string literal."""
    spans = [(start, end) for kind, start, end, _ in regions(cpp) if kind == "string"]
    at = cpp.find(marker)
    while at >= 0:
        assert any(start < at < end for start, end in spans), (
            "script text left its string literal: " + cpp[max(at - 160, 0):at + 40])
        at = cpp.find(marker, at + 1)
