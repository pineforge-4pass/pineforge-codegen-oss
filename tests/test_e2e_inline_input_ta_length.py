"""E2E: an inline ``input.*()`` TA constructor argument behaves exactly like
the variable-bound spelling (pineforge-codegen-oss#132).

``ta.ema(close, input.int(9, "fast"))`` was refused since 0.8.1 with
``Unsupported TA constructor length ... neither a compile-time constant nor
derived from an input`` while ``len = input.int(9, "fast")`` followed by
``ta.ema(close, len)`` was accepted.

Every case is driven from outside, through the interfaces a client uses
(``tests/_e2e.py``: transpile through the app's glue, compile against the
built engine runtime, run on the real 15m feed with ``inputs.json``
overrides keyed by the input title).

For each case the inline spelling and its variable-bound twin must produce
byte-identical ``engine_trades.csv`` at the defaults and under an input
override, and identical input manifests. The override has to change the
trades, so the equality cannot hold because both spellings ignore the input.

Skips cleanly without the engine environment ``tests/_e2e.py`` needs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._e2e import (
    Build, Outcome, derive_chart_feed, digest, execute_all,
    skip_unless_e2e_env, trade_count, transpile_json,
)

# The strategy from the issue body, verbatim.
ISSUE_132_SOURCE = """//@version=6
strategy("EMA Cross", overlay=true)
fast = ta.ema(close, input.int(9, "fast"))
slow = ta.ema(close, input.int(21, "slow"))
if ta.crossover(fast, slow)
    strategy.entry("L", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("L")
"""


@dataclass(frozen=True)
class Slot:
    """One inline input: ``{key}`` in a case body is spelled ``call`` inline,
    or read from a top-level ``<key>Input = call`` binding in the twin."""
    key: str
    call: str
    title: str
    override: object


@dataclass(frozen=True)
class Case:
    name: str
    body: str
    slots: tuple[Slot, ...]
    header: str = ""
    # ``var `` declares the bound twin's inputs as persistent vars.
    bound_prefix: str = ""
    # Why the override cannot move a trade, when it cannot; the byte equality
    # with the bound twin is required regardless.
    override_inert: str = ""

    def _header(self) -> str:
        return self.header or (
            f'//@version=6\nstrategy("e2e-inline-{self.name}", overlay=true)\n')

    def inline_source(self) -> str:
        body = self.body
        for s in self.slots:
            body = body.replace("{" + s.key + "}", s.call)
        return self._header() + body

    def bound_source(self) -> str:
        decls = "".join(f"{self.bound_prefix}{s.key}Input = {s.call}\n"
                        for s in self.slots)
        body = self.body
        for s in self.slots:
            body = body.replace("{" + s.key + "}", f"{s.key}Input")
        return self._header() + decls + body

    def overrides(self) -> dict:
        return {s.title: s.override for s in self.slots}


def _int(key: str, default: int, title: str, override: int) -> Slot:
    return Slot(key, f'input.int({default}, "{title}")', title, override)


def _float(key: str, default: float, title: str, override: float) -> Slot:
    return Slot(key, f'input.float({default}, "{title}")', title, override)


def _bool(key: str, default: bool, title: str, override: bool) -> Slot:
    lit = "true" if default else "false"
    return Slot(key, f'input.bool({lit}, "{title}")', title, override)


LONG_ON_CROSS = (
    'if ta.crossover({a}, {b})\n'
    '    strategy.entry("L", strategy.long)\n'
    'if ta.crossunder({a}, {b})\n'
    '    strategy.close("L")\n'
)


def _vs_close(expr: str) -> str:
    """``x`` is a price-scale line: trade close crossing it."""
    return f"x = {expr}\n" + LONG_ON_CROSS.format(a="close", b="x")


def _vs_zero(expr: str) -> str:
    """``x`` oscillates around zero: trade its zero crossings."""
    return f"x = {expr}\n" + LONG_ON_CROSS.format(a="x", b="0")


def _vs_self(expr: str) -> str:
    """Any scale: trade ``x`` crossing its own 5-bar SMA."""
    return f"x = {expr}\ns = ta.sma(x, 5)\n" + LONG_ON_CROSS.format(a="x", b="s")


def _while_true(expr: str) -> str:
    """``x`` is a bool: long while it holds."""
    return (f"x = {expr}\n"
            'if x\n    strategy.entry("L", strategy.long)\n'
            'else\n    strategy.close("L")\n')


LEN = _int("len", 14, "Length", 31)

# Every TA constructor in the analyzer registry that takes constructor
# arguments (TA_PERIOD_ARG, TA_MULTI_CTOR, vwap_bands), each argument an
# inline input.
TA_CASES: tuple[Case, ...] = (
    Case("sma", _vs_close("ta.sma(close, {len})"), (LEN,)),
    Case("ema", _vs_close("ta.ema(close, {len})"), (LEN,)),
    Case("rma", _vs_close("ta.rma(close, {len})"), (LEN,)),
    Case("rsi", _vs_self("ta.rsi(close, {len})"), (LEN,)),
    Case("atr", _vs_self("ta.atr({len})"), (LEN,)),
    Case("highest", _vs_self("ta.highest(high, {len})"), (LEN,)),
    Case("highest_1arg", _vs_self("ta.highest({len})"), (LEN,)),
    Case("lowest", _vs_self("ta.lowest(low, {len})"), (LEN,)),
    Case("change", _vs_zero("ta.change(close, {len})"), (LEN,)),
    Case("wma", _vs_close("ta.wma(close, {len})"), (LEN,)),
    Case("hma", _vs_close("ta.hma(close, {len})"), (LEN,)),
    Case("math_sum", _vs_zero("math.sum(close - open, {len})"), (LEN,)),
    Case("linreg", _vs_close("ta.linreg(close, {len}, 0)"), (LEN,)),
    Case("percentrank", _vs_self("ta.percentrank(close, {len})"), (LEN,)),
    Case("vwma", _vs_close("ta.vwma(close, {len})"), (LEN,)),
    Case("mom", _vs_zero("ta.mom(close, {len})"), (LEN,)),
    Case("roc", _vs_zero("ta.roc(close, {len})"), (LEN,)),
    Case("rising", _while_true("ta.rising(close, {len})"), (_int("len", 3, "Length", 6),)),
    Case("falling", _while_true("ta.falling(close, {len})"), (_int("len", 3, "Length", 6),)),
    Case("cci", _vs_zero("ta.cci(close, {len})"), (LEN,)),
    Case("median", _vs_close("ta.median(close, {len})"), (LEN,)),
    Case("highestbars", _vs_self("ta.highestbars(high, {len})"), (LEN,)),
    Case("lowestbars", _vs_self("ta.lowestbars(low, {len})"), (LEN,)),
    Case("cmo", _vs_zero("ta.cmo(close, {len})"), (LEN,)),
    Case("cog", _vs_self("ta.cog(close, {len})"), (LEN,)),
    Case("correlation", _vs_zero("ta.correlation(close, volume, {len})"), (LEN,)),
    Case("percentile_nearest_rank",
         _vs_close("ta.percentile_nearest_rank(close, {len}, 50)"), (LEN,)),
    Case("percentile_linear_interpolation",
         _vs_close("ta.percentile_linear_interpolation(close, {len}, 50)"), (LEN,)),
    Case("mode", _vs_close("ta.mode(close, {len})"), (LEN,)),
    Case("range", _vs_self("ta.range(close, {len})"), (LEN,)),
    Case("dev", _vs_self("ta.dev(close, {len})"), (LEN,)),
    Case("rci", _vs_zero("ta.rci(close, {len})"), (LEN,)),
    Case("macd",
         "[m, sg, h] = ta.macd(close, {fast}, {slow}, {signal})\n"
         + LONG_ON_CROSS.format(a="m", b="sg"),
         (_int("fast", 12, "Fast", 5), _int("slow", 26, "Slow", 40),
          _int("signal", 9, "Signal", 4))),
    Case("stoch", _vs_self("ta.stoch(close, high, low, {len})"), (LEN,)),
    Case("supertrend",
         "[st, dir] = ta.supertrend({factor}, {atrLen})\n"
         + LONG_ON_CROSS.format(a="close", b="st"),
         (_float("factor", 3.0, "Factor", 1.5), _int("atrLen", 10, "ATR Length", 21))),
    Case("dmi",
         "[dp, dm, adx] = ta.dmi({diLen}, {adxLen})\n"
         + LONG_ON_CROSS.format(a="dp", b="dm"),
         (_int("diLen", 14, "DI Length", 30), _int("adxLen", 14, "ADX Smoothing", 5))),
    Case("bb",
         "[mid, up, lo] = ta.bb(close, {len}, {mult})\n"
         'if ta.crossover(close, up)\n    strategy.entry("L", strategy.long)\n'
         'if ta.crossunder(close, mid)\n    strategy.close("L")\n',
         (_int("len", 20, "BB Length", 40), _float("mult", 2.0, "BB Mult", 1.0))),
    Case("kc",
         "[mid, up, lo] = ta.kc(close, {len}, {mult})\n"
         'if ta.crossover(close, up)\n    strategy.entry("L", strategy.long)\n'
         'if ta.crossunder(close, mid)\n    strategy.close("L")\n',
         (_int("len", 20, "KC Length", 40), _float("mult", 1.5, "KC Mult", 0.75))),
    Case("sar", _vs_close("ta.sar({start}, {inc}, {max})"),
         (_float("start", 0.02, "SAR Start", 0.05), _float("inc", 0.02, "SAR Inc", 0.04),
          _float("max", 0.2, "SAR Max", 0.4))),
    Case("pivots",
         "ph = ta.pivothigh(high, {leftH}, {rightH})\n"
         "pl = ta.pivotlow(low, {leftL}, {rightL})\n"
         'if not na(pl)\n    strategy.entry("L", strategy.long)\n'
         'if not na(ph)\n    strategy.close("L")\n',
         (_int("leftH", 5, "PH Left", 9), _int("rightH", 5, "PH Right", 2),
          _int("leftL", 5, "PL Left", 9), _int("rightL", 5, "PL Right", 2))),
    Case("pivots_2arg",
         "ph = ta.pivothigh({leftH}, {rightH})\n"
         "pl = ta.pivotlow({leftL}, {rightL})\n"
         'if not na(pl)\n    strategy.entry("L", strategy.long)\n'
         'if not na(ph)\n    strategy.close("L")\n',
         (_int("leftH", 5, "PH Left", 9), _int("rightH", 5, "PH Right", 2),
          _int("leftL", 5, "PL Left", 9), _int("rightL", 5, "PL Right", 2))),
    Case("alma", _vs_close("ta.alma(close, {len}, {offset}, {sigma})"),
         (_int("len", 9, "ALMA Length", 21), _float("offset", 0.85, "ALMA Offset", 0.5),
          _float("sigma", 6.0, "ALMA Sigma", 3.0))),
    Case("mfi", _vs_self("ta.mfi(hlc3, {len})"), (LEN,)),
    Case("tsi", _vs_zero("ta.tsi(close, {short}, {long})"),
         (_int("short", 13, "TSI Short", 5), _int("long", 25, "TSI Long", 50))),
    Case("wpr", _vs_self("ta.wpr({len})"), (LEN,)),
    Case("bbw", _vs_self("ta.bbw(close, {len}, {mult})"),
         (_int("len", 20, "BBW Length", 40), _float("mult", 2.0, "BBW Mult", 1.0))),
    Case("kcw", _vs_self("ta.kcw(close, {len}, {mult})"),
         (_int("len", 20, "KCW Length", 40), _float("mult", 1.5, "KCW Mult", 0.75))),
    Case("tr", _vs_self("ta.tr({handleNa})"),
         (_bool("handleNa", True, "Handle na", False),),
         override_inert="handle_na only decides the first bar's true range"),
    Case("stdev", _vs_self("ta.stdev(close, {len}, {biased})"),
         (_int("len", 20, "Stdev Length", 40), _bool("biased", True, "Biased", False))),
    Case("variance", _vs_self("ta.variance(close, {len}, {biased})"),
         (_int("len", 20, "Variance Length", 40), _bool("biased", True, "Biased", False))),
    Case("valuewhen", _vs_close("ta.valuewhen(close > open, close, {occ})"),
         (_int("occ", 2, "Occurrence", 3),)),
    Case("vwap_bands",
         '[v, up, lo] = ta.vwap(close, timeframe.change("D"), {mult})\n'
         'if ta.crossover(close, up)\n    strategy.entry("L", strategy.long)\n'
         'if ta.crossunder(close, v)\n    strategy.close("L")\n',
         (_float("mult", 1.0, "VWAP Band Mult", 2.0),)),
)

EMA_LEN = _int("len", 9, "fast", 23)

# The issue strategy itself, argument forms of the inline call, arithmetic
# and function-derived lengths, and TA sites reached through
# request.security, user functions and a loop.
SHAPE_CASES: tuple[Case, ...] = (
    Case("issue_132",
         "fast = ta.ema(close, {fast})\n"
         "slow = ta.ema(close, {slow})\n"
         "if ta.crossover(fast, slow)\n"
         '    strategy.entry("L", strategy.long)\n'
         "if ta.crossunder(fast, slow)\n"
         '    strategy.close("L")\n',
         (_int("fast", 9, "fast", 5), _int("slow", 21, "slow", 34)),
         header='//@version=6\nstrategy("EMA Cross", overlay=true)\n'),
    Case("kw_named", _vs_close("ta.ema(close, {len})"),
         (Slot("len", 'input.int(defval=9, title="fast")', "fast", 23),)),
    Case("kw_reordered", _vs_close("ta.ema(close, {len})"),
         (Slot("len", 'input.int(title="fast", defval=9)', "fast", 23),)),
    Case("kw_title_only", _vs_close("ta.ema(close, {len})"),
         (Slot("len", 'input.int(9, title="fast")', "fast", 23),)),
    Case("kw_every_param", _vs_close("ta.ema(close, {len})"),
         (Slot("len",
               'input.int(9, "fast", minval=1, maxval=200, step=1, '
               'tooltip="EMA length", inline="lens", group="Moving averages", '
               'confirm=false, display=display.none)',
               "fast", 23),)),
    Case("kw_options", _vs_close("ta.ema(close, {len})"),
         (Slot("len", 'input.int(9, "fast", options=[5, 9, 21])', "fast", 21),)),
    Case("plain_input", _vs_close("ta.ema(close, {len})"),
         (Slot("len", 'input(9, "fast")', "fast", 23),)),
    Case("title_punctuation", _vs_close("ta.ema(close, {len})"),
         (Slot("len", 'input.int(9, "Fast (EMA, bars)")', "Fast (EMA, bars)", 23),)),
    Case("empty_title", _vs_close("ta.ema(close, {len})"),
         (Slot("len", 'input.int(9, "")', "", 23),)),
    # The title spells the name of a derived length; it stays a title.
    Case("title_is_derived_name", "m = 2 * 3\n" + _vs_close("ta.ema(close, {len})"),
         (_int("len", 9, "m", 23),)),
    Case("var_binding", _vs_close("ta.ema(close, {len})"), (EMA_LEN,),
         bound_prefix="var "),
    Case("float_len", _vs_close("ta.ema(close, int({len}))"),
         (_float("len", 9.0, "fast", 23.0),)),
    Case("arith", _vs_close("ta.ema(close, {len} * 2 + 1)"),
         (_int("len", 4, "Half Length", 11),)),
    Case("math_round", _vs_close("ta.sma(close, math.round({len} * 1.5))"),
         (_float("len", 9.5, "Base Length", 20.0),)),
    Case("math_max", _vs_self("ta.rsi(close, math.max({len}, 2))"),
         (_int("len", 14, "RSI Length", 30),)),
    Case("two_inputs", _vs_close("ta.wma(close, {a} + {b})"),
         (_int("a", 5, "Base", 12), _int("b", 4, "Extra", 9))),
    Case("derived_binding", "n = {len} * 2\n" + _vs_close("ta.ema(close, n)"),
         (_int("len", 5, "Half Length", 12),)),
    Case("request_security",
         _vs_close('request.security(syminfo.tickerid, "60", ta.ema(close, {len}))'),
         (EMA_LEN,)),
    Case("user_function",
         "ma(src, n) => ta.ema(src, n)\n" + _vs_close("ma(close, {len})"),
         (EMA_LEN,)),
    Case("user_function_keyword_arg",
         "ma(src, n) => ta.ema(src, n)\n" + _vs_close("ma(close, n={len})"),
         (EMA_LEN,)),
    Case("user_function_request_security",
         'htf(n) => request.security(syminfo.tickerid, "60", ta.ema(close, n))\n'
         + _vs_close("htf({len})"),
         (EMA_LEN,)),
    Case("user_function_nested_title_is_param",
         "inner(len) => ta.ema(close, len)\n"
         "outer(len) => inner(len)\n" + _vs_close("outer({len})"),
         (_int("len", 9, "len", 23),)),
    Case("user_function_loop",
         "avgEma(n) =>\n"
         "    float acc = 0.0\n"
         "    for i = 0 to 1\n"
         "        acc += ta.ema(close, n)\n"
         "    acc / 2\n" + _vs_close("avgEma({len})"),
         (EMA_LEN,)),
)

# The analyzer registry key each TA case exercises.
TA_REGISTRY_CASES = {
    "sma": "sma", "ema": "ema", "rma": "rma", "rsi": "rsi", "atr": "atr",
    "highest": "highest", "lowest": "lowest", "change": "change", "wma": "wma",
    "hma": "hma", "sum": "math_sum", "linreg": "linreg",
    "percentrank": "percentrank", "vwma": "vwma", "mom": "mom", "roc": "roc",
    "rising": "rising", "falling": "falling", "cci": "cci", "median": "median",
    "highestbars": "highestbars", "lowestbars": "lowestbars", "cmo": "cmo",
    "cog": "cog", "correlation": "correlation",
    "percentile_nearest_rank": "percentile_nearest_rank",
    "percentile_linear_interpolation": "percentile_linear_interpolation",
    "mode": "mode", "range": "range", "dev": "dev", "rci": "rci",
    "macd": "macd", "stoch": "stoch", "supertrend": "supertrend", "dmi": "dmi",
    "bb": "bb", "kc": "kc", "sar": "sar", "pivothigh": "pivots",
    "pivotlow": "pivots", "alma": "alma", "mfi": "mfi", "tsi": "tsi",
    "wpr": "wpr", "bbw": "bbw", "kcw": "kcw", "tr": "tr", "stdev": "stdev",
    "variance": "variance", "valuewhen": "valuewhen", "vwap_bands": "vwap_bands",
}

ALL_CASES = TA_CASES + SHAPE_CASES
CASES_BY_NAME = {c.name: c for c in ALL_CASES}
assert len(CASES_BY_NAME) == len(ALL_CASES), "duplicate case name"
assert CASES_BY_NAME["issue_132"].inline_source() == ISSUE_132_SOURCE


def test_every_ta_constructor_with_arguments_has_a_case() -> None:
    from pineforge_codegen.analyzer import TA_CLASS_MAP, TA_NO_CTOR
    assert set(TA_REGISTRY_CASES) == (
        set(TA_CLASS_MAP) - set(TA_NO_CTOR)
        - {"vwap_anchored", "vwap_anchored_bands"}
    )
    assert {c.name for c in TA_CASES} >= set(TA_REGISTRY_CASES.values())


# ---------------------------------------------------------------------------
# Execution: every selected case's two spellings are built and run in
# parallel once per session; each test then judges its own case.
# ---------------------------------------------------------------------------

@dataclass
class CaseRun:
    inline: Outcome
    bound: Outcome


@pytest.fixture(scope="session")
def case_runs(request, tmp_path_factory) -> dict[str, CaseRun]:
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("e2e_inline_input")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    selected = [
        item.callspec.params["case_name"]
        for item in request.session.items
        if getattr(item, "originalname", None) == "test_inline_input_matches_bound_spelling"
    ] or list(CASES_BY_NAME)
    builds = {}
    for name in selected:
        case = CASES_BY_NAME[name]
        builds[f"{name}/inline"] = Build(case.inline_source(), case.overrides())
        builds[f"{name}/bound"] = Build(case.bound_source(), case.overrides())
    outcomes = execute_all(engine_root, feed, base, builds)
    return {
        name: CaseRun(inline=outcomes[f"{name}/inline"],
                      bound=outcomes[f"{name}/bound"])
        for name in selected
    }


@pytest.mark.parametrize("case_name", list(CASES_BY_NAME))
def test_inline_input_matches_bound_spelling(case_name: str, case_runs) -> None:
    case = CASES_BY_NAME[case_name]
    run = case_runs[case_name]
    if run.bound.error is not None:
        pytest.fail(f"[{case_name}] the variable-bound twin itself failed:\n"
                    f"{run.bound.error}", pytrace=False)
    if run.inline.error is not None:
        pytest.fail(f"[{case_name}] inline spelling failed:\n{run.inline.error}\n"
                    f"--- source ---\n{run.inline.build.source}", pytrace=False)

    inline_inputs = run.inline.transpiled["inputs"]
    bound_inputs = run.bound.transpiled["inputs"]
    assert inline_inputs == bound_inputs, (
        f"[{case_name}] input manifest differs:\n inline {inline_inputs}\n"
        f" bound  {bound_inputs}")
    assert [e["title"] for e in inline_inputs] == [s.title for s in case.slots]

    for tag in ("default", "override"):
        a, b = run.inline.trades[tag], run.bound.trades[tag]
        assert digest(a) == digest(b), (
            f"[{case_name}] {tag} trades differ: inline {trade_count(a)} trades "
            f"sha {digest(a)[:16]} vs bound {trade_count(b)} trades sha {digest(b)[:16]}")
    n_default = trade_count(run.inline.trades["default"])
    n_override = trade_count(run.inline.trades["override"])
    assert n_default > 0, f"[{case_name}] strategy never traded"
    if not case.override_inert:
        assert run.inline.trades["default"] != run.inline.trades["override"], (
            f"[{case_name}] override {case.overrides()} left the trades unchanged")
    print(
        f"E2E {case_name}: inline==bound  default {n_default} trades "
        f"sha256 {digest(run.inline.trades['default'])[:16]}  override "
        f"{case.overrides()} {n_override} trades "
        f"sha256 {digest(run.inline.trades['override'])[:16]}  "
        f"inputs {json.dumps(inline_inputs)}")


# A length that really is a series stays refused with the same diagnostic
# when an inline input is one of its leaves: the input is a stable leaf, the
# rest of the expression is not.
SERIES_LENGTHS = (
    ('ta.ema(close, close > open ? input.int(2, "A") : input.int(4, "B"))',
     "ta::EMA", '(close > open) ? input.int(2, "A") : input.int(4, "B")'),
    ('ta.sma(close, input.int(9, "Base") + bar_index % 5)',
     "ta::SMA", 'input.int(9, "Base") + (bar_index % 5)'),
    ('ta.rsi(close, math.max(input.int(14, "Len"), int(volume)))',
     "ta::RSI", 'math.max(input.int(14, "Len"), int(volume))'),
    ('ta.wma(close, input.source(close, "Src"))',
     "ta::WMA", 'input.source(close, "Src")'),
)


@pytest.mark.parametrize("expr,class_name,arg", SERIES_LENGTHS)
def test_series_length_with_inline_input_is_still_refused(
        expr: str, class_name: str, arg: str, tmp_path: Path) -> None:
    pine = tmp_path / "strategy.pine"
    pine.write_text('//@version=6\nstrategy("e2e-inline-refused")\n'
                    f"x = {expr}\n"
                    'if ta.crossover(close, x)\n    strategy.entry("L", strategy.long)\n')
    result = transpile_json(pine)
    assert not result["ok"], f"{expr} transpiled; its length is a series"
    assert [d["message"] for d in result["diagnostics"]] == [
        f"Unsupported TA constructor length '{arg}' for {class_name}: it is "
        "neither a compile-time constant nor derived from an input, so "
        "PineForge cannot size the indicator buffer. — Use a literal, an "
        "input.*() value, or arithmetic over those for TA lengths."]
