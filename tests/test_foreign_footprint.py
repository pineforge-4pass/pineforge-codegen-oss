"""A footprint of another symbol reads its pinned feed's delta column.

``request.security(sym, tf, request.footprint(ticks, va))`` followed by
``.delta()`` (or ``footprint.delta(fp)``) is the footprint delta of the
requested bar. The requests manifest pins it as a named column of the
symbol's feed, ``fp_delta_<ticks>_<va>`` (lane XSYM-B), and the engine hands
a foreign site its bar's column (``security_column_value``, lane XSYM-D). It
was a deferred refusal, and ``fp.delta()`` did not compile (a double has no
member). A footprint value is now its delta; every other ``footprint.*``
member stays refused by name, and a feed without the column is missing data:
the request's reads stop the run.

TradingView's footprint delta of a range depends on the range (month slices
of one year disagree), so a feed is pinned for the window it was captured
over: a synthetic BITSTAMP:BTCUSD delta filter on BINANCE:BTCUSDT 15 over
the capture's year books its 1674 trades exactly on it (kept outside this
repository with the capture).
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests._e2e import chart_feed_head, skip_unless_e2e_env
from tests._requests import build, hourly, run, same, write_root


HEAD = '//@version=6\nstrategy("xe footprint", overlay = true)\n'
REQUEST = 'footprint fp = request.security("PF:A", "15", request.footprint(100, 70))\n'
TRADE = ('if bar_index % 2 == 0 and d > 0\n    strategy.entry("L", strategy.long)\n'
         'if bar_index % 2 == 1\n    strategy.close("L")\n')
PINNED = "no data is pinned for this request, and its value was read"
M15 = 15 * 60_000


def test_footprint_delta_reads_the_feed_column():
    result = transpile_full(HEAD + REQUEST + 'float d = not na(fp) ? fp.delta() : na\n'
                            'float d2 = footprint.delta(fp)\n' + TRADE
                            + 'if d2 < 0\n    strategy.close("L")\n')
    cpp = result["cpp"]
    assert '_req_sec_0 = _pf_symbol_column(0, "fp_delta_100_70");' in cpp
    assert '_pf_symbol_data_installed(_pf_symbol, "15", "fp_delta_100_70")' in cpp
    assert "double fp = " in cpp and ".delta()" not in cpp
    compile_cpp(cpp, label="foreign-footprint-delta")


@pytest.mark.parametrize("member", ["poc()", "buy_volume()", "rows()"])
def test_other_footprint_members_stay_refused_by_name(member):
    with pytest.raises(CompileError, match=re.escape(f"footprint.{member.split('(')[0]}(...) "
                                                     "is not supported.")):
        transpile(HEAD + REQUEST + f'd = fp.{member}\n' + TRADE)
    with pytest.raises(CompileError, match="footprint"):
        transpile(HEAD + REQUEST + f'd = footprint.{member.split("(")[0]}(fp)\n' + TRADE)


def test_footprint_of_non_literal_rows_keeps_the_deferred_refusal():
    """The column's name spells the ticks per row: a series or input keys
    none, and the request stays a deferred refusal (it transpiles)."""
    result = transpile_full(HEAD + 'rows = input.int(100, "Rows")\n'
                            'footprint fp = request.security("PF:A", "15", '
                            'request.footprint(rows, 70))\nd = fp.delta()\n' + TRADE)
    assert any("no data is pinned" in d.message for d in result["diagnostics"])
    assert "register_security_eval" not in result["cpp"]


def test_footprint_delta_on_the_requested_bars(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_footprint")
    feed_path = chart_feed_head(engine, base, 160)
    opens = [int(line.split(",", 1)[0]) for line in feed_path.read_text().splitlines()[1:]]
    other = hourly("PF:A", opens[0] - 2 * M15, 170, 15)
    deltas = [round(((i * 53) % 29) - 14.5 + i * 0.001, 6) for i in range(len(other.bars))]
    other.columns["fp_delta_100_70"] = deltas
    source = ("// @pf-trace pf_d=d\n" + HEAD + REQUEST
              + 'float d = not na(fp) ? fp.delta() : na\n' + TRADE)
    work = base / "xe-footprint"
    build(source, work)
    result = run(engine, work, feed_path, write_root(base, "xe-footprint", "BINANCE:ETHUSDT",
                                                    "15", [other]))
    assert result.ok, result.error
    for chart_open in opens:
        visible = [i for i, bar in enumerate(other.bars) if bar[1] <= chart_open + M15]
        want = deltas[visible[-1]] if visible else float("nan")
        assert same(result.trace[chart_open]["pf_d"], want), chart_open
    # A feed of the symbol without the column is missing data.
    del other.columns["fp_delta_100_70"]
    result = run(engine, work, feed_path, write_root(base / "bare", "xe-footprint",
                                                    "BINANCE:ETHUSDT", "15", [other]))
    assert not result.ok
    # Line 4: the trace pragma is line 1.
    assert (f'request.security("PF:A", "15", ...) at line 4: {PINNED}') in result.error
