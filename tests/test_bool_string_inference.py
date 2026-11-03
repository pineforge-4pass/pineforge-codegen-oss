"""Pine reference: bools print true/false, including through legacy storage.

Every text row compiles and runs against the paired runtime. Its oracle is a
Pine ``if`` which prints a literal, independently of the text conversion and
its transport. These are Pine reference rows, not newly measured TV tapes.
Complete-C++ compatibility controls deliberately retain their frozen reference.
"""

from __future__ import annotations

import datetime as dt

import pytest

from pineforge_codegen import signatures, transpile
from pineforge_codegen.errors import CompileError
from pineforge_codegen.symbols import PineType
from tests._e2e import (
    build_strategy_library, reference_codegen, run_strategy,
    skip_unless_e2e_env, transpile_json,
)
from tests._pine_libraries import Layout


HEADER = '//@version=6\nstrategy("Bool text", overlay=true)\n'
NUMERIC_REFERENCE = "883221a00585e16a8e9802b4c16a7c85bf8fa4b8"
TOSTRING_CALLS = (
    "str.tostring({value})",
    "str.tostring(value = {value})",
    'str.tostring({value}, "#.##")',
    'str.tostring(format = "#.##", value = {value})',
)


def _source(body: str, expression: str) -> str:
    return (HEADER + body + f"text = {expression}\n"
            'strategy.entry("L", strategy.long, comment = text)\n')


@pytest.fixture(scope="module")
def native_env(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("bool_text_feed")
    # The existing native session probe's two US trading days, including
    # pre/post-market. Vary close independently so TA/transport rows see both
    # bool values and warm-up; no expected text comes from generated C++.
    stamps = [
        int(dt.datetime(2025, 1, day, 14, tzinfo=dt.timezone.utc).timestamp() * 1000)
        + step * 900_000
        for day in (6, 7) for step in range(30)
    ]
    closes = (100.5, 99.5, 101.5, 98.5, 100.0, 102.0, 97.0)
    feed = base / "chart.csv"
    feed.write_text(
        "timestamp,open,high,low,close,volume\n"
        + "".join(f"{stamp},100,103,96,{closes[index % len(closes)]},1\n"
                  for index, stamp in enumerate(stamps)),
        encoding="utf-8",
    )
    aux = base / "minutes.csv"
    aux.write_text(
        "timestamp,open,high,low,close,volume\n"
        + "".join(f"{stamp + minute * 60_000},100,103,96,"
                  f"{closes[index % len(closes)]},1\n"
                  for index, stamp in enumerate(stamps) for minute in range(15)),
        encoding="utf-8",
    )
    return engine, feed, len(stamps), aux


@pytest.fixture(scope="module")
def hourly_native_env(tmp_path_factory, native_env):
    engine, feed, count, aux = native_env
    rows = feed.read_text(encoding="utf-8").splitlines()
    start = int(rows[1].split(",", 1)[0])
    hourly_dir = tmp_path_factory.mktemp("bool_hourly_feed")
    hourly_feed = hourly_dir / "chart.csv"
    hourly_feed.write_text(
        rows[0] + "\n"
        + "".join(f"{start + index * 3_600_000},{row.split(',', 1)[1]}\n"
                  for index, row in enumerate(rows[1:])),
        encoding="utf-8",
    )
    # The auxiliary request.security feed must map onto the hourly chart's own
    # bars: sixty one-minute bars start at every hourly stamp (the shared feed's
    # auxiliary minutes belong to the original fifteen-minute stamps).
    hourly_aux = hourly_dir / "minutes.csv"
    hourly_aux.write_text(
        rows[0] + "\n"
        + "".join(f"{start + index * 3_600_000 + minute * 60_000},"
                  f"{row.split(',', 1)[1]}\n"
                  for index, row in enumerate(rows[1:]) for minute in range(60)),
        encoding="utf-8",
    )
    return engine, hourly_feed, count, hourly_aux


def _run_native(tmp_path, native_env, source, *, extra_flags=(), timeframe="15"):
    engine, feed, _count, aux = native_env
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    translated = transpile_json(pine)
    assert translated["ok"], translated.get("diagnostics")
    # This is also the compile check for EVERY registry/shape/keyword row.
    build_strategy_library(translated["cpp"], tmp_path, extra_flags=extra_flags)
    _, _records, stderr = run_strategy(
        engine, tmp_path, feed,
        {"input_tf": timeframe, "script_tf": timeframe,
         "aux_security_ohlcv_csv": str(aux), "aux_security_input_tf": "1",
         "runtime_overrides": {"session": "0930-1600",
                               "timezone": "America/New_York"}},
        "bool_text",
    )
    return stderr


def _messages(stderr, tag, count):
    prefix = f"[INFO] {tag} "
    rows = [line[len(prefix):].split(" ", 1) for line in stderr.splitlines()
            if line.startswith(prefix)]
    assert len(rows) == count, stderr
    assert all(len(row) == 2 for row in rows), rows
    result = {int(index): message for index, message in rows}
    assert len(result) == count, rows
    return result


def _reference_log(condition):
    # Same authored condition, no tostring, formatter or transported slot.
    return (f"if {condition}\n"
            '    log.info("Pine reference {0} true", bar_index)\n'
            "else\n"
            '    log.info("Pine reference {0} false", bar_index)\n')


def _assert_native_text(tmp_path, native_env, body, expression, condition,
                        *, history=0, once=False, prefix="", sink="",
                        extra_flags=()):
    """Observe every bar's text and its actual filled entry comment."""
    source = (
        HEADER + body + f"text = {expression}\n" + sink
        + _reference_log(condition)
        + 'log.info("observed {0} {1}", bar_index, text)\n'
        + 'if bar_index > 0\n'
        + '    log.info("comment {0} {1}", bar_index - 1, '
          'strategy.opentrades.entry_comment(0))\n'
        + 'strategy.entry("probe", bar_index % 2 == 0 ? strategy.long : '
          'strategy.short, qty = 1, comment = text)\n'
    )
    stderr = _run_native(tmp_path, native_env, source, extra_flags=extra_flags)
    count = native_env[2]
    reference = _messages(stderr, "Pine reference", count)
    assert set(reference.values()) <= {"true", "false"}, reference
    expected = {
        bar: prefix + (reference[0] if once else
                       reference[bar - history] if bar >= history else "false")
        for bar in range(count)
    }
    assert _messages(stderr, "observed", count) == expected, "Pine reference"
    assert _messages(stderr, "comment", count - 1) == {
        bar: expected[bar] for bar in range(count - 1)
    }, "Pine reference: the filled entry must carry the converted text"
    return stderr, expected


@pytest.mark.parametrize("call", TOSTRING_CALLS)
@pytest.mark.parametrize("value", (
    "session.isfirstbar_regular", "session.islastbar_regular",
    "session.isfirstbar", "barstate.islast",
), ids=lambda value: f"Pine reference-{value}")
def test_measured_builtin_bools_use_text_conversion(tmp_path, native_env, value, call):
    _assert_native_text(tmp_path, native_env, "", call.format(value=value), value)


@pytest.mark.parametrize("call", TOSTRING_CALLS)
@pytest.mark.parametrize("expression", (
    "ta.crossover(close, ta.sma(close, 5))",
    "ta.crossunder(close, ta.sma(close, 5))",
    "ta.cross(close, ta.sma(close, 5))",
    "ta.rising(close, 5)",
    "ta.falling(close, 5)",
))
def test_ta_bool_variable_uses_text_conversion(tmp_path, native_env, expression, call):
    _assert_native_text(
        tmp_path, native_env, f"ok = {expression}\n",
        call.format(value="ok"), expression,
    )


BUILTIN_BOOLS = sorted({
    name
    for table in (
        signatures.BUILTIN_VARIABLES, signatures.STRATEGY_VARIABLES,
        signatures.BARSTATE_VARIABLES, signatures.SYMINFO_VARIABLES,
        signatures.TIMEFRAME_VARIABLES, signatures.DISPLAY_VARIABLES,
    )
    for name, pine_type in table.items()
    if "." in name and pine_type == PineType.BOOL
})
TIMEFRAME_BOOLS = sorted(
    name for name, pine_type in signatures.TIMEFRAME_VARIABLES.items()
    if pine_type == PineType.BOOL
)


def _bool_calls():
    """Read every signature dictionary, including payload-dependent overloads."""
    calls = {}
    for table in vars(signatures).values():
        if not isinstance(table, dict):
            continue
        for function in table.values():
            if not isinstance(function, signatures.IntrinsicFunc):
                continue
            for signature in function.signatures:
                if signature.return_type == PineType.BOOL:
                    calls[function.name] = signature
                    break
    return calls


BOOL_CALLS = _bool_calls()
BOOL_SOURCE_EXCEPTIONS: dict[str, str] = {}


@pytest.mark.parametrize("table_name", (
    "ARRAY_FUNCTIONS", "MATRIX_FUNCTIONS", "REQUEST_VALUE_FUNCTIONS",
))
def test_value_registry_declares_collection_and_request_predicates(
        tmp_path, native_env, table_name):
    table = getattr(signatures, table_name, None)
    assert isinstance(table, dict) and table, f"missing predicate registry: {table_name}"
    assert any(signature.return_type == PineType.BOOL
               for function in table.values() for signature in function.signatures)
    name = next(function.name for function in table.values()
                if any(signature.return_type == PineType.BOOL
                       for signature in function.signatures))
    preamble, value = _builtin_source(name)
    _assert_native_text(tmp_path, native_env, preamble, f"str.tostring({value})", value)


def _builtin_source(name):
    if name in BUILTIN_BOOLS:
        return "", name
    signature = BOOL_CALLS[name]
    namespace = name.partition(".")[0]
    preamble = {
        "array": "collection = array.from(1.0, 2.0)\n",
        "matrix": "collection = matrix.new<float>(2, 2, 1.0)\n",
        "map": "collection = map.new<string, float>()\n",
    }.get(namespace, "")
    values = []
    for parameter in signature.params:
        if name == "input.bool" and values:
            # All trailing input metadata is optional in Pine; the registry
            # uses None for some optional defaults as well as required slots.
            break
        if parameter.default is not None:
            continue
        if parameter.name == "id":
            assert preamble, (name, parameter.name)
            value = "collection"
        elif parameter.name == "symbol":
            value = "syminfo.tickerid"
        elif parameter.name == "timeframe":
            value = '"60"'
        else:
            value = {
                PineType.BOOL: "close > open",
                PineType.INT: "0" if parameter.name == "occurrence" else "2",
                PineType.FLOAT: "close",
                PineType.STRING: '"ab"',
            }[parameter.pine_type]
            if namespace == "input" or name == "input":
                value = "true" if parameter.pine_type == PineType.BOOL else value
        values.append(value)
    return preamble, f"{name}({', '.join(values)})"


@pytest.mark.parametrize("name", sorted(set(BUILTIN_BOOLS) | set(BOOL_CALLS)),
                         ids=lambda name: f"Pine reference-{name}")
@pytest.mark.parametrize("shape,call", [
    (shape, call)
    for shape in (
        "direct", "stored", "parameter", "array", "history", "local", "return",
    )
    for call in (TOSTRING_CALLS if shape in ("stored", "local", "parameter")
                 else TOSTRING_CALLS[:1])
])
def test_every_registry_bool_has_bool_text_in_each_shape(
        tmp_path, native_env, name, shape, call):
    assert name not in BOOL_SOURCE_EXCEPTIONS, BOOL_SOURCE_EXCEPTIONS.get(name)
    preamble, value = _builtin_source(name)
    condition = value
    if shape == "parameter":
        preamble = f"render(p) => {call.format(value='p')}\n" + preamble
        expression = f"render({value})"
    elif shape == "local":
        preamble += (f"render() =>\n    flag = {value}\n"
                     + f"    {call.format(value='flag')}\n")
        expression = "render()"
    else:
        if shape in ("stored", "history"):
            preamble += f"flag = {value}\n"
            value = "flag[1]" if shape == "history" else "flag"
        elif shape == "array":
            preamble += f"flags = array.from({value})\n"
            value = "array.get(flags, 0)"
        elif shape == "return":
            preamble += f"predicate() => {value}\n"
            value = "predicate()"
        expression = call.format(value=value)
    _assert_native_text(
        tmp_path, native_env, preamble, expression, condition,
        history=1 if shape == "history" else 0,
    )


@pytest.mark.parametrize("body,value,condition,history", (
    ("flags = array.from(session.isfirstbar)\n", "flags.get(0)", "session.isfirstbar", 0),
    ("flags = array.from(ta.crossover(close, open))\n", "flags.get(0)",
     "ta.crossover(close, open)", 0),
    ("a = array.from(1, 2)\n", "array.includes(a, 1)", "array.includes(a, 1)", 0),
    ("a = array.from(1, 2)\n", "a.includes(1)", "array.includes(a, 1)", 0),
    ("a = array.from(1, 2)\nfound = array.includes(a, 1)\n", "found[1]",
     "array.includes(a, 1)", 1),
    ('rb = request.security(syminfo.tickerid, "60", close > open)\n', "rb",
     'request.security(syminfo.tickerid, "60", close > open)', 0),
    ('flags = request.security_lower_tf(syminfo.tickerid, "1", close > open)\n',
     "array.get(flags, 0)", "close > open", 0),
    ("", "ta.change(close > open)", "ta.change(close > open)", 0),
    ("", "ta.valuewhen(close > open, close < open, 0)",
     "ta.valuewhen(close > open, close < open, 0)", 0),
    ("f(p) => str.tostring(p)\n", "f(session.isfirstbar)", "session.isfirstbar", 0),
    ("f(p) => str.tostring(p)\n", "f(ta.crossover(close, open))",
     "ta.crossover(close, open)", 0),
    ("f(p) => str.tostring(p)\n", "f(barstate.isconfirmed)", "barstate.isconfirmed", 0),
    ("f(p) => str.tostring(p)\n", "f(close > open)", "close > open", 0),
))
def test_measured_bool_transport_paths(
        tmp_path, native_env, body, value, condition, history):
    expression = value if value.startswith("f(") else f"str.tostring({value})"
    _assert_native_text(
        tmp_path, native_env, body, expression, condition, history=history,
    )


@pytest.mark.parametrize("body,value,condition,history,once", (
    ("", "true", "true", 0, False),
    ("var bool flag = true\n", "flag", "true", 0, False),
    ("flag = close > open\n", "flag", "close > open", 0, False),
    ("flag = close > open\n", "flag[1]", "close > open", 1, False),
    ("var bool flag = false\nflag := close > open\n", "flag", "close > open", 0, False),
    ("is_up() => close > open\n", "is_up()", "close > open", 0, False),
    ("", "close > open ? true : false", "close > open", 0, False),
    ("flags = array.new<bool>(1, true)\n", "array.get(flags, 0)", "true", 0, False),
    ("flags = array.new<bool>(1, true)\n", "flags.get(0)", "true", 0, False),
    ("ok = ta.crossover(close, open)\nalias = ok\n", "alias",
     "ta.crossover(close, open)", 0, False),
    ("ok = ta.crossover(close, open)\n", "ok[1]", "ta.crossover(close, open)", 1, False),
    ("var ok = ta.crossover(close, open)\n", "ok", "ta.crossover(close, open)", 0, True),
    ("flag = session.isfirstbar_regular\n", "flag[1]", "session.isfirstbar_regular", 1, False),
    ("render() =>\n    ok = ta.crossover(close, open)\n    str.tostring(ok)\n",
     None, "ta.crossover(close, open)", 0, False),
    ("", "session.isfirstbar_regular[1]", "session.isfirstbar_regular", 1, False),
))
def test_bool_values_keep_their_type_through_text_conversion(
        tmp_path, native_env, body, value, condition, history, once):
    expression = f"str.tostring({value})" if value is not None else "render()"
    _assert_native_text(
        tmp_path, native_env, body, expression, condition,
        history=history, once=once,
    )


@pytest.mark.parametrize("value", TIMEFRAME_BOOLS,
                         ids=lambda value: f"Pine reference-stored-parameter-{value}")
def test_stored_compat_bool_can_cross_an_untyped_parameter(tmp_path, native_env, value):
    _assert_native_text(
        tmp_path, native_env,
        "render(p) => str.tostring(p)\n" + f"held = {value}\n",
        "render(held)", value,
    )


@pytest.mark.parametrize("seed", TIMEFRAME_BOOLS,
                         ids=lambda seed: f"Pine reference-legacy-{seed}")
def test_legacy_string_parameter_receives_na_predicate_safely(
        tmp_path, native_env, seed):
    # Regression for predicates that historically inferred a STRING parameter.
    # Both calls pass Pine bools; the TA slot's first value is numeric NaN.
    # Sanitization makes the old NaN->char UB a runtime failure on every host;
    # this test never asserts the implementation's cast or helper spelling.
    _, expected = _assert_native_text(
        tmp_path, native_env,
        "render(p) => str.tostring(p)\n"
        + f"seed = render({seed})\n"
        + "ok = ta.change(close > open)\n",
        "render(ok)", "ta.change(close > open)",
        extra_flags=("-fsanitize=float-cast-overflow",
                     "-fno-sanitize-recover=float-cast-overflow"),
    )
    assert expected[0] == "false", "Pine reference: no previous bool on bar zero"


@pytest.mark.parametrize("value", TIMEFRAME_BOOLS,
                         ids=lambda value: f"Pine reference-once-{value}")
def test_legacy_bool_helper_result_is_evaluated_once(tmp_path, native_env, value):
    _assert_native_text(
        tmp_path, native_env,
        "calls = array.new<int>(1, 0)\n"
        + "predicate() =>\n"
        + "    calls.set(0, calls.get(0) + 1)\n"
        + f"    {value}\n"
        + "rendered = str.tostring(predicate())\n"
        + "if calls.get(0) != 1\n"
        + '    runtime.error("text evaluated the predicate more than once")\n',
        "rendered", value,
    )


@pytest.mark.parametrize("value,shape", [
    pytest.param(value, shape, id=f"{shape}-{value}")
    for value in TIMEFRAME_BOOLS for shape in ("parameter", "history")
])
def test_native_timeframe_bool_values_match_pine_reference(
        tmp_path, hourly_native_env, value, shape):
    """Pine reference: transported predicates select and fill the same quantity."""
    if shape == "parameter":
        body = "q(p) => p ? 2 : 1\n" + f"qty = q({value})\n"
    else:
        body = f"flag = {value}\nqty = flag[1] ? 2 : 1\n"
    source = (
        '//@version=6\n'
        'strategy("t1", initial_capital = 1000000, currency = currency.NONE)\n'
        + body
        + "if bar_index > 6 and bar_index % 7 == 3\n"
        + '    strategy.entry("L", strategy.long, qty = qty)\n'
        + "if bar_index > 6 and bar_index % 7 == 5\n"
        + '    strategy.close("L")\n'
        + f"if {value}\n"
        + '    log.info("Pine reference {0} 2", bar_index)\n'
        + "else\n"
        + '    log.info("Pine reference {0} 1", bar_index)\n'
        + 'log.info("observed {0} {1}", bar_index, qty)\n'
        + "if bar_index > 6 and bar_index % 7 == 4\n"
        + '    log.info("filled {0} {1}", bar_index - 1, strategy.position_size)\n'
    )
    stderr = _run_native(tmp_path, hourly_native_env, source, timeframe="60")
    count = hourly_native_env[2]
    reference = _messages(stderr, "Pine reference", count)
    assert set(reference.values()) <= {"1", "2"}, reference
    history = 1 if shape == "history" else 0
    expected = {
        bar: float(reference[bar - history]) if bar >= history else 1.0
        for bar in range(count)
    }
    assert {
        bar: float(message)
        for bar, message in _messages(stderr, "observed", count).items()
    } == expected, "Pine reference: the transported predicate selects the quantity"
    entries = {
        bar: expected[bar] for bar in range(7, count - 1) if bar % 7 == 3
    }
    assert entries, "the chart must include a filled entry"
    assert {
        bar: float(message)
        for bar, message in _messages(stderr, "filled", len(entries)).items()
    } == entries, "Pine reference: the filled position carries the quantity"


@pytest.mark.parametrize("body,expression", (
    ("", "str.tostring(1.25)"),
    ("", "str.tostring(7)"),
    ("", "str.tostring(close)"),
    ("var float held = 1.25\n", "str.tostring(held)"),
    ("", "str.tostring(bar_index)"),
    ("", 'str.tostring(close, "#.00")'),
    ("", 'str.tostring(format = "#.00", value = close)'),
    ("", "str.tostring(syminfo.mintick)"),
    ("", "str.tostring(close, format.mintick)"),
    ("", "str.tostring(1.25, format.percent)"),
    ("", "str.tostring(1250, format.volume)"),
    ("average = ta.sma(close, 5)\n", "str.tostring(average)"),
    ("average = ta.sma(close, 5)\n", "str.tostring(value = average)"),
    ("average = ta.sma(close, 5)\n",
     'str.tostring(format = "#.##", value = average)'),
    ("elapsed = ta.barssince(close > open)\n", "str.tostring(elapsed)"),
    ("elapsed = ta.barssince(close > open)\n", "str.tostring(value = elapsed)"),
    ("elapsed = ta.barssince(close > open)\n",
     'str.tostring(format = "#.##", value = elapsed)'),
    ("price = close\n", "str.tostring(price[1])"),
    ("", "str.tostring(ta.sma(close, 5)[1])"),
))
def test_numeric_tostring_keeps_complete_reference_cpp(tmp_path, body, expression):
    reference = reference_codegen(NUMERIC_REFERENCE)
    assert reference is not None, "required numeric C++ reference is unavailable"
    pine = tmp_path / "strategy.pine"
    pine.write_text(_source(body, expression), encoding="utf-8")
    current = transpile_json(pine)
    previous = transpile_json(pine, reference)
    assert current["ok"], current.get("diagnostics")
    assert previous["ok"], previous.get("diagnostics")
    assert current["cpp"] == previous["cpp"]


@pytest.mark.parametrize("initializer", (
    "close > open", "ta.crossover(close, open)", "session.isfirstbar_regular",
))
def test_str_format_keeps_complete_reference_cpp(tmp_path, initializer):
    _assert_reference_cpp(tmp_path, _source(
        f"flag = {initializer}\n",
        'str.format("{0} {1}", flag, session.isfirstbar_regular)',
    ))


@pytest.mark.parametrize("consumer", (
    "entry", "close", "close_all", "exit",
    "label.new", "label.set_text", "label.set_tooltip",
    "log.info", "log.warning", "log.error",
), ids=lambda consumer: f"Pine reference-{consumer}")
def test_string_consumers_keep_explicit_bool_conversion(
        tmp_path, native_env, consumer):
    condition = "session.isfirstbar_regular"
    conversion = f"str.tostring({condition})"
    if consumer == "entry":
        _assert_native_text(
            tmp_path, native_env, "", conversion, condition,
        )
    elif consumer in ("close", "close_all", "exit"):
        command = {
            "close": f'strategy.close("L", comment = {conversion})',
            "close_all": f"strategy.close_all(comment = {conversion})",
            # All test prices exceed one: a sell limit at one fills on the
            # next bar, so this checks delivered exit text, not order spelling.
            "exit": f'strategy.exit("X", "L", limit = 1, comment = {conversion})',
        }[consumer]
        source = (
            HEADER + _reference_log(condition)
            + "if bar_index % 2 == 0\n"
            + "    if bar_index > 0\n"
            + '        log.info("delivered {0} {1}", bar_index - 1, '
              'strategy.closedtrades.exit_comment(strategy.closedtrades - 1))\n'
            + '    strategy.entry("L", strategy.long, qty = 1)\n'
            + f"else\n    {command}\n"
        )
        stderr = _run_native(tmp_path, native_env, source)
        count = native_env[2]
        reference = _messages(stderr, "Pine reference", count)
        expected = {bar: reference[bar] for bar in range(1, count - 1, 2)}
        assert _messages(stderr, "delivered", len(expected)) == expected, "Pine reference"
    elif consumer.startswith("log."):
        stderr, expected = _assert_native_text(
            tmp_path, native_env, "", conversion, condition,
            sink=f"{consumer}(text)\n",
        )
        level = {"log.info": "INFO", "log.warning": "WARN", "log.error": "ERROR"}[consumer]
        actual = [line for line in stderr.splitlines()
                  if line in (f"[{level}] true", f"[{level}] false")]
        assert actual == [f"[{level}] {value}" for value in expected.values()], "Pine reference"
    else:
        if consumer == "label.new":
            body = f"mark = label.new(bar_index, close, text = {conversion})\n"
            expression = "label.get_text(mark)"
        elif consumer == "label.set_text":
            body = ("var mark = label.new(0, close)\n"
                    + f"label.set_text(mark, {conversion})\n")
            expression = "label.get_text(mark)"
        else:
            # Pine has no tooltip getter. Observe the evaluated argument via a
            # helper's array write, without claiming the inert tooltip is stored.
            body = (
                'received = array.new<string>(1, "")\n'
                + "message() =>\n"
                + f"    converted = {conversion}\n"
                + "    received.set(0, converted)\n    converted\n"
                + "var mark = label.new(0, close)\n"
                + "label.set_tooltip(mark, message())\n"
            )
            expression = "received.get(0)"
        _assert_native_text(tmp_path, native_env, body, expression, condition)


@pytest.mark.parametrize("comment", ('"plain"', "str.tostring(close)", 'str.format("{0}", close)'))
def test_order_comments_without_bool_conversion_keep_reference_cpp(tmp_path, comment):
    _assert_reference_cpp(
        tmp_path, HEADER
        + f'strategy.order("L", strategy.long, qty = 1, comment = {comment})\n',
    )


def test_concatenation_keeps_explicit_bool_conversion(tmp_path, native_env):
    _assert_native_text(
        tmp_path, native_env, "", '"x" + str.tostring(session.isfirstbar_regular)',
        "session.isfirstbar_regular", prefix="x",
    )


@pytest.mark.parametrize("body,value,condition", (
    ("", "session.isfirstbar_regular", "session.isfirstbar_regular"),
    ("ok = ta.crossover(close, open)\n", "ok", "ta.crossover(close, open)"),
))
def test_string_cast_uses_bool_text_conversion(tmp_path, native_env, body, value, condition):
    _assert_native_text(tmp_path, native_env, body, f"string({value})", condition)


@pytest.mark.parametrize("value", ("true", "flag"))
def test_string_plus_bool_requires_an_explicit_conversion(value):
    with pytest.raises(CompileError, match="cannot combine"):
        transpile(_source("flag = close > open\n", f'"x" + {value}'))


@pytest.mark.parametrize("channel", ("info", "warning", "error"))
def test_log_format_arguments_keep_their_cpp_bool_type(tmp_path, native_env, channel):
    stderr, expected = _assert_native_text(
        tmp_path, native_env, "", 'str.format("{0}", session.isfirstbar_regular)',
        "session.isfirstbar_regular",
        sink=f'log.{channel}("{{0}}", session.isfirstbar_regular)\n',
    )
    level = {"info": "INFO", "warning": "WARN", "error": "ERROR"}[channel]
    actual = [line for line in stderr.splitlines()
              if line in (f"[{level}] true", f"[{level}] false")]
    assert actual == [f"[{level}] {value}" for value in expected.values()], "Pine reference"


@pytest.mark.parametrize("statement", (
    'alert(str.tostring(session.isfirstbar_regular), alert.freq_all)',
    'alertcondition(session.isfirstbar_regular, title = "first", message = "first")',
    'label.new(bar_index, close, tooltip = str.tostring(session.isfirstbar_regular))',
    'strategy.order("L", strategy.long, qty = 1, comment = str.tostring(session.isfirstbar_regular))',
))
def test_omitted_text_sinks_keep_reference_cpp(tmp_path, statement):
    """Frozen-C++ control: alerts, constructor tooltip and order comment are omitted."""
    reference = reference_codegen(NUMERIC_REFERENCE)
    assert reference is not None, "required C++ reference is unavailable"
    pine = tmp_path / "strategy.pine"
    pine.write_text(HEADER + statement + "\n", encoding="utf-8")
    current = transpile_json(pine)
    previous = transpile_json(pine, reference)
    assert current["ok"], current.get("diagnostics")
    assert previous["ok"], previous.get("diagnostics")
    assert current["cpp"] == previous["cpp"]


@pytest.mark.parametrize("body,comment", (
    ("", "str.tostring(session.isfirstbar_regular)"),
    ("flag = ta.crossover(close, open)\n", "str.tostring(flag)"),
    ("flag = close > open\n", "str.tostring(flag[1])"),
    ("", '"x" + str.tostring(session.isfirstbar_regular)'),
    ("", "string(session.isfirstbar_regular)"),
))
def test_order_with_a_bool_text_comment_compiles_and_runs(
        tmp_path, native_env, body, comment):
    """The engine's order carries no comment, so a bool text there must not break the build.

    A script that compiled and ran before the bool-text change keeps compiling and
    running, and the order itself is still placed: the position grows.
    """
    source = (
        HEADER + body
        + f'strategy.order("L", strategy.long, qty = 1, comment = {comment})\n'
        + 'log.info("ran {0} {1}", bar_index, strategy.position_size)\n'
    )
    stderr = _run_native(tmp_path, native_env, source)
    ran = _messages(stderr, "ran", native_env[2])
    assert float(ran[native_env[2] - 1]) > 0, "the order was placed and filled"


def _assert_reference_cpp(tmp_path, source):
    reference = reference_codegen(NUMERIC_REFERENCE)
    assert reference is not None, "required C++ reference is unavailable"
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    current = transpile_json(pine)
    previous = transpile_json(pine, reference)
    assert current["ok"], current.get("diagnostics")
    assert previous["ok"], previous.get("diagnostics")
    assert current["cpp"] == previous["cpp"]


@pytest.mark.parametrize("initializer", (
    "session.isfirstbar", "barstate.isconfirmed", "ta.crossover(close, open)",
    "ta.crossunder(close, open)", "ta.cross(close, open)",
    "ta.rising(close, 2)", "ta.falling(close, 2)",
    "timeframe.isintraday",
    "array.includes(a, 1)", "array.every(a)",
    "array.some(a)", 'str.contains("ab", "a")',
    'str.startswith("ab", "a")', 'str.endswith("ab", "b")',
    'map.contains(m, "a")', 'request.security(syminfo.tickerid, "60", close > open)',
))
def test_bool_conditions_keep_complete_reference_cpp(tmp_path, initializer):
    """Frozen-C++ control for conditions whose storage remains unchanged."""
    source = (
        HEADER + "a = array.from(1, 2)\nm = map.new<string, float>()\n"
        + f"flag = {initializer}\nalias = flag\n"
        + "if flag\n    strategy.entry(\"L\", strategy.long)\n"
        + "both = flag and alias\neither = flag or alias\nnegated = not flag\n"
        + 'selection = flag ? "T" : "F"\n'
        + f"if {initializer}\n    strategy.close(\"L\")\n"
    )
    _assert_reference_cpp(tmp_path, source)


@pytest.mark.parametrize("body,value,condition", (
    ("type State\n    bool active\ns = State.new(barstate.isconfirmed)\n",
     "s.active", "barstate.isconfirmed"),
    ("type State\n    bool active\ns = State.new(ta.crossover(close, open))\n",
     "s.active", "ta.crossover(close, open)"),
    ('m = map.new<string, bool>()\nm.put("a", session.isfirstbar)\n',
     'm.get("a")', "session.isfirstbar"),
    ('m = map.new<string, bool>()\nm.put("a", ta.crossover(close, open))\n',
     'm.get("a")', "ta.crossover(close, open)"),
    ("flag = not na(ta.pivothigh(high, 2, 2))\n", "flag", "not na(ta.pivothigh(high, 2, 2))"),
    ("flag = ta.crossover(close, open) ? true : false\n", "flag", "ta.crossover(close, open)"),
    ("f(p) => str.tostring(p)\n", "f(close > open)", "close > open"),
    ("", "timeframe.isintraday", "timeframe.isintraday"),
))
def test_correct_bool_text_keeps_reference_behavior(
        tmp_path, native_env, body, value, condition):
    expression = value if value.startswith("f(") else f"str.tostring({value})"
    _assert_native_text(tmp_path, native_env, body, expression, condition)


@pytest.mark.parametrize("call", TOSTRING_CALLS)
@pytest.mark.parametrize("body", (
    "flag = session.isfirstbar\nrender() =>\n    flag = 2.5\n    {conversion}\n",
    "flag = ta.crossover(close, open)\nrender(flag) => {conversion}\n",
))
def test_numeric_shadow_does_not_inherit_bool_text(tmp_path, body, call):
    body = body.format(conversion=call.format(value="flag"))
    argument = "render(2.5)" if "render(flag)" in body else "render()"
    _assert_reference_cpp(tmp_path, _source(body, argument))


@pytest.mark.parametrize("initializer", (
    "session.isfirstbar", "ta.crossover(close, open)", "array.includes(values, 1)",
))
def test_bool_provenance_crosses_nested_helpers(tmp_path, native_env, initializer):
    _assert_native_text(
        tmp_path, native_env,
        "values = array.from(1, 2)\n"
        "identity(p) => p\n"
        "render(p) =>\n    local = identity(p)\n    str.tostring(local)\n",
        f"render({initializer})", initializer,
    )


@pytest.mark.parametrize("value", (
    "session.isfirstbar_regular", "barstate.islast",
))
def test_v5_observers_keep_their_accepted_reference_cpp(tmp_path, value):
    """The v6 fix must not expand the existing v5 bool-observer refusals."""
    reference = reference_codegen(NUMERIC_REFERENCE)
    assert reference is not None, "required C++ reference is unavailable"
    source = _source("import pftest/BoolText/1 as B\n", "B.render()")
    library = ('//@version=5\nlibrary("BoolText")\n'
               f"export render() => str.tostring({value})\n")
    layout = Layout(tmp_path / "env")
    digest = layout.add_library("pftest/BoolText/1", library.encode("utf-8"))
    layout.add_probe("bool-text", source.encode("utf-8"),
                     {"pftest/BoolText/1": digest})
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    current = transpile_json(pine, extra_env=layout.env)
    previous = transpile_json(pine, reference, extra_env=layout.env)
    assert current["ok"], current.get("diagnostics")
    assert previous["ok"], previous.get("diagnostics")
    assert current["cpp"] == previous["cpp"]


def test_native_session_bool_text_matches_the_numeric_flags(tmp_path):
    """Pine reference: numeric flags independently determine the text."""
    engine = skip_unless_e2e_env()
    # Two regular US trading days, including pre/post-market bars. Both
    # session boundary flags must take both values; no fixed flag tape is
    # inferred from the text conversion being tested.
    stamps = [
        int(dt.datetime(2025, 1, day, 14, tzinfo=dt.timezone.utc).timestamp() * 1000)
        + step * 900_000
        for day in (6, 7) for step in range(30)
    ]
    feed = tmp_path / "chart.csv"
    feed.write_text(
        "timestamp,open,high,low,close,volume\n"
        + "".join(f"{stamp},100,101,99,100.5,1\n" for stamp in stamps),
        encoding="utf-8",
    )
    pine = tmp_path / "strategy.pine"
    timeframe_flags = sorted(
        name for name, pine_type in signatures.TIMEFRAME_VARIABLES.items()
        if pine_type == PineType.BOOL
    )
    stored_timeframes = "".join(
        f"held{index} = {name}\n"
        f'log.info("timeframe {index} {{0}} {{1}}", bar_index, str.tostring(held{index}))\n'
        f"// @pf-trace timeframe{index}={name}\n"
        for index, name in enumerate(timeframe_flags)
    )
    pine.write_text(
        HEADER
        + 'log.info("text {0} {1} {2}", bar_index, '
        'str.tostring(session.isfirstbar_regular), '
        'str.tostring(session.islastbar_regular))\n'
        + 'log.info("format {0} {1} {2}", bar_index, '
        'session.isfirstbar_regular, session.islastbar_regular)\n'
        + "// @pf-trace first=session.isfirstbar_regular\n"
        + "// @pf-trace last=session.islastbar_regular\n"
        + stored_timeframes,
        encoding="utf-8",
    )
    translated = transpile_json(pine)
    assert translated["ok"], translated.get("diagnostics")
    build_strategy_library(translated["cpp"], tmp_path)
    _, records, stderr = run_strategy(
        engine, tmp_path, feed,
        {"input_tf": "15", "script_tf": "15",
         "runtime_overrides": {"session": "0930-1600",
                               "timezone": "America/New_York"}},
        "bool_text", trace=True,
    )
    flags = {
        name: {r["bar_index"]: r["value"] for r in records if r["name"] == name}
        for name in ("first", "last")
    }
    for values in flags.values():
        assert len(values) == len(stamps)
        assert set(values.values()) == {0.0, 1.0}
    expected = {
        bar: tuple("true" if flags[name][bar] == 1.0 else "false"
                   for name in ("first", "last"))
        for bar in flags["first"]
    }
    for route in ("text", "format"):
        lines = [line.split() for line in stderr.splitlines()
                 if line.startswith(f"[INFO] {route} ")]
        assert len(lines) == len(stamps), stderr
        assert all(len(line) == 5 for line in lines), lines
        actual = {int(line[2]): tuple(line[3:]) for line in lines}
        assert actual == expected
    for index, _name in enumerate(timeframe_flags):
        values = {
            record["bar_index"]: record["value"] for record in records
            if record["name"] == f"timeframe{index}"
        }
        assert len(values) == len(stamps)
        assert set(values.values()) <= {0.0, 1.0}
        lines = [line.split() for line in stderr.splitlines()
                 if line.startswith(f"[INFO] timeframe {index} ")]
        assert len(lines) == len(stamps) and all(len(line) == 5 for line in lines)
        assert {int(line[3]): line[4] for line in lines} == {
            bar: "true" if value == 1.0 else "false" for bar, value in values.items()
        }
