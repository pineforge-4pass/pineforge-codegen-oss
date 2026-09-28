"""``request.security`` payloads reading the history of the derived price
series, and globals declared after the helpers whose payloads read them.

TradingView evaluates a payload on the requested bars. Two spellings read
the chart's values there instead:

* ``hl2[1]`` (and ``hlc3``, ``ohlc4``, ``hlcc4``): only the bar fields had
  requested-bar history, so the payload read the chart's ``_s_hl2`` Series,
  and a helper parameter bound to one was refused. ``hlcc4[1]`` on the chart
  itself did not compile: its Series pushed ``current_bar_.hlcc4``.
* A global declared after a helper, read by the helper's payload through a
  parameter (``g(_e) => request.security(..., _e[1])`` with a later
  ``ma = ta.sma(close, 3)``, ``x = g(ma)``): the parameter kept a read of
  ``na`` (security_contexts puts a value in only where the builder lowers
  it, and the builder read such a name on the chart's terms, the analyzer
  binding nothing in the helper's body). The copy's read now carries the
  binding (``GLOBAL_ANNOTATION``). A helper's TA that reads such a global's
  TA (``u(_x) => ta.sma(_x, 3)``, a later ``s5 = ta.sma(close, 5)``,
  ``request.security(..., u(s5))``) computed it inline and again in the
  prologue: two computes a bar. The prologue now orders its variants so each
  follows the variants its arguments read, all at the evaluator's top level
  (a helper's ``if`` between them must not hold one). A global declared
  after the helper is put in only when it is a number or a bool: the
  analyzer types the name a float, and a later string input compiled to a
  double request (the parent refused it, as it still does).

TradingView's tapes of ``xa2_price_later`` and ``xa2_prologue_order``
(``fixtures/xsym_a2_tv``, BINANCE:ETHUSDT.P 15) spell every read on each
close.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits


XSYM_A2_TV = Path(__file__).parent / "fixtures" / "xsym_a2_tv"
PRELUDE = '//@version=6\nstrategy("T")\n'
TRADE = 'if x > close\n    strategy.entry("L", strategy.long)\n'
UNBOUND = "whose argument PineForge does not recompute on the requested bars: it reads na"


def _evaluators(cpp: str) -> list[str]:
    return [" ".join(body.split()) for body in re.findall(
        r"void _eval_security_\d+\(const Bar& bar, bool is_complete\) \{\n(.*?)\n    \}",
        cpp, re.S)]


@pytest.mark.parametrize("body, lowered", [
    ('x = request.security(syminfo.tickerid, "D", hl2[1])\n',
     "_req_sec_0 = _sec0_hist_hl2[0]; if (is_complete) { "
     "_sec0_hist_hl2.push(((bar.high + bar.low) / 2.0)); }"),
    ('x = request.security(syminfo.tickerid, "D", hlc3[2] - hlcc4[1])\n',
     "_req_sec_0 = (_sec0_hist_hlc3[1] - _sec0_hist_hlcc4[0]);"),
    ('n = input.int(2, "N")\nx = request.security(syminfo.tickerid, "D", ohlc4[n])\n',
     "return (_hidx <= 0) ? ((bar.open + bar.high + bar.low + bar.close) / 4.0) : "
     "_sec0_hist_ohlc4[_hidx - 1];"),
    ('x = request.security(syminfo.tickerid, "D", nz(hl2[1]))\n',
     "auto _nz_v = (_sec0_hist_hl2[0]);"),
    # Through a helper parameter: refused.
    ('u(_x) => _x[1]\nx = request.security(syminfo.tickerid, "D", u(hl2))\n',
     "_req_sec_0 = _sec0_hist_hl2[0];"),
    # A payload parameter of the helper holding the request: it read na.
    ('g(_e) => request.security(syminfo.tickerid, "D", _e[1])\nx = g(hl2)\n',
     "_req_sec_0 = _sec0_hist_hl2[0];"),
])
def test_price_series_history_reads_the_requested_bars(body, lowered):
    result = transpile_full(PRELUDE + body + TRADE)
    evaluator = _evaluators(result["cpp"])[0]
    assert lowered in evaluator
    assert "_s_hl2" not in evaluator and "_s_hlc3" not in evaluator
    assert not any(d.message.endswith(UNBOUND) for d in result["diagnostics"])
    compile_cpp(result["cpp"], label="price series history in a payload")


def test_chart_hlcc4_history_compiles():
    cpp = transpile(PRELUDE + 'x = hlcc4[1]\n' + TRADE)
    assert ("_s_hlcc4.push(((current_bar_.high + current_bar_.low + current_bar_.close "
            "+ current_bar_.close) / 4.0));") in cpp
    compile_cpp(cpp, label="chart hlcc4 history")


LATER = ('g(_e) => request.security(syminfo.tickerid, "D", _e[1])\n'
         'h(_v, n) => request.security(syminfo.tickerid, "240", ta.sma(_v, n))\n')
GLOBALS = 'ma = ta.sma(close, 20)\nlen = input.int(4, "Len")\nbody = close - open\n'
CALLS = 'x = g(ma) + h(body, len)\n'


def test_global_declared_after_the_helper_is_put_in():
    after = transpile_full(PRELUDE + LATER + GLOBALS + CALLS + TRADE)
    assert not any(d.message.endswith(UNBOUND) for d in after["diagnostics"])
    evaluators = _evaluators(after["cpp"])
    # g(ma): the requested 20-bar SMA one requested bar back.
    assert re.search(r"_sec0__ta_sma_(\d+)\.compute\(bar\.close\).*"
                     r"_req_sec_0 = _sec0__ta_sma_\1_hist\[0\];", evaluators[0])
    # h(body, len): the requested SMA of the candle body, the input's length.
    assert "_sec1__ta_sma_1.compute((bar.close - bar.open))" in evaluators[1]
    assert re.search(r'_sec1__ta_sma_\w+ = ta::SMA\(get_input_int\("Len", 4\)\)', after["cpp"])
    # The evaluators of the globals declared first, up to the TA sites'
    # numbering (declaration order).
    before = transpile(PRELUDE + GLOBALS + LATER + CALLS + TRADE)
    renumbered = lambda text: re.sub(r"(_ta_[a-z]+_|_secval_)\d+", r"\1N", text)
    assert [renumbered(e) for e in evaluators] == [
        renumbered(e) for e in _evaluators(before)]
    compile_cpp(after["cpp"], label="payload parameters bound to later globals")


@pytest.mark.parametrize("body", [
    # A direct payload calling a helper with a later global.
    'u(_x) => ta.sma(_x, 3)\ns5 = ta.sma(close, 5)\n'
    'x = request.security(syminfo.tickerid, "D", u(s5))\n',
    # The helper holding the request, its parameter bound to a later global.
    'g(_e) => request.security(syminfo.tickerid, "D", ta.sma(_e, 3))\n'
    's5 = ta.sma(close, 5)\nx = g(s5)\n',
])
def test_a_later_globals_ta_is_computed_once_before_its_reader(body):
    cpp = transpile(PRELUDE + body + TRADE)
    evaluator = _evaluators(cpp)[0]
    # The 5-bar SMA advances once a bar, and ahead of the SMA of it.
    inner = re.findall(r"(_sec0__ta_sma_\d+)\.compute\(bar\.close\)", evaluator)
    assert len(inner) == 1, evaluator
    first = evaluator.index(f"{inner[0]}.compute(")
    outer = re.search(r"(_sec0__ta_sma_\d+)\.compute\(_secval_\d+\)", evaluator)
    assert outer is not None and first < outer.start(), evaluator
    compile_cpp(cpp, label="later global's TA read by a helper's TA")


HELPER_IF = 'f(_v) =>\n    r = 0.0\n    if close > open\n        r := _v\n    r + _v\n'


@pytest.mark.parametrize("body", [
    'u(_x) => ta.sma(f(_x), 3)\ns5 = ta.ema(close, 5)\n'
    'x = request.security(syminfo.tickerid, "D", u(s5))\n',
    'g(_e) => request.security(syminfo.tickerid, "D", ta.sma(f(_e), 3))\n'
    's5 = ta.ema(close, 5)\nx = g(s5)\n',
])
def test_a_prologue_variant_is_never_computed_inside_a_helpers_if(body):
    # The EMA is computed once, at the evaluator's top level, before the
    # helper's if-branch that reads it and the SMA of the helper's value.
    cpp = transpile(PRELUDE + HELPER_IF + body + TRADE)
    evaluator = _evaluators(cpp)[0]
    ema = re.findall(r"auto (_secval_\d+) = security_series_slot_is_new\(0\) \? "
                     r"(_sec0__ta_ema_\d+)\.compute\(bar\.close\)", evaluator)
    assert len(ema) == 1 and evaluator.count(f"{ema[0][1]}.compute(") == 1, evaluator
    assert evaluator.index(ema[0][0]) < evaluator.index("if (")
    assert f"_sec0_f_1_r = {ema[0][0]};" in evaluator
    compile_cpp(cpp, label="prologue variant read under a helper's if")


@pytest.mark.parametrize("body", [
    # A history read of one prologue variant before a value read of another:
    # the RSI once, before the EMA (the parent refused both spellings).
    'u(_x, _y) => ta.ema(_x + _y, 3)\ns5 = ta.sma(close, 5)\nr = ta.rsi(close, 14)\n'
    'x = request.security(syminfo.tickerid, "60", u(s5[1], r))\n',
    'g(_e, _f) => request.security(syminfo.tickerid, "60", ta.ema(_e[1] + _f, 3))\n'
    's5 = ta.sma(close, 5)\nr = ta.rsi(close, 14)\nx = g(s5, r)\n',
])
def test_every_read_of_a_prologue_variant_orders_it(body, monkeypatch):
    def order(cpp: str) -> list[str]:
        return re.findall(r"auto _secval_\w+ = security_series_slot_is_new\(0\) \? "
                          r"(_sec0__ta_[a-z]+)_\d+\.compute", _evaluators(cpp)[0])

    cpp = transpile(PRELUDE + body + TRADE)
    assert order(cpp) == ["_sec0__ta_sma", "_sec0__ta_rsi", "_sec0__ta_ema"]
    assert _evaluators(cpp)[0].count("_ta_rsi_3.compute(") == 1
    compile_cpp(cpp, label="prologue variants read after a history read")
    # The reads learnt by building the arguments (a cycle in the reach graph)
    # give the same order.
    from pineforge_codegen.codegen.base import CodeGen
    monkeypatch.setattr(CodeGen, "_security_ta_sites_reached",
                        lambda self, site, stack: set(range(len(self.ctx.ta_call_sites))))
    assert transpile(PRELUDE + body + TRADE) == cpp


def test_a_later_string_global_keeps_the_parents_refusal():
    # The analyzer types the name it cannot bind a float: put in, the string
    # request was typed a double and the C++ did not compile.
    with pytest.raises(CompileError, match="Unknown variable '_e'"):
        transpile(PRELUDE + (
            'g(_e) => request.security(syminfo.tickerid, "D", _e)\n'
            'sv = input.string("abc", "S")\nx = g(sv)\n'
            'if x == "abc"\n    strategy.entry("L", strategy.long)\n'))


def test_prologue_order_replays_tradingview_tape(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xa2_prologue_order")
    exits = replay(engine, base, {"probe": Build(source("xa2_prologue_order", XSYM_A2_TV))})
    tape = tape_exits("xa2_prologue_order", XSYM_A2_TV)
    assert len(tape) == 336
    # The close exiting at 08:00 UTC on 2025-04-02: the hourly and the
    # 4-hour SMA of 3 of f's value, over the 5- and 6-bar SMAs.
    assert tape[1743580800000] == "2491.202|1885.2178"
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    print(f"prologue order: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


def test_price_history_and_later_globals_replay_tradingview_tape(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xa2_price_later")
    exits = replay(engine, base, {"probe": Build(source("xa2_price_later", XSYM_A2_TV))})
    tape = tape_exits("xa2_price_later", XSYM_A2_TV)
    assert len(tape) == 336
    # The close exiting at 20:00 UTC on 2025-04-01: hourly hl2 one bar back,
    # a 4-hour ohlc4/hlc3/hlcc4 history sum, g(hl2), g(ma), u(s5), the
    # 4-hour SMA of the candle body over 4 bars and the chart's hlcc4 one bar
    # back.
    assert tape[1743537600000] == (
        "1907.765|-40.2192|1907.765|1912.3533|1906.7713|18.715|1905.0675")
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    print(f"price series history and later globals: {len(tape)} of {len(tape)} exit "
          "Signals equal TradingView's")
