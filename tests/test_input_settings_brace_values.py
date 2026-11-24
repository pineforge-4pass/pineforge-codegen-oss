"""Check dropdown settings and string defaults with the linked engine."""

import json

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


SOURCE = '''//@version=6
strategy("Settings values")
const int upper = 10
const float lower = 0.5
const string seed = "Alpha"
count = input.int(5, "Count", [upper, 5])
ratio = input.float(1.5, "Ratio", [2, 1.5, lower])
mode = input(defval=seed, title="Mode")
score = count + ratio + str.length(mode)
if score > 0
    strategy.entry("L", strategy.long, qty=score)
'''

DRIVER = r'''
#include <cassert>
#include <iostream>
#include <vector>

void read_settings_and_run(bool override_inputs, double expected) {
    void* strategy = nullptr;
    char error[256]{};
    assert(strategy_create_checked(nullptr, &strategy, error, sizeof(error)) == PF_SETTINGS_OK);
    if (override_inputs) {
        assert(strategy_set_input_checked(strategy, "Count", "6", error, sizeof(error))
               == PF_SETTINGS_INVALID_ARGUMENT);
        assert(strategy_set_input_checked(strategy, "Count", "10", error, sizeof(error))
               == PF_SETTINGS_OK);
        assert(strategy_set_input_checked(strategy, "Ratio", "2.0", error, sizeof(error))
               == PF_SETTINGS_OK);
        assert(strategy_set_input_checked(strategy, "Mode", "Beta", error, sizeof(error))
               == PF_SETTINGS_OK);
    }
    size_t required = 0;
    assert(strategy_get_effective_settings(strategy, nullptr, 0, &required, error, sizeof(error))
           == PF_SETTINGS_BUFFER_TOO_SMALL);
    std::vector<char> receipt(required);
    assert(strategy_get_effective_settings(strategy, receipt.data(), receipt.size(), &required,
                                           error, sizeof(error)) == PF_SETTINGS_OK);
    std::cout << receipt.data() << '\n';

    Bar bars[]{{100, 101, 99, 100, 1000, 1577836800000LL}};
    ReportC report{};
    assert(run_backtest_full_checked(strategy, bars, 1, "1", "1", 0, 4, 0,
                                     &report, error, sizeof(error)) == PF_SETTINGS_OK);
    const auto* generated = static_cast<GeneratedStrategy*>(strategy);
    assert(generated->mode == (override_inputs ? "Beta" : "Alpha"));
    assert(generated->score == expected);
    report_free(&report);
    strategy_free(strategy);
}

int main() {
    // Pine returns the declared defvals if no override was set:
    // 5 + 1.5 + str.length("Alpha") = 5 + 1.5 + 5 = 11.5.
    read_settings_and_run(false, 11.5);
    // Selecting the supplied choices and replacing the string reads:
    // 10 + 2 + str.length("Beta") = 10 + 2 + 4 = 16.
    read_settings_and_run(true, 16.0);
}
'''


def test_checked_input_choices_and_string_defaults_reach_the_strategy():
    output = run_emitted_tu(transpile(SOURCE), DRIVER, opt="-O0", label="checked input values")
    base, override = [
        {row["name"]: row for row in json.loads(line)["inputs"]}
        for line in output.splitlines()
    ]
    for name, kind, default, options, selected in (
        ("Count", "int", "5", ["10", "5"], "10"),
        ("Ratio", "float", "1.5", ["2", "1.5", "0.5"], "2"),
        ("Mode", "string", "Alpha", [], "Beta"),
    ):
        for rows, effective in ((base, default), (override, selected)):
            setting = rows[name]
            assert setting["supported"] is True
            assert setting["type"] == kind
            assert setting["default"] == default
            assert setting["options"] == options
            assert setting["effective_value"] == effective
            assert all(setting[field] is None for field in ("min", "max", "step"))
