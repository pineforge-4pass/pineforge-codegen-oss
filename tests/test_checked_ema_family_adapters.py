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


@pytest.mark.parametrize("family", _TYPES)
@pytest.mark.parametrize("named", (False, True))
def test_checked_precalculation_defers_length_stop_to_reached_call(family, named):
    call = (_NAMED if named else _POSITIONAL)[family].format(
        sample="sourceValue", span="lengthInput", second="secondInput", signal="signalInput"
    )
    observed = {
        "kc": ("observed", "observedUpper", "observedLower"),
        "macd": ("observedLine", "observedSignal", "observed"),
    }.get(family, ("observed",))
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
    driver = r"""
#include <cstdio>
#include <pineforge/run_failure.hpp>
#include <stdexcept>
void equal_value(double actual, double expected) {
    if (is_na(actual) != is_na(expected) ||
        (!is_na(expected) && actual != expected &&
         (!std::isfinite(actual) || !std::isfinite(expected) ||
          std::abs(actual - expected) > 1e-10 * (1 + std::abs(expected)))))
        throw std::runtime_error("cached and live routes differ");
}
struct RecordingStrategy : GeneratedStrategy {
    bool allow_cache = true;
    std::vector<std::vector<double>> values;
    void prepare_script_run(const Bar* bars, int n, bool allow) override {
        GeneratedStrategy::prepare_script_run(bars, n, allow && allow_cache);
        if (visited != 0 || completed != 0)
            throw std::runtime_error("preparation executed the script");
        if (sourceValue.size() != 0 || get_input_source("Source", sourceValue).size() != 0)
            throw std::runtime_error("precalculation leaked input source history");
        values.clear();
    }
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        values.push_back({sourceValue[0], sourceLag, beforeValue, OBSERVED_VALUES});
    }
};
void check_first_call(RecordingStrategy& strategy, const Bar& bar, int length) {
    bool stopped = false;
    try {
        strategy.dispatch_bar(bar);
    } catch (const std::exception& error) {
        const auto* failure = dynamic_cast<const pineforge::RunFailureInfo*>(&error);
        if (!failure || failure->run_failure_code() !=
                pineforge::RunFailureCode::pine_invalid_argument)
            throw;
        stopped = true;
    }
    if (stopped != (length <= 0))
        throw std::runtime_error("wrong generated length validation");
    if (strategy.visited != 1 || strategy.completed != (stopped ? 0 : 1))
        throw std::runtime_error("length stop moved before prior script statements");
    if (!is_na(strategy.sourceLag))
        throw std::runtime_error("first real bar saw replayed source history");
}
int main() {
    constexpr int count = 64;
    Bar bars[count];
    for (int i = 0; i < count; ++i) {
        const double value = 100.0 + i + (i % 5) * 0.25;
        bars[i] = {value, value + 2, value - 1, value + 0.5, 1.0,
                   1743465600000LL + i * 60000LL};
    }
    Bar missing_source = bars[0];
    missing_source.high = na<double>();
    missing_source.close = na<double>();
    int checked = 0;
    for (const char* key : {LENGTH_KEYS}) {
        for (int length : {0, -1, 7}) {
            for (bool precalculate : {false, true}) {
                RecordingStrategy strategy;
                strategy.set_input(key, std::to_string(length));
                strategy.set_input("Source", "high");
                strategy.prepare_script_run(&missing_source, 1, precalculate);
                if (strategy._use_precalc != (precalculate && length > 0))
                    throw std::runtime_error("wrong precalculation publication");
                if (length <= 0 && !(EMPTY_CACHES))
                    throw std::runtime_error("abandoned replay retained a partial cache");
                check_first_call(strategy, missing_source, length);
                if (precalculate && length <= 0) {
                    RecordingStrategy control;
                    control.set_input(key, std::to_string(length));
                    control.set_input("Source", "high");
                    control.prepare_script_run(&missing_source, 1, false);
                    check_first_call(control, missing_source, length);
                    equal_value(strategy.beforeValue, control.beforeValue);
                    equal_value(strategy.sourceValue[0], control.sourceValue[0]);
                }
                ++checked;

                // Reuse the same object after abandonment and success, with
                // changed valid lengths and selected input.source series.
                for (int valid : {3, 11}) {
                    const char* selected = valid == 3 ? "close" : "open";
                    strategy.set_input(key, std::to_string(valid));
                    strategy.set_input("Source", selected);
                    RecordingStrategy live;
                    live.allow_cache = false;
                    live.set_input(key, std::to_string(valid));
                    live.set_input("Source", selected);
                    strategy.run(bars, count);
                    live.run(bars, count);
                    if (!strategy._use_precalc || live._use_precalc)
                        throw std::runtime_error("valid rerun did not use the requested route");
                    if (strategy.values.size() != count || live.values.size() != count)
                        throw std::runtime_error("valid rerun omitted bars");
                    int published = 0;
                    for (int i = 0; i < count; ++i) {
                        const auto& actual = strategy.values[i];
                        const auto& expected = live.values[i];
                        for (std::size_t j = 0; j < actual.size(); ++j)
                            equal_value(actual[j], expected[j]);
                        equal_value(actual[0],
                                    valid == 3 ? bars[i].close : bars[i].open);
                        if (std::isfinite(actual.back())) ++published;
                    }
                    if (strategy.visited != count || strategy.completed != count ||
                        published == 0)
                        throw std::runtime_error("valid rerun did not complete and publish");
                    ++checked;
                }
            }
        }
    }
    std::printf("%d\n", checked);
}
"""
    keys = ["Length"]
    if family in ("tsi", "macd"):
        keys.append("Second length")
    if family == "macd":
        keys.append("Signal length")
    driver = driver.replace("LENGTH_KEYS", ", ".join(f'"{key}"' for key in keys))
    driver = driver.replace(
        "EMPTY_CACHES", " && ".join(f"strategy.{name}.empty()" for name in caches)
    )
    driver = driver.replace("OBSERVED_VALUES", ", ".join(observed))
    out = run_emitted_tu(
        cpp, driver, opt="-O0", label=f"checked_na_{family}_{named}"
    )
    assert int(out.strip()) == len(keys) * 3 * 2 * 3


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
int main() {
    Bar bars[] = {{100.0, 102.0, 99.0, 101.0, 1.0, 1743465600000LL}};
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
        strategy.dispatch_bar(bars[0]);
        if (strategy.visited != 1)
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
