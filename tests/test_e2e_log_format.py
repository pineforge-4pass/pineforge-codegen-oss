"""E2E: ``log.info`` / ``log.warning`` / ``log.error(formatString, arg0,
...)`` log the formatted string, arguments included (lane C5, defect 5).

TradingView (Pine v6 reference, ``log.info``): "Converts the formatting
string and value(s) into a formatted string, and sends the result to the
"Pine logs" menu". The User Manual (Strings, "Formatting strings"): "The
second overloads of all log.*() functions have the same parameter signature
and formatting behaviors as str.format()." The codegen emitted
``pine_log_info(<formatString>)`` and dropped every argument, so a log line
read ``close {0}`` whatever the bar.

A log call with arguments now lowers exactly like ``log.info(str.format(
formatString, arg0, ...))``: the engine's ``pine_str_format`` substitutes each
``{i}`` with its argument, rendered as the codegen renders every
``str.format`` argument (a string as is, a bool as ``true`` / ``false``, a
number through ``std::to_string``), and leaves a placeholder with no argument
as its literal text, as TradingView does ("If a placeholder refers to a
nonexistent argument, the formatted result treats that placeholder as a
literal character sequence"). That number rendering is PineForge's
``str.format`` rendering, not TradingView's: its default numeric format is
``#,###.###`` and it reads ``{0,number,...}`` patterns and apostrophe quoting,
which the engine's ``str_format`` does not. The single-argument
``log.info(message)`` overload logs its message as is.

Each case logs on the first three bars; the run's stderr (the engine writes
``[INFO]`` / ``[WARN]`` / ``[ERROR]`` lines there) must hold exactly the lines
built from those bars' feed values, and the same lines as the reference
spelled with ``str.format``.

Interfaces: ``tests/_e2e.py``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass

import pytest

from tests._e2e import (
    Build, Outcome, derive_chart_feed, execute_all, ok, skip_unless_e2e_env,
)


HEADER = ('//@version=6\nstrategy("e2e-c5-log-format", overlay=true)\n'
          "var int n = 0\nn += 1\n")
TRADE = ("x = ta.ema(close, 9)\n"
         "if ta.crossover(close, x)\n"
         '    strategy.entry("L", strategy.long)\n'
         "if ta.crossunder(close, x)\n"
         '    strategy.close("L")\n')
LOG_PREFIXES = ("[INFO] ", "[WARN] ", "[ERROR] ")


def _cpp_double(v: float) -> str:
    """``std::to_string(double)``: printf ``%f``."""
    return "%f" % v


@dataclass(frozen=True)
class LogCase:
    """``call`` logs on the first three bars; ``reference`` is the same log
    spelled with str.format; ``expected(bar)`` the line bar ``bar`` (a feed
    row) must log."""
    name: str
    call: str
    reference: str
    expected: object  # Callable[[dict], str]

    def _source(self, call: str) -> str:
        return HEADER + f"s = \"tag\"\nif n <= 3\n    {call}\n" + TRADE

    def subject(self) -> Build:
        return Build(self._source(self.call))

    def reference_build(self) -> Build:
        return Build(self._source(self.reference))


def _up(bar: dict) -> str:
    return "true" if float(bar["close"]) > float(bar["open"]) else "false"


CASES: tuple[LogCase, ...] = (
    LogCase("info_arguments",
            'log.info("bar {0}: close {1} volume {2} up {3} tag {4}", n, close, volume, close > open, s)',
            'log.info(str.format("bar {0}: close {1} volume {2} up {3} tag {4}", n, close, volume, close > open, s))',
            lambda bar: (f"[INFO] bar {bar['n']}: close {_cpp_double(float(bar['close']))} "
                         f"volume {_cpp_double(float(bar['volume']))} up {_up(bar)} tag tag")),
    LogCase("warning_reordered",
            'log.warning("{1} before {0}", "a", high)',
            'log.warning(str.format("{1} before {0}", "a", high))',
            lambda bar: f"[WARN] {_cpp_double(float(bar['high']))} before a"),
    LogCase("error_repeated",
            'log.error("n={0}, again {0}", n)',
            'log.error(str.format("n={0}, again {0}", n))',
            lambda bar: f"[ERROR] n={bar['n']}, again {bar['n']}"),
    LogCase("placeholder_without_argument",
            'log.info("{0} and {5}", low)',
            'log.info(str.format("{0} and {5}", low))',
            lambda bar: f"[INFO] {_cpp_double(float(bar['low']))} and {{5}}"),
    # Control: the one-argument overload logs its message as is.
    LogCase("message_only", 'log.info("plain {0} text")', 'log.info("plain {0} text")',
            lambda bar: "[INFO] plain {0} text"),
)
CASES_BY_NAME = {c.name: c for c in CASES}
assert len(CASES_BY_NAME) == len(CASES), "duplicate case name"


@pytest.fixture(scope="session")
def feed_and_outcomes(request, tmp_path_factory):
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("e2e_log_format")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    builds: dict[str, Build] = {}
    for item in request.session.items:
        if getattr(item, "originalname", None) == "test_log_line_carries_formatted_arguments":
            case = CASES_BY_NAME[item.callspec.params["case_name"]]
            builds[f"{case.name}/subject"] = case.subject()
            builds[f"{case.name}/reference"] = case.reference_build()
    return feed, execute_all(engine_root, feed, base, builds)


def _log_lines(outcome: Outcome) -> list[str]:
    return [line for line in outcome.logs["default"].splitlines()
            if line.startswith(LOG_PREFIXES)]


@pytest.mark.parametrize("case_name", list(CASES_BY_NAME))
def test_log_line_carries_formatted_arguments(case_name: str, feed_and_outcomes) -> None:
    feed, outcomes = feed_and_outcomes
    case = CASES_BY_NAME[case_name]
    subject = ok(outcomes, f"{case_name}/subject")
    reference = ok(outcomes, f"{case_name}/reference")
    with feed.open() as fh:
        rows = [dict(row, n=str(i + 1)) for i, row in zip(range(3), csv.DictReader(fh))]
    expected = [case.expected(bar) for bar in rows]
    got, ref = _log_lines(subject), _log_lines(reference)
    failures = []
    if got != expected:
        failures.append(f"logged {got!r}, expected {expected!r}")
    if got != ref:
        failures.append(f"logged {got!r}, the str.format reference logged {ref!r}")
    assert not failures, f"[{case_name}] " + "\n  ".join(failures)
    print(f"E2E log {case_name}: {len(got)} lines == expected == str.format reference; "
          f"first {got[0]!r}")
