"""strategy.risk.allow_entry_in lowers its argument by VALUE.

The direction the script restricts entries to is the VALUE of the argument:
`strategy.direction.long` or `.short` allow one side, `.all` both, whether the
argument is the constant itself, a ternary over the constants, a const, an
input, a helper's result or a string input comparison. Each shape below is
built, run on the same chart feed, and held to the run of the BARE LITERAL
CONSTANT of the value the shape means (written here as data from the source,
never read from a build): the traced position and the trade list must be
identical, bar for bar and trade for trade. Comparing with the emitted C++ of
an older build would not say what the engine admits.

An empty run proves nothing, so each control must show the positions its value
allows (long: 0 and 1, short: -1 and 0, all: -1, 0 and 1), and the three
controls must differ from each other.

The translation rows run everywhere: for a shape that means one side, the
generated program must not set "all directions" as a constant.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests._e2e import (
    Build, chart_feed_head, execute_all, per_bar_mismatches, skip_unless_e2e_env,
)

LONG = "strategy.direction.long"
SHORT = "strategy.direction.short"
ALL = "strategy.direction.all"
CALL = "strategy.risk.allow_entry_in"
HEADER = '//@version=6\nstrategy("risk values", default_qty_type = strategy.fixed, default_qty_value = 1)\n'
EMA = "fast = ta.ema(close, 9)\nslow = ta.ema(close, 21)\n"
ENTRIES = (
    "if ta.crossover(fast, slow)\n"
    "    strategy.entry('Long', strategy.long)\n"
    "if ta.crossunder(fast, slow)\n"
    "    strategy.entry('Short', strategy.short)\n"
)
TRACE = "// @pf-trace pos=strategy.position_size\n"
POSITIONS = {"long": {0.0, 1.0}, "short": {-1.0, 0.0}, "all": {-1.0, 0.0, 1.0}}
BARE = {"long": LONG, "short": SHORT, "all": ALL}


def _with_call(declarations: str, argument: str) -> str:
    return HEADER + EMA + declarations + f"{CALL}({argument})\n" + ENTRIES + TRACE


def _without_call(declarations: str) -> str:
    # The call is inside a helper the declarations run every bar.
    return HEADER + EMA + declarations + ENTRIES + TRACE


# (id, declarations, argument or None, the value the argument means).
# None: the declarations hold the call, inside a helper.
SHAPES = [
    ("literal-condition", "", f"true ? {LONG} : {ALL}", "long"),
    ("const-direction", f"const chosen = {LONG}\n", "chosen", "long"),
    ("issue-292-input", "useShort = input.bool(true)\n",
     f"useShort ? {SHORT} : {LONG}", "short"),
    ("input-expression", "useShort = input.bool(true)\nchooseLong = not useShort\n",
     f"chooseLong ? {LONG} : {ALL}", "all"),
    ("const-expression", "const bool useLong = true\n", f"useLong ? {LONG} : {ALL}", "long"),
    ("simple-timeframe", "", f"timeframe.isintraday ? {LONG} : {ALL}", "long"),
    ("simple-syminfo-self-comparison", "",
     f"syminfo.currency == syminfo.currency ? {LONG} : {ALL}", "long"),
    ("unrelated-series-local",
     f"chosen = {LONG}\nunused() =>\n    chosen = close > open ? {LONG} : {ALL}\n    chosen\n",
     "chosen", "long"),
    ("simple-helper-return", "isPositive(float value) => value > 0\n",
     f"isPositive(1.0) ? {LONG} : {ALL}", "long"),
    ("unknown-helper-return", f"direction() => {LONG}\n", "direction()", "long"),
    ("forwarded-input",
     "relay(bool enabled) =>\n"
     f"    {CALL}(enabled ? {LONG} : {ALL})\n    1\nx = relay(input.bool(true))\n",
     None, "long"),
    ("forwarded-method-input",
     "method relay(bool enabled) =>\n"
     f"    {CALL}(enabled ? {LONG} : {ALL})\n    1\ngate = input.bool(true)\nx = gate.relay()\n",
     None, "long"),
    ("parameter-shadows-builtin",
     f"run(float close) =>\n    {CALL}(close > 0 ? {LONG} : {ALL})\n    1\nx = run(1.0)\n",
     None, "long"),
    ("constant-local-shadows-series-global",
     f"chosen = close > open ? {LONG} : {ALL}\n"
     f"run() =>\n    chosen = {LONG}\n    {CALL}(chosen)\n    1\nx = run()\n",
     None, "long"),
]

# The input arms: dir == 'Long' selects the first constant, anything else the
# second. Each shape runs at its default and with the input overridden.
ARMS = [
    ("long", "short"), ("short", "long"), ("long", "all"), ("all", "short"),
]


def _shape_source(declarations: str, argument: str | None) -> str:
    if argument is None:
        return _without_call(declarations)
    return _with_call(declarations, argument)


def _arms_source(true_direction: str, false_direction: str, default: str) -> str:
    return (
        HEADER + EMA
        + f"dir = input.string('{default}', 'Direction', options = ['Long', 'Short'])\n"
        + f"{CALL}(dir == 'Long' ? strategy.direction.{true_direction} : "
        f"strategy.direction.{false_direction})\n"
        + ENTRIES + TRACE
    )


def _arm_value(true_direction: str, false_direction: str, selection: str) -> str:
    return true_direction if selection == "Long" else false_direction


def _control(value: str) -> str:
    return _with_call("", BARE[value])


def _positions(outcome, tag: str = "default") -> set[float]:
    return {r["value"] for r in outcome.traces[tag] if r["name"] == "pos"}


def _assert_same(subject, subject_tag, control, label):
    compared, mismatched, first = per_bar_mismatches(
        subject.traces[subject_tag], control.traces["default"], ("subject", "bare literal"))
    assert compared > 0, f"[{label}] no trace records"
    assert mismatched == 0, f"[{label}] {first}"
    assert subject.trades[subject_tag] == control.trades["default"], (
        f"[{label}] the trade lists differ")


@pytest.mark.parametrize("name,declarations,argument,means", SHAPES,
                         ids=[shape[0] for shape in SHAPES])
def test_a_direction_argument_is_lowered_by_its_value_in_translation(
        name, declarations, argument, means):
    cpp = transpile(_shape_source(declarations, argument))
    # A script that restricts entries to one side must not end up with a
    # constant "all directions" for its call: that is how an argument that is
    # not the bare constant used to be lowered.
    if means != "all":
        assert "set_pine_risk_direction(0);" not in cpp
    else:
        assert "set_pine_risk_direction(1);" not in cpp
        assert "set_pine_risk_direction(-1);" not in cpp


def test_the_three_controls_run_and_differ(tmp_path):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 1500)
    builds = {value: Build(_control(value), trace=True) for value in BARE}
    runs = execute_all(engine, feed, tmp_path / "runs", builds)
    for value, outcome in runs.items():
        assert outcome.error is None, outcome.error
        assert _positions(outcome) == POSITIONS[value], (value, _positions(outcome))
    assert len({runs[value].trades["default"] for value in BARE}) == 3


def test_every_shape_admits_the_entries_of_the_literal_it_means(tmp_path):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 1500)
    builds = {f"control-{value}": Build(_control(value), trace=True) for value in BARE}
    for name, declarations, argument, means in SHAPES:
        builds[f"shape-{name}"] = Build(_shape_source(declarations, argument), trace=True)
    for true_direction, false_direction in ARMS:
        for default in ("Long", "Short"):
            other = "Short" if default == "Long" else "Long"
            key = f"arms-{true_direction}-{false_direction}-{default}"
            builds[key] = Build(_arms_source(true_direction, false_direction, default),
                                overrides={"Direction": other}, trace=True)
    runs = execute_all(engine, feed, tmp_path / "runs", builds)
    failed = [key for key, outcome in runs.items() if outcome.error]
    assert not failed, {key: runs[key].error[:300] for key in failed}
    for value in BARE:
        assert _positions(runs[f"control-{value}"]) == POSITIONS[value]
    problems = []
    for name, _declarations, _argument, means in SHAPES:
        try:
            _assert_same(runs[f"shape-{name}"], "default", runs[f"control-{means}"], name)
        except AssertionError as exc:
            problems.append(f"{name} (means {means}): {str(exc)[:200]}")
    for true_direction, false_direction in ARMS:
        for default in ("Long", "Short"):
            other = "Short" if default == "Long" else "Long"
            key = f"arms-{true_direction}-{false_direction}-{default}"
            for tag, selection in (("default", default), ("override", other)):
                means = _arm_value(true_direction, false_direction, selection)
                try:
                    _assert_same(runs[key], tag, runs[f"control-{means}"], f"{key} {tag}")
                except AssertionError as exc:
                    problems.append(f"{key} {tag} (means {means}): {str(exc)[:200]}")
    assert not problems, "\n".join(problems)
