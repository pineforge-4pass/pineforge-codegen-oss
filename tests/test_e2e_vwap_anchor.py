"""E2E: every ``ta.vwap`` form either computes the anchored VWAP exactly or is
refused at transpile time (lane C4, defect 3).

TradingView (Pine v6 reference, ``ta.vwap``): ``anchor (series bool) The
condition that triggers the reset of VWAP calculations. When true,
calculations reset; when false, calculations proceed using the values
accumulated since the previous reset. Optional. The default is equivalent to
passing timeframe.change() with "1D" as its argument.``

The engine's ``ta::VWAP`` resets its accumulation only when the symbol's
session day changes -- exactly the default anchor -- and has no input for any
other. The codegen forwarded the 2-argument form's anchor to a
``compute()`` overload that does not exist (the TU failed to compile), and
dropped the 3-argument band form's anchor (a daily VWAP whatever the
anchor). Now:

* an anchor spelling ``timeframe.change("1D")`` (or ``"D"``) -- directly or
  through a never-reassigned binding -- is the default and runs on the
  engine's daily reset, exactly like ``ta.vwap(source)``; each such form is
  compared bar by bar (``@pf-trace``) with a spelled-out anchored VWAP
  (running sums of ``src * volume``, ``volume`` and ``src * src * volume``
  reset where ``timeframe.change("1D")`` is true), and a strategy trading on
  it must book the reference's trades;
* any other anchor is refused by ``transpile_json`` with a diagnostic
  naming the missing engine capability.

The reference's sums start at 0 on the first bar, as the engine's do. The
Pine reference adds "Calculations only begin the first time the anchor
condition becomes true. Until then, the function returns na.", and
``timeframe.change("1D")`` is false on the first bar, so TradingView's
default-anchored VWAP is na for the first partial session day while every
PineForge spelling of it -- ``ta.vwap(source)`` included -- already has
values there: a divergence of the engine's ``ta::VWAP`` on that first day
only, the same for every accepted form, left to the engine lane.

The reference rounds each product and sum on its own statement, as the
engine does (it is built with ``-ffp-contract=off``), so no FMA contraction
in the strategy TU can separate the two.

Interfaces: ``tests/_e2e.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._e2e import (
    Build, Outcome, derive_chart_feed, digest, execute_all, ok,
    per_bar_mismatches, skip_unless_e2e_env, summary, transpile_json,
)


HEADER = '//@version=6\nstrategy("e2e-c4-vwap", overlay=true)\n'
TRADE = ("if ta.crossover(close, x)\n"
         '    strategy.entry("L", strategy.long)\n'
         "if ta.crossunder(close, x)\n"
         '    strategy.close("L")\n')


def _reference(src: str, bands: str | None = None) -> str:
    """The VWAP of ``src`` anchored on ``timeframe.change("1D")``, spelled out
    with its sums starting at the first bar; with ``bands`` (a stdev
    multiplier) also its ``up`` / ``lo`` bands."""
    text = (f"refPv = {src} * volume\n"
            f"refPv2 = {src} * {src} * volume\n"
            'var float sPv = 0.0\nvar float sVol = 0.0\nvar float sPv2 = 0.0\n'
            'if timeframe.change("1D")\n'
            "    sPv := 0.0\n    sVol := 0.0\n    sPv2 := 0.0\n"
            "sPv := sPv + refPv\nsVol := sVol + volume\nsPv2 := sPv2 + refPv2\n"
            "x = sPv / sVol\n")
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

    def _trace(self) -> str:
        return TRACE_BANDS if self.bands is not None else TRACE_X

    def subject(self) -> Build:
        return Build(HEADER + self.subject_body + self._trace() + TRADE, trace=True)

    def reference(self) -> Build:
        return Build(HEADER + _reference(self.src, self.bands) + self._trace() + TRADE,
                     trace=True)


ACCEPTED: tuple[Accepted, ...] = (
    Accepted("two_arg_1D", 'x = ta.vwap(close, timeframe.change("1D"))\n'),
    Accepted("two_arg_D", 'x = ta.vwap(close, timeframe.change("D"))\n'),
    Accepted("two_arg_keywords",
             'x = ta.vwap(source=close, anchor=timeframe.change("1D"))\n'),
    Accepted("two_arg_alias",
             'newDay = timeframe.change("1D")\nx = ta.vwap(hlc3, newDay)\n', src="hlc3"),
    Accepted("two_arg_nested", 'x = ta.vwap(close, timeframe.change("1D")) * 1.0\n'),
    Accepted("bands_1D", '[x, up, lo] = ta.vwap(close, timeframe.change("1D"), 1.5)\n',
             bands="1.5"),
    Accepted("bands_keywords",
             '[x, up, lo] = ta.vwap(close, stdev_mult=1.5, anchor=timeframe.change("D"))\n',
             bands="1.5"),
    # Controls: the default anchor, spelled by omission.
    Accepted("one_arg", "x = ta.vwap(close)\n"),
    Accepted("bands_no_anchor", "[x, up, lo] = ta.vwap(close, stdev_mult=1.5)\n",
             bands="1.5"),
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


@dataclass(frozen=True)
class Refused:
    """``body`` is refused with ``ANCHOR_REFUSAL`` at ``(line, col)`` of its
    anchor (line 3 is the first body line)."""
    name: str
    body: str
    line: int
    col: int


REFUSED: tuple[Refused, ...] = (
    Refused("two_arg_weekly", 'x = ta.vwap(close, timeframe.change("W"))\n', 3, 20),
    Refused("two_arg_custom_bool", "x = ta.vwap(close, close > open)\n", 3, 20),
    Refused("two_arg_keyword_weekly",
            'x = ta.vwap(source=close, anchor=timeframe.change("1W"))\n', 3, 34),
    Refused("bands_weekly", '[x, up, lo] = ta.vwap(close, timeframe.change("W"), 1.0)\n', 3, 30),
    Refused("bands_session_anchor",
            "anchorTs = timestamp(\"America/New_York\", year, month, dayofmonth, 9, 30)\n"
            "isNewNy = time >= anchorTs and (na(time[1]) or time[1] < anchorTs)\n"
            "[x, up, lo] = ta.vwap(ohlc4, isNewNy, 1)\n", 5, 30),
    # A binding of the daily anchor that is reassigned, or persistent, is not
    # the default anchor.
    Refused("reassigned_alias",
            'a = timeframe.change("1D")\nif close > open\n    a := timeframe.change("W")\n'
            "x = ta.vwap(close, a)\n", 6, 20),
    Refused("var_alias", 'var a = timeframe.change("1D")\nx = ta.vwap(close, a)\n', 4, 20),
    # An input-selected anchor timeframe (the Pine reference's own example).
    Refused("input_timeframe",
            'tf = input.timeframe("1D", "Anchor")\nx = ta.vwap(close, timeframe.change(tf))\n',
            4, 20),
)
REFUSED_BY_NAME = {c.name: c for c in REFUSED}


@pytest.fixture(scope="session")
def outcomes(request, tmp_path_factory) -> dict[str, Outcome]:
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("e2e_vwap_anchor")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    builds: dict[str, Build] = {}
    for item in request.session.items:
        if getattr(item, "originalname", None) == "test_vwap_anchor_form_equals_spelled_out":
            case = ACCEPTED_BY_NAME[item.callspec.params["case_name"]]
            builds[f"{case.name}/subject"] = case.subject()
            builds[f"{case.name}/reference"] = case.reference()
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
