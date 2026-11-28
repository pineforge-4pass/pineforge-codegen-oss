"""#466: values on the requested helper's first, skipped and repeated calls.

The expected values are calculated from the synthetic bars in the #466
driver. These are native regression rows, not TradingView captures.
"""

import pytest

from tests.test_issue_466_call_clock_followups import _rows, _TAKEN


@pytest.mark.parametrize("helper", [
    pytest.param("f(float x) => ", id="expression"),
    pytest.param("f(float src) =>\n    x = src + 0.0\n    ", id="local-source"),
])
def test_sparse_ta_result_history_counts_completed_calls(helper):
    # Neither TA reads x[1]: it is the TA result's own history that needs
    # a call clock. Encode the current and preceding averages separately.
    source = f"""//@version=6
strategy("Requested averages on completed calls")
{helper}nz(ta.sma(x, 5), -1) * 1000 + nz(ta.sma(x, 5)[1], -1)
out = request.security(syminfo.tickerid, "15", close > open ? f(high) : -2, lookahead=barmerge.lookahead_on)
plot(out)
"""
    rows = _rows(source, lambda i: (50 + 5 * i,) * 3, "conditional TA result history")

    def sma(samples):
        return sum(samples[-5:]) / 5 if len(samples) >= 5 else -1

    for i, complete, close, high, value in rows:
        prior = [50 + 5 * j for j in _TAKEN if j < i]
        current, previous = sma([*prior, high]), sma(prior)
        expected = current * 1000 + previous if close > 10 else -2
        assert value == expected, (
            f"bar {i} complete={complete}: {value=}, {expected=}; "
            f"call-clock SMA {current}, previous completed SMA {previous}, samples {prior}"
        )


@pytest.mark.parametrize("reach", ["sibling-blocks", "statements-around-call", "both-blocks"])
def test_separate_lowerings_of_one_written_call_share_state(reach):
    global_def, extra = "", ""
    result = "(x[1] > 0 ? c : c)"
    if reach == "sibling-blocks":
        # A local history cell alone needs a clock; x has no history read.
        # Each use is lowered into its own C++ scope.
        result = "c"
        picker = """pick(float v) =>
    float selected = 0.0
    if high > 30
        selected := v
    else
        selected := v + 1000
    selected
"""
    elif reach == "statements-around-call":
        # The first lowering of calls() needs a statement outside its lambda
        # to bind g. Even within a single scope its text cannot be reused.
        picker = "pick(float v) => high > 30 ? v : v + 1000\n"
        global_def = "scaled(float x) =>\n    a = x * 2\n    a\ng = scaled(high)\n"
        extra = " + 0 * g"
    else:
        # The first lowering is inside the block; the second is outside it.
        # Both can execute, but a completed call must still count only once.
        picker = """pick(float v) =>
    float part = 0.0
    if high > 30
        part := v
    part + v
"""
    source = f"""//@version=6
strategy("Two reaches of one requested call")
{global_def}calls(int x) =>
    float c = 0.0
    c := nz(c[1], 0.0) + 1.0
    {result}{extra}
{picker}out = request.security(syminfo.tickerid, "15", pick(close > open ? calls(time) : -2), lookahead=barmerge.lookahead_on)
plot(out)
"""
    def highs(i):
        last = 40 if reach != "both-blocks" or i % 2 == 0 else 24
        return 20, last, last

    rows = _rows(source, highs, f"two stateful reaches: {reach}")
    arms = {(complete, high > 30) for _, complete, close, high, _ in rows if close > 10}
    required = {(False, False), (True, True)}
    if reach == "both-blocks":
        required.add((True, False))
    assert required <= arms, (
        f"NOT COMPARED: required (complete, high arm) {required}, observed {arms}"
    )
    for i, complete, close, high, value in rows:
        count = 1 + sum(j < i for j in _TAKEN)
        called = count if close > 10 else -2
        if reach == "both-blocks":
            expected = called * (2 if high > 30 else 1)
        else:
            expected = called + (0 if high > 30 else 1000)
        assert value == expected, (
            f"{reach}, bar {i} complete={complete} high={high}: {value=}, {expected=}, "
            f"previous completed calls {count - 1}"
        )


def test_separate_written_calls_with_no_arguments_keep_distinct_ta_history():
    # Argument bindings alone cannot distinguish the two calls to prior().
    source = """//@version=6
strategy("Two written calls to one TA helper")
prior() => nz(ta.sma(high, 2)[1], -1)
out = request.security(syminfo.tickerid, "15", prior() * 1000 + (close > open ? prior() : -2), lookahead=barmerge.lookahead_on)
plot(out)
"""
    rows = _rows(source, lambda i: (20 + 2 * i,) * 3, "dense and sparse TA histories")
    for i, complete, close, _, value in rows:
        called = [j for j in _TAKEN if j < i]
        dense = 20 + (i - 1) + (i - 2) if i >= 2 else -1
        sparse = 20 + sum(called[-2:]) if len(called) >= 2 else -1
        expected = dense * 1000 + (sparse if close > 10 else -2)
        assert value == expected, (
            f"bar {i} complete={complete}: {value=}, {expected=}, dense {dense}, "
            f"sparse {sparse}, previous completed calls {called}"
        )


def test_dense_helper_var_seed_uses_confirmed_first_bar():
    source = """//@version=6
strategy("Confirmed requested seed")
first(float x) =>
    var float seed = x
    seed
out = request.security(syminfo.tickerid, "15", first(high), lookahead=barmerge.lookahead_on)
plot(out)
"""
    rows = _rows(source, lambda i: (20 + 3 * i, 21 + 3 * i, 22 + 3 * i), "dense helper seed")
    provisional = [high for i, complete, _, high, _ in rows if i == 0 and not complete]
    confirmed = [high for i, complete, _, high, _ in rows if i == 0 and complete]
    assert provisional and confirmed == [22] and provisional[0] != confirmed[0], (
        f"NOT COMPARED: expected first provisional high below 22 and confirmed high 22; "
        f"observed {provisional=}, {confirmed=}"
    )
    for i, complete, _, high, value in rows:
        expected = high if i == 0 else confirmed[0]
        assert value == expected, (
            f"bar {i} complete={complete} high={high}: seed {value}, expected {expected}; "
            f"first provisional high {provisional[0]}, first confirmed high {confirmed[0]}"
        )
