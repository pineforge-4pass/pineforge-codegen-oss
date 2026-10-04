"""Stable codes and named arguments for every transpile diagnostic.

Every :class:`~pineforge_codegen.errors.Diagnostic` carries a ``code``
(``PF-E1203`` for an error, ``PF-W0412`` for a warning) and ``args``, the
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
UNCATALOGUED = {"error": "PF-E0000", "warning": "PF-W0000"}


@lru_cache(maxsize=1)
def _load() -> dict:
    with CATALOG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def diagnostics_catalog() -> dict:
    """The diagnostics catalog: ``{"schema", "codes": {code: entry}}``.

    Each entry gives ``severity`` (``error`` / ``warning``), ``area``, the
    English ICU MessageFormat ``message`` template, the ``hint`` template or
    ``None``, a one-line ``explanation`` and ``args``: per argument name its
    ``kind`` (``identifier``, ``type``, ``keyword``, ``number``, ``vocab`` or
    ``text``) and, for ``vocab``, its closed set of ``values``. A fresh copy
    is returned on every call.
    """
    return json.loads(json.dumps(_load()))


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

# Joins a message and its hint into one subject, so an argument both name is
# one value (a backreference). No template spells it.
_JOIN = "\x00"
_CANONICAL_INT = re.compile(r"-?(?:0|[1-9][0-9]*)")
_CANONICAL_FLOAT = re.compile(r"-?(?:0|[1-9][0-9]*)\.[0-9]+")


class _Matcher:
    __slots__ = ("code", "has_hint", "prefix", "suffix", "specificity",
                 "_parts", "_regex", "_kinds")

    def __init__(self, code: str, entry: dict):
        self.code = code
        message = parse_template(entry["message"])
        hint = entry.get("hint")
        self.has_hint = hint is not None
        self._parts = message + ([_JOIN] + parse_template(hint) if hint is not None else [])
        self.prefix = message[0] if message and isinstance(message[0], str) else ""
        self.suffix = message[-1] if message and isinstance(message[-1], str) else ""
        self.specificity = sum(len(p) for p in self._parts if isinstance(p, str))
        self._regex = None
        self._kinds = {name: spec.get("kind") for name, spec in entry.get("args", {}).items()}

    def match(self, subject: str) -> dict | None:
        if self._regex is None:
            pieces: list[str] = []
            seen: set[str] = set()
            for part in self._parts:
                if isinstance(part, str):
                    pieces.append(re.escape(part))
                elif part[0] in seen:
                    pieces.append(f"(?P={part[0]})")
                else:
                    seen.add(part[0])
                    pieces.append(f"(?P<{part[0]}>.*?)")
            self._regex = re.compile("".join(pieces), re.DOTALL)
        found = self._regex.fullmatch(subject)
        if found is None:
            return None
        args: dict[str, Any] = {}
        for name, value in found.groupdict().items():
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
    by_severity: dict[str, list[_Matcher]] = {"error": [], "warning": []}
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

    ``severity`` is ``"error"`` or ``"warning"``. A text no catalog template
    renders gets ``PF-E0000`` / ``PF-W0000`` with its text as ``args``.
    """
    subject = message if hint is None else message + _JOIN + hint
    for matcher in _matchers().get(severity, ()):
        if matcher.has_hint != (hint is not None):
            continue
        if not message.startswith(matcher.prefix) or not message.endswith(matcher.suffix):
            continue
        args = matcher.match(subject)
        if args is not None:
            return matcher.code, args
    args = {"message": message}
    if hint is not None:
        args["hint"] = hint
    code = UNCATALOGUED.get(severity, UNCATALOGUED["error"])
    return code, args
