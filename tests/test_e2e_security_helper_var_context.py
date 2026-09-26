"""Helper-local ``var`` state inside ``request.security``, against TradingView.

A helper called in a ``request.security`` payload runs on the requested
bars: its ``var`` state belongs to that call and advances once per requested
bar, whatever the chart does. TradingView's tape of ``sec2_var_ctx``
(``fixtures/security2_tv``) pins it: one counter helper, reset at 00:00 UTC,
called in three payloads (60, 240, and 60 with ``lookahead_on``) and on the
chart, counts hours, 4-hour bars, the current hour and 15-minute bars
respectively; a helper latching the last up-candle close reads the requested
candles, and at ``[1]`` under ``lookahead_on`` the previous requested bar's.

The existing lowering (``_security_helper_series_``: pushed on a new requested
slot, rolled back to ``[1]`` on a recomputation) already reproduces all 265
Signals; this pins it for the helper shapes the lane opens (tuple
declarations, mixed tuples, string state).
"""

from __future__ import annotations

from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits


def test_helper_var_state_is_per_call_and_per_requested_bar(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("security2_var_ctx")
    exits = replay(engine, base, {"probe": Build(source("sec2_var_ctx"))})
    tape = tape_exits("sec2_var_ctx")
    assert len(tape) == 265
    # The close sent on the 01:15 UTC bar (exit at 01:30): the 00:00 hour
    # has completed (1), no 4-hour bar yet (na), lookahead_on reads the 01:00
    # hour (2), and the chart's count, 1 on every 00:xx bar, is 3.
    assert tape[1743471000000].startswith("1|na|2|3|")
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    print(f"helper var state: {len(tape)} of {len(tape)} exit Signals equal TradingView's")
