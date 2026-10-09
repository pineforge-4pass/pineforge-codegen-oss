"""Stable codes and named arguments for every transpile diagnostic.

Every :class:`~pineforge_codegen.errors.Diagnostic` carries a ``code``
(``PF-E1203`` / ``PF-W0412``) and ``args``, the
named data its English ``message`` and ``hint`` were built from. The codes,
their severities, English ICU MessageFormat templates and one-line
explanations live in ``diagnostics_catalog.json`` beside this module, which
ships with the package and is returned by :func:`diagnostics_catalog`.

The emitters keep spelling their English text as they always have, so the
``message`` stays byte for byte what it was; a diagnostic is coded by the
catalog template its text renders from (:func:`classify`). The template's
literal text must equal the message's around its arguments, so a code is
never guessed: a text no template renders gets the uncatalogued code of its
severity (``PF-E0000`` / ``PF-W0000``), which the test suite refuses.

The match never backtracks over the text's characters: each literal segment
of a template goes to its leftmost place after the previous one, the last to
the text's end, and every argument is the text between its literals -- the
split a fullmatch of the template with lazy ``(.*?)`` arguments returns. Where
an argument is named twice (``{receiver}`` in the message and the hint) the
leftmost split can name it two values; then the later places of a literal are
tried in order, as the regex's backtracking tries them, within a fixed budget
of steps. The regex took seconds, then minutes, as a crafted message grew,
and a user's script spells argument text.

Rendering (:func:`render`) is the ICU MessageFormat subset the catalog uses:
literal text with ICU apostrophe quoting (``''`` is one apostrophe, ``'{'``
a literal brace) and simple ``{name}`` arguments, a string argument
inserted as is and a number in plain decimal digits.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("diagnostics_catalog.json")
CATALOG_SCHEMA = "pineforge-diagnostics-catalog/v1"
# Notes keep historical PF-W identities. The nonfatal fallback is shared;
# its catalog severity remains warning, while a Diagnostic retains its level.
UNCATALOGUED = {"error": "PF-E0000", "warning": "PF-W0000", "note": "PF-W0000"}


@lru_cache(maxsize=1)
def _load() -> dict:
    with CATALOG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def diagnostics_catalog() -> dict:
    """The diagnostics catalog: ``{"schema", "codes": {code: entry}}``.

    Each entry gives ``severity`` (``error`` / ``warning`` / ``note``), ``area``, the
    English ICU MessageFormat ``message`` template, the ``hint`` template or
    ``None``, a one-line ``explanation`` and ``args``: per argument name its
    ``kind`` (``identifier``, ``type``, ``keyword``, ``number``, ``vocab`` or
    ``text``) and, for ``vocab``, its closed set of ``values``. ``user_message``
    is a short ICU template over those same arguments, not rendered English.
    A fresh copy is returned on every call.
    """
    return json.loads(json.dumps(_load()))


def _user_message_template(code: str) -> str:
    """The short presentation template; never classify from or render it here."""
    return _load()["codes"][code]["user_message"]


# ---------------------------------------------------------------------------
# ICU MessageFormat subset
# ---------------------------------------------------------------------------

_SPECIAL = "{}"


def parse_template(template: str) -> list:
    """Split a catalog template into literal strings and ``(name,)`` argument
    tuples, applying ICU's apostrophe rules (``ApostropheMode.DOUBLE_OPTIONAL``,
    the ICU and FormatJS default)."""
    parts: list = []
    text: list[str] = []
    i, n = 0, len(template)
    while i < n:
        ch = template[i]
        if ch == "'":
            if i + 1 < n and template[i + 1] == "'":
                text.append("'")
                i += 2
                continue
            if i + 1 < n and template[i + 1] in _SPECIAL:
                # A quoted literal runs to the next lone apostrophe.
                i += 1
                while i < n:
                    if template[i] == "'":
                        if i + 1 < n and template[i + 1] == "'":
                            text.append("'")
                            i += 2
                            continue
                        i += 1
                        break
                    text.append(template[i])
                    i += 1
                continue
            text.append("'")
            i += 1
            continue
        if ch == "{":
            end = template.find("}", i)
            if end < 0:
                raise ValueError(f"unclosed argument in template: {template!r}")
            name = template[i + 1:end].strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError(f"unsupported argument {name!r} in template: {template!r}")
            if text:
                parts.append("".join(text))
                text = []
            parts.append((name,))
            i = end + 1
            continue
        if ch == "}":
            raise ValueError(f"unbalanced '}}' in template: {template!r}")
        text.append(ch)
        i += 1
    if text:
        parts.append("".join(text))
    return parts


def escape_literal(text: str) -> str:
    """ICU-escape literal text: every apostrophe doubled, every brace quoted."""
    out: list[str] = []
    for ch in text:
        if ch == "'":
            out.append("''")
        elif ch in _SPECIAL:
            out.append("'" + ch + "'")
        else:
            out.append(ch)
    return "".join(out)


def format_arg(value: Any) -> str:
    """An argument's text: a string as is, a number in plain decimal digits."""
    if isinstance(value, bool):
        raise TypeError("diagnostic arguments are strings or numbers")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return value
    raise TypeError(f"diagnostic argument of type {type(value).__name__}")


def render(template: str | None, args: dict) -> str | None:
    """Render a catalog template with a diagnostic's ``args``."""
    if template is None:
        return None
    return "".join(
        part if isinstance(part, str) else format_arg(args[part[0]])
        for part in parse_template(template)
    )


def render_diagnostic(code: str, args: dict) -> tuple[str, str | None]:
    """The English ``(message, hint)`` a code renders with ``args``."""
    entry = _load()["codes"][code]
    return render(entry["message"], args), render(entry.get("hint"), args)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

_CANONICAL_INT = re.compile(r"-?(?:0|[1-9][0-9]*)")
_CANONICAL_FLOAT = re.compile(r"-?(?:0|[1-9][0-9]*)\.[0-9]+")


def _segments(parts: list) -> tuple[str, tuple[tuple[str, str], ...]]:
    """A parsed template as its leading literal and ``(argument, literal after
    it)`` pairs; the literal after an argument may be empty."""
    head = ""
    pairs: list[list[str]] = []
    for part in parts:
        if isinstance(part, str):
            if pairs:
                pairs[-1][1] += part
            else:
                head += part
        else:
            pairs.append([part[0], ""])
    return head, tuple((name, literal) for name, literal in pairs)


# Literal places a template whose argument is named twice may try, per
# classification: a script's text never needs more than a few, and a crafted
# one gets the uncatalogued code instead of a long search.
_SEARCH_BUDGET = 4096


def _search(segments: list, texts: list[str], repeats: bool) -> dict[str, str] | None:
    """Split ``texts`` (the message, then the hint) by their templates'
    ``segments``: each argument ends at the leftmost place of the literal after
    it, the last argument of a text at its end -- the split a lazy-regex
    fullmatch returns. Without an argument named twice (``repeats``) that split
    succeeds whenever any does, since a leftmost place leaves the rest the most
    room: it is the only one tried, so the work is linear. With one, a split can
    give the name two values; then the later places of a literal are tried in
    order, as the regex backtracks, within ``_SEARCH_BUDGET`` places."""
    starts: list[int] = []
    ends: list[int] = []
    slots: list[tuple[int, str, str, bool]] = []   # text, argument, literal after it, last
    for index, ((head, pairs), text) in enumerate(zip(segments, texts)):
        if not text.startswith(head):
            return None
        if not pairs:
            if len(text) != len(head):
                return None
            starts.append(len(head))
            ends.append(len(head))
            continue
        tail = pairs[-1][1]
        end = len(text) - len(tail)
        if end < len(head) or not text.endswith(tail):
            return None
        starts.append(len(head))
        ends.append(end)
        for at, (name, literal) in enumerate(pairs):
            slots.append((index, name, literal, at == len(pairs) - 1))
    found: dict[str, str] = {}
    budget = [_SEARCH_BUDGET]

    def step(slot: int, pos: int) -> bool:
        if slot == len(slots):
            return True
        text_index, name, literal, last = slots[slot]
        text, end = texts[text_index], ends[text_index]
        following = slot + 1
        known = found.get(name)
        if last:
            value = text[pos:end]
            resume = (starts[slots[following][0]] if following < len(slots) else end)
            if known is not None:
                return known == value and step(following, resume)
            found[name] = value
            if step(following, resume):
                return True
            del found[name]
            return False
        if known is not None:
            stop = pos + len(known)
            return (text.startswith(known, pos) and stop + len(literal) <= end
                    and text.startswith(literal, stop) and step(following, stop + len(literal)))
        at = text.find(literal, pos, end)
        while at >= 0:
            budget[0] -= 1
            if budget[0] < 0:
                return False
            found[name] = text[pos:at]
            if step(following, at + len(literal)):
                return True
            del found[name]
            if not repeats:
                return False
            at = text.find(literal, at + 1, end) if at < end else -1
        return False

    first = starts[slots[0][0]] if slots else 0
    return found if step(0, first) else None


class _Matcher:
    __slots__ = ("code", "has_hint", "prefix", "suffix", "specificity",
                 "_message", "_hint", "_kinds", "_repeats")

    def __init__(self, code: str, entry: dict):
        self.code = code
        message = parse_template(entry["message"])
        hint = entry.get("hint")
        hint_parts = parse_template(hint) if hint is not None else []
        self.has_hint = hint is not None
        self._message = _segments(message)
        self._hint = _segments(hint_parts) if hint is not None else None
        self.prefix = message[0] if message and isinstance(message[0], str) else ""
        self.suffix = message[-1] if message and isinstance(message[-1], str) else ""
        # The text a template spells itself; the hint's separator counted as one
        # character, as the order of the catalog's codes has always assumed.
        self.specificity = (sum(len(p) for p in message + hint_parts if isinstance(p, str))
                            + (1 if hint is not None else 0))
        self._kinds = {name: spec.get("kind") for name, spec in entry.get("args", {}).items()}
        names = [part[0] for part in message + hint_parts if isinstance(part, tuple)]
        self._repeats = len(names) != len(set(names))

    def match(self, message: str, hint: str | None) -> dict | None:
        segments = [self._message] + ([self._hint] if self._hint is not None else [])
        texts = [message] + ([hint] if self._hint is not None else [])
        found = _search(segments, texts, self._repeats)
        if found is None:
            return None
        args: dict[str, Any] = {}
        for name, value in found.items():
            if self._kinds.get(name) == "number":
                if _CANONICAL_INT.fullmatch(value):
                    args[name] = int(value)
                    continue
                if _CANONICAL_FLOAT.fullmatch(value) and repr(float(value)) == value:
                    args[name] = float(value)
                    continue
            args[name] = value
        return args


@lru_cache(maxsize=1)
def _matchers() -> dict[str, list[_Matcher]]:
    by_severity: dict[str, list[_Matcher]] = {"error": [], "warning": [], "note": []}
    for code, entry in _load()["codes"].items():
        if code in UNCATALOGUED.values():
            continue
        by_severity[entry["severity"]].append(_Matcher(code, entry))
    for matchers in by_severity.values():
        # The template with the most literal text wins: a message two templates
        # render reads as the more specific one (both render it byte for byte).
        matchers.sort(key=lambda m: (-m.specificity, m.code))
    return by_severity


def classify(severity: str, message: str, hint: str | None = None) -> tuple[str, dict]:
    """The ``(code, args)`` of a diagnostic's English text.

    ``severity`` is ``"error"``, ``"warning"`` or ``"note"``; the catalog's
    declared severity selects the templates, never a code's prefix. A text
    no template renders gets ``PF-E0000`` for an error or ``PF-W0000`` for
    a warning/note, with its text as ``args`` and no new fallback identity.
    """
    for matcher in _matchers().get(severity, ()):
        if matcher.has_hint != (hint is not None):
            continue
        if not message.startswith(matcher.prefix) or not message.endswith(matcher.suffix):
            continue
        args = matcher.match(message, hint)
        if args is not None:
            return matcher.code, args
    args = {"message": message}
    if hint is not None:
        args["hint"] = hint
    code = UNCATALOGUED.get(severity, UNCATALOGUED["error"])
    return code, args
