"""Evaluated negative history offsets stop; other reads retain their values."""

from decimal import Decimal
import re
from textwrap import dedent

import pytest

from pineforge_codegen import transpile
from tests._e2e import reference_codegen, transpile_json
from tests.test_bool_string_inference import _messages, _run_native, native_env


CPP_REFERENCE = "c12c1877da48629b9c711f745a752ffc20ceef17"
HEADER = '//@version=6\nstrategy("History offset", overlay=true)\n'
INPUTS = 'a = input.int(30, "a")\nb = input.int(31, "b")\n'
OFFSET_MESSAGE = "The history offset must be a value between 0 and 10000."

REPRO_SOURCE = dedent("""\
    //@version=6
    strategy("repro", overlay = true, pyramiding = 10)
    a = input.int(30, "a")
    b = input.int(31, "b")
    float v = close[a - b]
    if bar_index == 10
        strategy.entry("p", strategy.long, qty = na(v) ? 7 : v == close ? 1 : v == close[1] ? 2 : 3)
    if bar_index == 11
        strategy.close_all()
""")


def _assert_negative_stop(tmp_path, native_env, source, offset=-1):
    # _run_native raises here only after translation and compilation succeed.
    with pytest.raises(RuntimeError, match="run_strategy failed") as failure:
        _run_native(tmp_path, native_env, source)
    message = str(failure.value)
    assert OFFSET_MESSAGE in message
    located = re.search(
        r"History reference at line (\d+): offset (-?\d+(?:\.\d+)?)\.", message,
    )
    assert located is not None, message
    assert Decimal(located[2]) == Decimal(str(offset))
    assert int(located[1]) > 0
    return message


def test_negative_input_difference_stops_the_strategy(tmp_path, native_env):
    message = _assert_negative_stop(tmp_path, native_env, REPRO_SOURCE)
    assert "History reference at line 5:" in message


NEGATIVE_READS = {
    "user_series": "value = close + open\nobserved = value[a - b]\n",
    "ta_series": "value = ta.sma(close, 2)\nobserved = value[a - b]\n",
    "ta_result": "observed = ta.sma(close, 2)[a - b]\n",
    "highest_result": "observed = ta.highest(high, 3)[a - b]\n",
    "lowest_result": "observed = ta.lowest(low, 3)[a - b]\n",
    "compound_result": "observed = (close - open)[a - b]\n",
    "left_operand": "observed = close[a - b] + 1\n",
    "condition": (
        "if close[a - b] > 0\n"
        '    strategy.entry("p", strategy.long)\n'
    ),
    "argument": 'log.info("value {0}", close[a - b])\n',
    "var_initializer": "var float observed = close[a - b]\n",
    "call_result": "read(float x) => x * 2\nobserved = read(close)[a - b]\n",
    "function_parameter": (
        "read(float src, int lag) => src[lag]\n"
        "observed = read(close, a - b)\n"
    ),
    "function_global": (
        "value = close + open\n"
        "read(int lag) => value[lag]\nobserved = read(a - b)\n"
    ),
    "session_flag": "observed = session.ismarket[a - b]\n",
    "function_session_flag": (
        "read(int lag) => session.ismarket[lag]\nobserved = read(a - b)\n"
    ),
    "strategy_series": "observed = strategy.position_size[a - b]\n",
    "array_read": (
        "values = array.from(close)\nobserved = (values[a - b]).get(0)\n"
    ),
    "array_copy": (
        "values = array.from(close)\nprevious = values[a - b]\n"
        "observed = na(previous)\n"
    ),
    "array_na": "values = array.from(close)\nobserved = na(values[a - b])\n",
    "array_change": (
        "values = array.from(close)\n(values[a - b]).push(open)\n"
    ),
    "matrix_read": (
        "values = matrix.new<float>(1, 1, close)\n"
        "observed = (values[a - b]).get(0, 0)\n"
    ),
    "object_reference": (
        "type Cell\n    float value\n"
        "cell = Cell.new(close)\nobserved = (cell[a - b]).value\n"
    ),
    "field_reference": (
        "type Cell\n    float value\n"
        "type Holder\n    Cell child\n"
        "holder = Holder.new(Cell.new(close))\n"
        "observed = (holder.child[a - b]).value\n"
    ),
    "requested_close": (
        'observed = request.security(syminfo.tickerid, "15", close[a - b])\n'
    ),
    "requested_time": (
        'observed = request.security(syminfo.tickerid, "15", time[a - b])\n'
    ),
    "requested_bar_index": (
        'observed = request.security(syminfo.tickerid, "15", bar_index[a - b])\n'
    ),
    "requested_ta": (
        'observed = request.security(syminfo.tickerid, "15", ta.sma(close, 2)[a - b])\n'
    ),
    "requested_call": (
        "read() => close + open\n"
        'observed = request.security(syminfo.tickerid, "15", read()[a - b])\n'
    ),
    "requested_global": (
        "value = close + open\n"
        'observed = request.security(syminfo.tickerid, "15", value[a - b])\n'
    ),
    "requested_local": (
        "read(int lag) =>\n    value = close + open\n    value[lag]\n"
        'observed = request.security(syminfo.tickerid, "15", read(a - b))\n'
    ),
    "requested_composed_local": (
        "read(float src, int lag) => src[lag]\n"
        'observed = request.security(syminfo.tickerid, "15", read(close[2], a - b))\n'
    ),
    "requested_composed_argument": (
        "read(float src, int lag) => src[lag]\n"
        'observed = request.security(syminfo.tickerid, "15", read(close[a - b], 2))\n'
    ),
    "requested_source": (
        'src = input.source(close, "Source")\n'
        'observed = request.security(syminfo.tickerid, "15", src[a - b])\n'
    ),
    "requested_session": (
        'observed = request.security(syminfo.tickerid, "15", session.ismarket[a - b])\n'
    ),
}


@pytest.mark.parametrize("shape", tuple(NEGATIVE_READS))
def test_each_evaluated_history_reader_stops(tmp_path, native_env, shape):
    body = NEGATIVE_READS[shape]
    if "observed" in body:
        body += 'log.info("value {0}", observed)\n'
    _assert_negative_stop(tmp_path, native_env, HEADER + INPUTS + body)


def test_negative_wide_offset_is_checked_before_narrowing(tmp_path, native_env):
    source = HEADER + (
        "var int lag = time_close\nlag := -4294967296\n"
        "observed = close[lag]\n"
        'log.info("value {0}", observed)\n'
    )
    _assert_negative_stop(tmp_path, native_env, source, -4294967296)


def test_negative_fraction_is_checked_before_truncation(tmp_path, native_env):
    source = HEADER + (
        'lag = input.float(-0.5, "Lag")\n'
        "observed = close[lag]\n"
        'log.info("value {0}", observed)\n'
    )
    _assert_negative_stop(tmp_path, native_env, source, -0.5)


def test_offset_that_becomes_negative_stops_at_its_read(tmp_path, native_env):
    source = HEADER + (
        "lag = bar_index == 10 ? -2 : 0\nobserved = close[lag]\n"
        'log.info("value {0}", observed)\n'
    )
    _assert_negative_stop(tmp_path, native_env, source, -2)


def _assert_logs_match(tmp_path, native_env, body):
    source = HEADER + dedent(body).lstrip() + (
        'log.info("reference {0} {1}", bar_index, reference)\n'
        'log.info("observed {0} {1}", bar_index, observed)\n'
    )
    stderr = _run_native(tmp_path, native_env, source)
    count = native_env[2]
    assert _messages(stderr, "observed", count) == _messages(stderr, "reference", count)


@pytest.mark.parametrize("a, b, reference", [
    (30, 30, "close"),
    (31, 30, "close[1]"),
    (300, 0, "float(na)"),
    (10000, 0, "float(na)"),
])
def test_nonnegative_runtime_offsets_match_reference(tmp_path, native_env, a, b, reference):
    _assert_logs_match(tmp_path, native_env, (
        f'a = input.int({a}, "a")\nb = input.int({b}, "b")\n'
        f"reference = {reference}\nobserved = close[a - b]\n"
    ))


def test_constant_offset_keeps_running(tmp_path, native_env):
    _assert_logs_match(tmp_path, native_env, """
        var float previous = na
        reference = previous
        observed = close[1]
        previous := close
    """)


@pytest.mark.parametrize("declarations, index", [
    ("", "na"),
    ("var int lag = na\n", "lag"),
    ("var int lag = time_close\nlag := na\n", "lag"),
])
def test_missing_offsets_keep_missing_history(tmp_path, native_env, declarations, index):
    _assert_logs_match(tmp_path, native_env, (
        declarations + f"observed = close[{index}]\nreference = float(na)\n"
    ))


@pytest.mark.parametrize("body", [
    "value = close + open\nreference = value[1]\nobserved = value[lag]\n",
    "reference = ta.sma(close, 2)[1]\nobserved = ta.sma(close, 2)[lag]\n",
    "reference = ta.highest(high, 3)[1]\nobserved = ta.highest(high, 3)[lag]\n",
    "reference = session.ismarket[1]\nobserved = session.ismarket[lag]\n",
    (
        "values = array.from(close)\nfloat total = 0.0\n"
        "for value in values[lag]\n    total += value\n"
        "reference = close\nobserved = total\n"
    ),
    (
        "read(float src, int back) => src[back]\n"
        "reference = close[1]\nobserved = read(close, lag)\n"
    ),
    (
        "values = array.from(close)\n"
        "reference = close[1]\n"
        "observed = na(values[lag]) ? na : (values[lag]).get(0)\n"
    ),
    (
        "values = matrix.new<float>(1, 1, close)\n"
        "reference = close[1]\n"
        "observed = na(values[lag]) ? na : (values[lag]).get(0, 0)\n"
    ),
    (
        'reference = request.security(syminfo.tickerid, "15", close[1])\n'
        'observed = request.security(syminfo.tickerid, "15", close[lag])\n'
    ),
    (
        'reference = request.security(syminfo.tickerid, "15", ta.sma(close, 2)[1])\n'
        'observed = request.security(syminfo.tickerid, "15", ta.sma(close, 2)[lag])\n'
    ),
    (
        "read(int back) =>\n    value = close + open\n    value[back]\n"
        'reference = request.security(syminfo.tickerid, "15", read(1))\n'
        'observed = request.security(syminfo.tickerid, "15", read(lag))\n'
    ),
])
def test_nonnegative_readers_keep_their_history(tmp_path, native_env, body):
    _assert_logs_match(tmp_path, native_env, 'lag = input.int(1, "Lag")\n' + body)


def test_zero_collection_offset_keeps_the_current_array(tmp_path, native_env):
    _assert_logs_match(tmp_path, native_env, """
        lag = input.int(0, "Lag")
        values = array.from(close)
        (values[lag]).push(open)
        reference = 2
        observed = values.size()
    """)


def test_offset_with_side_effects_runs_once_per_read(tmp_path, native_env):
    _assert_logs_match(tmp_path, native_env, """
        var calls = array.new<int>(1, 0)
        next_offset(array<int> counter) =>
            counter.set(0, counter.get(0) + 1)
            0
        value = close[next_offset(calls)]
        reference = str.tostring(bar_index + 1) + "|" + str.tostring(close)
        observed = str.tostring(calls.get(0)) + "|" + str.tostring(value)
    """)


def test_var_history_initializer_evaluates_its_offset_once(tmp_path, native_env):
    _assert_logs_match(tmp_path, native_env, """
        var calls = array.new<int>(1, 0)
        next_offset(array<int> counter) =>
            counter.set(0, counter.get(0) + 1)
            0
        var float first = close
        var float value = close[next_offset(calls)]
        reference = "1|" + str.tostring(first)
        observed = str.tostring(calls.get(0)) + "|" + str.tostring(value)
    """)


def test_unselected_history_offsets_are_not_evaluated(tmp_path, native_env):
    _assert_logs_match(tmp_path, native_env, """
        enabled = input.bool(false, "Enabled")
        lag = input.int(-1, "Lag")
        var calls = array.new<int>(1, 0)
        next_offset(array<int> counter, int back) =>
            counter.set(0, counter.get(0) + 1)
            back
        left = enabled and close[next_offset(calls, lag)] > 0
        right = not enabled or close[next_offset(calls, lag)] > 0
        chosen = enabled ? close[next_offset(calls, lag)] : close
        reference = "0|false|true|" + str.tostring(close)
        observed = str.tostring(calls.get(0)) + "|" + str.tostring(left) + "|" + str.tostring(right) + "|" + str.tostring(chosen)
    """)


UNCHANGED_SOURCES = {
    "no_history": (
        "value = ta.sma(close, 3)\n"
        'if close > value\n    strategy.entry("p", strategy.long)\n'
    ),
    "bar_constants": "observed = close[1] + open[2]\nplot(observed)\n",
    "constant_expression": (
        "const int back = 2\n"
        "observed = close[back + 1] + close[int(4 / 2)]\nplot(observed)\n"
    ),
    "user_series_constant": (
        "value = close + open\nobserved = value[1]\nplot(observed)\n"
    ),
    "ta_constants": (
        "observed = ta.sma(close, 2)[1] + ta.highest(high, 3)[2]\nplot(observed)\n"
    ),
    "function_constant": (
        "read(float src) => src[1]\nobserved = read(close)\nplot(observed)\n"
    ),
    "session_constant": "observed = session.ismarket[1]\nplot(observed ? 1 : 0)\n",
    "collection_constant": (
        "values = array.from(close)\n"
        "observed = na(values[1]) ? na : (values[1]).get(0)\nplot(observed)\n"
    ),
    "requested_constant": (
        'observed = request.security(syminfo.tickerid, "60", close[1])\nplot(observed)\n'
    ),
    "missing_constant": "observed = close[na]\nplot(observed)\n",
    "ordinary_array_index": (
        'lag = input.int(0, "Lag")\n'
        "values = array.from(close)\nobserved = values.get(lag)\nplot(observed)\n"
    ),
    "discarded_iteration_offset": (
        'lag = input.int(1, "Lag")\n'
        "values = array.from(close)\nfloat total = 0.0\n"
        "for value in values[lag]\n    total += value\nplot(total)\n"
    ),
    "discarded_plot_argument": (
        'lag = input.int(-1, "Lag")\nplot(close[lag])\n'
    ),
}


@pytest.mark.parametrize("shape", tuple(UNCHANGED_SOURCES))
def test_other_scripts_keep_complete_reference_cpp(tmp_path, shape):
    source = HEADER + UNCHANGED_SOURCES[shape]
    # 493fde6 adds checkpointed per-bar markers to the two inline TA histories.
    revision = (
        "493fde62b2aa54a052f45c23ed0c8f24f91c96d7"
        if shape == "ta_constants" else CPP_REFERENCE
    )
    reference = reference_codegen(revision)
    assert reference is not None, "required C++ reference is unavailable"
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before.get("diagnostics")
    cpp = transpile(source)
    assert cpp == before["cpp"]
