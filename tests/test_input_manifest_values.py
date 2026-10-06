"""The values the transpile-time input manifest publishes (no native build).

These are the values of existing keys that the checked-settings receipt's
descriptor decides; `tests/test_input_metadata_receipt.py` pins each one
against the compiled receipt, these pin them where no engine is built.
"""

import json
import runpy
from pathlib import Path

import pytest

from pineforge_codegen import transpile_full
from pineforge_codegen.codegen.checked_settings import _numeric_setting_value


ROOT = Path(__file__).resolve().parents[1]
GLUE = runpy.run_path(str(ROOT / "gate" / "glue.py"))["transpile_json"]
NATIVE_SOURCES = ["close", "high", "hl2", "hlc3", "hlcc4", "low", "ohlc4", "open", "volume"]


def _titled(source: str) -> dict:
    inputs = transpile_full(source)["inputs"]
    assert inputs == json.loads(GLUE(source))["inputs"]
    return {entry["title"]: entry for entry in inputs}


def _inputs(body: str) -> dict:
    return _titled(f'//@version=6\nstrategy("t")\n{body}\nplot(close)\n')


def _numeric_fixture() -> dict:
    return _titled((ROOT / "tests" / "fixtures" / "input_metadata_numeric.pine").read_text())


def test_every_input_has_a_support_boolean():
    inputs = _inputs('a = input.int(1, "A")\nb = input.bool(true, "B")\n'
                     'c = input.string("x", "C")\nd = input(close, "D")\n')
    assert all(type(entry["supported"]) is bool for entry in inputs.values())


def test_source_inputs_publish_their_series_and_the_native_choices():
    inputs = _inputs('a = input.source(hl2, "A")\nb = input(close, "B")\n')
    assert inputs["A"] == {"title": "A", "type": "source", "default": "hl2",
                           "options": NATIVE_SOURCES, "supported": True}
    # A plain input(<series>) keeps its string type; its default and choices are
    # the source's.
    assert inputs["B"] == {"title": "B", "type": "string", "default": "close",
                           "options": NATIVE_SOURCES, "supported": True}


def test_enum_inputs_publish_their_choices_and_keep_the_member_default():
    inputs = _inputs('enum Side\n    long\n    short\nside = input.enum(Side.short, "Side")\n')
    assert inputs["Side"] == {"title": "Side", "type": "enum", "default": "Side.short",
                              "options": ["Side.long", "Side.short"], "supported": True}


@pytest.mark.parametrize("literal,value", [
    ("alert.freq_all", "all"), ("alert.freq_once_per_bar", "once_per_bar"),
    ("currency.USD", "USD"), ("format.price", "price"),
    ("order.ascending", "ascending"), ("session.regular", "regular")])
def test_builtin_string_constants_publish_their_runtime_values(literal, value):
    inputs = _inputs(f'a = input.string({literal}, "A", options=[{literal}, "other"])\n')
    assert inputs["A"]["default"] == value
    assert inputs["A"]["options"] == [value, "other"]
    assert inputs["A"]["supported"] is True


def test_named_string_constants_publish_their_values():
    inputs = _inputs('SD = "abc"\na = input.string(SD, "A")\n'
                     'b = input.string("x", "B", options=["x", SD])\n')
    assert inputs["A"]["default"] == "abc" and inputs["A"]["supported"] is True
    assert inputs["B"]["options"] == ["x", "abc"]


def test_timeframe_and_session_choices_are_listed():
    inputs = _inputs('a = input.timeframe("D", "A", options=["D", "W"])\n'
                     'b = input.session("0930-1600", "B", options=["0930-1600", "0000-2400"])\n')
    assert inputs["A"]["options"] == ["D", "W"]
    assert inputs["B"]["options"] == ["0930-1600", "0000-2400"]


def test_an_empty_options_list_is_published_for_a_supported_input():
    entry = _inputs('a = input.string("a", "A", options=[])\n')["A"]
    assert entry["supported"] is True and entry["options"] == []


def test_strings_with_escapes_round_trip_through_the_descriptor():
    # The descriptor decodes the C++ literals the codegen writes: pin the escape
    # set (backslash, quote, newline, tab), raw control characters and non-ASCII.
    text = 'a"b\\c\td\ne é 😀 \x01\x7f what??/'
    pine = (text.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
            .replace("\n", "\\n"))
    entry = _inputs(f'a = input.string("{pine}", "A", options=["{pine}", "b"])\n')["A"]
    assert entry["default"] == text
    assert entry["options"] == [text, "b"]


def test_plain_signed_input_is_typed_by_its_literal():
    # input(-5) used to be {"type": "string", "default": None}: the unary minus
    # hid the literal. The receipt reads a signed plain input through its
    # double getter (kind "float"); the manifest types it as its literal is.
    inputs = _inputs('a = input(-5, "A")\nb = input(-2.5, "B")\nc = input(+3, "C")\n'
                     'd = input(5, "D")\ne = input(2.5, "E")\n')
    assert inputs["A"] == {"title": "A", "type": "int", "default": -5, "supported": True}
    assert inputs["B"] == {"title": "B", "type": "float", "default": -2.5, "supported": True}
    assert inputs["C"] == {"title": "C", "type": "int", "default": 3, "supported": True}
    assert inputs["D"] == {"title": "D", "type": "int", "default": 5, "supported": True}
    assert inputs["E"] == {"title": "E", "type": "float", "default": 2.5, "supported": True}


def test_signed_defaults_of_typed_inputs_are_numbers():
    inputs = _inputs('a = input.float(-2.5, "A")\nb = input.int(-100, "B")\n')
    assert inputs["A"]["default"] == -2.5 and inputs["B"]["default"] == -100


def test_numeric_literals_the_receipt_holds_are_published():
    inputs = _numeric_fixture()
    assert inputs["Dropdown int"]["options"] == [1, 5, 10]
    assert inputs["Dropdown float"]["options"] == [-1.5, 0.5, 2.0]
    assert inputs["Dropdown constant"]["options"] == [14, 5]
    assert inputs["Signed int"] == {"title": "Signed int", "type": "int", "default": -5,
                                    "min": -10, "max": -1, "step": 1, "supported": True}
    assert inputs["Signed float"]["min"] == -5.0
    assert inputs["Float constant"] == {"title": "Float constant", "type": "float",
                                        "default": 0.5, "min": -0.5, "max": 0.5,
                                        "supported": True}
    assert inputs["Int constant"] == {"title": "Int constant", "type": "int", "default": 14,
                                      "min": 1, "supported": True}
    assert inputs["Folded time"]["default"] == 1704153600000
    assert inputs["Price"]["default"] == 100.5


def test_values_the_receipt_computes_at_run_time_keep_the_previous_manifest():
    inputs = _numeric_fixture()
    assert inputs["Negated bool"] == {"title": "Negated bool", "type": "bool",
                                      "default": None, "supported": True}
    assert inputs["Tint"] == {"title": "Tint", "type": "string", "default": "color.red",
                              "supported": True}
    assert inputs["Expression default"]["default"] is None
    assert "options" not in inputs["Expression option"]
    assert inputs["Expression option"]["default"] == 5
    assert "min" not in inputs["Expression bound"] and "max" not in inputs["Expression bound"]
    assert inputs["Time from parts"]["default"] is None
    assert inputs["Plain constant"] == {"title": "Plain constant", "type": "string",
                                        "default": None, "supported": True}


@pytest.mark.parametrize("text,value", [
    ("5", 5), ("(-5)", -5), ("(+1.5)", 1.5), ("2.0", 2.0), ("1e3", 1000.0),
    ("1704153600000LL", 1704153600000), ("0.25f", 0.25), ("((-2))", -2),
    ("true", True), ("false", False)])
def test_numeric_literals_decode(text, value):
    decoded = _numeric_setting_value(text)
    assert decoded == value and type(decoded) is type(value)


@pytest.mark.parametrize("text", [
    "", "!(true)", "(14 * 2)", "(1) + (2)", "pine_color::red", "std::pow(2, 3)",
    "std::numeric_limits<double>::quiet_NaN()", "na<double>()", "0x10", "1 2", "--5"])
def test_expressions_do_not_decode(text):
    assert _numeric_setting_value(text) is None
