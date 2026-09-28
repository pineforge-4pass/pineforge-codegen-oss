"""``for [i, v] in <array>`` reads each element's index and value.

The tuple form of ``for ... in`` was lowered as a structured binding over the
collection, ``for (auto [i, v] : arr)``, which only a map's pairs admit: a
``std::vector`` has no pairs to decompose, so every array loop of this form
failed to compile. The array is now bound once and indexed, the index an
``int``, the element a copy of its value (a ``std::vector<bool>`` element
read through ``auto`` is a proxy a later ``arr.set(i, ...)`` changes).

TradingView's tape of ``fixtures/silent2_tv/cgs2_array_index_loop`` spells
index-weighted sums over a script array, a function's parameter, a string
array and a loop that is a function's last statement.
"""

from __future__ import annotations

from pathlib import Path

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import (
    Build, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "silent2_tv"
NAME = "cgs2_array_index_loop"


def test_the_array_index_loop_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {NAME: Build(source(NAME, FIXTURES))})[NAME]
    tape = tape_exits(NAME, FIXTURES)
    assert len(tape) == 336
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])
    print(f"array index loop: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


def test_an_array_tuple_loop_indexes_the_array():
    cpp = transpile(
        "//@version=6\n"
        'strategy("array loop")\n'
        "arr = array.from(1.0, 2.0, 3.0)\n"
        "s = 0.0\n"
        "for [i, v] in arr\n"
        "    s += i * v\n"
        "m = map.new<string, float>()\n"
        'm.put("a", 1.0)\n'
        "for [k, x] in m\n"
        "    s += x\n"
        "if s > 5\n"
        '    strategy.entry("L", strategy.long)\n')
    assert "auto&& __pf_array_iter_0 = arr;" in cpp
    assert "int i = __pf_array_index_0;" in cpp
    assert ("typename std::decay_t<decltype(__pf_array_iter_0)>::value_type v "
            "= __pf_array_iter_0[(size_t)__pf_array_index_0];") in cpp
    # A map keeps its key/value iteration.
    assert ".keys()) {" in cpp
    compile_cpp(cpp)


def test_the_bool_copy_tape_replays(tmp_path):
    # TradingView counts each element's value at its iteration: 3 or 2 set
    # flags before the body flips them, and 0 or 1 after.
    name = "cgs2_array_loop_bool_copy"
    exits = replay(skip_unless_e2e_env(), tmp_path,
                   {name: Build(source(name, FIXTURES))})[name]
    tape = tape_exits(name, FIXTURES)
    assert len(tape) == 336
    assert set(tape.values()) == {"3|0", "2|1"}
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])


def test_a_bool_element_is_its_value_at_the_iteration(tmp_path):
    # TradingView: v holds the element as the iteration read it; setting the
    # slot later in the body does not change v, so every bar counts 3.
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 5)
    runs = execute_all(engine, feed, tmp_path, {"bools": Build(
        "//@version=6\n"
        'strategy("bool loop")\n'
        "flags = array.from(true, true, true)\n"
        "int cnt = 0\n"
        "for [i, v] in flags\n"
        "    flags.set(i, false)\n"
        "    if v\n"
        "        cnt += 1\n"
        "if bar_index == 1\n"
        '    strategy.entry("L", strategy.long)\n'
        "// @pf-trace cnt=cnt\n",
        trace=True)})
    counts = [r["value"] for r in ok(runs, "bools").traces["default"]
              if r["name"] == "cnt"]
    assert counts and set(counts) == {3}
