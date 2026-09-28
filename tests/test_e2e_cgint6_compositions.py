"""Where lane CG-SILENT-2 meets codegen main's rules (integration CGINT6).

CG-SILENT-2 was cut before CGINT5a and CG-SESSION-REPIN reached main, and
three of its items change a function one of those lanes changed too:

- item 1 binds ``color.new`` / ``color.rgb`` keyword arguments, and
  CG-SESSION-REPIN hands a transparency that is not an integer literal to the
  engine as a double: a keyword transparency takes a positional one's
  conversion (``_whole_transparency`` reads the bound argument);
- item 3 computes a run-time integer ``+ - *`` past int32 in 64 bits, and
  CGINT5a folds one over constants into a 64-bit literal: operands the C++
  spells as int literals keep the fold, on the chart and in a payload, and the
  run-time product applies to the rest (``_visit_binop``, the payload's
  ``BinOp``);
- item 3's width is a provenance keyed by spelling, and CGINT5a keeps a
  constant's width out of ``request.security`` helper state: a helper
  ``var``'s state family is read in the epoch-only mode
  (``_security_helper_var_state_type``), so a helper's ``var int n`` beside
  another function's ``n = days * 86400000`` keeps the ``int`` family main
  compiled (both refused it, "helper-local var state currently supports only
  int, float, bool and string values").

TradingView's tape of ``fixtures/cgint6_tv/cgint6_compositions`` spells each
composition on every close, item 5c's nested helper call over CGINT5a's
helper ``var`` TA length and item 5a's color arrays included; the probe
compiles on neither parent (main: a color ``array.from`` braced into doubles;
the lane: the helper's ``c := math.min(c + 1, 5)`` recursed). Its ``a0``, a
constant ``color.new`` transparency, is the one divergence, which
``cgint6_const_transp`` isolates: TradingView truncates a constant
``color.new`` transparency where main's rule reads it through the alpha byte.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "cgint6_tv"
NAME = "cgint6_compositions"
FIELDS = ("ca", "cb", "cc", "a0", "a1", "w", "p", "hh", "cnt", "gv")
CONST = "cgint6_const_transp"
VAR_SOURCE = "cgint6_var_source"
# The constant color.new transparencies of cgint6_const_transp: TradingView
# truncates them; the engine reads each through the alpha byte.
TRUNCATED = {0: "11", 1: "11", 2: "11", 3: "11", 4: "11", 5: "11",
             8: "11", 9: "11", 11: "100", 12: "51", 15: "11"}
HEAD = '//@version=6\nstrategy("cgint6")\n'


def _field_mismatches(tape: dict[int, str], exits: dict[int, str]) -> dict[int, set]:
    """Per field index, the (tape, engine) pairs that differ."""
    out: dict[int, set] = {}
    for ms, signal in tape.items():
        engine = exits.get(ms)
        assert engine is not None, f"no engine exit at {ms}"
        for i, (tv, pf) in enumerate(zip(signal.split("|"), engine.split("|"))):
            if tv != pf:
                out.setdefault(i, set()).add((tv, pf))
    return out


@pytest.fixture(scope="module")
def replays(tmp_path_factory) -> dict[str, dict[int, str]]:
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("cgint6")
    return replay(engine, base, {NAME: Build(source(NAME, FIXTURES)),
                                 CONST: Build(source(CONST, FIXTURES)),
                                 VAR_SOURCE: Build(source(VAR_SOURCE, FIXTURES))})


def test_the_composition_tape_replays(replays):
    tape = tape_exits(NAME, FIXTURES)
    assert len(tape) == 312
    differ = _field_mismatches(tape, replays[NAME])
    # Every composition reads as TradingView's on every exit; a0 alone, a
    # constant color.new transparency in a var array.from, is truncated on
    # TradingView (10) and read through the alpha byte here (11).
    assert set(differ) == {FIELDS.index("a0")}, {FIELDS[i]: sorted(v)[:3] for i, v in differ.items()}
    assert differ[FIELDS.index("a0")] == {("10", "11")}
    print(f"cgint6 compositions: {len(tape)} exits, every field but a0 equals TradingView's")


def test_a_constant_color_new_transparency_is_truncated_on_tradingview(replays):
    """Pinned divergence (main's rule, not a CGINT6 composition): TradingView
    truncates a constant ``color.new`` transparency; an input, a series and a
    ``color.rgb`` constant go through the alpha byte, as the engine does."""
    tape = tape_exits(CONST, FIXTURES)
    assert len(tape) == 336
    assert set(tape.values()) == {"10|10|10|10|10|10|11|20|10|10|0|99|50|11|11|10|11"}
    differ = _field_mismatches(tape, replays[CONST])
    assert {i: {pf for _tv, pf in v} for i, v in differ.items()} == {
        i: {pf} for i, pf in TRUNCATED.items()}


def test_a_var_source_input_keeps_its_first_bar(replays):
    """CG-SILENT-2 item 5e, re-checked on main: ``var src =
    input.source(close)`` read with history emitted raw Pine (``input``) that
    did not compile until CGINT5a ran a history-read ``var``'s initializer at
    its declaration. It holds the first bar's value on the chart and on the
    requested bars, as TradingView's tape shows. Pinned: ``ta.sma(src, 5)`` on
    the chart reads na, where TradingView reads the var's value --
    ``_is_precalc_replayed_source_var`` takes the ``var`` for a replayed
    source input, so ``precalculate()`` computes the site over the member
    before any bar has run (main 76a5b26 reads the same)."""
    tape = tape_exits(VAR_SOURCE, FIXTURES)
    assert len(tape) == 312
    assert set(tape.values()) == {"1821.47|1822.625|0|1821.47|1831.2|1831.2|-1.155"}
    differ = _field_mismatches(tape, replays[VAR_SOURCE])
    assert differ == {6: {("-1.155", "NaN")}}


@pytest.mark.parametrize("keyword, positional, conversion", [
    ("color.new(color.red, transp = 10.5)", "color.new(color.red, 10.5)", "(double)_pf_color_v"),
    ("color.new(transp = 40, color = #336699)", "color.new(#336699, 40)", "(int)_pf_color_v"),
    ("color.rgb(10, 20, 30, transp = k + 0.5)", "color.rgb(10, 20, 30, k + 0.5)", "(double)_pf_color_v"),
    ("color.rgb(blue = 30, red = 10, green = 20, transp = 60)", "color.rgb(10, 20, 30, 60)", "(int)_pf_color_v"),
])
def test_a_keyword_transparency_takes_the_positional_conversion(keyword, positional, conversion):
    def cpp_of(call: str) -> str:
        cpp = transpile(HEAD + f"k = bar_index % 50\nx = color.t({call})\nplot(x)\n")
        line = next(l for l in cpp.splitlines() if l.strip().startswith("x = "))
        return line.strip()

    assert cpp_of(keyword) == cpp_of(positional)
    assert conversion in cpp_of(keyword)


def test_a_constant_product_keeps_its_fold_beside_a_run_time_one():
    cpp = transpile(HEAD + "const int MS = 7200000\nvar int q = 400\nq += 1\n"
                    "w = q * 7200000 + 400 * MS\nplot(w)\n")
    line = next(l.strip() for l in cpp.splitlines() if l.strip().startswith("w = "))
    # CGINT5a's fold spells the constant product; CG-SILENT-2 computes the
    # script variable's product in 64 bits, na-aware.
    assert "static_cast<int64_t>(2880000000LL)" in line
    assert "auto _pf_wide_l = (q); auto _pf_wide_r = (7200000);" in line
    assert "(400 * 7200000)" not in line and "(q * 7200000)" not in line
    compile_cpp(cpp)


def test_a_payload_keeps_the_fold_beside_the_requested_bar_index_product():
    cpp = transpile(HEAD + "int step = 2 * 60 * 60 * 1000\n"
                    'p = request.security(syminfo.tickerid, "60", bar_index * 7200000 + 400 * step)\n'
                    "plot(p)\n")
    payload = next(l.strip() for l in cpp.splitlines() if l.strip().startswith("_req_sec_0 = "))
    assert payload == ("_req_sec_0 = ((static_cast<int64_t>(_sec0_bar_index_) * 7200000) "
                       "+ static_cast<int64_t>(2880000000LL));")
    compile_cpp(cpp)


@pytest.mark.parametrize("body", [
    # Another function's local of the helper var's spelling, a 64-bit product.
    'days = input.int(30)\nh() =>\n    var int n = 0\n    n += 1\n    n\n'
    'g() =>\n    n = days * 86400000\n    n\n'
    'x = request.security(syminfo.tickerid, "60", h())\nplot(x + g())\n',
    # The same over a constant product (CGINT5a folds it).
    'const int MS = 7200000\nh() =>\n    var int n = 0\n    n += 1\n    n\n'
    'g() =>\n    n = 400 * MS\n    n\n'
    'x = request.security(syminfo.tickerid, "60", h())\nplot(x + g())\n',
    # A script variable of that spelling.
    'days = input.int(30)\nn = days * 86400000\nh() =>\n    var int n = 0\n    n += 1\n    n\n'
    'x = request.security(syminfo.tickerid, "60", h())\nplot(x + n)\n',
])
def test_a_helper_var_keeps_its_state_family_beside_a_wide_product(body):
    cpp = transpile(HEAD + body)
    compile_cpp(cpp)


def test_a_helper_var_an_epoch_reaches_through_its_spelling_stays_refused():
    # As on main: the epoch provenance reaches the helper's var, whose state
    # family holds no int64_t.
    with pytest.raises(CompileError, match="helper-local var state currently supports only"):
        transpile(HEAD + 'h() =>\n    var int n = 0\n    n += 1\n    n\n'
                  'g() =>\n    n = time\n    n\n'
                  'x = request.security(syminfo.tickerid, "60", h())\nplot(x + g())\n')


def test_a_nested_helper_call_keeps_a_var_length_per_call():
    cpp = transpile(HEAD + "u(_x) =>\n    var int c = 1\n    c := math.min(c + 1, 5)\n"
                    "    ta.highest(_x, c)\n"
                    'x = request.security(syminfo.tickerid, "60", u(u(close)))\nplot(x)\n')
    # Item 5c gives each written call its own requested TA state; CGINT5a
    # reads each call's var length on the requested bars.
    evaluator = cpp[cpp.index("void _eval_security_0"):cpp.index("void evaluate_security")]
    members = {tok for tok in evaluator.replace("(", " ").replace(".", " ").split()
               if tok.startswith("_sec0__ta_highest_")}
    assert len(members) == 2, sorted(members)
    compile_cpp(cpp)
