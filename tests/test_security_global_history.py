"""History of a global a ``request.security`` payload reads, on the requested bars.

TradingView evaluates a payload's globals on every requested bar: ``cs[1]``
of ``cs = smooth(close, 2)`` is the smoothed value one requested bar back.
The builder put the global's value under a subscript of its own, which no
prepass had sized: a user function's call was refused ("request.security
helper call history is only supported in the payload itself") and an
operator expression was indexed as a C++ scalar, which did not compile. Each
read of the global inlined the call again, which advanced its TA state once
per read (``cs + cs``). Through a helper's parameter
(``held(_s, _tf, _x) => request.security(_s, _tf, _x[1], ...)`` called with
``cs``) the value was not put in, and the payload read ``na``: the
talaeaelhussein-prop-scalper-v2-final probes booked no trade.

The history is now the requested clock's own, pushed once per completed
requested bar with what the payload reads as the global, which it lowers
once per evaluator; ``security_contexts`` puts such a global in a helper's
parameter. A Heikin-Ashi request inside the payload (``useHA ?
request.security(ticker.heikinashi(syminfo.tickerid), timeframe.period,
close) : close``) reads the requested context's Heikin-Ashi bars on
TradingView, which the evaluator does not build: evaluating it stops the run,
so a selection that never takes it runs.

TradingView's tapes of ``taila_nested_bucket`` and its twin with other
defaults (``fixtures/tail_a_tv``, BINANCE:ETHUSDT.P 15) spell every request's
value on each close; under overrides naming the twin's defaults the probe
replays its twin's tape.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile, transpile_full
from tests._compile import compile_cpp
from tests._e2e import Build, closed_trades, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits


TAIL_A_TV = Path(__file__).parent / "fixtures" / "tail_a_tv"
PRELUDE = '//@version=6\nstrategy("T")\n'
SMOOTH = (
    'smooth(_src, _len) =>\n'
    '    e1 = ta.ema(_src, _len)\n'
    '    al = ta.alma(_src, _len, 0.85, 5)\n'
    '    e1 + al\n'
)
HELD = ('held(_s, _tf, _x) => request.security(_s, _tf, _x[1], '
        'lookahead = barmerge.lookahead_on)\n')
TRADE = 'if x > close\n    strategy.entry("L", strategy.long)\n'
# The value the payload reads as the global, one requested bar back, pushed
# once where the evaluator closes.
HISTORY = ('if (is_complete) {{ _sec{n}_expr_hist_0_next = _hv; _sec{n}_expr_hist_0_set = true; }} '
           'int _hidx = (int)(1); if (is_na(_hidx)) return na<double>(); '
           'return (_hidx <= 0) ? _hv : _sec{n}_expr_hist_0[_hidx - 1]; }}())')
PUSH = ('if (is_complete) {{ if (_sec{n}_expr_hist_0_set) '
        '_sec{n}_expr_hist_0.push(_sec{n}_expr_hist_0_next); }}')
NA_WARNING = "whose argument PineForge does not recompute on the requested bars"


def _evaluators(cpp: str) -> list[str]:
    return [" ".join(body.split()) for body in re.findall(
        r"void _eval_security_\d+\(const Bar& bar, bool is_complete\) \{\n(.*?)\n    \}",
        cpp, re.S)]


def _warnings(result: dict) -> list[str]:
    return [d.message for d in result["diagnostics"] if NA_WARNING in d.message]


def test_history_of_a_multi_statement_global_is_lowered():
    # Every earlier build refused it.
    cpp = transpile(PRELUDE + SMOOTH + 'cs = smooth(close, 2)\n'
                    'x = request.security(syminfo.tickerid, "120", cs[1], '
                    'lookahead = barmerge.lookahead_on)\n' + TRADE)
    evaluator = _evaluators(cpp)[0]
    assert ("_req_sec_0 = ([&]() -> double { double _hv = ((_sec0_smooth_1_e1 + _sec0_smooth_2_al)); "
            + HISTORY.format(n=0) + ";") in evaluator
    assert evaluator.endswith(PUSH.format(n=0))
    compile_cpp(cpp, label="history of a multi-statement global")


def test_history_of_an_operator_global_compiles():
    # Every earlier build indexed the C++ scalar: ``(...)[(int)(1)]``.
    cpp = transpile(PRELUDE + 'up = input.bool(true, "Up")\nsrc = up ? high : low\n'
                    'x = request.security(syminfo.tickerid, "120", src[1])\n' + TRADE)
    evaluator = _evaluators(cpp)[0]
    assert ('_req_sec_0 = ([&]() -> double { double _hv = (((get_input_bool("Up", true)) '
            '? (bar.high) : (bar.low))); ' + HISTORY.format(n=0) + ";") in evaluator
    compile_cpp(cpp, label="history of an operator global")


def test_a_global_read_twice_is_one_value_per_requested_bar():
    # ``cs - cs[1]`` inlined the call at each read: two compute() calls of
    # one TA object per requested bar.
    cpp = transpile(PRELUDE + SMOOTH + 'cs = smooth(close, 2)\n'
                    'x = request.security(syminfo.tickerid, "120", cs - cs[1])\n' + TRADE)
    evaluator = _evaluators(cpp)[0]
    assert evaluator.count("_sec0__ta_ema_1_v1.compute(") == 1
    assert evaluator.count("_sec0__ta_alma_2_v1.compute(") == 1
    assert ("_req_sec_0 = ((_sec0_smooth_1_e1 + _sec0_smooth_2_al) - ([&]() -> double { "
            "double _hv = ((_sec0_smooth_1_e1 + _sec0_smooth_2_al)); ") in evaluator
    compile_cpp(cpp, label="a global read twice")


def test_helper_parameter_bound_to_a_multi_statement_global_is_put_in():
    # It read na, with the warning.
    result = transpile_full(PRELUDE + SMOOTH + HELD + 'cs = smooth(close, 2)\n'
                            'x = held(syminfo.tickerid, "120", cs)\n' + TRADE)
    assert not _warnings(result)
    evaluator = _evaluators(result["cpp"])[0]
    assert ("_req_sec_0 = ([&]() -> double { double _hv = ((_sec0_smooth_1_e1 + _sec0_smooth_2_al)); "
            + HISTORY.format(n=0) + ";") in evaluator
    compile_cpp(result["cpp"], label="helper parameter bound to a global")


def test_helper_parameter_bound_to_an_operator_global_is_put_in():
    # It read na, with the warning: its history did not compile.
    result = transpile_full(PRELUDE + 'mid = (high + low) / 2\n'
                            'g(_e) => request.security(syminfo.tickerid, "D", _e[1])\n'
                            'x = g(mid)\n' + TRADE)
    assert not _warnings(result)
    evaluator = _evaluators(result["cpp"])[0]
    assert ("_req_sec_0 = ([&]() -> double { double _hv = (((bar.high + bar.low) / 2)); "
            + HISTORY.format(n=0) + ";") in evaluator
    compile_cpp(result["cpp"], label="helper parameter bound to an operator global")


def test_cast_of_a_helper_local_in_a_ta_length_is_lowered():
    # The Hull average's lengths: ``int(math.round(_len / 2.0))`` rendered
    # ``_len`` on the chart's terms, an unknown variable, where the TA
    # constructors are planned.
    result = transpile_full(PRELUDE + (
        'hull(_src, _len) =>\n'
        '    half = math.max(1, int(math.round(_len / 2.0)))\n'
        '    ta.wma(2.0 * ta.wma(_src, half) - ta.wma(_src, _len), half)\n'
        'len = input.int(4, "Len")\n') + HELD + 'hs = hull(close, len)\n'
        'x = held(syminfo.tickerid, "120", hs)\n' + TRADE)
    assert not _warnings(result)
    evaluator = _evaluators(result["cpp"])[0]
    assert 'std::round(((double)(get_input_int("Len", 4)) / (double)(2.0)))' in evaluator
    compile_cpp(result["cpp"], label="cast of a helper local")


@pytest.mark.parametrize("helper", [
    # A global's value that is not one the builder lowers keeps the warning.
    'var float cs = na\ncs := close\n',
    'cs = str.tostring(close) == "1" ? 1.0 : 0.0\n',
])
def test_other_globals_keep_the_na_lowering(helper):
    result = transpile_full(PRELUDE + HELD + helper + 'x = held(syminfo.tickerid, "120", cs)\n'
                            + TRADE)
    assert _warnings(result)


def test_heikin_ashi_request_inside_a_payload_stops_where_taken():
    cpp = transpile(PRELUDE + SMOOTH + (
        'ha = input.bool(false, "HA")\n'
        'src = ha ? request.security(ticker.heikinashi(syminfo.tickerid), timeframe.period, '
        'close) : close\n'
        'cs = smooth(src, 2)\n'
        'x = request.security(syminfo.tickerid, "120", cs[1], '
        'lookahead = barmerge.lookahead_on)\n') + TRADE)
    chart, requested = _evaluators(cpp)
    # The chart's own request is unchanged; the payload's arm stops the run.
    assert chart == "_req_sec_0 = bar.close;"
    # The requested-context stop adds its fixed reason and original source line.
    assert ('((get_input_bool("HA", false)) ? (([&]() -> double { _PF_UNSUPPORTED_STOP('
            '"nested_heikinashi_request", 8, "request.security: a Heikin-Ashi request inside another '
            "request's expression reads that request's Heikin-Ashi bars, which PineForge "
            'does not build"); return na<double>(); }())) : (bar.close))') in requested
    compile_cpp(cpp, label="Heikin-Ashi request inside a payload")


def test_nested_bucket_replays_tradingview_tapes(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("taila_nested_bucket")
    exits = replay(engine, base, {"probe": Build(source("taila_nested_bucket", TAIL_A_TV))})
    tape = tape_exits("taila_nested_bucket", TAIL_A_TV)
    # 673 closes; the export's own close of the last bar shares its instant
    # with the probe's, and carries no comment.
    assert len(tape) == 672
    # The close sent on the 18:00 UTC bar of 2025-04-01: the "120" bucket
    # that opened there (20179.75 days) reads the smoothed close and open of
    # the bucket before, through the helpers and at the top level.
    assert tape[1743530400000] == "1914.886935|1913.274803|1914.886935|20179.75|20179.8333"
    missed = mismatches({ms: s for ms, s in tape.items() if s}, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    # The twin: Hull averages of the close one bar back, whose history the
    # helper reads at each of four TA calls.
    twin = tape_exits("taila_nested_bucket_hull_lag", TAIL_A_TV)
    overridden = closed_trades(engine, base / "probe", base / "tape_chart.csv",
                               {"Kind": "HULL", "Lag": 1})
    missed = mismatches({ms: s for ms, s in twin.items() if s},
                        {t["exit_time"]: t["exit_comment"] for t in overridden})
    assert not missed, "\n".join(missed[:10])
    # A run that takes the Heikin-Ashi arm stops where it reads it.
    with pytest.raises(RuntimeError, match="a Heikin-Ashi request inside another request"):
        closed_trades(engine, base / "probe", base / "tape_chart.csv", {"Heikin": "true"})
    print(f"nested bucket: {len(tape) - 1} of {len(tape) - 1} exit Signals equal TradingView's; "
          f"under Kind=HULL, Lag=1 the twin's {len(twin) - 1} of {len(twin) - 1}")
