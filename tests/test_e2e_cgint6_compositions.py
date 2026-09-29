"""Where lane CG-SILENT-2 meets codegen main's rules (integration CGINT6).

CG-SILENT-2 was cut before CGINT5a and CG-SESSION-REPIN reached main, and
several of its items meet a rule one of those lanes made:

- item 1 binds ``color.new`` / ``color.rgb`` keyword arguments, and
  CG-SESSION-REPIN hands a transparency that is not an integer literal to the
  engine as a double: a keyword transparency takes a positional one's
  conversion (``_whole_transparency`` reads the bound argument);
- item 3 computes a run-time integer ``+ - *`` past int32 in 64 bits, and
  CGINT5a folds one over constants into a 64-bit literal: operands the C++
  spells as int literals keep the fold, on the chart and in a payload, and the
  run-time product applies to the rest (``_visit_binop``, the payload's
  ``BinOp``); a reassigned top-level constant past int32 (CGINT5a's
  ``int64_t`` slot) is no 32-bit operand (``_narrow_int_name_info``), and an
  input's named bound reads the top-level constant (``_input_int_bound``);
- item 3's width is a provenance keyed by spelling, and CGINT5a keeps a
  constant's width out of ``request.security`` helper state: a helper
  ``var``'s state family is read in the epoch-only mode
  (``_security_helper_var_state_type``), so a helper's ``var int n`` beside
  another function's ``n = days * 86400000`` keeps the ``int`` family main
  compiled (both refused it, "helper-local var state currently supports only
  int, float, bool and string values");
- a payload's copy of a script variable only item 3's arithmetic makes wide is
  ``int64_t``, as its chart slot, and the double form narrows na-preserving
  into it (``_security_copy_is_arithmetic_wide``): typed ``int``, the copy
  read that double as undefined behaviour (quirk 9).

TradingView's tape of ``fixtures/cgint6_tv/cgint6_compositions`` spells each
composition on every close, item 5c's nested helper call over CGINT5a's
helper ``var`` TA length and item 5a's color arrays included; the probe
compiles on neither parent (main: a color ``array.from`` braced into doubles;
the lane: the helper's ``c := math.min(c + 1, 5)`` recursed). Two divergences
of main's rules are pinned, each with its own tape: TradingView truncates a
constant ``color.new`` transparency (``cgint6_const_transp``; the
composition tape's ``a0``), and ``ta.sma(src, 5)`` over ``var src =
input.source(...)`` reads na (``cgint6_var_source``, CG-SILENT-2 item 5e).
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
    """Per field index, the (tape, engine) pairs that differ. Up to the
    tape's last exit (the replay's feed runs past it) the engine exits at
    exactly the tape's instants, each with as many fields."""
    last = max(tape)
    inside = {ms for ms in exits if ms <= last}
    assert inside == set(tape), (
        f"{len(set(tape) - inside)} tape exits missing, "
        f"{len(inside - set(tape))} engine exits not on the tape")
    out: dict[int, set] = {}
    for ms, signal in tape.items():
        tv_fields, pf_fields = signal.split("|"), exits[ms].split("|")
        assert len(tv_fields) == len(pf_fields), (signal, exits[ms])
        for i, (tv, pf) in enumerate(zip(tv_fields, pf_fields)):
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
    # Pinned limitation, as on main: the helper's own ``n`` never holds an
    # epoch, but the epoch provenance is keyed by spelling, so another
    # function's ``n = time`` types it int64_t, a family its state lacks.
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



def test_a_reassigned_wide_constant_is_no_32_bit_operand():
    # CGINT5a types g's slot int64_t (its declaration is past int32): the
    # product is int64 arithmetic, as on main, not the na-aware double form
    # of a 32-bit operand (exact to 2**53 only).
    cpp = transpile(HEAD + "var int g = 3000000001\ng := g + 0\n"
                    "var int z = 3000000000\nz := g * 30000001\nplot(z)\n")
    assert "z = (g * 30000001);" in cpp
    compile_cpp(cpp)


def test_an_input_bound_named_by_a_constant_reads_it_everywhere():
    # maxval = MAXD keeps days * 1000000 within int32 at the top level and in
    # a function whose parameter shadows MAXD alike (inside f the bound read
    # the parameter, found no constant and widened the product).
    cpp = transpile(HEAD + "MAXD = 2000\ndays = input.int(30, minval = 1, maxval = MAXD)\n"
                    "a = days * 1000000\nf(MAXD) => days * 1000000 + MAXD\nplot(a + f(1))\n")
    assert "a = (days * 1000000);" in cpp
    assert "return ((days * 1000000) + MAXD);" in cpp
    compile_cpp(cpp)


def test_a_compound_modulo_of_a_wide_product_in_a_payload_compiles():
    # w %= q * 7200000 read in a payload: the double form went into C++
    # ``%`` on the lane (``invalid operands``), which main compiled; it
    # computes through std::fmod as the chart does, and narrows.
    cpp = transpile(HEAD + "var int q = 0\nif bar_index > 5\n    q := bar_index\n"
                    "var int w = 1\nw %= q * 7200000\n"
                    'x = request.security(syminfo.tickerid, "60", w)\nplot(x)\n')
    body = cpp[cpp.index("void _eval_security_0"):cpp.index("void evaluate_security")]
    store = next(l.strip() for l in body.splitlines()
                 if l.strip().startswith("_sec0_w = ") and "_pf_wide_l" in l)
    assert store.startswith("_sec0_w = [&](){ auto _pf_v = (std::fmod((double)(_sec0_w), (double)(")
    compile_cpp(cpp)


def test_a_payload_copy_narrows_the_product_of_a_global_it_expands():
    # w := a over a = q * 7200000: the payload expands a into its product,
    # the double form, which w's own value does not spell.
    cpp = transpile(HEAD + "var int q = 0\nif bar_index > 5\n    q := bar_index\n"
                    "a = q * 7200000\nvar int w = 0\nw := a\n"
                    'x = request.security(syminfo.tickerid, "60", w)\nplot(x)\n')
    assert "int64_t _sec0_w = 0;" in cpp
    body = cpp[cpp.index("void _eval_security_0"):cpp.index("void evaluate_security")]
    store = next(l.strip() for l in body.splitlines()
                 if l.strip().startswith("_sec0_w = ") and "_pf_wide_l" in l)
    assert store.startswith("_sec0_w = [&](){ auto _pf_v = ([&]() -> double { auto _pf_wide_l = (_sec0_q);")
    assert store.endswith("return is_na(_pf_v) ? na<int64_t>() : (int64_t)_pf_v; }();")
    compile_cpp(cpp)


def test_a_block_local_shadowing_a_wide_constant_keeps_its_product_64_bit():
    # The width scan cannot tell the block's int g from the top-level wide g:
    # z holds the 64-bit product either way (2160000072 at bar 72).
    cpp = transpile(HEAD + "var int g = 3000000001\ng := g + 0\nvar int z = 0\n"
                    "if bar_index > 1\n    int g = bar_index\n    z := g * 30000001\nplot(z)\n")
    assert "int64_t z;" in cpp
    compile_cpp(cpp)


def test_a_payload_copy_of_an_arithmetic_wide_variable_holds_64_bits():
    cpp = transpile(HEAD + "var int q = 0\nif bar_index > 5\n    q := bar_index\n"
                    "var int w = 0\nw := q * 7200000\nw += q * 7200000\n"
                    "var int t0 = 0\nt0 := time\n"
                    'x = request.security(syminfo.tickerid, "60", w + t0)\nplot(x)\n')
    # w is wide only by the 64-bit product: its copy is int64_t, and both
    # stores narrow the na-aware double form na-preserving.
    assert "int64_t _sec0_w = 0;" in cpp
    body = cpp[cpp.index("void _eval_security_0"):cpp.index("void evaluate_security")]
    stores = [l.strip() for l in body.splitlines()
              if l.strip().startswith("_sec0_w = ") and "_pf_wide_l" in l]
    assert len(stores) == 2 and all(
        "return is_na(_pf_v) ? na<int64_t>() : (int64_t)_pf_v; }()" in l for l in stores), stores
    # t0 is wide by an epoch: its copy keeps the int every earlier build
    # emitted (a truncation, pinned).
    assert "int _sec0_t0 = 0;" in cpp
    compile_cpp(cpp)
