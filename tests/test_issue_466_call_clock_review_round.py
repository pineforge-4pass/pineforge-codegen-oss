"""#466 review: compare requested evaluator values on the synthetic call-clock feed."""

from tests.test_issue_466_call_clock_followups import _rows, _TAKEN


def test_repeated_argument_reads_sample_the_owned_sma_once():
    source = """//@version=6
strategy("One average in a repeated argument")
g(float y) => ta.sma(y, 2)
twice(float v) => v + v
f(float x) => twice(g(x)) * 1000 + x[1]
out = request.security(syminfo.tickerid, "15", close > open ? nz(f(high), -1) : -2, lookahead=barmerge.lookahead_on)
plot(out)
"""
    rows = _increasing_rows(source, "twice(g(x)) on one call clock")
    for i, complete, close, high, value in rows:
        prior = [20 + j for j in _TAKEN if j < i]
        if close <= 10:
            expected = -2
        elif not prior:
            expected = -1
        else:
            mean = (high + prior[-1]) / 2
            expected = 2000 * mean + prior[-1]
        assert value == expected, (
            f"bar {i} complete={complete} high={high}: {value=}, {expected=}, "
            f"previous completed call highs {prior}"
        )


def test_nested_owned_averages_commit_in_dependency_order():
    source = """//@version=6
strategy("Previous EMA of a lagged SMA")
f(float x) => ta.ema(ta.sma(x[1], 2), 3)[1] + x[1]
span() =>
    distance = high - low
    distance
out = request.security(syminfo.tickerid, "15", (close > open ? nz(f(high), -1) : -2) + 0 * span(), lookahead=barmerge.lookahead_on)
plot(out)
"""
    rows = _increasing_rows(source, "nested SMA, EMA, and result history")
    # Only commits feed the next call. SMA skips missing samples; EMA(3)
    # starts at the first finite SMA and then weights both terms by 1/2.
    highs = []
    previous_ema = None
    for i, complete, close, high, value in rows:
        if close <= 10:
            expected = -2
        elif previous_ema is None:
            expected = -1
        else:
            expected = previous_ema + highs[-1]
        assert value == expected, (
            f"bar {i} complete={complete}: {value=}, {expected=}, "
            f"previous completed EMA {previous_ema}, completed highs {highs}"
        )
        if complete and close > 10:
            if len(highs) >= 2:
                sma = sum(highs[-2:]) / 2
                previous_ema = sma if previous_ema is None else (sma + previous_ema) / 2
            highs.append(high)


def test_repeated_wrapper_reads_keep_the_other_written_wrapper_independent():
    source = """//@version=6
strategy("Sparse and dense wrapper calls")
wrap() => ta.sma(high, 2)
twice(float v) => v + v
f(float x) => twice(wrap()) + 0 * x[1]
out = request.security(syminfo.tickerid, "15", nz(wrap(), -1) * 1000 + (close > open ? nz(f(high), -1) : -2), lookahead=barmerge.lookahead_on)
plot(out)
"""
    rows = _increasing_rows(source, "two different wrapper call paths")
    for i, complete, close, high, value in rows:
        dense = (20 + i - 1 + high) / 2 if i else -1
        prior = [20 + j for j in _TAKEN if j < i]
        twice_sparse = -2 if close <= 10 else (prior[-1] + high if prior else -1)
        expected = 1000 * dense + twice_sparse
        assert value == expected, (
            f"bar {i} complete={complete}: {value=}, {expected=}, "
            f"dense SMA {dense}, twice the sparse SMA {twice_sparse}, previous highs {prior}"
        )


def test_two_reads_of_cached_call_text_share_one_counter_slot():
    # The initializer of `held` lowers to an expression containing calls().
    # Both operands reuse that text; the second need not visit calls() again
    # at translation time. No TA participates in this measurement.
    source = """//@version=6
strategy("One counter behind two global reads")
calls() =>
    float n = 0.0
    n := nz(n[1], 0.0) + 1.0
    n
wrap() => close > open ? calls() : -2
held = wrap()
out = request.security(syminfo.tickerid, "15", held + held, lookahead=barmerge.lookahead_on)
plot(out)
"""
    rows = _increasing_rows(source, "same lowered call text read twice")
    for i, complete, close, _, value in rows:
        count = 1 + sum(j < i for j in _TAKEN)
        expected = 2 * count if close > 10 else -4
        assert value == expected, (
            f"bar {i} complete={complete}: {value=}, {expected=}, "
            f"two reads of call number {count}"
        )


def test_repeated_argument_reads_of_multistatement_helper_sma_history():
    source = """//@version=6
strategy("History of an SMA read twice")
twice(float v) => v + v
f(src) =>
    x = src + 0.0
    twice(nz(ta.sma(x, 2)[1], -1))
out = request.security(syminfo.tickerid, "15", close > open ? f(high) : -2, lookahead=barmerge.lookahead_on)
plot(out)
"""
    rows = _increasing_rows(source, "SMA history read twice in a multi-statement helper")
    # With one sample per completed call, [1] reads the SMA from the
    # previous completed call. Twice that mean is the sum of the last two
    # committed highs. Taken arms return -2 at bars 0–2 (twice the nz fallback),
    # 20 + 22 = 42 at bars 3–7, 22 + 27 = 49 at bar 8, 27 + 28 = 55 at
    # bars 9–12, and 28 + 32 = 60 at bars 13–15. Untaken arms return -2.
    bar_8 = next(value for i, complete, _, _, value in rows if i == 8 and complete)
    assert bar_8 == 49, f"bar 8 complete=True: value {bar_8}, expected 49"

    for i, complete, close, _, value in rows:
        if i == 8 and complete:
            continue  # Already compared above.
        prior = [20 + j for j in _TAKEN if j < i]
        expected = sum(prior[-2:]) if close > 10 and len(prior) >= 2 else -2
        assert value == expected, (
            f"bar {i} complete={complete} close={close}: {value=}, {expected=}, "
            f"previous completed call highs {prior}"
        )


def _increasing_rows(source, label):
    rows = _rows(source, lambda i: (20 + i,) * 3, label)
    for i, complete, _, high, _ in rows:
        assert high == 20 + i, (
            f"NOT COMPARED: bar {i} complete={complete} has high {high}, expected {20 + i}"
        )
    return rows
