"""A history-read ``var`` runs a non-constant initializer at its declaration.

Pine runs a ``var`` initializer once, in statement order, on the first bar
that reaches it. A primitive ``var`` does so at its declaration (a one-shot
guard), but one read with history (a ``Series<T>`` member) was pushed in the
first-bar preamble, ahead of the script body, from the analyzer's spelling
of its initializer. That was right only for a constant or a bar field:

* ``var int signal = direction.neutral`` pushed ``direction.neutral`` -- raw
  Pine on a UDT handle, before ``direction`` was assigned -- which did not
  compile ("no member named 'neutral' in 'pf_safe_Label'": the ten
  ai-distribution probes, lane XSYM-C); so did ``ta.sma(close, len)``,
  ``bar_index + 1``, ``color.red``, ``high[1]``, ``syminfo.mintick``,
  ``time`` and a user call;
* ``var float first2 = b`` over ``b = close * 2`` pushed the member ``b``
  before the body assigned it: 0 on the first bar, silently;
* ``var float seed = len * 1.5`` over an input pushed the input's default
  folded, so an override never reached it.

Such a ``var`` now initializes at its declaration like a scalar one: the
preamble pushes its na on the first bar and the declaration replaces that
slot once. A top-level declaration of a constant, a string literal, ``na``
or a bar field keeps the preamble, byte for byte; one in a block runs at its
declaration whatever its initializer, its history na until the first bar that
reaches it (the preamble pushed bar 0's value). TradingView's tapes of
``fixtures/silent_tv/cgs_series_var_init`` (and its twin with the input's
default at 5) and ``cgs_block_var_init`` spell every such var's history on
each close.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "silent_tv"
NAME = "cgs_series_var_init"
TWIN = "cgs_series_var_init_len5"
BLOCK = "cgs_block_var_init"


def test_the_series_var_init_tapes_replay_with_an_override(tmp_path):
    engine = skip_unless_e2e_env()
    builds = {NAME: Build(source(NAME, FIXTURES)), "override": Build(source(NAME, FIXTURES)),
              TWIN: Build(source(TWIN, FIXTURES))}
    exits = replay(engine, tmp_path, builds, params={"override": {"Len": "5"}})
    for name, run in ((NAME, NAME), (TWIN, TWIN), (TWIN, "override")):
        tape = tape_exits(name, FIXTURES)
        assert len(tape) == 336
        missed = mismatches(tape, exits[run])
        assert not missed, f"{run}: {len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])
    print(f"series var init: {len(tape)} of {len(tape)} exit Signals equal TradingView's, "
          "and the Len=5 override replays the twin's tape")


def test_a_block_declaration_reads_na_history_until_its_first_run(tmp_path):
    # The block first runs on the 03:15 UTC bar: fc[1] and k[1] are na there,
    # fc its close. The preamble used to push bar 0's close (and 1.5) and
    # carry them: fc held the first bar's close forever.
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {BLOCK: Build(source(BLOCK, FIXTURES))})[BLOCK]
    tape = tape_exits(BLOCK, FIXTURES)
    assert len(tape) == 330
    assert min(tape.items())[1] == "na|na|1833.75|2.5"  # the 03:15 bar
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])


def test_the_declaration_initializes_the_udt_field_var():
    cpp = transpile(source(NAME, FIXTURES))
    assert "signal.push(direction.neutral)" not in cpp
    assert "signal.push(na<int>());" in cpp
    body = cpp[cpp.index("direction = _pf_udt_"):]
    assert "signal.update(_pf_udt_pf_safe_Label.read(direction).neutral);" in body
    # b is assigned before first2 reads it; the input's getter, not its default.
    assert body.index("b = ") < body.index("first2.update(b);")
    assert "seed.update((len * 1.5));" in cpp
    # A bar field keeps the preamble.
    assert "firstClose.push(current_bar_.close);" in cpp
    compile_cpp(cpp)


_SHAPES = """//@version=6
strategy("series var initializers")
L = input.int(3, "L")
var float y = ta.sma(close, L)
var int z = bar_index + 1
var float w = math.max(open, close) * 2
var color c1 = color.red
var color c2 = #ff0000
var float h1 = high[1]
var float y1 = syminfo.mintick
var int t1 = time
var string s1 = "a"
var bool b1 = true
var int i1 = -1
var float f1 = na
var float x1 = close
var float z1 = math.pi
var float k1 = 2.5 * 4
if y[1] > 0 and z[1] > 0 and w[1] > 0 and c1[1] == c2[1] and h1[1] > 0 and y1[1] > 0 and t1[1] > 0 and s1[1] == "a" and b1[1] and i1[1] < 0 and na(f1[1]) and x1[1] > 0 and z1[1] > 3 and k1[1] > 0
    strategy.entry("L", strategy.long)
"""


def test_every_initializer_shape_compiles():
    cpp = transpile(_SHAPES)
    for pushed in ("y.push(na<double>());", "z.push(na<int>());", "c1.push(na<int64_t>());",
                   "h1.push(na<double>());", "t1.push(na<int64_t>());"):
        assert pushed in cpp, pushed
    # A constant, a string literal, na and a bar field keep the preamble.
    for kept in ('s1.push("a");', "b1.push(true);", "i1.push(-1);", "f1.push(na<double>());",
                 "x1.push(current_bar_.close);", "z1.push(3.141592653589793);", "k1.push(10);"):
        assert kept in cpp, kept
    compile_cpp(cpp)


@pytest.mark.parametrize("init", ["close * 2", "direction.neutral", "f(open)"])
def test_a_conditional_declaration_initializes_where_it_first_runs(init):
    cpp = transpile(
        "//@version=6\n"
        'strategy("conditional series var")\n'
        "type Label\n"
        "    int neutral\n"
        "Label direction = Label.new(neutral = 7)\n"
        "f(x) => x + 1\n"
        "if bar_index > 3\n"
        f"    var float v = {init}\n"
        "    v := v + 1\n"
        "    if v[1] > 0\n"
        '        strategy.entry("L", strategy.long)\n'
    )
    compile_cpp(cpp)


def test_a_renamed_block_member_initializes_where_it_first_runs():
    # Two blocks declare k: the second's member is k__blk1. Its constant
    # initializer runs at its declaration too, bar 0 pushing its na.
    cpp = transpile(
        "//@version=6\n"
        'strategy("renamed block var")\n'
        "if bar_index < 3\n"
        "    var float k = 1.5\n"
        "    k := k + 1\n"
        "    if k[1] > 0\n"
        '        strategy.entry("S", strategy.short)\n'
        "if bar_index >= 5\n"
        "    var float k = 2.5\n"
        "    k := k + 1\n"
        "    if na(k[1])\n"
        '        strategy.entry("L", strategy.long)\n'
    )
    assert "k__blk1.push(na<double>());" in cpp and "k__blk1.update(2.5);" in cpp
    assert "k__blk1.push(2.5)" not in cpp
    compile_cpp(cpp)
