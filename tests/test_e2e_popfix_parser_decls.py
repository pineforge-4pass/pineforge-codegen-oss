"""Typed declarations TradingView parses: ``color[] cs = ...`` and ``lib.T x``.

A statement starting ``color[] fanColors = array.from(...)`` stopped at
"Unexpected token RBRACKET": only keyword types (``float[]``) and ``var``
declarations took the postfix-array form. ``var xgb.XGBModel model = na``
stopped at "Expected EQUALS, got DOT" before the import it names could be
refused. Both now parse; the library type then meets the located import
refusal. ``array.from`` also builds the declaration's element type, so a
``color[]`` of color variables is a ``std::vector<int64_t>`` (its argument
inference read a color variable as float, "non-constant-expression cannot be
narrowed").

Replayed end to end beside a twin that holds the colors in plain variables:
identical traces and trades.
"""

from __future__ import annotations

import pytest

from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
    transpile_json,
)


HEAD = '//@version=6\nstrategy("popfix parser decls", overlay = true)\n'
TAIL = """
if ta.crossover(ta.ema(close, 5), ta.ema(close, 20))
    strategy.entry("L", strategy.long)
if ta.crossunder(ta.ema(close, 5), ta.ema(close, 20))
    strategy.close("L")
// @pf-trace up=first == c_up ? 1 : 0
// @pf-trace dn=second == c_dn ? 1 : 0
// @pf-trace n=count
"""
SHAPE = HEAD + """
c_up = color.green
c_dn = color.red
color[] cols = array.from(c_up, c_dn)
float[] levels = array.from(1.0, 2.0, 3.0)
first = array.get(cols, 0)
second = array.get(cols, 1)
count = array.size(cols) + array.size(levels)
""" + TAIL
REFERENCE = HEAD + """
c_up = color.green
c_dn = color.red
first = c_up
second = c_dn
count = 5
""" + TAIL


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("popfix_parser_decls")
    feed = chart_feed_head(engine, base, 3000)
    return execute_all(engine, feed, base, {
        "shape": Build(SHAPE, trace=True), "reference": Build(REFERENCE, trace=True)})


def test_color_array_declaration_runs_like_plain_colors(runs):
    print("color[]:", assert_same_runs(ok(runs, "shape"), ok(runs, "reference")))


def test_qualified_library_types_reach_the_import_refusal(tmp_path):
    pine = tmp_path / "strategy.pine"
    pine.write_text(HEAD + "import someone/SomeLib/1 as lib\n"
                    "var lib.Model model = na\n"
                    "lib.Model other = na\n")
    result = transpile_json(pine)
    assert not result["ok"]
    messages = [d["message"] for d in result["diagnostics"]]
    assert any("Import is not supported" in m for m in messages), messages
    assert not any("Expected EQUALS" in m for m in messages), messages
