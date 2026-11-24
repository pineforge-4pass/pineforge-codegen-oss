"""Check proved string defaults against the linked strategy and its receipt."""

import json

import pytest

from pineforge_codegen import transpile_full
from tests._compile import run_emitted_tu


@pytest.mark.parametrize("declarations,call", [
    ('a = "Alpha"\nb = a', 'input(b, "Mode")'),
    ('a = "Alpha"\nb = a', 'input(defval=b, title="Mode")'),
    ('a = "Al"\nb = a + "pha"', 'input(b, "Mode")'),
    ('a = "Alpha"\nconst string b = a', 'input(b, "Mode")'),
], ids=["positional", "named", "concatenation", "explicit-alias"])
def test_inferred_string_default_reaches_manifest_receipt_and_getter(declarations, call):
    full = transpile_full(
        f'//@version=6\nstrategy("String default")\n{declarations}\n'
        f'mode = {call}\nplot(str.length(mode))\n'
    )
    output = run_emitted_tu(full["cpp"], r'''
#include <cassert>
#include <iostream>
#include <vector>

int main() {
    for (const char* selection : {"", "Beta"}) {
        void* strategy = nullptr;
        char error[256]{};
        assert(strategy_create_checked(nullptr, &strategy, error, sizeof(error)) == PF_SETTINGS_OK);
        if (*selection) {
            assert(strategy_set_input_checked(strategy, "Mode", selection, error, sizeof(error))
                   == PF_SETTINGS_OK);
        }
        size_t required = 0;
        assert(strategy_get_effective_settings(strategy, nullptr, 0, &required, error, sizeof(error))
               == PF_SETTINGS_BUFFER_TOO_SMALL);
        std::vector<char> receipt(required);
        assert(strategy_get_effective_settings(strategy, receipt.data(), receipt.size(), &required,
                                               error, sizeof(error)) == PF_SETTINGS_OK);
        std::cout << receipt.data() << '\n';
        Bar bar{100, 101, 99, 100, 1000, 1577836800000LL};
        auto* generated = static_cast<GeneratedStrategy*>(strategy);
        generated->run(&bar, 1);
        assert(generated->last_error().empty());
        std::cout << generated->mode << '\n';
        strategy_free(strategy);
    }
}
''', opt="-O0", label="inferred string input")
    assert full["inputs"] == [
        {"title": "Mode", "type": "string", "default": "Alpha", "supported": True},
    ]
    receipt_default, read_default, receipt_override, read_override = output.splitlines()
    # The aliases or the concatenation produce "Alpha"; input returns that
    # string until the setter substitutes "Beta".
    assert (read_default, read_override) == ("Alpha", "Beta")
    for receipt, expected in ((receipt_default, "Alpha"), (receipt_override, "Beta")):
        setting, = json.loads(receipt)["inputs"]
        assert setting["default"] == "Alpha"
        assert setting["effective_value"] == expected
        assert setting["type"] == "string"


def test_bare_string_default_matches_the_same_native_comparison():
    full = transpile_full('''//@version=6
strategy("String default comparison")
const string seed = 1.0 == 1.00000000005 ? "A" : "B"
mode = input(seed, "Mode")
runtime_mode = 1.0 == 1.00000000005 ? "A" : "B"
plot(str.length(mode) + str.length(runtime_mode))
''')
    output = run_emitted_tu(full["cpp"], r'''
#include <cassert>
#include <iostream>

int main() {
    GeneratedStrategy strategy;
    Bar bar{100, 101, 99, 100, 1000, 1577836800000LL};
    strategy.run(&bar, 1);
    assert(strategy.last_error().empty());
    std::cout << strategy.mode << '\n' << strategy.runtime_mode << '\n';
}
''', opt="-O0", label="string default comparison")
    # Both comparisons are within the absolute equality band.
    assert output.splitlines() == ["A", "A"]
    assert full["inputs"][0]["default"] == "A"
