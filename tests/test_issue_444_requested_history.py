"""A helper inside request.security reads its argument on the requested clock."""

import math
from collections import defaultdict
from typing import NamedTuple

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu, skip_if_no_engine_lib


_CHART_MS = 15 * 60_000
_HOUR_MS = 60 * 60_000
_START = 1743465600000
_WICKS = (
    ((12, 18), (17, 20), (14, 19), (20, 19), (16, 24), (19, 23)),
    ((40, 57), (46, 51), (44, 60), (51, 49), (43, 58), (49, 54)),
)

_PINE = """//@version=6
strategy("parameter named like a global")
float spread = close - open
{helper}
float witness_value = request.security(syminfo.tickerid, "60", {payload}, lookahead=barmerge.lookahead_off)
plot(witness_value)
"""


class _Candle(NamedTuple):
    timestamp: int
    open: int
    high: int
    low: int
    close: int


def _chart_feed(run):
    for hour, (upper, lower) in enumerate(_WICKS[run]):
        for quarter, body in enumerate((2, -3, 1, -2)):
            timestamp = _START + hour * _HOUR_MS + quarter * _CHART_MS
            open_ = 200 + 300 * run + 11 * hour + quarter
            close = open_ + body * (1 + hour % 2)
            # The first child sets the hourly high and the third the low.
            # Individual chart ranges are smaller than the hourly ranges.
            high = max(open_, close) + (upper if quarter == 0 else 2)
            low = min(open_, close) - (lower if quarter == 2 else 2)
            yield _Candle(timestamp, open_, high, low, close)


def _expected_completed(feeds):
    expected = {}
    for run, feed in enumerate(feeds):
        by_hour = defaultdict(list)
        for bar in feed:
            by_hour[bar.timestamp - bar.timestamp % _HOUR_MS].append(bar)
        previous_range = None
        for hour, bars in sorted(by_hour.items()):
            assert [bar.timestamp for bar in bars] == list(
                range(hour, hour + _HOUR_MS, _CHART_MS)
            )
            high = max(bar.high for bar in bars)
            low = min(bar.low for bar in bars)
            last = bars[-1]
            expected[run, hour] = (
                last.timestamp, high, low, last.close - last.open, previous_range,
            )
            previous_range = high - low
    return expected


_DRIVER = r"""
#include <iomanip>
#include <iostream>

struct Observer : GeneratedStrategy {
    int run_id = 0;
    Bar completed{};
    bool pending = false;
    bool overlap = false;

    void evaluate_security(int id, const Bar& bar, bool complete) override {
        GeneratedStrategy::evaluate_security(id, bar, complete);
        if (complete) {
            overlap = overlap || pending;
            completed = bar;
            pending = true;
        }
    }

    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        if (!pending) return;
        // Read the Pine result after this chart bar, where the completed
        // request becomes visible. The evaluator's storage is private to it.
        std::cout << run_id << ' ' << completed.timestamp << ' ' << bar.timestamp
                  << ' ' << completed.high << ' ' << completed.low
                  << ' ' << (bar.close - bar.open) << ' ' << witness_value << '\n';
        pending = false;
    }
};

int main() {
    Observer strategy;
    std::cout << std::setprecision(17);
    @RUNS@
    return 0;
}
"""


def _driver(feeds):
    runs = []
    for run, feed in enumerate(feeds):
        bars = ",\n            ".join(
            f"Bar{{{bar.open}, {bar.high}, {bar.low}, {bar.close}, 1.0, {bar.timestamp}LL}}"
            for bar in feed
        )
        runs.append(f"""{{
        const Bar bars[] = {{
            {bars}
        }};
        strategy.run_id = {run};
        strategy.run(bars, {len(feed)}, "15", "15");
        if (!strategy.last_error().empty() || strategy.overlap || strategy.pending) {{
            std::cerr << "run " << strategy.run_id << ": " << strategy.last_error()
                      << " overlapping completions=" << strategy.overlap
                      << " unobserved completion=" << strategy.pending << '\\n';
            return 1;
        }}
    }}""")
    return _DRIVER.replace("@RUNS@", "\n    ".join(runs))


def _assert_completed(source, driver, expected, label):
    output = run_emitted_tu(transpile(source), driver, opt="-O2", label=label)
    print(f"{label}: run, requested open, chart open, high, low, chart body, value")
    print(output, end="")
    actual = {}
    for line in output.splitlines():
        run, hour, chart, high, low, body, value = line.split()
        key = (int(run), int(hour))
        assert key not in actual, f"{label}: duplicate completed bar {key}"
        value = float(value)
        actual[key] = (
            int(chart), float(high), float(low), float(body),
            None if math.isnan(value) else value,
        )
    missing = expected.keys() - actual.keys()
    unexpected = actual.keys() - expected.keys()
    assert not (missing or unexpected), (label, sorted(missing), sorted(unexpected))
    for key, wanted in expected.items():
        assert actual[key] == wanted, (label, key, actual[key], wanted)


def test_shadowed_helper_reads_previous_requested_range():
    skip_if_no_engine_lib()
    # Reuse one native strategy with different extrema on the same dates:
    # the new run must warm up again and only see its own requested bars.
    feeds = [list(_chart_feed(run)) for run in range(len(_WICKS))]
    expected = _expected_completed(feeds)
    driver = _driver(feeds)
    _assert_completed(
        _PINE.format(helper="", payload="high[1] - low[1]"),
        driver, expected, "direct requested history control",
    )
    print("direct requested history control matched the feed")
    _assert_completed(
        _PINE.format(helper="g(spread) => spread[1]", payload="g(high - low)"),
        driver, expected, "issue #444 requested helper",
    )
