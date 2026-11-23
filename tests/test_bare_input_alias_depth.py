"""A flat chain of immutable string declarations keeps its input overload."""

import json

import pytest

from pineforge_codegen import transpile_full
from tests._compile import run_emitted_tu


DRIVER = r'''
#include <cassert>
#include <iostream>
#include <vector>

int main() {
    void* strategy = nullptr;
    char error[256]{};
    assert(strategy_create_checked(nullptr, &strategy, error, sizeof(error)) == PF_SETTINGS_OK);
    size_t required = 0;
    assert(strategy_get_effective_settings(strategy, nullptr, 0, &required, error, sizeof(error))
           == PF_SETTINGS_BUFFER_TOO_SMALL);
    std::vector<char> receipt(required);
    assert(strategy_get_effective_settings(strategy, receipt.data(), receipt.size(), &required,
                                           error, sizeof(error)) == PF_SETTINGS_OK);
    Bar bar{100, 101, 99, 100, 1000, 1577836800000LL};
    auto* generated = static_cast<GeneratedStrategy*>(strategy);
    generated->run(&bar, 1);
    assert(generated->last_error().empty());
    std::cout << receipt.data() << '\n' << generated->mode << '\n';
    strategy_free(strategy);
}
'''


def chain(length):
    declarations = ['const string s0 = "Alpha"']
    declarations.extend(f"const string s{i} = s{i - 1}" for i in range(1, length))
    return (
        '//@version=6\nstrategy("String alias depth")\n'
        + "\n".join(declarations)
        + f'\nmode = input(s{length - 1}, "Mode")\nplot(str.length(mode))\n'
    )


@pytest.mark.parametrize("length", [50, 256, 257, 258, 512])
def test_flat_const_string_aliases_build_and_keep_the_default(length):
    # Every declaration copies the same string; no operation changes it.
    # This row can be copied to older trees without internal proof imports.
    full = transpile_full(chain(length))
    receipt, effective = run_emitted_tu(
        full["cpp"], DRIVER, opt="-O0", label=f"string alias chain of {length}"
    ).splitlines()
    assert full["inputs"] == [
        {"title": "Mode", "type": "string", "default": "Alpha", "supported": True},
    ]
    setting, = json.loads(receipt)["inputs"]
    assert setting["type"] == "string"
    assert setting["default"] == setting["effective_value"] == effective == "Alpha"


def test_flat_alias_chain_past_the_proof_budget_keeps_the_unproven_default():
    # Control: the 513th declaration exceeds the 512-step proof budget.
    # Pins today's declined default: the historical numeric getter, which
    # does not build for a string. A change that selects the string overload
    # for this shape must update this row.
    full = transpile_full(chain(513))
    assert full["inputs"][0]["default"] is None
    with pytest.raises(AssertionError, match=r"error:[^\n]*number"):
        run_emitted_tu(full["cpp"], DRIVER, opt="-O0", label="unproven string alias chain")
