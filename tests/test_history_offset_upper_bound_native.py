"""Evaluated history offsets above 10000 stop before narrowing."""

from decimal import Decimal
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.collection_history import NA_ARRAY_MESSAGE, NA_MATRIX_MESSAGE
from tests.test_bool_string_inference import _messages, _run_native, native_env
from tests.test_negative_history_offset_native import (
    HEADER, NEGATIVE_READS, OFFSET_MESSAGE, _assert_logs_match,
)


def _inputs(offset):
    return f'a = input.int({offset}, "a")\nb = input.int(0, "b")\n'


def _reader_source(shape, offset):
    body = NEGATIVE_READS[shape]
    if "observed" in body:
        body += 'log.info("value {0}", observed)\n'
    return HEADER + _inputs(offset) + body


def _offset_line(source):
    # A composed argument can contain both src[lag] and close[a - b].
    # The latter is the authored offset that exceeds the limit in that row.
    marker = "[a - b]" if "[a - b]" in source else "[lag]"
    return next(
        line for line, text in enumerate(source.splitlines(), 1)
        if marker in text
    )


def _assert_upper_stop(tmp_path, native_env, source, offset):
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
    line = _offset_line(source)
    assert int(located[1]) == line, message
    assert (
        f"History reference at line {line}: offset {located[2]}. {OFFSET_MESSAGE}"
    ) in message


@pytest.mark.parametrize("shape", tuple(NEGATIVE_READS))
def test_each_evaluated_history_reader_stops_above_the_limit(tmp_path, native_env, shape):
    _assert_upper_stop(tmp_path, native_env, _reader_source(shape, 10001), 10001)


# These consumers already stop on missing history in the short native feed.
# Their boundary rows must reach that existing error, rather than an offset stop.
MISSING_REFERENCE_ERRORS = {
    "array_read": NA_ARRAY_MESSAGE,
    "array_change": NA_ARRAY_MESSAGE,
    "matrix_read": NA_MATRIX_MESSAGE,
    "object_reference": "UDT access on na",
    "field_reference": "UDT access on na",
}


@pytest.mark.parametrize(
    "shape", tuple(shape for shape in NEGATIVE_READS if shape not in MISSING_REFERENCE_ERRORS),
)
def test_each_history_reader_keeps_running_at_the_limit(tmp_path, native_env, shape):
    source = _reader_source(shape, 10000) + 'log.info("completed {0} ok", bar_index)\n'
    stderr = _run_native(tmp_path, native_env, source)
    assert _messages(stderr, "completed", native_env[2]) == {
        bar: "ok" for bar in range(native_env[2])
    }


@pytest.mark.parametrize("shape", tuple(MISSING_REFERENCE_ERRORS))
def test_boundary_reference_reads_keep_their_existing_missing_error(tmp_path, native_env, shape):
    with pytest.raises(RuntimeError, match="run_strategy failed") as failure:
        _run_native(tmp_path, native_env, _reader_source(shape, 10000))
    message = str(failure.value)
    assert MISSING_REFERENCE_ERRORS[shape] in message
    assert OFFSET_MESSAGE not in message


@pytest.mark.parametrize("body", [
    "values = array.from(close)\nobserved = na(values[a - b])\n",
    (
        "values = array.from(close)\nprevious = values[a - b]\n"
        "observed = na(previous)\n"
    ),
    "values = matrix.new<float>(1, 1, close)\nobserved = na(values[a - b])\n",
    "type Cell\n    float value\ncell = Cell.new(close)\nobserved = na(cell[a - b])\n",
    (
        "type Cell\n    float value\ntype Holder\n    Cell child\n"
        "holder = Holder.new(Cell.new(close))\nobserved = na(holder.child[a - b])\n"
    ),
])
def test_boundary_reference_ids_keep_running_as_missing(tmp_path, native_env, body):
    _assert_logs_match(
        tmp_path, native_env, _inputs(10000) + body + "reference = true\n",
    )


@pytest.mark.parametrize("shape", ["requested_close", "requested_ta", "requested_source"])
@pytest.mark.parametrize("offset", [10001, 10000])
def test_requested_input_getters_check_the_limit(tmp_path, native_env, shape, offset):
    body = (
        f'lag = input.int({offset}, "Lag")\n'
        + NEGATIVE_READS[shape].replace("[a - b]", "[lag]")
    )
    if offset == 10001:
        source = HEADER + body + 'log.info("value {0}", observed)\n'
        _assert_upper_stop(tmp_path, native_env, source, offset)
    else:
        _assert_logs_match(tmp_path, native_env, body + "reference = float(na)\n")


@pytest.mark.parametrize("offset", [4294967296, 1099511627776])
def test_wide_offsets_are_checked_before_narrowing(tmp_path, native_env, offset):
    source = HEADER + (
        f"var int lag = time_close\nlag := {offset}\n"
        "observed = close[lag]\n"
        'log.info("value {0}", observed)\n'
    )
    _assert_upper_stop(tmp_path, native_env, source, offset)


def test_fraction_above_the_limit_is_checked_before_truncation(tmp_path, native_env):
    source = HEADER + (
        'lag = input.float(10000.5, "Lag")\n'
        "observed = close[lag]\n"
        'log.info("value {0}", observed)\n'
    )
    _assert_upper_stop(tmp_path, native_env, source, 10000.5)


def test_integral_float_at_the_limit_keeps_missing_history(tmp_path, native_env):
    _assert_logs_match(tmp_path, native_env, (
        'lag = input.float(10000.0, "Lag")\n'
        "reference = float(na)\nobserved = close[lag]\n"
    ))


@pytest.mark.parametrize("offset, reference", [
    (0, "close"),
    (9999, "float(na)"),
    (10000, "float(na)"),
])
def test_in_range_offsets_keep_their_values(tmp_path, native_env, offset, reference):
    _assert_logs_match(tmp_path, native_env, (
        f'lag = input.int({offset}, "Lag")\n'
        f"reference = {reference}\nobserved = close[lag]\n"
    ))


def test_offset_that_becomes_too_large_stops_at_its_read(tmp_path, native_env):
    source = HEADER + (
        "lag = bar_index == 10 ? 10001 : 10000\nobserved = close[lag]\n"
        'log.info("value {0}", observed)\n'
    )
    _assert_upper_stop(tmp_path, native_env, source, 10001)


def test_constant_offset_above_the_limit_keeps_translation():
    source = HEADER + "observed = close[10001]\n" + 'log.info("value {0}", observed)\n'
    cpp = transpile(source)
    # Integer NumberLiteral offsets reach the bar-series subscript unchanged.
    assert "_s_close[10001]" in cpp
    assert "History reference at line " not in cpp


def test_constant_offset_above_the_limit_keeps_missing_history(tmp_path, native_env):
    _assert_logs_match(
        tmp_path, native_env, "reference = float(na)\nobserved = close[10001]\n",
    )
