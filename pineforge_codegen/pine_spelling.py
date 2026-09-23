"""Pine source spellings that the analyzer hands the codegen as strings.

TA constructor arguments (``TACallSite.ctor_args``), user-function call-site
arguments and class-scope derived lengths travel as Pine source text that the
codegen scans for identifiers, substitutes into, folds and re-parses. An
inline ``input.*()`` call is a legitimate leaf of such an expression
(``ta.ema(close, input.int(9, "fast"))``), and its own argument text -- the
title string, keyword names, an ``options`` list -- is input metadata, not
part of the expression. These helpers keep string literals intact and let a
caller treat each inline input call as one leaf.
"""

from __future__ import annotations

import re
from typing import Callable

from .ast_nodes import (
    BoolLiteral, FuncCall, Identifier, MemberAccess, NaLiteral, NumberLiteral,
    StringLiteral, TupleLiteral, UnaryOp,
)

_STRING_OR_IDENT = re.compile(
    r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[A-Za-z_][A-Za-z_0-9]*'
)


def is_input_call(node) -> bool:
    """``input(...)`` or ``input.<type>(...)``, judged on the spelling alone."""
    if not isinstance(node, FuncCall):
        return False
    callee = node.callee
    if isinstance(callee, Identifier):
        return callee.name == "input"
    return (isinstance(callee, MemberAccess)
            and isinstance(callee.object, Identifier)
            and callee.object.name == "input")


def pine_string_literal(value: str) -> str:
    """The double-quoted literal the lexer reads back as ``value``."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def spell_input_call(node: FuncCall) -> str | None:
    """An input call spelled with every argument, keywords included, so the
    text re-parses to the same call. None when an argument is not one of the
    constant shapes an input takes (literal, name, ``display.none``-style
    member, signed number, ``options`` list)."""
    parts = [_spell_input_arg(a) for a in node.args]
    parts += [None if (v := _spell_input_arg(value)) is None else f"{key}={v}"
              for key, value in node.kwargs.items()]
    callee = _spell_input_arg(node.callee)
    if callee is None or None in parts:
        return None
    return f"{callee}({', '.join(parts)})"


def _spell_input_arg(node) -> str | None:
    if isinstance(node, NumberLiteral):
        return str(node.value)
    if isinstance(node, StringLiteral):
        return pine_string_literal(node.value)
    if isinstance(node, BoolLiteral):
        return "true" if node.value else "false"
    if isinstance(node, NaLiteral):
        return "na"
    if isinstance(node, Identifier):
        return node.name
    if isinstance(node, MemberAccess):
        obj = _spell_input_arg(node.object)
        return None if obj is None else f"{obj}.{node.member}"
    if isinstance(node, UnaryOp) and node.op in ("-", "+"):
        operand = _spell_input_arg(node.operand)
        return None if operand is None else f"{node.op}{operand}"
    if isinstance(node, TupleLiteral):
        elems = [_spell_input_arg(e) for e in node.elements]
        return None if None in elems else "[" + ", ".join(elems) + "]"
    return None


def sub_identifiers(text: str, repl: Callable[[re.Match], str]) -> str:
    """``re.sub`` over identifier tokens only; string literals pass through."""
    def _one(match: re.Match) -> str:
        if match.group(0)[0] in "\"'":
            return match.group(0)
        return repl(match)
    return _STRING_OR_IDENT.sub(_one, text)


def input_call_spans(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` of each ``input(...)`` / ``input.<type>(...)`` call in
    ``text``. Parentheses inside string literals do not count, and a call
    nested in another input call's arguments is part of the outer span."""
    spans: list[tuple[int, int]] = []
    if "input" not in text:
        return spans
    for match in _STRING_OR_IDENT.finditer(text):
        start = match.start()
        if match.group(0) != "input" or (spans and start < spans[-1][1]):
            continue
        if start > 0 and text[start - 1] == ".":
            continue  # a member named ``input``, not the namespace
        pos = match.end()
        member = re.match(r"\.[A-Za-z_][A-Za-z_0-9]*", text[pos:])
        if member is not None:
            pos += member.end()
        if pos >= len(text) or text[pos] != "(":
            continue
        end = _matching_paren(text, pos)
        if end is not None:
            spans.append((start, end))
    return spans


def _matching_paren(text: str, open_pos: int) -> int | None:
    """Index just past the ``)`` closing the ``(`` at ``open_pos``."""
    depth = 0
    pos = open_pos
    while pos < len(text):
        ch = text[pos]
        if ch in "\"'":
            literal = _STRING_OR_IDENT.match(text, pos)
            if literal is None:
                return None  # unterminated string literal
            pos = literal.end()
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return pos + 1
        pos += 1
    return None
