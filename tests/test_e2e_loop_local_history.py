"""A series variable declared inside a loop commits one history slot per bar.

TradingView commits ONE history slot per bar to a variable declared in a loop
body, holding the LAST evaluation of that bar, and every `y[k]` read of the bar
(k >= 1) reads the slot committed k bars back. A build that pushed a slot on
every iteration made `y[i]` read the previous iterations of the same bar.

Each source below uses r = 100 + 10 * bar_index and a loop that declares `y`;
the value rows hold the traced sum `s` of every chart bar to numbers written
here from that arithmetic (for the declaration `float y = r + i` the slot
committed on bar j holds r(j) + 3, the value of the last iteration i = 3). The
sum is na while a read reaches before the first bar. Nothing is read from the
code under test.
"""

from __future__ import annotations

import math

from tests._e2e import Build, chart_feed_head, execute_all, skip_unless_e2e_env

HEADER = '//@version=6\nstrategy("Loop local history")\n'
FOOTER = "plot(s)\n// @pf-trace s=s\n"
NAN = float("nan")


def _r(j: int) -> float:
    return 100.0 + 10.0 * j


def _source(declarations: str) -> str:
    return HEADER + "float r = 100.0 + 10.0 * bar_index\n" + declarations + FOOTER


def _for_sum(t: int) -> float:
    # s += y[i] for i = 1 to 3: the slot committed i bars back, r(t - i) + 3.
    return sum(_r(t - i) + 3.0 for i in (1, 2, 3)) if t >= 3 else NAN


def _previous_slot_three_times(t: int) -> float:
    # Three reads of y[1] (or of the slot one bar back): 3 * (r(t - 1) + 3).
    return 3.0 * (_r(t - 1) + 3.0) if t >= 1 else NAN


def _two_declarations_reached(t: int) -> float:
    # The declaration is reached at i = 1 and i = 3 only; both read y[1].
    return 2.0 * (_r(t - 1) + 3.0) if t >= 1 else NAN


def _reassigned_in_the_loop(t: int) -> float:
    # y is declared, then raised by 10 in the same iteration: the slot
    # committed on bar j holds r(j) + 3 + 10; three reads of y[1].
    return 3.0 * (_r(t - 1) + 13.0) if t >= 1 else NAN


def _tuple_slot_three_times(t: int) -> float:
    # `a` is the first element of [v + 1, v + 2] for v = r + i: the slot
    # committed on bar j holds r(j) + 3 + 1; three reads of a[1].
    return 3.0 * (_r(t - 1) + 4.0) if t >= 1 else NAN


def _outside_a_loop(t: int) -> float:
    # One declaration per bar, outside any loop: y[1] is r(t - 1) + 1.
    return _r(t - 1) + 1.0 if t >= 1 else NAN


# (id, the declarations, the traced sum on chart bar t)
ROWS = [
    ("for-local",
     "float s = 0.0\nfor i = 1 to 3\n    float y = r + i\n    s += y[i]\n", _for_sum),
    ("while-local",
     "float s = 0.0\nint i = 1\nwhile i <= 3\n    float y = r + i\n    s += y[i]\n    i += 1\n",
     _for_sum),
    ("for-in-local",
     "float s = 0.0\narr = array.from(1.0, 2.0, 3.0)\nfor v in arr\n    float y = r + v\n    s += y[1]\n",
     _previous_slot_three_times),
    ("conditional-local",
     "float s = 0.0\nfor i = 1 to 3\n    if i % 2 == 1\n        float y = r + i\n        s += y[1]\n",
     _two_declarations_reached),
    ("function-loop-local",
     "loopSum(float base) =>\n    float total = 0.0\n    for i = 1 to 3\n        float y = base + i\n"
     "        total += y[i]\n    total\nfloat s = loopSum(r)\n",
     _for_sum),
    ("reassigned-in-the-loop",
     "float s = 0.0\nfor i = 1 to 3\n    float y = r + i\n    y += 10.0\n    s += y[1]\n",
     _reassigned_in_the_loop),
    ("tuple-in-the-loop",
     "pair(float v) => [v + 1.0, v + 2.0]\nfloat s = 0.0\nfor i = 1 to 3\n    [a, b] = pair(r + i)\n    s += a[1]\n",
     _tuple_slot_three_times),
    ("outside-a-loop-neighbour",
     "float y = r + 1.0\nfloat s = y[1]\n",
     _outside_a_loop),
]


def _same(got: float, want: float) -> bool:
    return (math.isnan(got) and math.isnan(want)) or abs(got - want) < 1e-9


def test_a_loop_local_commits_one_history_slot_per_bar(tmp_path):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 1500)
    builds = {name: Build(_source(declarations), trace=True) for name, declarations, _ in ROWS}
    runs = execute_all(engine, feed, tmp_path / "runs", builds)
    problems = []
    for name, _declarations, value in ROWS:
        outcome = runs[name]
        if outcome.error:
            problems.append(f"[{name}] {outcome.error}")
            continue
        observed = {r["bar_index"]: r["value"] for r in outcome.traces["default"] if r["name"] == "s"}
        assert len(observed) == 1500, (name, len(observed))
        wrong = [(bar, observed[bar], value(bar)) for bar in sorted(observed)
                 if not _same(observed[bar], value(bar))]
        defined = [v for v in observed.values() if not math.isnan(v)]
        # Non-vacuity: the series has many defined values and they move.
        assert len(set(defined)) > 50, (name, len(set(defined)))
        if wrong:
            problems.append(
                f"[{name}] {len(wrong)} of 1500 chart bars differ from the arithmetic of the source; "
                f"first (chart bar, engine, expected): {wrong[:3]}")
    assert not problems, "\n".join(problems)
