"""Compile the emitted formatter and pin shortest-decimal, half-up rounding.

The test driver uses the exact C++ helper that codegen emits. Only the engine's
mintick helper is stubbed; none of these cases calls that separate path, whose
TradingView behavior is covered by test_e2e_tv_number_rendering.py. The same
driver source is also compiled on Linux amd64 with GCC 13 in the C6 gate.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pineforge_codegen.codegen.tv_number_format import TV_NUMBER_FORMAT_CPP
from tests import _compile as compile_env


# Values such as 1.0049999999999999 are rounded to an IEEE double before
# formatting. Its shortest round-trip text is "1.005", so it ties upward.
# nextafter supplies doubles whose shortest text is genuinely below/above it.
CASES = (
    ("exact_0125", "_pf_tv_decimal(0.125, 0, 2, false)", "0.13"),
    ("exact_25", "_pf_tv_decimal(2.5, 0, 0, false)", "3"),
    ("short_1005", "_pf_tv_decimal(1.005, 0, 2, false)", "1.01"),
    ("short_1015", "_pf_tv_decimal(1.015, 0, 2, false)", "1.02"),
    ("short_0285", "_pf_tv_decimal(0.285, 0, 2, false)", "0.29"),
    ("literal_10049999999999999",
     "_pf_tv_decimal(1.0049999999999999, 0, 2, false)", "1.01"),
    ("below_1005", "_pf_tv_decimal(std::nextafter(1.005, 0.0), 0, 2, false)",
     "1"),
    ("above_1005", "_pf_tv_decimal(std::nextafter(1.005, 2.0), 0, 2, false)",
     "1.01"),
    ("below_0125", "_pf_tv_decimal(std::nextafter(0.125, 0.0), 0, 2, false)",
     "0.12"),
    ("above_0125", "_pf_tv_decimal(std::nextafter(0.125, 1.0), 0, 2, false)",
     "0.13"),
    ("large_1e15", "_pf_tv_decimal(1e15, 0, 0, true)",
     "1,000,000,000,000,000"),
    ("large_half_1e15", "_pf_tv_decimal(999999999999999.5, 0, 0, true)",
     "1,000,000,000,000,000"),
    ("large_below_1e15",
     "_pf_tv_decimal(std::nextafter(999999999999999.5, 0.0), 0, 0, true)",
     "999,999,999,999,999"),
    ("large_above_1e15",
     "_pf_tv_decimal(std::nextafter(999999999999999.5, "
     "std::numeric_limits<double>::infinity()), 0, 0, true)",
     "1,000,000,000,000,000"),
    ("large_1e17", "_pf_tv_decimal(1e17, 0, 0, true)",
     "100,000,000,000,000,000"),
    ("large_below_1e17", "_pf_tv_decimal(std::nextafter(1e17, 0.0), 0, 0, true)",
     "99,999,999,999,999,984"),
    ("large_above_1e17",
     "_pf_tv_decimal(std::nextafter(1e17, "
     "std::numeric_limits<double>::infinity()), 0, 0, true)",
     "100,000,000,000,000,016"),
    ("negative_tie", "_pf_tv_decimal(-0.125, 0, 2, false)", "-0.13"),
    ("negative_rounded_zero",
     "_pf_tv_decimal(-std::nextafter(0.005, 0.0), 0, 2, false)", "0"),
    ("negative_005", "_pf_tv_decimal(-0.005, 0, 2, false)", "-0.01"),
    ("negative_zero", "_pf_tv_decimal(-0.0, 0, 2, false)", "0"),
    ("min_fraction", '_pf_tv_pattern(1.2, "#.00")', "1.20"),
    ("default_tostring", "pine_str_tostring_tv(0.1234567890123)",
     "0.123456789"),
    ("tostring_percent", 'pine_str_tostring_tv(1.005, "percent")', "1.01%"),
    ("pattern_percent_below",
     '_pf_tv_pattern(std::nextafter(0.01005, 0.0), "#.##%")', "1%"),
    ("pattern_percent_tie", '_pf_tv_pattern(0.01005, "#.##%")', "1.01%"),
    ("pattern_percent_above",
     '_pf_tv_pattern(std::nextafter(0.01005, 1.0), "#.##%")', "1.01%"),
    ("preset_percent_below",
     '_pf_tv_number_style(std::nextafter(0.125, 0.0), "percent")', "12%"),
    ("preset_percent_tie", '_pf_tv_number_style(0.125, "percent")', "13%"),
    ("currency_below",
     '_pf_tv_number_style(std::nextafter(1.005, 0.0), "currency")', "$1.00"),
    ("currency_tie", '_pf_tv_number_style(1.005, "currency")', "$1.01"),
    ("currency_negative", '_pf_tv_number_style(-1234.567, "currency")',
     "-$1,234.57"),
    ("volume_below", 'pine_str_tostring_tv(std::nextafter(1005.0, 0.0), "volume")',
     "1K"),
    ("volume_tie", 'pine_str_tostring_tv(1005.0, "volume")', "1.01K"),
    ("volume_above", 'pine_str_tostring_tv(std::nextafter(1005.0, 2000.0), "volume")',
     "1.01K"),
    ("volume_small", 'pine_str_tostring_tv(12.34, "volume")', "12"),
    ("volume_trillion", 'pine_str_tostring_tv(1e12, "volume")', "1T"),
    ("format_grouped", 'pine_str_format_tv("{0}", {1234.56789})',
     "1,234.568"),
    ("nan", "_pf_tv_decimal(std::numeric_limits<double>::quiet_NaN(), 0, 2, false)",
     "NaN"),
    ("positive_inf",
     "_pf_tv_decimal(std::numeric_limits<double>::infinity(), 0, 2, false)",
     "Infinity"),
    ("negative_inf",
     "_pf_tv_decimal(-std::numeric_limits<double>::infinity(), 0, 2, false)",
     "-Infinity"),
    ("nan_percent_pattern",
     '_pf_tv_pattern(std::numeric_limits<double>::quiet_NaN(), "#.##%")', "NaN"),
)


def render_driver_source() -> str:
    prelude = r"""
#include <algorithm>
#include <charconv>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <limits>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <type_traits>
#include <vector>

template <class T> bool is_na(T value) {
    if constexpr (std::is_floating_point_v<T>) return std::isnan(value);
    return value == std::numeric_limits<T>::min();
}
template <class T> T na() { return std::numeric_limits<T>::quiet_NaN(); }
std::string pine_str_tostring(double, const std::string&, double) {
    return "unused mintick stub";
}
"""
    lines = ["int main() {"]
    for name, expression, _ in CASES:
        lines.append(f'    std::cout << "{name}\\t" << ({expression}) << "\\n";')
    lines.append("}")
    return prelude + TV_NUMBER_FORMAT_CPP + "\n" + "\n".join(lines) + "\n"


def test_shortest_decimal_half_up_edge_battery(tmp_path: Path) -> None:
    compile_env.skip_if_no_compile_env()
    source = tmp_path / "tv_number_format_rounding.cpp"
    binary = tmp_path / "tv_number_format_rounding"
    source.write_text(render_driver_source())
    build = subprocess.run(
        [compile_env._COMPILER, "-std=c++17", "-O2",
         *compile_env.STRATEGY_FP_FLAGS, str(source), "-o", str(binary)],
        capture_output=True, text=True, timeout=120,
    )
    assert build.returncode == 0, build.stderr[:4000]
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    actual = dict(line.split("\t", 1) for line in run.stdout.splitlines())
    expected = {name: value for name, _, value in CASES}
    assert actual == expected, [
        (name, expected[name], actual.get(name))
        for name in expected if actual.get(name) != expected[name]
    ]
