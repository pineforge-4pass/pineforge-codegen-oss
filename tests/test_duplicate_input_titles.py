"""Per-declaration override keys; compile and execute the actual emitted TU."""

import json

import pytest

import pineforge_codegen as api
from pineforge_codegen import transpile, transpile_full
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("duplicate input titles")\n'
PAIR = HEADER + 'first = input.int(10, "Length")\nsecond = input.int(20, "Length")\n'


@pytest.mark.parametrize("optimization", ["-O0", "-O2"])
@pytest.mark.parametrize("title", ["Length", 'Span "quote" \\path'])
def test_checked_setter_addresses_duplicate_titles_independently(optimization, title):
    driver = r'''
#include <cassert>
#include <iostream>
#include <vector>
int main() {
    void* strategy = nullptr;
    char error[256]{};
    assert(strategy_create_checked(nullptr, &strategy, error, sizeof(error)) == PF_SETTINGS_OK);
    auto receipt = [&]() {
        size_t required = 0;
        assert(strategy_get_effective_settings(strategy, nullptr, 0, &required, error, sizeof(error)) == PF_SETTINGS_BUFFER_TOO_SMALL);
        std::vector<char> text(required);
        assert(strategy_get_effective_settings(strategy, text.data(), text.size(), &required, error, sizeof(error)) == PF_SETTINGS_OK);
        std::cout << text.data() << '\n';
    };
    receipt();
    assert(strategy_set_input_checked(strategy, "Length", "7", error, sizeof(error)) == PF_SETTINGS_OK);
    receipt();
    assert(strategy_set_input_checked(strategy, "Length#2", "23", error, sizeof(error)) == PF_SETTINGS_OK);
    for (const char* key : {"Length#3", "missing"}) {
        assert(strategy_set_input_checked(strategy, key, "99", error, sizeof(error)) == PF_SETTINGS_INVALID_ARGUMENT);
        assert(std::string(error) == "unknown input key");
    }
    receipt();
    Bar bars[]{{100, 120, 80, 110, 1000, 1577836800000LL}};
    ReportC report{};
    run_backtest(strategy, bars, 1, &report);
    assert(static_cast<GeneratedStrategy*>(strategy)->first == 7);
    assert(static_cast<GeneratedStrategy*>(strategy)->second == 23);
    report_free(&report);
    strategy_free(strategy);
}
'''
    for ordinal in (3, 2, 1):
        suffix = "" if ordinal == 1 else f"#{ordinal}"
        driver = driver.replace(json.dumps("Length" + suffix), json.dumps(title + suffix))
    receipts = [json.loads(line) for line in run_emitted_tu(
        transpile(PAIR.replace('"Length"', json.dumps(title))), driver,
        opt=optimization, label="duplicate checked inputs"
    ).splitlines()]
    assert [
        [(entry["name"], entry["effective_value"]) for entry in receipt["inputs"]]
        for receipt in receipts
    ] == [
        [(title, "10"), (title + "#2", "20")],
        [(title, "7"), (title + "#2", "20")],
        [(title, "7"), (title + "#2", "23")],
    ]


@pytest.mark.parametrize(
    "call,first_value,second_value,first_cpp,second_cpp,source_input",
    [
        ('input.int(10, "Control", options=[7, 10, 23])', "7", "23", "7", "23", False),
        ('input.float(1.0, "Control")', "1.5", "2.5", "1.5", "2.5", False),
        ('input.price(1.0, "Control")', "1.5", "2.5", "1.5", "2.5", False),
        ('input.bool(true, "Control")', "false", "true", "false", "true", False),
        ('input.string("a", "Control", options=["a", "b"])', "a", "b", '"a"', '"b"', False),
        ('input.text_area("a", "Control")', "alpha", "beta", '"alpha"', '"beta"', False),
        ('input.timeframe("60", "Control")', "120", "240", '"120"', '"240"', False),
        ('input.symbol("NASDAQ:AAPL", "Control")', "NASDAQ:MSFT", "NASDAQ:NVDA",
         '"NASDAQ:MSFT"', '"NASDAQ:NVDA"', False),
        ('input.session("0900-1600", "Control")', "1000-1500", "1100-1400",
         '"1000-1500"', '"1100-1400"', False),
        ('input.time(1577836800000, "Control")', "1700000000000", "1800000000000",
         "1700000000000LL", "1800000000000LL", False),
        ('input.color(#ff0000, "Control")', "4294901760", "4278190335",
         "4294901760LL", "4278190335LL", False),
        ('input.enum(Choice.a, "Control", options=[Choice.a, Choice.b])',
         "Choice.a", "Choice.b", "0", "1", False),
        ('input.source(close, "Control")', "open", "high", "100", "120", True),
        ('input(close, "Control")', "open", "high", "100", "120", True),
        ('input(10, "Control")', "7", "23", "7", "23", False),
        ('input(1.0, "Control")', "1.5", "2.5", "1.5", "2.5", False),
        ('input(true, "Control")', "false", "true", "false", "true", False),
        ('input("a", "Control")', "alpha", "beta", '"alpha"', '"beta"', False),
    ],
)
def test_duplicate_titles_across_input_kinds(
    call, first_value, second_value, first_cpp, second_cpp, source_input
):
    source = HEADER + "enum Choice\n    a\n    b\n" + f"first = {call}\nsecond = {call}\n"
    if source_input:
        source += "plot(first[1] + second[1])\n"  # Keep source histories observable.
    result = transpile_full(source)
    assert [entry["title"] for entry in result["inputs"]] == ["Control", "Control#2"]
    subscript = "[0]" if source_input else ""
    driver = r'''
#include <cassert>
int main() {
    void* strategy = strategy_create(nullptr);
    char error[256]{};
    assert(strategy_set_input_checked(strategy, "Control", FIRST_VALUE, error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_input_checked(strategy, "Control#2", SECOND_VALUE, error, sizeof(error)) == PF_SETTINGS_OK);
    Bar bars[]{{100, 120, 80, 110, 1000, 1577836800000LL}};
    ReportC report{};
    run_backtest(strategy, bars, 1, &report);
    auto* generated = static_cast<GeneratedStrategy*>(strategy);
    assert(generated->firstSUBSCRIPT == FIRST_CPP);
    assert(generated->secondSUBSCRIPT == SECOND_CPP);
    report_free(&report);
    strategy_free(strategy);
}
'''
    for token, value in {
        "FIRST_VALUE": json.dumps(first_value), "SECOND_VALUE": json.dumps(second_value),
        "FIRST_CPP": first_cpp, "SECOND_CPP": second_cpp, "SUBSCRIPT": subscript,
    }.items():
        driver = driver.replace(token, value)
    run_emitted_tu(result["cpp"], driver, opt="-O0", label=f"duplicate {call}")


def test_key_order_uses_exact_constant_titles_and_leaves_untitled_calls_alone():
    source = HEADER + '''const string TITLE = "Le" + "ngth"
first = input.int(10, TITLE, group="A")
other = input.int(11, "length")
nested = input.int(20, title="Length", group="B") + input.int(30, TITLE)
mixed = input.string("text", TITLE)
untitled = input.int(40)
pair = input.int(50) + input.int(60)
empty1 = input.int(70, "")
empty2 = input.int(80, "")
'''
    result = transpile_full(source)
    assert [entry["title"] for entry in result["inputs"]] == [
        "Length", "length", "Length#2", "Length#3", "Length#4",
        "untitled", "pair", "pair", "", "#2",
    ]
    # Only the unchanged untitled collision remains warned.
    warnings = [d.message for d in result["diagnostics"] if d.message.startswith("input key")]
    assert len(warnings) == 1 and "input key 'pair'" in warnings[0]


@pytest.mark.parametrize(
    "titles,expected_keys",
    [
        (["Length", "Length", "Length#2"], ["Length", "Length#3", "Length#2"]),
        (["Length#2", "Length", "Length"], ["Length#2", "Length", "Length#3"]),
        (
            ["Span", "Span", "Span", "Span#2", "Span#3"],
            ["Span", "Span#4", "Span#5", "Span#2", "Span#3"],
        ),
    ],
)
def test_duplicate_keys_skip_authored_titles_in_any_declaration_order(titles, expected_keys):
    def source(input_titles):
        return HEADER + "".join(
            f"value{i} = input.int({10 + i}, {json.dumps(title)})\n"
            for i, title in enumerate(input_titles)
        )

    result = transpile_full(source(titles))
    assert [entry["title"] for entry in result["inputs"]] == expected_keys
    assert not [d for d in result["diagnostics"] if d.message.startswith("input key")]
    # An independently authored script with the required keys emits the same TU.
    assert result["cpp"] == transpile_full(source(expected_keys))["cpp"]


def test_legacy_setter_bare_duplicate_title_sets_only_the_first_input():
    driver = r'''
#include <cassert>
int main() {
    void* strategy = strategy_create(nullptr);
    strategy_set_input(strategy, "Length", "7");
    strategy_set_input(strategy, "Length#2", "31");
    Bar bars[]{{100, 120, 80, 110, 1000, 1577836800000LL}};
    ReportC report{};
    run_backtest(strategy, bars, 1, &report);
    assert(static_cast<GeneratedStrategy*>(strategy)->first == 7);
    assert(static_cast<GeneratedStrategy*>(strategy)->second == 20);
    assert(static_cast<GeneratedStrategy*>(strategy)->authored == 31);
    report_free(&report);
    strategy_free(strategy);
}
'''
    source = PAIR + 'authored = input.int(30, "Length#2")\n'
    run_emitted_tu(transpile(source), driver, opt="-O0", label="legacy duplicate input")


def test_inline_ta_inputs_use_the_assigned_keys():
    result = transpile_full(HEADER + '''first = ta.sma(close, input.int(2, "Window"))
second = ta.sma(close, input.int(2, "Window"))
plot(first[1] + second[1])
''')
    assert [entry["title"] for entry in result["inputs"]] == ["Window", "Window#2"]


def test_request_copies_and_inventory_use_the_same_declaration_keys():
    source = HEADER + '''symbol1 = input.symbol("NASDAQ:AAPL", "Symbol")
symbol2 = input.symbol("NASDAQ:MSFT", "Symbol")
tf1 = input.timeframe("60", "Frame")
tf2 = input.timeframe("240", "Frame")
src1 = input.source(close, "Source")
src2 = input.source(high, "Source")
fetch(string symbol, string frame, value) =>
    request.security(symbol, frame, value)
value1 = fetch(symbol1, tf1, src1)
value2 = fetch(symbol2, tf2, src2)
plot(value1 + value2)
'''
    result = transpile_full(source)
    assert [entry["title"] for entry in result["inputs"]] == [
        "Symbol", "Symbol#2", "Frame", "Frame#2", "Source", "Source#2",
    ]
    reference = source.replace(
        'input.symbol("NASDAQ:MSFT", "Symbol")', 'input.symbol("NASDAQ:MSFT", "Symbol#2")'
    ).replace(
        'input.timeframe("240", "Frame")', 'input.timeframe("240", "Frame#2")'
    ).replace(
        'input.source(high, "Source")', 'input.source(high, "Source#2")'
    )
    expected = transpile_full(reference)
    assert result["cpp"] == expected["cpp"]
    # Display-only requests have no feed metadata. Check actual getter reads;
    # the complete C++ comparison above also covers the helper copies.
    cpp_lines = {line.strip() for line in result["cpp"].splitlines()}
    assert 'symbol2 = get_input_string("Symbol#2", std::string("NASDAQ:MSFT"));' in cpp_lines
    assert 'tf2 = get_input_string("Frame#2", std::string("240"));' in cpp_lines
    assert 'src2 = get_input_source("Source#2", _src_high_)[0];' in cpp_lines
    driver = r'''
#include <cassert>
int main() {
    void* strategy = strategy_create(nullptr);
    char error[256]{};
    assert(strategy_set_input_checked(strategy, "Symbol", "NASDAQ:NVDA", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_input_checked(strategy, "Symbol#2", "NASDAQ:GOOG", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_input_checked(strategy, "Frame", "120", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_input_checked(strategy, "Frame#2", "480", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_input_checked(strategy, "Source", "open", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_input_checked(strategy, "Source#2", "low", error, sizeof(error)) == PF_SETTINGS_OK);
    strategy_free(strategy);
}
'''
    run_emitted_tu(result["cpp"], driver, opt="-O0", label="duplicate request inputs")


def test_unique_title_cpp_and_complete_settings_text_are_unchanged(monkeypatch):
    import pineforge_codegen.analyzer.base as analyzer_base

    unique = HEADER + 'first = input.int(10, "Length")\nother = input.string("x", "Mode")\n'
    before = transpile_full(unique)
    duplicate = transpile_full(unique + 'second = input.int(20, "Length")\n')
    assert [entry["title"] for entry in duplicate["inputs"]] == ["Length", "Mode", "Length#2"]
    after = transpile_full(unique)
    assert after == before  # Complete C++ and manifest, also after another translation.

    def settings(cpp):
        start = cpp.index("    std::vector<::pineforge::checked_settings::Setting> _pf_settings_inputs()")
        end = cpp.index("    std::vector<::pineforge::checked_settings::Setting> _pf_settings_overrides()", start)
        return cpp[start:end]

    # Compare the whole settings table, with only the newly added input removed.
    extended_settings = "".join(
        line for line in settings(duplicate["cpp"]).splitlines(keepends=True)
        if '{"Length#2",' not in line
    )
    assert extended_settings == settings(before["cpp"])
    # Bypassing the new key pass reproduces the previous path for unique titles.
    monkeypatch.setattr(api, "assign_input_keys", lambda *args, **kwargs: None, raising=False)
    monkeypatch.setattr(analyzer_base, "assign_input_keys", lambda *args, **kwargs: None, raising=False)
    legacy = transpile_full(unique)
    assert legacy == before
    assert settings(legacy["cpp"]) == settings(after["cpp"])
