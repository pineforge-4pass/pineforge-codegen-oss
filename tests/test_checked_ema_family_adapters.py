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
def test_precalculation_checks_each_overridden_length_before_an_na_source(family, named):
    call = (_NAMED if named else _POSITIONAL)[family].format(
        sample="close", span="lengthInput", second="secondInput", signal="signalInput"
    )
    source = (
        '//@version=6\nstrategy("checked precalc")\n'
        'lengthInput = input.int(13, "Length")\n'
        'secondInput = input.int(25, "Second length")\n'
        'signalInput = input.int(9, "Signal length")\n'
        + _assignment(family, "observed", call)
        + "plot(observed)\n"
    )
    cpp = transpile(source)
    expected = _TYPES[family]
    assert _HEADER in cpp
    assert "void precalculate(const Bar* bars, int n)" in cpp
    # Both resets around precalculation and the live input reset construct the
    # same checked type. The first source below is NA in either route.
    assert re.search(rf"= {re.escape(expected)}\(.*get_input_int", cpp)
    driver = r"""
#include <cstdio>
#include <stdexcept>
int main() {
    Bar bars[] = {{100.0, 102.0, 99.0, na<double>(), 1.0, 1743465600000LL}};
    int checked = 0;
    for (const char* key : {LENGTH_KEYS}) {
        for (int length : {0, -1, 7}) {
            for (bool precalculate : {false, true}) {
                GeneratedStrategy strategy;
                strategy.set_input(key, std::to_string(length));
                bool stopped = false;
                try {
                    strategy.prepare_script_run(bars, 1, precalculate);
                    strategy.dispatch_bar(bars[0]);
                } catch (const std::exception&) {
                    stopped = true;
                }
                if (stopped != (length <= 0))
                    throw std::runtime_error("wrong generated length validation");
                ++checked;
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
    out = run_emitted_tu(
        cpp, driver, opt="-O0", label=f"checked_na_{family}_{named}"
    )
    assert int(out.strip()) == len(keys) * len((0, -1, 7)) * len((False, True))


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
