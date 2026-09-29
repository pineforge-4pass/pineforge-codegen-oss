"""An untyped function parameter keeps each written call's type.

TradingView compiles a function whose parameter has no declared type once
per argument type: ``s(x) => str.tostring(x)`` spells ``s(2.5)`` as "2.5"
beside ``s(5)``'s "5", and ``twice(x) => x * 2`` returns 2.5 for
``twice(1.25)``. PineForge emitted one body typed from the first call
(``std::string s(int x)``), so every later float argument was narrowed to an
int: ``s(2.5)`` read "2", ``twice(1.25)`` 2.

A plain function whose untyped parameter receives two primitive families at
its written calls is now emitted once per call site, each typed from its own
call (the per-call-site variants a stateful function gets), and so is a
function it forwards that parameter to, unless the copies (one per call
path) outgrow the script: more than 64 and four per written call, as a
diamond of forwarding helpers or a deep forwarding chain called many times
does. The function gaining the most copies and those it is reached from
keep one body each and warn; flat calls and unrelated functions keep their
variants.
TradingView's tape of ``fixtures/silent2_tv/cgs2_untyped_params`` spells
every value on each close.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile, transpile_full
from tests._compile import compile_cpp
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "silent2_tv"
NAME = "cgs2_untyped_params"


def test_the_untyped_parameter_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {NAME: Build(source(NAME, FIXTURES))})[NAME]
    tape = tape_exits(NAME, FIXTURES)
    assert len(tape) == 336
    assert all(s.startswith("5|2.5|6|2.5|07|0") for s in tape.values())
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])
    print(f"untyped params: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


def _script(body: str) -> str:
    return ('//@version=6\nstrategy("untyped")\n' + body
            + '\nif bar_index > 0\n    strategy.entry("L", strategy.long)\n')


def test_each_call_gets_its_own_typed_body():
    cpp = transpile(_script(
        "s(x) => str.tostring(x)\n"
        "a = s(5)\n"
        "b = s(2.5)\n"))
    assert "std::string s_cs0(int64_t x)" in cpp
    assert "std::string s_cs1(double x)" in cpp
    assert "a = s_cs0(5);" in cpp and "b = s_cs1(2.5);" in cpp
    compile_cpp(cpp)


def test_a_forwarded_parameter_is_typed_per_path():
    cpp = transpile(_script(
        "twice(x) => x * 2\n"
        "wrap(y) => twice(y) + 1\n"
        "k = wrap(2)\n"
        "w = wrap(0.75)\n"))
    assert "double twice_cs1(double x)" in cpp
    assert "double wrap_cs1(double y)" in cpp
    assert "double w = " in cpp
    compile_cpp(cpp)


@pytest.mark.parametrize("body", [
    # One family at every call: one shared body, as before.
    "s(x) => str.tostring(x)\na = s(5)\nb = s(6)\n",
    # A float first and an int later: the int is widened into the float
    # body, which keeps its value.
    "s(x) => str.tostring(x)\nb = s(2.5)\na = s(5)\n",
    # A declared type is authoritative.
    "s(float x) => str.tostring(x)\na = s(5)\nb = s(2.5)\n",
])
def test_calls_of_one_family_keep_one_body(body):
    cpp = transpile(_script(body))
    assert "s_cs0" not in cpp and "s_cs1" not in cpp
    compile_cpp(cpp)


def test_a_call_path_that_cannot_split_keeps_its_first_type_and_warns():
    # Two textual calls of ``s`` inside ``g`` are shared by both of g's
    # variants: the int path and the float path meet in one variant of s.
    # The script compiled before (with the float narrowed); it still does,
    # with a warning naming the conversion.
    full = transpile_full(_script(
        "s(x) => str.tostring(x)\n"
        "g(y) => s(y) + s(1)\n"
        "a = g(1)\n"
        "b = g(2.5)\n"))
    assert "std::string g_cs1(double y)" in full["cpp"]
    assert any("Untyped parameter 'x' of callable 's' receives int and float"
               in d.message for d in full["diagnostics"])
    compile_cpp(full["cpp"])


def test_a_diamond_of_forwarding_helpers_keeps_one_body_each():
    # Each function is emitted once per call path through the functions the
    # family makes stateful: 2**depth copies of f0 (depth 8: 512, 4339 lines;
    # depth 16: 42 MB in 42 s). Past 64 copies the script keeps its shared
    # bodies, typed from the first call, and warns.
    depth = 8
    helpers = "f0(x) => x * 2\n" + "".join(
        f"f{k}(x) => f{k - 1}(x) + f{k - 1}(x)\n" for k in range(1, depth + 1))
    full = transpile_full(_script(
        helpers + f"a = f{depth}(1)\nb = f{depth}(1.5)\nplot(a + b)\n"))
    cpp = full["cpp"]
    assert "__ni" not in cpp and "_cs1" not in cpp
    assert any(f"Untyped parameter 'x' of callable 'f{depth}' receives int and "
               "float at its written calls; a copy per call path would exceed 64"
               in d.message for d in full["diagnostics"])
    compile_cpp(cpp)


def test_flat_calls_and_unrelated_functions_keep_their_variants():
    # Seventy flat calls are seventy copies, one per call, not a blow-up:
    # each keeps its own type (s(close) returns the double). Beside a
    # diamond whose copies outgrow the script (1022 for 90 written calls),
    # which keeps one body per function, the unrelated t keeps its variants
    # and no warning names it.
    flat = "".join(f"v{k} = s({'close' if k % 2 else 'bar_index'})\n"
                   for k in range(70))
    diamond = "f0(x) => x * 2\n" + "".join(
        f"f{k}(x) => f{k - 1}(x) + f{k - 1}(x)\n" for k in range(1, 9))
    full = transpile_full(_script(
        "s(x) => x * 2\n" + flat + "t(x) => x * 2\n" + diamond
        + "a = f8(1)\nb = f8(1.5)\nc = t(2)\nd = t(2.5)\n"
        + "plot(v1 + a + b + c + d)\n"))
    cpp = full["cpp"]
    assert "double s_cs69(double x)" in cpp
    assert "double t_cs1(double x)" in cpp
    assert "f0_cs1" not in cpp and "__ni" not in cpp
    warned = [d.message for d in full["diagnostics"] if "would exceed 64" in d.message]
    assert len(warned) == 1 and "callable 'f8'" in warned[0]
    compile_cpp(cpp)


def test_flat_calls_forwarding_the_parameter_keep_their_variants():
    # Seventy flat calls of s, which forwards x to g: seventy copies of each,
    # one per written call, not a blow-up.
    flat = "".join(f"v{k} = s({'close' if k % 2 else 'bar_index'})\n"
                   for k in range(70))
    full = transpile_full(_script(
        "g(y) => y * 2\ns(x) => g(x)\n" + flat + "plot(v1 + v2)\n"))
    cpp = full["cpp"]
    assert "double s_cs69(double x)" in cpp and "double g_cs69(double y)" in cpp
    assert not any("would exceed 64" in d.message for d in full["diagnostics"])
    compile_cpp(cpp)


def test_a_deep_forwarding_chain_called_often_keeps_one_body_each():
    # 30 forwarding levels called 100 times: 3000 copies for 130 written
    # calls. Past four copies per written call the chain keeps its bodies.
    depth, calls = 30, 100
    chain = "f0(x) => x * 2\n" + "".join(
        f"f{k}(x) => f{k - 1}(x)\n" for k in range(1, depth))
    flat = "".join(f"v{i} = f{depth - 1}({'close' if i % 2 else 'bar_index'})\n"
                   for i in range(calls))
    full = transpile_full(_script(chain + flat + "plot(v1)\n"))
    assert "_cs1" not in full["cpp"]
    assert any(f"callable 'f{depth - 1}'" in d.message and "would exceed 64" in d.message
               for d in full["diagnostics"])
    compile_cpp(full["cpp"])
