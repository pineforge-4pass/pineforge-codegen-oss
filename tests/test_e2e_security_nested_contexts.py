"""``request.security`` timeframes through nested helpers, against TradingView.

``g(tf) => request.security(syminfo.tickerid, tf, ta.sma(close, 3))`` is
reached as ``h(tfA)``, ``h(tfB)``, ``k("60")`` and ``k(tfB)`` with ``h(tf) =>
g(tf)`` and ``k(tf) => h(tf)``: every call used to read the chart's
timeframe. TradingView's tape of ``xa_nested_tf_only`` (``fixtures/xsym_tv``,
BINANCE:ETHUSDT.P 15) spells the four values on each close: the 60-minute
SMA, the 240-minute SMA, the 60-minute one again and the 240-minute one
again. The inputs are read at registration: under overrides the probe trades
exactly like its literal twin.
"""

from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path

import pytest

from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, closed_trades, execute_all, ok,
    per_bar_mismatches, skip_unless_e2e_env, trade_count,
)
from tests._security_tapes import mismatches, replay, source, tape_exits


XSYM_TV = Path(__file__).parent / "fixtures" / "xsym_tv"

# ``xa_nested_tf_only`` with each helper call replaced by the request it
# reaches, at the timeframes the override below gives the inputs.
LITERAL_TWIN = """//@version=6
strategy("PF xa nested tf only twin", overlay = true)
a = request.security(syminfo.tickerid, "120", ta.sma(close, 3))
b = request.security(syminfo.tickerid, "D", ta.sma(close, 3))
c = request.security(syminfo.tickerid, "60", ta.sma(close, 3))
d = request.security(syminfo.tickerid, "D", ta.sma(close, 3))
s(x) => na(x) ? "na" : str.tostring(x)
m = minute(time, "UTC")
if m == 0 or m == 30
    strategy.entry("L", strategy.long)
if m == 15 or m == 45
    strategy.close("L", comment = s(a) + "|" + s(b) + "|" + s(c) + "|" + s(d))
"""


def test_nested_helper_timeframes_replay_tradingview_tape(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xsym_nested_tf_only")
    exits = replay(engine, base, {"probe": Build(source("xa_nested_tf_only", XSYM_TV)),
                                  "twin": Build(LITERAL_TWIN)})
    tape = tape_exits("xa_nested_tf_only", XSYM_TV)
    assert len(tape) == 265
    # The close sent on the 02:45 UTC bar (exit at 03:00): the 02:00 hour has
    # completed, no 4-hour bar yet.
    assert tape[1743476400000] == "1831.75|na|1831.75|na"
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    # Overrides of both inputs move a, b and d; the literal "60" stays.
    overridden = closed_trades(engine, base / "probe", base / "tape_chart.csv",
                               {"A": "120", "B": "D"})
    assert {t["exit_time"]: t["exit_comment"] for t in overridden} == exits["twin"]
    print(f"nested helper timeframes: {len(tape)} of {len(tape)} exit Signals equal "
          f"TradingView's; under A=120, B=D the probe's {len(overridden)} exits "
          "equal its literal twin's")


# These controls compare real engine runs. They add no TradingView tape or
# numerical TradingView observation beyond the existing captured fixtures.
def _request_value_source(body: str) -> str:
    return (
        '//@version=6\nstrategy("request values", overlay=true)\n'
        + body
        + '// @pf-trace value=value\n'
        'if not na(value) and bar_index % 8 == 0\n'
        '    strategy.entry("L", strategy.long)\n'
        'if bar_index % 8 == 4\n'
        '    strategy.close("L")\n'
        'plot(value)\n'
    )


@pytest.fixture(scope="module")
def request_value_feed(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("request_value_feed")
    # More than two weeks of the corpus's 15m bars: multiple daily values,
    # requested-bar history, EMA warmup and completed trades are exercised.
    return engine, chart_feed_head(engine, base, 1441)


def _assert_live_request_values(outcome):
    assert outcome.traces
    assert outcome.traces.keys() == outcome.trades.keys()
    for tag, records in outcome.traces.items():
        values = [r["value"] for r in records
                  if r["name"] == "value" and math.isfinite(r["value"])]
        assert len(values) > 100, (tag, len(values))
        assert min(values) < max(values), tag
        assert trade_count(outcome.trades[tag]) > 0, tag


def _value_trace(outcome, tag="default"):
    return [r for r in outcome.traces[tag] if r["name"] == "value"]


def _execute_lookahead_builds(engine, feed, base, builds, *, historical=True):
    # chart_feed_head produces 15m bars. Pin that grid for the default run
    # (inputs.json) and for --inputs-json overrides, rather than depending on
    # the runner's inferred/default grid. The existing COOF witnesses use the
    # same default-run inputs.json contract.
    settings = {"input_tf": "15", "script_tf": "15"}
    if historical:
        # The helper-var-length/loop tape replays enable this same historical
        # projection. Without it a request reads the bar in progress; close[1]
        # reads completed-bar history even while that bar is recomputed.
        settings["runtime_overrides"] = {
            "syminfo_metadata": {"historical_security_lookahead_projection": 1},
        }
    configured = {}
    for key, build in builds.items():
        directory = base / key
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "inputs.json").write_text(json.dumps(settings), encoding="utf-8")
        configured[key] = replace(
            build, overrides=({**settings, **build.overrides}
                              if build.overrides is not None else None))
    return execute_all(engine, feed, base, configured)


@pytest.fixture(scope="module")
def literal_lookahead_runs(request_value_feed, tmp_path_factory):
    engine, feed = request_value_feed
    base = tmp_path_factory.mktemp("literal_lookahead_values")
    builds = {
        mode: Build(_request_value_source(
            'value = request.security(syminfo.tickerid, "D", close[1], '
            f'lookahead=barmerge.lookahead_{mode})\n'), trace=True)
        for mode in ("on", "off")
    }
    return (
        _execute_lookahead_builds(engine, feed, base / "historical", builds),
        _execute_lookahead_builds(
            engine, feed, base / "unprojected", builds, historical=False),
    )


def test_literal_lookahead_native_sensitivity(literal_lookahead_runs):
    """An input-free control must distinguish the issue's exact payload."""
    historical, unprojected = literal_lookahead_runs
    for mode in ("on", "off"):
        _assert_live_request_values(ok(unprojected, mode))
    raw_compared, raw_changed, raw_first = per_bar_mismatches(
        _value_trace(ok(unprojected, "on")), _value_trace(ok(unprojected, "off")))
    print("unprojected literal daily close[1]: "
          f"{raw_compared=}, {raw_changed=}, {raw_first=}")
    assert raw_compared > 100
    on = ok(historical, "on")
    off = ok(historical, "off")
    _assert_live_request_values(on)
    _assert_live_request_values(off)
    compared, changed, first = per_bar_mismatches(_value_trace(on), _value_trace(off))
    print(f"literal daily close[1]: {compared=}, {changed=}, {first=}")
    assert compared > 100 and changed > 0, (
        "INVALID CONTROL: separate literal lookahead_on/off builds are invariant; "
        f"{compared=}, {changed=}. Inspect the native request publication and "
        "chart timeframe setup before judging the input-derived candidate."
    )


@pytest.mark.parametrize("default", [True, False])
def test_bool_input_transport_native_values(request_value_feed, tmp_path, default):
    """Typed overrides must behave like the runner's title-to-text inputs."""
    engine, feed = request_value_feed
    inputs = (
        f'enabled = input.bool({str(default).lower()}, "Enabled")\n'
        'periods = input.int(3, "Periods")\n'
        'weight = input.float(0.5, "Weight")\n'
        'tag = input.string("MiXeD", "Tag")\n'
    )
    body = (
        'value = (enabled ? high : low) + periods * weight '
        '+ (tag == "MiXeD" ? close : open)\n'
        '// @pf-trace bool_input=enabled ? 1 : 0\n'
        '// @pf-trace periods_input=periods\n'
        '// @pf-trace weight_input=weight\n'
        '// @pf-trace text_input=tag == "MiXeD" ? 1 : 0\n'
        '// @pf-trace case_input=tag == "False" ? 1 : 0\n'
        'if enabled and bar_index % 8 == 2\n'
        '    strategy.close("L")\n'
    )
    runtime = {
        "bar_magnifier": False,
        "syminfo_metadata": {"historical_security_lookahead_projection": 1},
    }
    typed = {"Enabled": not default, "Periods": 7, "Weight": 1.25, "Tag": "False",
             "runtime_overrides": runtime}
    # tests._requests.run already uses this flat title -> text schema beside
    # runtime_overrides. It avoids Python's capitalized boolean spelling at
    # the runner's str(value).encode() -> strategy_set_input boundary.
    text = {"Enabled": "false" if default else "true",
            "Periods": "7", "Weight": "1.25", "Tag": "False",
            "runtime_overrides": runtime}
    runs = _execute_lookahead_builds(engine, feed, tmp_path, {
        "typed": Build(_request_value_source(inputs + body), typed, trace=True),
        "text": Build(_request_value_source(inputs + body), text, trace=True),
        "literal_default": Build(_request_value_source(
            f'enabled = {str(default).lower()}\n'
            'periods = 3\nweight = 0.5\ntag = "MiXeD"\n' + body), trace=True),
        "literal_override": Build(_request_value_source(
            f'enabled = {str(not default).lower()}\n'
            'periods = 7\nweight = 1.25\ntag = "False"\n' + body), trace=True),
    })
    expected = {
        "default": {"bool_input": int(default), "periods_input": 3,
                    "weight_input": 0.5, "text_input": 1, "case_input": 0},
        "override": {"bool_input": int(not default), "periods_input": 7,
                     "weight_input": 1.25, "text_input": 0, "case_input": 1},
    }
    # Validate the text control against independent input-free literal builds
    # before judging the typed transport; every trace and trade comes from a run.
    for key in ("text", "typed"):
        outcome = ok(runs, key)
        _assert_live_request_values(outcome)
        assert set(outcome.trades) == {"default", "override"}
        for tag, markers in expected.items():
            for name, value in markers.items():
                observed = [r["value"] for r in outcome.traces[tag]
                            if r["name"] == name]
                print(f"input transport {key=} {default=} {tag=} {name=}: "
                      f"{len(observed)} values {set(observed)}")
                assert len(observed) == len(_value_trace(outcome, tag)) > 100
                assert all(actual == value for actual in observed), (
                    key, tag, name, value, set(observed))
            literal = ok(runs, f"literal_{tag}")
            _assert_live_request_values(literal)
            compared, mismatched, first = per_bar_mismatches(
                outcome.traces[tag], literal.traces["default"])
            assert compared > 100 and mismatched == 0, (key, tag, first)
            assert outcome.trades[tag] == literal.trades["default"], (key, tag)
        compared, changed, first = per_bar_mismatches(
            _value_trace(outcome), _value_trace(outcome, "override"))
        assert compared > 100 and changed > 0, (key, compared, changed, first)
        assert outcome.trades["default"] != outcome.trades["override"], key
        transported = json.loads((tmp_path / key / "inputs_override.json").read_text())
        assert transported["input_tf"] == transported["script_tf"] == "15"
        assert transported["runtime_overrides"] == runtime
        assert transported["runtime_overrides"]["bar_magnifier"] is False
        assert transported["Tag"] == "False"
    assert_same_runs(ok(runs, "typed"), ok(runs, "text"))
    # The in-process report helper reaches the same runner's input setter.
    # Compare its complete trades too, using the canonical text and literal
    # controls instead of treating a matching CLI serialization as enough.
    reports = {
        key: closed_trades(engine, tmp_path / key, feed, params,
                           input_tf="15", script_tf="15")
        for key, params in (("typed", typed), ("text", text),
                            ("literal_override", None))
    }
    assert reports["text"]
    assert reports["text"] == reports["literal_override"]
    assert reports["typed"] == reports["text"]


@pytest.mark.parametrize("default", [True, False])
def test_input_derived_lookahead_native_values(
        request_value_feed, literal_lookahead_runs, tmp_path, default):
    engine, feed = request_value_feed
    declare = (
        f'useLookahead = input.bool({str(default).lower()}, "Lookahead")\n'
        '// @pf-trace lookahead_input=useLookahead ? 1 : 0\n'
    )
    subject = _request_value_source(
        declare
        + 'mode = useLookahead ? barmerge.lookahead_on : barmerge.lookahead_off\n'
        'value = request.security(syminfo.tickerid, "D", close[1], lookahead=mode)\n')
    # The public workaround: literal requests, with the input selecting their
    # returned values. An override must change registration as well as defaults.
    reference = _request_value_source(
        declare
        + 'dailyOn = request.security(syminfo.tickerid, "D", close[1], '
        'lookahead=barmerge.lookahead_on)\n'
        'dailyOff = request.security(syminfo.tickerid, "D", close[1], '
        'lookahead=barmerge.lookahead_off)\n'
        'value = useLookahead ? dailyOn : dailyOff\n')
    overrides = {"Lookahead": not default}
    runs = _execute_lookahead_builds(engine, feed, tmp_path, {
        "subject": Build(subject, overrides=overrides, trace=True),
        "reference": Build(reference, overrides=overrides, trace=True),
    })
    control = ok(runs, "reference")
    _assert_live_request_values(control)
    for tag, enabled in (("default", default), ("override", not default)):
        selected = [r["value"] for r in control.traces[tag]
                    if r["name"] == "lookahead_input"]
        assert len(selected) == len(_value_trace(control, tag)) > 100
        assert all(value == int(enabled) for value in selected), (
            f"INVALID CONTROL: {tag} did not read Lookahead={enabled}; "
            f"observed input values {set(selected)}"
        )
        literal = ok(literal_lookahead_runs[0], "on" if enabled else "off")
        _assert_live_request_values(literal)
        compared, mismatched, first = per_bar_mismatches(
            _value_trace(control, tag), _value_trace(literal))
        assert compared > 100 and mismatched == 0, (
            f"INVALID CONTROL: workaround {tag} disagrees with its separate "
            f"literal request; {compared=}, {mismatched=}, {first=}"
        )
        assert control.trades[tag] == literal.trades["default"], tag
    # Compare only requested values: the input trace itself must never satisfy
    # sensitivity when both lookahead streams return the same values.
    compared, changed, first = per_bar_mismatches(
        _value_trace(control), _value_trace(control, "override"))
    print(f"lookahead workaround {default=}: {compared=}, {changed=}, {first=}")
    assert compared > 100 and changed > 0, (
        "INVALID CONTROL: the input toggled but the workaround values did not; "
        f"{compared=}, {changed=}. This is not a candidate equality verdict."
    )
    candidate = ok(runs, "subject")
    _assert_live_request_values(candidate)
    assert_same_runs(candidate, control)


def test_empty_timeframe_native_values(request_value_feed, tmp_path):
    engine, feed = request_value_feed
    builds = {}
    for key, timeframe in (("subject", '""'), ("reference", "timeframe.period")):
        body = (
            f'value = request.security(syminfo.tickerid, {timeframe}, close)\n'
            'diff = value - close\n'
            '// @pf-trace diff=diff\n')
        builds[key] = Build(_request_value_source(body), trace=True)
    # Also compare against chart close directly, so matching two request
    # implementations that both lag the chart cannot satisfy this control.
    builds["chart"] = Build(_request_value_source(
        'value = close\ndiff = value - close\n// @pf-trace diff=diff\n'), trace=True)
    runs = execute_all(engine, feed, tmp_path, builds)
    chart = ok(runs, "chart")
    control = ok(runs, "reference")
    _assert_live_request_values(chart)
    assert_same_runs(control, chart)
    candidate = ok(runs, "subject")
    _assert_live_request_values(candidate)
    assert_same_runs(candidate, chart)
    differences = [r["value"] for r in candidate.traces["default"] if r["name"] == "diff"]
    assert len(differences) == 1441
    assert all(value == 0 for value in differences)


def test_requested_history_in_nested_ema_native_values(request_value_feed, tmp_path):
    engine, feed = request_value_feed
    declare = 'base = request.security(syminfo.tickerid, "D", close)\n'
    builds = {}
    for key, payload in (
        ("subject", "ta.ema(base[1], 5)"),
        # A fixed-length EMA commutes with a one-bar shift of its input.
        # Keeping the offset inside this request uses the requested clock.
        ("reference", "ta.ema(base, 5)[1]"),
        ("unshifted", "ta.ema(base, 5)"),
    ):
        builds[key] = Build(_request_value_source(
            declare
            + f'value = request.security(syminfo.tickerid, "240", {payload})\n'),
            trace=True)
    runs = execute_all(engine, feed, tmp_path, builds)
    control = ok(runs, "reference")
    unshifted = ok(runs, "unshifted")
    _assert_live_request_values(control)
    _assert_live_request_values(unshifted)
    compared, changed, _ = per_bar_mismatches(
        control.traces["default"], unshifted.traces["default"])
    assert compared > 100 and changed > 0
    candidate = ok(runs, "subject")
    _assert_live_request_values(candidate)
    assert_same_runs(candidate, control)


@pytest.mark.parametrize("wrapper", [
    'fetch(sym, tf, expr) => request.security(sym, tf, expr)\n',
    'fetch(sym, tf, expr) => request.security(sym, tf, expr)\n'
    'forward(sym, tf, expr) => fetch(sym, tf, expr)\n',
])
def test_uncalled_request_wrapper_native_values(request_value_feed, tmp_path, wrapper):
    engine, feed = request_value_feed
    direct = 'value = request.security(syminfo.tickerid, "D", close[1])\n'
    runs = execute_all(engine, feed, tmp_path, {
        "subject": Build(_request_value_source(wrapper + direct), trace=True),
        "reference": Build(_request_value_source(direct), trace=True),
        "called": Build(_request_value_source(
            wrapper + 'value = fetch(syminfo.tickerid, "D", close[1])\n'), trace=True),
    })
    control = ok(runs, "reference")
    _assert_live_request_values(control)
    called = ok(runs, "called")
    _assert_live_request_values(called)
    assert_same_runs(called, control)
    candidate = ok(runs, "subject")
    _assert_live_request_values(candidate)
    assert_same_runs(candidate, control)


@pytest.mark.parametrize("offset", [1, 2])
def test_requested_history_alias_and_repeated_reads_native_values(
        request_value_feed, tmp_path, offset):
    engine, feed = request_value_feed
    declare = (
        'base = request.security(syminfo.tickerid, "D", close)\n'
        'first = base\nsecond = first\n')
    builds = {
        "subject": Build(_request_value_source(
            declare
            + 'value = request.security(syminfo.tickerid, "240", '
            f'ta.ema(second[{offset}], 5) + second[{offset}])\n'), trace=True),
        "reference": Build(_request_value_source(
            declare
            + 'value = request.security(syminfo.tickerid, "240", '
            # The supported TA-result histories provide an independent
            # control: a one-period SMA is the requested input's value.
            f'ta.ema(base, 5)[{offset}] + ta.sma(base, 1)[{offset}])\n'), trace=True),
    }
    runs = execute_all(engine, feed, tmp_path, builds)
    control = ok(runs, "reference")
    _assert_live_request_values(control)
    candidate = ok(runs, "subject")
    _assert_live_request_values(candidate)
    assert_same_runs(candidate, control)


@pytest.mark.parametrize("wrapper", [
    'fetch(sym, tf, expr, length) => '
    'request.security(sym, tf, ta.sma(expr, length))\n',
    'accumulate(src) =>\n'
    '    var total = 0.0\n'
    '    total += src\n'
    '    total\n'
    'fetch(sym, tf, expr) => '
    'request.security(sym, tf, expr > 0 ? accumulate(expr) : 0.0)\n',
])
def test_uncalled_requested_state_wrapper_native_values(
        request_value_feed, tmp_path, wrapper):
    engine, feed = request_value_feed
    direct = 'value = request.security(syminfo.tickerid, "D", close[1])\n'
    runs = execute_all(engine, feed, tmp_path, {
        "subject": Build(_request_value_source(wrapper + direct), trace=True),
        "reference": Build(_request_value_source(direct), trace=True),
    })
    control = ok(runs, "reference")
    _assert_live_request_values(control)
    candidate = ok(runs, "subject")
    _assert_live_request_values(candidate)
    assert_same_runs(candidate, control)


def _reached_request_source(body: str) -> str:
    return (
        '//@version=6\nstrategy("reached request values", overlay=true)\n'
        + body
        + '// @pf-trace value=value\n'
        'if not na(value) and bar_index % 8 == 0\n'
        '    strategy.entry("L", strategy.long, qty=value / close)\n'
        'if bar_index % 8 == 4\n'
        '    strategy.close("L")\n'
        'plot(value)\n'
    )


@pytest.fixture(scope="module")
def fixed_input_lookahead_literal_runs(request_value_feed, tmp_path_factory):
    """Independent input-free builds with value-sensitive order quantities."""
    engine, feed = request_value_feed
    return _execute_lookahead_builds(
        engine, feed, tmp_path_factory.mktemp("fixed_input_lookahead_controls"), {
            mode: Build(_reached_request_source(
                'value = request.security(syminfo.tickerid, "D", close[1], '
                f'lookahead=barmerge.lookahead_{mode})\n'), trace=True)
            for mode in ("on", "off")
        })


@pytest.mark.parametrize("kind, on_value, off_value, predicate", [
    pytest.param("time", 1735689600000, 1767225600000,
                 "selection < 1751328000000", id="time"),
    pytest.param("price", 1.25, 2.75, "selection < 2.0", id="price"),
    pytest.param("session", "0930-1600", "0000-2400",
                 'selection == "0930-1600"', id="session"),
    pytest.param("text_area", "Enabled\nMiXeD", "disabled\nMiXeD",
                 'selection == "Enabled\\nMiXeD"', id="text-area"),
])
@pytest.mark.parametrize("default_on", [True, False], ids=["on-to-off", "off-to-on"])
def test_fixed_primitive_input_lookahead_native_values(
        request_value_feed, fixed_input_lookahead_literal_runs, tmp_path,
        kind, on_value, off_value, predicate, default_on):
    """Typed input transport, requested arrays and orders must all agree."""
    engine, feed = request_value_feed
    default, override = ((on_value, off_value) if default_on else (off_value, on_value))
    # String traces encode exact membership in these two complete values.
    # An arbitrary string taking the off branch must produce -1, not pass.
    observed = (
        f'selection == {json.dumps(on_value)} ? 1 : '
        f'selection == {json.dumps(off_value)} ? 0 : -1'
        if isinstance(on_value, str) else "selection")
    declare = (
        f'rawChoice = input.{kind}({json.dumps(default)}, "Selection")\n'
        'selection = rawChoice\n'
        f'enabled = {predicate}\n'
        f'// @pf-trace input_observed={observed}\n'
        '// @pf-trace input_selected=enabled ? 1 : 0\n')
    subject = _reached_request_source(
        declare
        + 'first = enabled ? barmerge.lookahead_on : barmerge.lookahead_off\n'
        'second = first\n'
        'value = request.security(syminfo.tickerid, "D", close[1], lookahead=second)\n')
    reference = _reached_request_source(
        declare
        + 'dailyOn = request.security(syminfo.tickerid, "D", close[1], '
        'lookahead=barmerge.lookahead_on)\n'
        'dailyOff = request.security(syminfo.tickerid, "D", close[1], '
        'lookahead=barmerge.lookahead_off)\n'
        'value = enabled ? dailyOn : dailyOff\n')
    runs = _execute_lookahead_builds(engine, feed, tmp_path, {
        "subject": Build(subject, overrides={"Selection": override}, trace=True),
        "reference": Build(reference, overrides={"Selection": override}, trace=True),
    })
    literals = fixed_input_lookahead_literal_runs
    for mode in ("on", "off"):
        _assert_live_request_values(ok(literals, mode))
    compared, changed, first = per_bar_mismatches(
        _value_trace(ok(literals, "on")), _value_trace(ok(literals, "off")))
    assert compared > 100 and changed > 0, (
        "INVALID CONTROL: literal lookahead requests are invariant", compared, changed, first)
    assert ok(literals, "on").trades["default"] != ok(literals, "off").trades["default"], (
        "INVALID CONTROL: requested values did not change order quantities")

    # Judge the independent workaround before the candidate. A serialized
    # override alone says nothing about the setter or registration's getter.
    control = ok(runs, "reference")
    for key in ("reference", "subject"):
        outcome = ok(runs, key)
        assert set(outcome.traces) == set(outcome.trades) == {"default", "override"}
        _assert_live_request_values(outcome)
        for tag, requested, enabled in (
                ("default", default, default_on), ("override", override, not default_on)):
            markers = {"input_selected": int(enabled),
                       "input_observed": int(enabled) if isinstance(requested, str) else requested}
            for name, expected in markers.items():
                observed_values = [r["value"] for r in outcome.traces[tag] if r["name"] == name]
                print(f"fixed input {kind=} {key=} {tag=} {requested=!r} {name=}: "
                      f"expected={expected!r}, observed={set(observed_values)}")
                assert len(observed_values) == len(_value_trace(outcome, tag)) > 100
                assert all(actual == expected for actual in observed_values), (
                    key, tag, requested, name, expected, set(observed_values))
            literal = ok(literals, "on" if enabled else "off")
            compared, mismatched, first = per_bar_mismatches(
                _value_trace(outcome, tag), _value_trace(literal))
            assert compared > 100 and mismatched == 0, (key, tag, first)
            assert outcome.trades[tag] == literal.trades["default"], (key, tag)
        compared, changed, first = per_bar_mismatches(
            _value_trace(outcome), _value_trace(outcome, "override"))
        assert compared > 100 and changed > 0, (key, compared, changed, first)
        assert outcome.trades["default"] != outcome.trades["override"], key
        transported = json.loads((tmp_path / key / "inputs_override.json").read_text())
        assert type(transported["Selection"]) is type(override)
        assert transported["Selection"] == override
    assert_same_runs(ok(runs, "subject"), control)


@pytest.mark.parametrize("default, override", [("60", "240"), ("240", "60")],
                         ids=["hour-to-four-hour", "four-hour-to-hour"])
@pytest.mark.parametrize("forwarder, call", [
    pytest.param('forward() => fetch()\n', "forward()", id="transitive"),
    # TradingView refuses a method whose first parameter is unused (PF-E5087),
    # so the receiver is read in a way that cannot change the value: a finite
    # close times zero is zero, and adding zero leaves the requested value.
    pytest.param('method forward(float anchor) => fetch() + anchor * 0.0\n',
                 "close.forward()", id="method"),
])
def test_reached_request_helper_native_values(
        request_value_feed, tmp_path, forwarder, call, default, override):
    """A request reached solely through a helper or method stays live."""
    engine, feed = request_value_feed
    declare = (
        f'resolution = input.timeframe("{default}", "Resolution")\n'
        '// @pf-trace resolution_input=resolution == "60" ? 1 : 0\n'
    )
    # Only the forwarder calls fetch. References are separate strategy builds,
    # so their top-level requests cannot make this helper reachable.
    subject = _reached_request_source(
        declare
        + 'fetch() => request.security(syminfo.tickerid, resolution, close[1])\n'
        + forwarder + f'value = {call}\n')
    reference = _reached_request_source(
        declare
        + 'hourly = request.security(syminfo.tickerid, "60", close[1])\n'
        'fourHourly = request.security(syminfo.tickerid, "240", close[1])\n'
        'value = resolution == "60" ? hourly : fourHourly\n')
    builds = {
        "subject": Build(subject, overrides={"Resolution": override}, trace=True),
        "reference": Build(reference, overrides={"Resolution": override}, trace=True),
    }
    for timeframe in ("60", "240"):
        builds[f"fixed_{timeframe}"] = Build(_reached_request_source(
            f'value = request.security(syminfo.tickerid, "{timeframe}", close[1])\n'),
            trace=True)
    runs = _execute_lookahead_builds(engine, feed, tmp_path, builds)
    control = ok(runs, "reference")
    assert set(control.traces) == set(control.trades) == {"default", "override"}
    _assert_live_request_values(control)
    for tag, timeframe in (("default", default), ("override", override)):
        selected = [r["value"] for r in control.traces[tag]
                    if r["name"] == "resolution_input"]
        assert len(selected) == len(_value_trace(control, tag)) > 100
        assert all(value == int(timeframe == "60") for value in selected), (
            f"INVALID CONTROL: {tag} did not read Resolution={timeframe}"
        )
        fixed = ok(runs, f"fixed_{timeframe}")
        _assert_live_request_values(fixed)
        compared, mismatched, first = per_bar_mismatches(
            _value_trace(control, tag), _value_trace(fixed))
        assert compared > 100 and mismatched == 0, (
            f"INVALID CONTROL: reference {tag} differs from fixed {timeframe}; "
            f"{compared=}, {mismatched=}, {first=}"
        )
        assert control.trades[tag] == fixed.trades["default"], tag
    compared, changed, first = per_bar_mismatches(
        _value_trace(control), _value_trace(control, "override"))
    assert compared > 100 and changed > 0, (
        f"INVALID CONTROL: Resolution did not change requested values; "
        f"{compared=}, {changed=}, {first=}"
    )
    assert control.trades["default"] != control.trades["override"], (
        "INVALID CONTROL: requested values did not change order quantities/trades"
    )
    candidate = ok(runs, "subject")
    assert set(candidate.traces) == set(candidate.trades) == {"default", "override"}
    # A mutant that empties every helper request must get this far, after the
    # independent references and the subject's compile/run have succeeded.
    _assert_live_request_values(candidate)
    summary = assert_same_runs(candidate, control)
    print(f"reached helper {call=} {default=} {override=}: "
          f"{compared=}, {changed=}, {first=}; {summary}")
