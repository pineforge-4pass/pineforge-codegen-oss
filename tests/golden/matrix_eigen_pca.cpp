#include <pineforge/engine.hpp>
#include <pineforge/ta.hpp>
#include <pineforge/math.hpp>
#include <pineforge/series.hpp>
#include <pineforge/na.hpp>
#include <cstdint>
#include <cmath>
#include <algorithm>
#include <cstdlib>
#include <numeric>
#include <string>
#include <vector>
#include <tuple>
#include <memory>
#include <mutex>
#include <unordered_map>
#include <unordered_map>
#include <pineforge/color.hpp>
#include <pineforge/log.hpp>
#include <pineforge/str_utils.hpp>
#include <pineforge/session_time.hpp>
#include <pineforge/matrix.hpp>

using namespace pineforge;

// --- syminfo derivation helpers (PineForge G2) ---
static inline std::string _pf_derive_prefix(const std::string& tickerid) {
    std::size_t colon = tickerid.find(':');
    return (colon == std::string::npos) ? tickerid : tickerid.substr(0, colon);
}

static inline std::string _pf_derive_main_tickerid(const std::string& tickerid) {
    // Strip trailing digits (optionally followed by '!') from the symbol part.
    // e.g. "CME_MINI:ES1!" -> "CME_MINI:ES", "NYMEX:CL2!" -> "NYMEX:CL"
    std::string result = tickerid;
    std::size_t colon = result.find(':');
    std::size_t start = (colon == std::string::npos) ? 0 : colon + 1;
    // Find end of base symbol (strip trailing digits + optional '!')
    std::size_t end = result.size();
    if (end > start && result[end - 1] == '!') {
        --end;
    }
    while (end > start && std::isdigit((unsigned char)result[end - 1])) {
        --end;
    }
    return result.substr(0, end);
}

static inline std::string _pf_derive_country(const std::string& tickerid) {
    // Lookup country by exchange prefix (text before ':').
    std::size_t colon = tickerid.find(':');
    std::string prefix = (colon == std::string::npos)
        ? tickerid : tickerid.substr(0, colon);
    static const std::unordered_map<std::string, std::string> _tbl = {
        {"AMEX", "US"},
        {"AQUIS", "GB"},
        {"ARCA", "US"},
        {"ASX", "AU"},
        {"B3", "BR"},
        {"BMF", "BR"},
        {"BMFBOVESPA", "BR"},
        {"BSE", "IN"},
        {"CBOE", "US"},
        {"CBOT", "US"},
        {"CME", "US"},
        {"CME_MINI", "US"},
        {"COINBASE", "US"},
        {"COMEX", "US"},
        {"HKEX", "HK"},
        {"JSE", "ZA"},
        {"KOSPI", "KR"},
        {"KRX", "KR"},
        {"LSE", "GB"},
        {"MOEX", "RU"},
        {"NASDAQ", "US"},
        {"NSE", "IN"},
        {"NYMEX", "US"},
        {"NYSE", "US"},
        {"OSE", "JP"},
        {"OTC", "US"},
        {"SGX", "SG"},
        {"SIX", "CH"},
        {"SSE", "CN"},
        {"SZSE", "CN"},
        {"TSE", "JP"},
        {"TSX", "CA"},
        {"UPBIT", "KR"},
        {"VENTURE", "CA"},
        {"XETRA", "DE"}
    };
    auto it = _tbl.find(prefix);
    return (it != _tbl.end()) ? it->second : na<std::string>();
}
// --- end syminfo derivation helpers ---

class GeneratedStrategy : public BacktestEngine {
public:
    ta::SMA _ta_sma_1;
    std::vector<double> _precalc__ta_sma_1;
    ta::SMA _ta_sma_2;
    std::vector<double> _precalc__ta_sma_2;
    ta::SMA _ta_sma_3;
    ta::SMA _ta_sma_4;
    ta::SMA _ta_sma_5;
    ta::SMA _ta_sma_6;
    ta::Crossover _ta_crossover_7;
    ta::Crossunder _ta_crossunder_8;
    bool _use_precalc = false;
    PineMatrix m;
    int length = 0;
    double v1 = 0.0;
    double v2 = 0.0;
    double v1_mean = 0.0;
    double v2_mean = 0.0;
    double cov11 = 0.0;
    double cov12 = 0.0;
    double cov21 = 0.0;
    double cov22 = 0.0;
    bool covReady = false;
    double lam = 0.0;
    double lamSma = 0.0;
    bool _var_initialized = false;
    bool _ta_initialized_ = false;
    bool _inputs_initialized_ = false;

    explicit GeneratedStrategy() : _ta_sma_1(14), _ta_sma_2(14), _ta_sma_3(14), _ta_sma_4(14), _ta_sma_5(14), _ta_sma_6(14) {
        initial_capital_ = 1000000.0;
        default_qty_type_ = QtyType::FIXED;
        default_qty_value_ = 1.0;
        pyramiding_ = 1;
        commission_type_ = CommissionType::PERCENT;
        commission_value_ = 0.0;
        slippage_ = 0;
    }

    void set_strategy_override(const std::string& key, const std::string& value) {
        if (key == "initial_capital") { initial_capital_ = std::stod(value); return; }
        if (key == "commission_value") { commission_value_ = std::stod(value); return; }
        if (key == "default_qty_value") { default_qty_value_ = std::stod(value); return; }
        if (key == "pyramiding") { pyramiding_ = std::stoi(value); return; }
        if (key == "slippage") { slippage_ = std::stoi(value); return; }
        if (key == "process_orders_on_close") { process_orders_on_close_ = (value == "true" || value == "1"); return; }
        if (key == "close_entries_rule") { close_entries_rule_any_ = (value == "ANY" || value == "any" || value == "1"); return; }
        if (key == "default_qty_type") {
            if (value == "fixed" || value == "strategy.fixed" || value == "0") default_qty_type_ = QtyType::FIXED;
            else if (value == "percent_of_equity" || value == "strategy.percent_of_equity" || value == "1") default_qty_type_ = QtyType::PERCENT_OF_EQUITY;
            else if (value == "cash" || value == "strategy.cash" || value == "2") default_qty_type_ = QtyType::CASH;
            return;
        }
        if (key == "commission_type") {
            if (value == "percent" || value == "strategy.commission.percent" || value == "0") commission_type_ = CommissionType::PERCENT;
            else if (value == "cash_per_order" || value == "strategy.commission.cash_per_order" || value == "1") commission_type_ = CommissionType::CASH_PER_ORDER;
            else if (value == "cash_per_contract" || value == "strategy.commission.cash_per_contract" || value == "2") commission_type_ = CommissionType::CASH_PER_CONTRACT;
            return;
        }
    }

    void on_bar(const Bar& bar) override {
        if (!_var_initialized) {
            m = PineMatrix::new_(2, 2, 0.0);
            _var_initialized = true;
        } else {
        }
        if (!_inputs_initialized_) {
            length = get_input_int("Length", 14);
            _inputs_initialized_ = true;
        }
        if (!_ta_initialized_) {
            _ta_sma_1 = ta::SMA(get_input_int("Length", 14));
            _ta_sma_2 = ta::SMA(get_input_int("Length", 14));
            _ta_sma_3 = ta::SMA(get_input_int("Length", 14));
            _ta_sma_4 = ta::SMA(get_input_int("Length", 14));
            _ta_sma_5 = ta::SMA(get_input_int("Length", 14));
            _ta_sma_6 = ta::SMA(get_input_int("Length", 14));
            _ta_initialized_ = true;
        }
        v1 = (current_bar_.close - current_bar_.open);
        v2 = (current_bar_.high - current_bar_.low);
        v1_mean = (is_first_tick_ ? _ta_sma_1.compute(v1) : _ta_sma_1.recompute(v1));
        v2_mean = (is_first_tick_ ? _ta_sma_2.compute(v2) : _ta_sma_2.recompute(v2));
        cov11 = (is_first_tick_ ? _ta_sma_3.compute(((v1 - v1_mean) * (v1 - v1_mean))) : _ta_sma_3.recompute(((v1 - v1_mean) * (v1 - v1_mean))));
        cov12 = (is_first_tick_ ? _ta_sma_4.compute(((v1 - v1_mean) * (v2 - v2_mean))) : _ta_sma_4.recompute(((v1 - v1_mean) * (v2 - v2_mean))));
        cov21 = cov12;
        cov22 = (is_first_tick_ ? _ta_sma_5.compute(((v2 - v2_mean) * (v2 - v2_mean))) : _ta_sma_5.recompute(((v2 - v2_mean) * (v2 - v2_mean))));
        m.set((int)(0), (int)(0), cov11);
        m.set((int)(0), (int)(1), cov12);
        m.set((int)(1), (int)(0), cov21);
        m.set((int)(1), (int)(1), cov22);
        covReady = ((!(is_na(cov11)) && !(is_na(cov12))) && !(is_na(cov22)));
        lam = na<double>();
        if (covReady) {
            lam = ((((double)m.eigenvalues().size() > 0)) ? (m.eigenvalues()[0]) : (na<double>()));
        }
        lamSma = (is_first_tick_ ? _ta_sma_6.compute(lam) : _ta_sma_6.recompute(lam));
        if ((((covReady && !(is_na(lam))) && !(is_na(lamSma))) && (is_first_tick_ ? _ta_crossover_7.compute(lam, lamSma) : _ta_crossover_7.recompute(lam, lamSma)))) {
            strategy_entry(std::string("Long"), true, na<double>(), na<double>(), na<double>(), "");
        }
        if ((((covReady && !(is_na(lam))) && !(is_na(lamSma))) && (is_first_tick_ ? _ta_crossunder_8.compute(lam, lamSma) : _ta_crossunder_8.recompute(lam, lamSma)))) {
            strategy_entry(std::string("Short"), false, na<double>(), na<double>(), na<double>(), "");
        }
    }

    void precalculate(const Bar* bars, int n) {
        _use_precalc = false;
        if (n <= 0 || bars == nullptr) return;

        _precalc__ta_sma_1.resize(n);
        _precalc__ta_sma_2.resize(n);

        _ta_sma_1 = ta::SMA(14);
        _ta_sma_2 = ta::SMA(14);


        for (int i = 0; i < n; ++i) {
            _precalc__ta_sma_1[i] = _ta_sma_1.compute(v1);
            _precalc__ta_sma_2[i] = _ta_sma_2.compute(v2);
        }

        _ta_sma_1 = ta::SMA(14);
        _ta_sma_2 = ta::SMA(14);

        _use_precalc = true;
    }

    void run(const Bar* bars, int n) {
        precalculate(bars, n);
        BacktestEngine::run(bars, n);
    }

    void run(const Bar* input_bars, int n_input,
             const std::string& input_tf,
             const std::string& script_tf,
             bool bar_magnifier = false,
             int magnifier_samples = 4,
             MagnifierDistribution magnifier_dist = MagnifierDistribution::ENDPOINTS) {
        bool needs_dynamic = bar_magnifier || (!input_tf.empty() && !script_tf.empty() && input_tf != script_tf);
        if (needs_dynamic) {
            _use_precalc = false;
        } else {
            precalculate(input_bars, n_input);
        }
        BacktestEngine::run(input_bars, n_input, input_tf, script_tf, bar_magnifier, magnifier_samples, magnifier_dist);
    }

};

extern "C" {
    void* strategy_create(const char* params_json) {
        return new GeneratedStrategy();
    }
    void run_backtest(void* s, Bar* bars, int n, ReportC* out) {
        auto* strat = static_cast<GeneratedStrategy*>(s);
        strat->run(bars, n);
        strat->fill_report(out);
    }
    void run_backtest_full(void* s, Bar* bars, int n,
                           const char* input_tf, const char* script_tf,
                           int bar_magnifier, int magnifier_samples,
                           int magnifier_dist,
                           ReportC* out) {
        auto* strat = static_cast<GeneratedStrategy*>(s);
        std::string itf = input_tf ? input_tf : "";
        std::string stf = script_tf ? script_tf : "";
        bool needs_full_run = (bar_magnifier != 0)
            || (!itf.empty() && !stf.empty() && itf != stf);
        if (!needs_full_run) {
            strat->run(bars, n);
        } else {
            strat->run(bars, n, itf, stf, bar_magnifier != 0, magnifier_samples,
                       static_cast<MagnifierDistribution>(magnifier_dist));
        }
        strat->fill_report(out);
    }
    void strategy_free(void* s) {
        delete static_cast<GeneratedStrategy*>(s);
    }
    void report_free(ReportC* report) {
        BacktestEngine::free_report(report);
    }
    void strategy_set_input(void* s, const char* key, const char* value) {
        if (!s || !key || !value) return;
        static_cast<GeneratedStrategy*>(s)->set_input(key, value);
    }
    void strategy_set_override(void* s, const char* key, const char* value) {
        if (!s || !key || !value) return;
        static_cast<GeneratedStrategy*>(s)->set_strategy_override(key, value);
    }
    void strategy_set_magnifier_volume_weighted(void* s, int on) {
        if (!s) return;
        static_cast<GeneratedStrategy*>(s)->set_magnifier_volume_weighted(on != 0);
    }
}
