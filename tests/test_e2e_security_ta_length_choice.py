"""A TA length chosen by comparing an ``input.string`` with its options.

``emaLen = sensitivity == "Reactive" ? 8 : sensitivity == "Balanced" ? 21 :
...`` passed through a helper into ``request.security`` was refused
("Unsupported requested-context TA constructor length ... the helper-bound
expression is not a stable per-run scalar"), and on the chart the same length
failed the chart's TA constructor guard. The class-scope spelling of a derived
length (``_arith_expr_to_str``) had no form for a string literal, so the name
was never tracked as an input-derived scalar; the constructor reset could
not re-read it either, because its identifier scans read the literal's
contents (``Reactive``) as names. String literals are now spelled and blanked
out of those scans: both indicators are built from the override-aware
``get_input_string`` comparison, the requested one included.

TradingView's tape of ``sec2_ta_len_choice`` (``fixtures/security2_tv``,
default "B" -> SMA 10) matches on all 265 exits; an override picking "C"
runs exactly like the same script with the literal 20.
"""

from __future__ import annotations

from pineforge_codegen import transpile
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

LITERAL_20 = source("sec2_ta_len_choice").replace(
    'len = mode == "A" ? 5 : mode == "B" ? 10 : 20', "len = 20")


def test_input_chosen_length_matches_the_tape_and_its_override(tmp_path_factory):
    assert LITERAL_20 != source("sec2_ta_len_choice")
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("security2_ta_len_choice")
    exits = replay(
        engine, base,
        {"probe": Build(source("sec2_ta_len_choice")),
         "override": Build(source("sec2_ta_len_choice")),
         "literal": Build(LITERAL_20)},
        params={"override": {"Mode": "C"}},
    )
    tape = tape_exits("sec2_ta_len_choice")
    assert len(tape) == 265
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    assert exits["override"] == exits["literal"]
    assert exits["override"] != exits["probe"]
    print(f"input-chosen TA length: {len(tape)} of {len(tape)} exit Signals equal "
          f"TradingView's; Mode=C equals SMA 20 on {len(exits['literal'])} exits")


def test_both_indicators_are_reset_from_the_input_comparison():
    cpp = transpile(source("sec2_ta_len_choice"))
    choice = ('get_input_string("Mode", std::string("B")) == std::string("A")')
    resets = [line for line in cpp.splitlines() if "ta::SMA(" in line and choice in line]
    # The chart helper's site and the requested one, each reset from the input.
    assert any(line.strip().startswith("_ta_sma_") for line in resets), resets
    assert any(line.strip().startswith("_sec0_") for line in resets), resets
