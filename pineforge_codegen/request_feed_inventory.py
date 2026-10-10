"""The request-feed inventory a compiled strategy carries (interface v1).

A selected-window run decides, before it opens a feed or loads the strategy
library, whether every timeframe the strategy requests lies inside the build's
policy. ``build_request_feed_inventory`` is the producer half: for the C++ the
pinned transpiler generated it lists every bar request the compiled strategy
can make, and ``bind_request_feed_inventory`` binds that list to the linked
artifact. One JSON object, exactly these keys::

    {"schema": "pineforge-request-feed-inventory/v1",
     "source_sha256": "<64 lowercase hex of the generated C++'s UTF-8 bytes>",
     "artifact_sha256": null,          # filled after linking, by the bind helper
     "primary_chart_timeframe": null,  # the compile caller's chart binding
     "entries": [{"kind": "token", "timeframe": "D"},
                 {"kind": "input", "key": "HTF", "default": "240"},
                 {"kind": "unknown"}]}

``entries`` holds one member per request site, in source order (line, column,
then registration order). An empty list is complete; an ``unknown`` entry makes
the inventory incomplete. No expression is executed to resolve an inventory: a
timeframe is a token only where it is written, or where the codegen already
folds it at compile time to a string constant, and an input reference names the
manifest key (``codegen.input._get_input_title``) and the declaration's string
default; anything else, a computed expression and a default folded from an
input-dependent computation included, is ``unknown``.

What is listed, so that none is dropped silently:

* every registered request context that is not flagged dead
  (``CodeGen._security_eval_info``): the chart's own symbol, another symbol's
  feed and ``request.security_lower_tf`` alike, one per context a helper's call
  paths give it (``security_contexts``) and per call-site clone. A helper no
  top-level statement reaches whose request registers all the same (it is not
  flagged dead) is listed too, where ``request_discovery`` leaves it out: a
  superset only makes the inventory refuse more;
* every deferred refusal ``request_discovery.request_sites`` kept (another
  symbol's request registration cannot key before the first bar), by the
  timeframe as written;
* every other reachable ``request.security`` / ``request.security_lower_tf``
  call in the analyzed program that neither of those accounts for, as
  ``unknown`` (a request the pipeline did not classify is incomplete, never
  omitted).

Not listed: a request lowered to ``na`` (its value reaches display sinks only:
the C++ makes no request), a request of a helper nothing reaches that
registration flags dead (it registers the chart's own timeframe and is never
read) and the recorded fundamentals requests (``request.financial`` /
``earnings`` / ``dividends`` / ``splits``: series recorded per chart bar, no
timeframe feed).

A request that depends on the chart's own timeframe (an empty string,
``timeframe.period``) is a token only when the compile caller passes
``primary_chart_timeframe``: the inventory records that binding and the
admission requires it to equal the request's primary token. Without it the
entry is ``unknown``.

Wire tokens (:func:`canonical_wire_timeframe`): a positive whole-minute decimal
without leading zeros; ``D`` / ``1D`` is ``D`` and ``W`` / ``1W`` is ``W``;
bare ``M`` / ``S`` are ``1M`` / ``1S``; any other positive ``nD`` / ``nW`` /
``nM`` / ``nS`` stays as written. Whitespace, an empty string, a leading zero,
a decimal and every other spelling (``1H`` included: the interface gives it no
wire spelling) are no token and the entry is ``unknown``.
"""

from __future__ import annotations

import hashlib
import re

from .external_requests import LOWERING_ANNOTATION
from .request_discovery import (
    _UNPINNED_LOWERING, _guarded, _registered_timeframe, _request_args, _timeframe, _within,
)
from .security_contexts import ScriptIndex, _reached, _request_payload

SCHEMA = "pineforge-request-feed-inventory/v1"

_KEYS = frozenset({"schema", "source_sha256", "artifact_sha256",
                   "primary_chart_timeframe", "entries"})
_KEY_ORDER = ("schema", "source_sha256", "artifact_sha256",
              "primary_chart_timeframe", "entries")
# ``[0-9]`` and no flags: a fullwidth digit, a trailing newline or a lowercase
# unit is no token.
_WIRE_TOKEN = re.compile(r"(?:([1-9][0-9]*)([DWMS])?|([DWMS]))")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_BYTES_LIKE = (bytes, bytearray, memoryview)


class RequestFeedInventoryError(ValueError):
    """An inventory input the producer refuses: a primary chart timeframe that
    is no wire token, source text that is not text, or an inventory the bind
    helper cannot validate against the bytes it is given."""


def canonical_wire_timeframe(text) -> str | None:
    """``text`` as the inventory's wire token, or None when it is none."""
    if type(text) is not str:
        return None
    matched = _WIRE_TOKEN.fullmatch(text)
    if matched is None:
        return None
    digits, unit, bare = matched.groups()
    if bare is not None:
        return bare if bare in ("D", "W") else "1" + bare
    if unit is None:
        return digits
    if digits == "1" and unit in ("D", "W"):
        return unit
    return digits + unit


def build_request_feed_inventory(gen, ctx, sites, cpp, *, primary_chart_timeframe=None) -> dict:
    """The unbound inventory (``artifact_sha256`` null) of the strategy ``gen``
    generated ``cpp`` for (module docstring). ``sites`` are the requests the
    support checker kept (``_generate``'s fifth result); ``ctx`` and ``gen``
    are the analyzer's and the codegen's, read and left as they were."""
    if type(cpp) is not str:
        raise RequestFeedInventoryError("cpp is not the generated C++ source text")
    chart = None
    if primary_chart_timeframe is not None:
        chart = canonical_wire_timeframe(primary_chart_timeframe)
        if chart is None:
            raise RequestFeedInventoryError(
                f"primary_chart_timeframe {primary_chart_timeframe!r} is not a wire timeframe token")
    return {
        "schema": SCHEMA,
        "source_sha256": hashlib.sha256(cpp.encode("utf-8")).hexdigest(),
        "artifact_sha256": None,
        "primary_chart_timeframe": chart,
        "entries": _inventory_entries(gen, ctx, list(sites or ()), chart),
    }


def bind_request_feed_inventory(inventory, cpp_bytes, artifact_bytes) -> dict:
    """A copy of the unbound ``inventory`` with ``artifact_sha256`` filled from
    the linked artifact's bytes, once its shape is exact and its
    ``source_sha256`` is that of ``cpp_bytes``. Reads no file and loads no
    library; the trusted compile pipeline calls it right after linking."""
    _check_shape(inventory)
    if not isinstance(cpp_bytes, _BYTES_LIKE) or not isinstance(artifact_bytes, _BYTES_LIKE):
        raise RequestFeedInventoryError("cpp_bytes and artifact_bytes must be bytes")
    if inventory["artifact_sha256"] is not None:
        raise RequestFeedInventoryError(
            "inventory artifact_sha256 is already set; only an unbound inventory can be bound")
    if hashlib.sha256(cpp_bytes).hexdigest() != inventory["source_sha256"]:
        raise RequestFeedInventoryError(
            "inventory source_sha256 does not match the supplied C++ bytes")
    return {
        "schema": inventory["schema"],
        "source_sha256": inventory["source_sha256"],
        "artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "primary_chart_timeframe": inventory["primary_chart_timeframe"],
        "entries": [_copy_entry(entry) for entry in inventory["entries"]],
    }


# ---------------------------------------------------------------------------
# Binding: exact shape
# ---------------------------------------------------------------------------

def _is_hex64(value) -> bool:
    return type(value) is str and _HEX64.fullmatch(value) is not None


def _is_wire_token(value) -> bool:
    return type(value) is str and canonical_wire_timeframe(value) == value


def _check_shape(inventory) -> None:
    if not isinstance(inventory, dict) or set(inventory) != _KEYS:
        raise RequestFeedInventoryError(
            "inventory is not an object with exactly the keys " + ", ".join(_KEY_ORDER))
    if type(inventory["schema"]) is not str or inventory["schema"] != SCHEMA:
        raise RequestFeedInventoryError(f"inventory schema is not {SCHEMA}")
    if not _is_hex64(inventory["source_sha256"]):
        raise RequestFeedInventoryError("inventory source_sha256 is not 64 lowercase hex digits")
    artifact = inventory["artifact_sha256"]
    if artifact is not None and not _is_hex64(artifact):
        raise RequestFeedInventoryError(
            "inventory artifact_sha256 is neither null nor 64 lowercase hex digits")
    primary = inventory["primary_chart_timeframe"]
    if primary is not None and not _is_wire_token(primary):
        raise RequestFeedInventoryError(
            "inventory primary_chart_timeframe is neither null nor a canonical wire token")
    entries = inventory["entries"]
    if not isinstance(entries, list):
        raise RequestFeedInventoryError("inventory entries is not a list")
    for position, entry in enumerate(entries):
        if not _entry_has_shape(entry):
            raise RequestFeedInventoryError(
                f"inventory entries[{position}] is not exactly one of token, input or unknown")


def _entry_has_shape(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    kind = entry.get("kind")
    if kind == "token":
        return set(entry) == {"kind", "timeframe"} and _is_wire_token(entry["timeframe"])
    if kind == "input":
        return (set(entry) == {"kind", "key", "default"}
                and type(entry["key"]) is str and type(entry["default"]) is str)
    if kind == "unknown":
        return set(entry) == {"kind"}
    return False


def _copy_entry(entry: dict) -> dict:
    if entry["kind"] == "token":
        return {"kind": "token", "timeframe": entry["timeframe"]}
    if entry["kind"] == "input":
        return {"kind": "input", "key": entry["key"], "default": entry["default"]}
    return {"kind": "unknown"}


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

def _located(loc):
    """``(line, column)`` of a located node, else None."""
    if loc is None or loc.line is None or loc.col is None:
        return None
    return loc.line, loc.col


def _entry(classified, chart) -> dict:
    """One entry from ``request_discovery``'s classification of a timeframe:
    only a literal, a chart dependency under the caller's binding and an input
    whose default is a string are anything but ``unknown``."""
    kind = classified.get("kind") if isinstance(classified, dict) else None
    if kind == "literal":
        token = canonical_wire_timeframe(classified.get("value"))
        if token is not None:
            return {"kind": "token", "timeframe": token}
    elif kind == "chart":
        if chart is not None:
            return {"kind": "token", "timeframe": chart}
    elif kind == "input":
        key, default = classified.get("title"), classified.get("default")
        if type(key) is str and type(default) is str:
            return {"kind": "input", "key": key, "default": default}
    return {"kind": "unknown"}


def _inventory_entries(gen, ctx, sites: list, chart) -> list[dict]:
    """Every request site's entry, in source order (module docstring)."""
    found: list[tuple[tuple, dict]] = []

    def add(loc, entry: dict) -> None:
        line, col = _located(loc) or (0, 0)
        found.append(((line, col, len(found)), entry))

    calls = {call.sec_id: call for call in ctx.security_calls}
    dead = {item["sec_id"] for item in gen._security_calls if item.get("dead")}
    live = [info for info in gen._security_eval_info if info["sec_id"] not in dead]
    deferred = [site for site in sites
                if (site.annotations or {}).get(LOWERING_ANNOTATION) == _UNPINNED_LOWERING]
    budget = getattr(gen, "_budget", None)
    # Expanding a name registers a reassigned global's read with the first-bar
    # replay; generation is over, so leave its record as it was (as
    # ``discover_requests`` does).
    mutable_reads = set(gen._security_tf_mutable_reads)
    try:
        for info in live:
            # Past the time budget a timeframe is classified as written,
            # unexpanded: an expression stays computed, so unknown.
            classified = _guarded(_registered_timeframe, gen, info, expand=_within(budget))
            add(getattr(calls.get(info["sec_id"]), "loc", None), _entry(classified, chart))
        for site in deferred:
            # The C++ never registered it, so nothing expands its names here.
            _symbol, timeframe = _request_args(site)
            classified = _guarded(_timeframe, gen, timeframe, expand=False)
            add(site.loc, _entry(classified, chart))
        try:
            for node in _unaccounted_requests(ctx, calls, live, deferred):
                add(node.loc, {"kind": "unknown"})
        except Exception:  # noqa: BLE001 -- a sweep that fails is an unclassified site
            add(None, {"kind": "unknown"})
    finally:
        gen._security_tf_mutable_reads = mutable_reads
    return [entry for _, entry in sorted(found, key=lambda item: item[0])]


def _unaccounted_requests(ctx, calls: dict, live: list, deferred: list) -> list:
    """The reachable ``request.security`` / ``request.security_lower_tf`` calls
    of the analyzed program that no live context and no deferred site accounts
    for, by the payload node the analyzer recorded for them, else by their
    source position and kind. A call in a helper nothing reaches (a method's
    is reached) is no site the strategy can make."""
    index = ScriptIndex(ctx.ast)
    reached = _reached(index)
    live_calls = [calls[info["sec_id"]] for info in live if info["sec_id"] in calls]
    payloads = {id(call.expression) for call in live_calls}
    places = {(_located(call.loc), bool(call.is_lower_tf_array)) for call in live_calls}
    deferred_ids = {id(site) for site in deferred}
    deferred_places = {_located(site.loc) for site in deferred}
    found = []
    for owner, requests in index.requests.items():
        if owner in index.funcs and owner not in reached:
            continue
        for node in requests:
            payload = _request_payload(node)
            place = _located(node.loc)
            lower = node.callee.member == "security_lower_tf"
            if id(node) in deferred_ids or (payload is not None and id(payload) in payloads):
                continue
            if place is not None and ((place, lower) in places or place in deferred_places):
                continue
            found.append(node)
    return found
