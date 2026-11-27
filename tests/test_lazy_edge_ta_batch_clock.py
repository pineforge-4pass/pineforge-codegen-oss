"""A lazily evaluated ``ta.*`` call keeps its reached-only clock in a batch run.

A chart ``ta.*`` call in a ``?:`` arm or on the right of ``and`` / ``or``,
without a history read on the call, is stepped only on the bars that reach it:
the bare twins in ``tests/test_lazy_edge_ta_every_bar.py`` pin that clock. A
run that may precalculate (no magnifier, empty timeframes) used to fill such a
site on every chart bar and read that vector, so the bars that never reached
the call still entered its window and the same script computed two different
series depending on how it was run.

Invented bars, closes 2, 20, 8, 20, 14, 20, 20 with ``take = bar_index % 2 ==
0`` and ``y = take ? ta.sma(close, 3) : na``:

    reached-only (bars 0, 2, 4, 6)   na, na, 8, 14     first value at bar 4
    every chart bar, read when taken na, 10, 14, 18    first value at bar 2

An unconditional call, and a call whose own history is read (``[k]``: hoisted
to every-bar evaluation), keep their every-bar values and their vectors:
``test_per_execution_families_keep_inline_compute_and_never_precalc``,
``test_hoist_is_independent_of_run_mode`` and
``test_precalculated_extrema_direct_history_uses_chart_bar_clock`` pin those.
"""

from __future__ import annotations

import functools

import pytest

from pineforge_codegen import transpile
from tests import _compile as compile_env


_HEADER = '//@version=6\nstrategy("lazy batch clock")\n'
_GATE = "take = bar_index % 2 == 0\n"


# ---------------------------------------------------------------------------
# Emitted shape: no precalculated vector for a site the inline path reaches
# only when its operand is selected
# ---------------------------------------------------------------------------

_SHAPES = (
    ("sma-ternary", "y = take ? ta.sma(close, 3) : na", "_ta_sma_1"),
    ("sma-or", "y = take or ta.sma(close, 3) > 0", "_ta_sma_1"),
    ("highest-ternary", "y = take ? ta.highest(high, 3) : na", "_ta_highest_1"),
    ("highest-and", "y = take and ta.highest(high, 3) < 20", "_ta_highest_1"),
    ("lowest-and", "y = take and ta.lowest(low, 3) > 1", "_ta_lowest_1"),
    ("wma-and", "y = take and ta.wma(close, 3) > close", "_ta_wma_1"),
    ("stdev-or", "y = take or ta.stdev(close, 3) > 1", "_ta_stdev_1"),
    ("rsi-and", "y = take and ta.rsi(close, 3) > 50", "_ta_rsi_1"),
    ("atr-ternary", "y = take ? ta.atr(3) : na", "_ta_atr_1"),
)


@pytest.mark.parametrize(
    "statement, member", [row[1:] for row in _SHAPES], ids=[row[0] for row in _SHAPES],
)
def test_lazily_reached_site_has_no_precalculated_vector(statement, member):
    cpp = transpile(_HEADER + _GATE + statement + "\n")
    # Neither the vector, nor its fill in precalculate(), nor a read of it.
    assert f"_precalc_{member}" not in cpp
    line = next(ln for ln in cpp.splitlines() if ln.strip().startswith("y = "))
    assert f"{member}.compute(" in line
    assert "_use_precalc" not in line


# ---------------------------------------------------------------------------
# Executable check on the invented bars: a batch run and an inline run of the
# same generated strategy read the same values
# ---------------------------------------------------------------------------

_CLOSES = (2.0, 20.0, 8.0, 20.0, 14.0, 20.0, 20.0)

_CALLS = {
    "sma": "ta.sma(close, 3)",
    "wma": "ta.wma(close, 3)",
    "highest": "ta.highest(high, 3)",
    "lowest": "ta.lowest(low, 3)",
    "stdev": "ta.stdev(close, 3)",
    "rsi": "ta.rsi(close, 3)",
    "atr": "ta.atr(3)",
}

# (label, the expression over the every-bar value ``every_<family>``, the same
# expression with the call written in the lazy operand)
_ROWS = (
    ("sma-ternary", "take ? every_sma : na", "take ? ta.sma(close, 3) : na"),
    ("wma-ternary", "take ? every_wma : na", "take ? ta.wma(close, 3) : na"),
    ("highest-ternary", "take ? every_highest : na", "take ? ta.highest(high, 3) : na"),
    ("lowest-ternary", "take ? every_lowest : na", "take ? ta.lowest(low, 3) : na"),
    ("stdev-ternary", "take ? every_stdev : na", "take ? ta.stdev(close, 3) : na"),
    ("rsi-ternary", "take ? every_rsi : na", "take ? ta.rsi(close, 3) : na"),
    ("atr-ternary", "take ? every_atr : na", "take ? ta.atr(3) : na"),
    ("sma-or", "take or every_sma > 0", "take or ta.sma(close, 3) > 0"),
    ("highest-and", "take and every_highest < 20", "take and ta.highest(high, 3) < 20"),
)


def _script() -> str:
    lines = [_HEADER + _GATE.rstrip("\n")]
    for family, call in _CALLS.items():
        lines.append(f"every_{family} = {call}")
    for index, (_label, every_bar, lazy) in enumerate(_ROWS):
        lines.append(f"gated_{index} = {every_bar}")
        lines.append(f"lazy_{index} = {lazy}")
    # The call's own history is read: hoisted, every-bar in both runs.
    lines.append("hist_sma = take ? ta.sma(close, 3)[1] : na")
    return "\n".join(lines) + "\n"


def _columns() -> list[str]:
    names: list[str] = []
    for index, (label, _every_bar, _lazy) in enumerate(_ROWS):
        names.append(f"gated:{label}=gated_{index}")
        names.append(f"lazy:{label}=lazy_{index}")
    names.append("hist=hist_sma")
    return names


_DRIVER = r"""
#include <cmath>
#include <iomanip>
#include <iostream>

struct ObservedStrategy : GeneratedStrategy {
    int mode = 0;
    static void pf_observe_value(double value) {
        if (std::isnan(value)) std::cout << " na";
        else std::cout << ' ' << value;
    }
    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        std::cout << mode;
        PRINTS
        std::cout << '\n';
    }
};

int main() {
    std::cout << std::setprecision(17);
    const double closes[] = {CLOSES};
    const int n = sizeof(closes) / sizeof(closes[0]);
    Bar bars[COUNT];
    for (int i = 0; i < n; ++i) {
        bars[i] = Bar{closes[i], closes[i], closes[i], closes[i], 1.0,
                      1700000100000LL + i * 900000LL};
    }

    ObservedStrategy batch;                 // empty timeframes: may precalculate
    batch.mode = 0;
    batch.run(bars, n);
    if (!batch.last_error().empty()) {
        std::cerr << batch.last_error();
        return 2;
    }

    ObservedStrategy stepped;               // explicit timeframes: stepped inline
    stepped.mode = 1;
    stepped.run(bars, n, "15", "15");
    if (!stepped.last_error().empty()) {
        std::cerr << stepped.last_error();
        return 3;
    }
    return 0;
}
"""


@functools.lru_cache(maxsize=None)
def _observed() -> tuple[dict[str, list[float | None]], dict[str, list[float | None]]]:
    """Per-bar values of every column in the batch run and in the inline run."""
    columns = _columns()
    prints = "\n        ".join(
        f"pf_observe_value(static_cast<double>({column.split('=', 1)[1]}));"
        for column in columns
    )
    driver = (
        _DRIVER.replace("PRINTS", prints)
        .replace("CLOSES", ", ".join(f"{close:.1f}" for close in _CLOSES))
        .replace("COUNT", str(len(_CLOSES)))
    )
    output = compile_env.run_emitted_tu(
        transpile(_script()), driver, opt="-O0", label="lazy-batch-clock",
    )
    runs: list[dict[str, list[float | None]]] = [
        {column.split("=", 1)[0]: [] for column in columns} for _mode in range(2)
    ]
    for line in output.splitlines():
        mode, *values = line.split()
        assert len(values) == len(columns), line
        for column, value in zip(columns, values):
            runs[int(mode)][column.split("=", 1)[0]].append(
                None if value == "na" else float(value)
            )
    for run in runs:
        assert all(len(series) == len(_CLOSES) for series in run.values())
    return runs[0], runs[1]


@pytest.mark.parametrize("label", [row[0] for row in _ROWS])
def test_lazy_site_reads_the_same_values_in_batch_and_inline_runs(label):
    batch, inline = _observed()
    # The every-bar value of the same expression is one series in both runs.
    assert batch[f"gated:{label}"] == inline[f"gated:{label}"]
    # These bars tell the two clocks apart, so the comparison below is not
    # one of two equal series by accident.
    assert inline[f"lazy:{label}"] != inline[f"gated:{label}"]
    # The batch run reads what the inline run computes.
    assert batch[f"lazy:{label}"] == inline[f"lazy:{label}"]


def test_sma_arm_example_has_its_first_value_at_bar_4_in_both_runs():
    batch, inline = _observed()
    na = None
    reached_only = [na, na, na, na, 8.0, na, 14.0]
    assert inline["lazy:sma-ternary"] == reached_only
    assert batch["lazy:sma-ternary"] == reached_only
    # Controls: the unconditional call and the call read with ``[1]`` keep
    # their every-bar values in both runs.
    every_bar = [na, na, 10.0, na, 14.0, na, 18.0]
    assert batch["gated:sma-ternary"] == every_bar
    assert inline["gated:sma-ternary"] == every_bar
    previous_bar = [na, na, na, na, 16.0, na, 18.0]
    assert batch["hist"] == previous_bar
    assert inline["hist"] == previous_bar
