"""Numeric input options bind to the checked-settings descriptor."""

import json

import pytest

from pineforge_codegen import transpile_full
from tests._compile import run_emitted_tu


SETTINGS_DRIVER = r'''
#include <cassert>
#include <iostream>
#include <vector>
int main() {
    void* strategy = nullptr;
    char error[256]{};
    assert(strategy_create_checked(nullptr, &strategy, error, sizeof(error)) == PF_SETTINGS_OK);
    std::cout << "[";
    for (const char* value : {"3", "2"}) {
        const bool rejected = std::string(value) == "3";
        const int expected = rejected ? PF_SETTINGS_INVALID_ARGUMENT : PF_SETTINGS_OK;
        assert(strategy_set_input_checked(strategy, "Value", value, error, sizeof(error)) == expected);
        if (rejected) assert(error[0]);
        assert(strategy_set_input_checked(strategy, "Text", value, error, sizeof(error)) == expected);
        if (rejected) assert(error[0]);
        size_t required = 0;
        assert(strategy_get_effective_settings(strategy, nullptr, 0, &required,
                                               error, sizeof(error)) == PF_SETTINGS_BUFFER_TOO_SMALL);
        std::vector<char> receipt(required);
        assert(strategy_get_effective_settings(strategy, receipt.data(), receipt.size(), &required,
                                               error, sizeof(error)) == PF_SETTINGS_OK);
        if (!rejected) std::cout << ",";
        std::cout << receipt.data();
    }
    std::cout << "]";
    strategy_free(strategy);
}
'''


@pytest.mark.parametrize("kind,default,options", [
    ("float", 1.0, [1.0, 2.0]),
    ("int", 1, [1, 2]),
])
@pytest.mark.parametrize("call_style", ["positional", "keyword"])
def test_numeric_options_descriptor_and_checked_overrides(kind, default, options, call_style):
    argument = f"options={options}" if call_style == "keyword" else str(options)
    source = (
        '//@version=6\nstrategy("numeric options")\n'
        f'value = input.{kind}({default}, "Value", {argument})\n'
        'text = input.string("1", "Text", options=["1", "2"])\n'
        'plot(value)\n'
    )
    full = transpile_full(source)
    receipts = json.loads(run_emitted_tu(
        full["cpp"], SETTINGS_DRIVER, opt="-O0",
        label=f"numeric {kind} {call_style} options",
    ))
    assert full["inputs"][0] == {
        "title": "Value", "type": kind, "default": default,
        "options": options, "supported": True,
    }
    assert len(receipts) == 2
    number = float if kind == "float" else int
    for receipt, effective in zip(receipts, (default, options[1]), strict=True):
        inputs = {entry["name"]: entry for entry in receipt["inputs"]}
        numeric = inputs["Value"]
        assert numeric["supported"] is True
        assert numeric["type"] == numeric["kind"] == kind
        assert number(numeric["default"]) == default
        assert [number(option) for option in numeric["options"]] == options
        assert numeric["min"] is numeric["max"] is numeric["step"] is None
        assert number(numeric["effective_value"]) == effective
        assert inputs["Text"]["options"] == ["1", "2"]
        assert inputs["Text"]["effective_value"] == str(int(effective))
