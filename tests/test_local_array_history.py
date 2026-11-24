"""Native witnesses for #433: the snapshots left by one written function call."""

from typing import NamedTuple

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


START = 1743466500000
MINUTE = 60_000
# open, high, low, close, volume
BARS = [(1, 15, 1, 11, 7), (2, 28, 2, 21, 4), (3, 18, 3, 13, 9),
        (4, 54, 4, 50, 2), (5, 37, 5, 34, 6)]


def _observe(source, fields, *, bars=BARS, configure="", opt="-O0"):
    """Observe *every* execution of the emitted body through the native scheduler."""
    driver = r"""
#include <iomanip>
#include <iostream>
struct Probe : GeneratedStrategy {
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        std::cout << std::setprecision(17) << FIELDS << '\n';
    }
};
int main() {
    Bar bars[] = {BARS};
    Probe probe;
    CONFIGURE
    probe.run(bars, sizeof(bars) / sizeof(bars[0]), "1", "1");
    if (!probe.last_error().empty()) {
        std::cerr << probe.last_error();
        return 1;
    }
}
"""
    literals = ",\n".join(
        "{" + ", ".join(map(str, prices)) + f", {START + i * MINUTE}LL}}"
        for i, prices in enumerate(bars)
    )
    driver = (driver.replace("BARS", literals).replace("CONFIGURE", configure)
              .replace("FIELDS", " << ' ' << ".join(fields)))
    output = run_emitted_tu(transpile(source), driver, opt=opt, label="local array history")
    return [tuple(map(float, line.split())) for line in output.splitlines()]


def test_reported_local_array_history():
    source = """//@version=6
strategy("Local array history")
f(float y) =>
    la = array.from(y)
    na(la[1]) ? -1.0 : (la[1]).get(0)
r = f(close)
if r > 0
    strategy.entry("L", strategy.long)
"""
    assert _observe(source, ["r"]) == [
        (BARS[i - 1][3] if i else -1,) for i in range(len(BARS))
    ]


def test_dense_sparse_and_chart_snapshots():
    source = """//@version=6
strategy("Separate clocks")
f(float y) =>
    la = array.from(y)
    prior = na(la[1]) ? -1.0 : (la[1]).get(0)
    la.set(0, y + 100)
    prior
la = array.from(high)
dense = f(close)
sparse = bar_index % 2 == 0 ? f(open) : -9.0
chartPrev = na(la[1]) ? -1.0 : (la[1]).get(0)
"""
    expected = []
    dense = sparse = chart = -1
    for i, (opening, high, _, close, _) in enumerate(BARS):
        expected.append((dense, sparse if i % 2 == 0 else -9, chart))
        dense, chart = close + 100, high
        if i % 2 == 0:
            sparse = opening + 100
    assert _observe(source, ["dense", "sparse", "chartPrev"]) == expected


def test_missing_empty_and_valued_local_array():
    source = """//@version=6
strategy("Nullable local")
f(float y, int phase) =>
    array<float> a = na
    if phase == 1
        a := array.new<float>()
    if phase == 2
        a := array.from(y)
    prior = na(a[1]) ? -1.0 : (a[1]).size() == 0 ? -2.0 : (a[1]).get(0)
    live = na(a[0]) ? -1.0 : a.size() == 0 ? -2.0 : a.get(0)
    [prior, live]
[previous, current] = f(close, bar_index % 3)
"""
    live = [(-1, -2, bar[3])[i % 3] for i, bar in enumerate(BARS)]
    assert _observe(source, ["previous", "current"]) == list(zip([-1, *live[:-1]], live))


def test_returned_local_snapshot_without_copy_elision():
    source = """//@version=6
strategy("Array return")
carry(float price) =>
    a = array.from(price)
    prior = na(a[1]) ? -1.0 : (a[1]).size() == 0 ? -2.0 : (a[1]).get(0)
    a.push(prior)
    a
result = carry(close)
"""
    closes = [bar[3] for bar in BARS]
    assert _observe(source, ["result.at(0)", "result.at(1)"],
                    opt="-fno-elide-constructors") == list(zip(closes, [-1, *closes[:-1]]))


# Both market entries fill at ENTRY. Only the two target closes round up:
# sig10(99999999.0078125) = 99999999.01 and sig10(100000000.0625) =
# 100000000.1. The second residual exceeds the first trade's 1/128 profit.
# Every OHLC and fill price is exactly representable on the 1/128 tick grid.
ENTRY = 99_999_999
FILL_BARS = [
    (ENTRY - 1, ENTRY - 1, ENTRY - 1, ENTRY - 1, 11),
    (ENTRY, ENTRY + 1, ENTRY - 1, ENTRY, 21),
    (ENTRY, ENTRY + 1, ENTRY - 1, ENTRY + 1 / 128, 31),
    (ENTRY, ENTRY + 1, ENTRY - 1, ENTRY, 41),
    (ENTRY - 1, ENTRY - 1, ENTRY - 1, ENTRY - 1, 51),
    (ENTRY, ENTRY + 1, ENTRY - 1, ENTRY, 61),
    (ENTRY, ENTRY + 2, ENTRY - 1, ENTRY + 1 + 1 / 16, 71),
    (ENTRY, ENTRY + 1, ENTRY - 1, ENTRY, 81),
]
FILL_SOURCE = f"""//@version=6
strategy("Sparse close fills", initial_capital={ENTRY}, pyramiding=1, margin_long=100,
     calc_on_order_fills=true, process_orders_on_close=false, commission_value=0, slippage=0)
sample(float y) =>
BODY
varip int calls = 0
var int walks = 0
calls += 1
walks += 1
held = strategy.position_size
closedCount = strategy.closedtrades
exitBar = closedCount > 0 ? strategy.closedtrades.exit_bar_index(closedCount - 1) : -1
exitPrice = closedCount > 0 ? strategy.closedtrades.exit_price(closedCount - 1) : 0.0
marginExit = closedCount > 0 and strategy.closedtrades.exit_comment(closedCount - 1) == "Margin call"
buy = bar_index == 0 or bar_index == 4
checkFill = bar_index == 2 or bar_index == 6
reached = held == 0 and (buy or checkFill or bar_index == 7)
float sampled = -9.0
float r = -9.0
if reached
    [prev, live] = sample(volume)
    sampled := live
    r := prev
if buy
    strategy.entry("L", strategy.long, qty=1)
"""


class Frame(NamedTuple):
    bar: int
    time: int
    advance: bool
    calls: int
    walks: int
    held: int
    closed: int
    exit_bar: int
    exit_price: float
    margin: bool
    reached: bool
    payload: float
    sampled: float
    result: float


def _fill_frames(body):
    fields = ["bar_index_", "bar.timestamp", "history_advances_new_bar()", "calls", "walks",
              "held", "closedCount", "exitBar", "exitPrice", "marginExit", "reached",
              "bar.volume", "sampled", "r"]
    configure = """probe.set_margin_call_enabled(true);
    probe.set_syminfo_metadata("qty_step", 1);
    probe.set_syminfo_pointvalue(1);
    probe.set_syminfo_mintick(1.0 / 128);"""
    rows = _observe(FILL_SOURCE.replace("BODY", body), fields, bars=FILL_BARS, configure=configure)
    why = "NOT COMPARED: close-fill callback precondition"
    assert rows and all(len(row) == len(Frame._fields) for row in rows), (why, rows)
    frames = [Frame(*row) for row in rows]
    # A carried buy can recalculate at the open. Keep that callback too:
    # both executions start from the preceding committed bar in that case.
    groups = [[r for r in frames if r.bar == i] for i in range(len(FILL_BARS))]
    assert [r for group in groups for r in group] == frames, (why, frames)
    assert [r.calls for r in frames] == list(range(1, len(frames) + 1)), (why, frames)
    closed, exit_bar, exit_price = 0, -1, 0
    for i, (bar, group) in enumerate(zip(FILL_BARS, groups)):
        is_exit = i in (2, 6)
        assert len(group) == (2 if is_exit or i in (1, 5) else 1), (why, i, group)
        for j, r in enumerate(group):
            late = is_exit and j == 1
            if late:
                closed += 1
                exit_bar, exit_price = i, bar[3]
            assert (r.time, r.payload) == (START + MINUTE * i, bar[4]), (why, r)
            assert (r.walks, r.advance) == (i + 1 + int(late), not late), (why, r)
            assert r.held == (1 if i in (1, 2, 5, 6) and not late else 0), (why, r)
            assert (r.closed, r.margin, r.exit_bar, r.exit_price) == (
                closed, bool(closed), exit_bar, exit_price), (why, r)
            assert r.reached == (late or i in (0, 4, 7)), (why, r)
            assert r.sampled == (bar[4] if r.reached else -9), (why, r)
    return frames


def test_sparse_first_reach_after_close_fill():
    # Exercise the actual scheduler independently of the #433 lowering,
    # including on the base where the history-bearing body cannot translate.
    control = _fill_frames("    [y, y]")
    assert [r.result for r in control] == [
        r.payload if r.reached else -9 for r in control
    ], "NOT COMPARED: scalar call payload or branch did not match"

    frames = _fill_frames("""    a = array.from(y)
    prior = na(a[1]) ? -1.0 : (a[1]).get(0)
    [prior, a.get(0)]""")
    assert [r[:-1] for r in frames] == [r[:-1] for r in control], (
        "NOT COMPARED: callback sentinels changed between bodies", frames, control)
    # Only ordinary reached closes commit a new payload. A first reach on
    # the post-close callback sees that committed seed; the next seed must
    # not see the provisional fill-bar write.
    expected = []
    committed = -1
    for r in control:
        expected.append(committed if r.reached else -9)
        if r.reached and r.advance:
            committed = r.payload
    assert [r.result for r in frames] == expected
