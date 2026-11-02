"""Bool text follows Pine's true/false spelling without changing numeric text."""

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
    assert "bool ok = " in cpp


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


@pytest.mark.parametrize("value", BUILTIN_BOOLS)
@pytest.mark.parametrize("bind", (False, True))
def test_every_registered_builtin_bool_can_be_stringified(value, bind):
    body = f"flag = {value}\n" if bind else ""
    argument = "flag" if bind else value
    cpp = transpile(_source(body, f"str.tostring({argument})"))
    assert cpp.count(BOOL_TEXT) == 1


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
    ("elapsed = ta.barssince(close > open)\n", "str.tostring(elapsed)"),
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
def test_str_format_passes_bools_to_the_bool_value_constructor(initializer):
    cpp = transpile(_source(
        f"flag = {initializer}\n",
        'str.format("{0} {1}", flag, session.isfirstbar_regular)',
    ))
    assert "bool flag = " in cpp
    assert "_PFTvFormatValue(flag)" in cpp
    assert "_PFTvFormatValue(session_isfirstbar_regular_)" in cpp
    assert '_PFTvFormatValue(bool value)' in cpp
    assert 'text(value ? "true" : "false")' in cpp


@pytest.mark.parametrize("statement", (
    'strategy.entry("L", strategy.long, comment = str.tostring(session.isfirstbar_regular))',
    'strategy.order("L", strategy.long, comment = str.tostring(session.isfirstbar_regular))',
    'strategy.close("L", comment = str.tostring(session.isfirstbar_regular))',
    'strategy.close_all(comment = str.tostring(session.isfirstbar_regular))',
    'strategy.exit("X", "L", limit = close + 1, comment = str.tostring(session.isfirstbar_regular))',
    'label.new(bar_index, close, text = str.tostring(session.isfirstbar_regular))',
    'var mark = label.new(0, close)\nlabel.set_text(mark, str.tostring(session.isfirstbar_regular))',
    'log.info(str.tostring(session.isfirstbar_regular))',
    'log.warning(str.tostring(session.isfirstbar_regular))',
    'log.error(str.tostring(session.isfirstbar_regular))',
))
def test_string_consumers_keep_explicit_bool_conversion(statement):
    cpp = transpile(HEADER + statement + "\n")
    assert f"(session_isfirstbar_regular_ {BOOL_TEXT})" in cpp
    assert "pine_str_tostring_tv(session_isfirstbar_regular_," not in cpp


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
    'var mark = label.new(0, close)\nlabel.set_tooltip(mark, str.tostring(session.isfirstbar_regular))',
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
    pine.write_text(
        HEADER
        + 'log.info("text {0} {1} {2}", bar_index, '
        'str.tostring(session.isfirstbar_regular), '
        'str.tostring(session.islastbar_regular))\n'
        + 'log.info("format {0} {1} {2}", bar_index, '
        'session.isfirstbar_regular, session.islastbar_regular)\n'
        + "// @pf-trace first=session.isfirstbar_regular\n"
        + "// @pf-trace last=session.islastbar_regular\n",
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
