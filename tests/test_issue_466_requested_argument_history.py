"""#466: a requested helper parameter has the clock of its written call."""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu
from tests._e2e import reference_codegen, transpile_json


_BASE = "9b17c40e074a1bcab7ec581b5e71f13b171deb42"
_START = 1_743_465_600_000
_BAR_MS = 15 * 60_000
_TAKEN = (0, 2, 7, 8, 12, 15)

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


@pytest.mark.parametrize(("helpers", "lag", "field_lag", "depth"), [
    # [1] after a gap is the last call's argument: the read that decides
    # whether a signal fires on the first call after skipped bars.
    pytest.param("lag(int x) => x[1]", "lag(time)", 0, 1, id="direct-time-depth-1"),
    pytest.param("lag(int x) => x[2]", "lag(time)", 0, 2, id="direct-time-depth-2"),
    pytest.param("lag(int x) => x[3]", "lag(time)", 0, 3, id="direct-time"),
    # The two outer calls share one written inner call. Its history must
    # belong to each invocation path, and [1] is taken *before* sampling.
    pytest.param("lag(int x) => x[3]\nwrapped() => lag(time[1])",
                 "wrapped()", 1, 3, id="wrapped-shifted-time"),
])
def test_sparse_and_dense_requested_timestamps(helpers, lag, field_lag, depth):
    # TradingView's documented rule (Execution model, "Time series in
    # scopes"), not a TradingView plot or stored fixture (no capture date): a
    # written call keeps one entry per bar, committed only on a bar whose
    # closing evaluation ran the call, and [k] reads the last entry committed
    # as of k bars back. At bar 8, [3] is bar 5's entry at both sites: bar 5
    # itself at the dense site, and at the sparse site bar 2, the last call
    # up to bar 5 (the calls at bars 7 and 8 are later than the offset).
    # The first child takes the opposite branch from the completed bar: a
    # provisional call must neither consume history nor leave a phantom slot.
    times = [_START + i * _BAR_MS for i in range(16)]
    bars = [
        f"Bar{{10, 11, 9, {11 if take else 9}, 1, {timestamp + child * 300_000}LL}}"
        for i, timestamp in enumerate(times)
        for child, take in enumerate((i not in _TAKEN, i in _TAKEN, i in _TAKEN))
    ]
    source = _PINE.replace("lag(time)", lag).replace("lag(int x) => x[3]", helpers)
    output = run_emitted_tu(
        transpile(source), _DRIVER.replace("BARS", ",\n".join(bars)),
        opt="-O2", label="sparse and dense requested helper argument history",
    )
    rows = [line.split() for line in output.splitlines()]
    assert all(len(row) == 5 and row[1] in ("0", "1") for row in rows), (
        f"NOT COMPARED: expected five columns with a completion flag; received {rows}"
    )
    completed = [int(t) for t, complete, *_ in rows if complete == "1"]
    assert completed == times, f"NOT COMPARED: completed opens {completed}, expected {times}"
    index = {t: i for i, t in enumerate(times)}
    observed = {int(t) for t, *_ in rows}
    assert observed == index.keys(), (
        f"NOT COMPARED: evaluator opens {sorted(observed)}, expected {times}"
    )
    completed_takes = [index[int(t)] for t, flag, close, *_ in rows
                       if flag == "1" and float(close) > 10]
    assert completed_takes == list(_TAKEN), (
        f"NOT COMPARED: completed calls {completed_takes}, expected {_TAKEN}"
    )
    reversals = {(index[int(t)] in _TAKEN, float(close) > 10)
                 for t, flag, close, *_ in rows if flag == "0"}
    assert {(True, False), (False, True)} <= reversals, (
        f"NOT COMPARED: expected both provisional branch reversals, observed {reversals}"
    )
    for timestamp, complete, close, sparse, dense in rows:
        i = index[int(timestamp)]
        # The entry of bar i - depth: the last completed call up to that bar.
        committed = [j for j in _TAKEN if j <= i - depth]
        sparse_index = committed[-1] - field_lag if committed else -1
        dense_index = i - depth - field_lag
        expected_sparse = times[sparse_index] if sparse_index >= 0 else -1
        expected = (expected_sparse if float(close) > 10 else -2,
                    times[dense_index] if dense_index >= 0 else -1)
        assert (float(sparse), float(dense)) == expected, (i, complete, sparse, dense, expected)


def test_dense_helper_and_conditional_field_read_keep_base_cpp(tmp_path):
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
