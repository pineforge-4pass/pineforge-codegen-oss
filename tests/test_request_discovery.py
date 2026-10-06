"""``transpile_full(...)["requests"]``: the other symbols' feeds a script
reads, listed before it runs (``pineforge_codegen.request_discovery``).

A run supplies another symbol's bars as one feed per (symbol string,
timeframe), looked up byte for byte, so each entry states the symbol and the
timeframe as registration computes them before the first bar. A site lowered
to ``na`` (its value reaches display sinks only) or in a helper nothing
reaches reads no feed and is not listed; a deferred refusal is listed with an
``unresolvable`` symbol. ``tests/test_e2e_request_discovery.py`` runs the
listed keys against the engine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.request_discovery import canonical_timeframe

FIXTURES = Path(__file__).parent / "fixtures" / "request_discovery"
HEAD = '//@version=6\nstrategy("discovery", overlay = true)\n'
TRADE = 'if c > 0\n    strategy.entry("L", strategy.long)\n'


def _requests(body: str) -> list[dict]:
    return transpile_full(HEAD + body)["requests"]


def _only(body: str) -> dict:
    entries = _requests(body)
    assert len(entries) == 1, entries
    return entries[0]


def resolve(entries: list[dict], chart_tf: str, inputs: dict | None = None) -> set[tuple]:
    """The (symbol, timeframe) feed keys a run needs: an input's override or
    default, a computed value, the chart's timeframe for ``chart`` and an
    empty value, each timeframe in the engine's spelling."""
    inputs = inputs or {}
    keys = set()
    for entry in entries:
        symbol, tf = entry["symbol"], entry["timeframe"]
        sym = (inputs.get(symbol["title"], symbol["default"]) if symbol["kind"] == "input"
               else symbol["value"])
        if tf["kind"] == "chart":
            value = ""
        elif tf["kind"] == "input":
            value = inputs.get(tf["title"], tf["default"])
        else:
            value = tf["value"]
        keys.add((sym, canonical_timeframe(value) if value else chart_tf))
    return keys


# ---------------------------------------------------------------------------
# Symbol kinds
# ---------------------------------------------------------------------------

def test_literal_symbol_is_the_exact_key_exchange_prefix_and_perpetual_suffix_kept():
    entry = _only('c = request.security("BINANCE:ETHUSDT.P", "240", close)\n' + TRADE)
    assert entry == {
        "line": 3, "fn": "request.security",
        "symbol": {"kind": "literal", "value": "BINANCE:ETHUSDT.P"},
        "timeframe": {"kind": "literal", "value": "240"},
        "lookahead": False, "gaps": False, "ignore_invalid_symbol": False,
    }


def test_input_symbol_is_keyed_by_the_title_its_override_uses():
    """The run keys the feed on the input's value: its default, or the
    override the run passes under the input's title -- the manifest's title,
    which the generated getter reads."""
    source = (HEAD + 'other = input.symbol("BINANCE:ETHUSDT", "Other symbol")\n'
              'c = request.security(other, timeframe.period, close)\n' + TRADE)
    full = transpile_full(source)
    assert full["requests"] == [{
        "line": 4, "fn": "request.security",
        "symbol": {"kind": "input", "title": "Other symbol", "default": "BINANCE:ETHUSDT"},
        "timeframe": {"kind": "chart"},
        "lookahead": False, "gaps": False, "ignore_invalid_symbol": False,
    }]
    manifest = {entry["title"]: entry for entry in full["inputs"]}
    assert manifest["Other symbol"] == {"title": "Other symbol", "type": "string",
                                        "default": "BINANCE:ETHUSDT", "kind": "symbol",
                                        "supported": True}
    assert 'const std::string _pf_symbol = get_input_string("Other symbol", ' in full["cpp"]
    assert resolve(full["requests"], "240", {"Other symbol": "BINANCE:SOLUSDT"}) == {
        ("BINANCE:SOLUSDT", "240")}


@pytest.mark.parametrize("body, title", [
    ('other = input.symbol("BINANCE:ETHUSDT")\nc = request.security(other, "60", close)\n',
     "other"),
    ('c = request.security(input.symbol("BINANCE:ETHUSDT", "Sym"), "60", close)\n', "Sym"),
    ('o = input.symbol("BINANCE:ETHUSDT", "Sym")\n'
     'c = request.security(ticker.standard(o), "60", close)\n', "Sym"),
    ('o = input.symbol("BINANCE:ETHUSDT", "Sym")\n'
     'c = request.security(ticker.inherit(syminfo.tickerid, o), "60", close)\n', "Sym"),
    ('o = input.string("BINANCE:ETHUSDT", "Sym")\nc = request.security(o, "60", close)\n',
     "Sym"),
])
def test_input_symbol_spellings(body, title):
    """An untitled input is keyed by its declaration's name; an inline input,
    ``ticker.standard`` / ``ticker.inherit`` of one (they render their
    symbol unchanged) and an ``input.string`` read the input's value."""
    assert _only(body + TRADE)["symbol"] == {
        "kind": "input", "title": title, "default": "BINANCE:ETHUSDT"}


def test_symbol_computed_from_literals_has_its_value():
    entry = _only('base = "ETH"\nc = request.security("BINANCE:" + base + "USDT", "D", close)\n'
                  + TRADE)
    assert entry["symbol"] == {"kind": "computed", "expr": '"BINANCE:" + base + "USDT"',
                               "value": "BINANCE:ETHUSDT"}


def test_symbol_computed_from_an_input_default_names_the_input():
    entry = _only('base = input.string("ETH", "Base")\nsym = "BINANCE:" + base + "USDT"\n'
                  'c = request.security(sym, "1W", close)\n' + TRADE)
    assert entry["symbol"] == {"kind": "computed", "expr": "sym", "value": "BINANCE:ETHUSDT",
                               "inputs": ["Base"]}


def test_symbol_that_can_select_the_chart_is_computed_at_the_defaults():
    """A ternary over an input: the arm the defaults select; the chart's own
    strings have no value before the run."""
    source = ('useOther = input.bool(true, "Use other")\n'
              'c = request.security(useOther ? "TVC:DXY" : syminfo.tickerid, "60", close)\n')
    assert _only(source + TRADE)["symbol"] == {
        "kind": "computed", "expr": 'useOther ? "TVC:DXY" : syminfo.tickerid',
        "value": "TVC:DXY", "inputs": ["Use other"]}
    chart = _only(source.replace("input.bool(true", "input.bool(false") + TRADE)["symbol"]
    assert chart == {"kind": "computed", "expr": 'useOther ? "TVC:DXY" : syminfo.tickerid',
                     "inputs": ["Use other"]}


def test_unresolvable_symbol_is_the_deferred_refusal():
    """A reassigned symbol registration cannot compute: the run stops where
    the value is read, whatever feed it is given."""
    source = ('var string s = "BINANCE:ETHUSDT"\n'
              's := close > open ? "BINANCE:ETHUSDT" : "BINANCE:BTCUSDT"\n'
              'c = request.security(s, "60", close)\n' + TRADE)
    full = transpile_full(HEAD + source)
    assert full["requests"] == [{
        "line": 5, "fn": "request.security",
        "symbol": {"kind": "unresolvable", "expr": "s"},
        "timeframe": {"kind": "literal", "value": "60"},
        "lookahead": False, "gaps": False, "ignore_invalid_symbol": False,
    }]
    assert "no data is pinned for this request, and its value was read" in full["cpp"]


def test_requests_registration_cannot_key_are_unresolvable():
    """A timeframe with no value before the first bar, a request inside
    another's expression: deferred refusals, listed as such."""
    tf_var = _only('var string tf = "60"\nif bar_index == 0\n    tf := "240"\n'
                   'c = request.security("BINANCE:ETHUSDT", tf, close)\n' + TRADE)
    assert tf_var["symbol"] == {"kind": "unresolvable", "expr": '"BINANCE:ETHUSDT"'}
    assert tf_var["timeframe"] == {"kind": "computed", "expr": "tf"}
    nested = _requests('c = request.security("BINANCE:ETHUSDT", "60", '
                       'request.security("BINANCE:BTCUSDT", "60", close))\n' + TRADE)
    assert [e["symbol"] for e in nested] == [
        {"kind": "unresolvable", "expr": '"BINANCE:ETHUSDT"'},
        {"kind": "unresolvable", "expr": '"BINANCE:BTCUSDT"'}]


def test_site_lowered_to_na_and_unreached_helper_are_not_listed():
    """A value that reaches plots only is lowered to na (no feed read), and a
    helper nothing calls never runs."""
    plots_only = ('c = request.security("BINANCE:ETHUSDT", "60", close)\nplot(c)\n'
                  'if close > open\n    strategy.entry("L", strategy.long)\n')
    full = transpile_full(HEAD + plots_only)
    assert full["requests"] == []
    assert any("lowered to na" in d.message for d in full["diagnostics"])
    assert _requests('f(s) => request.security(s, "60", close)\nc = close\n' + TRADE) == []
    # Registered all the same (its value reaches an order in the helper), but
    # never read; a deferred refusal there never stops the run either.
    for symbol in ('"BINANCE:ETHUSDT"', 's'):
        assert _requests(
            'f() =>\n    var string s = "A:B"\n    s := close > open ? "A:B" : "C:D"\n'
            f'    v = request.security({symbol}, "60", close)\n'
            '    if v > 0\n        strategy.entry("L", strategy.long)\n    v\n'
            'c = close\n' + TRADE) == []


@pytest.mark.parametrize("body", [
    'c = request.security(syminfo.tickerid, "60", close)\n',
    'c = request.security(ticker.standard(syminfo.tickerid), "D", close)\n',
    'c = array.size(request.security_lower_tf("BINANCE:ETHUSDT", "1", close))\n',
    'c = request.earnings("NASDAQ:AAPL")\n',
])
def test_requests_reading_no_feed_are_not_listed(body):
    """The chart's own symbol, another symbol's lower-timeframe request (it
    stops the run where evaluated) and a recorded fundamentals series read
    no feed of another symbol's bars."""
    assert _requests(body + TRADE) == []


# ---------------------------------------------------------------------------
# Timeframes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tf, expected", [
    ("timeframe.period", {"kind": "chart"}),
    ("timeframe.main_period", {"kind": "chart"}),
    ('"240"', {"kind": "literal", "value": "240"}),
    ('"D"', {"kind": "literal", "value": "1D"}),
    ('"1D"', {"kind": "literal", "value": "1D"}),
    ('"1W"', {"kind": "literal", "value": "1W"}),
    ('"W"', {"kind": "literal", "value": "1W"}),
    ('"M"', {"kind": "literal", "value": "1M"}),
    ('"30S"', {"kind": "literal", "value": "30S"}),
])
def test_timeframe_spelled_as_the_engine_keys_a_feed(tf, expected):
    entry = _only(f'c = request.security("BINANCE:ETHUSDT", {tf}, close)\n' + TRADE)
    assert entry["timeframe"] == expected


def test_timeframe_global_input_and_computed():
    assert _only('tf = "D"\nc = request.security("BINANCE:ETHUSDT", tf, close)\n'
                 + TRADE)["timeframe"] == {"kind": "literal", "value": "1D"}
    assert _only('htf = input.timeframe("D", "HTF")\n'
                 'c = request.security("BINANCE:ETHUSDT", htf, close)\n'
                 + TRADE)["timeframe"] == {"kind": "input", "title": "HTF", "default": "D"}
    computed = ('useD = input.bool(true, "Use D")\ntf = useD ? "D" : timeframe.period\n'
                'c = request.security("BINANCE:ETHUSDT", tf, close)\n' + TRADE)
    assert _only(computed)["timeframe"] == {"kind": "computed", "expr": "tf", "value": "1D",
                                            "inputs": ["Use D"]}
    # The chart's timeframe at the defaults: an empty value.
    assert _only(computed.replace("input.bool(true", "input.bool(false"))["timeframe"] == {
        "kind": "computed", "expr": "tf", "value": "", "inputs": ["Use D"]}
    # An empty string is the chart's timeframe.
    assert _only('tf = ""\nc = request.security("BINANCE:ETHUSDT", tf, close)\n'
                 + TRADE)["timeframe"] == {"kind": "chart"}


@pytest.mark.parametrize("default, value", [("A", "60"), ("B", "1D"), ("C", "")])
def test_switch_timeframe_takes_the_arm_registration_takes(default, value):
    """As registration renders it: the matching arm, else the chart's
    timeframe (an unmatched switch without a default is na)."""
    entry = _only(f'mode = input.string("{default}", "Mode")\ntf = switch mode\n'
                  '    "A" => "60"\n    "B" => "D"\n'
                  'c = request.security("BINANCE:ETHUSDT", tf, close)\n' + TRADE)
    assert entry["timeframe"] == {"kind": "computed", "expr": "tf", "value": value,
                                  "inputs": ["Mode"]}
    entry = _only('hi = input.bool(false, "Hi")\ntf = switch\n    hi => "D"\n    => "W"\n'
                  'c = request.security("BINANCE:ETHUSDT", tf, close)\n' + TRADE)
    assert entry["timeframe"]["value"] == "1W"


def test_canonical_timeframe():
    assert [canonical_timeframe(t) for t in ("D", "W", "M", "S", "240", "1D", "12M", "1H")] == [
        "1D", "1W", "1M", "1S", "240", "1D", "12M", "1H"]


# ---------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------

def test_multiple_sites_and_one_symbol_at_two_timeframes():
    entries = _requests(
        'a = request.security("BINANCE:ETHUSDT", "240", close)\n'
        'b = request.security("BINANCE:ETHUSDT", "D", ta.sma(close, 10))\n'
        'd = request.security("BYBIT:SOLUSDT.P", "1W", close)\n'
        'c = a + b + d\n' + TRADE)
    assert [(e["line"], e["symbol"]["value"], e["timeframe"]["value"]) for e in entries] == [
        (3, "BINANCE:ETHUSDT", "240"), (4, "BINANCE:ETHUSDT", "1D"),
        (5, "BYBIT:SOLUSDT.P", "1W")]
    assert resolve(entries, "15") == {("BINANCE:ETHUSDT", "240"), ("BINANCE:ETHUSDT", "1D"),
                                      ("BYBIT:SOLUSDT.P", "1W")}


def test_helper_contexts_are_one_entry_per_call_path():
    """A helper's request registers once per symbol and timeframe its call
    paths pass (``security_contexts``): each is an entry at its line."""
    entries = _requests(
        'o = input.symbol("BINANCE:ETHUSDT", "Other")\n'
        'g(s, tf) => request.security(s, tf, close)\n'
        'h(s) => g(s, "60") + g(s, "D")\n'
        'c = h(o) + g("BINANCE:SOLUSDT", "240")\n' + TRADE)
    # Entries of one line follow the order the C++ registers them in.
    assert sorted(([e["line"], e["symbol"], e["timeframe"]] for e in entries),
                  key=json.dumps) == sorted([
        [4, {"kind": "input", "title": "Other", "default": "BINANCE:ETHUSDT"},
         {"kind": "literal", "value": "60"}],
        [4, {"kind": "input", "title": "Other", "default": "BINANCE:ETHUSDT"},
         {"kind": "literal", "value": "1D"}],
        [4, {"kind": "literal", "value": "BINANCE:SOLUSDT"},
         {"kind": "literal", "value": "240"}]], key=json.dumps)


def test_gaps_lookahead_and_ignore_invalid_symbol():
    entry = _only('c = request.security("BINANCE:ETHUSDT", "60", close[1], barmerge.gaps_on, '
                  'barmerge.lookahead_on, ignore_invalid_symbol = true)\n' + TRADE)
    assert (entry["gaps"], entry["lookahead"], entry["ignore_invalid_symbol"]) == (
        True, True, True)
    entry = _only('ig = input.bool(false, "Ignore")\nc = request.security("BINANCE:ETHUSDT", '
                  '"60", close, ignore_invalid_symbol = ig)\n' + TRADE)
    assert entry["ignore_invalid_symbol"] is False


def test_footprint_names_the_feed_column_it_reads():
    entry = _only('fp = request.security("BINANCE:BTCUSDT", "15", request.footprint(100, 70))\n'
                  'c = fp.delta()\n' + TRADE)
    assert entry["column"] == "fp_delta_100_70"
    assert entry["symbol"] == {"kind": "literal", "value": "BINANCE:BTCUSDT"}


def test_discovery_leaves_the_cpp_as_transpile_emits_it():
    source = (HEAD + 'other = input.symbol("BINANCE:ETHUSDT", "Other symbol")\n'
              'c = request.security(other, "D", close)\n' + TRADE)
    assert transpile_full(source)["cpp"] == transpile(source)


def test_input_manifest_kind_symbol_only_for_input_symbol():
    inputs = transpile_full(HEAD + 's = input.symbol("BINANCE:ETHUSDT", "S")\n'
                            't = input.string("x", "T")\nu = input.timeframe("D", "U")\n'
                            'plot(close)\n')["inputs"]
    assert [entry.get("kind") for entry in inputs] == ["symbol", None, None]


def test_glue_json_carries_the_requests():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_pf_glue", Path(__file__).resolve().parents[1] / "gate" / "glue.py")
    glue = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(glue)
    source = HEAD + 'c = request.security("BINANCE:ETHUSDT", "D", close)\n' + TRADE
    result = json.loads(glue.transpile_json(source))
    assert result["ok"] and result["requests"] == transpile_full(source)["requests"]


# ---------------------------------------------------------------------------
# The shared case: a BINANCE:BTCUSDT 4h chart requesting BINANCE:ETHUSDT at
# 240 and D through input.symbol (the harness's --symbol-feeds index beside it)
# ---------------------------------------------------------------------------

def test_shared_case_names_exactly_the_feeds_its_index_holds():
    full = transpile_full((FIXTURES / "case_strategy.pine").read_text())
    index = json.loads((FIXTURES / "case_symbols.json").read_text())
    needed = {(symbol, tf) for symbol, entry in index["symbols"].items()
              for tf in entry["feeds"]}
    assert resolve(full["requests"], "240") == needed == {
        ("BINANCE:ETHUSDT", "240"), ("BINANCE:ETHUSDT", "1D")}
    assert [(e["line"], e["timeframe"]) for e in full["requests"]] == [
        (7, {"kind": "chart"}), (8, {"kind": "literal", "value": "1D"})]
    assert all(e["symbol"] == {"kind": "input", "title": "Other symbol",
                               "default": "BINANCE:ETHUSDT"} for e in full["requests"])
