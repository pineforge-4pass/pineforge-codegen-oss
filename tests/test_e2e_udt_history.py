"""The history of a user-defined object or a drawing reference, end to end.

A variable of a user-defined type or of a drawing type holds a reference, and
its history holds the references it held (Pine v6 User Manual, "Type system":
value vs. reference types). TradingView's tapes of the synthetic probes in
``fixtures/udt_history_tv`` (see its README) read, through the history: a
``var`` object as it is now, a fresh object changed after its bar, a nested
object, an object or na; boxes, lines and labels, a box deleted through its
history; inside a function, a method and an ``if`` block, one value per
execution of the scope, a function's ``var`` object at two call sites
included; ``==`` / ``!=`` of line and label references by identity; and the
history of an expression whose value is an object (a field holding one, a
function's result, a ternary's selection, of globals or of typed
parameters), the reference it produced at its previous evaluation, on every
execution of its scope below a lazy edge. Each probe closes its position with
a comment spelling the values it read, and every exit Signal of a tape must
be the engine's.

The codegen before this lane (``BASE``) declared such a history
``Series<double>`` and pushed the handles into it: none of the probes
compiled.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._compile import compile_cpp
from tests._e2e import (
    Build, chart_feed_head, execute_all, ok, reference_codegen,
    skip_unless_e2e_env, transpile_json,
)
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "udt_history_tv"
TAPES = ("udth_ref", "udth_box", "udth_box2", "udth_fn", "udth_fn2", "udth_method",
         "udth_line_eq", "udth_expr", "udth_drawparam")
# codegen main when the lane started (1.0.0 and its docs).
BASE = "01b4ee3514ee57b3080f33072431bfd0699597a4"


@pytest.fixture(scope="module")
def exits(tmp_path_factory) -> dict[str, dict[int, str]]:
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("udt_history")
    return replay(engine, base, {name: Build(source(name, FIXTURES)) for name in TAPES})


@pytest.mark.parametrize("name", TAPES)
def test_the_tape_replays(exits, name):
    tape = tape_exits(name, FIXTURES)
    assert len(tape) == 336
    missed = mismatches(tape, exits[name])
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])


def test_the_tapes_read_references():
    # What TradingView read, before any replay: a var object's history is the
    # object itself (d1 = 0), a change through c[1] reaches (c[2]).n (n2 = 1).
    ref = set(tape_exits("udth_ref", FIXTURES).values())
    assert {s.rsplit("|", 2)[0] for s in ref} == {"-1|-1|-1|-1|-1", "0|1|1|2|0"}
    box = set(tape_exits("udth_box", FIXTURES).values())
    assert box == {"-1|-1|-1|x|true", "0|0|-1|live|true"}
    assert set(tape_exits("udth_line_eq", FIXTURES).values()) == {"1011|1111011111"}


@pytest.mark.parametrize("name", TAPES)
def test_the_pre_lane_build_did_not_compile_them(tmp_path, name):
    tree = reference_codegen(BASE)
    if tree is None:
        pytest.skip(f"codegen {BASE} is not in this checkout's history")
    pine = tmp_path / f"{name}.pine"
    pine.write_text(source(name, FIXTURES), encoding="utf-8")
    transpiled = transpile_json(pine, tree)
    if not transpiled.get("ok"):
        return
    with pytest.raises(AssertionError, match="compile-only check failed"):
        compile_cpp(transpiled["cpp"], label=f"{name} at {BASE[:8]}")


SYNTHETIC = """//@version=6
strategy("history values", overlay = true)
type Cell
    int v
    int n = 0
var acc = Cell.new(v = 0)
acc.v += 1
c = Cell.new(v = bar_index)
l = line.new(bar_index, close, bar_index + 1, close)
prevNa = na(c[1]) ? 1 : 0
prevV = na(c[1]) ? -1 : (c[1]).v
same = na(acc[1]) ? -1 : acc.v - (acc[1]).v
if bar_index >= 1
    p = c[1]
    p.n += 1
changed = bar_index >= 2 ? (c[2]).n : -1
lineNe = l != l[1] ? 1 : 0
lineSelf = l[1] == l[1] ? 1 : 0
if bar_index == 3
    strategy.entry("L", strategy.long)
// @pf-trace prevNa=prevNa
// @pf-trace prevV=prevV
// @pf-trace same=same
// @pf-trace changed=changed
// @pf-trace lineNe=lineNe
// @pf-trace lineSelf=lineSelf
"""


def test_history_values_bar_by_bar(tmp_path):
    # On six bars of the corpus feed: c[1] is na on the first bar and the
    # previous bar's object after it; a var object's history is the object
    # (its field read now, so the difference is 0); a change through c[1]
    # reaches (c[2]).n on the next bar; a line drawn every bar differs from
    # its history (a na reference included) and equals itself.
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 6)
    runs = execute_all(engine, feed, tmp_path, {"hist": Build(SYNTHETIC, trace=True)})
    values: dict[str, list[float]] = {}
    for record in ok(runs, "hist").traces["default"]:
        values.setdefault(record["name"], []).append(record["value"])
    assert values == {
        "prevNa": [1, 0, 0, 0, 0, 0],
        "prevV": [-1, 0, 1, 2, 3, 4],
        "same": [-1, 0, 0, 0, 0, 0],
        "changed": [-1, -1, 1, 1, 1, 1],
        "lineNe": [1, 1, 1, 1, 1, 1],
        "lineSelf": [1, 1, 1, 1, 1, 1],
    }


def test_a_field_of_a_na_object_stops_the_run(tmp_path):
    # TradingView stops udth_na_field on its first bar (RE10041: "Cannot
    # access the 'Cell.v' field of an undefined object"): (c[1]).v where
    # c[1] is na. So does the engine.
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 4)
    runs = execute_all(engine, feed, tmp_path, {
        "na_field": Build(source("udth_na_field", FIXTURES))})
    error = runs["na_field"].error
    assert error is not None and "UDT access on na" in error, error


DRAWING_PARAMETERS = """//@version=6
strategy("drawing parameters", overlay = true)
method prevTop(box this) =>
    na(this[1]) ? -1.0 : (this[1]).get_top()
prevParamTop(box x) =>
    na(x[1]) ? -1.0 : (x[1]).get_top()
prevY(line x) =>
    na(x[1]) ? -1.0 : (x[1]).get_y1()
b = box.new(bar_index, bar_index, bar_index + 1, 0)
l = line.new(bar_index, bar_index * 3, bar_index + 1, 0)
m1 = b.prevTop()
p1 = prevParamTop(b)
y1 = prevY(l)
if bar_index == 3
    strategy.entry("L", strategy.long)
// @pf-trace m1=m1
// @pf-trace p1=p1
// @pf-trace y1=y1
"""


def test_a_drawing_parameters_history_reads_its_methods_bar_by_bar(tmp_path):
    # The box a receiver or a parameter held at the previous call, read with
    # a built-in method: its top is the previous bar_index, a line's y1
    # three times it, -1 at the first call (udth_drawparam's m1, p1, y1).
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 6)
    runs = execute_all(engine, feed, tmp_path, {
        "params": Build(DRAWING_PARAMETERS, trace=True)})
    values: dict[str, list[float]] = {}
    for record in ok(runs, "params").traces["default"]:
        values.setdefault(record["name"], []).append(record["value"])
    assert values == {
        "m1": [-1, 0, 1, 2, 3, 4],
        "p1": [-1, 0, 1, 2, 3, 4],
        "y1": [-1, 0, 3, 6, 9, 12],
    }
