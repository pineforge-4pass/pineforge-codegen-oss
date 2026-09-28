"""A ``var`` input as a request.security helper's TA length reads its getter.

``var float vf = input.float(6.0, "VF")`` holds the input's value on every
bar. As the TA length of a helper a payload inlines (``g(close,
int(vf))``), the requested TA object is built by the runtime reset, which
``evaluate_security`` runs before ``on_bar`` has initialized ``vf``: the
reset read the ``na`` member, built ``ta::SMA((int)vf)`` with a length of
INT_MIN, and the run segfaulted (P1 of lane CG-SECURITY-2's review). A plain
input read there already used its getter; ``_security_var_input_call`` now
gives a never-reassigned ``var`` input the same getter.

TradingView's tape of ``fixtures/open_items_tv/sec_var_input_len`` (lab tv
--no-note, BINANCE:ETHUSDT.P 15, 265 closes) spells the helper with the
``var`` input length, bare and under ``nz``, beside the helper with the
literal 6; the replay compares all 265 closes, and an override of the input
builds the length it names.
"""

from __future__ import annotations

from pineforge_codegen import transpile
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay
from tests._tv_tapes import FIXTURES, exits_by_time

NAME = "sec_var_input_len"


def test_the_var_input_length_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    engine_exits = replay(engine, tmp_path, {NAME: Build((FIXTURES / f"{NAME}.pine").read_text())})[NAME]
    tape = exits_by_time(NAME)
    assert len(tape) == 265
    missed = mismatches(tape, engine_exits)
    assert not missed, f"{len(missed)} of {len(tape)}: {missed[:3]}"


def test_an_override_resizes_the_requested_indicator(tmp_path):
    engine = skip_unless_e2e_env()
    source = (FIXTURES / f"{NAME}.pine").read_text()
    literal = source.replace('g(close, int(vf))', 'g(close, 9)')
    runs = replay(engine, tmp_path, {"override": Build(source), "literal": Build(literal)},
                  params={"override": {"VF": "9"}})
    fields = lambda exits: {ms: s.split("|")[:2] for ms, s in exits.items()}
    assert fields(runs["override"]) == fields(runs["literal"])


def test_the_requested_constructor_reads_the_getter():
    cpp = transpile((FIXTURES / f"{NAME}.pine").read_text())
    ctors = [l for l in cpp.splitlines() if "_sec0__ta_sma" in l and "ta::SMA(" in l]
    assert ctors and all('get_input_double("VF", 6.0)' in l for l in ctors), ctors
