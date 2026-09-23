"""E2E: ``ta.valuewhen(condition, source, occurrence)`` returns the source at
the occurrence-th most recent bar where the condition held, for every
occurrence (lane C4, defect 2).

``ta::ValueWhen`` keeps ``max(1, max_occurrence + 1)`` values, the bound its
constructor takes (default 1), while ``compute(condition, source,
occurrence)`` reads ``values_[occurrence]``. The codegen passed the
occurrence to ``compute()`` only (``valuewhen`` sat in ``TA_NO_CTOR``), so the
object kept two values and every occurrence >= 2 read ``na`` on every bar.

Each case traces ``ta.valuewhen`` (``@pf-trace``) and compares it bar by bar
with a spelled-out reference -- the last four sources where the condition
held, shifted through ``var`` floats -- and requires a strategy trading on it
to book exactly the reference's trades. The input-backed occurrence also
runs under an override, which must move the trades onto the literal
occurrence's.

Interfaces: ``tests/_e2e.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests._e2e import (
    Build, Outcome, derive_chart_feed, digest, execute_all, ok,
    per_bar_mismatches, skip_unless_e2e_env, summary,
)


HEADER = ('//@version=6\nstrategy("e2e-c4-valuewhen", overlay=true)\n'
          "c = close > open and volume > volume[1]\n")
# The last four sources where ``c`` held, newest first.
REFERENCE_CHAIN = ("var float v0 = na\nvar float v1 = na\n"
                   "var float v2 = na\nvar float v3 = na\n"
                   "if c\n    v3 := v2\n    v2 := v1\n    v1 := v0\n    v0 := close\n")
TRADE_AND_TRACE = ("// @pf-trace x=x\n"
                   "if ta.crossover(close, x)\n"
                   '    strategy.entry("L", strategy.long)\n'
                   "if ta.crossunder(close, x)\n"
                   '    strategy.close("L")\n')


@dataclass(frozen=True)
class Case:
    """``subject`` spells ``x`` with ta.valuewhen, ``reference`` from the
    spelled-out chain; ``override`` also runs both under that input override,
    whose trades must equal the literal case ``override_equals``."""
    name: str
    subject_body: str
    reference_body: str
    override: dict | None = None
    override_equals: str | None = None

    def subject(self) -> Build:
        return Build(HEADER + self.subject_body + TRADE_AND_TRACE, self.override, trace=True)

    def reference(self) -> Build:
        return Build(HEADER + REFERENCE_CHAIN + self.reference_body + TRADE_AND_TRACE,
                     self.override, trace=True)


def _literal(k: int) -> Case:
    return Case(f"occurrence_{k}", f"x = ta.valuewhen(c, close, {k})\n", f"x = v{k}\n")


OCC_INPUT = 'k = input.int(2, "Occurrence")\n'
CASES: tuple[Case, ...] = (
    *(_literal(k) for k in (0, 1, 2, 3)),
    Case("input_occurrence", OCC_INPUT + "x = ta.valuewhen(c, close, k)\n",
         OCC_INPUT + "x = k == 0 ? v0 : k == 1 ? v1 : k == 2 ? v2 : v3\n",
         override={"Occurrence": 3}, override_equals="occurrence_3"),
    Case("keyword_occurrence",
         "x = ta.valuewhen(condition=c, source=close, occurrence=2)\n", "x = v2\n"),
    # Two call sites of one helper: each per-call-site clone keeps its own
    # occurrence's history.
    Case("user_function",
         "vw(src, n) => ta.valuewhen(c, src, n)\nx = vw(close, 3)\ny = vw(close, 2)\n"
         "// @pf-trace y=y\n",
         "x = v3\ny = v2\n// @pf-trace y=y\n"),
    # Nested in an expression.
    Case("nested", "x = ta.valuewhen(c, close, 2) * 1.0\n", "x = v2 * 1.0\n"),
)
CASES_BY_NAME = {c.name: c for c in CASES}
assert len(CASES_BY_NAME) == len(CASES), "duplicate case name"


def _builds_for(name: str) -> dict[str, Build]:
    case = CASES_BY_NAME[name]
    out = {f"{name}/subject": case.subject(), f"{name}/reference": case.reference()}
    if case.override_equals:
        lit = CASES_BY_NAME[case.override_equals]
        out[f"{lit.name}/subject"] = lit.subject()
    return out


@pytest.fixture(scope="session")
def outcomes(request, tmp_path_factory) -> dict[str, Outcome]:
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("e2e_valuewhen_occurrence")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    builds: dict[str, Build] = {}
    for item in request.session.items:
        if getattr(item, "originalname", None) == "test_valuewhen_equals_spelled_out_reference":
            builds.update(_builds_for(item.callspec.params["case_name"]))
    return execute_all(engine_root, feed, base, builds)


@pytest.mark.parametrize("case_name", list(CASES_BY_NAME))
def test_valuewhen_equals_spelled_out_reference(case_name: str, outcomes) -> None:
    case = CASES_BY_NAME[case_name]
    subject = ok(outcomes, f"{case_name}/subject")
    reference = ok(outcomes, f"{case_name}/reference")
    failures = []
    lines = []
    for tag in subject.trades:
        compared, mismatched, first = per_bar_mismatches(
            subject.traces[tag], reference.traces[tag], ("ta.valuewhen", "spelled-out"))
        a, b = subject.trades[tag], reference.trades[tag]
        if mismatched:
            failures.append(f"{tag}: ta.valuewhen differs from the spelled-out reference on "
                            f"{mismatched} of {compared} traced bars; first: {first}")
        if digest(a) != digest(b):
            failures.append(f"{tag}: trades differ: ta.valuewhen {summary(a)} vs "
                            f"spelled-out {summary(b)}")
        lines.append(f"{tag} {compared} bars equal, {summary(a)}")
    if case.override is not None:
        default, override = subject.trades["default"], subject.trades["override"]
        if default == override:
            failures.append(f"override {case.override} left the trades unchanged "
                            f"({summary(default)})")
        lit = ok(outcomes, f"{case.override_equals}/subject")
        if digest(override) != digest(lit.trades["default"]):
            failures.append(
                f"override {case.override} gives {summary(override)}, not the "
                f"{case.override_equals} build's {summary(lit.trades['default'])}")
        else:
            lines.append(f"override == {case.override_equals}")
    assert not failures, f"[{case_name}] " + "\n  ".join(failures)
    print(f"E2E valuewhen {case_name}: ta.valuewhen == spelled-out  " + "  ".join(lines))
