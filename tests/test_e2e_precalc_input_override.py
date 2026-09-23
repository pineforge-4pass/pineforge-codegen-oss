"""E2E: an input override reaches every input-backed TA length, whatever the
spelling around the TA call (lane C4, defect 1).

A static chart TA site -- one whose ``compute()`` arguments are bar data --
is precalculated: ``prepare_script_run`` calls ``precalculate()`` before the
first bar (the engine allows it for a run with no magnifier and empty
timeframes, which is how ``run_strategy.py`` runs), and the site reads
``_precalc_<member>[bar_index_]`` from then on. ``precalculate()`` re-built
each such TA from its length's *compile-time* value -- the input's default,
or the placeholder ``1`` when the length does not fold (a
``timeframe.*``-dependent one) -- while the input-override-aware rebuild
(``_ta_initialized_``) only runs later, in ``on_bar``. A TA call assigned
directly (``x = ta.ema(close, len)``) never reads the precalculated series,
so it resized; the same call nested in any expression did not.

Each case spells an ``input.int(14, "Length")`` length somewhere a TA call
can sit and runs it at the default and under ``{"Length": 31}``; its twins
spell the literal length (14 and 31). The subject must book exactly its
twin's trades in both runs, and the override must move them.

Interfaces: ``tests/_e2e.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests._e2e import (
    Build, Outcome, derive_chart_feed, digest, execute_all, ok,
    skip_unless_e2e_env, summary,
)


HEADER = '//@version=6\nstrategy("e2e-c4-precalc", overlay=true)\n'
LEN_DECL = 'len = input.int(14, "Length")\n'
DEFAULT, OVERRIDE = 14, 31


def _long_on_cross(a: str, b: str) -> str:
    return (f"if ta.crossover({a}, {b})\n"
            '    strategy.entry("L", strategy.long)\n'
            f"if ta.crossunder({a}, {b})\n"
            '    strategy.close("L")\n')


def _vs_close(expr: str) -> str:
    """``x`` is a price-scale line: trade close crossing it."""
    return f"x = {expr}\n" + _long_on_cross("close", "x")


def _vs_self(expr: str) -> str:
    """Any scale: trade ``x`` crossing its own 5-bar SMA."""
    return f"x = {expr}\ns = ta.sma(x, 5)\n" + _long_on_cross("x", "s")


@dataclass(frozen=True)
class Case:
    """``{len}`` in ``body`` is the input-backed length: ``len`` in the
    subject, the literal value in its twins. ``twin_body`` spells the twins
    when the subject's length is not a plain substitution (a length that
    reduces to ``len`` on this intraday chart)."""
    name: str
    body: str
    twin_body: str | None = None

    def subject(self) -> Build:
        return Build(HEADER + LEN_DECL + self.body.replace("{len}", "len"),
                     {"Length": OVERRIDE})

    def twin(self, value: int) -> Build:
        body = self.twin_body if self.twin_body is not None else self.body
        return Build(HEADER + body.replace("{len}", str(value)))


CASES: tuple[Case, ...] = (
    # Nested in arithmetic.
    Case("arith", _vs_close("ta.ema(close, {len}) * 1.0")),
    # Inside a function call. ``ta.change``'s length is also a compute()
    # argument; ``nz`` wraps a ctor-only length.
    Case("math_abs_change", _vs_self("math.abs(ta.change(close, {len}))")),
    Case("nz_call", _vs_close("nz(ta.ema(close, {len}), close)")),
    # In a condition.
    Case("condition",
         "bull = ta.rsi(close, {len}) > 50\n"
         'if bull\n    strategy.entry("L", strategy.long)\n'
         'else\n    strategy.close("L")\n'),
    # In a user function.
    Case("user_function",
         "f(n) => ta.ema(close, n) * 1.0\n" + _vs_close("f({len})")),
    # Inside request.security.
    Case("request_security",
         _vs_close('request.security(syminfo.tickerid, "60", ta.ema(close, {len}) * 1.0)')),
    # The length an expression of inputs, spelled at the call and bound.
    Case("length_expression", _vs_close("ta.ema(close, {len} * 2) * 1.0")),
    Case("derived_binding", "n = {len} * 2\n" + _vs_close("ta.ema(close, n) * 1.0")),
    # A length that does not fold at transpile time: precalculate() sized it
    # with the placeholder 1 even at the default.
    Case("timeframe_conditional",
         _vs_close("ta.ema(close, timeframe.isintraday ? {len} : 20) * 1.0"),
         twin_body=_vs_close("ta.ema(close, {len}) * 1.0")),
    # A history read of the call ([1]), through the generic and the
    # highest/lowest precalculated-history lowerings.
    Case("history_read", _vs_close("ta.sma(close, {len})[1]")),
    Case("highest_lowest_history",
         "if close > ta.highest(high, {len})[1]\n    strategy.entry(\"L\", strategy.long)\n"
         "if close < ta.lowest(low, {len})[1]\n    strategy.close(\"L\")\n"),
    # Below a top-level lazy edge (evaluated every bar, hoisted).
    Case("lazy_and_rhs",
         "c = bar_index % 2 == 0 and close > ta.sma(close, {len})[1]\n"
         'if c\n    strategy.entry("L", strategy.long)\n'
         'if close < ta.sma(close, {len})\n    strategy.close("L")\n'),
)
CASES_BY_NAME = {c.name: c for c in CASES}
assert len(CASES_BY_NAME) == len(CASES), "duplicate case name"


def _builds_for(name: str) -> dict[str, Build]:
    case = CASES_BY_NAME[name]
    return {f"{name}/subject": case.subject(),
            f"{name}/lit_{DEFAULT}": case.twin(DEFAULT),
            f"{name}/lit_{OVERRIDE}": case.twin(OVERRIDE)}


@pytest.fixture(scope="session")
def outcomes(request, tmp_path_factory) -> dict[str, Outcome]:
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("e2e_precalc_input_override")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    builds: dict[str, Build] = {}
    for item in request.session.items:
        if getattr(item, "originalname", None) == "test_input_override_reaches_nested_ta_length":
            builds.update(_builds_for(item.callspec.params["case_name"]))
    return execute_all(engine_root, feed, base, builds)


@pytest.mark.parametrize("case_name", list(CASES_BY_NAME))
def test_input_override_reaches_nested_ta_length(case_name: str, outcomes) -> None:
    subject = ok(outcomes, f"{case_name}/subject")
    lit_default = ok(outcomes, f"{case_name}/lit_{DEFAULT}").trades["default"]
    lit_override = ok(outcomes, f"{case_name}/lit_{OVERRIDE}").trades["default"]
    default, override = subject.trades["default"], subject.trades["override"]
    failures = []
    if digest(default) != digest(lit_default):
        failures.append(f"default: {summary(default)} vs the literal {DEFAULT} twin's "
                        f"{summary(lit_default)}")
    if digest(override) != digest(lit_override):
        failures.append(f"override {subject.build.overrides}: {summary(override)} vs the "
                        f"literal {OVERRIDE} twin's {summary(lit_override)}")
    if default == override:
        failures.append(f"override {subject.build.overrides} left the trades unchanged "
                        f"({summary(default)})")
    assert not failures, f"[{case_name}] " + "\n  ".join(failures)
    print(f"E2E precalc {case_name}: default {summary(default)} == literal {DEFAULT}  "
          f"override {subject.build.overrides} {summary(override)} == literal {OVERRIDE}")
