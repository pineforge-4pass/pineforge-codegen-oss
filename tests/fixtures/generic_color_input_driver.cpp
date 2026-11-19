// Compile each unmodified generated translation unit with the same driver.
#include "generated.cpp"

#include <algorithm>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <locale>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <vector>

static std::vector<Bar> read_bars(const char* path) {
    std::ifstream file(path);
    std::string line;
    if (!std::getline(file, line)) throw std::runtime_error("cannot read OHLCV header");
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line != "timestamp,open,high,low,close,volume")
        throw std::runtime_error("unexpected OHLCV header");
    std::vector<Bar> bars;
    while (std::getline(file, line)) {
        std::replace(line.begin(), line.end(), ',', ' ');
        std::istringstream row(line);
        row.imbue(std::locale::classic());
        double o, h, l, c, v;
        long long t;
        std::string extra;
        if (!(row >> t >> o >> h >> l >> c >> v) || (row >> extra))
            throw std::runtime_error("malformed OHLCV row");
        bars.push_back(Bar{o, h, l, c, v, t});
    }
    if (!file.eof() || bars.empty()) throw std::runtime_error("incomplete OHLCV read");
    return bars;
}

static void save(const std::string& path, const char* text) {
    std::ofstream out(path, std::ios::binary);
    out.exceptions(std::ios::failbit | std::ios::badbit);
    out << (text ? text : "");
    out.close();
}

static void receipt(void* handle, const std::string& path, std::ostream& status,
                    const char* phase) {
    char error[1024]{};
    size_t required = 0;
    const int size_rc = strategy_get_effective_settings(handle, nullptr, 0, &required,
                                                        error, sizeof(error));
    status << phase << "_size_rc\t" << size_rc << '\n'
           << phase << "_size\t" << required << '\n';
    save(path + ".size.error", error);
    if (size_rc != PF_SETTINGS_BUFFER_TOO_SMALL || !required) return;
    std::vector<char> json(required);
    const int rc = strategy_get_effective_settings(handle, json.data(), json.size(),
                                                   &required, error, sizeof(error));
    status << phase << "_rc\t" << rc << '\n';
    save(path + ".error", error);
    if (rc == PF_SETTINGS_OK) save(path + ".json", json.data());
}

// Read the engine's ledger on the handle that made the checked batch call.
// The ledger and its accessors are the same ones used by Pine trade builtins;
// taking member pointers here also works when the host makes them protected.
struct Ledger : GeneratedStrategy {
    static void write(GeneratedStrategy& s, std::ostream& status, const std::string& path) {
        const auto closed = (s.*(&Ledger::trades_)).size();
        const auto open = (s.*(&Ledger::pyramid_entries_)).size();
        status << "closed_trades\t" << closed << '\n' << "open_positions\t" << open << '\n'
               << "net\t" << (s.*(&Ledger::net_profit))() << '\n';
        std::ofstream out(path, std::ios::binary);
        out.exceptions(std::ios::failbit | std::ios::badbit);
        out.imbue(std::locale::classic());
        out << std::setprecision(std::numeric_limits<double>::max_digits10)
            << "state,entry_time_ms,exit_time_ms,entry_price,exit_price,size,commission,net\n";
        for (size_t i = 0; i < closed; ++i) {
            out << "closed," << (s.*(&Ledger::closed_trade_entry_time))(i) << ','
                << (s.*(&Ledger::closed_trade_exit_time))(i) << ','
                << (s.*(&Ledger::closed_trade_entry_price))(i) << ','
                << (s.*(&Ledger::closed_trade_exit_price))(i) << ','
                << (s.*(&Ledger::closed_trade_size))(i) << ','
                << (s.*(&Ledger::closed_trade_commission))(i) << ','
                << (s.*(&Ledger::closed_trade_profit))(i) << '\n';
        }
        for (size_t i = 0; i < open; ++i) {
            out << "open," << (s.*(&Ledger::open_trade_entry_time))(i) << ",,"
                << (s.*(&Ledger::open_trade_entry_price))(i) << ",,"
                << (s.*(&Ledger::open_trade_size))(i) << ','
                << (s.*(&Ledger::open_trade_commission))(i) << ','
                << (s.*(&Ledger::open_trade_profit))(i) << '\n';
        }
        out.close();
    }
};

int main(int argc, char** argv) {
    if (argc != 5) return 2;  // feed, evidence directory, manifest title, raw setter string
    try {
        auto bars = read_bars(argv[1]);
        const std::string dir = std::string(argv[2]) + '/';
        std::ofstream status(dir + "status.tsv");
        status.exceptions(std::ios::failbit | std::ios::badbit);
        status.imbue(std::locale::classic());
        status << std::unitbuf << std::setprecision(std::numeric_limits<double>::max_digits10)
               << "bars_loaded\t" << bars.size() << '\n';
        char error[1024]{};
        void* handle = nullptr;
        const int create_rc = strategy_create_checked(nullptr, &handle, error, sizeof(error));
        const std::unique_ptr<void, decltype(&strategy_free)> owned(handle, strategy_free);
        status << "create_rc\t" << create_rc << '\n'
               << "create_ok\t" << (create_rc == PF_SETTINGS_OK && handle) << '\n';
        save(dir + "create.error", error);
        if (create_rc != PF_SETTINGS_OK || !handle) return 3;

        receipt(handle, dir + "before", status, "before");
        const int set_rc = strategy_set_input_checked(handle, argv[3], argv[4], error, sizeof(error));
        status << "set_rc\t" << set_rc << '\n' << "set_ok\t" << (set_rc == PF_SETTINGS_OK) << '\n'
               << "set_invalid\t" << (set_rc == PF_SETTINGS_INVALID_ARGUMENT) << '\n';
        save(dir + "set.error", error);
        receipt(handle, dir + "after", status, "after");
        // Checked refusal is an observation. Pytest judges it after all cases.
        if (set_rc != PF_SETTINGS_OK) return 0;

        ReportC report{};
        const std::unique_ptr<ReportC, decltype(&report_free)> report_owner(&report, report_free);
        const int run_rc = run_backtest_full_checked(handle, bars.data(), static_cast<int>(bars.size()),
                                                      "60", "60", 0, 4, 0,
                                                      &report, error, sizeof(error));
        auto& s = *static_cast<GeneratedStrategy*>(handle);
        status << "run_rc\t" << run_rc << '\n' << "run_ok\t" << (run_rc == PF_SETTINGS_OK) << '\n'
               << "last_run_status\t" << strategy_last_run_status(handle) << '\n'
               << "bars_processed\t" << s.script_bars_processed() << '\n'
               << "report_total_trades\t" << report.total_trades << '\n'
               << "script_color\t" << s.lineCol << '\n';
        save(dir + "run.error", error);
        save(dir + "last_run.error", strategy_get_last_error(handle));
        save(dir + "last_run_code.error", strategy_get_last_error_code(handle));
        receipt(handle, dir + "post_run", status, "post_run");
        if (run_rc != PF_SETTINGS_OK) return 4;
        Ledger::write(s, status, dir + "trades.csv");
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 5;
    }
}
