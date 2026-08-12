"""Plain-UDF history parameters follow their written call site's chart clock."""

from __future__ import annotations

import re

from pineforge_codegen import transpile
from tests.test_runtime_var_initialization import _compile_and_run


_SOURCE = '''//@version=6
strategy("UDF series parameter chart history")
at3(float src) => src[3]
max3(float src) =>
    float maximum = src[1]
    for i = 2 to 3
        if src[i] > maximum
            maximum := src[i]
    maximum
nested(float src) => at3(src)
execute = bar_index == 0 or bar_index == 4 or bar_index == 8 or bar_index == 12
source = bar_index == 0 ? 120.0 :
     bar_index == 4 ? 80.0 :
     bar_index == 8 ? 40.0 :
     bar_index == 9 ? 30.0 :
     bar_index == 10 ? 20.0 :
     bar_index == 11 ? 10.0 : 0.0
sparseAt3 = execute ? at3(source) : na
skip = not execute
sparseMaxIsHold = skip or max3(source) == 40.0
alwaysMax3 = max3(source)
nestedAt3 = execute ? nested(source) : na
var bool firstCallMissing = false
var bool nestedFirstCallMissing = false
var float observedSparseAt3 = na
var bool observedSparseMaxIsHold = false
var float observedAlwaysMax3 = na
var float observedNestedAt3 = na
if bar_index == 0
    firstCallMissing := na(sparseAt3)
    nestedFirstCallMissing := na(nestedAt3)
if bar_index == 12
    observedSparseAt3 := sparseAt3
    observedSparseMaxIsHold := sparseMaxIsHold
    observedAlwaysMax3 := alwaysMax3
    observedNestedAt3 := nestedAt3
'''


_DRIVER = r'''
#include <iostream>
#include <vector>
int main() {
    std::vector<Bar> bars;
    for (int i = 0; i <= 12; ++i) {
        double value = 100.0 + i;
        bars.push_back(Bar{value, value, value, value, 1.0,
                           1700000000000LL + i * 60000LL});
    }
    GeneratedStrategy strategy;
    strategy.run(bars.data(), static_cast<int>(bars.size()));
    std::cout << strategy.observedSparseAt3 << " "
              << strategy.observedSparseMaxIsHold << " "
              << strategy.observedAlwaysMax3 << " "
              << strategy.observedNestedAt3 << " "
              << strategy.firstCallMissing << " "
              << strategy.nestedFirstCallMissing << "\n";
}
'''


def test_plain_udf_history_parameters_hold_last_on_skipped_chart_bars() -> None:
    """Discriminate alias, compact, hole, and chart-aligned hold-last models."""
    cpp = transpile(_SOURCE)

    # Direct, lazy-or, always-called, nested forwarding, and its outer call all
    # own independent buffers.  They advance in the on_bar preamble and an
    # execution only replaces the already-created current slot.
    members = re.findall(
        r"^\s+Series<double> (_udf_series_arg_\d+);$", cpp, re.MULTILINE
    )
    assert len(members) == 5
    assert len(set(members)) == 5
    for member in members:
        assert (
            f"if (history_advances_new_bar()) "
            f"{member}.push({member}.current());" in cpp
        )
        assert f"{member}.update(_sv);" in cpp
        assert f"if (history_advances_new_bar()) {member}.push(_sv);" not in cpp

    # LLLL discriminator semantics: both sparse sites hold 40, the independent
    # always-called site sees caller-chart history max(10, 20, 30), nested
    # forwarding has its own matching clock, and first-call prehistory is na.
    assert _compile_and_run(cpp + _DRIVER) == "40 1 30 40 1 1\n"


def test_plain_udf_history_buffers_are_rollback_owned() -> None:
    cpp = transpile(_SOURCE)
    members = re.findall(
        r"^\s+Series<double> (_udf_series_arg_\d+);$", cpp, re.MULTILINE
    )
    for member in members:
        assert re.search(rf"^\s+{re.escape(member)},$", cpp, re.MULTILINE)
        assert re.search(
            rf"this->{re.escape(member)} = "
            rf"_pf_script_state_checkpoint_->_pf_value_\d+;",
            cpp,
        )


def test_requested_context_udfs_keep_their_separate_legacy_clock() -> None:
    """Chart callsites get the factor; requested-context clones do not."""
    source = '''//@version=6
strategy("requested UDF history scope")
history(float src) => src[1]
outer(float src) => history(src)
chart = outer(close)
requested = request.security(syminfo.tickerid, "60", outer(close))
requestedLower = request.security_lower_tf(
    syminfo.tickerid, "1", outer(close)
)
'''
    cpp = transpile(source)
    members = re.findall(
        r"^\s+Series<double> (_udf_series_arg_\d+);$", cpp, re.MULTILINE
    )

    # Only the chart's outer() call and its nested history() call own this
    # chart-clocked state.  The request.security and security_lower_tf variants
    # are inlined by security.py and retain their established direct aliases.
    assert len(members) == 2
    assert "double outer_cs0(const Series<double>& src)" in cpp
    assert "_udf_series_arg_1.update(_sv)" in cpp
    assert "chart = outer_cs0(([&]() -> const Series<double>&" in cpp
    for index in (1, 2):
        body = cpp.split(
            f"double outer_cs{index}(const Series<double>& src)", 1
        )[1].split("\n    }", 1)[0]
        assert "_udf_series_arg_" not in body
        assert re.search(r"return history(?:__ni\d+|_cs\d+)\(src\);", body)

    security_evaluators = cpp.split("void _eval_security_0", 1)[1].split(
        "void evaluate_security", 1
    )[0]
    assert "_udf_series_arg_" not in security_evaluators


def test_requested_context_nested_in_chart_wrapper_stays_out_of_chart_clock() -> None:
    source = '''//@version=6
strategy("wrapped requested UDF history scope")
history(float src) => src[1]
outer(float src) => history(src)
securityWrapper() => request.security(
    syminfo.tickerid, "60", outer(close)
)
lowerWrapper() => request.security_lower_tf(
    syminfo.tickerid, "1", outer(close)
)
requested = securityWrapper()
requestedLower = lowerWrapper()
'''
    cpp = transpile(source)

    # The wrappers execute on the chart, but their expression arguments do
    # not: security.py inlines outer()/history() on each requested clock.
    assert "_udf_series_arg_" not in cpp
    outer_bodies = re.findall(
        r"double outer(?:_cs\d+|__ni\d+)?\(const Series<double>& src\) "
        r"\{(.*?)\n    \}",
        cpp,
        re.DOTALL,
    )
    assert outer_bodies
    for body in outer_bodies:
        assert "_udf_series_arg_" not in body
        assert re.search(r"return history(?:__ni\d+|_cs\d+)\(src\);", body)
