"""#440: runtime deadlines belong to individual Pine loops."""

import json
import re

import pytest

from gate.glue import transpile_json as glue_json
from pineforge_codegen import transpile, transpile_full, transpile_with_request_inventory
from pineforge_codegen.errors import CompileError
from tests._compile import run_emitted_tu, skip_if_no_engine_lib
from tests._e2e import reference_codegen, transpile_json


BASE = "9b17c40e074a1bcab7ec581b5e71f13b171deb42"
HEADER = '//@version=6\nstrategy("Loop watchdog", currency=currency.NONE)\n'
DRIVER = r'''
#include <chrono>
#include <iostream>
#include <unistd.h>
int main() {
    Bar bars[] = {{10, 10, 10, 10, 1, 60000}, {20, 20, 20, 20, 1, 120000}};
    GeneratedStrategy strategy;
    alarm(8);  // Bound only execution of this child, including on base.
    const auto start = std::chrono::steady_clock::now();
    strategy.run(bars, 2, "1", "1");
    const auto elapsed = std::chrono::steady_clock::now() - start;
    alarm(0);
    std::cout << strategy.last_error() << '|' << strategy.total;
    if (!strategy.last_error().empty())
        std::cout << '|' << std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count();
}
'''


def _base_cpp(source, tmp_path):
    reference = reference_codegen(BASE)
    assert reference is not None, f"NOT COMPARED: required base {BASE}, got {reference}"
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    result = transpile_json(pine, root=reference)
    assert result["ok"] and result["cpp"], f"NOT COMPARED: base emission failed: {result}"
    return result["cpp"]


ENDLESS = HEADER + """float total = 0.0
if bar_index == 1
    while true
        total += 1.0
        continue
"""


def test_default_watchdog_stops_an_endless_while():
    skip_if_no_engine_lib()
    output = run_emitted_tu(transpile(ENDLESS), DRIVER, opt="-O0", label="default loop limit")
    assert re.search(r"Error on bar 1:.*Pine line 5\b.*\b2000 ms\b", output), output
    assert int(output.rsplit("|", 1)[1]) >= 2000, output


@pytest.mark.parametrize("inner", ["while true", "for i = 0 to i + slack"])
def test_small_limit_reports_the_inner_loop_and_nonzero_bar(inner):
    skip_if_no_engine_lib()
    source = HEADER + f"""slack = input.int(1)
float total = 0.0
if bar_index == 1
    for j = 1 to 2
        {inner}
            total += 1.0
            continue
"""
    cpp = transpile(source, loop_timeout_ms=5)
    output = run_emitted_tu(cpp, DRIVER, opt="-O2", label="nested loop limit")
    assert re.search(r"Error on bar 1:.*Pine line 7\b.*\b5 ms\b", output), output
    assert 5 <= int(output.rsplit("|", 1)[1]) < 2000, output


def test_finite_loop_result_matches_the_same_native_run_on_base(tmp_path):
    skip_if_no_engine_lib()
    # Descending iteration, a continue, an inner break and the second bar
    # all affect the observed total. No TradingView capture is the oracle.
    source = HEADER + """float total = 0.0
for i = 3 to 1
    if i == 2
        continue
    for v in array.from(1, 2, 3)
        if v == 3
            break
        total += i * v + bar_index
"""
    before = run_emitted_tu(_base_cpp(source, tmp_path), DRIVER, opt="-O2", label="finite base")
    assert before == "|16", f"NOT COMPARED: expected base |16, got {before!r}"
    after = run_emitted_tu(transpile(source), DRIVER, opt="-O2", label="finite candidate")
    assert after == before


def test_no_loop_cpp_is_byte_identical_to_base(tmp_path):
    # Even a timer-looking *authored* identifier must retain its spelling.
    source = HEADER + "_pf_loop_watchdog_0 = close\nplot(_pf_loop_watchdog_0)\n"
    before = _base_cpp(source, tmp_path).encode()
    assert transpile(source).encode() == before
    assert transpile(source, loop_timeout_ms=5).encode() == before


def test_requested_helper_loop_has_a_watchdog_in_its_evaluator():
    source = HEADER + """measure(float x) =>
    float sum = 0.0
    for i = 1 to 3
        sum += x
    while sum < 0.0
        sum += x
    sum
total = request.security(syminfo.tickerid, "60", measure(close))
"""
    cpp = transpile(source)
    evaluator = cpp.split("void _eval_security_0(", 1)[1].split("\n    }", 1)[0]
    for pine_line in (5, 7):
        watchdog = re.search(rf"(_pf_loop_watchdog_\d+)\{{bar_index_, {pine_line}\}};", evaluator)
        assert watchdog and f"{watchdog[1]}.poll();" in evaluator


@pytest.mark.parametrize("api", (transpile_full, transpile_with_request_inventory))
def test_extended_api_uses_the_same_loop_limit(api):
    assert api(ENDLESS, loop_timeout_ms=17)["cpp"] == transpile(ENDLESS, loop_timeout_ms=17)


def test_glue_forwards_the_limit_and_rejects_invalid_options():
    assert json.loads(glue_json(ENDLESS, loop_timeout_ms=17))["cpp"] == (
        transpile(ENDLESS, loop_timeout_ms=17))
    for invalid in (0, -1, True, 0.5, "1", 2**31):
        with pytest.raises(CompileError):
            transpile(ENDLESS, loop_timeout_ms=invalid)
        result = json.loads(glue_json(ENDLESS, loop_timeout_ms=invalid))
        assert not result["ok"] and result["diagnostics"]


def test_repeated_short_loop_times_out_at_its_sampled_exit():
    skip_if_no_engine_lib()
    source = HEADER + """float total = 0.0
for outer = 1 to 200
    total += 1000.0
    for inner = 1 to 4
        total += 1.0
"""
    cpp = transpile(source, loop_timeout_ms=2)
    # Four iterations divide 256 exactly: each sample falls on a short exit.
    # With a fresh timer per visit the old exit check blames the outer loop.
    clock = r'''
#include <chrono>
struct CountingLoopClock {
    using time_point = std::chrono::steady_clock::time_point;
    static inline unsigned reads = 0;
    static time_point now() { return time_point{} + std::chrono::milliseconds(reads++); }
};
'''
    assert "std::chrono::steady_clock" in cpp
    cpp = clock + cpp.replace("std::chrono::steady_clock", "CountingLoopClock")
    driver = r'''
#include <iostream>
int main() {
    Bar bar{10, 10, 10, 10, 1, 60000};
    GeneratedStrategy strategy;
    strategy.run(&bar, 1, "1", "1");
    std::cout << strategy.last_error() << '|' << strategy.total << '|' << CountingLoopClock::reads;
}
'''
    output = run_emitted_tu(cpp, driver, opt="-O2", label="repeated short loop")
    message, completed, reads = output.split("|")
    assert message == "Error on bar 0: Pine line 6: loop time limit exceeded (2 ms)", output
    # The second full batch must trip at exit, before the 129th outer entry.
    assert int(completed) == 128 * 1000 + 512, output
    assert int(reads) == 4, output  # Two starts on this bar and two inner samples.
