"""Generated consumers use the paired, checked EMA-family source API.

These are routing and execution-contract regressions, authored before the
emitter change. They select no TradingView startup value, seed or NA mask.
The supplementary scripts' positional/named/helper/nested consumers are
extended with loop, lazy and requested contexts here. The original reporter
and plain-copy control remain in test_issue_291_pins.py.
"""

import re

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp, run_emitted_tu


_HEADER = "#include <pineforge/source/pine_ta_length.hpp>"
_TYPES = {
    "ema": "pineforge::source::PineEma",
    "tsi": "pineforge::source::PineTsi",
    "kc": "_PFKC",
    "kcw": "_PFKCW",
    "macd": "pineforge::source::PineMacd",
}
_POSITIONAL = {
    "ema": "ta.ema({sample}, {span})",
    "tsi": "ta.tsi({sample}, {span}, {second})",
    "kc": "ta.kc({sample}, {span}, 1.5, false)",
    "kcw": "ta.kcw({sample}, {span}, 1.5, false)",
    "macd": "ta.macd({sample}, {span}, {second}, {signal})",
}
_NAMED = {
    "ema": "ta.ema(length={span}, source={sample})",
    "tsi": "ta.tsi(long_length={second}, source={sample}, short_length={span})",
    "kc": "ta.kc(mult=1.5, series={sample}, useTrueRange=false, length={span})",
    "kcw": "ta.kcw(useTrueRange=false, length={span}, series={sample}, mult=1.5)",
    "macd": "ta.macd(siglen={signal}, source={sample}, slowlen={second}, fastlen={span})",
}


def _assignment(family, name, call):
    if family == "kc":
        return f"[{name}, {name}Upper, {name}Lower] = {call}\n"
    if family == "macd":
        return f"[{name}Line, {name}Signal, {name}] = {call}\n"
    return f"{name} = {call}\n"


def _source(family, qualifier):
    # Distinct lengths expose constructor argument swaps; they are inputs,
    # never expected numerical answers.
    definitions = ""
    if qualifier == "fixed":
        spans = ("13", "25", "9")
    else:
        spans = ("lengthInput", "secondInput", "signalInput")
        for name, default, title in zip(
            spans, (13, 25, 9), ("Length", "Second length", "Signal length")
        ):
            if qualifier == "const":
                definitions += f"const int {name} = {default}\n"
            elif qualifier == "input":
                definitions += f'{name} = input.int({default}, "{title}")\n'
            else:
                definitions += (
                    f"simple int {name} = syminfo.type == \"crypto\""
                    f" ? {default} : {default + 2}\n"
                )
    args = dict(sample="sourceValue", span=spans[0],
                second=spans[1], signal=spans[2])
    helper_call = f"sampleFamily(sourceValue, {', '.join(spans)})"
    local = _assignment(
        family, "helperResult",
        _POSITIONAL[family].format(
            sample="sample", span="span", second="secondSpan", signal="signalSpan"
        ),
    )
    return (
        '//@version=6\nstrategy("checked family consumers")\n'
        + definitions
        + "sampleFamily(float sample, simple int span, simple int secondSpan,"
          " simple int signalSpan) =>\n"
        + "".join("    " + line + "\n" for line in local.splitlines())
        + "    helperResult\n"
        + "sourceValue = bar_index % 11 == 4 ? float(na) : close\n"
        + _assignment(family, "staticValue", _POSITIONAL[family].format(
            **dict(args, sample="close")
        ))
        + _assignment(family, "positional", _POSITIONAL[family].format(**args))
        + _assignment(family, "namedValue", _NAMED[family].format(**args))
        + f"helperValue = {helper_call}\n"
        + f"otherContext = sampleFamily(sourceValue + open / 256, {', '.join(spans)})\n"
        + f"nestedValue = ta.ema({helper_call}, 3)\n"
        + "previousValue = positional[1]\n"
        + f"writtenPrevious = {helper_call}[1]\n"
        + f"alternateValue = close > open ? {helper_call}"
          f" : sampleFamily(open, {', '.join(spans)})\n"
        + "float accumulated = 0.0\nfor iteration = 0 to 2\n"
        + f"    accumulated += sampleFamily(close + iteration, {', '.join(spans)})\n"
        + f"lazyValue = close > open and {helper_call} > 0\n"
        + f'localValue = request.security(syminfo.tickerid, "60", {helper_call})\n'
        + f'foreignValue = request.security("PF:OTHER", "240", {helper_call})\n'
        + "if bar_index % 2 == 0 and foreignValue > 0\n"
        + '    strategy.entry("foreign", strategy.long, qty = foreignValue)\n'
        + "plot(staticValue + positional + namedValue + alternateValue + helperValue + otherContext"
          " + nestedValue + previousValue + writtenPrevious"
          " + accumulated + localValue + foreignValue)\n"
        + "plot(lazyValue ? 1 : 0)\n"
    )


def _members(cpp, family):
    return re.findall(
        rf"^\s*(\S.*?)\s+((?:_sec\d+_)?_ta_{family}_\w+);$", cpp, re.M
    )


@pytest.mark.parametrize("family", _TYPES)
@pytest.mark.parametrize("qualifier", ("fixed", "const", "input", "simple"))
def test_every_consumer_uses_the_checked_type_and_matching_factory(family, qualifier):
    cpp = transpile(_source(family, qualifier))
    assert _HEADER in cpp
    expected = _TYPES[family]
    members = _members(cpp, family)
    chart = [name for _kind, name in members if not name.startswith("_sec")]
    requested = [name for _kind, name in members if name.startswith("_sec")]
    assert len(chart) >= 4, members
    assert len(set(chart)) == len(chart), members
    assert requested, members
    for kind, name in members:
        assert kind in (expected, f"pineforge::source::FirstCallBound<{expected}>"), (kind, name)
    if qualifier == "simple":
        assert any("FirstCallBound<" in kind for kind, _name in members)
        assert f"return {expected}(" in cpp
    else:
        # Input-stable sites retain their constructor, resets and precalc.
        assert any(f"_precalc_{name}" in cpp for name in chart)
    if family in ("kc", "kcw"):
        adapter = "PineKc" if family == "kc" else "PineKcw"
        assert f"pineforge::source::{adapter} impl_;" in cpp
        assert "PF_KC_HAS_USE_TRUE_RANGE" not in cpp
    else:
        kernel = {"ema": "EMA", "tsi": "TSI", "macd": "MACD"}[family]
        assert not re.search(rf"\bta::{kernel}\s+_?(?:sec\d+_)?_ta_", cpp)
        assert f"return ta::{kernel}(" not in cpp
        assert f"= ta::{kernel}(" not in cpp
    assert "_PFForeignEmaSeeding _pf_ema_seeding;" in cpp
    # Compilation covers the complete generated class, factories, requested
    # copies, tuple result types, lifecycle resets and both method branches.
    # A present but obsolete engine header must fail; no header-version skip.
    compile_cpp(cpp, label=f"checked_{family}_{qualifier}")


_OBSERVED = {
    "kc": ("observed", "observedUpper", "observedLower"),
    "macd": ("observedLine", "observedSignal", "observed"),
}


def _hosted_script(family, named, sample="sourceValue[1]"):
    # The hosted family call reads the selected input source one bar back. A
    # history reference is missing on the first bar by Pine's own rule, so the
    # first call's sample is na while every bar the host admits stays finite;
    # from the second bar on it is the series the "Source" input selects. The
    # emitter-shape test passes the original unlagged sample.
    call = (_NAMED if named else _POSITIONAL)[family].format(
        sample=sample, span="lengthInput", second="secondInput", signal="signalInput"
    )
    observed = _OBSERVED.get(family, ("observed",))
    source = (
        '//@version=6\nstrategy("checked precalc")\n'
        'lengthInput = input.int(13, "Length")\n'
        'secondInput = input.int(25, "Second length")\n'
        'signalInput = input.int(9, "Signal length")\n'
        'sourceValue = input.source(high, "Source")\n'
        "var int visited = 0\nvar int completed = 0\n"
        "visited += 1\n"
        "sourceLag = sourceValue[1]\n"
        "beforeValue = ta.sma(open, 2)\n"
        + _assignment(family, "observed", call)
        + f"completed += 1\nplot({' + '.join(observed)} + beforeValue + visited + completed + sourceLag)\n"
    )
    return source, observed


def _length_keys(family):
    keys = ["Length"]
    if family in ("tsi", "macd"):
        keys.append("Second length")
    if family == "macd":
        keys.append("Signal length")
    return keys


# The host owns the lifecycle in the drivers built on this harness: run()
# admits the bars, prepares the script once through prepare_script_run and
# dispatches every bar through the scheduler to on_source_bar. The bare
# overload offers the precalculated route and the explicit-timeframe overload
# does not (tests/test_ta_float_setting_from_input.py measures both). The host
# records a stop the script raises instead of rethrowing it, so the drivers
# read its failure record; a failed handle is terminal and is never run again.
_HOSTED_HARNESS = r"""
#include <cmath>
#include <cstddef>
#include <cstdio>
#include <pineforge/run_failure.hpp>
#include <stdexcept>
#include <string>
#include <vector>
void equal_value(double actual, double expected, const char* what) {
    if (is_na(actual) != is_na(expected) ||
        (!is_na(expected) && actual != expected &&
         (!std::isfinite(actual) || !std::isfinite(expected) ||
          std::abs(actual - expected) > 1e-10 * (1 + std::abs(expected)))))
        throw std::runtime_error(what);
}
// Observes the host's own calls. Arguments reach the generated hooks
// unchanged, and nothing here throws: the host would record a throwing hook
// as a failure of the run.
struct RecordingStrategy : GeneratedStrategy {
    int preparations = 0;
    bool offered = false;
    bool published = false;
    bool caches_empty = false;
    bool script_ran_in_preparation = false;
    bool source_history_leaked = false;
    std::vector<std::vector<double>> values;
    void prepare_script_run(const Bar* bars, int n, bool allow) override {
        ++preparations;
        offered = allow;
        GeneratedStrategy::prepare_script_run(bars, n, allow);
        published = _use_precalc;
        caches_empty = EMPTY_CACHES;
        script_ran_in_preparation = visited != 0 || completed != 0;
        // The host's own source series are read only after an offered replay,
        // which is what could leave history in them.
        source_history_leaked = sourceValue.size() != 0 ||
            get_input_source("Source", sourceValue).size() != 0;
        values.clear();
    }
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        values.push_back({sourceValue[0], sourceLag, beforeValue, OBSERVED_VALUES});
    }
};
// Finite, well-formed one-minute bars: the host admits every one of them.
void fill_bars(Bar* bars, int count) {
    for (int i = 0; i < count; ++i) {
        const double value = 100.0 + i + (i % 5) * 0.25;
        bars[i] = {value, value + 2, value - 1, value + 0.5, 1.0,
                   1743465600000LL + i * 60000LL};
    }
}
// One hosted lifecycle on this handle, on the route its overload selects.
void run_route(RecordingStrategy& strategy, const Bar* bars, int count, bool cached) {
    if (cached) strategy.run(bars, count);
    else strategy.run(bars, count, "1", "1");
}
// The host's preparation of the latest run, as the hook observed it.
void require_preparation(RecordingStrategy& strategy, int lifecycles, bool cached,
                         bool publishes) {
    if (strategy.preparations != lifecycles)
        throw std::runtime_error("host did not prepare each run exactly once");
    if (strategy.offered != cached)
        throw std::runtime_error("host did not offer the requested route");
    if (strategy.published != publishes)
        throw std::runtime_error("wrong precalculation publication");
    if (strategy.script_ran_in_preparation)
        throw std::runtime_error("preparation executed the script");
    if (strategy.source_history_leaked)
        throw std::runtime_error("precalculation leaked input source history");
}
// A run the host completed: an empty failure record, a Completed lifecycle,
// the requested route, and the script executed on every bar.
void require_completed(RecordingStrategy& strategy, int count, bool cached) {
    const auto failure = pineforge::run_failure_value_of(strategy);
    const auto state = strategy.native_state();
    if (failure.code != pineforge::RunFailureCode::none ||
        strategy.last_run_status() != 0 || !strategy.last_error().empty() ||
        state.kind != pineforge::NativeLifecycleKind::Completed)
        throw std::runtime_error(
            std::string("hosted run failed: ") +
            pineforge::run_failure_code_of(strategy) + ": " + strategy.last_error());
    if (strategy._use_precalc != cached)
        throw std::runtime_error("valid run did not use the requested route");
    if (strategy.visited != count || strategy.completed != count ||
        strategy.values.size() != static_cast<std::size_t>(count))
        throw std::runtime_error("valid run omitted bars");
}
// The bar field a "Source" selection names.
double selected_value(const Bar& bar, const std::string& selected) {
    if (selected == "open") return bar.open;
    if (selected == "high") return bar.high;
    if (selected == "close") return bar.close;
    throw std::runtime_error("unknown source selection");
}
// Every bar of a completed run read the selected input source, and the call's
// own sample, that source one bar back, was missing on the first bar only.
void require_selected_source(RecordingStrategy& strategy, const Bar* bars, int count,
                             const std::string& selected) {
    int published = 0;
    for (int i = 0; i < count; ++i) {
        const auto& row = strategy.values[i];
        equal_value(row[0], selected_value(bars[i], selected),
                    "run did not read the selected input source");
        equal_value(row[1], i == 0 ? na<double>() : selected_value(bars[i - 1], selected),
                    "call sample is not the selected source one bar back");
        if (std::isfinite(row.back())) ++published;
    }
    if (published == 0)
        throw std::runtime_error("valid run did not publish");
}
// Two completed runs recorded the same values on every bar.
void require_same_values(RecordingStrategy& actual_run, RecordingStrategy& expected_run,
                         int count, const char* what) {
    for (int i = 0; i < count; ++i) {
        const auto& actual = actual_run.values[i];
        const auto& expected = expected_run.values[i];
        for (std::size_t j = 0; j < actual.size(); ++j)
            equal_value(actual[j], expected[j], what);
    }
}
"""


def _hosted_driver(main, keys, caches, observed):
    driver = _HOSTED_HARNESS + main
    driver = driver.replace("LENGTH_KEYS", ", ".join(f'"{key}"' for key in keys))
    driver = driver.replace("EMPTY_CACHES", " && ".join(f"{name}.empty()" for name in caches))
    return driver.replace("OBSERVED_VALUES", ", ".join(observed))


@pytest.mark.parametrize("family", _TYPES)
@pytest.mark.parametrize("named", (False, True))
def test_checked_precalculation_emits_checked_type_and_cache(family, named):
    # Emitter shape only, on the original script. Native behaviour is the
    # separate hosted test below, which requires none of these strings.
    source, _observed = _hosted_script(family, named, sample="sourceValue")
    cpp = transpile(source)
    expected = _TYPES[family]
    assert _HEADER in cpp
    assert "void precalculate(const Bar* bars, int n)" in cpp
    cache = f"_precalc_{_members(cpp, family)[0][1]}"
    assert f"{cache}[i] =" in cpp
    caches = re.findall(r"^\s+std::vector<.+> (_precalc_\w+);$", cpp, re.M)
    assert len(caches) >= 2
    # Both resets around precalculation and the live input reset construct the
    # same checked type. The first source below is NA in either route.
    assert re.search(rf"= {re.escape(expected)}\(.*get_input_int", cpp)


@pytest.mark.parametrize("family", _TYPES)
@pytest.mark.parametrize("named", (False, True))
def test_checked_precalculation_defers_length_stop_to_reached_call(family, named):
    # Native host behaviour of whatever the current emitter produces: no
    # emitter-shape string gates the compile, so base and candidate codegen
    # both reach the host and are judged by its typed failure record.
    source, observed = _hosted_script(family, named)
    cpp = transpile(source)
    caches = re.findall(r"^\s+std::vector<.+> (_precalc_\w+);$", cpp, re.M)
    # The driver observes these caches; a script with none has no cached route.
    assert caches, "the script has no precalculated route"
    # The family call's own cached route. Its member is found by the name the
    # analyzer gives the site, whatever type declares it, so the unchecked
    # emitter's member binds too, and the assignment checked is that member's
    # own: the ta.sma cache alone is not evidence. The script has these two
    # sites, hence two caches.
    cache = f"_precalc_{_members(cpp, family)[0][1]}"
    assert f"{cache}[i] =" in cpp
    assert len(caches) >= 2
    keys = _length_keys(family)
    driver = _hosted_driver(r"""
// The typed invalid-length stop as the host recorded it: the code from the
// engine's failure record, the origin from the lifecycle the host latched,
// and the timing from where the script stood when run() returned.
void require_length_stop(RecordingStrategy& strategy, const Bar& first) {
    const auto failure = pineforge::run_failure_value_of(strategy);
    const auto state = strategy.native_state();
    if (failure.code != pineforge::RunFailureCode::pine_invalid_argument)
        throw std::runtime_error(
            std::string("wrong generated length validation: ") +
            pineforge::run_failure_code_of(strategy) + ": " + strategy.last_error());
    if (strategy.last_run_status() != 1 ||
        state.kind != pineforge::NativeLifecycleKind::Failed ||
        state.failure.code != pineforge::NativeFailureCode::CallbackException)
        throw std::runtime_error("length stop was not raised inside a hosted script callback");
    if (strategy.visited != 1 || strategy.completed != 0 || !strategy.values.empty())
        throw std::runtime_error("length stop moved before prior script statements");
    if (!is_na(strategy.sourceLag))
        throw std::runtime_error("first real bar saw replayed source history");
    equal_value(strategy.sourceValue[0], first.high,
                "first bar did not read the selected input source");
}
// A real second begin on the stopped handle. PX refuses it: configure on a
// Failed host renders "native host already failed" (native_execution_consumer
// .cpp:2998-3001), prepare_native_begin then resets the script once with no
// bars and no offer (pine_strategy_host.cpp:686,712-713) and throws, so the
// public begin returns before admission (native_execution_consumer.cpp:1955-
// 1965,11301). The latched lifecycle keeps the first cause; no bar runs.
void require_refused_rerun(RecordingStrategy& strategy, const Bar* bars, int count,
                           bool cached) {
    run_route(strategy, bars, count, cached);
    const auto failure = pineforge::run_failure_value_of(strategy);
    const auto state = strategy.native_state();
    if (strategy.last_run_status() != 1 ||
        state.kind != pineforge::NativeLifecycleKind::Failed ||
        state.failure.code != pineforge::NativeFailureCode::CallbackException ||
        failure.code == pineforge::RunFailureCode::none || strategy.last_error().empty())
        throw std::runtime_error("host accepted a begin on a failed handle");
    if (strategy.preparations != 2 || strategy.offered || strategy.published ||
        strategy.script_ran_in_preparation)
        throw std::runtime_error("refused begin prepared a run");
    if (!strategy.values.empty() || strategy.visited != 0 || strategy.completed != 0)
        throw std::runtime_error("refused begin dispatched script bars");
}
int main() {
    constexpr int count = 64;
    Bar bars[count];
    fill_bars(bars, count);
    int checked = 0;
    for (const char* key : {LENGTH_KEYS}) {
        for (int length : {0, -1, 7}) {
            for (bool precalculate : {false, true}) {
                RecordingStrategy strategy;
                strategy.set_input(key, std::to_string(length));
                strategy.set_input("Source", "high");
                run_route(strategy, bars, count, precalculate);
                require_preparation(strategy, 1, precalculate, precalculate && length > 0);
                if (length > 0) {
                    require_completed(strategy, count, precalculate);
                    require_selected_source(strategy, bars, count, "high");
                } else {
                    if (!strategy.caches_empty)
                        throw std::runtime_error("abandoned replay retained a partial cache");
                    require_length_stop(strategy, bars[0]);
                    if (precalculate) {
                        // The abandoned replay leaves what the live route
                        // leaves, on a handle of its own.
                        RecordingStrategy control;
                        control.set_input(key, std::to_string(length));
                        control.set_input("Source", "high");
                        run_route(control, bars, count, false);
                        require_preparation(control, 1, false, false);
                        require_length_stop(control, bars[0]);
                        equal_value(strategy.beforeValue, control.beforeValue,
                                    "abandoned and live routes differ before the stop");
                        equal_value(strategy.sourceValue[0], control.sourceValue[0],
                                    "abandoned and live routes differ before the stop");
                    }
                    // After the comparisons: another real begin on the
                    // stopped handle, which the host must refuse.
                    require_refused_rerun(strategy, bars, count, precalculate);
                }
                ++checked;
            }
        }
        // The stopped handles above are terminal. Valid lengths and the other
        // selected sources start from fresh handles, one per route.
        for (int valid : {3, 11}) {
            const char* selected = valid == 3 ? "close" : "open";
            RecordingStrategy cached;
            cached.set_input(key, std::to_string(valid));
            cached.set_input("Source", selected);
            RecordingStrategy live;
            live.set_input(key, std::to_string(valid));
            live.set_input("Source", selected);
            run_route(cached, bars, count, true);
            run_route(live, bars, count, false);
            require_preparation(cached, 1, true, true);
            require_preparation(live, 1, false, false);
            require_completed(cached, count, true);
            require_completed(live, count, false);
            require_same_values(cached, live, count, "cached and live routes differ");
            require_selected_source(cached, bars, count, selected);
            ++checked;
        }
    }
    std::printf("%d\n", checked);
}
""", keys, caches, observed)
    out = run_emitted_tu(
        cpp, driver, opt="-O0", label=f"checked_na_{family}_{named}"
    )
    # Per key: three lengths on both routes, then two valid lengths with their
    # selected sources.
    assert int(out.strip()) == len(keys) * (3 * 2 + 2)


@pytest.mark.parametrize("family", _TYPES)
def test_one_handle_reruns_with_changed_length_and_source_inputs(family):
    # Reuse is a contract of completed runs: the host refuses another begin on
    # a handle whose run failed, so the length stop above has no rerun.
    source, observed = _hosted_script(family, named=False)
    cpp = transpile(source)
    caches = re.findall(r"^\s+std::vector<.+> (_precalc_\w+);$", cpp, re.M)
    assert caches, "the script has no precalculated route to rerun"
    keys = _length_keys(family)
    driver = _hosted_driver(r"""
int main() {
    constexpr int count = 64;
    Bar bars[count];
    fill_bars(bars, count);
    int checked = 0;
    for (const char* key : {LENGTH_KEYS}) {
        for (bool cached : {false, true}) {
            // One handle for all three lifecycles. Each run() leaves it
            // Completed, the state the host accepts another begin from. The
            // first lifecycle is the baseline; the next two are the reruns.
            RecordingStrategy reused;
            int lifecycles = 0;
            for (int length : {7, 3, 11}) {
                const char* selected = length == 7 ? "high" : length == 3 ? "close" : "open";
                reused.set_input(key, std::to_string(length));
                reused.set_input("Source", selected);
                run_route(reused, bars, count, cached);
                require_preparation(reused, ++lifecycles, cached, cached);
                require_completed(reused, count, cached);
                require_selected_source(reused, bars, count, selected);
                // A handle that has never run, on the same route, is what
                // the same inputs compute without an earlier lifecycle.
                RecordingStrategy fresh;
                fresh.set_input(key, std::to_string(length));
                fresh.set_input("Source", selected);
                run_route(fresh, bars, count, cached);
                require_preparation(fresh, 1, cached, cached);
                require_completed(fresh, count, cached);
                require_same_values(reused, fresh, count,
                                    "reused handle differs from a fresh handle");
                ++checked;
            }
        }
    }
    std::printf("%d\n", checked);
}
""", keys, caches, observed)
    out = run_emitted_tu(cpp, driver, opt="-O0", label=f"checked_reuse_{family}")
    assert int(out.strip()) == len(keys) * 2 * 3


@pytest.mark.parametrize(
    ("body", "guarded"),
    (
        ("value = ta.ema(close, 3)\n", True),
        ("value = ta.sma(close, 3)\n", False),
        ("unused(float sample) => ta.ema(sample, 3)\nvalue = ta.sma(close, 3)\n", False),
        (
            'simple int span = syminfo.type == "crypto" ? 3 : 5\n'
            "value = ta.ema(close, span) + ta.sma(close, 3)\n",
            False,
        ),
    ),
)
def test_precalculation_handler_only_covers_live_eligible_checked_sites(body, guarded):
    cpp = transpile('//@version=6\nstrategy("precalc handler selection")\n' + body + "plot(value)\n")
    precalc = cpp.split("void precalculate(const Bar* bars, int n)", 1)[1].split("\n    }", 1)[0]
    assert ("catch (const std::exception& _pf_precalc_error)" in precalc) is guarded
    if guarded:
        assert "#include <pineforge/run_failure.hpp>" in cpp
        assert precalc.index(".resize(n);") < precalc.index("try {")
        assert precalc.index("= pineforge::source::PineEma(") < precalc.index("try {")
        assert precalc.index("_use_precalc = true;") < precalc.index("catch (")
        assert precalc.rindex("= pineforge::source::PineEma(") > precalc.index("catch (")
        assert "catch (...)" not in precalc


@pytest.mark.parametrize("begin", (0, -1))
def test_precalculation_propagates_unrelated_string_errors_and_allocation_failures(begin):
    # Long text forces a real allocation in the replay's source expression.
    # The invalid substring raises the engine's carried pine_string_error,
    # independent of the checked EMA length.
    padding = "0" * 64
    cpp = transpile(
        '//@version=6\nstrategy("precalc failure boundary")\n'
        "var int visited = 0\nvisited += 1\n"
        f'value = ta.ema(str.tonumber(str.substring("{padding}" + str.tostring(close), {begin})), 3)\n'
        "plot(value + visited)\n"
    )
    member = _members(cpp, "ema")[0][1]
    cache = f"_precalc_{member}"
    assert f"{cache}[i] =" in cpp
    driver = r"""
#include <cstdio>
#include <cstdlib>
#include <new>
#include <pineforge/run_failure.hpp>
#include <stdexcept>
#include <string>
static bool fail_next_allocation = false;
void* operator new(std::size_t size) {
    if (fail_next_allocation) {
        fail_next_allocation = false;
        throw std::bad_alloc();
    }
    if (void* memory = std::malloc(size ? size : 1)) return memory;
    throw std::bad_alloc();
}
void operator delete(void* memory) noexcept { std::free(memory); }
void operator delete(void* memory, std::size_t) noexcept { std::free(memory); }
void* operator new[](std::size_t size) { return ::operator new(size); }
void operator delete[](void* memory) noexcept { std::free(memory); }
void operator delete[](void* memory, std::size_t) noexcept { std::free(memory); }
// Observes the host's own preparation call; its arguments pass through
// unchanged.
struct HostedStrategy : GeneratedStrategy {
    int preparations = 0;
    bool offered = false;
    void prepare_script_run(const Bar* bars, int n, bool allow) override {
        ++preparations;
        offered = allow;
        GeneratedStrategy::prepare_script_run(bars, n, allow);
    }
};
int main() {
    Bar bars[] = {{100.0, 102.0, 99.0, 101.0, 1.0, 1743465600000LL}};
    // Direct preparation: the replay's own exception is caught where it is
    // raised. This object is never handed to the host.
    GeneratedStrategy strategy;
    bool stopped = false;
    try {
        strategy.prepare_script_run(bars, 1, true);
    } catch (const std::exception& error) {
        const auto* failure = dynamic_cast<const pineforge::RunFailureInfo*>(&error);
        if (!failure || failure->run_failure_code() != pineforge::RunFailureCode::pine_string_error)
            throw;
        stopped = true;
    }
    if (stopped != INVALID_BEGIN || strategy.visited != 0 ||
        strategy._use_precalc != !stopped)
        throw std::runtime_error("unrelated replay failure was suppressed");
    int checked = 1;
    if (!stopped) {
        // The successful counterpart runs on the real host, on a fresh
        // handle: run() prepares it once and the scheduler dispatches the bar.
        HostedStrategy hosted;
        hosted.run(bars, 1);
        const auto failure = pineforge::run_failure_value_of(hosted);
        const auto state = hosted.native_state();
        if (failure.code != pineforge::RunFailureCode::none ||
            hosted.last_run_status() != 0 || !hosted.last_error().empty() ||
            state.kind != pineforge::NativeLifecycleKind::Completed)
            throw std::runtime_error(
                std::string("hosted valid string source failed: ") +
                pineforge::run_failure_code_of(hosted) + ": " + hosted.last_error());
        if (hosted.preparations != 1 || hosted._use_precalc != hosted.offered)
            throw std::runtime_error("host did not prepare the valid string source once");
        if (hosted.visited != 1)
            throw std::runtime_error("valid string source did not execute");
        for (bool inside_replay : {false, true}) {
            GeneratedStrategy failing;
            // With capacity reserved, resize cannot allocate; the long
            // string source still must allocate during actual replay.
            if (inside_replay) failing.CACHE_MEMBER.reserve(1);
            fail_next_allocation = true;
            bool allocation_stopped = false;
            try {
                failing.precalculate(bars, 1);
            } catch (const std::bad_alloc&) {
                allocation_stopped = true;
            }
            fail_next_allocation = false;
            if (!allocation_stopped || failing._use_precalc || failing.visited != 0)
                throw std::runtime_error("allocation failure was suppressed");
            ++checked;
        }
    }
    std::printf("%d\n", checked);
}
"""
    driver = driver.replace("CACHE_MEMBER", cache).replace(
        "INVALID_BEGIN", "true" if begin < 0 else "false"
    )
    out = run_emitted_tu(cpp, driver, opt="-O0", label=f"precalc_failure_boundary_{begin}")
    assert int(out.strip()) == (1 if begin < 0 else 3)


def test_keltner_scope_restores_context_on_compute_and_recompute_failure():
    cpp = transpile(
        '//@version=6\nstrategy("checked keltner scope")\n'
        "[basis, upper, lower] = ta.kc(close, 3, 1.5, false)\n"
        "widthValue = ta.kcw(close, 3, 1.5)\nplot(basis + widthValue)\n"
    )
    assert "pineforge::source::PineKc impl_;" in cpp
    assert "pineforge::source::PineKcw impl_;" in cpp
    driver = r"""
#include <cstdio>
#include <stdexcept>
template<class Channel>
int check_scope() {
    int checked = 0;
    for (bool prior : {false, true}) {
        for (int length : {0, -1, 3}) {
            for (bool again : {false, true}) {
                ta::ema_na_warmup_flag() = prior;
                Channel channel(length, 1.5, false);
                bool stopped = false;
                try {
                    if (again) (void)channel.recompute(na<double>(), 4.0, 2.0, 3.0);
                    else (void)channel.compute(na<double>(), 4.0, 2.0, 3.0);
                } catch (const std::exception&) {
                    stopped = true;
                }
                if (stopped != (length <= 0))
                    throw std::runtime_error("wrong checked adapter route");
                if (ta::ema_na_warmup_flag() != prior)
                    throw std::runtime_error("keltner context leaked");
                ++checked;
            }
        }
    }
    return checked;
}
int main() {
    const bool prior = ta::ema_na_warmup_flag();
    const int checked = check_scope<_PFKC>() + check_scope<_PFKCW>();
    ta::ema_na_warmup_flag() = prior;
    std::printf("%d\n", checked);
}
"""
    out = run_emitted_tu(cpp, driver, opt="-O0", label="checked_keltner_scope")
    assert int(out.strip()) == 2 * 2 * 3 * 2
