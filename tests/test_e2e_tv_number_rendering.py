"""TradingView trade Signals pin numeric text shared by str.* and log.*.

The 2025-04-01 c6-number-rendering, c6-number-edge and c6-format-pattern TV
exports encode each
format call in a separate order Signal. Pine v6's str.tostring reference names
the default format '#.##########', format.mintick, format.percent,
format.volume and #/0 patterns; str.format uses numbered placeholders and
number styles (integer, percent, currency or a custom pattern).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tests._e2e import (
    Build, derive_chart_feed, execute_all, ok, summary, skip_unless_e2e_env,
)
from pineforge_codegen.pine_spelling import pine_string_literal


# Expected strings are the Entry long Signals in TradingView's exported tapes.
CASES = (
    ("dflt", "str.tostring(1234.56789)", "1234.56789"),
    ("pattern", 'str.tostring(1.2, "#.##")', "1.2"),
    ("zeros", 'str.tostring(1.2, "#.00")', "1.20"),
    ("percent", "str.tostring(1.2345, format.percent)", "1.23%"),
    ("volume", "str.tostring(1250, format.volume)", "1.25K"),
    ("mintick", "str.tostring(1.2345, format.mintick)", "1.23"),
    ("fmt_default", 'str.format("{0}", 1234.56789)', "1,234.568"),
    ("fmt_pattern", 'str.format("{0,number,#.##}", 1234.56789)', "1234.57"),
    ("fmt_integer", 'str.format("{0,number,integer}", 1234.56789)', "1,235"),
    ("fmt_percent", 'str.format("{0,number,percent}", 0.1234)', "12%"),
    ("fmt_currency", 'str.format("{0,number,currency}", 1234.56789)', "$1,234.57"),
    ("vol2500", "str.tostring(2500, format.volume)", "2.5K"),
    ("vol1000", "str.tostring(1000, format.volume)", "1K"),
    ("vol12", "str.tostring(12.34, format.volume)", "12"),
    ("pc1", "str.tostring(1.005, format.percent)", "1.01%"),
    ("prec", "str.tostring(0.1234567890123)", "0.123456789"),
    ("group", 'str.tostring(1234.56789, "#,###.##")', "1,234.57"),
    ("negcurr", 'str.format("{0,number,currency}", -1234.567)', "-$1,234.57"),
    ("negpc", 'str.format("{0,number,percent}", -0.1234)', "-12%"),
    ("quoted", '''str.format("'{0}' {0}", 2.5)''', "{0} 2.5"),
    ("fmtzero", 'str.format("{0,number,#.00}", 1.2)', "1.20"),
    ("bool", 'str.format("{0}", true)', "true"),
    ("na", "str.tostring(float(na))", "NaN"),
    ("mintick_trail", "str.tostring(1.2, format.mintick)", "1.20"),
    ("mintick_int", "str.tostring(2, format.mintick)", "2.00"),
    ("tostring_pcpat", 'str.tostring(0.1234, "#.##%")', "12.34%"),
    ("format_pcpat", 'str.format("{0,number,#.##%}", 0.1234)', "12.34%"),
    ("format_quote2", '''str.format("''{0}", 2.5)''', "'2.5"),
)


def _source(subject: bool) -> str:
    lines = ['//@version=6', 'strategy("c6-tv-number-rendering", overlay=true)']
    for index, (name, expression, expected) in enumerate(CASES):
        value = expression if subject else pine_string_literal(expected)
        lines.extend([
            f"s_{name} = {value}",
            f"// @pf-trace check_{name}=s_{name} == {pine_string_literal(expected)} ? 1 : 0",
            f"if bar_index == {index * 3}",
            f"    strategy.entry(s_{name}, strategy.long)",
            f"if bar_index == {index * 3 + 1}",
            "    strategy.close_all()",
        ])
    lines.extend([
        'if bar_index == 0',
        ('    log.info("fmt {0}", 1234.56789)' if subject
         else '    log.info("fmt 1,234.568")'),
        ('    log.warning("{0,number,percent}", 0.1234)' if subject
         else '    log.warning("12%")'),
        ('    log.error("{0,number,currency}", 1234.56789)' if subject
         else '    log.error("$1,234.57")'),
    ])
    return "\n".join(lines) + "\n"


@pytest.fixture(scope="session")
def tv_number_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("c6_tv_number")
    full_feed = derive_chart_feed(engine, base / "full_chart.csv")
    feed = base / "chart.csv"
    with full_feed.open() as inp, feed.open("w") as out:
        for _, line in zip(range(91), inp):
            out.write(line)
    runs = execute_all(engine, feed, base, {
        "subject": Build(_source(True), trace=True),
        "reference": Build(_source(False), trace=True),
    })
    return ok(runs, "subject"), ok(runs, "reference")


def test_number_strings_match_tv_trade_signals(tv_number_runs):
    subject, reference = tv_number_runs
    fixture = Path(__file__).parent / "fixtures/c6_tv_evidence"
    signals = {}
    for filename in ("number_rendering_tv_trades.csv", "number_edge_tv_trades.csv",
                     "number_pattern_tv_trades.csv"):
        with (fixture / filename).open(encoding="utf-8-sig") as file:
            for row in csv.DictReader(file):
                if row["Type"].startswith("Entry"):
                    name, value = row["Signal"].split(":", 1)
                    signals[name] = value
    for name, _, expected in CASES:
        assert signals[name] == expected, f"TradingView's {name} Signal changed"
    checks = subject.traces["default"]
    assert len(checks) == len(CASES) * 90
    failures = [r for r in checks if r["value"] != 1]
    assert not failures, (f"{len(failures)} of {len(checks)} number-format traces "
                          f"differ from TV; first {failures[0] if failures else None}")
    assert subject.trades["default"] == reference.trades["default"], (
        f"formatted {summary(subject.trades['default'])}; "
        f"TV literals {summary(reference.trades['default'])}")
    print(f"TV numbers: {len(checks)} traces equal {len(CASES)} tape Signals; "
          f"formatted {summary(subject.trades['default'])} = "
          f"TV literals {summary(reference.trades['default'])}")


def test_log_uses_the_same_number_formatter(tv_number_runs):
    subject, reference = tv_number_runs
    expected = reference.logs["default"].strip().splitlines()
    actual = subject.logs["default"].strip().splitlines()
    assert expected == ["[INFO] fmt 1,234.568", "[WARN] 12%", "[ERROR] $1,234.57"]
    assert actual == expected, f"log lines {actual!r} vs TV literal lines {expected!r}"
    print(f"TV log numbers: {actual!r}")
