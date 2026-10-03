"""The opt-in settings ABI validates before mutating real generated strategies."""
import json

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


SOURCE = '''//@version=6
strategy("checked settings", initial_capital=10000, default_qty_value=1)
enum Side
    neutral
    long
    short
const int FLOOR = 1
length = input.int(3, "Length", minval=FLOOR, maxval=50, step=2)
threshold = input.float(2.5, "Threshold", minval=0, maxval=10, step=0.25)
enabled = input.bool(true, "Enabled")
mode = input.string("fast", "Mode", options=["fast", "slow"])
side = input.enum(Side.long, "Side", options=[Side.long, Side.short])
choice = input.int(3, "Choice", options=[3, 5, 7])
stamp = input.time(1577836800000, "Stamp")
shade = input.color(#ff0000, "Shade")
if enabled and bar_index % length == 0
    strategy.entry("entry", strategy.long, qty=threshold)
'''


def test_metadata_and_exception_wrappers_are_emitted():
    cpp = transpile(SOURCE)
    for symbol in ("strategy_settings_api_version", "strategy_create_checked",
                   "strategy_set_input_checked", "strategy_set_override_checked",
                   "strategy_get_effective_settings", "run_backtest_full_checked"):
        assert symbol in cpp
    assert '#if __has_include(<pineforge/checked_settings.hpp>)' in cpp
    assert '{"Length", "int"' in cpp
    assert '{"Mode", "string"' in cpp and '{std::string("fast"), std::string("slow")}' in cpp
    assert '{"Side.long", "Side.short"}' in cpp
    assert cpp.count('catch (...)') >= 7
    for entrypoint in ("run_backtest", "run_backtest_full", "strategy_set_input",
                       "strategy_set_override", "strategy_set_magnifier_volume_weighted"):
        assert f'_pf_record_failure("{entrypoint}", _pf_error.what())' in cpp
        assert f'_pf_record_failure("{entrypoint}", "unknown C++ exception")' in cpp
    assert 'unknown input key' in cpp and 'unknown override key' in cpp
    assert 'return new GeneratedStrategy();' in cpp


@pytest.mark.parametrize("optimization", ["-O0", "-O2"])
def test_checked_values_and_receipt_at_runtime(optimization):
    driver = r'''
#include <cassert>
#include <iostream>
#include <vector>
int main() {
    void* strategy = nullptr;
    char error[256]{};
    assert(strategy_create_checked(nullptr, &strategy, error, sizeof(error)) == PF_SETTINGS_OK);
    for (const auto& invalid : std::vector<std::pair<const char*, const char*>>{
            {"Lenght", "3"}, {"Length", "3x"}, {"Length", "0"},
            {"Enabled", "yes"}, {"Threshold", "NaN"}, {"Threshold", "Inf"},
            {"Mode", "typo"}, {"Side", "Side.neutral"}, {"Side", "3"},
            {"Choice", "4"}, {"Length", "5.5"}, {"Length", "5.0000000000000001"},
            {"Length", "2147483648.0"}, {"Length", "NaN"}, {"Length", "Inf"}}) {
        assert(strategy_set_input_checked(strategy, invalid.first, invalid.second,
                                          error, sizeof(error)) == PF_SETTINGS_INVALID_ARGUMENT);
        assert(error[0]);
    }
    assert(strategy_set_input_checked(strategy, "Length", "5.0", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_input_checked(strategy, "Length", "5e0", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_input_checked(strategy, "Side", "short", error, sizeof(error)) == PF_SETTINGS_INVALID_ARGUMENT);
    assert(std::string(error) == "invalid enum option");
    assert(strategy_set_input_checked(strategy, "Enabled", "0", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_input_checked(strategy, "Side", "Side.short", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_input_checked(strategy, "Stamp", "1700000000000", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_override_checked(strategy, "default_qty_type", "typo", error, sizeof(error)) == PF_SETTINGS_INVALID_ARGUMENT);
    assert(strategy_set_override_checked(strategy, "slippage", "2x", error, sizeof(error)) == PF_SETTINGS_INVALID_ARGUMENT);
    assert(strategy_set_override_checked(strategy, "process_orders_on_close", "yes", error, sizeof(error)) == PF_SETTINGS_INVALID_ARGUMENT);
    assert(strategy_set_override_checked(strategy, "default_qty_type", "strategy.cash", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_override_checked(strategy, "default_qty_value", "200", error, sizeof(error)) == PF_SETTINGS_OK);
    size_t required = 0;
    assert(strategy_get_effective_settings(strategy, nullptr, 0, &required, error, sizeof(error)) == PF_SETTINGS_BUFFER_TOO_SMALL);
    std::vector<char> receipt(required);
    assert(strategy_get_effective_settings(strategy, receipt.data(), receipt.size(), &required, error, sizeof(error)) == PF_SETTINGS_OK);
    std::cout << receipt.data();
    strategy_free(strategy);
}
'''
    receipt = json.loads(run_emitted_tu(transpile(SOURCE), driver, opt=optimization,
                                       label="checked settings"))
    inputs = {entry["name"]: entry for entry in receipt["inputs"]}
    assert inputs["Length"]["min"] == 1
    assert inputs["Length"]["max"] == 50
    assert inputs["Length"]["step"] == 2
    assert inputs["Length"]["effective_value"] == "5"
    assert inputs["Length"]["kind"] == "int"
    assert inputs["Enabled"]["effective_value"] == "false"
    assert inputs["Side"]["options"] == ["Side.long", "Side.short"]
    assert inputs["Side"]["effective_value"] == "2"
    assert inputs["Choice"]["options"] == ["3", "5", "7"]
    assert inputs["Stamp"]["effective_value"] == "1700000000000"
    assert inputs["Shade"]["default"] == "4294901760"
    assert inputs["Shade"]["effective_value"] == "4294901760"
    assert inputs["Shade"]["type"] == "int" and inputs["Shade"]["kind"] == "string"
    overridden = {entry["name"]: entry for entry in receipt["overrides"]}["default_qty_value"]
    assert overridden["default"] == "1" and overridden["effective_value"] == "200"
    assert {entry["name"]: entry for entry in receipt["overrides"]}["default_qty_type"]["effective_value"] == "cash"


def test_duplicate_input_key_is_only_refused_by_checked_setter():
    source = '''//@version=6
strategy("duplicate settings")
first = input.int(3, "Shared")
second = input.int(7, "Shared")
'''
    driver = r'''
#include <cassert>
int main() {
    void* strategy = strategy_create(nullptr);
    char error[128]{};
    assert(strategy_set_input_checked(strategy, "Shared", "9", error, sizeof(error)) == PF_SETTINGS_UNSUPPORTED);
    strategy_set_input(strategy, "Shared", "9");
    strategy_free(strategy);
}
'''
    run_emitted_tu(transpile(source), driver, opt="-O0", label="ambiguous checked input")


def test_enum_options_supply_type_for_nonliteral_default():
    source = SOURCE.replace('side = input.enum(Side.long,',
                            'defaultSide = Side.long\nside = input.enum(defaultSide,')
    driver = r'''
#include <cassert>
#include <iostream>
#include <vector>
int main() {
    void* strategy = strategy_create(nullptr);
    char error[256]{};
    assert(strategy_set_input_checked(strategy, "Side", "Side.short", error, sizeof(error)) == PF_SETTINGS_OK);
    size_t required = 0;
    assert(strategy_get_effective_settings(strategy, nullptr, 0, &required, error, sizeof(error)) == PF_SETTINGS_BUFFER_TOO_SMALL);
    std::vector<char> receipt(required);
    assert(strategy_get_effective_settings(strategy, receipt.data(), receipt.size(), &required, error, sizeof(error)) == PF_SETTINGS_OK);
    std::cout << receipt.data();
    strategy_free(strategy);
}
'''
    receipt = json.loads(run_emitted_tu(transpile(source), driver, opt="-O0", label="enum option inference"))
    setting = {entry["name"]: entry for entry in receipt["inputs"]}["Side"]
    assert setting["supported"] is True
    assert setting["options"] == ["Side.long", "Side.short"]
    assert setting["option_values"] == ["1", "2"]
    assert setting["effective_value"] == "2"


def test_legacy_setter_failure_is_readable_and_prevents_execution():
    driver = r'''
#include <cassert>
int main() {
    auto* strategy = static_cast<GeneratedStrategy*>(strategy_create(nullptr));
    char error[256]{};
    strategy_set_override(strategy, "pyramiding", "abc");
    const auto failure = strategy->last_error();
    assert(failure.find("strategy_set_override:") == 0 && failure.size() > 23);
    ReportC report{};
    report.total_trades = 99;
    run_backtest_full(strategy, nullptr, 0, "", "", 0, 4, 0, &report);
    assert(report.total_trades == 0 && strategy->last_error() == failure);
    assert(run_backtest_full_checked(strategy, nullptr, 0, "", "", 0, 4, 0,
                                     &report, error, sizeof(error)) == PF_SETTINGS_RUN_FAILED);
    assert(std::string(error) == failure);
    strategy_free(strategy);
}
'''
    run_emitted_tu(transpile(SOURCE), driver, opt="-O0", label="legacy exception readback")
