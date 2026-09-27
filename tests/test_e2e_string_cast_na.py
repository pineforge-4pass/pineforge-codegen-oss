"""``string(na)`` is Pine's cast of ``na`` to a string: the string na.

The richardgong1988 probes type an ``if``'s other arm with it,
``[fTop, fBot] = if useS3a or useS3b ... S.fractal() else [string(na),
string(na)]``, then read ``useS3a ? fTop : na``. Three gaps kept that from
compiling: the codegen lowered ``string(na)`` as ``str.tostring(na)``
("NaN", through a formatter a script without ``str.tostring`` does not
declare); the analyzer typed ``string(...)`` as the unknown-builtin FLOAT, so
the selection's arms disagreed (STRING and FLOAT) and the tuple went untyped;
and a string element of a selection tuple was declared ``double``.

TradingView's tapes of ``xc_string_cast_na`` and ``xc_string_tuple_select``
(``fixtures/xsym_lib_tv``, BINANCE:ETHUSDT.P 15) spell ``na()`` of each value
in every exit Signal, and PineForge reproduces all 96 of each.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "xsym_lib_tv"
HEAD = '//@version=6\nstrategy("T")\n'


def test_string_na_cast_is_the_string_na():
    cpp = transpile(HEAD + 's = string(na)\nif na(s)\n    strategy.entry("L", strategy.long)\n')
    assert "s = na<std::string>();" in cpp
    assert "pine_str_tostring_tv" not in cpp


def test_a_selection_tuple_of_strings_declares_strings():
    cpp = transpile(HEAD + 'f() => [close > open ? "A" : na, "B"]\n'
                    '[x, y] = if close > 0\n    f()\nelse\n    [string(na), string(na)]\n'
                    'z = close > 1 ? x : na\n'
                    'if na(z)\n    strategy.entry("L", strategy.long)\n')
    assert "std::string x = std::string(\"\");" in cpp
    assert "std::string y = std::string(\"\");" in cpp
    assert "? (x) : (na<std::string>())" in cpp


@pytest.mark.parametrize("name", ["xc_string_cast_na", "xc_string_tuple_select"])
def test_string_casts_replay_tradingview_tapes(tmp_path_factory, name):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp(name)
    exits = replay(engine, base, {"probe": Build(source(name, FIXTURES))})
    tape = {k: v for k, v in tape_exits(name, FIXTURES).items() if v}
    assert len(tape) == 96
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
