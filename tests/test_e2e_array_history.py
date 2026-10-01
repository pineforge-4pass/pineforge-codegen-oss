"""The history of an array or a matrix, end to end.

TradingView's ``a[k]`` on an array or a matrix is a read-only copy of the
collection as the variable left it at the end of its scope's execution k
executions back: a ``var`` array's history is the array as the previous bar
left it, not the array itself (``fixtures/array_history_tv``, see its
README). Its tapes of the synthetic probes read, through the history: a
``var`` array grown before and after the reads, a fresh array per bar, a
variable reassigned on its bar, an array or na, a variable bound to its own
history; a ``var`` matrix, a fresh matrix, a matrix of strings; an object's
array and matrix fields through the object's history (the fields' arrays as
they are now); a ``for...in`` loop over the history, which iterates the array
the variable holds now; an array changed after its declaration, an if
block's local, a dynamic offset, the history bound to a variable and given
to a function, a ``var`` array rebound every fourth bar and an array of
objects. Each probe closes its position with a comment spelling the values
it read, and every exit Signal of a tape must be the engine's.

A change to the history stops TradingView's run (RE10051), and so does a
method on it before the variable has one (RE10052 / RE10053); the engine's
run stops with TradingView's text.

The codegen before this lane (``BASE``) lowered ``a[1]`` to the current
array's element 1 and ``m[1]`` to a ``Series<double>``: none of the history
probes compiled.
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

FIXTURES = Path(__file__).parent / "fixtures" / "array_history_tv"
TAPES = ("ahist_ref", "ahist_mtx", "ahist_field", "ahist_loop", "ahist_more")
# codegen when the lane started (cg/udt-history, the object history lane).
BASE = "a7c6473512623d8fb302a370702770b28e47b933"


@pytest.fixture(scope="module")
def exits(tmp_path_factory) -> dict[str, dict[int, str]]:
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("array_history")
    return replay(engine, base, {name: Build(source(name, FIXTURES)) for name in TAPES})


@pytest.mark.parametrize("name", TAPES)
def test_the_tape_replays(exits, name):
    tape = tape_exits(name, FIXTURES)
    assert len(tape) == 336
    missed = mismatches(tape, exits[name])
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])


def test_the_tapes_read_copies():
    # What TradingView read, before any replay: a var array's history is one
    # element shorter than the array (d1 = 1) and ends with the previous
    # bar's last element (n2 = -1); a var matrix's history holds the previous
    # bar's value (vd = -1); a for...in loop over b[1] sums the current
    # [bar_index] (l1 = bar_index, every close's first field is odd).
    ref = {tuple(s.split("|")[:2]) for s in tape_exits("ahist_ref", FIXTURES).values()}
    assert ref == {("-1", "-1"), ("1", "-1")}
    mtx = {s.split("|")[0] for s in tape_exits("ahist_mtx", FIXTURES).values()}
    assert mtx == {"-1"}
    loop = tape_exits("ahist_loop", FIXTURES)
    sums = sorted(int(s.split("|")[0]) for s in loop.values())
    assert sums[0] == -1 and all(v % 2 == 1 for v in sums[1:])


@pytest.mark.parametrize("name", [n for n in TAPES if n != "ahist_field"])
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
strategy("collection history values", overlay = true)
var va = array.new<int>()
va.push(bar_index)
b = array.from(bar_index)
b.push(bar_index * 10)
m = matrix.new<int>(1, 1, bar_index * 3)
prevNa = na(b[1]) ? 1 : 0
prevFirst = na(b[1]) ? -1 : (b[1]).get(0)
prevSize = na(b[1]) ? -1 : array.size(b[1])
varLag = na(va[1]) ? -1 : va.size() - (va[1]).size()
mPrev = na(m[1]) ? -1 : (m[1]).get(0, 0)
int loopSum = 0
for v in b[1]
    loopSum += v
pb = b[1]
bound = pb.size()
if bar_index == 3
    strategy.entry("L", strategy.long)
// @pf-trace prevNa=prevNa
// @pf-trace prevFirst=prevFirst
// @pf-trace prevSize=prevSize
// @pf-trace varLag=varLag
// @pf-trace mPrev=mPrev
// @pf-trace loopSum=loopSum
// @pf-trace bound=bound
"""


def test_history_values_bar_by_bar(tmp_path):
    # On six bars of the corpus feed: b[1] is na on the first bar and the
    # previous bar's two-element array after it; a var array's history is
    # one element shorter than the array; a matrix's history holds the
    # previous bar's value; a for...in loop over b[1] sums the current
    # [bar_index, bar_index * 10]; a variable bound to the history holds a
    # copy of it (an empty array on the first bar, where TradingView's is na).
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 6)
    runs = execute_all(engine, feed, tmp_path, {"hist": Build(SYNTHETIC, trace=True)})
    values: dict[str, list[float]] = {}
    for record in ok(runs, "hist").traces["default"]:
        values.setdefault(record["name"], []).append(record["value"])
    assert values == {
        "prevNa": [1, 0, 0, 0, 0, 0],
        "prevFirst": [-1, 0, 1, 2, 3, 4],
        "prevSize": [-1, 2, 2, 2, 2, 2],
        "varLag": [-1, 1, 1, 1, 1, 1],
        "mPrev": [-1, 0, 3, 6, 9, 12],
        "loopSum": [0, 11, 22, 33, 44, 55],
        "bound": [0, 2, 2, 2, 2, 2],
    }


# TradingView stops these probes' runs (fixtures/array_history_tv README);
# the engine's run stops with TradingView's text.
HISTORICAL = "Cannot modify the elements of a historical array or any slices of that array."
STOPS = {
    "ahist_push": HISTORICAL,
    "ahist_push_ns": HISTORICAL,
    "ahist_var_push": HISTORICAL,
    "ahist_mset": HISTORICAL,
    "ahist_na_size": "Cannot call array methods when id of array is na.",
    "ahist_na_rows": "Cannot call matrix methods when id of matrix is na.",
}
# And these run on TradingView: a copy of the history, a zero offset and an
# object's array field read through the object's history change freely.
RUNS = ("ahist_copy_push", "ahist_zero_push", "ahist_field_push")


@pytest.fixture(scope="module")
def stop_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("array_history_stops")
    feed = chart_feed_head(engine, base, 8)
    return execute_all(engine, feed, base, {
        name: Build(source(name, FIXTURES)) for name in (*STOPS, *RUNS)})


@pytest.mark.parametrize("name", sorted(STOPS))
def test_the_run_stops_where_tradingview_stops(stop_runs, name):
    error = stop_runs[name].error
    assert error is not None and STOPS[name] in error, error


@pytest.mark.parametrize("name", RUNS)
def test_the_run_goes_on_where_tradingview_goes_on(stop_runs, name):
    ok(stop_runs, name)
