"""A string a script spells reaches the generated C++ only as the value of a
string literal or as comment text, never as code.

Every entry point that carries a Pine string into the C++ -- input titles, ids,
messages, symbols, sessions, timezones, timeframes, format patterns, keys,
enum titles, library code, an omitted field's placeholder comment -- is fed
strings holding quotes, backslashes, line breaks and comment delimiters.
Each script is refused, or its C++ keeps every such string inside a string
literal (``tests/_cpp_tokens.py``). Each entry point first transpiles with a
benign string, so no case passes by being refused for an unrelated reason.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from pineforge_codegen.pine_spelling import pine_string_literal

from tests._cpp_tokens import assert_inert, assert_only_in_strings, regions, string_values

MARKER = "PFSW"

# Strings holding quotes, backslashes, line breaks and comment delimiters.
SAMPLES = {
    "quote": f'x" {MARKER} "',
    "backslash": f'x\\" {MARKER} \\',
    "newline": f"x\n{MARKER}",
    "comment_close": f'x*/ {MARKER} " /*',
    "line_comment": f'x" // {MARKER}',
    "carriage_return": f"x\r{MARKER}",
    "crlf": f"x\r\n{MARKER}",
    "nul_digit": "x\0" + f"7{MARKER}",
    "line_directive": f"x\n#{MARKER}",
    "line_splice": f"x\\\n{MARKER}",
    "trigraph": f"x??={MARKER}",
    "bmp": f"前{MARKER}é",
    "non_bmp": f"🐧{MARKER}🧭",
    "unicode_line_separator": f"x\u2028{MARKER}",
    "unicode_next_line": f"x\u0085{MARKER}",
    "long": "x" * 100_000 + MARKER,
}

PRELUDE = '//@version=6\nstrategy("t")\n'
TRADE = 'if close > open\n    strategy.entry("L", strategy.long)\n'

# name -> (script with ``{s}`` where the string goes, benign string)
ENTRY_POINTS = {
    "input_title": (PRELUDE + 'n = input.int(5, {s})\nif close > ta.sma(close, n)\n'
                    '    strategy.entry("L", strategy.long)\n', "Length"),
    "input_string_default": (PRELUDE + 'm = input.string({s}, "Mode")\nif m == "a"\n'
                             '    strategy.entry("L", strategy.long)\n', "a"),
    "input_string_option": (PRELUDE + 'm = input.string("a", "Mode", options=["a", {s}])\n'
                            'if m == "a"\n    strategy.entry("L", strategy.long)\n', "b"),
    "input_text_area": (PRELUDE + 'm = input.text_area({s}, "Notes")\nif str.length(m) > 0\n'
                        '    strategy.entry("L", strategy.long)\n', "note"),
    "input_timeframe_default": (PRELUDE + 'tf = input.timeframe({s}, "TF")\n'
                                'x = request.security(syminfo.tickerid, tf, close)\n'
                                'if x > open\n    strategy.entry("L", strategy.long)\n', "60"),
    "input_symbol_default": (PRELUDE + 'sym = input.symbol({s}, "Symbol")\n'
                             'x = request.security(sym, "60", close)\n'
                             'if x > open\n    strategy.entry("L", strategy.long)\n',
                             "BINANCE:BTCUSDT"),
    "input_session_default": (PRELUDE + 'sess = input.session({s}, "Session")\n'
                              'if not na(time(timeframe.period, sess))\n'
                              '    strategy.entry("L", strategy.long)\n', "0930-1600"),
    "security_symbol_literal": (PRELUDE + 'x = request.security({s}, "60", close)\n'
                                'if x > open\n    strategy.entry("L", strategy.long)\n',
                                "BINANCE:BTCUSDT"),
    "security_symbol_constant": (PRELUDE + 'sym = {s}\nx = request.security(sym, "60", close)\n'
                                 'if x > open\n    strategy.entry("L", strategy.long)\n',
                                 "BINANCE:BTCUSDT"),
    "security_symbol_helper_param": (
        PRELUDE + 'f(s) => request.security(s, "60", close)\nx = f({s})\n'
        'if x > open\n    strategy.entry("L", strategy.long)\n', "BINANCE:BTCUSDT"),
    "security_symbol_ticker_inherit": (
        PRELUDE + 'x = request.security(ticker.inherit(syminfo.tickerid, {s}), "60", close)\n'
        'if x > open\n    strategy.entry("L", strategy.long)\n', "BINANCE:BTCUSDT"),
    "security_symbol_ticker_standard": (
        PRELUDE + 'x = request.security(ticker.standard({s}), "60", close)\n'
        'if x > open\n    strategy.entry("L", strategy.long)\n', "BINANCE:BTCUSDT"),
    "syminfo_prefix_function": (
        PRELUDE + 'if syminfo.prefix({s}) == "BINANCE"\n    strategy.entry("L", strategy.long)\n',
        "BINANCE:BTCUSDT"),
    "security_string_payload": (
        PRELUDE + 'x = request.security(syminfo.tickerid, "60", {s} + str.tostring(close))\n'
        'if str.length(x) > 3\n    strategy.entry("L", strategy.long)\n', "c="),
    "security_timeframe_literal": (PRELUDE + 'x = request.security(syminfo.tickerid, {s}, close)\n'
                                   'if x > open\n    strategy.entry("L", strategy.long)\n', "60"),
    "security_timeframe_constant": (
        PRELUDE + 'tf = {s}\nx = request.security(syminfo.tickerid, tf, close)\n'
        'if x > open\n    strategy.entry("L", strategy.long)\n', "60"),
    "security_timeframe_helper_param": (
        PRELUDE + 'f(t) => request.security(syminfo.tickerid, t, close)\nx = f({s})\n'
        'if x > open\n    strategy.entry("L", strategy.long)\n', "60"),
    "security_timeframe_switch_arm": (
        PRELUDE + 'm = input.int(1)\nx = switch m\n'
        '    1 => request.security(syminfo.tickerid, {s}, close)\n    => close\n'
        'if x > open\n    strategy.entry("L", strategy.long)\n', "60"),
    "lower_tf_timeframe_constant": (
        PRELUDE + 'tf = {s}\nx = request.security_lower_tf(syminfo.tickerid, tf, close)\n'
        'if array.size(x) > 0\n    strategy.entry("L", strategy.long)\n', "1"),
    "financial_id": (PRELUDE + 'x = request.financial(syminfo.tickerid, {s}, "FQ")\n'
                     'if x > 0\n    strategy.entry("L", strategy.long)\n', "TOTAL_REVENUE"),
    "earnings_symbol": (PRELUDE + 'x = request.earnings({s})\n'
                        'if x > 0\n    strategy.entry("L", strategy.long)\n', "NASDAQ:AAPL"),
    "time_timeframe": (PRELUDE + 'if not na(time({s}))\n    strategy.entry("L", strategy.long)\n', "D"),
    "time_session_timezone": (PRELUDE + 'if not na(time("D", {s}, {s}))\n'
                              '    strategy.entry("L", strategy.long)\n', "UTC"),
    "time_close_timeframe": (PRELUDE + 'if time_close({s}) > time\n'
                             '    strategy.entry("L", strategy.long)\n', "D"),
    "timeframe_change": (PRELUDE + 'if timeframe.change({s})\n    strategy.entry("L", strategy.long)\n',
                         "D"),
    "timeframe_in_seconds": (PRELUDE + 'if timeframe.in_seconds({s}) > 60\n'
                             '    strategy.entry("L", strategy.long)\n', "60"),
    "vwap_anchor": (PRELUDE + 'v = ta.vwap(close, timeframe.change({s}))\n'
                    'if close > v\n    strategy.entry("L", strategy.long)\n', "D"),
    "timestamp_timezone": (PRELUDE + 'if time > timestamp({s}, 2020, 1, 1, 0, 0)\n'
                           '    strategy.entry("L", strategy.long)\n', "UTC"),
    "hour_timezone": (PRELUDE + 'if hour(time, {s}) > 9\n    strategy.entry("L", strategy.long)\n',
                      "America/New_York"),
    "str_format_pattern": (PRELUDE + 'if str.length(str.format({s}, close)) > 0\n'
                           '    strategy.entry("L", strategy.long)\n', "{0}"),
    "str_format_time_pattern": (PRELUDE + 'if str.length(str.format_time(time, {s}, "UTC")) > 0\n'
                                '    strategy.entry("L", strategy.long)\n', "yyyy-MM-dd"),
    "str_tostring_format": (PRELUDE + 'if str.length(str.tostring(close, {s})) > 0\n'
                            '    strategy.entry("L", strategy.long)\n', "#.##"),
    "str_functions": (PRELUDE + 'v = str.replace_all(syminfo.ticker, {s}, {s})\n'
                      'if str.contains(v, {s}) or str.pos(v, {s}) > 0\n'
                      '    strategy.entry("L", strategy.long)\n', "x"),
    "string_switch_case": (PRELUDE + 'k = switch syminfo.type\n    {s} => 1\n    => 2\n'
                           'if k == 1\n    strategy.entry("L", strategy.long)\n', "crypto"),
    "string_comparison": (PRELUDE + 'if syminfo.type == {s}\n    strategy.entry("L", strategy.long)\n',
                          "crypto"),
    "var_string": (PRELUDE + 'var string s = {s}\nif str.length(s) > 0\n'
                   '    strategy.entry("L", strategy.long)\n', "abc"),
    "function_default": (PRELUDE + 'f(string s = {s}) => str.length(s)\nif f() > 0\n'
                         '    strategy.entry("L", strategy.long)\n', "abc"),
    "map_key": (PRELUDE + 'var m = map.new<string, float>()\nm.put({s}, close)\n'
                'if m.get({s}) > open\n    strategy.entry("L", strategy.long)\n', "k"),
    "array_from": (PRELUDE + 'a = array.from({s}, "b")\nif array.size(a) > 1\n'
                   '    strategy.entry("L", strategy.long)\n', "a"),
    "udt_string_default": (PRELUDE + 'type T\n    string s = {s}\nt = T.new()\n'
                           'if str.length(t.s) > 0\n    strategy.entry("L", strategy.long)\n',
                           "abc"),
    "enum_title": (PRELUDE + 'enum E\n    a = {s}\n    b = "B"\ne = input.enum(E.a, "E")\n'
                   'if str.length(str.tostring(e)) > 0\n    strategy.entry("L", strategy.long)\n',
                   "A"),
    "order_ids_and_comments": (
        PRELUDE + 'if close > open\n    strategy.entry({s}, strategy.long, comment={s}, '
        'alert_message={s})\nstrategy.exit("x", {s}, profit=10, comment={s})\n'
        'strategy.close({s}, comment={s})\nstrategy.cancel({s})\n'
        'strategy.order({s}, strategy.short, 1)\n', "id"),
    "alerts_and_logs": (PRELUDE + 'if close > open\n    alert({s})\n    log.info({s})\n'
                        '    log.warning({s} + str.tostring(close))\n'
                        '    log.error({s}, close)\n    strategy.entry("L", strategy.long)\n'
                        'alertcondition(close > open, {s}, {s})\n', "msg"),
    "runtime_error": (PRELUDE + 'if bar_index < 0\n    runtime.error({s})\n' + TRADE, "boom"),
    "drawing_text": (PRELUDE + 'if close > open\n    label.new(bar_index, close, {s})\n'
                     '    strategy.entry("L", strategy.long)\n', "drawing text"),
    "pf_trace_expression": (PRELUDE + 'traceValue = {s}\n'
                            '// @pf-trace probe=str.length(traceValue)\n' + TRADE, "abc"),
    "omitted_table_field": (PRELUDE + 'type O\n    table t\n    float v\n'
                            'f(string s) => O.new(na, str.length(s))\nf({s}).t := na\n' + TRADE,
                            "abc"),
}


# Entry points whose string the C++ also spells as comment text.
COMMENT_SITES = {"omitted_table_field"}

NOT_EMITTED_ENTRY_POINTS = {
    "strategy_title": ('//@version=6\nstrategy({s})\n' + TRADE,
                       "strategy titles are not emitted"),
    "strategy_shorttitle": ('//@version=6\nstrategy("t", shorttitle={s})\n' + TRADE,
                            "strategy short titles are not emitted"),
    "input_tooltip_group_inline": (
        PRELUDE + 'n = input.int(5, "Length", tooltip={s}, group={s}, inline={s})\n'
        'if close > ta.sma(close, n)\n    strategy.entry("L", strategy.long)\n',
        "visual input metadata is not emitted"),
    "financial_period": (
        PRELUDE + 'x = request.financial(syminfo.tickerid, "TOTAL_REVENUE", {s})\n'
        'if x > 0\n    strategy.entry("L", strategy.long)\n',
        "unrecognized financial periods defer refusal without emitting their value"),
    "plot_titles": (PRELUDE + 'plot(close, {s})\nhline(1.0, {s})\n'
                    'plotshape(close > open, {s}, text={s})\n' + TRADE,
                    "plot titles and visual text are not emitted"),
}


def _source(name: str, value: str) -> str:
    template, _benign = ENTRY_POINTS[name]
    return template.replace("{s}", pine_string_literal(value))


@pytest.mark.parametrize("name", ENTRY_POINTS)
def test_entry_point_transpiles_with_a_benign_string(name):
    _template, benign = ENTRY_POINTS[name]
    cpp = transpile(_source(name, benign))
    assert_inert(cpp, MARKER)
    if name in COMMENT_SITES:
        assert any(benign in text for kind, _start, _end, text in regions(cpp)
                   if kind == "comment")
    elif name == "financial_id":
        assert any(f"|{benign}|FQ|" in value for value in string_values(cpp))
    elif name == "lower_tf_timeframe_constant":
        (registration,) = [line.strip() for line in cpp.splitlines()
                           if line.strip().startswith("register_security_lower_tf_eval(")]
        assert string_values(registration) == [benign]
    else:
        assert benign in string_values(cpp)
    if name == "pf_trace_expression":
        (trace_line,) = [line for line in cpp.split("\n") if 'trace(std::string("probe"),' in line]
        assert benign in string_values(trace_line)


@pytest.mark.parametrize("sample", SAMPLES.values(), ids=SAMPLES.keys())
@pytest.mark.parametrize("name", ENTRY_POINTS)
def test_script_string_never_reaches_the_cpp_as_code(name, sample):
    try:
        cpp = transpile(_source(name, sample))
    except CompileError as exc:
        assert any(MARKER in diagnostic.message for diagnostic in exc.diagnostics
                   if diagnostic.level.name == "ERROR")
        return
    assert MARKER in cpp
    assert "\0" not in cpp
    assert_inert(cpp, MARKER)
    if name not in COMMENT_SITES:
        assert_only_in_strings(cpp, MARKER)
        assert any(sample in value for value in string_values(cpp))


@pytest.mark.parametrize("sample", SAMPLES.values(), ids=SAMPLES.keys())
@pytest.mark.parametrize("name", NOT_EMITTED_ENTRY_POINTS)
def test_non_emitted_string_is_not_counted_as_an_emitter(name, sample):
    template, reason = NOT_EMITTED_ENTRY_POINTS[name]
    cpp = transpile(template.replace("{s}", pine_string_literal(sample)))
    assert MARKER not in cpp, reason
    assert_inert(cpp, MARKER)


@pytest.mark.parametrize("period", ["FQ", "FY", "FH", "TTM"])
def test_valid_financial_period_reaches_the_recorded_key(period):
    template, _reason = NOT_EMITTED_ENTRY_POINTS["financial_period"]
    cpp = transpile(template.replace("{s}", pine_string_literal(period)))
    assert f"|TOTAL_REVENUE|{period}|gaps_off|lookahead_off" in string_values(cpp)


def test_timestamp_date_string_reaches_the_date_parser():
    cpp = transpile(PRELUDE + 'if time > timestamp("2020-01-01")\n'
                    '    strategy.entry("L", strategy.long)\n')
    assert "1577836800000" in cpp
    assert "2020-01-01" not in cpp


@pytest.mark.parametrize("sample", SAMPLES.values(), ids=SAMPLES.keys())
def test_timestamp_date_string_samples_are_refused_by_the_date_parser(sample):
    source = PRELUDE + f"if time > timestamp({pine_string_literal(sample)})\n" + '    strategy.entry("L", strategy.long)\n'
    with pytest.raises(CompileError) as caught:
        transpile(source)
    assert any("timestamp(dateString): could not parse" in diagnostic.message
               and MARKER in diagnostic.message for diagnostic in caught.value.diagnostics)


LIBRARY = '//@version=6\n// @description strings\nlibrary("Strings")\n\nexport label() => {s}\n'


@pytest.mark.parametrize("sample", SAMPLES.values(), ids=SAMPLES.keys())
def test_library_string_never_reaches_the_cpp_as_code(sample):
    source = (PRELUDE + "import pftest/Strings/1 as S\n"
              "if str.length(S.label()) > 0\n    strategy.entry(\"L\", strategy.long)\n")
    if "\r" in sample:
        literal = '"""' + sample + '"""'
        expected = sample.replace("\r\n", "\n").replace("\r", "\n")
    else:
        literal = pine_string_literal(sample)
        expected = sample
    library = LIBRARY.replace("{s}", literal)
    cpp = transpile(source, libraries={"pftest/Strings/1": library})
    assert "\0" not in cpp
    assert_inert(cpp, MARKER)
    assert_only_in_strings(cpp, MARKER)
    assert expected in string_values(cpp)


def test_library_entry_point_transpiles_with_a_benign_string():
    source = (PRELUDE + "import pftest/Strings/1 as S\n"
              "if str.length(S.label()) > 0\n    strategy.entry(\"L\", strategy.long)\n")
    cpp = transpile(source, libraries={"pftest/Strings/1": LIBRARY.replace("{s}", '"benign"')})
    assert_inert(cpp, MARKER)
    assert 'std::string("benign")' in cpp


def test_omitted_field_placeholder_comment_keeps_the_receiver_inside():
    # A table field has no C++ member: its assignment becomes a comment
    # naming the receiver, whose rendering holds the script's string.
    cpp = transpile(_source("omitted_table_field", "a*/ PFSW /*"))
    assert_inert(cpp, MARKER)
    assert ('/* drawing field assignment omitted: f(std::string("a* / PFSW / *")).t := ... */'
            in cpp)
    cpp = transpile(_source("omitted_table_field", "abc"))
    assert '/* drawing field assignment omitted: f(std::string("abc")).t := ... */' in cpp


# Values the transpiler validates before they reach a C++ string literal --
# a pf-trace name, a footprint column, a helper series key, a recorded key --
# are escaped there too: with the validation bypassed, a sample value stays
# inside its literal.

SAMPLE_VALUE = f'x" {MARKER} "\\'


@pytest.mark.parametrize("sample", SAMPLES.values(), ids=SAMPLES.keys())
def test_pf_trace_literal_expression_reaches_its_emitter(sample, monkeypatch):
    import pineforge_codegen
    from pineforge_codegen.pragmas import extract_pf_trace_pragmas
    (pragma,) = extract_pf_trace_pragmas('// @pf-trace probe=str.length("benign")\n')
    pragma.expr_node.args[0].value = sample
    monkeypatch.setattr(pineforge_codegen, "extract_pf_trace_pragmas",
                        lambda *args, **kwargs: [pragma])
    cpp = transpile(PRELUDE + TRADE)
    (trace_line,) = [line for line in cpp.split("\n") if 'trace(std::string("probe"),' in line]
    assert "\0" not in trace_line
    assert_inert(trace_line, MARKER)
    assert_only_in_strings(trace_line, MARKER)
    assert sample in string_values(trace_line)


def test_pf_trace_name_is_escaped(monkeypatch):
    import re
    from pineforge_codegen import pragmas
    monkeypatch.setattr(pragmas, "_PRAGMA_RE",
                        re.compile(r"^\s*//\s+@pf-trace\s+(\S+?)\s*=\s*(.+?)\s*$"))
    cpp = transpile(PRELUDE + '// @pf-trace a"PFSW"b=close\n' + TRADE)
    assert_inert(cpp, MARKER)
    assert 'trace(std::string("a\\"PFSW\\"b"), ' in cpp


def test_footprint_column_is_escaped(monkeypatch):
    from pineforge_codegen import support_checker
    monkeypatch.setattr(support_checker, "footprint_column", lambda node: SAMPLE_VALUE)
    cpp = transpile(PRELUDE + 'fp = request.security("BINANCE:BTCUSDT", "60", '
                    'request.footprint(100, 70))\nif fp.delta() > 0\n'
                    '    strategy.entry("L", strategy.long)\n')
    assert_inert(cpp, MARKER)
    assert cpp.count('"x\\" PFSW \\"\\\\"') >= 2


def _emitter(**attrs):
    from types import SimpleNamespace
    from pineforge_codegen.codegen.helpers import NamingHelper
    return SimpleNamespace(_cpp_string_escape=NamingHelper._cpp_string_escape, **attrs)


def test_helper_series_key_is_escaped():
    from pineforge_codegen.codegen.security import SecurityEmitter
    for strings in (set(), {SAMPLE_VALUE}):
        ref = SecurityEmitter._security_helper_series_ref(
            _emitter(_security_string_series=strings), SAMPLE_VALUE)
        assert_inert(f"double v = {ref};", MARKER)
        assert_only_in_strings(ref, MARKER)


def test_recorded_key_is_escaped():
    from types import SimpleNamespace
    from pineforge_codegen.codegen.security import SecurityEmitter
    from pineforge_codegen.external_requests import RECORDED_KEY_ANNOTATION
    request = SimpleNamespace(args=["sym"], annotations={RECORDED_KEY_ANNOTATION: {
        "fn": SAMPLE_VALUE, "field": SAMPLE_VALUE, "period": SAMPLE_VALUE, "gaps": "off",
        "lookahead": "off"}})
    key = SecurityEmitter._recorded_key_expr(
        _emitter(_visit_expr=lambda node: "syminfo_.tickerid"), request)
    assert_inert(f"auto k = {key};", MARKER)
    assert_only_in_strings(key, MARKER)


@pytest.mark.parametrize("namespace", ["currency", "SampleEnum"])
def test_member_string_is_escaped_at_the_emitter(namespace, monkeypatch):
    from pineforge_codegen.analyzer import Analyzer
    from pineforge_codegen.ast_nodes import Identifier, MemberAccess
    from pineforge_codegen.codegen import CodeGen
    from pineforge_codegen.lexer import Lexer
    from pineforge_codegen.parser import Parser
    program = Parser(Lexer(PRELUDE).tokenize(), source=PRELUDE).parse()
    emitter = CodeGen(Analyzer(program).analyze())
    monkeypatch.setattr(emitter, "_visit_expr", lambda node: namespace)
    value = emitter._visit_member_access(
        MemberAccess(object=Identifier(name=namespace), member=SAMPLE_VALUE))
    assert_inert(value, MARKER)
    assert_only_in_strings(value, MARKER)
    assert string_values(value) == [SAMPLE_VALUE]


@pytest.mark.parametrize("suffix", ["", "0", "1", "7", "8", "9", "123", "000"])
def test_nul_uses_a_three_digit_octal_escape(suffix):
    from pineforge_codegen.codegen.helpers import NamingHelper
    sample = "x\0" + suffix + MARKER
    escaped = NamingHelper._cpp_string_escape(sample)
    assert escaped == "x\\000" + suffix + MARKER
    assert "\0" not in escaped
    assert string_values(f'"{escaped}"') == [sample]


def test_nul_literal_compiles_without_a_raw_nul():
    from pineforge_codegen.codegen.helpers import NamingHelper
    from tests._compile import compile_cpp
    escaped = NamingHelper._cpp_string_escape("x\0" + "7PFSW")
    cpp = (f'constexpr char value[] = "{escaped}";\n'
           'static_assert(sizeof(value) == 8);\n'
           'static_assert(value[1] == 0 && value[2] == \'7\' && value[3] == \'P\');\n')
    compile_cpp(cpp, label="NUL literal", extra_flags=("-Wall", "-Werror"))
