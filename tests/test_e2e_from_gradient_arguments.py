"""color.from_gradient evaluates its arguments; its colour is still the na colour.

color.from_gradient only tints charts, so its value reaches no order and
PineForge keeps emitting the na colour (a warned visual-only stub). The
lowering also dropped its arguments (``codegen/visit_call.py``
``_visit_color_call`` returned ``"0"`` for it), so a call in them never
ran: a counting probe never counted, and a helper keeping a series in one
never advanced (lane W2's open findings). TradingView evaluates each
argument once per execution, in parameter order, whether written by
position or by keyword.

The call now evaluates every argument that is not a side-effect-free read,
once, in TradingView's parameter order, and yields the na colour; a call
whose arguments are all plain reads keeps its ``0`` spelling. TradingView's
tape of ``fixtures/open_items_tv/from_gradient_args`` (lab tv --no-note)
spells each counting probe's lag behind ``bar_index + 1`` (0: once per
bar), by position and by keyword, and a helper's series stored on every
bar; the replay compares every exit comment.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests._e2e import Build, execute_all, ok, skip_unless_e2e_env
from tests._tv_tapes import FIXTURES, exit_misses, tape, window_feed


def test_the_from_gradient_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    feed = window_feed(engine, tmp_path)
    runs = execute_all(engine, feed, tmp_path, {
        "from_gradient_args": Build((FIXTURES / "from_gradient_args.pine").read_text())})
    ok(runs, "from_gradient_args")
    trades = tape("from_gradient_args")
    assert len(trades) == 7
    misses = exit_misses(engine, tmp_path / "from_gradient_args", feed, trades)
    assert not misses, misses[:3]


HEAD = ('//@version=6\nstrategy("from_gradient", overlay = true)\n'
        "f(float v) => v * 2\n")


def _assignment(body: str) -> str:
    cpp = transpile(HEAD + body + "\nif close > open\n    strategy.entry(\"L\", strategy.long)\n")
    return next(l.strip() for l in cpp.splitlines() if l.strip().startswith("c = "))


def test_plain_reads_keep_the_na_colour_spelling():
    assert _assignment("c = color.from_gradient(close, low, high, color.red, color.green)") == "c = 0;"


@pytest.mark.parametrize("call", [
    "color.from_gradient(f(close), f(low), f(high), color.red, color.green)",
    "color.from_gradient(top_value = f(high), value = f(close), bottom_value = f(low), "
    "bottom_color = color.red, top_color = color.green)",
])
def test_each_argument_runs_once_in_parameter_order(call):
    line = _assignment(f"c = {call}")
    assert line.count("(void)(") == 3
    assert (line.index("f(current_bar_.close)") < line.index("f(current_bar_.low)")
            < line.index("f(current_bar_.high)"))
    assert line.endswith("return 0; }());")
