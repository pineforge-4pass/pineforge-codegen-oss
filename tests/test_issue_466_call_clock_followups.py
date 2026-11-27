"""#466 follow-ups: a conditional helper call has one clock and one state.

The review of the #466 change left notes on what a conditional
``request.security`` helper call still got wrong. Each test here has one of
them as its target:

* a ``ta.*`` site the evaluator computed where it opens advanced with the
  requested bar, while ``x[1]`` beside it counted the call's completed runs
  (N1);
* a ``var`` kept the seed of the first run that reached it, even when that
  run was a provisional one the completed evaluation replaced (N3);
* a call a helper reads in two arms was lowered once per arm, each lowering
  with its own state behind the call's single flag (N5).

No value here is TradingView's. As in the #466 rows, the expectations are the
issue's call-site rule on a synthetic feed: there is no stored fixture and no
capture date.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


_START = 1_743_465_600_000
_BAR_MS = 15 * 60_000
_TIMES = [_START + i * _BAR_MS for i in range(16)]
_TAKEN = (0, 2, 7, 8, 12, 15)

_DRIVER = r"""
#include <iomanip>
#include <iostream>
#include <vector>
struct Observer : GeneratedStrategy {
    void evaluate_security(int id, const Bar& bar, bool complete) override {
        GeneratedStrategy::evaluate_security(id, bar, complete);
        if (id == 0)
            std::cout << bar.timestamp << ' ' << complete << ' ' << bar.close << ' '
                      << bar.high << ' ' << _req_sec_0 << '\n';
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


def _rows(source, highs, label):
    """Evaluator rows ``(requested bar, complete, close, high, value)``.

    The #466 feed: three five-minute children per requested bar, the first
    taking the opposite branch from the completed bar, so a call can run on
    a provisional evaluation only, or first on a later evaluation of its bar.
    ``highs(i)`` gives the highs of the three children of requested bar ``i``.
    """
    bars = [
        f"Bar{{10, {highs(i)[child]}, 9, {11 if take else 9}, 1, {timestamp + child * 300_000}LL}}"
        for i, timestamp in enumerate(_TIMES)
        for child, take in enumerate((i not in _TAKEN, i in _TAKEN, i in _TAKEN))
    ]
    output = run_emitted_tu(
        transpile(source), _DRIVER.replace("BARS", ",\n".join(bars)), opt="-O2", label=label)
    raw = [line.split() for line in output.splitlines()]
    assert all(len(row) == 5 and row[1] in ("0", "1") for row in raw), (
        f"NOT COMPARED: expected five columns with a completion flag; received {raw}"
    )
    index = {t: i for i, t in enumerate(_TIMES)}
    observed = {int(row[0]) for row in raw}
    assert observed == index.keys(), (
        f"NOT COMPARED: evaluator opens {sorted(observed)}, expected {_TIMES}"
    )
    rows = [(index[int(t)], flag == "1", float(close), float(high), float(value))
            for t, flag, close, high, value in raw]
    completed = [i for i, complete, *_ in rows if complete]
    assert completed == list(range(len(_TIMES))), (
        f"NOT COMPARED: completed bars {completed}, expected one each of {len(_TIMES)}"
    )
    completed_takes = [i for i, complete, close, *_ in rows if complete and close > 10]
    assert completed_takes == list(_TAKEN), (
        f"NOT COMPARED: completed calls {completed_takes}, expected {_TAKEN}"
    )
    reversals = {(i in _TAKEN, close > 10) for i, complete, close, *_ in rows if not complete}
    assert {(True, False), (False, True)} <= reversals, (
        f"NOT COMPARED: expected both provisional branch reversals, observed {reversals}"
    )
    return rows


# --- N1 ---------------------------------------------------------------------

_TA_HELPER = """//@version=6
strategy("Call clock ta")
f(float x) => ta.sma(x[1], 2) * 1000 + x[1]
"""

# name -> (script, how many times the payload adds the call's value). Both
# keep the ta.sma site out of the lazy inline form: the evaluator computed
# it where it opens, on every requested bar.
_CALL_CLOCK_TA = {
    # A multi-statement helper anywhere in the payload keeps every ta.* site
    # in the opening block.
    "beside-a-multi-statement-helper": (
        _TA_HELPER + """g() =>
    a = high - low
    a
out = request.security(syminfo.tickerid, "15", (close > open ? nz(f(high), -1) : -2) + g() * 0, lookahead=barmerge.lookahead_on)
plot(out)
""",
        1,
    ),
    # ``v + v`` reaches the site twice: one value, one advance per run.
    "reached-twice": (
        _TA_HELPER + """twice(float v) => v + v
out = request.security(syminfo.tickerid, "15", close > open ? nz(twice(f(high)), -1) : -2, lookahead=barmerge.lookahead_on)
plot(out)
""",
        2,
    ),
}


@pytest.mark.parametrize("name", sorted(_CALL_CLOCK_TA))
def test_hoisted_ta_follows_the_call_clock(name):
    # Requested bar i has high 20 + i. At a run of the call, x[1] is the high
    # of the last completed call and ta.sma(x[1], 2) the mean of that and of
    # the completed call before it: both count completed calls. The value
    # keeps them apart, 1000 * mean + x[1].
    source, reaches = _CALL_CLOCK_TA[name]
    rows = _rows(source, lambda i: (20 + i,) * 3, f"ta.* on a conditional call's clock ({name})")
    for i, complete, close, _high, value in rows:
        prior = [j for j in _TAKEN if j < i]
        if close <= 10:
            expected = -2
        elif len(prior) < 2:
            expected = -1
        else:
            lagged = 20 + prior[-1]
            expected = reaches * ((lagged + 20 + prior[-2]) / 2 * 1000 + lagged)
        part = value / reaches
        assert value == expected, (
            f"bar {i} complete={complete}: value {value}, expected {expected}; "
            f"as 1000 * mean + x[1] it reads mean {(part - part % 500) / 1000}, "
            f"x[1] {part % 500}; completed calls before this bar {prior}"
        )


# --- N3 ---------------------------------------------------------------------

# The ``var`` reaches the conditional call through a global's initializer:
# under a helper's own ternary it is refused, before #466 and now
# (tests/test_issue_466_sparse_call_review.py).
_VAR_SEED = """//@version=6
strategy("Call clock var seed")
first(int x) =>
    var float seed = high
    x[1] > 0 ? seed : seed
kept = close > open ? first(time) : -2
out = request.security(syminfo.tickerid, "15", kept, lookahead=barmerge.lookahead_on)
plot(out)
"""


def test_var_seed_comes_from_the_confirmed_run():
    # A requested bar's high rises with each child, so the provisional run
    # that first reaches the call reads a lower high than the completed one.
    # Until a run is confirmed the initializer runs again; from then on the
    # ``var`` keeps the confirmed run's high.
    rows = _rows(
        _VAR_SEED, lambda i: (20 + 10 * i, 21 + 10 * i, 22 + 10 * i),
        "var seed on a conditional call")
    first = _TAKEN[0]
    provisional = [high for i, complete, close, high, _ in rows
                   if i <= first and close > 10 and not complete]
    confirmed = [high for i, complete, close, high, _ in rows
                 if i == first and close > 10 and complete]
    assert provisional and len(confirmed) == 1 and provisional[0] != confirmed[0], (
        f"NOT COMPARED: provisional highs {provisional} before the first confirmed "
        f"run, confirmed highs {confirmed}; the seed needs two different values"
    )
    for i, complete, close, high, value in rows:
        if close <= 10:
            expected = -2
        elif i <= first:
            expected = high
        else:
            expected = confirmed[0]
        assert value == expected, (
            f"bar {i} complete={complete} high={high}: seed {value}, expected {expected}; "
            f"first provisional high {provisional[0]}, confirmed high {confirmed[0]}"
        )


# --- N5 ---------------------------------------------------------------------

# ``pick`` reads its argument in two arms, so the one written call
# ``calls(time)`` is reached at two places of the lowered expression.
_ONE_CALL_TWO_ARMS = """//@version=6
strategy("Call clock reads")
calls(int x) =>
    float c = 0.0
    c := nz(c[1], 0.0) + 1.0
    x[1] > 0 ? c : c
pick(float v) => high > 30 ? v : v + 1000
out = request.security(syminfo.tickerid, "15", close > open ? pick(calls(time)) : -2, lookahead=barmerge.lookahead_on)
plot(out)
"""


def test_a_call_read_in_two_arms_keeps_one_state():
    # The first child of a requested bar has high 20 and the later ones 40:
    # a provisional run reads the call through the ``+ 1000`` arm and a
    # completed run through the other. Both are the same call, so each run
    # sees the count its completed runs left, plus itself.
    rows = _rows(_ONE_CALL_TWO_ARMS, lambda i: (20, 40, 40), "one call read in two arms")
    arms = {(complete, high > 30) for _, complete, close, high, _ in rows if close > 10}
    assert {(False, False), (True, True)} <= arms, (
        "NOT COMPARED: expected a provisional run through the low-high arm and a "
        f"completed run through the other; observed (complete, high > 30) {sorted(arms)}"
    )
    for i, complete, close, high, value in rows:
        count = sum(j < i for j in _TAKEN) + 1
        if close <= 10:
            expected = -2
        else:
            expected = count if high > 30 else count + 1000
        assert value == expected, (
            f"bar {i} complete={complete} high={high}: value {value}, expected {expected} "
            f"(completed calls before this bar {count - 1})"
        )
