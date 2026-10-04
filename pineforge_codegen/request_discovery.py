"""The other symbols' data a script requests, listed before it runs.

``transpile_full(...)["requests"]`` names every request site that reads
another symbol's feed, so a host can fetch the bars a run needs before it
starts it. A run supplies another symbol's bars as one feed per (symbol
string, timeframe) and the engine looks a site's feed up byte for byte
(``codegen/emit_top.py``, ``_emit_foreign_security_registration``), so each
entry states both the way registration computes them:

    {"line": 7, "fn": "request.security",
     "symbol":    {"kind": "input", "title": "Other symbol", "default": "BINANCE:ETHUSDT"},
     "timeframe": {"kind": "chart"},
     "lookahead": false, "gaps": false, "ignore_invalid_symbol": false}

The sites are the registrations the C++ makes: ``request.security`` of
another symbol that the support checker lowered onto its feed
(``external_requests.FEED_LOWERING``), one entry per context a helper's call
paths give it (``security_contexts``), with the feed column a footprint reads
(``column``). A site whose value reaches display and alert sinks only is
lowered to ``na`` and reads nothing, and a helper nothing reaches never runs:
neither is listed. A deferred refusal -- another symbol's request whose value
can reach a trade but which registration cannot key before the first bar --
is listed with an ``unresolvable`` symbol: its first read stops the run.
Every other request reads no feed: the chart's symbol, ``request.*`` of
fundamentals (recorded series, not bars) and ``request.security_lower_tf`` of
another symbol (it stops the run where it is evaluated).

Names are expanded as registration expands them
(``_substitute_tf_input_reads``: the expression the C++ renders and the run
evaluates before the first bar), and a ``value`` is computed only over
``ScriptIndex.registration_value``'s grammar -- literals, inputs at their
defaults, ``+ == != and or not``, ternaries and ``ticker.inherit`` /
``standard`` -- so discovery states a value where the run computes the same
one from the same expression.
"""

from __future__ import annotations

from .ast_nodes import (
    BinOp, BoolLiteral, FuncCall, Identifier, MemberAccess, NaLiteral, NumberLiteral,
    StringLiteral, Subscript, Ternary, UnaryOp,
)
from .errors import Phase
from .external_requests import FEED_LOWERING, LOWERING_ANNOTATION, _nodes
from .pine_spelling import is_input_call, pine_string_literal
from .security_contexts import _REGISTRATION_INPUTS, ticker_symbol_arg

# A deferred refusal's lowering (``support_checker._lower_no_data_request``,
# ``external_requests.unpin_requests``).
_UNPINNED_LOWERING = "unpinned"
# The chart's own timeframe where a fold meets ``timeframe.period``.
_CHART = object()
_UNKNOWN = object()
# A folded value longer than any symbol or timeframe is no key.
_MAX_VALUE_CHARS = 1024
_PRECEDENCE = {"or": 1, "and": 2, "==": 3, "!=": 3, "<": 4, "<=": 4, ">": 4, ">=": 4,
               "+": 5, "-": 5, "*": 6, "/": 6, "%": 6}


def request_sites(program) -> list[FuncCall]:
    """The ``request.security`` calls the support checker lowered onto
    another symbol's feed or to a deferred refusal: taken before the passes
    that replace a refusal by its ``na`` (``lower_no_data_requests``) or turn
    a feed back into one (``unpin_requests``), which mark it so."""
    return [node for node in _nodes(program)
            if isinstance(node, FuncCall) and _call_name(node) == ("request", "security")
            and (node.annotations or {}).get(LOWERING_ANNOTATION)
            in (FEED_LOWERING, _UNPINNED_LOWERING)]


def discover_requests(gen, ctx, sites: list[FuncCall]) -> list[dict]:
    """The requests of another symbol's data (module docstring), by line,
    each distinct entry once."""
    entries: list[tuple[tuple, dict]] = []
    calls = {call.sec_id: call for call in ctx.security_calls}
    mutable_reads = set(gen._security_tf_mutable_reads)

    def add(loc, entry: dict) -> None:
        entry = {"line": loc.line if loc is not None else None, "fn": "request.security",
                 **entry}
        where = (loc.line or 0, loc.col or 0) if loc is not None else (0, 0)
        entries.append(((*where, len(entries)), entry))

    budget = getattr(gen, "_budget", None)
    try:
        for info in gen._security_eval_info:
            if not info.get("foreign"):
                continue
            if budget is not None:
                budget.check(phase=Phase.CODEGEN)
            entry = {
                "symbol": _guarded(_symbol, gen, info["symbol_node"]),
                "timeframe": _guarded(_registered_timeframe, gen, info),
                "lookahead": bool(info.get("lookahead_on")),
                "gaps": bool(info.get("gaps_on")),
                "ignore_invalid_symbol": _guarded(_flag, gen, info.get("ignore_invalid_node")),
            }
            column = gen._security_footprint_column(info["sec_id"])
            if column:
                entry["column"] = column
            add(getattr(calls.get(info["sec_id"]), "loc", None), entry)
        for site in sites:
            if (site.annotations or {}).get(LOWERING_ANNOTATION) != _UNPINNED_LOWERING:
                continue
            if budget is not None:
                budget.check(phase=Phase.CODEGEN)
            # The C++ never registered it, so nothing expands its names here:
            # its arguments are listed as written.
            symbol, timeframe = _request_args(site)
            gaps, lookahead = _gaps_lookahead(site)
            ignore = site.kwargs.get("ignore_invalid_symbol")
            add(site.loc, {
                "symbol": {"kind": "unresolvable", "expr": _spell(symbol)},
                "timeframe": _guarded(_timeframe, gen, timeframe, expand=False),
                "lookahead": _barmerge(lookahead, "lookahead_on"),
                "gaps": _barmerge(gaps, "gaps_on"),
                "ignore_invalid_symbol": (
                    False if ignore is None else
                    ignore.value if isinstance(ignore, BoolLiteral) else None),
            })
    finally:
        # Expanding a name registers a reassigned global's read with the
        # first-bar replay; generation is over, so leave its record as it was.
        gen._security_tf_mutable_reads = mutable_reads
    out: list[dict] = []
    for _, entry in sorted(entries, key=lambda item: item[0]):
        if entry not in out:
            out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Symbol and timeframe
# ---------------------------------------------------------------------------

def _guarded(classify, gen, node, **options):
    """``classify(gen, node)``; a shape it does not know is listed as
    computed, with no value: discovery never fails a script that
    transpiled."""
    try:
        return classify(gen, node, **options)
    except Exception:  # noqa: BLE001
        if isinstance(node, dict):
            node = node.get("tf_node")
        return None if classify is _flag else {"kind": "computed", "expr": _spell(node)}


def _symbol(gen, node) -> dict:
    """``literal`` / ``input`` / ``computed`` as registration computes the
    symbol string the run keys its feed on."""
    resolved = _passthrough(_expand(gen, node))
    if isinstance(resolved, StringLiteral):
        return {"kind": "literal", "value": resolved.value}
    if _is_registered_input(resolved):
        return _input(gen, resolved)
    return _computed(gen, node, resolved, timeframe=False)


def _registered_timeframe(gen, info: dict) -> dict:
    """The timeframe a feed site registers with (``_resolve_security_tf``'s
    ``tf`` / ``tf_expr``, ``configure_security_evaluators``)."""
    tf, tf_expr = info.get("tf"), info.get("tf_expr")
    if tf:
        return {"kind": "literal", "value": canonical_timeframe(tf)}
    if tf == "" or tf_expr in ("input_tf_", "script_tf_"):
        return {"kind": "chart"}
    return _timeframe(gen, info.get("tf_node"))


def _timeframe(gen, node, expand: bool = True) -> dict:
    """A timeframe registration expands (``expand``), or as written."""
    if node is None:
        return {"kind": "chart"}
    resolved = _expand(gen, node) if expand else node
    if isinstance(resolved, StringLiteral):
        if resolved.value == "":
            return {"kind": "chart"}
        return {"kind": "literal", "value": canonical_timeframe(resolved.value)}
    if _is_timeframe_period(resolved):
        return {"kind": "chart"}
    if _is_registered_input(resolved, timeframe=True):
        return _input(gen, resolved)
    if not expand:
        return {"kind": "computed", "expr": _spell(node)}
    return _computed(gen, node, resolved, timeframe=True)


def canonical_timeframe(tf: str) -> str:
    """The engine's spelling of a feed's timeframe
    (``canonical_symbol_timeframe``): Pine's bare ``D`` / ``W`` / ``M`` /
    ``S`` folded to ``1D`` / ``1W`` / ``1M`` / ``1S``, any other text kept
    as it is (whole minutes ``"240"``, ``<n>D|W|M|S``; a feed matches no
    other spelling)."""
    return "1" + tf if tf in ("D", "W", "M", "S") else tf


def _input(gen, call: FuncCall) -> dict:
    default = _input_default(gen, call)
    return {"kind": "input", "title": gen._get_input_title(call),
            "default": None if default is _UNKNOWN else default}


def _computed(gen, node, resolved, *, timeframe: bool) -> dict:
    out = {"kind": "computed", "expr": _spell(node)}
    value = _fold(gen, resolved)
    if value is _CHART and timeframe:
        out["value"] = ""
    elif isinstance(value, str):
        out["value"] = canonical_timeframe(value) if timeframe and value else value
    titles: list[str] = []
    for call in _input_calls(gen, resolved):
        title = gen._get_input_title(call)
        if title not in titles:
            titles.append(title)
    if titles:
        out["inputs"] = titles
    return out


def _flag(gen, node) -> bool | None:
    """``ignore_invalid_symbol`` as registration reads it: false when left
    out, else its value at the inputs' defaults (None when unknown)."""
    if node is None:
        return False
    value = _fold(gen, _expand(gen, node))
    return value if isinstance(value, bool) else None


def _expand(gen, node):
    """``node`` with its names expanded as registration expands them."""
    return gen._substitute_tf_input_reads(node, set())


def _passthrough(node):
    """``ticker.inherit`` / ``ticker.standard`` render their symbol argument
    unchanged (``visit_call``)."""
    while (isinstance(node, FuncCall) and _call_name(node) in (
            ("ticker", "inherit"), ("ticker", "standard"))):
        symbol = ticker_symbol_arg(node)
        if symbol is None:
            break
        node = symbol
    return node


def _is_registered_input(node, timeframe: bool = False) -> bool:
    """An input whose value registration reads: ``input.symbol`` /
    ``string`` (``timeframe`` for a timeframe), or ``input()`` of a string."""
    if not is_input_call(node):
        return False
    ns, name = _call_name(node)
    if ns is None:
        default = node.args[0] if node.args else node.kwargs.get("defval")
        return isinstance(default, StringLiteral)
    return name in (_REGISTRATION_INPUTS - {"bool"}) or (timeframe and name == "timeframe")


def _input_default(gen, call: FuncCall):
    default = gen._get_input_default(call)
    if isinstance(default, (StringLiteral, BoolLiteral, NumberLiteral)):
        return default.value
    return _UNKNOWN


def _input_calls(gen, node) -> list[FuncCall]:
    """The input calls ``node`` reads, through the names registration
    expands (a ``switch``'s too), in source order."""
    found: dict[int, FuncCall] = {}
    seen: set[str] = set()
    pending = [node]
    while pending:
        for item in _nodes(pending.pop()):
            if is_input_call(item):
                found.setdefault(id(item), item)
            elif isinstance(item, Identifier) and item.name not in seen:
                seen.add(item.name)
                expanded = _expand(gen, item)
                if expanded is not item:
                    pending.append(expanded)
    return sorted(found.values(), key=lambda call: (call.loc.line, call.loc.col)
                  if call.loc is not None else (0, 0))


def _fold(gen, node):
    """The value at the inputs' defaults of an expression in
    ``ScriptIndex.registration_value``'s grammar, else ``_UNKNOWN``. The
    chart's own symbol strings are the chart's (unknown here);
    ``timeframe.period`` is ``_CHART``."""
    if isinstance(node, (StringLiteral, BoolLiteral)):
        return node.value
    if isinstance(node, NumberLiteral):
        return node.value
    if _is_timeframe_period(node):
        return _CHART
    if isinstance(node, FuncCall):
        if is_input_call(node):
            ns, name = _call_name(node)
            if ns is None or name in _REGISTRATION_INPUTS or name == "timeframe":
                return _input_default(gen, node)
            return _UNKNOWN
        passed = _passthrough(node)
        return _UNKNOWN if passed is node else _fold(gen, passed)
    if isinstance(node, Ternary):
        condition = _fold(gen, node.condition)
        if not isinstance(condition, bool):
            return _UNKNOWN
        return _fold(gen, node.true_val if condition else node.false_val)
    if isinstance(node, UnaryOp):
        operand = _fold(gen, node.operand)
        return (not operand) if node.op == "not" and isinstance(operand, bool) else _UNKNOWN
    if isinstance(node, BinOp) and node.op in ("+", "==", "!=", "and", "or"):
        left = _fold(gen, node.left)
        if left is _UNKNOWN or left is _CHART:
            return _UNKNOWN
        right = _fold(gen, node.right)
        if right is _UNKNOWN or right is _CHART:
            return _UNKNOWN
        if node.op == "+":
            if not (isinstance(left, str) and isinstance(right, str)
                    and len(left) + len(right) <= _MAX_VALUE_CHARS):
                return _UNKNOWN
            return left + right
        if node.op in ("and", "or"):
            if not (isinstance(left, bool) and isinstance(right, bool)):
                return _UNKNOWN
            return (left and right) if node.op == "and" else (left or right)
        if type(left) is not type(right):
            return _UNKNOWN
        return (left == right) if node.op == "==" else (left != right)
    return _UNKNOWN


# ---------------------------------------------------------------------------
# Call shapes and spelling
# ---------------------------------------------------------------------------

def _call_name(node: FuncCall) -> tuple[str | None, str | None]:
    callee = node.callee
    if isinstance(callee, Identifier):
        return None, callee.name
    if isinstance(callee, MemberAccess) and isinstance(callee.object, Identifier):
        return callee.object.name, callee.member
    return None, None


def _is_timeframe_period(node) -> bool:
    return (isinstance(node, MemberAccess) and node.member == "period"
            and isinstance(node.object, Identifier) and node.object.name == "timeframe")


def _request_args(node: FuncCall):
    symbol = node.args[0] if node.args else node.kwargs.get("symbol")
    timeframe = node.args[1] if len(node.args) > 1 else node.kwargs.get("timeframe")
    return symbol, timeframe


def _gaps_lookahead(node: FuncCall):
    gaps = node.kwargs.get("gaps")
    if gaps is None and len(node.args) > 3:
        gaps = node.args[3]
    lookahead = node.kwargs.get("lookahead")
    if lookahead is None and len(node.args) > 4:
        lookahead = node.args[4]
    return gaps, lookahead


def _barmerge(node, on: str) -> bool | None:
    """A ``barmerge.*`` constant's flag; left out is off; None when computed."""
    if node is None:
        return False
    if (isinstance(node, MemberAccess) and isinstance(node.object, Identifier)
            and node.object.name == "barmerge"):
        return node.member == on
    return None


def _spell(node, parent: int = 0, right: bool = False) -> str:
    """``node`` spelled as Pine source (display only)."""
    if node is None:
        return ""
    if isinstance(node, StringLiteral):
        return pine_string_literal(node.value)
    if isinstance(node, BoolLiteral):
        return "true" if node.value else "false"
    if isinstance(node, NumberLiteral):
        return str(node.value)
    if isinstance(node, NaLiteral):
        return "na"
    if isinstance(node, Identifier):
        return node.name
    if isinstance(node, MemberAccess):
        return f"{_spell(node.object, 9)}.{node.member}"
    if isinstance(node, FuncCall):
        args = [_spell(a) for a in node.args]
        args += [f"{key} = {_spell(value)}" for key, value in node.kwargs.items()]
        return f"{_spell(node.callee, 9)}({', '.join(args)})"
    if isinstance(node, Subscript):
        return f"{_spell(node.object, 9)}[{_spell(node.index)}]"
    if isinstance(node, UnaryOp):
        text = f"not {_spell(node.operand, 7)}" if node.op == "not" else (
            f"{node.op}{_spell(node.operand, 7)}")
        return f"({text})" if parent > 7 else text
    if isinstance(node, BinOp):
        level = _PRECEDENCE.get(node.op, 5)
        text = (f"{_spell(node.left, level)} {node.op} "
                f"{_spell(node.right, level, right=True)}")
        return f"({text})" if level < parent or (right and level == parent) else text
    if isinstance(node, Ternary):
        text = (f"{_spell(node.condition, 1)} ? {_spell(node.true_val)} : "
                f"{_spell(node.false_val)}")
        return f"({text})" if parent else text
    return "..."
