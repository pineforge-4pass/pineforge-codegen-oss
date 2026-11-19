"""#416: a generic color input round-trips through the native checked ABI."""

import csv
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest

from tests import _compile as native
from tests._e2e import REPO_ROOT, _GLUE_MAIN


GENERIC = """//@version=6
strategy("generic input with a color default", overlay = true, initial_capital = 10000)
fast = ta.ema(close, 10)
slow = ta.ema(close, 30)
lineCol = input(color.lime, "Line color")
plot(fast, color = lineCol)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long, qty = 0.01)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""
TYPED = GENERIC.replace('lineCol = input(', 'lineCol = input.color(', 1)
TITLE = "Line color"
DEFAULT = 4278249078
OVERRIDE = 4278255360
FEED_SHA256 = "46306ea9a90a6df0f1d2e18f29d929a1e36caaacc8536310503136e8cfdababf"
FIRST_MS, LAST_MS, HOUR_MS = 1704067200000, 1709251200000, 3600000
GETTER = re.compile(r'\blineCol\s*=\s*(get_input_\w+)\("Line color",')
DRIVER_SOURCE = r"""// Compile each unmodified generated translation unit with the same driver.
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
"""


def _json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _command(directory, name, argv, **kwargs):
    argv = [str(arg) for arg in argv]
    _json(directory / f"{name}.argv.json", argv)
    with (directory / f"{name}.stdout").open("wb") as out, \
            (directory / f"{name}.stderr").open("wb") as err:
        result = subprocess.run(argv, stdout=out, stderr=err, check=False, **kwargs)
    (directory / f"{name}.exit").write_text(f"{result.returncode}\n", encoding="ascii")
    return result.returncode


def _feed(out):
    raw = os.environ.get("PINEFORGE_416_BTCUSDT_1H_FEED")
    if not raw:
        unavailable = pytest.fail if os.environ.get("PINEFORGE_REQUIRE_NATIVE") == "1" else pytest.skip
        unavailable("set PINEFORGE_416_BTCUSDT_1H_FEED to the pinned #416 hourly feed")
    path = Path(raw).resolve(strict=True)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    reader = csv.DictReader(io.StringIO(data.decode("ascii")))
    assert reader.fieldnames == ["timestamp", "open", "high", "low", "close", "volume"]
    stamps = [int(row["timestamp"]) for row in reader]
    _json(out / "feed.json", {
        "path": str(path), "sha256": digest, "bars": len(stamps),
        "first_ms": stamps[0] if stamps else None, "last_ms": stamps[-1] if stamps else None,
    })
    assert digest == FEED_SHA256
    assert stamps == list(range(FIRST_MS, LAST_MS + HOUR_MS, HOUR_MS))
    return path, len(stamps)


def _run_arm(root, label, source, feed):
    arm = root / label
    arm.mkdir()
    pine = arm / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    rc = _command(arm, "transpile", [
        sys.executable, "-c", _GLUE_MAIN, REPO_ROOT / "gate", pine,
    ], cwd=REPO_ROOT, env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)))
    if rc:
        return {"setup_error": f"public transpiler exited {rc}"}
    try:
        public = json.loads((arm / "transpile.stdout").read_bytes())
    except ValueError as error:
        return {"setup_error": f"cannot read public transpile result: {error}"}
    if not public.get("ok"):
        return {"setup_error": "public transpiler refused the source"}
    cpp = public["cpp"]
    (arm / "generated.cpp").write_text(cpp, encoding="utf-8")
    inputs = public["inputs"]
    entry = next((row for row in inputs if row["title"] == TITLE), None)
    evidence = {"inputs": inputs, "entry": entry, "getters": sorted(set(GETTER.findall(cpp))),
                "cases": {}}
    driver = arm / "generic_color_input_driver.cpp"
    driver.write_bytes(DRIVER_SOURCE.encode("utf-8"))
    executable = arm / "driver"
    rc = _command(arm, "compile", [
        native._COMPILER, "-std=c++17", "-O2", *native.STRATEGY_FP_FLAGS,
        *native._include_flags(isolate_headers=True),
        "-I", arm, driver, native._ENGINE_LIB, "-o", executable,
    ])
    if rc:
        evidence["setup_error"] = f"native compiler exited {rc}"
        return evidence

    values = {"decimal-default": str(DEFAULT), "decimal-override": str(OVERRIDE),
              "raw-hex": "#00FF00"}
    if entry is not None and type(entry.get("default")) in (str, int):
        values["manifest-default"] = str(entry["default"])
    for case, value in values.items():
        directory = arm / case
        directory.mkdir()
        key = entry["title"] if entry is not None else TITLE
        _json(directory / "request.json", {"key": key, "value": value})
        rc = _command(directory, "native", [executable, feed, directory, key, value])
        observed = {"value": value, "exit": rc}
        evidence["cases"][case] = observed
        status_file = directory / "status.tsv"
        if status_file.is_file():
            try:
                observed.update(dict(line.split("\t", 1)
                                     for line in status_file.read_text().splitlines()))
            except ValueError as error:
                observed["status_parse_error"] = str(error)
        for phase in ("before", "after", "post_run"):
            receipt = directory / f"{phase}.json"
            if receipt.is_file():
                try:
                    document = json.loads(receipt.read_bytes())
                    observed[phase] = next(
                        (row for row in document["inputs"] if row["name"] == key), None)
                    if phase == "before":
                        observed["declared_config"] = {
                            row["name"]: row["effective_value"] for row in document["overrides"]
                            if row["name"] in {"initial_capital", "commission_value"}
                        }
                except (ValueError, KeyError, TypeError) as error:
                    observed[f"{phase}_parse_error"] = str(error)
        for operation in ("create", "set", "run", "last_run", "last_run_code"):
            error = directory / f"{operation}.error"
            if error.is_file():
                observed[f"{operation}_error"] = error.read_text()
        tape = directory / "trades.csv"
        if tape.is_file() and rc == 0 and "net" in observed:
            data = tape.read_bytes()
            rows = list(csv.DictReader(io.StringIO(data.decode("ascii"))))
            count = len(rows)
            net = Decimal(observed["net"])
            observed.update(
                trade_count=count, trade_csv_sha256=hashlib.sha256(data).hexdigest(),
                open_trades=sum(row["state"] == "open" for row in rows),
                difference_from_reporter={
                    "trades": count - 26,
                    "net": (str(net.quantize(Decimal("0.01")) - Decimal("29.93"))
                            if net.is_finite() else None),
                },
            )
    return evidence


def _effective(observed, phase):
    return (observed.get(phase) or {}).get("effective_value")


def _matches_integer(value, expected):
    # Capture the float descriptor's actual serialization on RED, even if
    # it spells these exact integers in scientific notation.
    if not isinstance(value, str):
        return False
    try:
        return Decimal(value) == expected
    except InvalidOperation:
        return False


def _metrics(observed):
    return {key: observed[key] for key in (
        "trade_count", "report_total_trades", "net", "trade_csv_sha256",
    )}


def _check_arm(label, arm, bars, unexpected, defect):
    def check(condition, message, failures=unexpected):
        if not condition:
            failures.append(f"{label}: {message}")

    if "setup_error" in arm:
        check(False, arm["setup_error"])
        return
    entry = arm["entry"]
    expected_entry = {"title": TITLE, "type": "color", "default": DEFAULT, "supported": True}
    check(len(arm["inputs"]) == 1 and entry == expected_entry
          and type(entry["default"]) is int, f"manifest is {entry!r}", defect)
    check(arm["getters"] == ["get_input_int64"], f"lineCol getters are {arm['getters']!r}", defect)
    check(set(arm["cases"]) == {"decimal-default", "decimal-override", "manifest-default", "raw-hex"},
          "missing setter attempt")
    for case, obs in arm["cases"].items():
        check(obs["exit"] == 0 and obs.get("create_ok") == "1", f"{case}: setup or driver failed")
        check(not any(key.endswith("_parse_error") for key in obs),
              f"{case}: unreadable driver status or receipt")
        check(obs.get("bars_loaded") == str(bars), f"{case}: driver did not load the full feed")
        config = obs.get("declared_config", {})
        check(_matches_integer(config.get("initial_capital"), 10000)
              and _matches_integer(config.get("commission_value"), 0),
              f"{case}: wrong strategy capital or commission: {config!r}")
        check(_effective(obs, "before") is not None and _effective(obs, "after") is not None,
              f"{case}: missing checked receipt")
        check(_matches_integer(_effective(obs, "before"), DEFAULT), f"{case}: fresh handle default changed")
        for phase in ("before", "after", "post_run"):
            if phase in obs:
                row = obs[phase] or {}
                check(row.get("type") == "int", f"{case}: {phase} receipt is not integer", defect)
        if case == "raw-hex":
            check(obs.get("set_invalid") == "1" and bool(obs.get("set_error")),
                  "raw hex must return an explicit invalid-argument error")
            check(_effective(obs, "before") == _effective(obs, "after"), "raw hex changed settings")
            check("run_rc" not in obs, "raw hex unexpectedly reached execution")
        elif obs.get("set_ok") != "1":
            check(obs.get("set_invalid") == "1" and bool(obs.get("set_error")),
                  f"{case}: refusal did not report invalid argument")
            check(_effective(obs, "before") == _effective(obs, "after"), f"{case}: refusal mutated settings")
            check("run_rc" not in obs, f"{case}: refused setting reached execution")
            check(False, f"{case}: setter refused {obs['value']!r}: {obs.get('set_error')!r}", defect)
        else:
            expected = OVERRIDE if case == "decimal-override" else DEFAULT
            check(obs["value"] == str(expected) and not obs.get("set_error"),
                  f"{case}: wrong setter success")
            check(all(_matches_integer(_effective(obs, phase), expected)
                      for phase in ("after", "post_run")),
                  f"{case}: checked readback disagrees with the requested value")
            if (obs.get("after") or {}).get("type") == "int":
                check(_effective(obs, "after") == _effective(obs, "post_run") == str(expected),
                      f"{case}: checked integer readback is not canonical decimal")
            check(_matches_integer(obs.get("script_color"), expected),
                  f"{case}: the script did not consume the setting")
            check(obs.get("run_ok") == "1" and obs.get("last_run_status") == "0"
                  and not any(obs.get(f"{key}_error") for key in ("run", "last_run", "last_run_code"))
                  and obs.get("bars_processed") == str(bars), f"{case}: checked batch did not complete")
            check(obs.get("trade_count", 0) > 0 and int(obs.get("report_total_trades", "0")) > 0
                  and math.isfinite(float(obs.get("net", "nan"))),
                  f"{case}: missing real trades or net profit")


def test_generic_color_input_checked_round_trip(tmp_path):
    output = tmp_path / "issue-416"
    output.mkdir()
    feed, bars = _feed(output)
    native.skip_if_no_engine_lib()
    arms = {label: _run_arm(output, label, source, feed)
            for label, source in (("typed", TYPED), ("generic", GENERIC))}
    unexpected, defect = [], []
    _check_arm("typed", arms["typed"], bars, unexpected, unexpected)
    _check_arm("generic", arms["generic"], bars, unexpected, defect)
    generic, typed = (arms[key].get("cases", {}) for key in ("generic", "typed"))
    for case in ("manifest-default", "decimal-default", "decimal-override"):
        subject, control = generic.get(case, {}), typed.get(case, {})
        if subject.get("set_ok") == "1" and control.get("set_ok") == "1":
            if "trade_count" not in subject or "trade_count" not in control:
                unexpected.append(f"{case}: missing paired native tape")
            elif _metrics(subject) != _metrics(control):
                unexpected.append(f"{case}: native trade count, net or CSV digest diverged")

    # An expected failure remains an ordinary pytest failure. This receipt
    # identifies its cause after both binaries and every attempt have run.
    manifest = generic.get("manifest-default", {})
    base_signature = (
        arms["generic"].get("entry") == {
            "title": TITLE, "type": "string", "default": "color.lime", "supported": True,
        }
        and arms["generic"].get("getters") == ["get_input_double"]
        and manifest.get("value") == "color.lime"
        and manifest.get("set_invalid") == "1"
        and manifest.get("set_error") == "expected a finite decimal number"
    )
    outcome = ("unexpected" if unexpected or (defect and not base_signature)
               else "issue-red" if defect else "pass")
    _json(output / "evidence.json", {
        "outcome": outcome, "unexpected": unexpected, "defect": defect, "arms": arms,
    })
    assert not unexpected and not defect, (
        f"{outcome}; raw evidence: {output}\n" + "\n".join(unexpected + defect))
