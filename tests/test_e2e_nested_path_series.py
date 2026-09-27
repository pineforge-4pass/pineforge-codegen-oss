"""Every call path of a nested helper keeps its own history-read locals.

A helper's local read through history (``float f = 0.0`` then ``f := k *
nz(f[1]) + s``) is persistent Series state, one per Pine call path. When the
helper is reached through a second call of its caller, the codegen mints a
context-sensitive instance (``filt__ni1``) with fresh copies of the path's
``var`` and fixnan state, but not of those locals: every such instance kept
writing the original member, so the paths shared one series. cs-lev-
tradleware-gaussian-channel-stochrsi-eth calls its Gaussian filter
``f_pole`` twice, for the price and for the true range; the second path's
nine filters all wrote the price filter's ``_f``, the channel band sat about
20 true ranges wide and the script never traded (lane W2, F04, hidden behind
the filter's former ``return 0.0``).

The shape runs end to end beside the same filter spelled out once per call
path, each copy called once: identical per-bar values and trades.
"""

from __future__ import annotations

import pytest

from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)


HEAD = '//@version=6\nstrategy("w2 nested path series", overlay = true)\n'
FILTER = """{name}(float s, float k) =>
    float {f} = 0.0
    {f} := k * nz({f}[1]) + s
"""
SHAPE = HEAD + FILTER.format(name="filt", f="f") + """pole(float s) =>
    [filt(s, 0.5), filt(s, 0.25)]
[a1, a2] = pole(close)
[b1, b2] = pole(open)
"""
# Distinct local names: two callables' history locals of one name share a
# generated member, which the codegen refuses.
REFERENCE = HEAD + "".join(FILTER.format(name=f"filt{i}", f=f"f{i}") for i in range(1, 5)) + """a1 = filt1(close, 0.5)
a2 = filt2(close, 0.25)
b1 = filt3(open, 0.5)
b2 = filt4(open, 0.25)
"""
TAIL = """// @pf-trace a1=a1
// @pf-trace a2=a2
// @pf-trace b1=b1
// @pf-trace b2=b2
if ta.crossover(a1 - a2, b1 - b2)
    strategy.entry("L", strategy.long)
if ta.crossunder(a1 - a2, b1 - b2)
    strategy.close("L")
"""


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("w2_nested_series")
    feed = chart_feed_head(engine, base, 3000)
    return execute_all(engine, feed, base, {
        "shape": Build(SHAPE + TAIL, trace=True),
        "reference": Build(REFERENCE + TAIL, trace=True)})


def test_each_call_path_keeps_its_own_filter_state(runs):
    print("nested series:", assert_same_runs(ok(runs, "shape"), ok(runs, "reference")))
