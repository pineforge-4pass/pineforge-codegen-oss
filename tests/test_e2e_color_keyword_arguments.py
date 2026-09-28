"""``color.*`` keyword arguments bind to TradingView's parameter names.

``color.new(color, transp)``, ``color.rgb(red, green, blue, transp)`` and
``color.r|g|b|t(color)`` read their positional arguments only, so a keyword
was dropped silently: ``color.new(#1E90FF, transp = 40)`` lowered to the
color ``0`` (black, fully transparent), ``color.rgb(10, 20, 30, transp = 80)``
to an opaque color, and ``color.new(color = c, transp = 60)`` to ``0``.

TradingView's tape of ``fixtures/silent2_tv/cgs2_color_keywords`` spells each
keyword form's channels and transparency beside its positional twin on every
close: they are equal, whatever order the keywords are written in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "silent2_tv"
NAME = "cgs2_color_keywords"


def test_the_color_keyword_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {NAME: Build(source(NAME, FIXTURES))})[NAME]
    tape = tape_exits(NAME, FIXTURES)
    assert len(tape) == 336
    # Every keyword color equals its positional twin on TradingView.
    assert all(signal.split("|")[0] == signal.split("|")[7]
               and signal.split("|")[2] == signal.split("|")[8]
               for signal in tape.values())
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])
    print(f"color keywords: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


def _rhs(cpp: str, name: str) -> str:
    """The C++ the script body assigns to ``name``."""
    marker = f"        {name} = "
    start = cpp.index(marker) + len(marker)
    return cpp[start:cpp.index(";\n", start)]


@pytest.mark.parametrize("keyword, positional", [
    ("color.new(#1E90FF, transp = 40)", "color.new(#1E90FF, 40)"),
    ("color.new(color = #1E90FF, transp = 40)", "color.new(#1E90FF, 40)"),
    ("color.new(transp = 40, color = #1E90FF)", "color.new(#1E90FF, 40)"),
    ("color.new(color = base, transp = 60)", "color.new(base, 60)"),
    ("color.rgb(10, 20, 30, transp = 80)", "color.rgb(10, 20, 30, 80)"),
    ("color.rgb(blue = 7, red = 5, green = 6, transp = 40)", "color.rgb(5, 6, 7, 40)"),
    ("color.rgb(red = 5, green = 6, blue = 7)", "color.rgb(5, 6, 7)"),
    ("color.r(color = base)", "color.r(base)"),
    ("color.t(color = base)", "color.t(base)"),
])
def test_a_keyword_call_lowers_like_its_positional_twin(keyword, positional):
    def script(call):
        return ("//@version=6\n"
                'strategy("color keywords")\n'
                "base = close > open ? #1E90FF : #FF8C00\n"
                f"x = {call}\n"
                "if color.t(x) > 0\n"
                '    strategy.entry("L", strategy.long)\n')

    keyword_cpp = transpile(script(keyword))
    positional_cpp = transpile(script(positional))
    assert _rhs(keyword_cpp, "x") == _rhs(positional_cpp, "x")
    assert _rhs(keyword_cpp, "x") != "0"
    compile_cpp(keyword_cpp)


def test_positional_color_calls_keep_their_spelling():
    cpp = transpile(
        "//@version=6\n"
        'strategy("positional colors")\n'
        "a = color.new(#1E90FF, 40)\n"
        "b = color.rgb(10, 20, 30)\n"
        "if color.t(a) + color.r(b) > 0\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    assert _rhs(cpp, "a").startswith("pine_color::new_color(0xff1E90FFLL, ")
    assert _rhs(cpp, "b") == (
        "pine_color::new_color(static_cast<int64_t>("
        "(static_cast<uint64_t>(10) & 0xFFULL) << 16 | "
        "(static_cast<uint64_t>(20) & 0xFFULL) << 8 | "
        "(static_cast<uint64_t>(30) & 0xFFULL)), 0)")
