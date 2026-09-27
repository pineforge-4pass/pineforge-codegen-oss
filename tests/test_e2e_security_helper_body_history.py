"""History of a helper call inside a helper body a request.security payload
inlines reads the requested bar's history.

``h() => nz(g()[1])`` in a payload: TradingView evaluates ``g()[1]`` in the
requested context, the previous requested bar's value. The prepass that
gives a payload's ``f()[1]`` its requested history walked the payload and
the globals it reads, not the helper bodies it inlines, so the builder
refused the bare ``k() => g()[1]`` ("helper call history is only supported
in the payload itself"), and under a builtin the expression visitor looked
for a chart history member the evaluator has none of and raised an
AssertionError (lane CG-SECURITY-2's pre-existing defects). The prepass now
walks the user functions the payload calls too, and keeps a helper body's
history where the payload reaches it once; one it reaches twice (``h() +
h()``) would need a series per inline and is refused where it is lowered, a
located refusal where the visitor used to crash.

TradingView's tape of ``fixtures/open_items_tv/sec_helper_body_hist`` (lab
tv --no-note, BINANCE:ETHUSDT.P 15, 265 closes) spells ``h()``, ``k()`` and
``close[1]`` requested on "60" on every close; the replay compares all 265.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay
from tests._tv_tapes import FIXTURES, exits_by_time

NAME = "sec_helper_body_hist"


def test_the_helper_body_history_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    engine_exits = replay(engine, tmp_path, {NAME: Build((FIXTURES / f"{NAME}.pine").read_text())})[NAME]
    tape = exits_by_time(NAME)
    assert len(tape) == 265
    missed = mismatches(tape, engine_exits)
    assert not missed, f"{len(missed)} of {len(tape)}: {missed[:3]}"


HEAD = '//@version=6\nstrategy("helper body history", overlay = true)\ng() => close\n'


@pytest.mark.parametrize("helper", ["h() => nz(g()[1])", "h() => g()[1]"])
def test_a_helper_inlined_twice_is_refused_where_its_history_is_lowered(helper):
    with pytest.raises(CompileError) as info:
        transpile(HEAD + helper + '\nv = request.security(syminfo.tickerid, "60", h() + h())\n'
                  "if v > 0\n    strategy.entry(\"L\", strategy.long)\n")
    errors = [d for d in info.value.diagnostics
              if "helper call history is only supported in the payload itself" in d.message]
    assert errors and errors[0].location.line == 4
