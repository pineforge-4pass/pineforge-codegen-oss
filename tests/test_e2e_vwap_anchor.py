"""E2E: omitted-anchor VWAP keeps the historical session-day class while every
explicit anchor, including timeframe.change("1D"/"D"), uses the TA1 anchored
class. The tests compare traced values and trades with a spelled-out running
sum and assert the first-value bars witnessed by the public TradingView probe
in ``tests/fixtures/c7_tv_evidence``.

The old-engine compatibility branch is compiled separately against the
pre-TA1 engine headers; it retains the former session-day lowering there.

Interfaces: ``tests/_e2e.py``.
"""


from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._e2e import (
    Build, Outcome, derive_chart_feed, digest, execute_all, ok,
    per_bar_mismatches, reference_codegen, skip_unless_e2e_env, summary,
    transpile_json,
)


HEADER = '//@version=6\nstrategy("e2e-c4-vwap", overlay=true)\n'
TRADE = ("if ta.crossover(close, x)\n"
         '    strategy.entry("L", strategy.long)\n'
         "if ta.crossunder(close, x)\n"
         '    strategy.close("L")\n')


def _reference(src: str, bands: str | None = None,
               anchor: str = 'timeframe.change("1D")',
               wait_for_anchor: bool = False) -> str:
    """The VWAP of ``src`` anchored on ``timeframe.change("1D")``, spelled out
    with its sums starting at the first bar; with ``bands`` (a stdev
    multiplier) also its ``up`` / ``lo`` bands."""
    text = (f"refPv = {src} * volume\n"
            f"refPv2 = {src} * {src} * volume\n"
            'var float sPv = 0.0\nvar float sVol = 0.0\nvar float sPv2 = 0.0\n'
            f"if {anchor}\n"
            "    sPv := 0.0\n    sVol := 0.0\n    sPv2 := 0.0\n"
            "sPv := sPv + refPv\nsVol := sVol + volume\nsPv2 := sPv2 + refPv2\n"
            + ("x = (started ? sPv / sVol : na)\n" if wait_for_anchor
               else "x = sPv / sVol\n"))
    if wait_for_anchor:
        text = text.replace(
            "var float sPv2 = 0.0\n",
            "var float sPv2 = 0.0\nvar bool started = false\n",
        ).replace(
            f"if {anchor}\n",
            f"if {anchor}\n    started := true\n",
        )
    if bands is not None:
        text += ("refMeanSq = x * x\n"
                 "refVar = sPv2 / sVol - refMeanSq\n"
                 "refSd = math.sqrt(math.max(refVar, 0.0))\n"
                 f"refOff = {bands} * refSd\n"
                 "up = x + refOff\nlo = x - refOff\n")
    return text


TRACE_X = "// @pf-trace x=x\n"
TRACE_BANDS = "// @pf-trace x=x\n// @pf-trace up=up\n// @pf-trace lo=lo\n"


@dataclass(frozen=True)
class Accepted:
    name: str
    subject_body: str
    src: str = "close"
    bands: str | None = None
    anchor: str = 'timeframe.change("1D")'
    wait_for_anchor: bool = False

    def _trace(self) -> str:
        return TRACE_BANDS if self.bands is not None else TRACE_X

    def subject(self) -> Build:
        return Build(HEADER + self.subject_body + self._trace() + TRADE, trace=True)

    def reference(self) -> Build:
        return Build(HEADER + _reference(self.src, self.bands, self.anchor,
                                         self.wait_for_anchor) + self._trace() + TRADE,
                     trace=True)


ACCEPTED: tuple[Accepted, ...] = (
    Accepted("two_arg_1D", 'x = ta.vwap(close, timeframe.change("1D"))\n',
             wait_for_anchor=True),
    Accepted("two_arg_D", 'x = ta.vwap(close, timeframe.change("D"))\n',
             wait_for_anchor=True),
    Accepted("two_arg_keywords",
             'x = ta.vwap(source=close, anchor=timeframe.change("1D"))\n',
             wait_for_anchor=True),
    Accepted("two_arg_alias",
             'newDay = timeframe.change("1D")\nx = ta.vwap(hlc3, newDay)\n',
             src="hlc3", wait_for_anchor=True),
    Accepted("two_arg_nested", 'x = ta.vwap(close, timeframe.change("1D")) * 1.0\n',
             wait_for_anchor=True),
    Accepted("bands_1D", '[x, up, lo] = ta.vwap(close, timeframe.change("1D"), 1.5)\n',
             bands="1.5", wait_for_anchor=True),
    Accepted("bands_keywords",
             '[x, up, lo] = ta.vwap(close, stdev_mult=1.5, anchor=timeframe.change("D"))\n',
             bands="1.5", wait_for_anchor=True),
    # Controls: the default anchor, spelled by omission.
    Accepted("one_arg", "x = ta.vwap(close)\n"),
    Accepted("bands_no_anchor", "[x, up, lo] = ta.vwap(close, stdev_mult=1.5)\n",
             bands="1.5"),
    Accepted("two_arg_weekly", 'x = ta.vwap(close, timeframe.change("W"))\n',
             anchor='timeframe.change("W")', wait_for_anchor=True),
    Accepted("two_arg_custom", "x = ta.vwap(close, close > open)\n",
             anchor="close > open", wait_for_anchor=True),
    Accepted("bands_custom", "[x, up, lo] = ta.vwap(close, close > open, 1.5)\n",
             bands="1.5", anchor="close > open", wait_for_anchor=True),
)
ACCEPTED_BY_NAME = {c.name: c for c in ACCEPTED}


ANCHOR_REFUSAL = (
    "ta.vwap anchor is not supported: the engine's ta::VWAP resets its "
    "accumulation only when the symbol's session day changes (the default "
    'anchor, timeframe.change("1D")) and has no reset-on-anchor input, so any '
    "other anchor would silently compute a daily VWAP. — Omit the anchor or "
    'pass timeframe.change("1D"). Another anchor needs engine support: a '
    "ta::VWAP that resets on a bar where the anchor is true and returns na "
    "until it first is."
)


ANCHOR_APPROXIMATION = (
    "ta.vwap anchor is approximated: the engine's ta::VWAP has no "
    "reset-on-anchor input yet and resets only when the symbol's session day "
    "changes, so this band form ignores its anchor and runs the session-day "
    "VWAP. — Omit the anchor or pass timeframe.change(\"1D\") to run it "
    "exactly."
)

# The codegen before lane C4 (db003cb, origin/main when C4 began its
# amendment): the band form dropped its anchor.
PRE_C4 = "db003cbde884b5a40a99fc9aa72b998fb7279134"
_SESSION_ANCHOR = ("anchorTs = timestamp(\"America/New_York\", year, month, dayofmonth, 9, 30)\n"
                   "isNewNy = time >= anchorTs and (na(time[1]) or time[1] < anchorTs)\n")


@dataclass(frozen=True)
class Approximated:
    """A band form with a non-default anchor: accepted with
    ``ANCHOR_APPROXIMATION`` at ``(line, col)`` of its anchor, and trading
    exactly like ``PRE_C4``'s transpile of ``pre_c4_body`` (the same body; a
    keyword spelling, which did not compile there, uses its positional
    twin)."""
    name: str
    body: str
    line: int
    col: int
    pre_c4_body: str | None = None

    def subject(self) -> Build:
        return Build(HEADER + self.body + TRADE)

    def pre_c4(self, codegen) -> Build:
        return Build(HEADER + (self.pre_c4_body or self.body) + TRADE, codegen=codegen)


APPROXIMATED: tuple[Approximated, ...] = ()
APPROXIMATED_BY_NAME = {c.name: c for c in APPROXIMATED}


@dataclass(frozen=True)
class Refused:
    """``body`` is refused with ``ANCHOR_REFUSAL`` at ``(line, col)`` of its
    anchor (line 3 is the first body line)."""
    name: str
    body: str
    line: int
    col: int


REFUSED: tuple[Refused, ...] = ()
REFUSED_BY_NAME = {c.name: c for c in REFUSED}


FIRST_VALUE_SOURCE = (
    HEADER +
    'vDefault = ta.vwap(close)\n'
    'vExplicit = ta.vwap(close, timeframe.change("1D"))\n'
    'vAnchor5 = ta.vwap(close, bar_index == 5)\n'
    '// @pf-trace vDefault=vDefault\n'
    '// @pf-trace vExplicit=vExplicit\n'
    '// @pf-trace vAnchor5=vAnchor5\n'
)


@pytest.fixture(scope="session")
def outcomes(request, tmp_path_factory) -> dict[str, Outcome]:
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("e2e_vwap_anchor")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    builds: dict[str, Build] = {}
    pre_c4 = reference_codegen(PRE_C4)
    for item in request.session.items:
        name = getattr(item, "originalname", None)
        if name == "test_vwap_anchor_form_equals_spelled_out":
            case = ACCEPTED_BY_NAME[item.callspec.params["case_name"]]
            builds[f"{case.name}/subject"] = case.subject()
            builds[f"{case.name}/reference"] = case.reference()
        elif name == "test_vwap_explicit_anchor_first_values":
            builds["first_values"] = Build(FIRST_VALUE_SOURCE, trace=True)
        elif (name == "test_band_form_anchor_is_approximated_with_a_warning"
              and getattr(item, "callspec", None) is not None
              and item.callspec.params.get("case_name") in APPROXIMATED_BY_NAME):
            approx = APPROXIMATED_BY_NAME[item.callspec.params["case_name"]]
            builds[f"{approx.name}/subject"] = approx.subject()
            if pre_c4 is not None:
                builds[f"{approx.name}/pre_c4"] = approx.pre_c4(pre_c4)
    return execute_all(engine_root, feed, base, builds)


@pytest.mark.parametrize("case_name", list(ACCEPTED_BY_NAME))
def test_vwap_anchor_form_equals_spelled_out(case_name: str, outcomes) -> None:
    subject = ok(outcomes, f"{case_name}/subject")
    reference = ok(outcomes, f"{case_name}/reference")
    compared, mismatched, first = per_bar_mismatches(
        subject.traces["default"], reference.traces["default"], ("ta.vwap", "spelled-out"))
    a, b = subject.trades["default"], reference.trades["default"]
    failures = []
    if mismatched:
        failures.append(f"ta.vwap differs from the spelled-out anchored VWAP on "
                        f"{mismatched} of {compared} traced values; first: {first}")
    if digest(a) != digest(b):
        failures.append(f"trades differ: ta.vwap {summary(a)} vs spelled-out {summary(b)}")
    assert not failures, f"[{case_name}] " + "\n  ".join(failures)
    print(f"E2E vwap {case_name}: ta.vwap == spelled-out anchored VWAP  "
          f"{compared} traced values equal, {summary(a)}")


def test_vwap_explicit_anchor_first_values(outcomes) -> None:
    """The public TV probe witnesses the first-value bars on both feeds:
    omitted-anchor VWAP starts on bar 0, the explicit daily anchor starts on
    the first daily change, and ``bar_index == 5`` starts on bar 5."""
    subject = ok(outcomes, "first_values")
    expected = {"vDefault": 0, "vExplicit": 96, "vAnchor5": 5}
    failures = []
    for name, first_bar in expected.items():
        records = [r for r in subject.traces["default"] if r["name"] == name]
        finite = [r for r in records if not math.isnan(r["value"])]
        if not finite:
            failures.append(f"{name} never became finite")
        elif finite[0]["bar_index"] != first_bar:
            failures.append(
                f"{name} first finite bar {finite[0]['bar_index']} != {first_bar}"
            )
    assert not failures, "first-value bars diverged from the public TradingView probe: " + "; ".join(failures)
    print("E2E vwap first values: omitted=bar 0, explicit daily=bar 96, bar-index anchor=bar 5")


@pytest.mark.parametrize("case_name", list(APPROXIMATED_BY_NAME))
def test_band_form_anchor_is_approximated_with_a_warning(case_name: str, outcomes) -> None:
    case = APPROXIMATED_BY_NAME[case_name]
    subject = ok(outcomes, f"{case_name}/subject")
    if f"{case_name}/pre_c4" not in outcomes:
        pytest.skip(f"the pre-C4 codegen ({PRE_C4[:12]}) is not in this checkout's history")
    pre_c4 = ok(outcomes, f"{case_name}/pre_c4")
    warnings = [(d["line"], d["col"], d["message"])
                for d in subject.transpiled.get("diagnostics", [])
                if d["severity"] == "warning" and d["message"].startswith("ta.vwap")]
    assert warnings == [(case.line, case.col, ANCHOR_APPROXIMATION)], (
        f"[{case_name}] warnings {subject.transpiled.get('diagnostics')}")
    a, b = subject.trades["default"], pre_c4.trades["default"]
    assert digest(a) == digest(b), (
        f"[{case_name}] trades {summary(a)} vs the pre-C4 build's {summary(b)}")
    same_cpp = subject.transpiled["cpp"] == pre_c4.transpiled["cpp"]
    print(f"E2E vwap {case_name}: warned at {case.line}:{case.col}, trades == pre-C4 "
          f"({PRE_C4[:7]}) build  {summary(a)}  C++ identical to pre-C4: {same_cpp}")


@pytest.mark.parametrize("case_name", list(REFUSED_BY_NAME))
def test_vwap_unsupported_anchor_is_refused(case_name: str, tmp_path: Path) -> None:
    case = REFUSED_BY_NAME[case_name]
    pine = tmp_path / "strategy.pine"
    pine.write_text(HEADER + case.body + TRADE)
    result = transpile_json(pine)
    assert not result["ok"], f"[{case_name}] transpiled; its anchor is not the default"
    assert [(d["line"], d["col"], d["message"]) for d in result["diagnostics"]] == [
        (case.line, case.col, ANCHOR_REFUSAL)], f"[{case_name}] {result['diagnostics']}"
    print(f"E2E vwap {case_name}: refused at {case.line}:{case.col}")
