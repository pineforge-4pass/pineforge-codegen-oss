"""ta.sar, ta.dmi, ta.mfi, ta.rci and ta.cci are built from the engine's Pine
source-layer types.

The kernel classes keep their own rules; the Pine built-ins follow the
source-layer types ``PineSar``, ``PineDmiReference``, ``PineMfi``, ``PineRci``
and ``PineCci`` (``codegen/ta.py::_TA_PINE_LAYER_CLASSES``).

The routing test is the emitter's shape: every declaration, reset and factory
of a site spells the selected type. The table test holds the class table, the
include and guard texts, the reserved macro names and the test helpers'
spelling list to one another. The hosted tests run the emitted translation
unit on invented bars, on the precalculated route and on the live one, and
read the script's own values. Each expected row is worked by hand in its
test's comment; none is a value recorded on TradingView or copied from a run.
"""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.codegen.helpers import CPP_STANDARD_MACROS
from pineforge_codegen.cpp_allowlist import ALLOWED_INCLUDES
from tests._compile import compile_cpp, run_emitted_tu
from tests._legacy_cpp import _EMITTER_SOURCE_SPELLINGS


_LENGTH_HEADER = "pineforge/source/pine_ta_length.hpp"
# family: (selected type, kernel class, header, constant call, input call,
#          input declaration)
_FAMILIES = {
    "sar": ("pineforge::source::PineSar", "ta::SAR", _LENGTH_HEADER,
            "ta.sar(0.02, 0.02, 0.2)", "ta.sar(settingInput, 0.02, 0.2)",
            'settingInput = input.float(0.02, "Setting")'),
    "dmi": ("pineforge::source::PineDmiReference", "ta::DMI", _LENGTH_HEADER,
            "ta.dmi(5, 5)", "ta.dmi(settingInput, 5)",
            'settingInput = input.int(5, "Setting")'),
    "mfi": ("pineforge::source::PineMfi", "ta::MFI", _LENGTH_HEADER,
            "ta.mfi(close, 5)", "ta.mfi(close, settingInput)",
            'settingInput = input.int(5, "Setting")'),
    "rci": ("pineforge::source::PineRci", "ta::RCI", _LENGTH_HEADER,
            "ta.rci(close, 5)", "ta.rci(close, settingInput)",
            'settingInput = input.int(5, "Setting")'),
    "cci": ("pineforge::source::PineCci", "ta::CCI", "pineforge/source/pine_ta_cci.hpp",
            "ta.cci(close, 5)", "ta.cci(close, settingInput)",
            'settingInput = input.int(5, "Setting")'),
}


def _members(cpp, family):
    """Every declared object of ``ta.<family>``: its C++ type and its name."""
    return re.findall(
        rf"^\s*(\S.*?)\s+((?:_sec\d+_)?_ta_{family}_\w+);$", cpp, re.M
    )


def _routing_source(family):
    """One site of each kind the type is spelled for: constant arguments, an
    input argument (rebuilt per run) and a request.security copy."""
    _selected, _kernel, _header, constant, from_input, declaration = _FAMILIES[family]
    if family == "dmi":
        # The tuple form; the requested copy is read through a helper.
        return (
            '//@version=6\nstrategy("source types")\n' + declaration + "\n"
            f"[fixedPlus, fixedMinus, fixedValue] = {constant}\n"
            f"[inputPlus, inputMinus, inputValue] = {from_input}\n"
            f'higher() => request.security(syminfo.tickerid, "60", {constant})\n'
            "[requestedPlus, requestedMinus, requested] = higher()\n"
            "plot(fixedValue + inputValue + requested)\n"
        )
    return (
        '//@version=6\nstrategy("source types")\n' + declaration + "\n"
        f"fixedValue = {constant}\n"
        f"inputValue = {from_input}\n"
        f'requested = request.security(syminfo.tickerid, "60", {constant})\n'
        "plot(fixedValue + inputValue + requested)\n"
    )


@pytest.mark.parametrize("family", _FAMILIES)
def test_every_site_is_built_from_the_source_layer_type(family):
    selected, kernel, header, _constant, _from_input, _declaration = _FAMILIES[family]
    cpp = transpile(_routing_source(family))
    members = _members(cpp, family)
    chart = [name for _kind, name in members if not name.startswith("_sec")]
    requested = [name for _kind, name in members if name.startswith("_sec")]
    assert len(chart) >= 2, members
    assert requested, members
    for kind, name in members:
        assert kind in (selected, f"pineforge::source::FirstCallBound<{selected}>"), (kind, name)
    # No member, reset or factory is left on the kernel class. A tuple result
    # type (ta::DMIResult) is not a site's type and keeps its spelling.
    assert not re.search(rf"\b{re.escape(kernel)}\s+(?:_sec\d+_)?_ta_", cpp)
    assert f"= {kernel}(" not in cpp
    assert f"return {kernel}(" not in cpp
    # The site whose argument is an input rebuilds the same type from the
    # input's getter, in its per-run reset or its first-call factory.
    assert re.search(rf"(?:= |return ){re.escape(selected)}\(.*get_input_", cpp)
    # The type's header is included: once, and before the math header.
    include = f"#include <{header}>\n"
    assert cpp.count(include) == 1
    assert cpp.index(include) < cpp.index("#include <pineforge/math.hpp>\n")
    # These types check no argument: the replay keeps no invalid-length handler.
    assert "_pf_precalc_error" not in cpp


@pytest.mark.parametrize("family", _FAMILIES)
def test_every_site_compiles_against_the_paired_engine(family):
    # The engine header must declare the type with the kernel class's
    # constructor and methods: an engine without it fails here, at the
    # include or at the member declarations.
    compile_cpp(transpile(_routing_source(family)), label=f"source_type_{family}")


def test_the_class_table_its_headers_guards_and_reserved_names_agree():
    table = CodeGen._TA_PINE_LAYER_CLASSES
    includes = CodeGen._TA_PINE_LAYER_INCLUDES
    guards = CodeGen._TA_PINE_LAYER_GUARDS
    features = set()
    for class_name, (cpp_type, header, feature, checked) in table.items():
        # Every header a row names has an include line to write.
        assert header in includes, class_name
        assert isinstance(checked, bool), class_name
        if feature is not None:
            features.add(feature)
        # A historical comparison must know the spelling as the emitter's.
        if cpp_type.startswith("pineforge::source::"):
            assert cpp_type in _EMITTER_SOURCE_SPELLINGS, class_name
    for header, line in includes.items():
        path = f"pineforge/source/{header}.hpp"
        assert line == f"#include <{path}>"
        # A translation unit with the line passes the generated-C++ allowlist.
        assert path in ALLOWED_INCLUDES
        assert line in _EMITTER_SOURCE_SPELLINGS
    # The header of the dynamic-length lowering is written first.
    assert next(iter(includes)) == CodeGen._TA_LENGTH_HEADER
    # A feature macro is an object-like macro of an included header: a script
    # name with its spelling must be escaped.
    assert features <= CPP_STANDARD_MACROS
    # A guard is the guard of a feature some class names.
    assert set(guards) <= features
    for feature, lines in guards.items():
        assert lines[0] == f"#ifndef {feature}"
        assert lines[1].startswith('#error "') and lines[1].endswith(f'({feature})"')
        assert lines[2:] == ("#endif",)
    # The five built-ins this module is about.
    for selected, kernel, header, *_ in _FAMILIES.values():
        cpp_type, key, feature, checked = table[kernel]
        assert cpp_type == selected
        assert includes[key] == f"#include <{header}>"
        assert feature is not None and not checked


# One hosted run per route on a handle of its own: the bare overload offers
# the precalculated route, which replays the call over the whole feed before
# the first bar, and the overload with a timeframe does not. Every bar's
# observed values are printed as "<route> <bar> <value>...", na spelled out
# so that it is never a zero.
_DRIVER = r"""
#include <cstddef>
#include <cstdio>
#include <pineforge/run_failure.hpp>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>
// A member the emitter keeps as a series is read at its current bar.
template <class T> static double observed_value(const T& value) {
    if constexpr (std::is_arithmetic_v<T>) return static_cast<double>(value);
    else return static_cast<double>(value[0]);
}
struct RecordingStrategy : GeneratedStrategy {
    std::vector<std::vector<double>> rows;
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        rows.push_back({OBSERVED_VALUES});
    }
};
int main() {
    // Finite, well-formed one-minute bars: open, high, low, close, volume, time.
    const Bar bars[] = {
BAR_ROWS
    };
    const int count = static_cast<int>(sizeof bars / sizeof bars[0]);
    for (bool cached : {true, false}) {
        RecordingStrategy strategy;
        if (cached) strategy.run(bars, count);
        else strategy.run(bars, count, "1", "1");
        const auto failure = pineforge::run_failure_value_of(strategy);
        const auto state = strategy.native_state();
        if (failure.code != pineforge::RunFailureCode::none ||
            strategy.last_run_status() != 0 || !strategy.last_error().empty() ||
            state.kind != pineforge::NativeLifecycleKind::Completed)
            throw std::runtime_error(
                std::string("hosted run failed: ") +
                pineforge::run_failure_code_of(strategy) + ": " + strategy.last_error());
        if (strategy._use_precalc != cached)
            throw std::runtime_error("the run did not take the requested route");
        if (strategy.rows.size() != static_cast<std::size_t>(count))
            throw std::runtime_error("the script did not run once per bar");
        for (int i = 0; i < count; ++i) {
            std::printf("%s %d", cached ? "cached" : "live", i);
            for (double value : strategy.rows[i]) {
                if (is_na(value)) std::printf(" na");
                else std::printf(" %.17g", value);
            }
            std::printf("\n");
        }
    }
}
"""


def _observe(family, body, observed, bars, label):
    """The observed members on every bar of both routes: route -> rows, na as
    None.

    ``bars`` are invented (open, high, low, close, volume) rows, one minute
    apart."""
    cpp = transpile('//@version=6\nstrategy("source type values")\n' + body)
    # Precondition of the cached rows, on any emitter: the script has one
    # call of the built-in, and the replay that runs before the first bar
    # steps it.
    members = {name for _kind, name in _members(cpp, family)}
    replayed = set(re.findall(rf"\b_precalc_(_ta_{family}_\w+)\[i\] =", cpp))
    assert len(members) == 1 and replayed == members, (members, replayed)
    rows = ",\n".join(
        "        {" + ", ".join(repr(float(value)) for value in bar)
        + f", {1743465600000 + index * 60000}LL}}"
        for index, bar in enumerate(bars)
    )
    driver = _DRIVER.replace(
        "OBSERVED_VALUES", ", ".join(f"observed_value({name})" for name in observed)
    ).replace("BAR_ROWS", rows)
    out = run_emitted_tu(cpp, driver, opt="-O0", label=label)
    table = {}
    for line in out.splitlines():
        route, _bar, *values = line.split()
        table.setdefault(route, []).append(
            [None if value == "na" else float(value) for value in values]
        )
    assert sorted(table) == ["cached", "live"], out
    return table


def _assert_rows(table, expected):
    """Both routes read ``expected`` on every bar: None is na, a number is
    met within 1e-9."""
    for route in ("cached", "live"):
        rows = table[route]
        assert len(rows) == len(expected), (route, rows)
        for bar, (actual, wanted) in enumerate(zip(rows, expected)):
            wanted = wanted if isinstance(wanted, tuple) else (wanted,)
            assert len(actual) == len(wanted), (route, bar, actual)
            for got, want in zip(actual, wanted):
                if want is None:
                    assert got is None, (route, bar, actual, wanted)
                else:
                    assert got is not None and abs(got - want) <= 1e-9, (
                        route, bar, actual, wanted)


def _bars(closes, volumes=None):
    """Bars around invented closes: open at the close, a range of one each way."""
    volumes = volumes or [1.0] * len(closes)
    return [(close, close + 1.0, close - 1.0, close, volume)
            for close, volume in zip(closes, volumes)]


@pytest.mark.parametrize("first_volume,first_value", [(1.0, 75.0), (2.0, 200.0 / 3.0)])
def test_mfi_first_value_counts_the_first_bar_on_both_sides(first_volume, first_value):
    # ta.mfi(close, 2) on closes 2 4 2 4 2 4. The first bar's flow (close *
    # volume) enters both sums, so the first value is on bar 1:
    #   volume 1: upper 2 + 4, lower 2: 100 - 100 / (1 + 6 / 2) = 75
    #   first volume 2: upper 4 + 4, lower 4: 100 - 100 / (1 + 8 / 4) = 200 / 3
    # From bar 2 the window holds one rising flow of 4 and one falling flow of
    # 2: 100 - 100 / (1 + 4 / 2) = 200 / 3. The kernel class is na on bar 1.
    closes = [2.0, 4.0, 2.0, 4.0, 2.0, 4.0]
    volumes = [first_volume] + [1.0] * 5
    table = _observe(
        "mfi", "observed = ta.mfi(close, 2)\nplot(observed)\n", ("observed",),
        _bars(closes, volumes), "source_type_mfi",
    )
    _assert_rows(table, [None, first_value] + [200.0 / 3.0] * 4)


def test_rci_is_na_through_bar_length_minus_one():
    # ta.rci(close, 5): na on bars 0 to 4, then the rank correlation of the
    # last five closes. The kernel class answers 50 on bar 4.
    #   bar 5: 14 12 17 13 10 -> -50      bar 8: 13 10 16 19 15 -> 60
    #   bar 6: 12 17 13 10 16 ->   0      bar 9: 10 16 19 15 18 -> 50
    #   bar 7: 17 13 10 16 19 ->  30
    closes = [11.0, 14.0, 12.0, 17.0, 13.0, 10.0, 16.0, 19.0, 15.0, 18.0]
    table = _observe(
        "rci", "observed = ta.rci(close, 5)\nplot(observed)\n", ("observed",),
        _bars(closes), "source_type_rci",
    )
    _assert_rows(table, [None] * 5 + [-50.0, 0.0, 30.0, 60.0, 50.0])


def test_cci_of_a_flat_window_is_zero():
    # The source is 0.1 on every bar (close * 0 is 0 for a finite close).
    # Three values of 0.1 sum to 0.30000000000000004, so the kernel class
    # reads a mean deviation of one rounding step and answers -1 / 0.015,
    # about -66.67, from bar 2 on. The source-layer type answers 0.
    table = _observe(
        "cci", "observed = ta.cci(close * 0 + 0.1, 3)\nplot(observed)\n", ("observed",),
        _bars([20.0, 21.0, 19.0, 22.0, 20.5]), "source_type_cci",
    )
    _assert_rows(table, [None, None, 0.0, 0.0, 0.0])


def test_sar_reads_a_close_inside_the_band_as_equal():
    # ta.sar(0.02, 0.02, 0.2). Bar 1 closes 4e-11 above bar 0, which a Pine
    # relational does not read as greater: the trend starts short from the
    # previous high 12 (12 - 0.02 * 3 = 11.94 is above the bar's high, so the
    # line is held at 12; bar 2 the same). The kernel class compares exactly,
    # starts long from the previous low and answers 8 on both bars.
    bars = [
        (10.0, 12.0, 8.0, 10.0, 1.0),
        (10.0, 11.0, 9.0, 10.00000000004, 1.0),
        (9.5, 10.5, 8.5, 9.0, 1.0),
    ]
    table = _observe(
        "sar", "observed = ta.sar(0.02, 0.02, 0.2)\nplot(observed)\n", ("observed",),
        bars, "source_type_sar",
    )
    _assert_rows(table, [None, 12.0, 12.0])


def test_dmi_holds_both_indices_on_a_zero_range_bar():
    # ta.dmi(1, 2). Bar 4 is flat at bar 3's close: its true range is zero
    # and both quotients are 0 / 0. Both indices keep bar 3's values (50, 0)
    # and ADX takes one more step: (100 + 75) / 2 = 87.5, then
    # (100 + 87.5) / 2 = 93.75 and 93.75 / 2 = 46.875. The kernel class
    # answers three na on bar 4 and its ADX misses that step.
    bars = [
        (9.0, 10.0, 8.0, 9.0, 1.0),
        (10.0, 11.0, 9.0, 10.0, 1.0),
        (10.0, 11.0, 9.0, 10.0, 1.0),
        (11.0, 12.0, 10.0, 12.0, 1.0),
        (12.0, 12.0, 12.0, 12.0, 1.0),
        (11.0, 12.0, 10.0, 11.0, 1.0),
        (11.0, 12.0, 10.0, 11.0, 1.0),
    ]
    table = _observe(
        "dmi",
        "[plusIndex, minusIndex, adxValue] = ta.dmi(1, 2)\n"
        "plot(plusIndex + minusIndex + adxValue)\n",
        ("plusIndex", "minusIndex", "adxValue"), bars, "source_type_dmi",
    )
    _assert_rows(table, [
        (None, None, None),
        (50.0, 0.0, None),
        (0.0, 0.0, 50.0),
        (50.0, 0.0, 75.0),
        (50.0, 0.0, 87.5),
        (0.0, 100.0, 93.75),
        (0.0, 0.0, 46.875),
    ])
