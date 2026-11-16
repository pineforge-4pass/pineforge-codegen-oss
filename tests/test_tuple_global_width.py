"""Tuple-bound global storage accounts for writes after the declaration."""

import re

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


SOURCE = '''//@version=6
strategy("tuple global width")
narrow() => [1, 2]
[outv, other] = narrow()
'''

BARS = (
    (3_000_000_000.75, 1_706_745_600_001),
    (4_294_967_309.5, 1_706_745_660_003),
    (12.75, 1_706_745_720_007),
)


def _stored_values(rhs, opt):
    cpp = transpile(
        SOURCE + f"outv := {rhs}\n// @pf-trace outv=outv\n"
    )
    bars = ",\n".join(
        f"        {{{close!r}, {close!r}, {close!r}, {close!r}, 1.0, {stamp}LL}}"
        for close, stamp in BARS
    )
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
''' + bars + r'''
    };
    for (auto& bar : bars) {
        GeneratedStrategy strategy;
        strategy.run(&bar, 1, "1", "1");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 1;
        }
        std::cout << strategy.outv << "\n";
    }
}
'''
    output = run_emitted_tu(
        cpp, driver, opt=opt, label="tuple global later wide write",
    )
    return [int(value) for value in output.splitlines()]


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_tuple_global_keeps_later_epoch_write(opt):
    assert _stored_values("time", opt) == [stamp for _, stamp in BARS]


@pytest.mark.parametrize("opt", ["-O0", "-O2"])
def test_tuple_global_keeps_later_float_to_int_write(opt):
    assert _stored_values("int(close)", opt) == [int(close) for close, _ in BARS]


@pytest.mark.parametrize(
    "writes",
    [
        "",
        "outv := other\n",
        "if close > 0\n    int outv = 0\n    outv := time\n",
    ],
    ids=["unchanged", "narrow-write", "shadow-wide-write"],
)
def test_tuple_global_without_wide_writes_keeps_int_storage(writes):
    cpp = transpile(
        SOURCE + writes + "// @pf-trace outv=outv\n// @pf-trace other=other\n"
    )
    for name in ("outv", "other"):
        assert re.search(rf"^    int {name} = [^;\n]+;$", cpp, re.MULTILINE)
