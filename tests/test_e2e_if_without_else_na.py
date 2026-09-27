"""An if without else, or a switch without default, is na when no arm runs.

TradingView gives an if without else (an else-if chain without a final
else, a switch without default) the value ``na`` when none of its arms runs:
a numeric or string na, ``false`` for a bool. That holds for a function's
last statement, for one nested in a taken arm, and for the value a global,
reassigned or function-local variable takes -- the variable does not keep
its previous bar's value. The codegen returned the tail slot's default
(``double _func_ret = 0.0``, emit_top's function emitter) and assigned a
declaration's or reassignment's target only inside the arms, so a global
kept the previous bar's value on every bar no arm ran (only a map or matrix
target got its na, ``emit_implicit_na_fallback``).

TradingView's tape of ``fixtures/open_items_tv/if_tail_na`` (lab tv
--no-note) spells every shape on each exit bar: ``NaN`` for the float tails
(an if, one nested in a taken arm, a switch, an else-if chain), ``na`` for
``na()`` of the int and string tails, ``F`` for the bool tail, -7 for ``nz``
of a local if-value, and ``NaN`` for the global declaration, switch and
reassignment on each bar whose close is not above its open. The replay
compares every exit comment, and each shape traces bar for bar like its
ternary spelling ``up ? close : na``.
"""

from __future__ import annotations

import pytest

from tests._e2e import (
    Build, chart_feed_head, execute_all, ok, per_bar_mismatches,
    skip_unless_e2e_env,
)
from tests._tv_tapes import FIXTURES, exit_misses, tape, window_feed


def test_the_if_tail_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    feed = window_feed(engine, tmp_path)
    runs = execute_all(engine, feed, tmp_path, {
        "if_tail_na": Build((FIXTURES / "if_tail_na.pine").read_text())})
    ok(runs, "if_tail_na")
    trades = tape("if_tail_na")
    assert len(trades) == 7
    misses = exit_misses(engine, tmp_path / "if_tail_na", feed, trades)
    assert not misses, misses[:3]


HEAD = '//@version=6\nstrategy("if without else", overlay = true)\nup = close > open\n'
# Each shape and its ternary spelling, traced as "s" and "r".
TWINS = {
    "global_declaration": ("s = if up\n    close", "r = up ? close : na"),
    "global_switch": ("s = switch\n    up => close", "r = up ? close : na"),
    "reassignment": ("var float s = 0.0\ns := if up\n    close",
                     "var float r = 0.0\nr := up ? close : na"),
    "function_tail": ("f(bool c) =>\n    if c\n        close\ns = f(up)",
                      "f(bool c) => c ? close : na\nr = f(up)"),
    "nested_tail": ("f(bool c) =>\n    if close > 0\n        if c\n            close\n"
                    "    else\n        2.0\ns = f(up)",
                    "f(bool c) => close > 0 ? (c ? close : na) : 2.0\nr = f(up)"),
    "int_tail": ("f(bool c) =>\n    if c\n        1\ns = f(up)",
                 "f(bool c) => c ? 1 : na\nr = f(up)"),
    "local_declaration": ("f(bool c) =>\n    y = if c\n        close\n    nz(y, -7.0)\ns = f(up)",
                          "f(bool c) =>\n    y = c ? close : na\n    nz(y, -7.0)\nr = f(up)"),
}
TAIL = ("\n// @pf-trace v={name}\nif na({name})\n"
        "    strategy.entry(\"L\", strategy.long)\nelse\n    strategy.close(\"L\")\n")


@pytest.fixture(scope="module")
def twin_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("if_without_else")
    feed = chart_feed_head(engine, base, 400)
    builds = {}
    for shape, (subject, reference) in TWINS.items():
        builds[f"{shape}-subject"] = Build(HEAD + subject + TAIL.format(name="s"), trace=True)
        builds[f"{shape}-reference"] = Build(HEAD + reference + TAIL.format(name="r"), trace=True)
    return execute_all(engine, feed, base, builds)


@pytest.mark.parametrize("shape", sorted(TWINS))
def test_an_unmatched_if_value_is_its_ternary_spelling(twin_runs, shape):
    subject = ok(twin_runs, f"{shape}-subject")
    reference = ok(twin_runs, f"{shape}-reference")
    compared, mismatched, first = per_bar_mismatches(
        subject.traces["default"], reference.traces["default"])
    assert compared == 400
    assert mismatched == 0, first
    assert subject.trades["default"] == reference.trades["default"]
