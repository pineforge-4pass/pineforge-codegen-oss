"""The precalculated TA cache lives in the engine's O(1) state store.

A5-CGEN, second milestone. The cache is the one audited container category the
codegen owns completely: one logical element per run bar, trivially copyable
elements, and exactly three operations (``resize``, element write, element
read). Its size is a run value, so no compile-time bound exists and the cache
cannot be preallocated. It is therefore routed through
``pineforge/detail/o1_store.hpp``: a fixed resident working set over a spilling
backing, allocated by id, never refusing for capacity.

Fail-before: on the lane base the emitted translation unit has no store view
and no ``strategy_script_state_store_stats_v1`` export, so every test here
fails on an assertion or on the driver's compile rather than passing
vacuously. The same file is the fail-before evidence for this change.

Pine parity is proven by construction and by execution: the resident fallback
behind the same interface is ``std::vector<T>``, so the same generated source
built with ``-DPF_O1_STORE_DISABLED`` runs the strategy on the identical
values, and both runs must report byte-identical trades and per-bar broker
state hashes.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from pineforge_codegen import _generate, transpile
from tests import _compile as compile_env
from tests.test_pinemap_semantics import _find_engine_library


# Two static TA sites -> two precalculated caches, each one element per bar.
# The strategy trades off the cached values, so any storage error changes the
# report.
STORE_SOURCE = '''//@version=6
strategy("store-backed precalc")
s = ta.sma(close, 7)
e = ta.ema(close, 9)
if close > s and close > e
    strategy.entry("L", strategy.long)
if close < s
    strategy.close("L")
plot(s + e)
'''

# 60,000 bars x 8 bytes x 2 caches = 960,000 logical bytes, well past the
# emitted 64-page (262,144-byte) resident ceiling, so the run must spill.
BARS = 60000
RESIDENT_CEILING = 64 * 4096


DRIVER = r'''
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

namespace {
std::uint64_t mix(std::uint64_t digest, std::uint64_t value) {
    return (digest ^ value) * 1099511628211ULL;
}
std::uint64_t bits(double value) {
    std::uint64_t word = 0;
    std::memcpy(&word, &value, sizeof word);
    return word;
}
}  // namespace

int main() {
    const int n = __BARS__;
    std::vector<Bar> bars;
    bars.reserve(static_cast<std::size_t>(n));
    double px = 100.0;
    for (int i = 0; i < n; ++i) {
        px += ((i % 7) - 3) * 0.25;
        bars.push_back(Bar{px, px + 0.5, px - 0.5, px, 1.0,
                           (i + 1) * int64_t{900000}});
    }
    void* handle = strategy_create(nullptr);
    if (!handle) return 10;
    strategy_set_broker_state_hash_recording(handle, 1);

    std::uint64_t before[4] = {7, 7, 7, 7};
    const int opened_before = strategy_script_state_store_stats_v1(handle, before);

    ReportC report{};
    run_backtest_full(handle, bars.data(), n, "", "", 0, 4,
                      static_cast<int>(MagnifierDistribution::ENDPOINTS), &report);
    if (const char* error = strategy_get_last_error(handle)) {
        std::printf("RUN_ERROR %s\n", error);
        return 11;
    }

    std::uint64_t after[4] = {0, 0, 0, 0};
    const int opened_after = strategy_script_state_store_stats_v1(handle, after);

    std::uint64_t digest = 1469598103934665603ULL;
    digest = mix(digest, static_cast<std::uint64_t>(report.total_trades));
    digest = mix(digest, bits(report.net_profit));
    for (int64_t i = 0; i < report.broker_state_hash_len; ++i)
        digest = mix(digest, report.broker_state_hash[i]);
    for (int64_t i = 0; i < report.equity_curve_len; ++i) {
        digest = mix(digest, static_cast<std::uint64_t>(report.equity_curve[i].time_ms));
        digest = mix(digest, bits(report.equity_curve[i].equity));
        digest = mix(digest, bits(report.equity_curve[i].open_profit));
    }
    for (int64_t i = 0; i < report.trades_len; ++i) {
        const auto& trade = report.trades[i];
        digest = mix(digest, static_cast<std::uint64_t>(trade.entry_bar_index));
        digest = mix(digest, static_cast<std::uint64_t>(trade.exit_bar_index));
        digest = mix(digest, bits(trade.entry_price));
        digest = mix(digest, bits(trade.exit_price));
        digest = mix(digest, bits(trade.pnl));
        digest = mix(digest, static_cast<std::uint64_t>(trade.is_long));
    }
    std::printf(
        "REPORT trades=%d net=%.17g hash_len=%lld digest=%llx\n",
        report.total_trades, report.net_profit,
        static_cast<long long>(report.broker_state_hash_len),
        static_cast<unsigned long long>(digest));
    std::printf(
        "STORE opened_before=%d opened_after=%d live=%llu resident=%llu "
        "spilled=%llu backing=%llu\n",
        opened_before, opened_after,
        static_cast<unsigned long long>(after[0]),
        static_cast<unsigned long long>(after[1]),
        static_cast<unsigned long long>(after[2]),
        static_cast<unsigned long long>(after[3]));
    report_free(&report);
    strategy_free(handle);
    return 0;
}
'''


def _generated_cpp() -> str:
    return transpile(STORE_SOURCE)


def _run(cpp_source: str, *, label: str, flags: tuple[str, ...] = ()) -> str:
    """Compile and run one generated TU; return its stdout.

    ``tests.test_pinemap_semantics._compile_and_run`` cannot pass extra
    compiler flags, and the parity check needs ``-DPF_O1_STORE_DISABLED`` on
    the very same emitted source.
    """
    compile_env.skip_if_no_compile_env()
    engine_library = _find_engine_library()
    if engine_library is None:
        pytest.skip("built libpineforge not found; set PINEFORGE_ENGINE_LIB")
    assert compile_env._COMPILER is not None
    assert compile_env._ENGINE_INC is not None
    assert compile_env._EIGEN_INC is not None
    with tempfile.TemporaryDirectory(prefix=f"pineforge-{label}-") as tmp:
        source_path = Path(tmp) / "probe.cpp"
        executable = Path(tmp) / "probe"
        source_path.write_text(cpp_source)
        command = [
            compile_env._COMPILER,
            "-std=c++17",
            "-O0",
            *compile_env.STRATEGY_FP_FLAGS,
            *flags,
            "-I", str(compile_env._ENGINE_INC),
            "-I", str(compile_env._EIGEN_INC),
        ]
        if compile_env._GENERATED_INC is not None:
            command += ["-I", str(compile_env._GENERATED_INC)]
        command += [str(source_path), str(engine_library), "-pthread",
                    "-o", str(executable)]
        built = subprocess.run(command, capture_output=True, text=True,
                               timeout=600)
        if built.returncode != 0:
            raise AssertionError(
                f"{label} failed to link\n"
                + "\n".join((built.stderr or built.stdout).splitlines()[:120])
            )
        ran = subprocess.run([str(executable)], capture_output=True, text=True,
                             timeout=300)
        if ran.returncode != 0:
            raise AssertionError(
                f"{label} exited {ran.returncode}\n"
                f"stdout:\n{ran.stdout}\nstderr:\n{ran.stderr}"
            )
        return ran.stdout


def _lines(stdout: str) -> dict[str, str]:
    return {
        line.split(" ", 1)[0]: line
        for line in stdout.splitlines()
        if " " in line
    }


# ---------------------------------------------------------------------------
# Emission: the store view replaces the resident cache container.
# ---------------------------------------------------------------------------


def test_precalculated_cache_is_store_backed():
    cpp = _generated_cpp()
    assert "_PFO1Buffer<double> _precalc_" in cpp
    # The resident container is gone, not merely accompanied.
    assert "std::vector<double> _precalc_" not in cpp
    # The view is a typed facade over the engine's byte store.
    assert "pineforge::detail::o1::Store::open" in cpp
    assert "pineforge::detail::o1::make_memfd_backing" in cpp
    assert "_PFO1StoreOwner _pf_o1_store_;" in cpp


def test_store_region_is_scoped_to_scripts_that_need_it():
    assert "_PFO1Buffer" in transpile(STORE_SOURCE)
    plain = transpile('//@version=6\nstrategy("no cache")\nplot(close)\n')
    assert "_PFO1Buffer" not in plain
    assert "_PFO1StoreOwner" not in plain
    assert "strategy_script_state_store_stats_v1" not in plain


def test_store_stats_export_is_declared_after_the_strategy_class():
    cpp = _generated_cpp()
    export = cpp.find("int strategy_script_state_store_stats_v1(")
    assert export > 0
    assert export > cpp.find("class GeneratedStrategy")


def test_resident_fallback_is_emitted_for_engines_without_the_store():
    cpp = _generated_cpp()
    # The guard selects the store only when the paired engine carries it.
    assert "if __has_include(<pineforge/detail/o1_store.hpp>)" in cpp
    assert '#define _PF_O1_CURRENT_STORAGE "resident"' in cpp
    # The fallback is the lowering every earlier build emitted.
    fallback = cpp.split('#define _PF_O1_CURRENT_STORAGE "resident"', 1)[1]
    assert "std::vector<T> _pf_values_;" in fallback
    assert "void resize(std::size_t _pf_n) { _pf_values_.resize(_pf_n); }" in fallback


def test_store_backed_script_compiles_against_the_paired_engine():
    compile_env.compile_cpp(_generated_cpp(), label="store-backed precalc")
    compile_env.compile_cpp(_generated_cpp(), label="store-backed precalc resident",
                            extra_flags=("-DPF_O1_STORE_DISABLED",))


def test_bound_row_reports_the_store_storage():
    gen, _ctx, cpp, _diagnostics, _sites = _generate(
        STORE_SOURCE, True, "store.pine")
    rows = [row for row in gen.script_state_bounds if row["kind"] == "precalc"]
    assert rows, gen.script_state_bounds
    for row in rows:
        assert row["cardinality_bound"] is None
        assert row["element_bytes_cpp"] == "sizeof(double)"
        assert row["storage_requirement"] == "external"
        # The row reports the storage the TU is built against, not a guess.
        assert row["current_storage"] == "_PF_O1_CURRENT_STORAGE"
    assert '_PF_O1_CURRENT_STORAGE},' in cpp


# ---------------------------------------------------------------------------
# Run: values survive the store, spill is bounded, and Pine results do not move.
# ---------------------------------------------------------------------------


def test_store_run_spills_and_matches_the_resident_lowering():
    cpp = _generated_cpp()
    store_out = _lines(_run(cpp + DRIVER.replace("__BARS__", str(BARS)),
                            label="store-precalc"))
    resident_out = _lines(_run(cpp + DRIVER.replace("__BARS__", str(BARS)),
                               label="store-precalc-resident",
                               flags=("-DPF_O1_STORE_DISABLED",)))

    # Zero Pine-observable difference: same trades, same net, same per-bar
    # broker state hashes and equity.
    assert store_out["REPORT"] == resident_out["REPORT"], (
        store_out["REPORT"], resident_out["REPORT"])

    # The resident control never opens a store.
    assert "opened_before=0 opened_after=0" in resident_out["STORE"]
    assert "live=0 resident=0 spilled=0 backing=0" in resident_out["STORE"]

    store = dict(
        field.split("=", 1) for field in store_out["STORE"].split()[1:]
    )
    messages = []
    if store["opened_before"] != "0":
        messages.append("the store opened before the first bar")
    if store["opened_after"] != "1":
        messages.append("the store never opened for the cache")
    live = int(store["live"])
    # One element per bar for each of the two caches.
    if live < BARS * 8 * 2:
        messages.append(f"live={live} does not hold every cache element")
    resident = int(store["resident"])
    if resident > RESIDENT_CEILING:
        messages.append(f"resident={resident} exceeds the fixed working set")
    if int(store["spilled"]) <= 0:
        messages.append("no page spilled; the cache stayed resident")
    if int(store["backing"]) <= 0:
        messages.append("no backing bytes were written")
    assert not messages, messages
