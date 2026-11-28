"""#466: a requested helper parameter keeps one history entry per requested bar."""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu
from tests._e2e import reference_codegen, transpile_json


_BASE = "9b17c40e074a1bcab7ec581b5e71f13b171deb42"
_START = 1_743_465_600_000
_BAR_MS = 15 * 60_000
_TIMES = [_START + i * _BAR_MS for i in range(16)]
_TAKEN = (0, 2, 7, 8, 12, 15)
# The first call completes on bar 2: bars 0 and 1 close without one, so the
# parameter's history opens with two entries that hold no argument.
_FIRST_CALL_AT_2 = (2, 3, 7, 11, 12, 15)

_PINE = """//@version=6
strategy("Requested argument clock")
lag(int x) => x[3]
sample() =>
    sparse = close > open ? lag(time) : -2
    dense = lag(time)
    [nz(sparse, -1), nz(dense, -1)]
[s, d] = request.security(syminfo.tickerid, "15", sample(), lookahead=barmerge.lookahead_on)
plot(s)
plot(d)
"""

_DRIVER = r"""
#include <iomanip>
#include <iostream>
#include <vector>
struct Observer : GeneratedStrategy {
    void evaluate_security(int id, const Bar& bar, bool complete) override {
        GeneratedStrategy::evaluate_security(id, bar, complete);
        if (id == 0)
            std::cout << bar.timestamp << ' ' << complete << ' ' << bar.close << ' '
                      << std::get<0>(_req_sec_0) << ' ' << std::get<1>(_req_sec_0) << '\n';
    }
};
int main() {
    Observer strategy;
    std::cout << std::setprecision(17);
    std::vector<Bar> bars = {BARS};
    strategy.run(bars.data(), bars.size(), "5", "5");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 1;
    }
    return 0;
}
"""


def _evaluations(source, taken, label, high=lambda i: 11):
    """Every evaluation of the request, as ``(bar, complete, called, sparse,
    dense)``: 16 requested bars, three five-minute children each.

    The completed evaluation of a requested bar calls the conditional helper
    on ``taken`` only. The first child takes the opposite branch from the
    completed bar: a provisional call must neither consume history nor leave
    a phantom slot. ``high`` gives requested bar ``i`` its high.
    """
    bars = [
        f"Bar{{10, {high(i)}, 9, {11 if take else 9}, 1, {timestamp + child * 300_000}LL}}"
        for i, timestamp in enumerate(_TIMES)
        for child, take in enumerate((i not in taken, i in taken, i in taken))
    ]
    output = run_emitted_tu(
        transpile(source), _DRIVER.replace("BARS", ",\n".join(bars)),
        opt="-O2", label=label,
    )
    rows = [line.split() for line in output.splitlines()]
    assert all(len(row) == 5 and row[1] in ("0", "1") for row in rows), (
        f"NOT COMPARED: expected five columns with a completion flag; received {rows}"
    )
    completed = [int(t) for t, complete, *_ in rows if complete == "1"]
    assert completed == _TIMES, f"NOT COMPARED: completed opens {completed}, expected {_TIMES}"
    index = {t: i for i, t in enumerate(_TIMES)}
    observed = {int(t) for t, *_ in rows}
    assert observed == index.keys(), (
        f"NOT COMPARED: evaluator opens {sorted(observed)}, expected {_TIMES}"
    )
    completed_takes = [index[int(t)] for t, flag, close, *_ in rows
                       if flag == "1" and float(close) > 10]
    assert completed_takes == list(taken), (
        f"NOT COMPARED: completed calls {completed_takes}, expected {taken}"
    )
    reversals = {(index[int(t)] in taken, float(close) > 10)
                 for t, flag, close, *_ in rows if flag == "0"}
    assert {(True, False), (False, True)} <= reversals, (
        f"NOT COMPARED: expected both provisional branch reversals, observed {reversals}"
    )
    return [(index[int(t)], complete, float(close) > 10, float(sparse), float(dense))
            for t, complete, close, sparse, dense in rows]


@pytest.mark.parametrize(("helpers", "lag", "field_lag", "depth", "taken"), [
    # [1] after a gap is the last call's argument: the read that decides
    # whether a signal fires on the first call after skipped bars.
    pytest.param("lag(int x) => x[1]", "lag(time)", 0, 1, _TAKEN, id="direct-time-depth-1"),
    pytest.param("lag(int x) => x[2]", "lag(time)", 0, 2, _TAKEN, id="direct-time-depth-2"),
    pytest.param("lag(int x) => x[3]", "lag(time)", 0, 3, _TAKEN, id="direct-time"),
    # The two outer calls share one written inner call. Its history must
    # belong to each invocation path, and [1] is taken *before* sampling.
    pytest.param("lag(int x) => x[3]\nwrapped() => lag(time[1])",
                 "wrapped()", 1, 3, _TAKEN, id="wrapped-shifted-time"),
    # Bars 0 and 1 close before any call, so their entries hold no argument:
    # [1] at the first call (bar 2) and [6] at bar 7 (bar 1's entry) read na,
    # and a read that lands on bar 2 or later reads the last call up to it.
    pytest.param("lag(int x) => x[1]", "lag(time)", 0, 1, _FIRST_CALL_AT_2,
                 id="first-call-at-bar-2-depth-1"),
    pytest.param("lag(int x) => x[6]", "lag(time)", 0, 6, _FIRST_CALL_AT_2,
                 id="first-call-at-bar-2-depth-6"),
    # The measured read in a request: a float parameter read two back, on an
    # irregular schedule. Requested bar i has high 20 + i.
    pytest.param("lag(float x) => x[2]", "lag(high)", 0, 2, _FIRST_CALL_AT_2,
                 id="float-high-depth-2"),
])
def test_sparse_and_dense_requested_timestamps(helpers, lag, field_lag, depth, taken):
    # The rule is measured on TradingView charts, not quoted from the manual.
    # A float parameter read two back at a call made on every third bar reads,
    # on bar 7, the argument of bar 4's call, where two calls back would be
    # bar 1's; a script variable read through a call site reads the same way
    # (tests/fixtures/udt_history_tv/README.md, ``udth_clock``, and
    # tests/fixtures/function_global_history/README.md, stored captures). So
    # a written call keeps one entry per bar: the argument of its last
    # completed call, which a bar without the call copies, and no argument
    # before the first call. No capture was taken inside a request: these
    # rows apply the measured chart rule to requested bars on a synthetic
    # feed. At bar 8 of the first schedule, [3] is bar 5's entry at both
    # sites: bar 5 itself at the dense site, and at the sparse site bar 2,
    # the last call up to bar 5 (the calls at bars 7 and 8 are later than the
    # offset).
    by_high = "high" in lag
    values = [20 + i for i in range(16)] if by_high else _TIMES
    source = _PINE.replace("lag(time)", lag).replace("lag(int x) => x[3]", helpers)
    evaluations = _evaluations(
        source, taken, "sparse and dense requested helper argument history",
        high=(lambda i: 20 + i) if by_high else (lambda i: 11),
    )
    for i, complete, called, sparse, dense in evaluations:
        # The entry of bar i - depth: the last completed call up to that bar.
        committed = [j for j in taken if j <= i - depth]
        sparse_index = committed[-1] - field_lag if committed else -1
        dense_index = i - depth - field_lag
        expected_sparse = values[sparse_index] if sparse_index >= 0 else -1
        expected = (expected_sparse if called else -2,
                    values[dense_index] if dense_index >= 0 else -1)
        assert (sparse, dense) == expected, (i, complete, sparse, dense, expected)


def test_dense_helper_and_conditional_field_read_keep_base_cpp(tmp_path):
    # Neither helper declares a var; the conditional field read alone must
    # not change this control's C++ or move its reference commit.
    source = """//@version=6
strategy("Dense argument clock")
lag(float x) => x[2]
sample() =>
    dense = lag(high[1])
    close > open ? dense : low[2]
plot(request.security(syminfo.tickerid, "15", sample()))
"""
    reference = reference_codegen(_BASE)
    assert reference is not None, f"NOT COMPARED: base {_BASE} unavailable; head has source"
    path = tmp_path / "control.pine"
    path.write_text(source, encoding="utf-8")
    before = transpile_json(path, root=reference)
    assert before["ok"] and before["cpp"], f"NOT COMPARED: base produced no C++: {before}"
    assert transpile(source).encode() == before["cpp"].encode()
