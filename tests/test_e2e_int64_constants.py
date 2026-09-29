"""Integer arithmetic over constants keeps Pine's 64 bits.

Pine's ``int`` is 64-bit: ``400 * 7200000`` is 2880000000 on TradingView.
The codegen folded an int-literal-only ``+ - *`` tree past int32 into a
64-bit literal, but a constant name was emitted as its literal after that
check, so ``400 * MS`` over ``const int MS = 7200000`` (or over ``int step =
2 * 60 * 60 * 1000``) became the C++ ``int`` product ``(400 * 7200000)``,
which overflows to -1414967296. A ``request.security`` payload expands a
global into its declaration and folded nothing at all. A name bound to such
a value, or to a literal past int32 (``g = 3000000000``,
``var int e = 300 * MS``), was stored in a 32-bit ``int``.

Lane W5B-ENG-MARGIN-RESIDUAL's probes spelled their time windows ``rel <
n * step``: with ``n = 400`` the window was negative and no entry was ever
placed. TradingView's tape of ``fixtures/silent_tv/cgs_int64_const`` spells
every shape on each close, and the window admits every entry of the week.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._security_tapes import mismatches, replay, source, tape_exits
from tests._e2e import Build, skip_unless_e2e_env

FIXTURES = Path(__file__).parent / "fixtures" / "silent_tv"
NAME = "cgs_int64_const"
WIDE = "2880000000|2880000000|2880000000|2160000000|2160000000|3000000000|4000000000|-2160000000|2880000000"


def test_the_int64_constant_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {NAME: Build(source(NAME, FIXTURES))})[NAME]
    tape = tape_exits(NAME, FIXTURES)
    assert len(tape) == 336 and set(tape.values()) == {WIDE}
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])
    print(f"int64 constants: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


def test_constant_products_fold_to_64_bit_literals():
    cpp = transpile(source(NAME, FIXTURES))
    for spelling in ("(400 * 7200000)", "(300 * 7200000)", "(2000000000 + 2000000000)"):
        assert spelling not in cpp, spelling
    assert "static_cast<int64_t>(2880000000LL)" in cpp
    for declaration in ("int64_t e;", "int64_t g = 0;"):
        assert declaration in cpp, declaration


def test_a_payload_folds_the_global_it_expands():
    cpp = transpile(
        "//@version=6\n"
        'strategy("payload constants")\n'
        "int step = 2 * 60 * 60 * 1000\n"
        "const int MS = 7200000\n"
        'x = request.security(syminfo.tickerid, "60", time + 400 * step)\n'
        'y = request.security(syminfo.tickerid, "60", close * (400 * MS))\n'
        "if x > time and y > 0\n"
        '    strategy.entry("L", strategy.long)\n'
    )
    evaluators = cpp[cpp.index("void _eval_security_0"):cpp.index("void evaluate_security")]
    assert "(400 * (((2 * 60) * 60) * 1000))" not in evaluators
    assert "(400 * 7200000)" not in evaluators
    assert evaluators.count("static_cast<int64_t>(2880000000LL)") == 2
    compile_cpp(cpp)


@pytest.mark.parametrize("body, spelling", [
    # A product that fits int32 keeps its spelling byte for byte.
    ("int step = 2 * 60 * 60 * 1000\nx = 120 * step\nplot(x)", "(120 * 7200000)"),
])
def test_narrow_arithmetic_keeps_its_spelling(body, spelling):
    assert spelling in transpile('//@version=6\nstrategy("narrow")\n' + body)


def test_arithmetic_over_a_variable_is_not_folded():
    # ``n`` is no constant, so nothing folds it; past int32 its product is
    # computed at run time in 64 bits, na-aware since a script variable can
    # be na (CG-SILENT-2 item 3: the C++ int product ``(n * 7200000)`` this
    # used to pin overflowed at n = 401).
    cpp = transpile('//@version=6\nstrategy("narrow")\n'
                    "var int n = 400\nn += 1\nx = n * 7200000\nplot(x)")
    assert "(n * 7200000)" not in cpp and "LL)" not in cpp
    assert "auto _pf_wide_l = (n); auto _pf_wide_r = (7200000);" in cpp
    compile_cpp(cpp)


def test_a_parameter_named_like_a_constant_is_not_folded():
    # ``MS`` inside ``f`` is the parameter, not the constant it shadows.
    cpp = transpile(
        "//@version=6\n"
        'strategy("shadow")\n'
        "const int MS = 7200000\n"
        "f(int MS) => 400 * MS\n"
        "plot(f(2))\n"
    )
    assert "static_cast<int64_t>(2880000000LL)" not in cpp
    # The parameter's product is CG-SILENT-2 item 3's run-time 64-bit one.
    assert "auto _pf_wide_l = (400); auto _pf_wide_r = (MS);" in cpp


@pytest.mark.parametrize("body, kept", [
    # A request.security helper's var state holding a constant past int32
    # keeps the int slot it compiled with (its state map holds int, float,
    # bool and string values).
    ('h() =>\n    var int v = 3000000000\n    v := v + 1\n    v\n'
     'x = request.security(syminfo.tickerid, "60", h())\nplot(x)', "int v = na<int>();"),
    # So does one sharing its spelling with another callable's local.
    ('h() =>\n    var int n = 0\n    n += 1\n    n\ng() =>\n    n = 3000000000\n    n\n'
     'x = request.security(syminfo.tickerid, "60", h())\nplot(x + g())', "int n = na<int>();"),
    # An int array a literal past int32 is pushed to keeps its element type,
    # which an array<int> parameter binds to.
    ('var arr = array.new_int()\narray.push(arr, 3000000000)\n'
     'f(array<int> a) => array.size(a)\nplot(f(arr))', "std::vector<int> arr;"),
])
def test_constant_width_widens_only_its_own_slot(body, kept):
    cpp = transpile('//@version=6\nstrategy("constant width")\n' + body)
    assert kept in cpp
    compile_cpp(cpp)


def test_a_product_past_int64_is_not_folded():
    # float constants holding integral values are inlined as int literals;
    # 1e27 has no int64 literal, so the product keeps its spelling.
    cpp = transpile('//@version=6\nstrategy("past int64")\n'
                    "float big = 1000000000\ny = big * big * big\nplot(y)")
    assert "1000000000000000000000000000LL" not in cpp
    compile_cpp(cpp)


def test_a_reassigned_top_level_constant_keeps_64_bits():
    cpp = transpile(
        "//@version=6\n"
        'strategy("reassigned constants")\n'
        "const int MS = 7200000\n"
        "e = 300 * MS\n"
        "e := e + 1\n"
        "int d = 3000000000\n"
        "d := d + 1\n"
        "plot(e + d)\n"
    )
    assert "int64_t e = 0;" in cpp and "int64_t d = 0;" in cpp
    compile_cpp(cpp)


def test_a_local_named_like_a_wide_constant_keeps_its_int_na():
    # ``g`` in f is the function's int local: its slot, and the na a
    # math.round of na narrows to, are int's, not the global g's int64_t.
    cpp = transpile(
        "//@version=6\n"
        'strategy("local shadow")\n'
        "g = 3000000000\n"
        "f(float src) =>\n"
        "    int g = 0\n"
        "    g := math.round(src)\n"
        "    g\n"
        "plot(na(f(close[5000])) ? 1 : 0)\n"
    )
    body = cpp[cpp.index("int f(double src)"):]
    body = body[:body.index("\n    }\n")]
    assert "na<int64_t>()" not in body and "na<int>()" in body
    compile_cpp(cpp)
