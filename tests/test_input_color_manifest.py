"""#231: color defaults can be sent straight back to the native input setter."""

import csv
import json
import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile_full
from pineforge_codegen.codegen.tables import COLOR_CONST_MAP
from tests._compile import run_emitted_tu
from tests.test_input_manifest_values import _titled
from tests.test_string_settings_constants import RECEIPT_DRIVER


REPORTER_SOURCE = """//@version=6
strategy('Color input')
lineColor = input.color(color.red, 'Line color')
plot(close, color=lineColor)
"""

# Hand-transcribed from the independent TradingView v6 readout in
# fixtures/color_tv/w11-color-v6-eth15/{strategy.pine,tv_trades.csv}: n0, n1,
# n2 spell each named color's r,g,b,t. All seventeen have transparency 0.
# This is test evidence, never the production palette or an engine substitute.
V6_REFERENCE = [
    ("aqua", 0xFF00BCD4), ("black", 0xFF363A45), ("blue", 0xFF2962FF),
    ("fuchsia", 0xFFE040FB), ("gray", 0xFF787B86), ("green", 0xFF4CAF50),
    ("lime", 0xFF00E676), ("maroon", 0xFF880E4F), ("navy", 0xFF311B92),
    ("olive", 0xFF808000), ("orange", 0xFFFF9800), ("purple", 0xFF9C27B0),
    ("red", 0xFFF23645), ("silver", 0xFFB2B5BE), ("teal", 0xFF089981),
    ("white", 0xFFFFFFFF), ("yellow", 0xFFFDD835),
]

COLOR_EXPRESSIONS = [
    ("#123456", 0xFF123456), ("#12345680", 0x80123456),
    ("color.new(color.red, 0)", 0xFFF23645),
    ("color.new(color.red, 50)", 0x80F23645),
    ("color.new(color.red, 100)", 0x00F23645),
    ("color.new(color.red, 10.5)", 0xE4F23645),
    ("color.new(transp=50, color=color.blue)", 0x802962FF),
    ("color.new(color.new(color.red, 50), 25)", 0xBFF23645),
    ("color.rgb(12, 34, 56)", 0xFF0C2238),
    ("color.rgb(12, 34, 56, 50)", 0x800C2238),
    ("color.rgb(12, 34, 56, 100)", 0x000C2238),
    ("color.rgb(blue=56, transp=10.5, red=12, green=34)", 0xE40C2238),
]


def _source(expression):
    return ("//@version=6\nstrategy('Color input')\n"
            f"lineColor = input.color({expression}, 'Line color')\n"
            "plot(close, color=lineColor)\n")


def _assert_color(entry, expected):
    # Keep the type first: commit 1 is red on the reported bug, before any
    # native build or interpretation of the formerly textual default.
    assert entry["type"] == "color"
    assert type(entry["default"]) is int
    assert entry["default"] == expected
    assert entry["supported"] is True
    assert entry == {"title": "Line color", "type": "color",
                     "default": expected, "supported": True}


def test_reporters_exact_script_publishes_a_round_trippable_default():
    _assert_color(_titled(REPORTER_SOURCE)["Line color"], 4294063685)


def test_reference_palette_agrees_with_the_recorded_pine_v6_readout():
    assert set(dict(V6_REFERENCE)) == set(COLOR_CONST_MAP)
    fixture = Path(__file__).parent / "fixtures" / "color_tv" / "w11-color-v6-eth15"
    source = (fixture / "strategy.pine").read_text()
    with (fixture / "tv_trades.csv").open(encoding="utf-8-sig") as stream:
        comments = {row["Signal"] for row in csv.DictReader(stream)
                    if row["Type"].startswith("Exit")}
    for group in ("n0", "n1", "n2"):
        line = next(line for line in source.splitlines() if line.startswith(group + " ="))
        names = re.findall(r"q\(color\.(\w+)\)", line)
        values = [dict(V6_REFERENCE)[name] for name in names]
        channels = [",".join(str((value >> shift) & 0xFF) for shift in (16, 8, 0)) + ",0"
                    for value in values]
        assert group + " " + "|".join(channels) in comments


@pytest.mark.parametrize("name,cpp_constant", COLOR_CONST_MAP.items())
def test_every_codegen_color_constant_has_an_integer_default(name, cpp_constant):
    source = _source(f"color.{name}")
    _assert_color(_titled(source)["Line color"], dict(V6_REFERENCE)[name])
    # The manifest correction must preserve the codegen's existing default.
    assert f'get_input_int64("Line color", {cpp_constant})' in transpile_full(source)["cpp"]


@pytest.mark.parametrize("name,expected", V6_REFERENCE)
def test_every_reference_color_constant_has_its_pine_v6_value(name, expected):
    _assert_color(_titled(_source(f"color.{name}"))["Line color"], expected)


@pytest.mark.parametrize("expression,expected", COLOR_EXPRESSIONS)
def test_literal_color_builders_publish_argb_with_transparency(expression, expected):
    _assert_color(_titled(_source(expression))["Line color"], expected)


@pytest.mark.parametrize("expression", [
    "color.new(color.red, close)", "color.rgb(close, 34, 56)",
])
def test_unfoldable_color_builders_keep_the_previous_default(expression):
    entry = _titled(_source(expression))["Line color"]
    assert entry["type"] == "color"
    assert entry["default"] is None
    assert entry["supported"] is True


def test_manifest_default_round_trips_through_the_paired_engine_setter_and_run():
    full = transpile_full(REPORTER_SOURCE)
    entry, = full["inputs"]
    _assert_color(entry, 4294063685)
    # The setter takes text on the C boundary. Serialize the manifest's own
    # integer, rather than substituting a known-good color in the driver.
    driver = r'''
#include <cassert>
int main() {
    void* strategy = nullptr;
    char error[512]{};
    assert(strategy_create_checked(nullptr, &strategy, error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_input_checked(strategy, "Line color", DEFAULT_TEXT,
                                      error, sizeof(error)) == PF_SETTINGS_OK);
    assert(error[0] == '\0');
    Bar bars[]{{100, 101, 99, 100, 1000, 1577836800000LL}};
    ReportC report{};
    assert(run_backtest_full_checked(strategy, bars, 1, "1", "1", 0, 4, 0,
                                     &report, error, sizeof(error)) == PF_SETTINGS_OK);
    assert(error[0] == '\0');
    assert(std::string(strategy_get_last_error(strategy)).empty());
    assert(static_cast<GeneratedStrategy*>(strategy)->lineColor == DEFAULT_INTEGER);
    report_free(&report);
    strategy_free(strategy);
}
'''
    driver = driver.replace("DEFAULT_TEXT", json.dumps(str(entry["default"])))
    driver = driver.replace("DEFAULT_INTEGER", str(entry["default"]) + "LL")
    run_emitted_tu(full["cpp"], driver, opt="-O0", label="input.color manifest round trip #231")


def test_all_literal_color_defaults_equal_the_paired_engines_compiled_values():
    # The engine evaluates the C++ symbols/builders the codegen already emits;
    # compare every manifest value with that independent native evaluation.
    expressions = [(f"color.{name}", value) for name, value in V6_REFERENCE] + COLOR_EXPRESSIONS
    source = ("//@version=6\nstrategy('Color defaults')\n" + "\n".join(
        f'c{i} = input.color({expression}, "C{i}")'
        for i, (expression, _) in enumerate(expressions)) + "\nplot(close)\n")
    full = transpile_full(source)
    assert all(entry["type"] == "color" for entry in full["inputs"])
    receipt = json.loads(run_emitted_tu(full["cpp"], RECEIPT_DRIVER, opt="-O0",
                                       label="input.color compiled defaults #231"))
    for entry, checked, (_, expected) in zip(full["inputs"], receipt["inputs"], expressions):
        assert entry["title"] == checked["name"]
        assert entry["default"] == int(checked["default"]) == expected
        assert entry["supported"] is checked["supported"] is True


def test_every_other_input_type_keeps_its_existing_manifest_entry():
    # Reuse the declarations/expected values pinned in test_extract_inputs,
    # test_input_manifest_values (including input_metadata_numeric.pine),
    # test_request_discovery's symbol pin and test_codegen_input_getters.
    # Add only a color row; numeric spellings and all existing fields stay put.
    source = '''//@version=6
strategy("Other input metadata")
enum Side
    long
    short
RATIO = 0.5
signedInt = input.int(-5, "Signed int", minval=-10, maxval=-1, step=1)
constFloat = input.float(RATIO, "Float constant", minval=-RATIO, maxval=RATIO)
enabled = input.bool(true, "Enabled")
frequency = input.string(alert.freq_all, "Frequency", options=[alert.freq_all, alert.freq_once_per_bar])
source = input.source(hl2, "Source")
side = input.enum(Side.short, "Side")
tf = input.timeframe("D", "A", options=["D", "W"])
sessionChoice = input.session("0930-1600", "B", options=["0930-1600", "0000-2400"])
s = input.symbol("BINANCE:ETHUSDT", "S")
foldedTime = input.time(timestamp("2024-01-02T00:00:00"), "Folded time")
text = input.text_area("x", "ta")
plainPrice = input.price(100.5, "Price")
lineColor = input.color(color.red, "Line color")
plot(close)
'''
    expected = [
        {"title": "Signed int", "type": "int", "default": -5,
         "min": -10, "max": -1, "step": 1, "supported": True},
        {"title": "Float constant", "type": "float", "default": 0.5,
         "min": -0.5, "max": 0.5, "supported": True},
        {"title": "Enabled", "type": "bool", "default": True, "supported": True},
        {"title": "Frequency", "type": "string", "default": "all",
         "options": ["all", "once_per_bar"], "supported": True},
        {"title": "Source", "type": "source", "default": "hl2",
         "options": ["close", "high", "hl2", "hlc3", "hlcc4", "low", "ohlc4", "open", "volume"],
         "supported": True},
        {"title": "Side", "type": "enum", "default": "Side.short",
         "options": ["Side.long", "Side.short"], "supported": True},
        {"title": "A", "type": "string", "default": "D", "options": ["D", "W"], "supported": True},
        {"title": "B", "type": "string", "default": "0930-1600",
         "options": ["0930-1600", "0000-2400"], "supported": True},
        {"title": "S", "type": "string", "default": "BINANCE:ETHUSDT", "kind": "symbol", "supported": True},
        {"title": "Folded time", "type": "int", "default": 1704153600000, "supported": True},
        {"title": "ta", "type": "string", "default": "x", "supported": True},
        {"title": "Price", "type": "float", "default": 100.5, "supported": True},
        {"title": "Line color", "type": "color", "default": 4294063685, "supported": True},
    ]
    # Compare JSON too: Python equality alone would miss int -> float changes.
    actual = list(_titled(source).values())
    assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)
