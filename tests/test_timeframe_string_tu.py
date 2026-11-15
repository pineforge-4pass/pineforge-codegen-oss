"""Build and execute complete string-argument TUs at distinct optimizations."""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


@pytest.mark.parametrize("optimization", ["-O0", "-O2"])
def test_string_timeframe_controls_build_and_run(optimization):
    source = (
        '//@version=6\nstrategy("String timeframe compilation")\n'
        'frameChoice = input.timeframe("")\n'
        'string missingFrame = na\n'
        'unrelatedString = str.tostring(close > open)\n'
        'chosenFrame() => frameChoice\n'
        'withSeconds(frameArg) => timeframe.in_seconds(frameArg)\n'
        'withChange(frameArg) => timeframe.change(timeframe = frameArg)\n'
        'typedSeconds(string frameArg) => timeframe.in_seconds(timeframe = frameArg)\n'
        'typedChange(string frameArg) => timeframe.change(frameArg)\n'
    )
    arguments = {
        "explicit": '"240"',
        "empty": '""',
        "variable": "frameChoice",
        "return": "chosenFrame()",
        "formatted": "str.tostring(60)",
        "hostString": "strategy.position_entry_name",
        "typedMissing": "missingFrame",
    }
    names = []
    for function in ("in_seconds", "change"):
        for kind, argument in arguments.items():
            for named in (False, True):
                name = f"{function}_{kind}_{'named' if named else 'positional'}"
                names.append(name)
                supplied = f"timeframe = {argument}" if named else argument
                source += f"{name} = timeframe.{function}({supplied})\n"
    for helper in ("withSeconds", "withChange", "typedSeconds", "typedChange"):
        for kind, argument in (("empty", '""'), ("explicit", '"240"')):
            name = f"{helper}_{kind}"
            names.append(name)
            source += f"{name} = {helper}({argument})\n"
    driver = r'''
#include <iostream>
#include <vector>
int main() {
    std::vector<pineforge::Bar> bars(5);
    for (std::size_t i = 0; i < bars.size(); ++i) {
        bars[i].open = bars[i].high = bars[i].low = bars[i].close = 100.0 + i;
        bars[i].volume = 1.0;
        bars[i].timestamp = 1704067200000LL + static_cast<long long>(i) * 3600000LL;
    }
    GeneratedStrategy strategy;
    strategy.run(bars.data(), bars.size(), "60", "60");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << "\n";
        return 1;
    }
'''
    for name in names:
        driver += (
            f'    std::cout << "{name} " << '
            f'static_cast<double>(strategy.{name}) << "\\n";\n'
        )
    driver += "}\n"
    output = run_emitted_tu(
        transpile(source), driver, opt=optimization, label="string timeframe controls",
    )
    # Value parity has its own replay oracles. This test's target is building
    # every control and executing it through the real generated strategy.
    rows = [line.split() for line in output.splitlines()]
    assert [row[0] for row in rows] == names
    assert all(len(row) == 2 for row in rows)
    for _, value in rows:
        float(value)
    print(f"string timeframe controls {optimization}:\n{output}", end="")
