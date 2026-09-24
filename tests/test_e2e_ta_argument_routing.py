"""E2E: every argument of a ``ta.*`` call reaches the engine parameter it
belongs to, or the call is refused at transpile time (lane C5, defect 1).

The codegen routes a TA call's arguments by position: some to the engine
class's constructor, the rest to ``compute()``. Several TradingView
parameters had nowhere to go:

* ``ta.alma(series, length, offset, sigma, floor)``: ``floor`` reached
  ``ALMA::compute(src)`` (the TU did not compile) and ``floor = true`` by
  keyword was dropped (a silently unfloored ALMA). TradingView: ``floor
  (simple bool) An optional parameter. Specifies whether the offset
  calculation is floored before ALMA is calculated. Default value is false.``
  The engine's ``ta::ALMA`` has no floor: ``m = offset * (length - 1)``.
* ``ta.kc`` / ``ta.kcw(series, length, mult, useTrueRange)``:
  ``useTrueRange`` reached a 4-argument ``compute()`` that does not exist,
  and by keyword it was dropped. TradingView: ``useTrueRange (simple bool) An
  optional parameter. Specifies if True Range is used; default is true. If
  the value is false, the range will be calculated with the expression (high
  - low).`` The engine's ``ta::KC`` always averages the true range.
* TradingView names the source of ``ta.alma`` / ``ta.bb`` / ``ta.bbw`` /
  ``ta.cmo`` / ``ta.kc`` / ``ta.kcw`` ``series``; the registry called it
  ``source``, so ``series=close`` was dropped and ``compute()`` lost its
  source (the TU did not compile).
* ``ta.highestbars(length)`` / ``ta.lowestbars(length)`` ("One arg version:
  length is the number of bars back. Algorithm uses high as a source
  series.") sent the length to ``compute()`` as the source, and
  ``ta.highest(length=10)`` lost its default source.
* ``ta.pivot_point_levels(type, anchor, developing)`` dropped ``anchor`` and
  ``developing``: every anchor computed the previous bar's pivots.
* An argument that binds to no parameter -- a misspelt keyword, one too many,
  one given twice, a required one missing -- was dropped, shifted into the
  next constructor slot (``ta.alma(close, 9, sigma=4)`` built
  ``ALMA(9, 4)``, offset 4) or failed the C++ compile.

Each spelling now ends one of three ways:

* routed exactly: compared bar by bar (``@pf-trace``) with a spelled-out
  reference -- TradingView's own "the same on pine" formula where the
  reference gives one, else the documented equivalent call -- and must book
  the reference's trades;
* approximated with a warning: a value the engine class cannot compute, in a
  spelling that transpiled and compiled before this lane (a keyword
  ``floor`` / ``useTrueRange``, whose keyword was dropped; any
  ``pivot_point_levels`` anchor or ``developing``), keeps that lowering. The
  support checker warns, naming the approximation and the missing engine
  capability, and the strategy must book exactly the trades the same script
  books when transpiled by ``PRE_C5`` (e6a64cd). Refusing such a script
  would turn one that grades against TradingView today into an error;
* refused, with the exact diagnostic: a spelling that never compiled (the
  positional ``floor`` / ``useTrueRange`` reached a ``compute()`` overload
  that does not exist), or one TradingView itself rejects (a missing required
  argument, an unknown parameter, one too many, one given twice).

The ALMA and Keltner references round each product and sum on its own
statement, as the engine does (it is built with ``-ffp-contract=off``), so no
FMA contraction in the strategy TU can separate the two.

The engine's ``ta::KC`` returns na for all three bands on a bar where its
range average is na -- the first bar, whose true range has no previous close
-- while TradingView's ``f_kc`` returns its basis ``ta.ema(src, length)``
there. The reference spells that first-bar rule out (``m`` is na while the
range average is), so the comparison isolates the routing; the one-bar
middle-band divergence belongs to the engine lane.

Interfaces: ``tests/_e2e.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._e2e import (
    Build, Outcome, derive_chart_feed, digest, execute_all, ok,
    per_bar_mismatches, reference_codegen, skip_unless_e2e_env, summary,
    transpile_json,
)


HEADER = '//@version=6\nstrategy("e2e-c5-ta-arguments", overlay=true)\n'


def _trade(a: str, b: str, exit_on: str | None = None) -> str:
    """Long when ``a`` crosses over ``b``; flat when it crosses under
    ``exit_on`` (default ``b``)."""
    return (f"if ta.crossover({a}, {b})\n"
            '    strategy.entry("L", strategy.long)\n'
            f"if ta.crossunder({a}, {exit_on or b})\n"
            '    strategy.close("L")\n')


def _traces(*names: str) -> str:
    return "".join(f"// @pf-trace {n}={n}\n" for n in names)


# TradingView's pine_alma with length 12, offset 0.7, sigma 4.5, floor false
# (m = offset * (windowsize - 1)), each step on its own statement.
ALMA_REFERENCE = (
    "refM = 0.7 * (12 - 1)\n"
    "refS = 12 / 4.5\n"
    "float refNorm = 0.0\n"
    "float refSum = 0.0\n"
    "for i = 0 to 11\n"
    "    d = i - refM\n"
    "    w = math.exp(-(d * d) / (2.0 * refS * refS))\n"
    "    refNorm := refNorm + w\n"
    "    p = close[11 - i] * w\n"
    "    refSum := refSum + p\n"
    "x = refSum / refNorm\n"
)

ALMA_FLOOR_REFERENCE = ALMA_REFERENCE.replace(
    "refM = 0.7 * (12 - 1)",
    "refM = math.floor(0.7 * (12 - 1))",
)

# TradingView's f_kc(close, 20, 1.5, true): basis = ta.ema(src, length),
# span = ta.tr, rangeEma = ta.ema(span, length), bands basis +- rangeEma * mult.
# ``ta.tr`` is spelled ``ta.tr(false)``, which TradingView declares it
# equivalent to ("True range, equivalent to ta.tr(handle_na = false)"):
# PineForge's bare ``ta.tr`` reads high - low on the first bar (handle_na true).
KC_REFERENCE = (
    "refBasis = ta.ema(close, 20)\n"
    "refSpan = ta.tr(false)\n"
    "refRangeEma = ta.ema(refSpan, 20)\n"
    "refOff = refRangeEma * 1.5\n"
    "m = refBasis\n"
    "u = refBasis + refOff\n"
    "l = refBasis - refOff\n"
)
# TradingView's f_kcw: ((basis + rangeEma * mult) - (basis - rangeEma * mult)) / basis.
KCW_REFERENCE = KC_REFERENCE + "x = (u - l) / m\n"

KC_HIGH_LOW_REFERENCE = (
    "refBasis = ta.ema(close, 20)\n"
    "refSpan = high - low\n"
    "refRangeEma = ta.ema(refSpan, 20)\n"
    "refOff = refRangeEma * 1.5\n"
    "m = refBasis\n"
    "u = refBasis + refOff\n"
    "l = refBasis - refOff\n"
)
KCW_HIGH_LOW_REFERENCE = KC_HIGH_LOW_REFERENCE + "x = (u - l) / m\n"
PIVOT_TRADE = (
    'if not na(x) and strategy.position_size == 0\n'
    '    strategy.entry("L", strategy.long)\n'
)


@dataclass(frozen=True)
class Routed:
    """``subject_body`` must equal ``reference_body`` on every traced name and
    book the same trades."""
    name: str
    subject_body: str
    reference_body: str
    traced: tuple[str, ...] = ("x",)
    trade: str = _trade("close", "x")

    def subject(self) -> Build:
        return Build(HEADER + self.subject_body + _traces(*self.traced) + self.trade,
                     trace=True)

    def reference(self) -> Build:
        return Build(HEADER + self.reference_body + _traces(*self.traced) + self.trade,
                     trace=True)


KC_TRADE = _trade("close", "u", exit_on="l")

ROUTED: tuple[Routed, ...] = (
    # ta.alma: floor false is the engine's ALMA; it must not reach compute().
    Routed("alma_floor_false", "x = ta.alma(close, 12, 0.7, 4.5, false)\n", ALMA_REFERENCE),
    Routed("alma_floor_false_keyword",
           "x = ta.alma(close, 12, 0.7, 4.5, floor=false)\n", ALMA_REFERENCE),
    Routed("alma_floor_false_binding",
           "noFloor = false\nx = ta.alma(close, 12, 0.7, 4.5, noFloor)\n", ALMA_REFERENCE),
    Routed("alma_floor_true", "x = ta.alma(close, 12, 0.7, 4.5, true)\n",
           ALMA_FLOOR_REFERENCE),
    Routed("alma_floor_true_keyword",
           "x = ta.alma(close, 12, 0.7, 4.5, floor=true)\n", ALMA_FLOOR_REFERENCE),
    Routed("alma_series_keyword",
           "x = ta.alma(series=close, length=12, offset=0.7, sigma=4.5, floor=false)\n",
           ALMA_REFERENCE),
    Routed("alma_keywords_reordered",
           "x = ta.alma(close, 12, sigma=4.5, offset=0.7)\n", ALMA_REFERENCE),
    # ta.kc / ta.kcw: useTrueRange true is the engine's KC.
    Routed("kc_true_range", "[m, u, l] = ta.kc(close, 20, 1.5, true)\n", KC_REFERENCE,
           traced=("m", "u", "l"), trade=KC_TRADE),
    Routed("kc_series_keywords",
           "[m, u, l] = ta.kc(series=close, length=20, mult=1.5, useTrueRange=true)\n",
           KC_REFERENCE, traced=("m", "u", "l"), trade=KC_TRADE),
    Routed("kc_default_true_range", "[m, u, l] = ta.kc(close, 20, 1.5)\n", KC_REFERENCE,
           traced=("m", "u", "l"), trade=KC_TRADE),
    Routed("kcw_true_range", "x = ta.kcw(close, 20, 1.5, true)\n", KCW_REFERENCE,
           trade=_trade("x", "0.02")),
    Routed("kcw_true_range_keyword", "x = ta.kcw(close, 20, 1.5, useTrueRange=true)\n",
           KCW_REFERENCE, trade=_trade("x", "0.02")),
    Routed("kc_high_low_range", "[m, u, l] = ta.kc(close, 20, 1.5, false)\n",
           KC_HIGH_LOW_REFERENCE, traced=("m", "u", "l"), trade=KC_TRADE),
    Routed("kc_high_low_range_keyword",
           "[m, u, l] = ta.kc(close, 20, 1.5, useTrueRange=false)\n",
           KC_HIGH_LOW_REFERENCE, traced=("m", "u", "l"), trade=KC_TRADE),
    Routed("kc_high_low_range_input",
           '[m, u, l] = ta.kc(close, 20, 1.5, useTrueRange=input.bool(false, "TR"))\n',
           KC_HIGH_LOW_REFERENCE, traced=("m", "u", "l"), trade=KC_TRADE),
    Routed("kcw_high_low_range", "x = ta.kcw(close, 20, 1.5, false)\n",
           KCW_HIGH_LOW_REFERENCE, trade=_trade("x", "0.02")),
    # TradingView's parameter name ``series``.
    Routed("bb_series_keyword",
           "[m, u, l] = ta.bb(series=close, length=20, mult=2.0)\n",
           "[m, u, l] = ta.bb(close, 20, 2.0)\n",
           traced=("m", "u", "l"), trade=KC_TRADE),
    Routed("bbw_series_keyword", "x = ta.bbw(series=close, length=20, mult=2.0)\n",
           "x = ta.bbw(close, 20, 2.0)\n", trade=_trade("x", "3.0")),
    Routed("cmo_series_keyword", "x = ta.cmo(series=close, length=9)\n",
           "x = ta.cmo(close, 9)\n", trade=_trade("x", "0")),
    # One-arg forms: the default source is high (highest*) or low (lowest*).
    Routed("highestbars_length_only", "x = ta.highestbars(10)\n",
           "x = ta.highestbars(high, 10)\n", trade=_trade("x", "-5")),
    Routed("lowestbars_length_only", "x = ta.lowestbars(10)\n",
           "x = ta.lowestbars(low, 10)\n", trade=_trade("x", "-5")),
    Routed("highestbars_length_keyword", "x = ta.highestbars(length=10)\n",
           "x = ta.highestbars(high, 10)\n", trade=_trade("x", "-5")),
    Routed("highest_length_keyword", "x = ta.highest(length=10)\n",
           "x = ta.highest(high, 10)\n", trade=_trade("close", "x[1]")),
    Routed("lowest_length_keyword", "x = ta.lowest(length=10)\n",
           "x = ta.lowest(low, 10)\n", trade=_trade("close", "x[1]")),
    # The same routing inside request.security.
    Routed("security_alma_floor_false",
           'x = request.security(syminfo.tickerid, "60", ta.alma(close, 12, 0.7, 4.5, false))\n',
           'x = request.security(syminfo.tickerid, "60", ta.alma(close, 12, 0.7, 4.5))\n'),
    Routed("security_kc_true_range",
           '[m, u, l] = request.security(syminfo.tickerid, "60", ta.kc(close, 20, 1.5, true))\n',
           '[m, u, l] = request.security(syminfo.tickerid, "60", ta.kc(close, 20, 1.5))\n',
           traced=("m", "u", "l"), trade=KC_TRADE),
    Routed("security_highestbars_length_only",
           'x = request.security(syminfo.tickerid, "60", ta.highestbars(10))\n',
           'x = request.security(syminfo.tickerid, "60", ta.highestbars(high, 10))\n',
           trade=_trade("x", "-5")),
    # Controls: the documented forms that already routed exactly.
    Routed("alma_four_args", "x = ta.alma(close, 12, 0.7, 4.5)\n", ALMA_REFERENCE),
    Routed("pivot_levels_anchor_true",
           'lv = ta.pivot_point_levels("Traditional", true)\nx = array.get(lv, 1)\n',
           "pp = (high[1] + low[1] + close[1]) / 3\nx = pp * 2 - low[1]\n"),
    Routed("pivot_levels_developing",
           'lv = ta.pivot_point_levels("Traditional", true, true)\nx = array.get(lv, 1)\n',
           "p = (high + low + close) / 3\nx = p * 2 - low\n",
           trade=PIVOT_TRADE),
)
ROUTED_BY_NAME = {c.name: c for c in ROUTED}
assert len(ROUTED_BY_NAME) == len(ROUTED), "duplicate case name"


ALMA_FLOOR = (
    "ta.alma floor is not supported: the engine's ta::ALMA always computes its "
    "offset m = offset * (length - 1) unfloored and has no floor input, so a "
    "floor that is not the constant false would silently compute an unfloored "
    "ALMA. — Omit floor or pass false. A floored ALMA needs engine support: a "
    "ta::ALMA that uses math.floor(offset * (length - 1)) as m."
)


def _true_range(fn: str, cls: str, what: str) -> str:
    return (f"ta.{fn} useTrueRange is not supported: the engine's {cls} always "
            "averages the true range and has no high - low range input, so a "
            "useTrueRange that is not the constant true would silently compute a "
            f"true-range {what}. — Omit useTrueRange or pass true. A high - low "
            f"range needs engine support: a {cls.split(' ')[0]} whose range series "
            "is high - low.")


KC_TRUE_RANGE = _true_range("kc", "ta::KC", "Keltner channel")
KCW_TRUE_RANGE = _true_range("kcw", "ta::KCW (over ta::KC)", "Keltner channel width")

SIG_HINT = " — Use the parameters of TradingView's signature."

ALMA_FLOOR_APPROX = (
    "ta.alma floor is approximated: the engine's ta::ALMA has no floor input and "
    "always computes its offset m = offset * (length - 1) unfloored, so this call "
    "ignores floor and runs the unfloored ALMA. — Omit floor or pass floor=false "
    "to run it exactly. A floored ALMA needs engine support: a ta::ALMA that uses "
    "math.floor(offset * (length - 1)) as m."
)


def _true_range_approx(fn: str, cls: str, what: str) -> str:
    return (f"ta.{fn} useTrueRange is approximated: the engine's {cls} always "
            "averages the true range and has no high - low range input, so this "
            f"call ignores useTrueRange and runs the true-range {what}. — Omit "
            "useTrueRange or pass useTrueRange=true to run it exactly. A high - low "
            f"range needs engine support: a {cls.split(' ')[0]} whose range series "
            "is high - low.")


KC_TRUE_RANGE_APPROX = _true_range_approx("kc", "ta::KC", "Keltner channel")
KCW_TRUE_RANGE_APPROX = _true_range_approx("kcw", "ta::KCW (over ta::KC)",
                                           "Keltner channel width")
PIVOT_ANCHOR_APPROX = (
    "ta.pivot_point_levels anchor is approximated: PineForge passes the engine's "
    "ta::pivot_point_levels the previous bar's high, low and close -- the period "
    "an anchor that is true on every bar closes -- and the engine has no "
    "anchored-period accumulation, so this call ignores its anchor and computes "
    "the previous bar's pivots. — Pass anchor = true to run it exactly. Another "
    "anchor needs engine support: pivot levels over the high, low and close "
    "accumulated since the anchor was last true."
)
PIVOT_DEVELOPING_APPROX = (
    "ta.pivot_point_levels developing is approximated: PineForge computes the "
    "levels of the last completed period and the engine has no developing-period "
    "levels, so this call ignores developing and computes completed-period "
    "pivots. — Omit developing or pass false to run it exactly. Developing pivots "
    "need engine support: levels recalculated over the period in progress."
)

# The codegen before lane C5 (origin/main when the lane was rebased).
PRE_C5 = "e6a64cd83fd61bf82723bc74c77a8c1555c65897"


@dataclass(frozen=True)
class Approximated:
    """``body`` transpiles with ``warnings`` (``(line, col, message)``, line 3
    is the first body line) and books the trades of ``PRE_C5``'s transpile of
    the same script."""
    name: str
    body: str
    warnings: tuple[tuple[int, int, str], ...]
    trade: str = _trade("close", "x")

    def subject(self) -> Build:
        return Build(HEADER + self.body + self.trade)

    def pre_c5(self, codegen) -> Build:
        return Build(HEADER + self.body + self.trade, codegen=codegen)


_SEC = 'request.security(syminfo.tickerid, "60", '
_PIVOT_R1 = "x = array.get(lv, 1)\n"
APPROXIMATED: tuple[Approximated, ...] = ()
APPROXIMATED_BY_NAME = {c.name: c for c in APPROXIMATED}
assert len(APPROXIMATED_BY_NAME) == len(APPROXIMATED), "duplicate case name"
_WARNED_FUNCTIONS = ("ta.alma ", "ta.kc ", "ta.kcw ", "ta.pivot_point_levels ")


@dataclass(frozen=True)
class Refused:
    """``body`` is refused with ``message`` at ``(line, col)`` (line 3 is the
    first body line)."""
    name: str
    body: str
    line: int
    col: int
    message: str


REFUSED: tuple[Refused, ...] = (
    # Arguments that bind to no parameter of TradingView's signature: spellings
    # TradingView itself rejects.
    Refused("alma_two_args", "x = ta.alma(close, 9)\n", 3, 5,
            "ta.alma is missing its arguments 'offset', 'sigma' (TradingView: "
            "ta.alma(series, length, offset, sigma, floor = false))." + SIG_HINT),
    Refused("pivot_levels_missing_anchor",
            'lv = ta.pivot_point_levels("Traditional")\nx = array.get(lv, 1)\n', 3, 6,
            "ta.pivot_point_levels is missing its argument 'anchor' (TradingView: "
            "ta.pivot_point_levels(type, anchor, developing = false))." + SIG_HINT),
    Refused("alma_offset_skipped", "x = ta.alma(close, 12, sigma=4.5)\n", 3, 5,
            "ta.alma is missing its argument 'offset' (TradingView: ta.alma(series, "
            "length, offset, sigma, floor = false))." + SIG_HINT),
    Refused("sma_misspelt_keyword", "x = ta.sma(close, lenght=14)\n", 3, 26,
            "ta.sma has no parameter 'lenght' (TradingView: ta.sma(source, length))."
            + SIG_HINT),
    Refused("sma_extra_argument", "x = ta.sma(close, 14, 3)\n", 3, 23,
            "ta.sma takes at most 2 arguments, got 3 (TradingView: ta.sma(source, "
            "length))." + SIG_HINT),
    Refused("sma_length_twice", "x = ta.sma(close, 14, length=20)\n", 3, 30,
            "ta.sma got 'length' twice, by position and by keyword (TradingView: "
            "ta.sma(source, length))." + SIG_HINT),
    Refused("bb_mult_missing", "[m, u, l] = ta.bb(close, 20)\n", 3, 13,
            "ta.bb is missing its argument 'mult' (TradingView: ta.bb(series, length, "
            "mult))." + SIG_HINT),
    Refused("bb_source_keyword", "[m, u, l] = ta.bb(source=close, length=20, mult=2.0)\n",
            3, 26, "ta.bb has no parameter 'source' (TradingView: ta.bb(series, length, "
            "mult))." + SIG_HINT),
    Refused("linreg_offset_missing", "x = ta.linreg(close, 20)\n", 3, 5,
            "ta.linreg is missing its argument 'offset' (TradingView: ta.linreg(source, "
            "length, offset))." + SIG_HINT),
    Refused("sar_inc_skipped", "x = ta.sar(0.02, max=0.2)\n", 3, 5,
            "ta.sar is missing its argument 'inc' (TradingView: ta.sar(start, inc, "
            "max))." + SIG_HINT),
)
REFUSED_BY_NAME = {c.name: c for c in REFUSED}
assert len(REFUSED_BY_NAME) == len(REFUSED), "duplicate case name"


@pytest.fixture(scope="session")
def outcomes(request, tmp_path_factory) -> dict[str, Outcome]:
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("e2e_ta_argument_routing")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    builds: dict[str, Build] = {}
    pre_c5 = reference_codegen(PRE_C5)
    for item in request.session.items:
        name = getattr(item, "originalname", None)
        if name == "test_ta_arguments_route_like_reference":
            case = ROUTED_BY_NAME[item.callspec.params["case_name"]]
            builds[f"{case.name}/subject"] = case.subject()
            builds[f"{case.name}/reference"] = case.reference()
        elif (name == "test_engine_limited_value_is_approximated_with_a_warning"
              and getattr(item, "callspec", None) is not None
              and item.callspec.params.get("case_name") in APPROXIMATED_BY_NAME):
            approx = APPROXIMATED_BY_NAME[item.callspec.params["case_name"]]
            builds[f"{approx.name}/subject"] = approx.subject()
            if pre_c5 is not None:
                builds[f"{approx.name}/pre_c5"] = approx.pre_c5(pre_c5)
    return execute_all(engine_root, feed, base, builds)


@pytest.mark.parametrize("case_name", list(ROUTED_BY_NAME))
def test_ta_arguments_route_like_reference(case_name: str, outcomes) -> None:
    subject = ok(outcomes, f"{case_name}/subject")
    reference = ok(outcomes, f"{case_name}/reference")
    compared, mismatched, first = per_bar_mismatches(
        subject.traces["default"], reference.traces["default"], ("subject", "reference"))
    a, b = subject.trades["default"], reference.trades["default"]
    failures = []
    if mismatched:
        failures.append(f"differs from the spelled-out reference on {mismatched} of "
                        f"{compared} traced values; first: {first}")
    if digest(a) != digest(b):
        failures.append(f"trades differ: subject {summary(a)} vs reference {summary(b)}")
    if not a.strip() or len(a.splitlines()) < 2:
        failures.append("the strategy booked no trades, so the trade check proves nothing")
    assert not failures, f"[{case_name}] " + "\n  ".join(failures)
    print(f"E2E ta-args {case_name}: == reference  {compared} traced values equal, "
          f"{summary(a)}")


@pytest.mark.parametrize("case_name", list(APPROXIMATED_BY_NAME))
def test_engine_limited_value_is_approximated_with_a_warning(case_name: str, outcomes) -> None:
    case = APPROXIMATED_BY_NAME[case_name]
    subject = ok(outcomes, f"{case_name}/subject")
    if f"{case_name}/pre_c5" not in outcomes:
        pytest.skip(f"the pre-C5 codegen ({PRE_C5[:12]}) is not in this checkout's history")
    pre_c5 = ok(outcomes, f"{case_name}/pre_c5")
    warnings = tuple((d["line"], d["col"], d["message"])
                     for d in subject.transpiled.get("diagnostics", [])
                     if d["severity"] == "warning" and d["message"].startswith(_WARNED_FUNCTIONS))
    assert warnings == case.warnings, f"[{case_name}] warnings {subject.transpiled.get('diagnostics')}"
    a, b = subject.trades["default"], pre_c5.trades["default"]
    assert digest(a) == digest(b), (
        f"[{case_name}] trades {summary(a)} vs the pre-C5 build's {summary(b)}")
    same_cpp = subject.transpiled["cpp"] == pre_c5.transpiled["cpp"]
    print(f"E2E ta-args {case_name}: warned at "
          f"{', '.join(f'{w[0]}:{w[1]}' for w in warnings)}, trades == pre-C5 "
          f"({PRE_C5[:7]}) build  {summary(a)}  C++ identical to pre-C5: {same_cpp}")


@pytest.mark.parametrize("case_name", list(REFUSED_BY_NAME))
def test_ta_argument_without_engine_route_is_refused(case_name: str, tmp_path: Path) -> None:
    case = REFUSED_BY_NAME[case_name]
    pine = tmp_path / "strategy.pine"
    pine.write_text(HEADER + case.body + _trade("close", "close[1]"))
    result = transpile_json(pine)
    assert not result["ok"], f"[{case_name}] transpiled; it has no exact engine route"
    got = [(d["line"], d["col"], d["message"]) for d in result["diagnostics"]]
    assert got == [(case.line, case.col, case.message)], f"[{case_name}] {got}"
    print(f"E2E ta-args {case_name}: refused at {case.line}:{case.col}")
