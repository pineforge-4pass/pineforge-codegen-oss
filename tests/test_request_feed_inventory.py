"""The request-feed inventory (``pineforge_codegen.request_feed_inventory``,
interface v1): every bar request a compiled strategy can make, as a wire token,
an input reference or ``unknown``, bound to the C++ it was generated from.

The inventory is read before a selected-window run opens a feed or loads the
strategy library, so each test states what an admission may rely on: an empty
list is complete, ``unknown`` is incomplete, a token is exactly a timeframe the
script writes (or the caller's chart binding), an input reference names the
manifest key and its string default, and binding refuses bytes the inventory
was not made from. ``transpile`` and ``transpile_full`` stay as they were.
"""

from __future__ import annotations

import hashlib

import pytest

import pineforge_codegen
from pineforge_codegen import (
    _generate, transpile, transpile_full, transpile_with_request_inventory,
)
from pineforge_codegen.errors import Level
from pineforge_codegen.request_feed_inventory import (
    SCHEMA, RequestFeedInventoryError, bind_request_feed_inventory,
    build_request_feed_inventory, canonical_wire_timeframe,
)

HEAD = '//@version=6\nstrategy("inventory", overlay = true)\n'
TRADE = 'if c > 0\n    strategy.entry("L", strategy.long)\n'
UNKNOWN = {"kind": "unknown"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inventory(body: str, **options) -> dict:
    return transpile_with_request_inventory(HEAD + body, **options)["request_feed_inventory"]


def _entries(body: str, **options) -> list[dict]:
    return _inventory(body, **options)["entries"]


def _token(timeframe: str) -> dict:
    return {"kind": "token", "timeframe": timeframe}


# ---------------------------------------------------------------------------
# What the inventory holds
# ---------------------------------------------------------------------------

def test_no_requests_is_a_complete_empty_inventory():
    result = transpile_with_request_inventory(HEAD + "c = close\n" + TRADE)
    assert set(result) == {"cpp", "inputs", "strategyParams", "diagnostics", "requests",
                           "request_feed_inventory"}
    assert result["request_feed_inventory"] == {
        "schema": SCHEMA, "source_sha256": _sha(result["cpp"].encode("utf-8")),
        "artifact_sha256": None, "primary_chart_timeframe": None, "entries": [],
    }
    assert SCHEMA == "pineforge-request-feed-inventory/v1"


@pytest.mark.parametrize("tf, token", [("60", "60"), ("D", "D")])
def test_same_ticker_literal_timeframe_is_a_token(tf, token):
    """The chart's own symbol reads no feed of another symbol, but its request
    registers a timeframe all the same: it is listed (``discover_requests``
    leaves it out)."""
    assert _entries(f'c = request.security(syminfo.tickerid, "{tf}", close)\n' + TRADE) == [
        _token(token)]


@pytest.mark.parametrize("tf, token", [
    ("240", "240"), ("D", "D"), ("1D", "D"), ("W", "W"), ("1W", "W"), ("M", "1M"),
    ("30S", "30S"),
])
def test_other_symbol_literal_timeframe_is_a_token(tf, token):
    assert _entries(f'c = request.security("BINANCE:ETHUSDT", "{tf}", close)\n' + TRADE) == [
        _token(token)]


def test_entries_follow_the_source_order():
    entries = _entries(
        'a = request.security("BINANCE:ETHUSDT", "240", close)\n'
        'b = request.security(syminfo.tickerid, "D", close)\n'
        'd = request.security("BYBIT:SOLUSDT.P", "1W", close)\n'
        'c = a + b + d\n' + TRADE)
    assert entries == [_token("240"), _token("D"), _token("W")]


@pytest.mark.parametrize("title_args, key", [
    ('"D", "HTF"', "HTF"),
    ('"240", title = "Higher timeframe"', "Higher timeframe"),
    ('"D"', "htf"),
])
def test_input_reference_names_the_manifest_key_and_the_string_default(title_args, key):
    """The key is the one an override is keyed by (the manifest's ``title``: the
    title argument, else the declaration's name) and the default is the
    declaration's own string, as written."""
    source = HEAD + (f'htf = input.timeframe({title_args})\n'
                     'c = request.security("BINANCE:ETHUSDT", htf, close)\n' + TRADE)
    default = title_args.split(",")[0].strip().strip('"')
    result = transpile_with_request_inventory(source)
    assert result["request_feed_inventory"]["entries"] == [
        {"kind": "input", "key": key, "default": default}]
    manifest = {entry["title"]: entry for entry in transpile_full(source)["inputs"]}
    assert manifest[key]["default"] == default


def test_input_string_reference_and_its_override_key():
    source = HEAD + ('mode = input.string("60", "Request timeframe")\n'
                     'c = request.security(syminfo.tickerid, mode, close)\n' + TRADE)
    inventory = transpile_with_request_inventory(source)["request_feed_inventory"]
    assert inventory["entries"] == [
        {"kind": "input", "key": "Request timeframe", "default": "60"}]
    assert "Request timeframe" in {e["title"] for e in transpile_full(source)["inputs"]}


def test_helper_contexts_are_one_entry_per_call_path():
    """A helper's request registers once per symbol and timeframe its call
    paths pass (``security_contexts``): each context is an entry."""
    entries = _entries(
        'o = input.symbol("BINANCE:ETHUSDT", "Other")\n'
        'g(s, tf) => request.security(s, tf, close)\n'
        'h(s) => g(s, "60") + g(s, "D")\n'
        'c = h(o) + g("BINANCE:SOLUSDT", "240")\n' + TRADE)
    # One line: the order within it is the order the C++ registers them in.
    assert sorted(entry["timeframe"] for entry in entries) == ["240", "60", "D"]
    assert all(entry["kind"] == "token" for entry in entries)


@pytest.mark.parametrize("body", [
    'useD = input.bool(true, "Use D")\ntf = useD ? "D" : timeframe.period\n'
    'c = request.security("BINANCE:ETHUSDT", tf, close)\n',
    'mode = input.string("A", "Mode")\ntf = switch mode\n    "A" => "60"\n    "B" => "D"\n'
    'c = request.security("BINANCE:ETHUSDT", tf, close)\n',
])
def test_computed_timeframe_is_unknown_never_a_guessed_token(body):
    """``discover_requests`` folds these at the inputs' defaults ("1D", "60");
    an inventory states no default it computed, whoever binds the chart."""
    assert _entries(body + TRADE) == [UNKNOWN]
    assert _entries(body + TRADE, primary_chart_timeframe="15") == [UNKNOWN]


def test_deferred_refusal_is_listed_by_its_timeframe_as_written():
    """Registration cannot key the reassigned symbol, so the run stops where
    the value is read; the site is still a request the script makes."""
    source = ('var string s = "BINANCE:ETHUSDT"\n'
              's := close > open ? "BINANCE:ETHUSDT" : "BINANCE:BTCUSDT"\n'
              'c = request.security(s, "60", close)\n' + TRADE)
    assert _entries(source) == [_token("60")]
    deferred_tf = ('var string tf = "60"\nif bar_index == 0\n    tf := "240"\n'
                   'c = request.security("BINANCE:ETHUSDT", tf, close)\n' + TRADE)
    assert _entries(deferred_tf) == [UNKNOWN]


def test_lower_timeframe_request_is_listed():
    entries = _entries(
        'c = array.size(request.security_lower_tf("BINANCE:ETHUSDT", "1", close))\n' + TRADE)
    assert entries == [_token("1")]


def test_a_request_lowered_to_na_is_not_a_request():
    """Its value reaches plots only: the C++ makes no request, so a complete
    empty list is true of it."""
    source = ('c = request.security("BINANCE:ETHUSDT", "60", close)\nplot(c)\n'
              'if close > open\n    strategy.entry("L", strategy.long)\n')
    assert _entries(source) == []


# ---------------------------------------------------------------------------
# The chart binding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    'c = request.security("BINANCE:ETHUSDT", timeframe.period, close)\n',
    'c = request.security("BINANCE:ETHUSDT", timeframe.main_period, close)\n',
    'tf = ""\nc = request.security("BINANCE:ETHUSDT", tf, close)\n',
])
def test_chart_dependency_is_a_token_only_under_the_callers_binding(body):
    unbound = _inventory(body + TRADE)
    assert (unbound["primary_chart_timeframe"], unbound["entries"]) == (None, [UNKNOWN])
    bound = _inventory(body + TRADE, primary_chart_timeframe="15")
    assert (bound["primary_chart_timeframe"], bound["entries"]) == ("15", [_token("15")])
    # The binding is a wire token like any other: spelled 1D, it is D.
    daily = _inventory(body + TRADE, primary_chart_timeframe="1D")
    assert (daily["primary_chart_timeframe"], daily["entries"]) == ("D", [_token("D")])


def test_a_binding_changes_no_literal_entry():
    body = 'c = request.security("BINANCE:ETHUSDT", "D", close)\n' + TRADE
    assert _entries(body, primary_chart_timeframe="15") == [_token("D")]


@pytest.mark.parametrize("binding", ["", " 15", "15 ", "05", "1H", "abc", 15, b"15"])
def test_a_binding_that_is_no_wire_token_is_refused(binding):
    with pytest.raises(RequestFeedInventoryError, match="is not a wire timeframe token"):
        transpile_with_request_inventory(HEAD + "c = close\n" + TRADE,
                                         primary_chart_timeframe=binding)


@pytest.mark.parametrize("text, token", [
    ("1", "1"), ("5", "5"), ("60", "60"), ("240", "240"), ("1440", "1440"),
    ("D", "D"), ("1D", "D"), ("2D", "2D"), ("W", "W"), ("1W", "W"), ("3W", "3W"),
    ("M", "1M"), ("1M", "1M"), ("12M", "12M"), ("S", "1S"), ("1S", "1S"), ("30S", "30S"),
])
def test_wire_tokens(text, token):
    assert canonical_wire_timeframe(text) == token


@pytest.mark.parametrize("text", [
    "", " 5", "5 ", "05", "0", "00", "0D", "-5", "1.5", "5.0", "1H", "4H", "H", "d", "1d",
    "2w", "60m", "１５", "5\n", "D1", "1DD", None, 15, b"5",
])
def test_not_wire_tokens(text):
    assert canonical_wire_timeframe(text) is None


# ---------------------------------------------------------------------------
# The generated source and the generation state are left alone
# ---------------------------------------------------------------------------

def test_the_cpp_is_exactly_the_one_transpile_returns():
    source = HEAD + ('other = input.symbol("BINANCE:ETHUSDT", "Other symbol")\n'
                     'c = request.security(other, "D", close)\n' + TRADE)
    plain = transpile(source)
    result = transpile_with_request_inventory(source)
    assert result["cpp"] == plain == transpile_full(source)["cpp"]
    assert result["request_feed_inventory"]["source_sha256"] == _sha(plain.encode("utf-8"))
    assert set(transpile_full(source)) == {
        "cpp", "inputs", "strategyParams", "diagnostics", "requests"}
    assert transpile_with_request_inventory(source) == result


def test_building_leaves_the_generation_state_as_it_was():
    source = HEAD + ('htf = input.timeframe("D", "HTF")\n'
                     'c = request.security("BINANCE:ETHUSDT", htf, close)\n' + TRADE)
    gen, ctx, cpp, _support, sites = _generate(source, True, "<input>")
    before = (set(gen._security_tf_mutable_reads), len(ctx.diagnostics), len(sites),
              [info["sec_id"] for info in gen._security_eval_info])
    build_request_feed_inventory(gen, ctx, sites, cpp)
    assert before == (set(gen._security_tf_mutable_reads), len(ctx.diagnostics), len(sites),
                      [info["sec_id"] for info in gen._security_eval_info])


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------

CPP = b"// generated source\n"


def _valid() -> dict:
    return {"schema": SCHEMA, "source_sha256": _sha(CPP), "artifact_sha256": None,
            "primary_chart_timeframe": None,
            "entries": [_token("D"), {"kind": "input", "key": "HTF", "default": "240"},
                        dict(UNKNOWN)]}


def test_binding_fills_the_artifact_digest_into_a_copy():
    artifact = b"\x7fELF linked artifact"
    original = _valid()
    bound = bind_request_feed_inventory(original, CPP, artifact)
    assert bound == {**_valid(), "artifact_sha256": _sha(artifact)}
    assert list(bound) == ["schema", "source_sha256", "artifact_sha256",
                           "primary_chart_timeframe", "entries"]
    assert original == _valid()
    bound["entries"][0]["timeframe"] = "W"
    assert original["entries"][0] == _token("D")


def test_binding_a_transpiled_inventory_to_its_exact_utf8_bytes():
    result = transpile_with_request_inventory(
        HEAD + 'c = request.security("BINANCE:ETHUSDT", "D", close)\n' + TRADE)
    inventory, cpp = result["request_feed_inventory"], result["cpp"]
    artifact = b"linked"
    bound = bind_request_feed_inventory(inventory, cpp.encode("utf-8"), artifact)
    assert bound["artifact_sha256"] == _sha(artifact)
    assert bound["entries"] == [_token("D")]
    assert inventory["artifact_sha256"] is None


def test_binding_refuses_source_bytes_the_inventory_was_not_made_from():
    result = transpile_with_request_inventory(
        HEAD + 'c = request.security("BINANCE:ETHUSDT", "D", close)\n' + TRADE)
    inventory, cpp = result["request_feed_inventory"], result["cpp"]
    other = transpile(HEAD + 'c = request.security("BINANCE:ETHUSDT", "W", close)\n' + TRADE)
    for wrong in (cpp.encode("utf-8") + b"\n", cpp.replace("\n", "\r\n").encode("utf-8"),
                  other.encode("utf-8"), b""):
        with pytest.raises(RequestFeedInventoryError,
                           match="source_sha256 does not match the supplied C\\+\\+ bytes"):
            bind_request_feed_inventory(inventory, wrong, b"linked")


def test_binding_refuses_an_inventory_that_is_already_bound():
    inventory = _valid()
    inventory["artifact_sha256"] = "0" * 64
    with pytest.raises(RequestFeedInventoryError, match="artifact_sha256 is already set"):
        bind_request_feed_inventory(inventory, CPP, b"linked")


@pytest.mark.parametrize("cpp_bytes, artifact_bytes", [
    (CPP.decode(), b"linked"), (CPP, "linked"), (None, b"linked"), (CPP, None),
])
def test_binding_takes_bytes_only(cpp_bytes, artifact_bytes):
    with pytest.raises(RequestFeedInventoryError, match="must be bytes"):
        bind_request_feed_inventory(_valid(), cpp_bytes, artifact_bytes)


@pytest.mark.parametrize("mutate", [
    lambda inv: inv.pop("entries"),
    lambda inv: inv.update(extra=1),
    lambda inv: inv.update(schema="pineforge-request-feed-inventory/v2"),
    lambda inv: inv.update(source_sha256="A" * 64),
    lambda inv: inv.update(source_sha256="abc"),
    lambda inv: inv.update(artifact_sha256="abc"),
    lambda inv: inv.update(primary_chart_timeframe="1D"),
    lambda inv: inv.update(primary_chart_timeframe=15),
    lambda inv: inv.update(entries={}),
    lambda inv: inv["entries"].append({"kind": "token"}),
    lambda inv: inv["entries"].append({"kind": "token", "timeframe": "05"}),
    lambda inv: inv["entries"].append({"kind": "token", "timeframe": "1D"}),
    lambda inv: inv["entries"].append({"kind": "token", "timeframe": ""}),
    lambda inv: inv["entries"].append({"kind": "input", "key": "K", "default": 5}),
    lambda inv: inv["entries"].append({"kind": "input", "key": "K"}),
    lambda inv: inv["entries"].append({"kind": "unknown", "timeframe": "D"}),
    lambda inv: inv["entries"].append({"kind": "other"}),
    lambda inv: inv["entries"].append("D"),
])
def test_binding_refuses_an_inexact_shape(mutate):
    inventory = _valid()
    mutate(inventory)
    with pytest.raises(RequestFeedInventoryError):
        bind_request_feed_inventory(inventory, CPP, b"linked")


@pytest.mark.parametrize("inventory", [None, [], "inventory", 7])
def test_binding_refuses_what_is_no_object(inventory):
    with pytest.raises(RequestFeedInventoryError, match="is not an object with exactly the keys"):
        bind_request_feed_inventory(inventory, CPP, b"linked")


# ---------------------------------------------------------------------------
# transpile_with_request_inventory against transpile_full
# ---------------------------------------------------------------------------

# Input metadata of three kinds (string, int, symbol), strategy() parameters, a
# same-ticker daily request, a feed read by a trade, and the two diagnostics
# this script raises: ta.ema's warmup note and bar_index's window warning.
METADATA = (
    '//@version=6\n'
    'strategy("metadata", overlay = true, initial_capital = 10000, pyramiding = 2)\n'
    'mode = input.string("fast", "Mode", options = ["fast", "slow"])\n'
    'length = input.int(14, "Length", options = [14, 21])\n'
    'other = input.symbol("BINANCE:ETHUSDT", "Other symbol")\n'
    'e = ta.ema(close, length)\n'
    'daily = request.security(syminfo.tickerid, "D", close)\n'
    'feed = request.security(other, "240", close)\n'
    'if mode == "fast" and e > daily and feed > 0 and bar_index > 0\n'
    '    strategy.entry("L", strategy.long)\n'
)


def test_new_api_returns_the_transpile_full_fields_from_one_generation():
    legacy = transpile_full(METADATA)
    result = transpile_with_request_inventory(METADATA)
    assert set(result) == {"cpp", "inputs", "strategyParams", "diagnostics", "requests",
                           "request_feed_inventory"}
    assert result["cpp"] == transpile(METADATA) == legacy["cpp"]
    for key in ("inputs", "strategyParams", "diagnostics", "requests"):
        assert result[key] == legacy[key], key
    assert [entry["title"] for entry in result["inputs"]] == ["Mode", "Length", "Other symbol"]
    assert {"initial_capital", "pyramiding"} <= set(result["strategyParams"])
    assert {Level.WARNING, Level.NOTE} <= {d.level for d in result["diagnostics"]}
    assert result["requests"] == [{
        "line": 8, "fn": "request.security",
        "symbol": {"kind": "input", "title": "Other symbol", "default": "BINANCE:ETHUSDT"},
        "timeframe": {"kind": "literal", "value": "240"},
        "lookahead": False, "gaps": False, "ignore_invalid_symbol": False,
    }]
    assert result["request_feed_inventory"]["entries"] == [_token("D"), _token("240")]


def test_new_api_runs_generation_exactly_once(monkeypatch):
    generate = pineforge_codegen._generate
    calls = []

    def counted(*args, **kwargs):
        calls.append(args)
        return generate(*args, **kwargs)

    monkeypatch.setattr(pineforge_codegen, "_generate", counted)
    transpile_with_request_inventory(METADATA)
    assert len(calls) == 1


def test_same_ticker_daily_request_is_inventoried_though_requests_leaves_it_out():
    source = HEAD + 'c = request.security(syminfo.tickerid, "D", close)\n' + TRADE
    result = transpile_with_request_inventory(source)
    assert transpile_full(source)["requests"] == result["requests"] == []
    assert result["request_feed_inventory"]["entries"] == [_token("D")]
