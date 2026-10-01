"""An int given to a float field of a user-defined object's constructor.

TradingView converts the int to the float it is: ``Cell.new(v = bar_index)``
holds ``bar_index`` in its ``float`` field, an int that is na holds na, and an
epoch holds the epoch (``fixtures/array_history_tv`` ``uctor_float``). The
codegen initializes the record with a designated initializer, where C++
refuses to narrow a non-constant ``int`` or ``int64_t`` into a ``double``:
none of these constructors compiled. The value is now converted where the
field is a ``double``, an integer na to na; an integer literal, which
compiled, keeps its spelling. An int assigned to such a field (``c.v :=
iv``) compiled, but an int na became -2147483648 where TradingView's field
is na (``uassign_float``): it is converted the same way.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import (
    Build, chart_feed_head, execute_all, ok, reference_codegen,
    skip_unless_e2e_env, transpile_json,
)
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "array_history_tv"
BASE = "a7c6473512623d8fb302a370702770b28e47b933"


def _script(body: str) -> str:
    return (
        "//@version=6\n"
        'strategy("ctor", overlay = true)\n'
        "type Cell\n    float v\n    float w = 0.0\n"
        f"{body}"
        "if c.v > 3\n"
        '    strategy.entry("L", strategy.long)\n'
    )


# Every spelling below compiles on TradingView (uctor_float holds most).
SHAPES = {
    "bar_index_keyword": "c = Cell.new(v = bar_index)\n",
    "int_variable_positional": "int iv = bar_index * 2\nc = Cell.new(iv)\n",
    "int_na_variable": "int iv = bar_index * 2\nif bar_index % 3 == 0\n    iv := na\nc = Cell.new(iv)\n",
    "epoch": "c = Cell.new(v = time)\n",
    "int_arithmetic_second_field": "c = Cell.new(close, bar_index + 1)\n",
    "function_int_parameter": "mk(int k) =>\n    Cell.new(v = k)\nc = mk(bar_index * 5)\n",
    "int_cast": "c = Cell.new(v = int(close))\n",
    "nested_object": (
        "type Outer\n    Cell inner\n    float s\n"
        "o = Outer.new(Cell.new(bar_index), bar_index)\nc = o.inner\n"),
    "var_object": "var c = Cell.new(v = bar_index)\n",
}


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_every_int_to_float_constructor_compiles(name):
    compile_cpp(transpile(_script(SHAPES[name])), label=name)


def test_an_int_literal_keeps_its_spelling():
    # A constant compiled before (C++ narrows a constant that fits): its
    # initializer is unchanged.
    cpp = transpile(_script("c = Cell.new(v = 3, w = -2)\n"))
    assert "_PFUdtRecord_Cell{.v = 3, .w = (-2)}" in cpp
    compile_cpp(cpp, label="int literal")


def test_an_expression_of_int_literals_keeps_its_spelling():
    # So does an expression of literals a double holds exactly; one past
    # 2**53, which no double holds, is converted at run time.
    cpp = transpile(_script("c = Cell.new(1 + 2, 5 * 2)\n"))
    assert "_PFUdtRecord_Cell{.v = (1 + 2), .w = (5 * 2)}" in cpp
    compile_cpp(cpp, label="int literal expression")
    wide = transpile(_script("c = Cell.new(3000000000 * 4000000)\n"))
    assert "_PFUdtRecord_Cell{.v = [&](){ auto _pf_w" in wide
    compile_cpp(wide, label="wide int literal expression")


def test_a_double_value_keeps_its_spelling():
    cpp = transpile(_script("c = Cell.new(v = close, w = 0.5)\n"))
    assert "_PFUdtRecord_Cell{.v = current_bar_.close, .w = 0.5}" in cpp


def test_the_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {"uctor_float": Build(source("uctor_float", FIXTURES))})
    tape = tape_exits("uctor_float", FIXTURES)
    assert len(tape) == 336
    # TradingView: 0|na|1|0|0 on every third bar's close, 0|0|1|0|0 elsewhere.
    assert set(tape.values()) == {"0|0|1|0|0", "0|na|1|0|0"}
    missed = mismatches(tape, exits["uctor_float"])
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])


def test_an_assigned_int_keeps_its_na(tmp_path):
    # TradingView: na|0 on every third bar's close, 0|0 elsewhere; the
    # pre-lane build read -2147483648 there.
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {"uassign_float": Build(source("uassign_float", FIXTURES))})
    tape = tape_exits("uassign_float", FIXTURES)
    assert len(tape) == 336
    assert set(tape.values()) == {"0|0", "na|0"}
    missed = mismatches(tape, exits["uassign_float"])
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])


def test_an_assigned_double_or_literal_keeps_its_spelling():
    # An int variable stored as an int is converted; one the script stores
    # as a double (never reassigned) and a double keep their spelling.
    cpp = transpile(_script(
        "c = Cell.new()\nc.v := close\nc.w := 3\nint dv = bar_index\nc.w := dv\n"
        "int iv = bar_index\nif bar_index % 3 == 0\n    iv := na\nc.w := iv\n"))
    assert "_pf_udt_Cell.get(c).v = current_bar_.close;" in cpp
    assert "_pf_udt_Cell.get(c).w = 3;" in cpp
    assert "_pf_udt_Cell.get(c).w = dv;" in cpp
    assert "_pf_udt_Cell.get(c).w = [&](){ auto _pf_w = (iv);" in cpp
    compile_cpp(cpp, label="assigned float fields")


def test_the_pre_lane_build_did_not_compile_it(tmp_path):
    # Clang refuses the narrowing of a non-constant int in the designated
    # initializer; GCC only warns, and reads an int na there as
    # -2147483648. Narrowing as an error makes both say so.
    tree = reference_codegen(BASE)
    if tree is None:
        pytest.skip(f"codegen {BASE} is not in this checkout's history")
    pine = tmp_path / "uctor_float.pine"
    pine.write_text(source("uctor_float", FIXTURES), encoding="utf-8")
    transpiled = transpile_json(pine, tree)
    assert transpiled.get("ok"), transpiled
    with pytest.raises(AssertionError, match="compile-only check failed"):
        compile_cpp(transpiled["cpp"], label=f"uctor_float at {BASE[:8]}",
                    extra_flags=("-Werror=narrowing",))
    pine_now = transpile_json(pine)
    assert pine_now.get("ok"), pine_now
    compile_cpp(pine_now["cpp"], label="uctor_float", extra_flags=("-Werror=narrowing",))


VALUES = """//@version=6
strategy("ctor values", overlay = true)
type Cell
    float v
int iv = bar_index * 2
if bar_index % 3 == 0
    iv := na
c = Cell.new(v = bar_index)
n = Cell.new(iv)
e = Cell.new(v = time)
isNa = na(n.v) ? 1 : 0
asFloat = c.v / 2
epochExact = e.v == time ? 1 : 0
if bar_index == 3
    strategy.entry("L", strategy.long)
// @pf-trace asFloat=asFloat
// @pf-trace isNa=isNa
// @pf-trace epochExact=epochExact
"""


def test_the_fields_hold_the_numbers_bar_by_bar(tmp_path):
    # bar_index / 2 in a float field is a float division; an na int is na on
    # every third bar; an epoch is exact in a double.
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 6)
    runs = execute_all(engine, feed, tmp_path, {"values": Build(VALUES, trace=True)})
    values: dict[str, list[float]] = {}
    for record in ok(runs, "values").traces["default"]:
        values.setdefault(record["name"], []).append(record["value"])
    assert values == {
        "asFloat": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5],
        "isNa": [1, 0, 0, 1, 0, 0],
        "epochExact": [1, 1, 1, 1, 1, 1],
    }
