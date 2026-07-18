"""Finite-choice series lengths for ``ta.highest`` / ``ta.lowest``.

The engine extrema ABI owns one fixed-size history per object.  A Pine length
that selects between two input-qualified values is therefore lowered to two
fixed histories advanced on every bar, followed by a value selection.  These
tests pin that bounded lowering and keep arbitrary series-length support out of
scope.
"""

from __future__ import annotations

import math
import re

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests.test_float_relational_runtime import _compile_and_run


def _member_periods(cpp: str, family: str) -> list[int]:
    names = re.findall(rf"ta::{family}\s+(_ta_\w+_\d+)\s*;", cpp)
    periods: list[int] = []
    for name in names:
        match = re.search(rf"\b{re.escape(name)}\((\d+)\)", cpp)
        assert match, (name, cpp)
        periods.append(int(match.group(1)))
    return periods


def test_wellman_shape_expands_both_extrema_histories() -> None:
    src = """//@version=6
strategy("wellman finite length reduction")
normalSwingLookback = input.int(10, "Normal", minval=1)
highVolSwingLookback = input.int(7, "High Vol", minval=1)
highVolThreshold = input.float(1.2, "Threshold")
regimeFastAtrLen = input.int(14, "Fast Regime ATR", minval=3)
regimeSlowAtrLen = input.int(200, "Slow Regime ATR", minval=50)
fastRegimeAtr = ta.atr(regimeFastAtrLen)
slowRegimeAtr = ta.atr(regimeSlowAtrLen)
volatilityRatio = slowRegimeAtr != 0 ? fastRegimeAtr / slowRegimeAtr : 1.0
activeSwingLookback = volatilityRatio >= highVolThreshold ? highVolSwingLookback : normalSwingLookback
globalLowestLow = ta.lowest(low, activeSwingLookback)
globalHighestHigh = ta.highest(high, activeSwingLookback)
plot(globalLowestLow + globalHighestHigh)
"""

    cpp = transpile(src)

    assert sorted(_member_periods(cpp, "Lowest")) == [7, 10]
    assert sorted(_member_periods(cpp, "Highest")) == [7, 10]
    assert cpp.count("pf_ta_length_choice_1_true") > 1
    assert cpp.count("pf_ta_length_choice_1_false") > 1
    assert cpp.count("pf_ta_length_choice_1_selected") > 1
    assert cpp.count("pf_ta_length_choice_2_true") > 1
    assert cpp.count("pf_ta_length_choice_2_false") > 1
    assert cpp.count("pf_ta_length_choice_2_selected") > 1

    # Snapshot the authored selected length once, then advance both fixed
    # histories before selecting the matching result.
    low_snapshot = cpp.index("pf_ta_length_choice_1_selected =")
    low_true = cpp.index("pf_ta_length_choice_1_true =")
    low_false = cpp.index("pf_ta_length_choice_1_false =")
    low_select = cpp.index("globalLowestLow =", low_false)
    assert low_snapshot < low_true < low_false < low_select
    high_snapshot = cpp.index("pf_ta_length_choice_2_selected =")
    high_true = cpp.index("pf_ta_length_choice_2_true =")
    high_false = cpp.index("pf_ta_length_choice_2_false =")
    high_select = cpp.index("globalHighestHigh =", high_false)
    assert high_snapshot < high_true < high_false < high_select


@pytest.mark.parametrize("family,source", [("lowest", "low"), ("highest", "high")])
@pytest.mark.parametrize("aliased", [False, True])
def test_inline_and_aliased_choice_forms_compile(
    family: str, source: str, aliased: bool
) -> None:
    choice = "selected" if aliased else "(bar_index % 2 == 0 ? fastLen : slowLen)"
    alias = (
        "selected = bar_index % 2 == 0 ? fastLen : slowLen\n"
        if aliased else ""
    )
    src = f"""//@version=6
strategy("finite choice form")
fastLen = input.int(2, "Fast", minval=1)
slowLen = input.int(4, "Slow", minval=1)
{alias}value = ta.{family}({source}, {choice})
plot(value)
"""

    cpp = transpile(src)
    cxx_family = "Lowest" if family == "lowest" else "Highest"
    assert sorted(_member_periods(cpp, cxx_family)) == [2, 4]
    compile_cpp(cpp, label=f"finite_choice_{family}_{'alias' if aliased else 'inline'}")


def test_one_argument_default_source_form_expands() -> None:
    src = """//@version=6
strategy("finite choice default source")
a = input.int(2, "A", minval=1)
b = input.int(5, "B", minval=1)
n = close > open ? a : b
lo = ta.lowest(n)
hi = ta.highest(n)
plot(lo + hi)
"""

    cpp = transpile(src)
    assert sorted(_member_periods(cpp, "Lowest")) == [2, 5]
    assert sorted(_member_periods(cpp, "Highest")) == [2, 5]
    assert ".compute(current_bar_.low)" in cpp
    assert ".compute(current_bar_.high)" in cpp


def test_stable_input_selector_keeps_existing_single_object_route() -> None:
    src = """//@version=6
strategy("stable finite choice")
useShort = input.bool(true, "Use short")
shortLen = input.int(2, "Short", minval=1)
longLen = input.int(4, "Long", minval=1)
n = useShort ? shortLen : longLen
x = ta.lowest(low, n)
plot(x)
"""

    cpp = transpile(src)
    assert len(_member_periods(cpp, "Lowest")) == 1
    assert "pf_ta_length_choice" not in cpp


def test_input_manifest_contains_only_authored_inputs() -> None:
    src = """//@version=6
strategy("finite choice manifest")
shortLen = input.int(2, "Short", minval=1)
longLen = input.int(4, "Long", minval=1)
n = close > open ? shortLen : longLen
x = ta.lowest(low, n)
plot(x)
"""

    full = transpile_full(src)
    assert [item["title"] for item in full["inputs"]] == ["Short", "Long"]
    assert "pf_ta_length_choice" not in repr(full["inputs"])


def test_generated_names_avoid_authored_collision_and_are_deterministic() -> None:
    src = """//@version=6
strategy("finite choice collision")
pf_ta_length_choice_1_true = 123.0
pf_ta_length_choice_1_false = 456.0
a = input.int(2, "A", minval=1)
b = input.int(4, "B", minval=1)
n = close > open ? a : b
x = ta.lowest(low, n)
plot(x + pf_ta_length_choice_1_true + pf_ta_length_choice_1_false)
"""

    cpp = transpile(src)
    assert cpp == transpile(src)
    assert "pf_ta_length_choice_2_true" in cpp
    assert "pf_ta_length_choice_2_false" in cpp


def test_generated_names_avoid_callable_and_snapshot_collisions() -> None:
    src = """//@version=6
strategy("finite choice callable collision")
pf_ta_length_choice_1_selected() => 1.0
pf_ta_length_choice_1_true() => 2.0
pf_ta_length_choice_1_false() => 3.0
a = input.int(2, "A", minval=1)
b = input.int(4, "B", minval=1)
n = close > open ? a : b
x = ta.lowest(low, n)
plot(x + pf_ta_length_choice_1_selected() + pf_ta_length_choice_1_true() + pf_ta_length_choice_1_false())
"""

    cpp = transpile(src)
    assert "pf_ta_length_choice_2_selected" in cpp
    assert "pf_ta_length_choice_2_true" in cpp
    assert "pf_ta_length_choice_2_false" in cpp
    compile_cpp(cpp, label="finite_choice_callable_collision")


@pytest.mark.parametrize(
    "source",
    [
        # The selected-length alias itself is changed before the call.
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nn := bar_index % 3 + 1\nx = ta.lowest(low, n)",
        # Copying the selector at the call would observe a different decision
        # than the one captured when n was authored.
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nflip = close > open\nn = flip ? a : b\nflip := not flip\nx = ta.lowest(low, n)",
        # A later write matters on the next bar, so the mutation census spans
        # the whole program rather than stopping at the call site.
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nx = ta.lowest(low, n)\na := 8",
        # Persistent aliases have declaration-time semantics outside the
        # proven ordinary per-bar alias subset.
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nvar n = close > open ? a : b\nx = ta.lowest(low, n)",
        # Inline input declarations would be duplicated by the fixed-history
        # calls, so require authored top-level aliases for input leaves.
        "x = ta.lowest(low, close > open ? input.int(2, \"A\") : input.int(4, \"B\"))",
        # Choice leaves must already exist where the aliased ternary is
        # declared, not merely by the later TA call.
        "n = close > open ? a : b\na = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nx = ta.lowest(low, n)",
        # The same source-order rule applies to the selector itself.
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = flip ? a : b\nflip = close > open\nx = ta.lowest(low, n)",
        # And to transitive aliases inside a selector even when another side
        # of the expression visibly depends on a bar series.
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nflip = close > threshold\nthreshold = input.float(1.0, \"Threshold\")\nn = flip ? a : b\nx = ta.lowest(low, n)",
        # Duplicate choice/arm names are authored-invalid and must retain the
        # analyzer's loud failure instead of being given last-wins semantics.
        "a = input.int(2, \"A1\", minval=1)\na = input.int(3, \"A2\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nn = close < open ? b : a\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nflip = close > open\nflip = high > low\nn = flip ? a : b\nx = ta.lowest(low, n)",
        # A write nested in a callable is conservatively part of the mutation
        # census; otherwise lowering can mask an authored-invalid global write.
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nmutate() =>\n    a := 3\n    a\nn = close > open ? a : b\nx = ta.lowest(low, n)",
        # Only one direct input alias is admitted per branch; extra alias
        # chains stay outside the Wellman-shaped subset.
        "base = input.int(2, \"Base\", minval=1)\na = base\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nx = ta.lowest(low, n)",
        # Constructor lengths must be provably positive for every input value.
        "a = input.int(2, \"A\")\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=0)\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", options=[0, 2])\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nx = ta.lowest(low, n)",
        "n = close > open ? 0 : 4\nx = ta.lowest(low, n)",
        "n = close > open ? -1 : 4\nx = ta.lowest(low, n)",
        # A non-VarDecl authored binder is not an external/builtin name and
        # cannot be forward-referenced through the bounded dependency model.
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = flag and close > open ? a : b\n[flag, other] = [true, false]\nx = ta.lowest(low, n)",
        # Explicit non-int annotations cannot be erased by synthetic lowering.
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nfloat n = close > open ? a : b\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nbool n = close > open ? a : b\nx = ta.lowest(low, n)",
        "float a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nx = ta.lowest(low, n)",
        # Pine v6 has no numeric-to-bool coercion, and this bounded route only
        # admits the direct comparison condition used by Wellman.
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = close ? a : b\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nflip = close > open\nn = flip ? a : b\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = ta.crossover(close, open) ? a : b\nx = ta.lowest(low, n)",
        # A comparison token alone is insufficient: both operands must belong
        # to the bounded numeric expression subset.
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nflag = close > open\nn = flag >= 1 ? a : b\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = barstate.isconfirmed >= 1 ? a : b\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nflag = input.bool(true, \"Flag\")\nn = flag >= close ? a : b\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nmode = input.string(\"x\", \"Mode\")\nn = mode >= close ? a : b\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nunknown() => close\nn = unknown() >= 1 ? a : b\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nfast = input.int(14, \"Fast\", minval=1)\nslow = input.int(20, \"Slow\", minval=1)\nint ratio = ta.atr(fast) / ta.atr(slow)\nn = ratio >= 1.0 ? a : b\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nint threshold = input.float(1.2, \"Threshold\")\nn = close >= threshold ? a : b\nx = ta.lowest(low, n)",
        # Any authored binder shadowing a native bar-series spelling disables
        # this bounded pass, even when the shadow is otherwise unrelated or is
        # the extrema result itself.
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\ntime = 1\nn = close > open ? a : b\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = high > open ? a : b\nclose = ta.lowest(low, n)\nx = close",
        # A direct top-level TupleAssign binder colliding with the extrema
        # result is another duplicate target, regardless of source order.
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\n[x, z] = [1.0, 2.0]\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nx = ta.lowest(low, n)\n[x, z] = [1.0, 2.0]",
        # Extrema are float-valued; incompatible result annotations must retain
        # the original constructor/type failure rather than being converted by
        # a synthetic ternary assignment.
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nint x = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nbool x = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nstring x = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nx = 0.0\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nx = ta.lowest(low, n)\nx = 0.0",
    ],
)
def test_unsafe_or_ambiguous_finite_choices_remain_rejected(
    source: str,
) -> None:
    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        transpile(f'//@version=6\nstrategy("mutation guard")\n{source}\nplot(x)\n')


def test_aliased_length_is_snapshotted_at_call_site() -> None:
    src = """//@version=6
strategy("finite choice authored snapshot")
a = input.int(2, "A", minval=1)
b = input.int(4, "B", minval=1)
n = close > open ? a : b
x = ta.lowest(low, n)
plot(x)
"""

    cpp = transpile(src)
    assert "pf_ta_length_choice_1_selected = n;" in cpp
    compile_cpp(cpp, label="finite_choice_authored_snapshot")


@pytest.mark.parametrize("shadowed", ["close", "time"])
def test_shadowed_numeric_builtin_does_not_take_finite_choice_route(
    shadowed: str,
) -> None:
    # The existing stable-length path happens to accept this authored-invalid
    # comparator.  This pass must decline it; fixing general annotation/type
    # enforcement is deliberately outside the Wellman lowering.
    src = f"""//@version=6
strategy("finite choice builtin shadow")
a = input.int(2, "A", minval=1)
b = input.int(4, "B", minval=1)
bool {shadowed} = input.bool(true, "Shadow")
n = {shadowed} >= 1 ? a : b
x = ta.lowest(low, n)
plot(x)
"""

    cpp = transpile(src)
    assert "pf_ta_length_choice" not in cpp


@pytest.mark.parametrize("qualifier", ["var", "varip"])
def test_persistent_extrema_target_does_not_take_finite_choice_route(
    qualifier: str,
) -> None:
    src = f"""//@version=6
strategy("finite choice persistent result")
a = input.int(2, "A", minval=1)
b = input.int(4, "B", minval=1)
n = close > open ? a : b
{qualifier} float x = ta.lowest(low, n)
plot(x)
"""

    # check_support=False reaches this lowering for varip; the ordinary public
    # path rejects varip even earlier because batch mode has no intrabar ticks.
    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        transpile(src, check_support=False)


@pytest.mark.parametrize("shadowed", ["ta", "input"])
def test_shadowed_builtin_namespace_remains_rejected(
    shadowed: str,
) -> None:
    src = f"""//@version=6
strategy("finite choice namespace shadow")
a = input.int(2, "A", minval=1)
b = input.int(4, "B", minval=1)
{shadowed} = close
n = close > open ? a : b
x = ta.lowest(low, n)
plot(x)
"""

    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        transpile(src)


def test_positive_options_domains_are_admitted() -> None:
    src = """//@version=6
strategy("finite choice positive options")
a = input.int(2, "A", options=[1, 2])
b = input.int(4, "B", options=[3, 4])
n = close > open ? a : b
x = ta.lowest(low, n)
plot(x)
"""

    cpp = transpile(src)
    assert sorted(_member_periods(cpp, "Lowest")) == [2, 4]
    compile_cpp(cpp, label="finite_choice_positive_options")


def test_explicit_int_choice_and_arm_aliases_are_admitted() -> None:
    src = """//@version=6
strategy("finite choice explicit int")
int a = input.int(2, "A", minval=1)
int b = input.int(4, "B", minval=1)
int n = close > open ? a : b
x = ta.lowest(low, n)
plot(x)
"""

    cpp = transpile(src)
    assert sorted(_member_periods(cpp, "Lowest")) == [2, 4]
    compile_cpp(cpp, label="finite_choice_explicit_int")


def test_explicit_float_extrema_result_is_admitted() -> None:
    src = """//@version=6
strategy("finite choice explicit float result")
int a = input.int(2, "A", minval=1)
int b = input.int(4, "B", minval=1)
int n = close > open ? a : b
float x = ta.lowest(low, n)
plot(x)
"""

    cpp = transpile(src)
    assert sorted(_member_periods(cpp, "Lowest")) == [2, 4]
    compile_cpp(cpp, label="finite_choice_explicit_float_result")


@pytest.mark.parametrize(
    "source",
    [
        # Arbitrary, unbounded series length.
        "n = ta.barssince(close > open)\nx = ta.lowest(low, n)",
        # One choice is itself series-valued rather than a fixed leaf.
        "a = input.int(2, \"A\")\nn = close > open ? a : int(ta.atr(3))\nx = ta.lowest(low, n)",
        # Other TA families retain their simple/fixed-length contract.
        "a = input.int(2, \"A\")\nb = input.int(4, \"B\")\nn = close > open ? a : b\nx = ta.ema(close, n)",
        # Non-identifier source expressions stay outside the bounded route.
        "a = input.int(2, \"A\")\nb = input.int(4, \"B\")\nn = close > open ? a : b\nx = ta.lowest(low + 0.0, n)",
        # The two-argument route is deliberately native-source exact:
        # lowest(low, n) and highest(high, n), with no aliases or shadows.
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nx = ta.lowest(close, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nn = close > open ? a : b\nx = ta.highest(open, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nsrc = low\nn = close > open ? a : b\nx = ta.lowest(src, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\nlow = close\nn = close > open ? a : b\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\n[low, other] = [close, open]\nn = close > open ? a : b\nx = ta.lowest(low, n)",
        "a = input.int(2, \"A\", minval=1)\nb = input.int(4, \"B\", minval=1)\n[high, other] = [close, open]\nn = close > open ? a : b\nx = ta.highest(high, n)",
    ],
)
def test_unbounded_or_out_of_scope_lengths_remain_rejected(source: str) -> None:
    with pytest.raises(CompileError, match="Unsupported TA constructor length"):
        transpile(f'//@version=6\nstrategy("negative")\n{source}\nplot(x)\n')


_RUNTIME_PINE = """//@version=6
strategy("finite choice runtime")
fastLen = input.int(2, "Fast", minval=1)
slowLen = input.int(4, "Slow", minval=1)
selected = bar_index % 2 == 0 ? fastLen : slowLen
lo = ta.lowest(low, selected)
hi = ta.highest(high, selected)

var float lo2 = na
var float hi2 = na
var float lo3 = na
var float hi3 = na
var float lo4 = na
var float hi4 = na
var float lo5 = na
var float hi5 = na
if bar_index == 2
    lo2 := lo
    hi2 := hi
if bar_index == 3
    lo3 := lo
    hi3 := hi
if bar_index == 4
    lo4 := lo
    hi4 := hi
if bar_index == 5
    lo5 := lo
    hi5 := hi
"""


_RUNTIME_DRIVER = r"""
#include <iomanip>
#include <iostream>

int main() {
    const double highs[] = {10, 12, 11, 15, 13, 14};
    const double lows[] = {5, 4, 6, 3, 7, 2};
    Bar bars[6];
    for (int i = 0; i < 6; ++i) {
        const double mid = (highs[i] + lows[i]) / 2.0;
        bars[i] = Bar{mid, highs[i], lows[i], mid, 1.0,
                      static_cast<int64_t>(i) * 900000};
    }

    GeneratedStrategy strategy;
    strategy.run(bars, 6);
    std::cout << std::setprecision(17)
              << strategy.lo2 << '\t' << strategy.hi2 << '\t'
              << strategy.lo3 << '\t' << strategy.hi3 << '\t'
              << strategy.lo4 << '\t' << strategy.hi4 << '\t'
              << strategy.lo5 << '\t' << strategy.hi5 << '\n';
    return 0;
}
"""


def test_runtime_switches_between_fully_advanced_fixed_histories() -> None:
    cpp = transpile(_RUNTIME_PINE)
    stdout = _compile_and_run(cpp + _RUNTIME_DRIVER)
    observed = tuple(float(value) for value in stdout.strip().split("\t"))
    assert observed == (4.0, 12.0, 3.0, 15.0, 3.0, 15.0, 2.0, 15.0)
    assert all(math.isfinite(value) for value in observed)


_LONG_INACTIVE_PINE = """//@version=6
strategy("finite choice long inactive runtime")
shortLen = input.int(2, "Short", minval=1)
longLen = input.int(5, "Long", minval=1)
selected = bar_index < 6 ? shortLen : longLen
lo = ta.lowest(low, selected)
reverseSelected = bar_index < 6 ? longLen : shortLen
reverseLo = ta.lowest(low, reverseSelected)
equalSelected = bar_index % 2 == 0 ? 3 : 3
equalLo = ta.lowest(low, equalSelected)

var float early0 = na
var float early1 = na
var float beforeFlip = na
var float afterFlip = na
var float reverseAfterFlip = na
var float equalAfterFlip = na
if bar_index == 0
    early0 := lo
if bar_index == 1
    early1 := lo
if bar_index == 5
    beforeFlip := lo
if bar_index == 6
    afterFlip := lo
    reverseAfterFlip := reverseLo
    equalAfterFlip := equalLo
"""


_LONG_INACTIVE_DRIVER = r"""
#include <iomanip>
#include <iostream>

int main() {
    const double lows[] = {9, 8, 1, 7, 6, 5, 4};
    Bar bars[7];
    for (int i = 0; i < 7; ++i) {
        bars[i] = Bar{lows[i] + 1.0, lows[i] + 2.0, lows[i],
                      lows[i] + 1.0, 1.0,
                      static_cast<int64_t>(i) * 900000};
    }

    GeneratedStrategy strategy;
    strategy.run(bars, 7);
    std::cout << std::setprecision(17)
              << strategy.early0 << '\t' << strategy.early1 << '\t'
              << strategy.beforeFlip << '\t' << strategy.afterFlip << '\t'
              << strategy.reverseAfterFlip << '\t'
              << strategy.equalAfterFlip << '\n';
    return 0;
}
"""


def test_runtime_keeps_long_inactive_bank_warm_before_late_flip() -> None:
    cpp = transpile(_LONG_INACTIVE_PINE)
    stdout = _compile_and_run(cpp + _LONG_INACTIVE_DRIVER)
    observed = tuple(float(value) for value in stdout.strip().split("\t"))
    # Pin the engine's first-bar na warmup and first available value.  After six
    # short-window bars, switching to the long bank immediately sees its
    # preserved five-bar history.
    assert math.isnan(observed[0])
    assert observed[1:] == (8.0, 5.0, 1.0, 4.0, 4.0)
