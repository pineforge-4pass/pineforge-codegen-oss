"""Permanent retained-C-handle pins on bounded literal data, not a TV grader."""
from pathlib import Path

from pineforge_codegen import transpile
from tests.test_runtime_var_initialization import _compile_and_run


DRIVER = r'''
#include <cassert>
#include <iostream>
#include <tuple>
#include <type_traits>
#include <vector>
using NativeTrade = std::remove_pointer_t<decltype(ReportC{}.trades)>;
using EquityPoint = std::remove_pointer_t<decltype(ReportC{}.equity_curve)>;
struct Snapshot {
    int total;
    double net;
    std::vector<NativeTrade> trades;
    std::vector<EquityPoint> equity;
    std::vector<uint64_t> hashes;
    std::vector<std::tuple<std::string, std::string, std::string, uint64_t, int>> identities;
};
std::string copied(const char* value) { return value ? value : ""; }
std::vector<Bar> literal_bars(int count) {
    std::vector<Bar> bars;
    for (int i = 0; i < count; ++i) {
        double close = 100.0 + 2.0 * (i % 2);
        bars.push_back(Bar{close, close + 0.5, close - 0.5, close, 1.0,
                           (i + 1) * int64_t{900000}});
    }
    return bars;
}
Snapshot take_report(void* handle, ReportC& report, int count) {
    const char* error = strategy_get_last_error(handle);
    assert(error == nullptr || *error == '\0');
    assert(report.broker_state_hash_len == count);
    assert(report.equity_curve_len == count);
    Snapshot result{report.total_trades, report.net_profit, {}, {}, {}, {}};
    if (report.trades_len)
        result.trades.assign(report.trades, report.trades + report.trades_len);
    if (count) {
        result.equity.assign(report.equity_curve, report.equity_curve + count);
        result.hashes.assign(report.broker_state_hash, report.broker_state_hash + count);
    }
    for (int i = 0; i < report.trades_len; ++i) {
        result.identities.emplace_back(
            copied(strategy_closed_trade_entry_id(handle, i)),
            copied(strategy_closed_trade_exit_id(handle, i)),
            copied(strategy_closed_trade_exit_comment(handle, i)),
            strategy_closed_trade_entry_incarnation(handle, i),
            strategy_closed_trade_close_cause(handle, i));
    }
    report_free(&report);  // all arrays and borrowed names already copied
    return result;
}
Snapshot run(void* handle, std::vector<Bar>& bars, int count) {
    ReportC report{};
    run_backtest_full(handle, bars.data(), count, "15", "15", 0, 4,
                     static_cast<int>(MagnifierDistribution::ENDPOINTS), &report);
    return take_report(handle, report, count);
}
void same(const Snapshot& a, const Snapshot& b) {
    assert(a.total == b.total && a.net == b.net);
    assert(a.hashes == b.hashes && a.identities == b.identities);
    assert(a.trades.size() == b.trades.size());
    assert(a.equity.size() == b.equity.size());
    for (size_t i = 0; i < a.trades.size(); ++i) {
        const auto& x = a.trades[i]; const auto& y = b.trades[i];
        assert(std::tie(x.entry_time, x.exit_time, x.entry_price, x.exit_price,
                        x.qty, x.pnl, x.pnl_pct, x.is_long, x.commission,
                        x.max_runup, x.max_drawdown, x.entry_bar_index,
                        x.exit_bar_index, x.open_at_end)
            == std::tie(y.entry_time, y.exit_time, y.entry_price, y.exit_price,
                        y.qty, y.pnl, y.pnl_pct, y.is_long, y.commission,
                        y.max_runup, y.max_drawdown, y.entry_bar_index,
                        y.exit_bar_index, y.open_at_end));
    }
    for (size_t i = 0; i < a.equity.size(); ++i)
        assert(std::tie(a.equity[i].time_ms, a.equity[i].equity, a.equity[i].open_profit)
            == std::tie(b.equity[i].time_ms, b.equity[i].equity, b.equity[i].open_profit));
}
void* fresh_handle() {
    auto* handle = strategy_create(nullptr);
    assert(handle != nullptr);
    strategy_set_broker_state_hash_recording(handle, 1);
    return handle;
}
'''


def test_corpus_sma_same_c_handle_full_short_full_matches_fresh():
    source = (Path(__file__).parent / "fixtures" / "reuse_sma152.pine").read_text()
    main = r'''
int main() {
    auto bars = literal_bars(200);
    void* fresh = fresh_handle();
    void* short_fresh = fresh_handle();
    void* reused = fresh_handle();  // one create, no free between these runs
    auto reference = run(fresh, bars, 200);
    auto short_reference = run(short_fresh, bars, 50);
    assert(reference.total > 10);
    assert(short_reference.total == 0);
    same(reference, run(reused, bars, 200));
    same(reference, run(reused, bars, 200));
    same(short_reference, run(reused, bars, 50));
    same(reference, run(reused, bars, 200));
    strategy_free(reused);
    strategy_free(short_fresh);
    strategy_free(fresh);
    std::cout << "reuse-ok\n";
}
'''
    assert _compile_and_run(transpile(source) + DRIVER + main) == "reuse-ok\n"


def test_cached_input_and_constructor_var_follow_explicit_a_b_a_setters():
    source = '''//@version=6
strategy("compiled input lifecycle", initial_capital=1000000,
         default_qty_type=strategy.fixed, default_qty_value=1)
length = input.int(3, "Length")
var int count = 7
count += 1
average = ta.sma(close, length)
if ta.crossover(close, average)
    strategy.entry("L", strategy.long)
if ta.crossunder(close, average)
    strategy.entry("S", strategy.short)
'''
    main = r'''
int main() {
    auto bars = literal_bars(200);
    void* a = fresh_handle();
    void* b = fresh_handle();
    void* reused = fresh_handle();
    strategy_set_input(a, "Length", "3");
    strategy_set_input(b, "Length", "17");
    auto reference_a = run(a, bars, 200);
    auto reference_b = run(b, bars, 200);
    assert(reference_a.total > 10 && reference_b.total > 10);
    assert(reference_a.total != reference_b.total);  // input actually matters
    strategy_set_input(reused, "Length", "3");
    same(reference_a, run(reused, bars, 200));
    assert(static_cast<GeneratedStrategy*>(reused)->count == 207);
    strategy_set_input(reused, "Length", "17");
    same(reference_b, run(reused, bars, 200));
    assert(static_cast<GeneratedStrategy*>(reused)->length == 17);
    assert(static_cast<GeneratedStrategy*>(reused)->count == 207);
    // Inputs persist until explicitly changed; A is restored, not omitted.
    strategy_set_input(reused, "Length", "3");
    same(reference_a, run(reused, bars, 200));
    assert(static_cast<GeneratedStrategy*>(reused)->count == 207);
    same(reference_a, run(reused, bars, 200));  // set-once survives another run
    strategy_free(reused); strategy_free(a); strategy_free(b);
    std::cout << "inputs-ok\n";
}
'''
    assert _compile_and_run(transpile(source) + DRIVER + main) == "inputs-ok\n"


def test_compiled_sma_stream_restart_and_batch_transition_preserve_lifecycle():
    source = (Path(__file__).parent / "fixtures" / "reuse_sma152.pine").read_text()
    main = r'''
Snapshot streamed(void* handle, std::vector<Bar>& bars) {
    assert(strategy_stream_begin(handle, reinterpret_cast<const pf_bar_t*>(bars.data()),
                                 200, "15", "15") == 0);
    const pf_trade_tick_t ticks[] = {
        {201 * int64_t{900000}, 1, 100.0, 1.0},
        {201 * int64_t{900000} + 450000, 2, 102.0, 1.0},
        {202 * int64_t{900000}, 3, 100.0, 1.0},
        {202 * int64_t{900000} + 450000, 4, 102.0, 1.0},
    };
    assert(strategy_stream_push_ticks(handle, ticks, 4) == 0);
    assert(strategy_stream_advance_time(handle, 203 * int64_t{900000}) == 0);
    assert(strategy_stream_end(handle, 0) == 0);
    // Two new completed bars retained the warmed SMA, instead of resetting it
    // at each tick. All summands are exact small integers in this literal tape.
    assert(static_cast<GeneratedStrategy*>(handle)->s == (15150.0 + 204.0) / 152.0);
    ReportC report{};
    assert(strategy_stream_fill_report(handle, reinterpret_cast<pf_report_t*>(&report)) == 0);
    assert(report.script_bars_processed == 202);
    return take_report(handle, report, 202);
}
int main() {
    auto bars = literal_bars(200);
    void* fresh_stream = fresh_handle();
    void* fresh_batch = fresh_handle();
    void* reused = fresh_handle();
    auto reference = streamed(fresh_stream, bars);
    assert(reference.total > 10);
    run(reused, bars, 50); // shorter batch history must not leak into warmup
    same(reference, streamed(reused, bars));
    same(reference, streamed(reused, bars));
    same(run(fresh_batch, bars, 200), run(reused, bars, 200));
    strategy_free(reused); strategy_free(fresh_batch); strategy_free(fresh_stream);
    std::cout << "stream-ok\n";
}
'''
    assert _compile_and_run(transpile(source) + DRIVER + main) == "stream-ok\n"


def test_mutable_udt_collection_state_restarts_with_coof():
    source = '''//@version=6
strategy("mutable run lifecycle", initial_capital=1000000,
         default_qty_type=strategy.fixed, default_qty_value=1,
         calc_on_order_fills=true)
type LifecycleCell
    float total
var LifecycleCell cell = LifecycleCell.new(7)
var array<LifecycleCell> refs = array.new<LifecycleCell>()
var map<string, float> lookup = map.new<string, float>()
var matrix<float> accum = matrix.new<float>(1, 1, 0)
refs.push(cell)
cell.total += 1
lookup.put("value", cell.total)
accum.set(0, 0, accum.get(0, 0) + 1)
observed = cell.total + refs.size() + lookup.get("value") + accum.get(0, 0)
if bar_index % 31 == 0 and strategy.position_size == 0
    strategy.entry("L", strategy.long)
if bar_index % 31 == 1 and strategy.position_size > 0
    strategy.close("L")
'''
    main = r'''
int main() {
    auto bars = literal_bars(200);
    void* fresh = fresh_handle();
    void* reused = fresh_handle();
    auto reference = run(fresh, bars, 200);
    double observed = static_cast<GeneratedStrategy*>(fresh)->observed;
    assert(reference.total > 1 && observed > 200.0);
    same(reference, run(reused, bars, 200));
    assert(static_cast<GeneratedStrategy*>(reused)->observed == observed);
    same(reference, run(reused, bars, 200));
    assert(static_cast<GeneratedStrategy*>(reused)->observed == observed);
    strategy_free(reused); strategy_free(fresh);
    std::cout << "collections-ok\n";
}
'''
    assert _compile_and_run(transpile(source) + DRIVER + main) == "collections-ok\n"
