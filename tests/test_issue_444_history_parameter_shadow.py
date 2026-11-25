"""A history parameter reads its argument at that call site, even if it hides a global."""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu, skip_if_no_engine_lib


_SPREAD = "float spread = close - open"
_SHADOW = f"{_SPREAD}\ng(spread) => spread[1]"
_PREVIOUS_RANGE = ("na", "4", "7", "11")
_PREVIOUS_BODY = ("na", "2", "-3", "5")

_DRIVER = r"""
#include <iostream>

int main() {
    const Bar bars[] = {
        {10.0, 13.0,  9.0, 12.0, 1.0, 1743465600000LL},
        {20.0, 23.0, 16.0, 17.0, 1.0, 1743465660000LL},
        {30.0, 40.0, 29.0, 35.0, 1.0, 1743465720000LL},
        {40.0, 47.0, 39.0, 44.0, 1.0, 1743465780000LL},
    };
    auto print = [](auto value) {
        if (is_na(value)) std::cout << " na";
        else std::cout << ' ' << value;
    };
    for (int count = 1; count <= 4; ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars, count, "1", "1");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << '\n';
            return 1;
        }
        // PRINT_OBSERVATIONS
        std::cout << '\n';
    }
}
"""


@pytest.mark.parametrize("definitions, observations", [
    pytest.param(_SHADOW, {"g(high - low)": _PREVIOUS_RANGE}, id="shadow"),
    pytest.param(
        f"{_SPREAD}\ng(src) => src[1]",
        {"g(high - low)": _PREVIOUS_RANGE}, id="renamed-parameter",
    ),
    pytest.param(
        _SHADOW,
        {"g(high - low) + 100 * spread[1]": ("na", "204", "-293", "511"),
         "spread[1]": _PREVIOUS_BODY}, id="global-has-own-history",
    ),
    pytest.param(
        _SHADOW,
        {"g(high - low) + 100 * g(open)": ("na", "1004", "2007", "3011")},
        id="two-call-sites",
    ),
    pytest.param(
        f"{_SPREAD}\ng(src) => src[1]\n"
        "method previous(float spread) => g(spread)\n"
        "method direct(float spread) => spread[1]",
        {"(high - low).previous()": _PREVIOUS_RANGE,
         "(close - open).previous()": _PREVIOUS_BODY,
         "(high - low).direct()": _PREVIOUS_RANGE,
         "(close - open).direct()": _PREVIOUS_BODY},
        id="forwarded-method-parameter",
    ),
    pytest.param(
        "int n = 3\ng(n) => n[1]",
        {"g(3 * bar_index + 5)": ("na", "5", "8", "11")}, id="integer-shadow",
    ),
])
def test_history_parameter_and_global_have_separate_storage(definitions, observations):
    skip_if_no_engine_lib()
    pine = ['//@version=6', 'strategy("parameter named like a global")', definitions]
    reads = []
    for i, expression in enumerate(observations):
        # Native plots are dropped; read the actual generated member instead.
        name = f"observed{i}"
        pine.extend([f"{name} = {expression}", f"plot({name})"])
        reads.append(f"print(strategy.{name});")
    driver = _DRIVER.replace("// PRINT_OBSERVATIONS", "\n        ".join(reads))
    output = run_emitted_tu(transpile("\n".join(pine) + "\n"), driver,
                            opt="-O0", label="issue-444")
    assert [tuple(line.split()) for line in output.splitlines()] == list(
        zip(*observations.values(), strict=True)
    )
