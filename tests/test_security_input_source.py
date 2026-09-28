"""``input.source`` values read by a ``request.security`` payload.

TradingView evaluates a payload on the requested bars, where an
``input.source`` value is the selected series of those bars: the requested
close for ``input.source(close)``, its high under an override naming
``high``. The payload builder rendered the value on the chart's terms,
``get_input_source(key, base)[0]``, the chart bar's value (michaellitton16's
RSI of its source), and refused its history through a helper parameter
(``logeq(_s, _l) => ta.change(_s, _l) / _s[_l]`` called as
``logeq(lemaSrc, lemaLen)``, job-1361-shitholed-lema-system-v2-4): "request.
security helper-parameter history currently requires a direct OHLC/time
bar-series binding". CG-W9-SEC (on main) reads the requested bar's value of
the series ``get_input_source`` selects (the ``_pf_src`` lambda,
``_security_source_input_expr``) and keeps one per completed requested bar
for its history (``_sec<N>_hist_input_source_K``); lane CG-XSYM-A2 carried
the same rule on a base without it, and its builder gave way to main's in
the integration (CGINT4). A parameter of the helper holding the request,
bound to such a value, is put in its place (``security_contexts``, lane
CG-XSYM-A2): ``g(_e) => request.security(..., _e[1])`` called as ``g(src)``
read na, and ``h(_v) => request.security(..., ta.sma(_v, 3))`` was refused
("Unknown variable '_v'").

TradingView's tapes of ``xa2_source_payload`` and ``xa2_source_param`` and of
their twins with other source defaults (``fixtures/xsym_a2_tv``,
BINANCE:ETHUSDT.P 15) spell every request's value on each close; under
overrides naming the twin's sources each probe replays its twin's tape.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp
from tests._e2e import Build, closed_trades, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits


XSYM_A2_TV = Path(__file__).parent / "fixtures" / "xsym_a2_tv"
PRELUDE = '//@version=6\nstrategy("T")\n'
TRADE = 'if x > close\n    strategy.entry("L", strategy.long)\n'
LEMA = 'lemaSrc = input.source(close, "LEMA Source")\nlemaLen = input.int(3, "LEMA Length")\n'
# The requested bar's value of the series a source input selects, as the
# evaluators below are normalized: ``<SRC key:base>``.
REQUESTED = "<SRC {key}:{base}>"
_SOURCE_VALUE = re.compile(
    r"\(\[&\]\(\) -> double \{ const Series<double>\* _pf_src = "
    r"&get_input_source\(\"([^\"]*)\", _src_(\w+?)_\); return .*? \}\(\)\)")


def _evaluators(cpp: str) -> list[str]:
    return [_SOURCE_VALUE.sub(lambda m: f"<SRC {m.group(1)}:{m.group(2)}>", " ".join(body.split()))
            for body in re.findall(
                r"void _eval_security_\d+\(const Bar& bar, bool is_complete\) \{\n(.*?)\n    \}",
                cpp, re.S)]


def test_helper_history_of_a_source_input_is_lowered():
    # The job-1361 shape: every earlier build refused it.
    cpp = transpile(PRELUDE + LEMA + (
        'logeq(_s, _l) => ta.change(_s, _l) / _s[_l]\n'
        'x = request.security(syminfo.tickerid, "D", logeq(lemaSrc, 5))\n') + TRADE)
    evaluator = _evaluators(cpp)[0]
    value = REQUESTED.format(key="LEMA Source", base="close")
    assert f"_sec0__ta_change_1.compute({value}, 5)" in evaluator
    assert "_req_sec_0 = (_secval_0 / _sec0_hist_input_source_0[4]);" in evaluator
    assert f"if (is_complete) {{ _sec0_hist_input_source_0.push({value}); }}" in evaluator
    compile_cpp(cpp, label="input.source history through a helper")


def test_input_length_history_reads_through_the_getter():
    cpp = transpile(PRELUDE + LEMA + (
        'logeq(_s, _l) => ta.change(_s, _l) / _s[_l]\n'
        'x = request.security(syminfo.tickerid, "D", logeq(lemaSrc, lemaLen))\n') + TRADE)
    evaluator = _evaluators(cpp)[0]
    assert ('int _hidx = get_input_int("LEMA Length", 3); if (is_na(_hidx)) '
            'return na<double>(); return (_hidx <= 0) ? '
            + REQUESTED.format(key="LEMA Source", base="close")
            + " : _sec0_hist_input_source_0[_hidx - 1];") in evaluator
    compile_cpp(cpp, label="input.source history at an input length")


@pytest.mark.parametrize("payload, lowered", [
    # The value, which used to read the chart bar.
    ("src", "_req_sec_0 = {value};"),
    ("src[1] + src[2]", "_req_sec_0 = (_sec0_hist_input_source_0[0] + _sec0_hist_input_source_0[1]);"),
    ("ta.sma(src, 3)", "_sec0__ta_sma_1.compute({value})"),
    # Under a builtin the expression visitor renders.
    ("nz(src[1])", "auto _nz_v = (_sec0_hist_input_source_0[0]);"),
])
def test_source_input_reads_the_requested_bar(payload, lowered):
    cpp = transpile(PRELUDE + 'src = input.source(hl2, "S")\n' + (
        f'x = request.security(syminfo.tickerid, "D", {payload})\n') + TRADE)
    evaluator = _evaluators(cpp)[0]
    assert lowered.format(value=REQUESTED.format(key="S", base="hl2")) in evaluator
    assert "get_input_source(\"S\", _src_hl2_)[0]" not in evaluator
    compile_cpp(cpp, label=f"input.source payload {payload}")


def test_untitled_source_input_keeps_its_declarations_key():
    evaluator = _evaluators(transpile(PRELUDE + (
        'src = input(close)\n'
        'x = request.security(syminfo.tickerid, "D", src[1])\n') + TRADE))[0]
    assert REQUESTED.format(key="src", base="close") in evaluator


@pytest.mark.parametrize("helper, lowered", [
    # History of the parameter: it read na, with a warning.
    ('g(_e) => request.security(syminfo.tickerid, "60", _e[1])\nx = g(src)\n',
     "_req_sec_0 = _sec0_hist_input_source_0[0];"),
    # A TA of it, and its value: "Unknown variable '_v'".
    ('h(_v) => request.security(syminfo.tickerid, "240", ta.sma(_v, 3))\nx = h(src)\n',
     "_sec0__ta_sma_1.compute({value})"),
    ('h(_v) => request.security(syminfo.tickerid, "240", _v - open)\nx = h(src)\n',
     "_req_sec_0 = ({value} - bar.open);"),
])
def test_payload_parameter_bound_to_a_source_input_is_put_in(helper, lowered):
    result = transpile_full(PRELUDE + 'src = input.source(close, "S")\n' + helper + TRADE)
    evaluator = _evaluators(result["cpp"])[0]
    assert lowered.format(value=REQUESTED.format(key="S", base="close")) in evaluator
    assert not any("does not recompute" in d.message for d in result["diagnostics"])
    compile_cpp(result["cpp"], label="payload parameter bound to input.source")


def test_method_reading_a_source_history_inlines_on_the_requested_bar():
    # The pre-pass deciding whether a payload's method inlines scans its
    # history before generation; the parent kept the method on the chart.
    cpp = transpile(PRELUDE + (
        'src = input.source(close, "S")\n'
        'method m(float x) => x - src[1]\n'
        'x = request.security(syminfo.tickerid, "60", close.m())\n') + TRADE)
    assert "_req_sec_0 = (bar.close - _sec0_hist_input_source_0[0]);" in _evaluators(cpp)[0]
    compile_cpp(cpp, label="payload method reading input.source history")


def test_source_beside_a_global_under_builtins_reads_the_requested_bar():
    # CG-W9-SEC (on main) reads a source input on the requested bar under a
    # builtin call as well, and CG-OPEN-ITEMS 5b3791d re-evaluates the global
    # g there: the evaluator keeps no chart read. (Lane CG-XSYM-A2 pinned the
    # chart fallback of nz(src) here, on a base with neither rule.)
    result = transpile_full(PRELUDE + (
        'src = input.source(close, "S")\ng = ta.sma(close, 5)\n'
        'x = request.security(syminfo.tickerid, "60", nz(src) + nz(g))\n') + TRADE)
    evaluator = _evaluators(result["cpp"])[0]
    assert "auto _nz_v = (<SRC S:close>);" in evaluator
    assert "_sec0__ta_sma_1.compute(bar.close)" in evaluator
    assert not any("on the chart's bar, as before" in d.message for d in result["diagnostics"])


def test_history_of_a_reassigned_source_stays_refused():
    # A reassigned global is not the input's series: its history keeps the
    # refusal every earlier build gave it.
    with pytest.raises(CompileError, match="helper-parameter history currently requires"):
        transpile(PRELUDE + (
            'var float src = input.source(close, "S")\nsrc := open\n'
            'f(_s) => _s[1]\n'
            'x = request.security(syminfo.tickerid, "D", f(src))\n') + TRADE)


def test_source_payload_replays_tradingview_tapes(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xa2_source_payload")
    exits = replay(engine, base, {"probe": Build(source("xa2_source_payload", XSYM_A2_TV))})
    tape = tape_exits("xa2_source_payload", XSYM_A2_TV)
    assert len(tape) == 336
    # The close exiting at 20:00 UTC on 2025-04-01: the hourly close, the one
    # before, the 4-hour log change over 2 bars, the hourly SMA of hl2 over 3
    # bars, the hourly RSI of 5 bars less 50, and hl2 two hours back less the
    # close.
    assert tape[1743537600000] == "1909.12|1907.5|0.0256|1910.58|19.7789|8.605"
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    # Overrides naming the twin's sources replay the twin's tape.
    twin = tape_exits("xa2_source_payload_high", XSYM_A2_TV)
    overridden = closed_trades(engine, base / "probe", base / "tape_chart.csv",
                               {"Src": "high", "HL Src": "ohlc4"})
    missed = mismatches(twin, {t["exit_time"]: t["exit_comment"] for t in overridden})
    assert not missed, "\n".join(missed[:10])
    print(f"input.source payloads: {len(tape)} of {len(tape)} exit Signals equal "
          f"TradingView's; under Src=high, HL Src=ohlc4 {len(twin)} of {len(twin)} "
          "equal the twin's")


def test_source_parameter_payload_replays_tradingview_tapes(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xa2_source_param")
    exits = replay(engine, base, {"probe": Build(source("xa2_source_param", XSYM_A2_TV))})
    tape = tape_exits("xa2_source_param", XSYM_A2_TV)
    assert len(tape) == 336
    # The close exiting at 20:00 UTC on 2025-04-01: the hourly close one
    # requested bar back, the 4-hour SMA of 3 closes, and the hourly close
    # less the hourly open.
    assert tape[1743537600000] == "1907.5|1896.1633|1.63"
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    twin = tape_exits("xa2_source_param_low", XSYM_A2_TV)
    overridden = closed_trades(engine, base / "probe", base / "tape_chart.csv", {"Src": "low"})
    missed = mismatches(twin, {t["exit_time"]: t["exit_comment"] for t in overridden})
    assert not missed, "\n".join(missed[:10])
    print(f"input.source payload parameters: {len(tape)} of {len(tape)} exit Signals "
          f"equal TradingView's; under Src=low {len(twin)} of {len(twin)} equal the twin's")
