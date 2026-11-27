"""A per-bar length is lowered to the function's engine series class where TradingView compiles it.

TradingView compiles a length the script computes on each bar for the window
functions of ``CASES`` (and for the stdev multiplier of ``ta.vwap``'s bands).
On the chart such a call is lowered to the function's engine class
``pineforge::source::Series*`` (``_TA_SERIES_LENGTH_CLASSES`` in
``pineforge_codegen/codegen/ta.py``). Every call passes what the fixed-length
class's ``compute`` takes, then the fixed class's other constructor arguments
the script spells, then the per-call value last. The classes are declared in
three engine headers, each included only by a script that uses one of its
classes and followed by the guard of that class's feature macro. A constant
or input length keeps its fixed-length class and constructor. A call written
inside a loop keeps its refusal: it would call one object once on every pass
of every bar (``LOOP_REFUSED``).

TradingView refuses a per-bar value for the length of ta.ema, ta.rma, ta.rsi,
ta.atr, ta.hma, ta.tsi, ta.macd, ta.dmi, ta.kc, ta.kcw and ta.rci, for
ta.supertrend's atrPeriod, for the ``mult`` of ta.bb and for the ``offset`` of
ta.linreg. Each of those stays (or, for the offset, becomes) a refusal here.

The value test runs the script natively on the eighteen invented bars of
``tests/_series_sma_oracle.py``. The length is 2, 3, 4, repeating. Each bar's
value is compared with the same function's constant-length call of that bar's
length, which the same script computes on the same bars with the fixed-length
class, and, where the window formula is elementary, with a recomputation
written here from the bars. Neither is a value recorded on TradingView: what
TradingView computes after a length changes is recorded for none of these
functions.
"""

from __future__ import annotations

import math
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests import _series_sma_oracle as oracle
from tests._compile import compile_cpp, run_emitted_tu

HEADER = '//@version=6\nstrategy("series length classes")\n'
# 2, 3, 4, repeating: bar b computes its call with the length b % 3 + 2.
LENGTH = "n = bar_index % 3 + 2\n"
LENGTHS = (2, 3, 4)
ENGINE = "pineforge::source::Series"
LENGTH_HEADER = "#include <pineforge/source/pine_ta_length.hpp>\n"
PLAIN_CODE = "PF-E5009"

CLOSES = oracle.CLOSES
OPENS = oracle.OPENS
# The driver of tests/_series_sma_oracle.py builds each bar's high and low
# from its open and close.
HIGHS = tuple(max(o, c) for o, c in zip(OPENS, CLOSES))
LOWS = tuple(min(o, c) for o, c in zip(OPENS, CLOSES))

# For each function: the engine class after "Series"; the engine header that
# declares it (``pine_ta_series_<this>.hpp``); how many arguments the class's
# compute() takes for the call, the per-call value included (ta.vwap's
# omitted-anchor form spells its symbol clock through one macro, which the
# count takes as written); and the statements that leave the call's value in
# ``{out}`` when its per-call argument is ``{n}``.
CASES = {
    "wma": ("Wma", "ma", 2, "float {out} = ta.wma(close, {n})\n"),
    "vwma": ("Vwma", "ma", 3, "float {out} = ta.vwma(close, {n})\n"),
    "alma": ("Alma", "ma", 4, "float {out} = ta.alma(close, {n}, 0.85, 6)\n"),
    "alma_floor": (
        "Alma", "ma", 5, "float {out} = ta.alma(close, {n}, 0.85, 6, true)\n"),
    "linreg": ("Linreg", "ma", 3, "float {out} = ta.linreg(close, {n}, 0)\n"),
    "bb": (
        "Bb", "ma", 3,
        "[{out}_m, {out}_u, {out}_l] = ta.bb(close, {n}, 2.0)\n"
        "float {out} = {out}_m + 0.5 * {out}_u - 0.25 * {out}_l\n"),
    "bbw": ("Bbw", "ma", 3, "float {out} = ta.bbw(close, {n}, 2.0)\n"),
    "vwap_bands": (
        "VwapBands", "ma", 4,
        "[{out}_v, {out}_u, {out}_l] = ta.vwap(close, stdev_mult={n})\n"
        "float {out} = {out}_v + 0.5 * {out}_u - 0.25 * {out}_l\n"),
    "vwap_anchored_bands": (
        "VwapBands", "ma", 7,
        "[{out}_v, {out}_u, {out}_l] = ta.vwap(close, close > open, {n})\n"
        "float {out} = {out}_v + 0.5 * {out}_u - 0.25 * {out}_l\n"),
    "stdev": ("Stdev", "stats", 2, "float {out} = ta.stdev(close, {n})\n"),
    "stdev_unbiased": (
        "Stdev", "stats", 3, "float {out} = ta.stdev(close, {n}, false)\n"),
    "variance": ("Variance", "stats", 2, "float {out} = ta.variance(close, {n})\n"),
    "dev": ("Dev", "stats", 2, "float {out} = ta.dev(close, {n})\n"),
    "range": ("Range", "stats", 2, "float {out} = ta.range(close, {n})\n"),
    "median": ("Median", "stats", 2, "float {out} = ta.median(close, {n})\n"),
    "mode": ("Mode", "stats", 2, "float {out} = ta.mode(close, {n})\n"),
    "percentrank": (
        "Percentrank", "stats", 2, "float {out} = ta.percentrank(close, {n})\n"),
    "percentile_linear_interpolation": (
        "PercentileLinear", "stats", 3,
        "float {out} = ta.percentile_linear_interpolation(close, {n}, 50)\n"),
    "correlation": (
        "Correlation", "stats", 3,
        "float {out} = ta.correlation(close, open, {n})\n"),
    "sum": ("MathSum", "stats", 2, "float {out} = math.sum(close, {n})\n"),
    "stoch": ("Stoch", "osc", 4, "float {out} = ta.stoch(close, high, low, {n})\n"),
    "cci": ("Cci", "osc", 2, "float {out} = ta.cci(close, {n})\n"),
    "mfi": ("Mfi", "osc", 3, "float {out} = ta.mfi(close, {n})\n"),
    "mom": ("Mom", "osc", 2, "float {out} = ta.mom(close, {n})\n"),
    "roc": ("Roc", "osc", 2, "float {out} = ta.roc(close, {n})\n"),
    "cmo": ("Cmo", "osc", 2, "float {out} = ta.cmo(close, {n})\n"),
    "wpr": ("Wpr", "osc", 4, "float {out} = ta.wpr({n})\n"),
    "cog": ("Cog", "osc", 2, "float {out} = ta.cog(close, {n})\n"),
    "rising": (
        "Rising", "osc", 2, "float {out} = ta.rising(close, {n}) ? 1.0 : 0.0\n"),
    "falling": (
        "Falling", "osc", 2, "float {out} = ta.falling(close, {n}) ? 1.0 : 0.0\n"),
}

# The arguments a call spells from the bar itself, by position: the engine
# class takes them where the fixed-length class's compute() does.
BAR_ARGUMENTS = {
    "vwma": {1: "current_bar_.volume"},
    "mfi": {1: "current_bar_.volume"},
    "wpr": {0: "current_bar_.close", 1: "current_bar_.high", 2: "current_bar_.low"},
    # The session-day form: the bar's volume, then its timestamp and the
    # symbol's clock, which one macro of the translation unit appends.
    "vwap_bands": {
        1: "current_bar_.volume",
        2: ("current_bar_.timestamp PF_VWAP_SESSION_ANCHOR_ARGS("
            "syminfo_.timezone, syminfo_.session)"),
    },
    # The anchor form keeps the fixed shim's clock arguments, which the
    # engine class does not read, before Pine's anchor.
    "vwap_anchored_bands": {
        1: "current_bar_.volume", 2: "current_bar_.timestamp",
        3: "syminfo_.timezone", 4: "syminfo_.session",
    },
}


def _statements(case: str, out: str, argument: str) -> str:
    return CASES[case][3].replace("{out}", out).replace("{n}", argument)


def _series_source(case: str) -> str:
    """The function called once, with the per-bar length."""
    return HEADER + LENGTH + _statements(case, "out", "n") + "plot(out)\n"


def _value_source(case: str) -> str:
    """The per-bar call as ``out`` and, beside it, the same call with each
    constant length as ``r2``, ``r3`` and ``r4``."""
    return (
        HEADER + LENGTH + _statements(case, "out", "n")
        + "".join(_statements(case, f"r{length}", str(length)) for length in LENGTHS)
    )


def _members(cpp: str, cpp_type: str) -> list[str]:
    return re.findall(rf"^\s*{re.escape(cpp_type)}\s+(\w+);$", cpp, re.M)


def _call_arguments(cpp: str, member: str, method: str) -> list[str]:
    """The arguments of the first ``member.<method>(...)``, split at the
    commas of the call itself."""
    opening = f"{member}.{method}("
    start = cpp.index(opening) + len(opening)
    depth, begin, arguments = 0, start, []
    for position in range(start, len(cpp)):
        char = cpp[position]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                arguments.append(cpp[begin:position].strip())
                return arguments
            depth -= 1
        elif char == "," and depth == 0:
            arguments.append(cpp[begin:position].strip())
            begin = position + 1
    raise AssertionError(f"{opening} is not closed")


@pytest.mark.parametrize("case", list(CASES))
def test_a_per_bar_length_is_lowered_to_the_functions_series_class(case: str) -> None:
    suffix, header, arity, _statement = CASES[case]
    # On a generator without the lowering this transpile is refused (PF-E5009).
    cpp = transpile(_series_source(case))

    [member] = _members(cpp, ENGINE + suffix)
    arguments = _call_arguments(cpp, member, "compute")
    # The engine class's compute() for this call, the per-call value last.
    assert len(arguments) == arity, arguments
    assert re.fullmatch(
        r"pineforge::source::ta_number\(n(\[0\])?\)", arguments[-1]), arguments
    for position, expected in BAR_ARGUMENTS.get(case, {}).items():
        assert arguments[position] == expected, arguments
    # recompute() is called with the same arguments.
    assert _call_arguments(cpp, member, "recompute") == arguments

    # The header of the lowering itself, then the class's own header with the
    # guard of the class's feature macro right behind it.
    include = f"#include <pineforge/source/pine_ta_series_{header}.hpp>\n"
    macro = "PINEFORGE_HAS_PINE_SERIES_" + suffix.upper()
    assert cpp.count(LENGTH_HEADER) == 1 and cpp.count(include) == 1
    assert cpp.index(LENGTH_HEADER) < cpp.index(include)
    after = cpp[cpp.index(include) + len(include):].split("#include", 1)[0]
    assert after.startswith(f'#ifndef {macro}\n#error "'), after
    assert after.endswith(f'({macro})"\n#endif\n'), after
    # The script names no other series header.
    assert cpp.count("#include <pineforge/source/pine_ta_series_") == 1
    compile_cpp(cpp, label=f"series_length_{case}")


def test_one_guard_per_class_follows_each_header_once() -> None:
    # Two classes of one header and one of another: each header is included
    # once, followed by the guards of the classes the script uses, in the
    # order of the generator's table whatever order the script calls them in.
    cpp = transpile(
        HEADER + LENGTH
        + "float a = ta.cog(close, n)\n"
        + "float b = ta.wma(close, n)\n"
        + "float c = ta.mom(close, n)\n"
        + "float d = ta.wma(open, n)\n"
        + "plot(a + b + c + d)\n"
    )
    ma = "#include <pineforge/source/pine_ta_series_ma.hpp>\n"
    osc = "#include <pineforge/source/pine_ta_series_osc.hpp>\n"
    assert cpp.count(ma) == 1 and cpp.count(osc) == 1
    assert "pine_ta_series_stats" not in cpp
    assert cpp.index(LENGTH_HEADER) < cpp.index(ma) < cpp.index(osc)
    assert re.findall(r"^#ifndef (PINEFORGE_HAS_PINE_SERIES_\w+)$", cpp, re.M) == [
        "PINEFORGE_HAS_PINE_SERIES_WMA",
        "PINEFORGE_HAS_PINE_SERIES_MOM",
        "PINEFORGE_HAS_PINE_SERIES_COG",
    ]
    assert len(_members(cpp, ENGINE + "Wma")) == 2
    compile_cpp(cpp, label="series_length_three_classes")


@pytest.mark.parametrize("statement, fixed", [
    ("float out = ta.wma(close, 5)\n", "ta::WMA"),
    ('float out = ta.wma(close, input.int(5, "Length"))\n', "ta::WMA"),
    ("float out = ta.cci(close, 5)\n", "pineforge::source::PineCci"),
    ("[out, upper, lower] = ta.vwap(close, stdev_mult=2.0)\n", "ta::VWAPBands"),
])
def test_a_constant_or_input_length_names_no_series_header(
        statement: str, fixed: str) -> None:
    cpp = transpile(HEADER + statement + "plot(out)\n")
    assert _members(cpp, fixed)
    assert "pine_ta_series_" not in cpp
    assert "PINEFORGE_HAS_PINE_SERIES_" not in cpp
    assert ENGINE not in cpp


def _window(values, bar: int, length: int):
    """The last ``length`` values at ``bar``, oldest first, or None while the
    bar has fewer."""
    if bar + 1 < length:
        return None
    return values[bar + 1 - length:bar + 1]


def _mean(values) -> float:
    return sum(values) / len(values)


def _population_variance(values) -> float:
    mean = _mean(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def _mean_deviation(values) -> float:
    mean = _mean(values)
    return sum(abs(value - mean) for value in values) / len(values)


def _direct_wma(bar: int, length: int):
    window = _window(CLOSES, bar, length)
    if window is None:
        return None
    # The newest value weighs ``length``, the oldest 1.
    return (sum(value * (index + 1) for index, value in enumerate(window))
            / (length * (length + 1) / 2))


def _direct_linreg(bar: int, length: int):
    window = _window(CLOSES, bar, length)
    if window is None:
        return None
    xs = range(length)
    sum_x, sum_y = sum(xs), sum(window)
    sum_xy = sum(x * y for x, y in zip(xs, window))
    sum_xx = sum(x * x for x in xs)
    slope = (length * sum_xy - sum_x * sum_y) / (length * sum_xx - sum_x ** 2)
    intercept = (sum_y - slope * sum_x) / length
    # Offset 0: the line's value at the newest bar.
    return intercept + slope * (length - 1)


def _direct_correlation(bar: int, length: int):
    closes, opens = _window(CLOSES, bar, length), _window(OPENS, bar, length)
    if closes is None:
        return None
    mean_c, mean_o = _mean(closes), _mean(opens)
    covariance = sum((c - mean_c) * (o - mean_o) for c, o in zip(closes, opens))
    return covariance / math.sqrt(
        sum((c - mean_c) ** 2 for c in closes) * sum((o - mean_o) ** 2 for o in opens))


def _direct_stoch(bar: int, length: int):
    highs, lows = _window(HIGHS, bar, length), _window(LOWS, bar, length)
    if highs is None:
        return None
    return 100 * (CLOSES[bar] - min(lows)) / (max(highs) - min(lows))


def _direct_wpr(bar: int, length: int):
    highs, lows = _window(HIGHS, bar, length), _window(LOWS, bar, length)
    if highs is None:
        return None
    return 100 * (CLOSES[bar] - max(highs)) / (max(highs) - min(lows))


def _direct_cci(bar: int, length: int):
    window = _window(CLOSES, bar, length)
    if window is None:
        return None
    return (CLOSES[bar] - _mean(window)) / (0.015 * _mean_deviation(window))


def _direct_cog(bar: int, length: int):
    window = _window(CLOSES, bar, length)
    if window is None:
        return None
    # The newest value weighs 1, the oldest ``length``.
    weighted = sum(value * (index + 1) for index, value in enumerate(reversed(window)))
    return -weighted / sum(window)


def _over_window(function):
    def direct(bar: int, length: int):
        window = _window(CLOSES, bar, length)
        return None if window is None else function(window)
    return direct


def _against_bar_back(function):
    def direct(bar: int, length: int):
        return None if bar < length else function(CLOSES[bar], CLOSES[bar - length])
    return direct


# The functions whose window formula is elementary, recomputed from the bars:
# ``(bar, length)`` gives the value, or None where the window is incomplete
# (such a bar is left to the constant-length comparison).
DIRECT = {
    "sum": _over_window(sum),
    "range": _over_window(lambda window: max(window) - min(window)),
    "stdev": _over_window(lambda window: math.sqrt(_population_variance(window))),
    "variance": _over_window(_population_variance),
    "dev": _over_window(_mean_deviation),
    "mom": _against_bar_back(lambda now, then: now - then),
    "roc": _against_bar_back(lambda now, then: 100 * (now - then) / then),
    "wma": _direct_wma,
    "linreg": _direct_linreg,
    "correlation": _direct_correlation,
    "stoch": _direct_stoch,
    "wpr": _direct_wpr,
    "cci": _direct_cci,
    "cog": _direct_cog,
}


def _close_to(value: float, expected: float) -> bool:
    return abs(value - expected) <= 1e-9 * max(1.0, abs(expected))


@pytest.mark.parametrize("case", list(CASES))
def test_a_per_bar_length_answers_each_bars_own_window(case: str) -> None:
    members = ("out",) + tuple(f"r{length}" for length in LENGTHS)
    # On a generator without the lowering this transpile is refused (PF-E5009).
    cpp = transpile(_value_source(case))
    rows = oracle.per_bar_rows(
        run_emitted_tu(cpp, oracle.per_bar_driver(members), opt="-O0",
                       label=f"series_length_values_{case}"),
        len(members))

    compared = 0
    recomputed = 0
    for bar, row in enumerate(rows):
        length = bar % 3 + 2
        value, constant = row[0], row[1 + LENGTHS.index(length)]
        what = f"{case}: bar {bar}, length {length}"
        if math.isnan(constant):
            assert math.isnan(value), (
                f"{what}: {value!r}; the constant-length call answers na")
        else:
            compared += 1
            assert _close_to(value, constant), (
                f"{what}: {value!r}; the constant-length call answers {constant!r}")
        direct = DIRECT[case](bar, length) if case in DIRECT else None
        if direct is not None:
            recomputed += 1
            assert _close_to(value, direct), (
                f"{what}: {value!r}; the recomputation from the bars gives {direct!r}")
    # Neither comparison may pass on warm-up bars alone.
    assert compared >= 9, f"{case}: NOT COMPARED, only {compared} bars hold a value"
    if case in DIRECT:
        assert recomputed >= 9, f"{case}: NOT COMPARED, only {recomputed} bars recomputed"


def _refusal(source: str):
    with pytest.raises(CompileError) as caught:
        transpile(source)
    assert len(caught.value.diagnostics) == 1
    return caught.value.diagnostics[0]


# TradingView refuses a per-bar value for each of these arguments.
TRADINGVIEW_REFUSED = {
    "ema": ("float out = ta.ema(close, n)\n", "ta::EMA"),
    "rma": ("float out = ta.rma(close, n)\n", "ta::RMA"),
    "rsi": ("float out = ta.rsi(close, n)\n", "ta::RSI"),
    "atr": ("float out = ta.atr(n)\n", "ta::ATR"),
    "hma": ("float out = ta.hma(close, n)\n", "ta::HMA"),
    "tsi": ("float out = ta.tsi(close, n, 25)\n", "ta::TSI"),
    "macd": ("[out, signal, hist] = ta.macd(close, n, 26, 9)\n", "ta::MACD"),
    "dmi": ("[plus, minus, out] = ta.dmi(n, 14)\n", "ta::DMI"),
    "kc": ("[out, upper, lower] = ta.kc(close, n, 1.5)\n", "_PFKC"),
    "kcw": ("float out = ta.kcw(close, n, 1.5)\n", "_PFKCW"),
    "rci": ("float out = ta.rci(close, n)\n", "ta::RCI"),
    "supertrend": ("[out, direction] = ta.supertrend(3.0, n)\n", "ta::Supertrend"),
    "bb_mult": ("[out, upper, lower] = ta.bb(close, 5, n)\n", "ta::BB"),
    "bb_length_and_mult": ("[out, upper, lower] = ta.bb(close, n, n)\n", "ta::BB"),
}


@pytest.mark.parametrize("case", list(TRADINGVIEW_REFUSED))
def test_a_per_bar_value_tradingview_refuses_stays_refused(case: str) -> None:
    statement, class_name = TRADINGVIEW_REFUSED[case]
    diagnostic = _refusal(HEADER + LENGTH + statement + "plot(out)\n")
    assert diagnostic.code == PLAIN_CODE
    assert diagnostic.message.startswith(
        f"Unsupported TA constructor length 'n' for {class_name}: ")
    assert ENGINE not in diagnostic.message


# A per-bar length of a function with a series class, where the lowering does
# not take it: a request.security copy, a length the callable receives from
# its call site, and a script-level persistent declaration that is not shown
# to vary by bar.
STILL_REFUSED = {
    "requested": (
        LENGTH
        + 'float out = request.security(syminfo.tickerid, "60", ta.wma(close, n))\n'),
    "forwarded": (
        LENGTH
        + "weighted(int len) => ta.wma(close, len)\n"
        + "float out = weighted(n)\n"),
    "persistent": (
        "varip int p = 5\n"
        + "float out = ta.wma(close, p)\n"),
}


@pytest.mark.parametrize("case", list(STILL_REFUSED))
def test_a_per_bar_length_outside_the_lowering_stays_refused(case: str) -> None:
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + STILL_REFUSED[case] + "plot(out)\n")
    assert caught.value.diagnostics
    for diagnostic in caught.value.diagnostics:
        # Each refusal names the fixed-length class it cannot size.
        assert "ta::WMA" in diagnostic.message, diagnostic.message
        assert ENGINE not in diagnostic.message


# A call written inside a loop runs once on every pass, so the three passes
# of ``for i = 2 to 4`` call one object three times on a bar, with the lengths
# 2, 3 and 4. An engine series class records one sample on every call: its
# window then counts calls, not bars, and holds the bar's close once for each
# pass. With the closes 10, 12, 11, 15 of tests/_series_sma_oracle.py, the
# loop over ta.wma would total 34.8 on bar 1 and 42.6 on bar 3, where
# ta.wma(close, 2) + ta.wma(close, 3) + ta.wma(close, 4) is na on bar 1 (the
# length 3 has two bars) and 41/3 + 79/6 + 12.7 on bar 3. No value of
# TradingView is recorded for such a call of these functions, so it keeps the
# refusal it had before the series classes were lowered. One function of each
# engine header first, with the whole diagnostic; then every row of ``CASES``.
LOOP = "for i = 2 to 4\n"
LOOP_REFUSED = {
    "wma": ("ta.wma(close, i)", "ta::WMA"),
    "stdev": ("ta.stdev(close, i)", "ta::StdDev"),
    "cci": ("ta.cci(close, i)", "ta::CCI"),
}


@pytest.mark.parametrize("case", list(LOOP_REFUSED))
def test_a_loop_counter_length_keeps_its_refusal(case: str) -> None:
    call, class_name = LOOP_REFUSED[case]
    source = (
        HEADER
        + "float total = 0.0\n"
        + LOOP
        + f"    total += {call}\n"
        + "plot(total)\n"
    )
    # On a generator that lowers the call this transpile succeeds.
    diagnostic = _refusal(source)
    assert diagnostic.code == PLAIN_CODE
    assert diagnostic.message.startswith(
        f"Unsupported TA constructor length 'i' for {class_name}: ")
    assert ENGINE not in diagnostic.message
    # The header takes two lines, ``total`` and the loop one each.
    assert diagnostic.location.line == 5


@pytest.mark.parametrize("case", list(CASES))
def test_a_call_inside_a_loop_is_not_lowered_to_a_series_class(case: str) -> None:
    # The statements of the case in the loop body, their per-call argument
    # the loop counter, and the value each pass leaves added up.
    body = "".join(
        "    " + line + "\n"
        for line in _statements(case, "out", "i").splitlines()
    )
    source = (
        HEADER
        + "float total = 0.0\n"
        + LOOP
        + body
        + "    total += out\n"
        + "plot(total)\n"
    )
    # On a generator that lowers the call this transpile succeeds.
    with pytest.raises(CompileError) as caught:
        transpile(source)
    assert caught.value.diagnostics
    for diagnostic in caught.value.diagnostics:
        assert ENGINE not in diagnostic.message


def test_a_per_bar_linreg_offset_is_refused() -> None:
    # TradingView declares ``offset`` simple. The call compiled here, with the
    # offset read on every bar, before this refusal.
    source = (
        HEADER
        + "k = bar_index % 3\n"
        + "float out = ta.linreg(close, 5, k)\n"
        + "plot(out)\n"
    )
    diagnostic = _refusal(source)
    assert diagnostic.code == PLAIN_CODE
    assert diagnostic.message.startswith(
        "Unsupported TA constructor length 'k' for ta::Linreg: ")
    assert diagnostic.location.line == 4


def test_a_per_bar_linreg_offset_is_refused_beside_a_per_bar_length() -> None:
    # The offset is what TradingView refuses: the refusal names it, not the
    # length, which the series class would take.
    source = (
        HEADER + LENGTH
        + "k = bar_index % 3\n"
        + "float out = ta.linreg(close, n, k)\n"
        + "plot(out)\n"
    )
    diagnostic = _refusal(source)
    assert diagnostic.code == PLAIN_CODE
    assert diagnostic.message.startswith(
        "Unsupported TA constructor length 'k' for ta::Linreg: ")
    assert diagnostic.location.line == 5


def test_a_per_bar_linreg_offset_is_refused_in_a_request_security_expression() -> None:
    # The call written as the request's own expression: its copy is planned
    # from the call, and the plan asks the offset before anything else. A
    # call reached through a helper that only the request evaluates is not
    # covered by this row.
    source = (
        HEADER
        + 'float out = request.security(syminfo.tickerid, "60", '
        + "ta.linreg(close, 5, bar_index % 3))\n"
        + "plot(out)\n"
    )
    diagnostic = _refusal(source)
    assert diagnostic.code == PLAIN_CODE
    assert diagnostic.message.startswith("Unsupported TA constructor length '")
    assert " for ta::Linreg: " in diagnostic.message
    assert diagnostic.location.line == 3


@pytest.mark.parametrize("offset", ["0", "2", 'input.int(1, "Offset")'])
def test_a_simple_linreg_offset_keeps_its_fixed_class(offset: str) -> None:
    source = HEADER + f"float out = ta.linreg(close, 5, {offset})\nplot(out)\n"
    cpp = transpile(source)
    assert _members(cpp, "ta::Linreg")
    assert not _members(cpp, ENGINE + "Linreg")
