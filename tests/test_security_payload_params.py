"""A ``request.security`` payload reading a parameter of the helper that
holds the request.

The payload is evaluated on the requested bars, where the argument a
parameter is bound to is recomputed. The evaluator, a class method, never
bound such a parameter: ``nr(_s, _tf, _e) => request.security(_s, _tf,
_e[1], ...)`` read a member nothing pushed (``na`` on every bar,
talaeaelhussein-prop-scalper-v2-final booked no trade), a bare ``_v`` was
refused as an unknown variable, and ``f(3)`` and ``f(6)`` with
``ta.sma(close, len)`` shared one evaluator built from the first call's
length. ``security_contexts`` resolves each such parameter on every call
path, copies the requests once per distinct value and puts the value in the
parameter's place. What the analyzer's call-site clones already bind exactly
keeps its lowering, and so does a value the requested bars do not recompute
(a user call, a ``var`` global, a name the helper shadows), with a warning
where it reads ``na``. An ``input.source`` value is put in
(``tests/test_security_input_source.py``), and so are ``hl2`` and its family
and a global declared after the helper
(``tests/test_security_price_history_and_later_globals.py``).

TradingView's tapes of ``xa_payload_param`` and ``xa_payload_bare``
(``fixtures/xsym_tv``, BINANCE:ETHUSDT.P 15) spell every call's value on
each close.
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


XSYM_TV = Path(__file__).parent / "fixtures" / "xsym_tv"
PRELUDE = '//@version=6\nstrategy("T")\n'
TRADE = 'if x > close\n    strategy.entry("L", strategy.long)\n'
UNBOUND = "whose argument PineForge does not recompute on the requested bars: it reads na"


def _evaluators(cpp: str) -> list[str]:
    return [" ".join(body.split()) for body in re.findall(
        r"void _eval_security_\d+\(const Bar& bar, bool is_complete\) \{\n(.*?)\n    \}",
        cpp, re.S)]


def _registrations(cpp: str) -> list[str]:
    return re.findall(r"register_security_eval\((.*)\);", cpp)


def test_each_length_gets_its_own_request():
    cpp = transpile(PRELUDE + (
        'f(len) => request.security(syminfo.tickerid, "D", ta.sma(close, len))\n'
        'x = f(10) + f(20)\n') + TRADE)
    assert _registrations(cpp) == ['0, "D", input_tf_, false, false',
                                   '1, "D", input_tf_, false, false']
    assert re.search(r"_sec0__ta_sma_\w+\(10\)", cpp)
    assert re.search(r"_sec1__ta_sma_\w+\(20\)", cpp)


def test_series_history_reads_each_calls_argument():
    evaluators = _evaluators(transpile(PRELUDE + (
        'g(_e) => request.security(syminfo.tickerid, "D", _e[1])\n'
        'x = g(close) + g(open)\n') + TRADE))
    assert len(evaluators) == 2
    assert "_sec0_hist_close[0]" in evaluators[0]
    assert "_sec1_hist_open[0]" in evaluators[1]


def test_nested_no_repaint_helper_reads_each_global():
    # The talaeaelhussein shape: the series reaches the request through two
    # helpers, each call path with its own.
    cpp = transpile(PRELUDE + (
        'fast = ta.sma(close, 3)\nslow = ta.sma(close, 6)\n'
        'nr(_s, _tf, _e) => request.security(_s, _tf, _e[1], '
        'lookahead = barmerge.lookahead_on)\n'
        'reso(_x, _use, _r) => _use ? nr(syminfo.tickerid, _r, _x) : _x\n'
        'x = reso(fast, true, "60") - reso(slow, true, "60")\n') + TRADE)
    assert _registrations(cpp) == ['0, "60", input_tf_, true, false',
                                   '1, "60", input_tf_, true, false']
    evaluators = _evaluators(cpp)
    assert "_sec0__ta_sma_" in evaluators[0] and "_sec1__ta_sma_" in evaluators[1]
    assert not any("_e[1]" in e for e in evaluators)


@pytest.mark.parametrize("payload, lowered", [
    ("_v", "_req_sec_0 = ((bar.high + bar.low) / 2.0);"),
    ("_v * 2", "_req_sec_0 = (((bar.high + bar.low) / 2.0) * 2);"),
])
def test_bare_reads_used_to_be_unknown_variables(payload, lowered):
    evaluators = _evaluators(transpile(PRELUDE + (
        f'h(_v) => request.security(syminfo.tickerid, "60", {payload})\n'
        'x = h(hl2)\n') + TRADE))
    assert evaluators == [lowered]


@pytest.mark.parametrize("body", [
    # A user call: every earlier build refuses its history on the requested bars.
    'u() => ta.sma(close, 5)\n'
    'g(_e) => request.security(syminfo.tickerid, "D", _e[1])\nx = g(u())\n',
    # A var global.
    'var float lvl = 0.0\nlvl := close\n'
    'g(_e) => request.security(syminfo.tickerid, "D", _e[1])\nx = g(lvl)\n',
    # The helper declares the name the argument reads.
    'x0 = ta.sma(close, 3)\n'
    'g(_e) =>\n    x0 = 1.0\n    request.security(syminfo.tickerid, "D", _e[1]) + x0\n'
    'x = g(x0)\n',
    # History of a literal, na, or another history read does not compile.
    'g(_e) => request.security(syminfo.tickerid, "D", _e[1])\nx = g(1.5)\n',
    'g(_e) => request.security(syminfo.tickerid, "D", _e[1])\nx = g(na)\n',
    'g(_e) => request.security(syminfo.tickerid, "D", _e[1])\nx = g(close[1])\n',
    'prev = close[1]\n'
    'g(_e) => request.security(syminfo.tickerid, "D", _e[1])\nx = g(prev)\n',
    'g(_e) => request.security(syminfo.tickerid, "D", _e[1])\nx = g(ta.sma(close, 5)[1])\n',
    # An overloaded helper on the path.
    'g(float _e) => request.security(syminfo.tickerid, "D", _e[1])\n'
    'h(float x) => g(x)\nh(float x, float y) => x * y\nx = h(close)\n',
    # A global holding an operator expression or a math call.
    'mid = (high + low) / 2\n'
    'g(_e) => request.security(syminfo.tickerid, "D", _e[1])\nx = g(mid)\n',
    'am = math.abs(close - open)\n'
    'g(_e) => request.security(syminfo.tickerid, "D", _e[1])\nx = g(am)\n',
    # A global under a cast, which renders on the chart's terms.
    's = close\n'
    'g(_e) => request.security(syminfo.tickerid, "D", _e[1])\nx = g(ta.sma(float(s), 3))\n',
    # A value doubling through twenty nested helpers.
    'g(_e) => request.security(syminfo.tickerid, "D", _e[1])\n'
    + "".join(f'h{i}(x) => {"g" if i == 1 else f"h{i - 1}"}(x + x)\n' for i in range(1, 21))
    + 'x = h20(close)\n',
])
def test_value_not_recomputed_keeps_its_lowering_and_warns(body):
    result = transpile_full(PRELUDE + body + TRADE)
    assert _evaluators(result["cpp"]) == ["_req_sec_0 = _e[1];"]
    assert "__pfctx" not in result["cpp"]
    assert any(d.message.endswith(UNBOUND) for d in result["diagnostics"])


def test_each_call_sites_length_under_differing_timeframes():
    # The analyzer's call-site clones for differing timeframes built every
    # clone from the first call's length.
    cpp = transpile(PRELUDE + (
        'f(tf, len) => request.security(syminfo.tickerid, tf, ta.sma(close, len))\n'
        'x = f("60", 3) + f("240", 6)\n') + TRADE)
    assert _registrations(cpp) == ['0, "60", input_tf_, false, false',
                                   '1, "240", input_tf_, false, false']
    assert re.search(r"_sec0__ta_sma_\w+\(3\)", cpp)
    assert re.search(r"_sec1__ta_sma_\w+\(6\)", cpp)


def test_one_value_at_every_call_keeps_the_analyzers_lowering():
    cpp = transpile(PRELUDE + (
        'f(len) => request.security(syminfo.tickerid, "D", ta.sma(close, len))\n'
        'x = f(10) + f(10)\n') + TRADE)
    assert "__pfctx" not in cpp
    assert _registrations(cpp) == ['0, "D", input_tf_, false, false']


def test_untitled_inputs_keep_their_declarations_keys():
    cpp = transpile(PRELUDE + (
        'f(len) => request.security(syminfo.tickerid, "60", ta.sma(close, len))\n'
        'lenA = f(input.int(3))\nlenB = f(input.int(6))\nx = lenA - lenB\n') + TRADE)
    assert re.search(r'_sec0__ta_sma_\w+ = ta::SMA\(get_input_int\("lenA", 3\)\)', cpp)
    assert re.search(r'_sec1__ta_sma_\w+ = ta::SMA\(get_input_int\("lenB", 6\)\)', cpp)


@pytest.mark.parametrize("body", [
    # A payload parameter's request, the Heikin-Ashi alias declared after it.
    'f(string sym, int len) => request.security(sym, "60", ta.sma(close, len))\n'
    'haSym = ticker.heikinashi(syminfo.tickerid)\nx = f(haSym, 3) + f(haSym, 6)\n',
    'f(string sym, float _e) => request.security(sym, "60", _e[1])\n'
    'haSym = ticker.heikinashi(syminfo.tickerid)\nx = f(haSym, close)\n',
    # A symbol resolved through helpers, the alias declared after them.
    'g(sym) => request.security(sym, "60", close)\nh(sym) => g(sym)\n'
    'haSym = ticker.heikinashi(syminfo.tickerid)\nx = h(haSym)\n',
])
def test_heikin_ashi_alias_declared_after_the_helper(body):
    registrations = _registrations(transpile(PRELUDE + body + TRADE))
    assert registrations and all(r.endswith(", true") for r in registrations)


@pytest.mark.parametrize("body", [
    # A global under a builtin the builder renders on the chart's terms.
    'gs1 = ta.sma(close, 3)\n'
    'h(_v) => request.security(syminfo.tickerid, "60", nz(_v))\nx = h(gs1)\n',
    # A global under nz, in the argument itself.
    'src = ta.sma(close, 5)\n'
    'h(_v) => request.security(syminfo.tickerid, "60", _v)\nx = h(nz(src))\n',
    # A tuple payload: a helper returning one does not compile.
    'h(_v) => request.security(syminfo.tickerid, "D", [_v, _v * 2])\n'
    '[p, q] = h(close)\nx = p - q\n',
    # A caller's local holding an if expression.
    'g(_v) => request.security(syminfo.tickerid, "60", _v)\n'
    'h(x) =>\n    y = if x > 0\n        close\n    else\n        open\n    g(y)\n'
    'x = h(close - open)\n',
])
def test_bare_reads_the_builder_cannot_lower_stay_unknown_variables(body):
    with pytest.raises(CompileError, match="Unknown variable '_v'"):
        transpile(PRELUDE + body + TRADE)


def test_overloaded_helper_carrying_a_context_is_refused():
    with pytest.raises(CompileError, match="helper 'h' is overloaded"):
        transpile(PRELUDE + (
            'g(tf) => request.security(syminfo.tickerid, tf, close)\n'
            'h(string tf) => g(tf)\nh(string tf, float y) => y\nx = h("60")\n') + TRADE)


@pytest.mark.parametrize("body, read", [
    # PineCoders' non-repainting helper, symbol and timeframe as parameters.
    ('f(_symbol, _res, _src) => request.security(_symbol, _res, _src[1], '
     'lookahead = barmerge.lookahead_on)\nx = f(syminfo.tickerid, "D", close)\n',
     "_sec0_hist_close[0]"),
    ('tfi = input.timeframe("60", "TF")\n'
     'f(string tf, float _e) => request.security(syminfo.tickerid, tf, _e[1])\n'
     'x = f(tfi, close)\n', "_sec0_hist_close[0]"),
    ('f(string sym, float _e) => request.security(sym, "60", _e)\n'
     'x = f(syminfo.tickerid, close)\n', "bar.close"),
])
def test_symbol_and_timeframe_parameters_beside_the_payload_compile(body, read):
    cpp = transpile(PRELUDE + body + TRADE)
    assert read in _evaluators(cpp)[0]
    compile_cpp(cpp, label="payload beside symbol/timeframe parameters")


@pytest.mark.parametrize("declare", ["src = close\n", "var float src = close\nsrc := open\n"])
def test_warning_names_a_global_of_the_parameters_name(declare):
    result = transpile_full(PRELUDE + declare + (
        'y = src[3]\n'
        'f(src) => request.security(syminfo.tickerid, "D", src[1])\n'
        'x = f(src) + y * 0\n') + TRADE)
    assert any(d.message.endswith("it reads the global 'src' instead")
               for d in result["diagnostics"])


def test_helper_copies_warn_once_under_the_helpers_name():
    result = transpile_full(PRELUDE + (
        'g(tf, _e) => request.security(syminfo.tickerid, tf, _e[1])\n'
        'h(tf, _e) => g(tf, _e)\nx = h("60", close[1]) + h("D", close[1])\n') + TRADE)
    warned = [d.message for d in result["diagnostics"] if d.message.endswith(UNBOUND)]
    assert len(warned) == 1 and "parameter of 'g'" in warned[0]


def test_overloaded_payload_helper_keeps_its_refusal():
    # Every earlier build refused the length (a located error, not a crash).
    with pytest.raises(CompileError, match="Unsupported TA constructor length 'n'"):
        transpile(PRELUDE + (
            'g(int n) => request.security(syminfo.tickerid, "D", ta.sma(close, n))\n'
            'g(float a, float b) => a * b\nx = g(5) + g(close, open)\n') + TRADE)


def test_a_helper_nothing_reaches_does_not_warn():
    result = transpile_full(PRELUDE + (
        'g(float _e) => request.security(syminfo.tickerid, "60", _e[1])\n'
        'h(float y) => g(y)\nx = close\n') + TRADE)
    assert _registrations(result["cpp"]) == ['0, "60", input_tf_, false, false']
    assert not any(d.message.endswith(UNBOUND) for d in result["diagnostics"])


@pytest.mark.parametrize("name, sample", [
    # The close exiting at 20:00 UTC on 2025-04-01. a/b: the 3- and 6-hour
    # SMA one requested bar back; c/d: the 4-hour SMA of 3 bars, and of 6,
    # not yet warm; e/o: the hourly close and open one requested bar back.
    # The call sites of each helper read different values.
    ("xa_payload_param", "1912.3533|1901.4283|1896.1633|na|1907.5|1914.56"),
    # p/q: the hourly hl2 and open; u/w: the 4-hour SMA of the high minus the
    # high over 3 bars, and of the low over 6, not yet warm.
    ("xa_payload_bare", "1906.25|1907.49|-9.35|na"),
])
def test_payload_parameters_replay_tradingview_tape(tmp_path_factory, name, sample):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp(name)
    exits = replay(engine, base, {"probe": Build(source(name, XSYM_TV))})
    tape = tape_exits(name, XSYM_TV)
    assert len(tape) == 265
    assert tape[1743537600000] == sample
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    print(f"{name}: {len(tape)} of {len(tape)} exit Signals equal TradingView's")
