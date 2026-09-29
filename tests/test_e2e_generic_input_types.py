"""A generic ``input()`` is the type of its default (lane TAIL-E, item 3).

Pine v6 types ``input(defval)`` by ``defval``: ``input("x")`` is a string
input, ``input(3)`` an int one, ``input(true)`` a bool one. The codegen typed
every generic input's member ``double`` and read it through
``get_input_double``, so a string default assigned ``std::string`` to
``double`` and the TU did not compile (job-2936-samm1011-lobster-channel-breakout,
the one population script with a string default: its webhook secret). The
member and the getter now follow the default, as the inline read
(``_render_input_value``) already did.

TradingView's tape of ``te_generic_input`` (``fixtures/tail_e_tv``) spells
the string input and the int input in its orders -- ``str.tostring(n / 2)``
reads "1.5" -- and sizes them ``n * x`` = 1.5.
"""

from __future__ import annotations

import re

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import skip_unless_e2e_env
from tests._tail_e_tapes import (
    BAR_MS, DAY_MS, START_MS, build, engine_rows, feed, mismatches, source, tape_rows,
)

NAME = "te_generic_input"


def test_generic_inputs_take_their_defaults_types():
    cpp = transpile(source(NAME))
    for decl in ("std::string tag = ", "int n = ", "bool flag = ", "double x = "):
        assert decl in cpp, decl
    for read in ('tag = get_input_string("Tag", std::string("pf-tag"));',
                 'n = get_input_int("N", 3);',
                 'flag = get_input_bool("Flag", true);',
                 'x = get_input_double("X", 0.5);'):
        assert read in cpp, read
    compile_cpp(cpp, label="generic input types")


def test_a_string_default_generic_input_compiles_in_a_string_expression():
    cpp = transpile('//@version=6\nstrategy("s")\nsecret = input("k-1", "Secret")\n'
                    'msg = "{" + secret + "}"\n'
                    'if bar_index == 3\n    strategy.entry("L", strategy.long, comment = msg)\n')
    assert re.search(r"std::string secret = ", cpp)
    compile_cpp(cpp, label="generic string input in a string expression")


def test_the_generic_input_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    tape = tape_rows(f"{NAME}_eth15")
    assert len(tape) == 96
    chart = feed(engine, tmp_path, START_MS + DAY_MS + BAR_MS)
    rows, trades = engine_rows(engine, build(tmp_path, NAME, source(NAME)), chart)
    # Entries carry their id ("L"); closes spell the inputs.
    closes = {key: value for key, value in tape.items() if key[0] == "X"}
    missed = mismatches(closes, rows)
    assert not missed, f"{len(missed)} of {len(closes)} closes differ:\n" + "\n".join(missed[:5])
    assert {t["qty"] for t in trades} == {1.5}
