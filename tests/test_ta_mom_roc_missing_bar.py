"""Pine ``ta.mom`` / ``ta.roc`` read ``source[length]`` by bar position.

TradingView answers ``ta.mom(x, n)`` with ``x - x[n]`` and ``ta.roc(x, n)``
with ``100 * (x - x[n]) / x[n]``. The subscript counts bars, so a bar whose
source is na keeps its place and the call is na when either end is na. The
engine's ``ta::Mom`` / ``ta::ROC`` count present samples instead, so the Pine
call is built from the engine's Pine-layer objects ``PineMom`` / ``PineRoc``
(``codegen/ta.py::_TA_POSITIONAL_LAG_CLASSES``).

Invented closes, length 2, the source hidden on bar 2:

    bar      0    1    2    3     4    5    6
    close    2    4    6    8     16   10   22
    source   2    4    na   8     16   10   22
    ta.mom   na   na   na   4     na   2    6
    ta.roc   na   na   na   100   na   25   37.5

Bar 3 reads bar 1 and bar 4 reads bar 2, which is missing; from bar 5 on the
missing bar is more than two bars back. Built from ``ta::Mom`` / ``ta::ROC``
the same script answers 6 (8 - 2) and 12 (16 - 4), 300 and 300 on bars 3
and 4.

A call that runs only on some bars and reads the generator's held source
history is another lowering with its own tests
(``tests/test_lazy_source_clock.py``).
"""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.helpers import cpp_code_only
from tests._compile import compile_cpp, run_emitted_tu


_HEADER = "#include <pineforge/source/pine_ta_length.hpp>"
_TYPES = {
    "mom": "pineforge::source::PineMom",
    "roc": "pineforge::source::PineRoc",
}
_KERNEL = {"mom": "ta::Mom", "roc": "ta::ROC"}


_GAP_SCRIPT = (
    '//@version=6\nstrategy("momentum and rate of change around a missing bar")\n'
    "gappedSource = bar_index == 2 ? float(na) : close\n"
    "momValue = ta.mom(gappedSource, 2)\n"
    "rocValue = ta.roc(gappedSource, 2)\n"
    "plot(momValue + rocValue)\n"
)

# The host runs the script once per bar; each row is read after that bar and
# printed as "bar source mom roc", na spelled out so that it is never a zero.
_GAP_DRIVER = r"""
#include <cstddef>
#include <cstdio>
#include <pineforge/run_failure.hpp>
#include <stdexcept>
#include <string>
#include <vector>
struct RecordingStrategy : GeneratedStrategy {
    std::vector<std::vector<double>> rows;
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        rows.push_back({gappedSource, momValue, rocValue});
    }
};
int main() {
    const double closes[] = {2, 4, 6, 8, 16, 10, 22};
    constexpr int count = 7;
    Bar bars[count];
    for (int i = 0; i < count; ++i) {
        const double price = closes[i];
        // Finite, well-formed one-minute bars: open, high, low, close, volume, time.
        bars[i] = {price, price + 1, price - 1, price, 1.0, 1743465600000LL + i * 60000LL};
    }
    RecordingStrategy strategy;
    strategy.run(bars, count, "1", "1");
    const auto failure = pineforge::run_failure_value_of(strategy);
    const auto state = strategy.native_state();
    if (failure.code != pineforge::RunFailureCode::none ||
        strategy.last_run_status() != 0 || !strategy.last_error().empty() ||
        state.kind != pineforge::NativeLifecycleKind::Completed)
        throw std::runtime_error(
            std::string("hosted run failed: ") +
            pineforge::run_failure_code_of(strategy) + ": " + strategy.last_error());
    if (strategy.rows.size() != static_cast<std::size_t>(count))
        throw std::runtime_error("the script did not run once per bar");
    for (int i = 0; i < count; ++i) {
        std::printf("%d", i);
        for (double value : strategy.rows[i]) {
            if (is_na(value)) std::printf(" na");
            else std::printf(" %.17g", value);
        }
        std::printf("\n");
    }
}
"""

# bar, source, ta.mom, ta.roc. The engine classes answer "3 8 6 300" and
# "4 16 12 300" on the two bars after the missing one.
_GAP_ROWS = [
    "0 2 na na",
    "1 4 na na",
    "2 na na na",
    "3 8 4 100",
    "4 16 na na",
    "5 10 2 25",
    "6 22 6 37.5",
]


def test_mom_and_roc_read_the_bar_length_bars_back_across_a_missing_bar():
    out = run_emitted_tu(
        transpile(_GAP_SCRIPT), _GAP_DRIVER, opt="-O0", label="mom_roc_missing_bar"
    )
    assert out.splitlines() == _GAP_ROWS


# One call of each kind the object is built for: a literal length, keyword
# arguments, an input length (rebuilt per run, precalculated), a simple
# length (built on the first call) and a request.security copy.
_CONSUMERS = (
    '//@version=6\nstrategy("momentum and rate of change consumers")\n'
    'lengthInput = input.int(5, "Length")\n'
    'simple int simpleLength = syminfo.type == "crypto" ? 3 : 4\n'
    "gappedSource = bar_index % 11 == 4 ? float(na) : close\n"
    "fixedValue = ta.FAMILY(gappedSource, 2)\n"
    "namedValue = ta.FAMILY(source = gappedSource, length = 3)\n"
    "inputValue = ta.FAMILY(close, lengthInput)\n"
    "simpleValue = ta.FAMILY(close, simpleLength)\n"
    'requestedValue = request.security(syminfo.tickerid, "60", ta.FAMILY(close, 2))\n'
    "plot(fixedValue + namedValue + inputValue + simpleValue + requestedValue)\n"
)


def _consumers(family):
    return transpile(_CONSUMERS.replace("FAMILY", family))


def _members(cpp, family):
    return re.findall(
        rf"^\s*(\S.*?)\s+((?:_sec\d+_)?_ta_{family}_\w+);$", cpp, re.M
    )


@pytest.mark.parametrize("family", sorted(_TYPES))
def test_every_object_of_the_call_is_the_pine_layer_type(family):
    cpp = _consumers(family)
    expected = _TYPES[family]
    assert _HEADER in cpp
    members = _members(cpp, family)
    chart = [name for _kind, name in members if not name.startswith("_sec")]
    requested = [name for _kind, name in members if name.startswith("_sec")]
    assert len(chart) >= 4, members
    assert requested, members
    assert {kind for kind, _name in members} == {
        expected, f"pineforge::source::FirstCallBound<{expected}>",
    }, members
    # The input length's per-run reset and the simple length's first-call
    # factory build the declared type.
    assert re.search(rf"= {re.escape(expected)}\(.*get_input_int", cpp)
    assert f"return {expected}(" in cpp
    # No declaration, reset or factory names the engine class any more.
    assert _KERNEL[family] not in cpp_code_only(cpp)


@pytest.mark.parametrize("family", sorted(_TYPES))
def test_consumers_compile_against_the_paired_engine(family):
    # The engine header must define the two objects: an engine without them
    # fails here, at the member declarations.
    compile_cpp(_consumers(family), label=f"positional_{family}")


@pytest.mark.parametrize("family", sorted(_TYPES))
def test_precalculation_builds_the_pine_layer_type_without_a_length_handler(family):
    cpp = transpile(
        '//@version=6\nstrategy("precalculated momentum")\n'
        f"value = ta.{family}(close, 3)\nplot(value)\n"
    )
    precalc = cpp.split("void precalculate(const Bar* bars, int n)", 1)[1].split("\n    }", 1)[0]
    # Built for the replay and rebuilt for the run.
    assert precalc.count(f"= {_TYPES[family]}(") == 2
    # The objects accept every length, so the replay keeps no length-stop
    # handler: that belongs to the checked EMA-family adapters.
    assert "try {" not in precalc
    assert "_pf_precalc_error" not in precalc
