"""Bool text follows Pine's true/false spelling without changing numeric text."""

from __future__ import annotations

import datetime as dt
import re

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
BOOL_TEXT = '? std::string("true") : std::string("false")'
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


@pytest.mark.parametrize("call", TOSTRING_CALLS)
@pytest.mark.parametrize("value,rendered", (
    ("session.isfirstbar_regular", "session_isfirstbar_regular_"),
    ("session.islastbar_regular", "session_islastbar_regular_"),
    ("session.isfirstbar", "session_isfirstbar_"),
    ("barstate.islast", "barstate_islast_"),
))
def test_measured_builtin_bools_use_text_conversion(value, rendered, call):
    cpp = transpile(_source("", call.format(value=value)))
    assert f"({rendered} {BOOL_TEXT})" in cpp
    assert f"pine_str_tostring_tv({rendered}," not in cpp


@pytest.mark.parametrize("call", TOSTRING_CALLS)
@pytest.mark.parametrize("expression", (
    "ta.crossover(close, ta.sma(close, 5))",
    "ta.crossunder(close, ta.sma(close, 5))",
    "ta.cross(close, ta.sma(close, 5))",
    "ta.rising(close, 5)",
    "ta.falling(close, 5)",
))
def test_ta_bool_variable_uses_text_conversion(expression, call):
    cpp = transpile(_source(f"ok = {expression}\n", call.format(value="ok")))
    assert f"(ok {BOOL_TEXT})" in cpp
    assert "pine_str_tostring_tv(ok," not in cpp


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
def test_value_registry_declares_collection_and_request_predicates(table_name):
    table = getattr(signatures, table_name, None)
    assert isinstance(table, dict) and table, f"missing predicate registry: {table_name}"
    assert any(signature.return_type == PineType.BOOL
               for function in table.values() for signature in function.signatures)


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


def _text_assignment(cpp):
    start = re.search(r"(?m)^\s*text = ", cpp)
    assert start is not None, cpp
    depth = 0
    tokens = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[(){}\[\];]')
    for token in tokens.finditer(cpp, start.end()):
        value = token.group()
        if value in ("(", "{", "["):
            depth += 1
        elif value in (")", "}", "]"):
            depth -= 1
        elif value == ";" and depth == 0:
            return cpp[start.end():token.start()].strip()
    pytest.fail("the text assignment has no terminating semicolon")


def _assert_text_assignment(cpp, *, operand=None):
    # Inspect this entire assignment, including lambdas in its argument.
    # A conversion in another statement cannot satisfy the assertion.
    expression = _text_assignment(cpp)
    assert expression.startswith("("), expression
    suffix = f" {BOOL_TEXT})"
    assert expression.endswith(suffix), expression
    assert "pine_str_tostring_tv(" not in expression
    condition = expression[1:-len(suffix)]
    if operand is not None:
        assert re.search(rf"\b{re.escape(operand)}\b", condition), condition
    return condition


def _stored_condition(name, variable):
    storage = getattr(signatures, "VARIABLE_STORAGE_COMPATIBILITY", {})
    if storage.get(name) == PineType.STRING:
        return f"(!({variable}).empty() && ({variable})[0] != '\\0')"
    return variable


@pytest.mark.parametrize("name", sorted(set(BUILTIN_BOOLS) | set(BOOL_CALLS)))
@pytest.mark.parametrize("shape,call", [
    (shape, call)
    for shape in (
        "direct", "stored", "parameter", "array", "history", "local", "return",
    )
    for call in (TOSTRING_CALLS if shape in ("stored", "local", "parameter")
                 else TOSTRING_CALLS[:1])
])
def test_every_registry_bool_has_bool_text_in_each_shape(name, shape, call):
    assert name not in BOOL_SOURCE_EXCEPTIONS, BOOL_SOURCE_EXCEPTIONS.get(name)
    preamble, value = _builtin_source(name)
    if shape == "parameter":
        cpp = transpile(_source(
            f"render(p) => {call.format(value='p')}\n" + preamble,
            f"render({value})",
        ))
        assert f"return ({_stored_condition(name, 'p')} {BOOL_TEXT});" in cpp
        assert "pine_str_tostring_tv(p," not in cpp
    elif shape == "local":
        cpp = transpile(_source(
            preamble + f"render() =>\n    flag = {value}\n"
            + f"    {call.format(value='flag')}\n",
            "render()",
        ))
        assert f"return (flag {BOOL_TEXT});" in cpp
        assert "pine_str_tostring_tv(flag," not in cpp
    else:
        operand = None
        if shape in ("stored", "history"):
            preamble += f"flag = {value}\n"
            value = "flag[1]" if shape == "history" else "flag"
            operand = "flag"
        elif shape == "array":
            preamble += f"flags = array.from({value})\n"
            value = "array.get(flags, 0)"
            operand = "flags"
        elif shape == "return":
            preamble += f"predicate() => {value}\n"
            value = "predicate()"
            operand = None
        cpp = transpile(_source(preamble, call.format(value=value)))
        condition = _assert_text_assignment(cpp, operand=operand)
        if shape == "return":
            assert re.search(r"\bpredicate(?:_[A-Za-z0-9]+)*\(", condition), condition
        if shape == "stored":
            assert condition == _stored_condition(name, "flag")


@pytest.mark.parametrize("body,value", (
    ("flags = array.from(session.isfirstbar)\n", "flags.get(0)"),
    ("flags = array.from(ta.crossover(close, open))\n", "flags.get(0)"),
    ("a = array.from(1, 2)\n", "array.includes(a, 1)"),
    ("a = array.from(1, 2)\n", "a.includes(1)"),
    ("a = array.from(1, 2)\nfound = array.includes(a, 1)\n", "found[1]"),
    ('rb = request.security(syminfo.tickerid, "60", close > open)\n', "rb"),
    ('flags = request.security_lower_tf(syminfo.tickerid, "1", close > open)\n',
     "array.get(flags, 0)"),
    ("", "ta.change(close > open)"),
    ("", "ta.valuewhen(close > open, close < open, 0)"),
    ("f(p) => str.tostring(p)\n", "f(session.isfirstbar)"),
    ("f(p) => str.tostring(p)\n", "f(ta.crossover(close, open))"),
    ("f(p) => str.tostring(p)\n", "f(barstate.isconfirmed)"),
    ("f(p) => str.tostring(p)\n", "f(close > open)"),
))
def test_measured_bool_transport_paths(body, value):
    if value.startswith("f("):
        cpp = transpile(_source(body, value))
        assert f"return (p {BOOL_TEXT});" in cpp
        assert "pine_str_tostring_tv(p," not in cpp
    else:
        cpp = transpile(_source(body, f"str.tostring({value})"))
        _assert_text_assignment(cpp)


@pytest.mark.parametrize("body,value", (
    ("", "true"),
    ("var bool flag = true\n", "flag"),
    ("flag = close > open\n", "flag"),
    ("flag = close > open\n", "flag[1]"),
    ("var bool flag = false\nflag := close > open\n", "flag"),
    ("is_up() => close > open\n", "is_up()"),
    ("", "close > open ? true : false"),
    ("flags = array.new<bool>(1, true)\n", "array.get(flags, 0)"),
    ("flags = array.new<bool>(1, true)\n", "flags.get(0)"),
    ("ok = ta.crossover(close, open)\nalias = ok\n", "alias"),
    ("ok = ta.crossover(close, open)\n", "ok[1]"),
    ("var ok = ta.crossover(close, open)\n", "ok"),
    ("flag = session.isfirstbar_regular\n", "flag[1]"),
    ("render() =>\n    ok = ta.crossover(close, open)\n    str.tostring(ok)\n",
     None),
    ("", "session.isfirstbar_regular[1]"),
))
def test_bool_values_keep_their_type_through_text_conversion(body, value):
    expression = f"str.tostring({value})" if value is not None else "render()"
    cpp = transpile(_source(body, expression))
    assert cpp.count(BOOL_TEXT) == 1


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


@pytest.mark.parametrize("statement", (
    'strategy.entry("L", strategy.long, comment = str.tostring(session.isfirstbar_regular))',
    'strategy.order("L", strategy.long, comment = str.tostring(session.isfirstbar_regular))',
    'strategy.close("L", comment = str.tostring(session.isfirstbar_regular))',
    'strategy.close_all(comment = str.tostring(session.isfirstbar_regular))',
    'strategy.exit("X", "L", limit = close + 1, comment = str.tostring(session.isfirstbar_regular))',
    'label.new(bar_index, close, text = str.tostring(session.isfirstbar_regular))',
    'var mark = label.new(0, close)\nlabel.set_text(mark, str.tostring(session.isfirstbar_regular))',
    'var mark = label.new(0, close)\nlabel.set_tooltip(mark, str.tostring(session.isfirstbar_regular))',
    'log.info(str.tostring(session.isfirstbar_regular))',
    'log.warning(str.tostring(session.isfirstbar_regular))',
    'log.error(str.tostring(session.isfirstbar_regular))',
))
def test_string_consumers_keep_explicit_bool_conversion(statement):
    cpp = transpile(HEADER + statement + "\n")
    assert f"(session_isfirstbar_regular_ {BOOL_TEXT})" in cpp
    assert "pine_str_tostring_tv(session_isfirstbar_regular_," not in cpp


def test_order_comment_transports_the_converted_value():
    cpp = transpile(
        HEADER + 'strategy.order("L", strategy.long, qty = 1, '
        'comment = str.tostring(session.isfirstbar_regular))\n'
    )
    assert re.search(
        r'^\s*strategy_order\([^\n]*, \(session_isfirstbar_regular_ '
        + re.escape(BOOL_TEXT) + r'\)\);$',
        cpp, re.M,
    ), cpp


@pytest.mark.parametrize("comment", ('"plain"', "str.tostring(close)", 'str.format("{0}", close)'))
def test_order_comments_without_bool_conversion_keep_reference_cpp(tmp_path, comment):
    _assert_reference_cpp(
        tmp_path, HEADER
        + f'strategy.order("L", strategy.long, qty = 1, comment = {comment})\n',
    )


def test_concatenation_keeps_explicit_bool_conversion():
    cpp = transpile(_source("", '"x" + str.tostring(session.isfirstbar_regular)'))
    assert f"(session_isfirstbar_regular_ {BOOL_TEXT})" in cpp
    assert 'std::string("x") +' in cpp


@pytest.mark.parametrize("body,value", (
    ("", "session.isfirstbar_regular"),
    ("ok = ta.crossover(close, open)\n", "ok"),
))
def test_string_cast_uses_bool_text_conversion(body, value):
    cpp = transpile(_source(body, f"string({value})"))
    assert cpp.count(BOOL_TEXT) == 1


@pytest.mark.parametrize("value", ("true", "flag"))
def test_string_plus_bool_requires_an_explicit_conversion(value):
    with pytest.raises(CompileError, match="cannot combine"):
        transpile(_source("flag = close > open\n", f'"x" + {value}'))


@pytest.mark.parametrize("channel", ("info", "warning", "error"))
def test_log_format_arguments_keep_their_cpp_bool_type(channel):
    cpp = transpile(
        HEADER + f'log.{channel}("{{0}}", session.isfirstbar_regular)\n'
    )
    assert "_PFTvFormatValue(session_isfirstbar_regular_)" in cpp
    assert 'text(value ? "true" : "false")' in cpp


@pytest.mark.parametrize("statement", (
    'alert(str.tostring(session.isfirstbar_regular), alert.freq_all)',
    'alertcondition(session.isfirstbar_regular, title = "first", message = "first")',
    'label.new(bar_index, close, tooltip = str.tostring(session.isfirstbar_regular))',
))
def test_omitted_text_sinks_keep_reference_cpp(tmp_path, statement):
    """Compatibility control: these sinks do not currently deliver any text."""
    reference = reference_codegen(NUMERIC_REFERENCE)
    assert reference is not None, "required C++ reference is unavailable"
    pine = tmp_path / "strategy.pine"
    pine.write_text(HEADER + statement + "\n", encoding="utf-8")
    current = transpile_json(pine)
    previous = transpile_json(pine, reference)
    assert current["ok"], current.get("diagnostics")
    assert previous["ok"], previous.get("diagnostics")
    assert current["cpp"] == previous["cpp"]


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
    "timeframe.isintraday", "timeframe.isminutes", "timeframe.isseconds",
    "array.includes(a, 1)", "array.every(a)",
    "array.some(a)", 'str.contains("ab", "a")',
    'str.startswith("ab", "a")', 'str.endswith("ab", "b")',
    'map.contains(m, "a")', 'request.security(syminfo.tickerid, "60", close > open)',
))
def test_bool_conditions_keep_complete_reference_cpp(tmp_path, initializer):
    source = (
        HEADER + "a = array.from(1, 2)\nm = map.new<string, float>()\n"
        + f"flag = {initializer}\nalias = flag\n"
        + "if flag\n    strategy.entry(\"L\", strategy.long)\n"
        + "both = flag and alias\neither = flag or alias\nnegated = not flag\n"
        + 'selection = flag ? "T" : "F"\n'
        + f"if {initializer}\n    strategy.close(\"L\")\n"
    )
    _assert_reference_cpp(tmp_path, source)


@pytest.mark.parametrize("body,value", (
    ("type State\n    bool active\ns = State.new(barstate.isconfirmed)\n", "s.active"),
    ("type State\n    bool active\ns = State.new(ta.crossover(close, open))\n", "s.active"),
    ('m = map.new<string, bool>()\nm.put("a", session.isfirstbar)\n', 'm.get("a")'),
    ('m = map.new<string, bool>()\nm.put("a", ta.crossover(close, open))\n', 'm.get("a")'),
    ("flag = not na(ta.pivothigh(high, 2, 2))\n", "flag"),
    ("flag = ta.crossover(close, open) ? true : false\n", "flag"),
    ("f(p) => str.tostring(p)\n", "f(close > open)"),
    ("", "timeframe.isintraday"),
))
def test_correct_bool_text_keeps_complete_reference_cpp(tmp_path, body, value):
    expression = value if value.startswith("f(") else f"str.tostring({value})"
    _assert_reference_cpp(tmp_path, _source(body, expression))


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
def test_bool_provenance_crosses_nested_helpers(initializer):
    cpp = transpile(_source(
        "values = array.from(1, 2)\n"
        "identity(p) => p\n"
        "render(p) =>\n    local = identity(p)\n    str.tostring(local)\n",
        f"render({initializer})",
    ))
    assert f"return (local {BOOL_TEXT});" in cpp
    assert "pine_str_tostring_tv(local," not in cpp


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


def test_native_order_comment_keeps_the_bool_text(tmp_path):
    engine = skip_unless_e2e_env()
    start = int(dt.datetime(2025, 1, 6, 14, tzinfo=dt.timezone.utc).timestamp() * 1000)
    feed = tmp_path / "chart.csv"
    feed.write_text(
        "timestamp,open,high,low,close,volume\n"
        + "".join(f"{start + index * 900_000},100,101,99,100.5,1\n"
                  for index in range(6)),
        encoding="utf-8",
    )
    pine = tmp_path / "strategy.pine"
    pine.write_text(
        HEADER
        + 'if bar_index == 0\n    strategy.order("L", strategy.long, qty = 1, '
        'comment = str.tostring(session.isfirstbar_regular))\n'
        + 'if bar_index == 2\n    log.info("comment {0}", '
        'strategy.opentrades.entry_comment(0))\n'
        + "// @pf-trace expected=session.isfirstbar_regular\n",
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
        "order_text", trace=True,
    )
    first = [record["value"] for record in records
             if record["name"] == "expected" and record["bar_index"] == 0]
    assert len(first) == 1 and first[0] in (0.0, 1.0)
    expected = "true" if first[0] == 1.0 else "false"
    assert [line for line in stderr.splitlines() if line.startswith("[INFO] comment ")] == [
        f"[INFO] comment {expected}",
    ]
