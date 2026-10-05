"""Order metadata follows emitted parameters, without changing computation."""

import json
from pathlib import Path
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.order_shapes import HostReadLines, scan_host_reads
from tests._compile import compile_cpp


SOURCE = '//@version=6\nstrategy("order shapes", process_orders_on_close=true)\n'
RECEIPT_PATTERN = r'checked_settings::receipt\(("(?:[^"\\]|\\.)*"), json, capacity, required\);'


def receipt(body, declaration=SOURCE, *, check_support=True):
    cpp = transpile(declaration + body, check_support=check_support)
    documents = re.findall(RECEIPT_PATTERN, cpp)
    assert len(documents) == 3
    document = json.loads(documents[2])
    result = json.loads(document)
    assert json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False) == document
    assert set(result) == {"version", "process_orders_on_close", "calls", "entry_ids", "host_reads", "settings", "unmodeled"}
    assert result["version"] == 1
    return result


@pytest.mark.parametrize(("first", "second"), [
    ('strategy.entry("L", strategy.long)', 'strategy.entry("L", strategy.long, limit=na)'),
    ('strategy.entry("L", strategy.long)', 'strategy.entry("L", strategy.long, stop=na, qty=na)'),
    ('strategy.entry("L", strategy.long)', 'strategy.entry("L", strategy.long, alert_message="alert", disable_alert=true)'),
    ('strategy.entry("L", strategy.long, 2, close, na)', 'strategy.entry(id="L", direction=strategy.long, qty=2, limit=close, stop=na)'),
    ('strategy.exit("X", limit=close)', 'strategy.exit("X", from_entry="", limit=close)'),
    ('strategy.exit("X")', 'strategy.exit("X", from_entry="")'),
    ('strategy.exit("X", limit=close)', 'strategy.exit("X", limit=close, alert_message="a", disable_alert=true)'),
    ('strategy.order("L", strategy.long)', 'strategy.order(id="L", direction=strategy.long, qty=0)'),
    ('strategy.close("L")', 'strategy.close(id="L", alert_message="a", disable_alert=true)'),
    ('strategy.cancel("L")', 'strategy.cancel(id="L")'),
])
def test_default_expansion_families(first, second):
    check_support = first != 'strategy.exit("X")'
    assert receipt(first, check_support=check_support) == receipt(second, check_support=check_support)


@pytest.mark.parametrize(("setup", "value", "expected"), [
    ("", "na", "absent"), ("", "3", "literal"), ("", "-3.5", "literal"),
    ("constant = 3\n", "constant", "literal"),
    ("constant = 3\nalias = constant\n", "alias", "literal"),
    ("var float missing = na\n", "missing", "maybe_na"),
    ("constant = 3\nconstant := 4\n", "constant", "maybe_na"),
    ("", "close", "never_na"), ("", "hl2", "never_na"),
    ("", "hlc3", "never_na"), ("", "ohlc4", "never_na"),
    ("", "hlcc4", "never_na"), ("", "high - low + close", "never_na"),
    ("", "-open", "never_na"), ("factor = 2\n", "close * factor", "never_na"),
    ("", "2 * close", "never_na"), ("", "close * open", "maybe_na"),
    ("", "close / 2", "maybe_na"), ("", "volume", "maybe_na"),
    ("", "close[1]", "maybe_na"),
    ("value = close\n", "value[1]", "maybe_na"),
    ("cond = close > open\nvalue = close\n", "cond ? value : na", "maybe_na"),
    ("", "ta.sma(close, 2)", "maybe_na"),
    ("value = input.float(3)\n", "value", "maybe_na"),
    ("", "input.int(3)", "maybe_na"),
    ("factor = 2\n", "strategy.position_avg_price * factor", "maybe_na"),
    ("f() => 3\n", "f()", "maybe_na"),
    ("value = close\n", "value", "maybe_na"),
])
def test_numeric_nullability(setup, value, expected):
    assert receipt(setup + f'strategy.entry("L", strategy.long, limit={value})')["calls"][0]["limit"] == expected


@pytest.mark.parametrize(("terms", "expected"), [
    ("", {"qty_type": "absent", "oca_type": "absent", "oca_name": "absent"}),
    (', qty_type=-1, oca_type=0, oca_name=""', {"qty_type": "absent", "oca_type": "absent", "oca_name": "absent"}),
    (', qty_type=strategy.fixed, oca_type=strategy.oca.none', {"qty_type": "literal:0", "oca_type": "absent", "oca_name": "absent"}),
    (', qty_type=strategy.cash, oca_type=strategy.oca.cancel, oca_name="group"', {"qty_type": "literal:2", "oca_type": "literal:1", "oca_name": "literal"}),
    (', qty_type=input.int(0), oca_type=input.int(1), oca_name=input.string("group")', {"qty_type": "dynamic", "oca_type": "dynamic", "oca_name": "dynamic"}),
])
def test_entry_enum_and_text_defaults(terms, expected):
    call = receipt(f'strategy.entry("L", strategy.long{terms})')["calls"][0]
    assert {name: call[name] for name in expected} == expected
    assert set(call) == {"site", "call", "context", "id", "direction", "limit", "stop", "qty", "comment", "oca_name", "oca_type", "qty_type"}


@pytest.mark.parametrize(("terms", "expected"), [
    ("", "absent"), (", qty_percent=100.0", "absent"),
    (", qty_percent=100", "literal"), (", qty_percent=50", "literal"),
    (", qty_percent=close", "never_na"), (", qty_percent=input.float(50)", "maybe_na"),
])
def test_exit_percentage_default_is_lowered_text(terms, expected):
    assert receipt(f'strategy.exit("X", limit=close{terms})')["calls"][0]["qty_percent"] == expected


def test_exit_fields_and_dropped_tick_legs():
    call = receipt('strategy.exit("X", limit=close, stop=low, profit=10, loss=5, trail_points=4, trail_offset=2, trail_price=high, qty=1, comment="exit", oca_name="O")')["calls"][0]
    assert call == {
        "site": 0, "call": "exit", "context": "straight", "form": "levels", "id": "literal",
        "from_entry": "global", "target": "dangling", "order": "mixed",
        "limit": "never_na", "stop": "never_na", "trail_points": "literal", "trail_offset": "literal",
        "trail_price": "never_na", "qty": "literal", "qty_percent": "absent", "comment": "literal",
        "oca_name": "literal", "profit_ticks": "absent", "loss_ticks": "absent",
    }
    ticks = receipt('strategy.exit("X", profit=10, loss=5)')["calls"][0]
    assert ticks["profit_ticks"] == ticks["loss_ticks"] == "literal"
    assert ticks["limit"] == ticks["stop"] == "absent"
    cancellation = receipt('strategy.exit("X", qty=2, qty_percent=50, oca_name="O")', check_support=False)["calls"][0]
    assert set(cancellation) == {"site", "call", "context", "form", "id", "from_entry", "comment", "target", "order"}
    assert cancellation["form"] == "cancel_bracket"


@pytest.mark.parametrize("call", ["entry", "order", "exit", "close", "cancel"])
@pytest.mark.parametrize(("identifier", "expected"), [
    ('"private-literal-ID"', "literal"), ('""', "empty"), ('input.string("L")', "dynamic"),
])
def test_ids(call, identifier, expected):
    terms = ", strategy.long" if call in ("entry", "order") else ", limit=close" if call == "exit" else ""
    result = receipt(f"strategy.{call}({identifier}{terms})")
    assert result["calls"][0]["id"] == expected
    assert "private-literal-ID" not in json.dumps(result)


@pytest.mark.parametrize(("identifier", "expected"), [
    ('"L"', "named"), ('""', "global"), ('input.string("L")', "dynamic"),
])
def test_from_entry_ids(identifier, expected):
    assert receipt(f'strategy.exit("X", from_entry={identifier}, limit=close)')["calls"][0]["from_entry"] == expected


@pytest.mark.parametrize(("direction", "expected"), [
    ("strategy.long", "long"), ("strategy.short", "short"),
    ("close > open", "dynamic"),
])
def test_directions(direction, expected):
    assert receipt(f'strategy.entry("L", {direction})')["calls"][0]["direction"] == expected


@pytest.mark.parametrize(("body", "context"), [
    ('strategy.entry("L", strategy.long)', "straight"),
    ('if close > open\n    strategy.entry("L", strategy.long)', "straight"),
    ('switch\n    close > open => strategy.entry("L", strategy.long)', "straight"),
    ('for count = 0 to 1\n    strategy.entry("L", strategy.long)', "repeatable"),
    ('for price in array.from(close)\n    strategy.entry("L", strategy.long)', "repeatable"),
    ('count = 0\nwhile count < 1\n    strategy.entry("L", strategy.long)\n    count += 1', "repeatable"),
    ('f(float price) =>\n    strategy.entry("L", strategy.long, limit=price)\n    price\nvalue = f(close)', "repeatable"),
    ('method enter(float price) =>\n    strategy.entry("L", strategy.long, limit=price)\n    price\nvalue = close.enter()', "repeatable"),
])
def test_context(body, context):
    assert receipt(body)["calls"][0]["context"] == context


def test_each_emitted_helper_clone_has_an_order_descriptor():
    body = '''f(float price) =>
    strategy.entry("L", strategy.long, limit=price)
    ta.sma(price, 2)
value = f(close) + f(open)'''
    cpp = transpile(SOURCE + body)
    result = receipt(body)
    assert cpp.count("strategy_entry(") == len(result["calls"]) == 2
    assert all(call["context"] == "repeatable" for call in result["calls"])
    assert result["entry_ids"]["multi_site"] == 1
    assert result["unmodeled"] == []


def test_immutable_global_is_not_mutated_by_a_same_named_local():
    body = '''value = 5
f(float value) =>
    value := value + 1
    value
changed = f(close)
strategy.exit("X", limit=value)'''
    assert receipt(body)["calls"][0]["limit"] == "literal"


def test_block_shadows_do_not_leak_to_sibling_or_outer_sites():
    body = '''value = 5.0
if close > open
    if high > low
        value = close
        strategy.exit("local", limit=value)
    strategy.exit("outer", limit=value)
else
    strategy.exit("sibling", limit=value)'''
    assert [call["limit"] for call in receipt(body)["calls"]] == ["maybe_na", "literal", "literal"]


def test_statement_order_and_relations():
    result = receipt('''strategy.exit("before", "L", limit=high)
strategy.entry("L", strategy.long)
strategy.exit("after", "L", stop=low)
strategy.entry("S", strategy.short)
strategy.entry("shared", strategy.long)
strategy.exit("mixed", "shared", limit=high)
strategy.entry("shared", strategy.short)
strategy.close("L")
strategy.cancel("S")
strategy.exit("global", limit=high)
strategy.exit("dangling", "absent-id", stop=low)''')
    calls = result["calls"]
    assert [calls[index]["order"] for index in (0, 2, 5)] == ["before", "after", "mixed"]
    assert [calls[index]["target"] for index in (0, 2, 5, 7, 8, 9, 10)] == ["long", "long", "both", "long", "short", "both", "dangling"]
    assert result["entry_ids"] == {"long": 2, "short": 2, "shared": 1, "multi_site": 1}
    assert [call["site"] for call in calls] == list(range(len(calls)))


def test_empty_cancel_is_not_a_global_close_target():
    result = receipt('''strategy.entry("L", strategy.long)
strategy.close("")
strategy.cancel("")
strategy.entry("", strategy.short)
strategy.cancel("")''')
    assert [result["calls"][index]["target"] for index in (1, 2, 4)] == ["both", "short", "short"]
    without_empty_entry = receipt('strategy.entry("L", strategy.long)\nstrategy.cancel("")')
    assert without_empty_entry["calls"][1]["target"] == "dangling"


def test_close_all_and_cancel_all_parameters():
    result = receipt('strategy.close_all(comment="bye", immediately=true)\nstrategy.cancel_all()')
    assert result["calls"] == [
        {"site": 0, "call": "close_all", "context": "straight", "comment": "literal", "qty": "absent", "qty_percent": "absent", "immediately": "literal:true"},
        {"site": 1, "call": "cancel_all", "context": "straight"},
    ]


def test_order_zero_quantity_and_oca_parameters():
    call = receipt('strategy.order("O", strategy.short, oca_name="group", oca_type=strategy.oca.reduce)')["calls"][0]
    assert call["qty"] == "absent"
    assert call["oca_name"] == "literal" and call["oca_type"] == "literal:2"
    assert set(call) == {"site", "call", "context", "id", "direction", "qty", "limit", "stop", "oca_name", "oca_type"}


def test_unmodeled_and_read_only_calls():
    result = receipt('''strategy.risk.max_position_size(10)
strategy.risk.max_intraday_filled_orders(5)
profit = strategy.closedtrades.profit(0)
value = strategy.convert_to_account(close)
strategy.cancel_all()''')
    assert result["unmodeled"] == ["strategy.risk.max_intraday_filled_orders", "strategy.risk.max_position_size"]


def test_settings_echo_and_pooc_false():
    result = receipt('strategy.cancel_all()', '//@version=6\nstrategy("default")\n')
    assert result["process_orders_on_close"] is False
    assert result["settings"] == {
        "initial_capital": 100000.0, "default_qty_type": "percent_of_equity", "default_qty_value": 100.0,
        "pyramiding": 1, "commission_type": "percent", "commission_value": 0.0, "slippage": 0,
        "margin_long": 100.0, "margin_short": 100.0, "close_entries_rule": "FIFO",
    }
    customized = receipt('', '//@version=6\nstrategy("settings", process_orders_on_close=true, initial_capital=12345, default_qty_type=strategy.cash, default_qty_value=20, pyramiding=2, commission_type=strategy.commission.cash_per_contract, commission_value=1.5, slippage=2, margin_long=0, margin_short=50, close_entries_rule="ANY")\n')
    assert customized["process_orders_on_close"] is True
    assert customized["settings"] == {
        "initial_capital": 12345.0, "default_qty_type": "cash", "default_qty_value": 20.0,
        "pyramiding": 2, "commission_type": "cash_per_contract", "commission_value": 1.5,
        "slippage": 2, "margin_long": 0.0, "margin_short": 50.0, "close_entries_rule": "ANY",
    }


@pytest.mark.parametrize("body", [
    'strategy.entry("L", strategy.long)',
    'value = time("1", bars_back=-1)\nstrategy.entry("L", strategy.long)',
    'if session.isfirstbar or session.islastbar or session.isfirstbar_regular or session.islastbar_regular\n    strategy.entry("L", strategy.long)',
    'if barstate.islast or barstate.islastconfirmedhistory\n    strategy.entry("L", strategy.long)',
    'value = last_bar_index + bar_index\nstrategy.entry("L", strategy.long)',
    'value = request.security(syminfo.tickerid, "5", close)\nstrategy.entry("L", strategy.long)',
    'value = session.ismarket\nstrategy.entry("L", strategy.long)',
    'strategy.entry("L", strategy.long, comment="session_islastbar_")\nplot(close, title="pine_time_offset")',
])
def test_host_emission_superset_of_token_crosscheck(body):
    cpp = transpile(SOURCE + body)
    result = receipt(body)
    assert scan_host_reads(cpp) <= set(result["host_reads"])


def test_host_strings_do_not_create_reads():
    result = receipt('strategy.entry("L", strategy.long, comment="session_islastbar_")\nplot(close, title="pine_time_offset")')
    assert "session_islastbar_" not in result["host_reads"]
    assert "pine_time_offset" not in result["host_reads"]
    reads = set()
    lines = HostReadLines(reads)
    lines.extend(['/* session_islastbar_', 'pine_time_offset */', 'std::string("barstate_islast_");', "current_bar_.close;"])
    assert reads == {"current_bar_"}


def test_host_recording_does_not_use_the_tu_crosscheck(monkeypatch):
    def forbidden_scan(_cpp):
        pytest.fail("emission must not depend on the cross-check scanner")

    monkeypatch.setattr("pineforge_codegen.codegen.order_shapes.scan_host_reads", forbidden_scan)
    result = receipt('value = session.ismarket\nstrategy.entry("L", strategy.long)')
    assert "current_bar_" in result["host_reads"]


@pytest.mark.parametrize(("expression", "expected"), [
    ('""', "absent"), ('"exit"', "literal"), ('input.string("exit")', "dynamic"),
])
def test_comment_classes(expression, expected):
    assert receipt(f'strategy.close("L", comment={expression})')["calls"][0]["comment"] == expected


@pytest.mark.parametrize(("expression", "expected"), [
    ("false", "absent"), ("true", "literal:true"), ("input.bool(false)", "dynamic"),
])
def test_immediately_classes(expression, expected):
    assert receipt(f'strategy.close_all(immediately={expression})')["calls"][0]["immediately"] == expected


def test_legacy_receipts_are_byte_identical():
    fixture = Path(__file__).parent / "fixtures" / "order_shapes_legacy.json"
    frozen = json.loads(fixture.read_text())
    documents = re.findall(RECEIPT_PATTERN, transpile(frozen["source"]))
    assert [json.loads(document) for document in documents[:2]] == frozen["documents"]


def test_order_shapes_exports_compile():
    compile_cpp(transpile(SOURCE + 'strategy.entry("L", strategy.long)\nstrategy.close("L")'), label="order_shapes")
