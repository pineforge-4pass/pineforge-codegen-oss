"""Hostile authored text stays data, and weakening its protection goes red.

Every row runs the public transpiler with support checking enabled. Controls
run the same assertion body after replacing exactly one escaper or mangler;
only an assertion about the resulting C++ can satisfy a control.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from pineforge_codegen.codegen.helpers import NamingHelper
from pineforge_codegen.codegen.input import InputHelper
from pineforge_codegen.cpp_allowlist import check_generated_cpp

from tests._cpp_tokens import code_only, is_allowlist_refusal, regions, string_values


PRELUDE = '//@version=6\nstrategy("Text boundaries")\n'
TRADE = 'if close > open\n    strategy.entry("Long", strategy.long)\n'

# Sixty-four adjacent 64-byte blocks plus one quote give 4097 authored bytes.
LONG_VALUE = (
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    b'"'
)


@dataclass(frozen=True)
class Text:
    name: str
    pine: str
    expected: bytes
    comment_expected: bytes
    library_expected: bytes | None = None


TEXTS = (
    # Pine's single quotes leave the double quotes and punctuation as data.
    Text("literal_close", "'edge\"; system(\"inert\"); //'",
         b'edge"; system("inert"); //', b'edge"; system("inert"); //'),
    # Both block delimiters stay literal bytes; a placeholder separates them.
    Text("comment_open", "'edge*/ system(\"inert\"); /*tail'",
         b'edge*/ system("inert"); /*tail', b'edge* / system("inert"); / *tail'),
    # Pine's two newline escapes become two LF bytes around the directive text.
    Text("preprocessor_line", "'edge\\n#pragma once\\n\"'",
         b'edge\n#pragma once\n"', b'edge\n#pragma once\n"'),
    # Triple quotes retain LF, lone CR and Unicode separators; libraries fold CR.
    Text("line_boundaries", "'''a\nb\rc\u2028d\u2029e\u0085f\"'''",
         b'a\nb\rc\xe2\x80\xa8d\xe2\x80\xa9e\xc2\x85f"',
         b'a\nb\rc\xe2\x80\xa8d\xe2\x80\xa9e\xc2\x85f"',
         b'a\nb\nc\xe2\x80\xa8d\xe2\x80\xa9e\xc2\x85f"'),
    # Pine consumes each doubled backslash once, including the final pair.
    Text("trailing_backslash", r"""'edge\\"tail\\'""",
         b'edge\\"tail\\', b'edge\\"tail\\'),
    # Triple apostrophes retain the raw delimiter and the isolated apostrophe.
    Text("raw_and_character", """'''edge)tag"; R"tag(body)tag"; 'x'''""",
         b"""edge)tag"; R"tag(body)tag"; 'x""",
         b"""edge)tag"; R"tag(body)tag"; 'x"""),
    # The doubled Pine backslash leaves UCN-looking text as six ordinary bytes.
    Text("alternate_spellings", r"""'edge??=??/<:%:>\\u0041"'""",
         b'edge??=??/<:%:>\\u0041"', b'edge??=??/<:%:>\\u0041"'),
    # A literal NUL followed by 7 must decode as two bytes, never octal 07.
    Text("nul_digit", "'edge\0" + "7\"'", b'edge\x007"', b'edge\x007"'),
    # Call and keyword spellings inside a string remain the complete text.
    Text("reserved_spellings", "'system(\"inert\"); namespace'",
         b'system("inert"); namespace', b'system("inert"); namespace'),
    # The quote and following suffix belong to the value, not a literal suffix.
    Text("token_suffix", "'edge\"_suffix'", b'edge"_suffix', b'edge"_suffix'),
    # Triple apostrophes need no quoting changes for these 4097 literal bytes.
    Text("long_text", "'''" + LONG_VALUE.decode("ascii") + "'''",
         LONG_VALUE, LONG_VALUE),
)


@dataclass(frozen=True)
class Channel:
    name: str
    body: str
    focus: str = ""
    library: bool = False
    receipt: str = ""
    field: str = ""
    prefix: bytes = b""
    suffix: bytes = b""
    declaration: str = ""


# Each script has one hostile argument, so a control cannot die at another
# channel's hostile argument. Fixed strings are distinct from every Text value.
CHANNELS = (
    Channel("literal", 'log.info({text})\n'),
    Channel("constant", 'message = {text}\nlog.info(message)\n'),
    Channel("persistent", 'var string message = {text}\nlog.info(message)\n'),
    Channel("function_default",
            'message(string text = {text}) => text\nlog.info(message())\n'),
    Channel("record_default",
            'type Record\n    string text = {text}\n'
            'record = Record.new()\nlog.info(record.text)\n'),
    Channel("array_element", 'texts = array.from({text}, "fixed")\n'),
    Channel("map_key", 'var values = map.new<string, float>()\n'
            'values.put({text}, close)\n'),
    Channel("switch_arm", 'value = switch syminfo.type\n'
            '    {text} => 1\n    => 2\n'),
    Channel("enum_title", 'enum Choice\n    first = {text}\n    second = "fixed"\n'
            'choice = input.enum(Choice.first, "Choice")\n'
            'log.info(str.tostring(choice))\n'),
    Channel("input_title", 'length = input.int(3, {text})\n'),
    Channel("generic_input_title", 'length = input(3, title={text})\n'),
    Channel("input_title_constant", 'TITLE = {text}\nlength = input.int(3, TITLE)\n',
            focus="get_input_int("),
    # A constant title concatenation adds these four fixed bytes before the value.
    Channel("input_title_composed", 'length = input.int(3, "Key " + {text})\n',
            focus="get_input_int(", prefix=b"Key "),
    Channel("inline_input_title", 'average = ta.sma(close, input.int(3, {text}))\n'),
    Channel("input_default", 'text = input.string({text}, "Text")\n'),
    Channel("generic_input_default", 'text = input({text}, "Text")\n'),
    Channel("input_option", 'text = input.string("fixed", "Text", '
            'options=["fixed", {text}])\n'),
    Channel("text_area", 'text = input.text_area({text}, "Text")\n'),
    Channel("symbol_default", 'symbol = input.symbol({text}, "Symbol")\n'),
    Channel("session_default", 'sessionValue = input.session({text}, "Session")\n'),
    Channel("timeframe_default", 'timeframeValue = input.timeframe({text}, "TF")\n'),
    Channel("security_symbol", 'value = request.security({text}, "60", close)\n'
            'if value > open\n    strategy.entry("Request", strategy.long)\n'),
    Channel("security_payload", 'value = request.security(syminfo.tickerid, "60", '
            '{text} + str.tostring(close))\nlog.info(value)\n'),
    Channel("ticker_symbol", 'log.info(ticker.standard({text}))\n'),
    Channel("symbol_prefix", 'log.info(syminfo.prefix({text}))\n'),
    Channel("symbol_ticker", 'log.info(syminfo.ticker({text}))\n'),
    Channel("time_timeframe", 'value = time({text})\n'),
    Channel("time_session", 'value = time("D", {text}, "UTC")\n'),
    Channel("time_timezone", 'value = time("D", "0000-0000", {text})\n'),
    Channel("time_close_timeframe", 'value = time_close({text})\n'),
    Channel("time_close_session", 'value = time_close("D", {text}, "UTC")\n'),
    Channel("time_close_timezone", 'value = time_close("D", "0000-0000", {text})\n'),
    Channel("timeframe_change", 'value = timeframe.change({text})\n'),
    Channel("timeframe_seconds", 'value = timeframe.in_seconds({text})\n'),
    Channel("calendar_timezone", 'value = hour(time, {text})\n'),
    Channel("timestamp_timezone", 'value = timestamp({text}, 2020, 1, 1, 0, 0)\n'),
    Channel("format_pattern", 'log.info(str.format({text}, close))\n'),
    Channel("time_format_pattern", 'log.info(str.format_time(time, {text}, "UTC"))\n'),
    Channel("time_format_timezone",
            'log.info(str.format_time(time, "yyyy", {text}))\n'),
    Channel("number_format", 'log.info(str.tostring(close, {text}))\n'),
    Channel("string_argument", 'log.info(str.replace_all(syminfo.ticker, {text}, "fixed"))\n'),
    Channel("order_id", 'strategy.entry({text}, strategy.long)\n'),
    Channel("order_comment", 'strategy.entry("Entry", strategy.long, comment={text})\n'),
    Channel("order_group", 'strategy.entry("Entry", strategy.long, '
            'oca_name={text}, oca_type=strategy.oca.cancel)\n'),
    Channel("exit_id", 'strategy.exit({text}, "Entry", profit=10)\n'),
    Channel("exit_from", 'strategy.exit("Exit", {text}, profit=10)\n'),
    Channel("exit_comment", 'strategy.exit("Exit", "Entry", profit=10, comment={text})\n'),
    Channel("exit_group", 'strategy.exit("Exit", "Entry", profit=10, oca_name={text})\n'),
    Channel("close_id", 'strategy.close({text})\n'),
    Channel("close_comment", 'strategy.close("Entry", comment={text})\n'),
    Channel("close_all_comment", 'strategy.close_all(comment={text})\n'),
    Channel("cancel_id", 'strategy.cancel({text})\n'),
    Channel("market_order_id", 'strategy.order({text}, strategy.long, 1)\n'),
    Channel("market_order_group", 'strategy.order("Entry", strategy.long, 1, '
            'oca_name={text}, oca_type=strategy.oca.cancel)\n'),
    Channel("log_warning", 'log.warning({text})\n'),
    Channel("log_error", 'log.error({text}, close)\n'),
    Channel("runtime_message", 'if bar_index < 0\n    runtime.error(message={text})\n'),
    Channel("label_text", 'label.new(bar_index, close, {text})\n'),
    Channel("label_update", 'labelValue = label.new(bar_index, close, "fixed")\n'
            'label.set_text(labelValue, {text})\n'),
    Channel("label_tooltip", 'labelValue = label.new(bar_index, close, "fixed")\n'
            'label.set_tooltip(labelValue, {text})\n'),
    Channel("box_text", 'boxValue = box.new(bar_index, high, bar_index + 1, low)\n'
            'box.set_text(boxValue, {text})\n'),
    Channel("trace_expression", 'message = {text}\n'
            '// @pf-trace probe=str.length(message)\n', focus='trace(std::string("probe"),'),
    Channel("library_body", 'import texttest/TextModule/1 as TextModule\n'
            'log.info(TextModule.message())\n', library=True),
    Channel("currency_from", 'value = request.currency_rate({text}, "USD")\n'),
    Channel("currency_to", 'value = request.currency_rate("USD", {text})\n'),
    Channel("earnings_symbol", 'value = request.earnings({text})\n'
            'if value > open\n    strategy.entry("Request", strategy.long)\n'),
    Channel("financial_symbol",
            'value = request.financial({text}, "TOTAL_REVENUE", "FQ")\n'
            'if value > open\n    strategy.entry("Request", strategy.long)\n'),
    # The stop's call spelling includes its first two arguments and an ellipsis.
    Channel("financial_id", 'value = request.financial(syminfo.tickerid, {text}, "FQ")\n'
            'if value > open\n    strategy.entry("Request", strategy.long)\n',
            prefix=b'request.financial(syminfo.tickerid, "', suffix=b'", ...)'),
    Channel("capabilities_symbol", 'value = request.security({text}, "60", close)\n'
            'if value > open\n    strategy.entry("Request", strategy.long)\n',
            receipt="strategy_capabilities_receipt", field="symbol"),
    Channel("confirmed_expression",
            'value = request.security(syminfo.tickerid, "60", {text})\nlog.info(value)\n',
            receipt="strategy_confirmed_bar_receipt", field="expression"),
    Channel("declaration_currency", "", receipt="strategy_capabilities_receipt",
            field="currency",
            declaration='//@version=6\nstrategy("Text boundaries", currency={text})\n'),
)


def _assert_clean(cpp: str, name: str) -> None:
    findings = check_generated_cpp(cpp)
    assert findings == [], f"{name}: generated C++ has allowlist findings: {findings}"


def _text_case(channel: Channel, text: Text) -> None:
    name = f"{channel.name}-{text.name}"
    template = (channel.declaration or PRELUDE) + channel.body
    source = template.replace("{text}", text.pine) + TRADE
    libraries = None
    expected = text.expected
    if channel.library:
        libraries = {
            "texttest/TextModule/1": '//@version=6\nlibrary("TextModule")\n'
            'export message() => ' + text.pine + '\n',
        }
        if text.library_expected is not None:
            expected = text.library_expected
    cpp = transpile(source, libraries=libraries)
    if channel.receipt:
        _assert_receipt_text(cpp, channel, text, name)
        if not channel.declaration:
            decoded = [value.encode("utf-8") for value in string_values(cpp)]
            assert text.expected in decoded, f"{name}: decoded runtime request text changed"
        _assert_clean(cpp, name)
        return
    selected = cpp
    if channel.focus:
        selected = "\n".join(line for line in cpp.split("\n") if channel.focus in line)
        assert selected, f"{name}: emitted text context is missing"
    decoded = [value.encode("utf-8") for value in string_values(selected)]
    assert channel.prefix + expected + channel.suffix in decoded, (
        f"{name}: decoded authored literal is missing or changed"
    )
    _assert_clean(cpp, name)


def _assert_receipt_text(cpp: str, channel: Channel, text: Text, name: str) -> None:
    definition = "int " + channel.receipt + "("
    assert definition in cpp, f"{name}: emitted receipt is missing"
    body = cpp.split(definition, 1)[1].split("\n    }", 1)[0]
    documents = [value for value in string_values(body) if value.startswith("{")]
    assert len(documents) == 1, f"{name}: decoded receipt literal is missing or split"
    try:
        document = json.loads(documents[0])
    except json.JSONDecodeError as error:
        raise AssertionError(f"{name}: decoded receipt is not JSON") from error
    if channel.declaration:
        declarations = document.get("declarations", {})
        value = declarations.get(channel.field)
        assert isinstance(value, str) and value.encode("utf-8") == text.expected, (
            f"{name}: decoded declaration changed the authored text"
        )
        return
    requests = document.get("requests")
    assert isinstance(requests, list) and len(requests) == 1, (
        f"{name}: decoded receipt request is missing or duplicated"
    )
    value = requests[0].get(channel.field)
    assert isinstance(value, str) and value.encode("utf-8") == text.expected, (
        f"{name}: decoded receipt changed the authored text"
    )


def _expect_case_failure(case_body, name: str) -> None:
    # A parser exception or an internal AssertionError cannot count as a kill,
    # with one exception: the emitter's own duplicate script-state member
    # invariant is the defence that catches a weakened name allocator, so it
    # counts as the kill for the name-collision case only. The translation
    # pipeline itself now refuses generated C++ that has allowlist findings;
    # that refusal (and only that: every diagnostic one of the allowlist's
    # fixed reasons) is the same defence firing earlier, so it counts as the
    # kill for every case. The ASCII invariant of the emitter's last pass (a
    # fixed-message AssertionError) is likewise the same defence firing later.
    pattern = re.escape(name) + r": (generated C\+\+|decoded|emitted)"
    if name == "name_collision":
        pattern += r"|duplicate generated script-state member: pf_safe_"
    try:
        case_body()
    except CompileError as error:
        assert is_allowlist_refusal(error), f"{name}: unrelated compile error {error}"
        return
    except AssertionError as error:
        # The emitter's final ASCII check also kills a weakened escaper that
        # lets a non-ASCII code point through (its message is fixed).
        assert (re.search(pattern, str(error))
                or re.fullmatch(r"non-ASCII C\+\+ code point U\+[0-9A-F]{4} at line [0-9]+", str(error))
                ), f"{name}: unrelated assertion {error}"
        return
    pytest.fail(f"{name}: the weakened control was not killed")


def _paired_control(monkeypatch, owner, method, weakened, case_body, name: str) -> None:
    case_body()
    with monkeypatch.context() as patch:
        patch.setattr(owner, method, weakened)
        _expect_case_failure(case_body, name)


@pytest.mark.parametrize("channel", CHANNELS, ids=lambda row: row.name)
@pytest.mark.parametrize("text", TEXTS, ids=lambda row: row.name)
def test_string_channel_inertness(monkeypatch, channel, text):
    """The channel's weakened escaper must kill this same authored class."""
    if channel.receipt:
        _paired_control(
            monkeypatch, json, "dumps", _weaken_json_string_escape,
            lambda: _text_case(channel, text), f"{channel.name}-{text.name}",
        )
        return
    _paired_control(
        monkeypatch, NamingHelper, "_cpp_string_escape", staticmethod(lambda value: value),
        lambda: _text_case(channel, text), f"{channel.name}-{text.name}",
    )


JSON_ESCAPE = json.dumps


def _weaken_json_string_escape(value, *args, **kwargs):
    encoded = JSON_ESCAPE(value, *args, **kwargs)
    # Drop nested quote protection in strings; ordinary JSON keys stay escaped.
    return encoded.replace(r'\\\"', r'\"') if isinstance(value, str) else encoded


@pytest.mark.parametrize("channel", tuple(row for row in CHANNELS if row.receipt and not row.declaration),
                         ids=lambda row: row.name)
@pytest.mark.parametrize("text", TEXTS, ids=lambda row: row.name)
def test_receipt_runtime_escape_control_kills_string_channel(monkeypatch, channel, text):
    _paired_control(
        monkeypatch, NamingHelper, "_cpp_string_escape", staticmethod(lambda value: value),
        lambda: _text_case(channel, text), f"{channel.name}-{text.name}",
    )


@pytest.mark.parametrize("text", TEXTS, ids=lambda row: row.name)
def test_input_key_control_kills_string_channel(monkeypatch, text):
    channel = next(row for row in CHANNELS if row.name == "input_title")
    _paired_control(
        monkeypatch, InputHelper, "_input_key_literal", lambda self, title: '"' + title + '"',
        lambda: _text_case(channel, text), f"{channel.name}-{text.name}",
    )


def _comment_case(text: Text) -> None:
    name = f"placeholder-{text.name}"
    source = (
        PRELUDE + 'type Record\n    table view\n    float value\n'
        'record(string text) => Record.new(na, str.length(text))\n'
        'record("*/Guard" + ' + text.pine + ').view := na\n' + TRADE
    )
    cpp = transpile(source)
    comments = [value for kind, _start, _end, value in regions(cpp)
                if kind == "comment" and value.startswith("/* drawing field assignment omitted:")]
    assert len(comments) == 1, f"{name}: emitted placeholder is missing or split"
    comment = comments[0]
    assert comment.endswith(".view := ... */"), f"{name}: emitted placeholder ended early"
    decoded = [value.encode("utf-8") for value in string_values(comment[2:-2])]
    # The receiver has two literal operands; only the comment delimiters change.
    assert decoded == [b"* /Guard", text.comment_expected], f"{name}: decoded comment text changed"
    _assert_clean(cpp, name)


@pytest.mark.parametrize("text", TEXTS, ids=lambda row: row.name)
@pytest.mark.parametrize("method", ("_cpp_comment_escape", "_cpp_string_escape"),
                         ids=("comment_escape", "string_escape"))
def test_comment_channel_inertness(monkeypatch, text, method):
    _paired_control(
        monkeypatch, NamingHelper, method, staticmethod(lambda value: value),
        lambda: _comment_case(text), f"placeholder-{text.name}",
    )


# These controls each remove only one rule, preserving all the other escapes.
STRING_ESCAPE = NamingHelper._cpp_string_escape


@pytest.mark.parametrize(
    "text_name,escaped,raw",
    (
        # Removing quote protection must expose the authored closing quote.
        ("literal_close", '\\"', '"'),
        # Removing LF protection must expose the two authored line endings.
        ("preprocessor_line", "\\n", "\n"),
        # Removing CR protection must expose the authored lone carriage return.
        ("line_boundaries", "\\r", "\r"),
        # Removing slash doubling must change the authored final backslash.
        ("trailing_backslash", "\\\\", "\\"),
        # Shortening a NUL escape lets the following 7 join the octal number.
        ("nul_digit", "\\000", "\\0"),
    ),
    ids=("quote_rule", "newline_rule", "carriage_return_rule", "backslash_rule", "nul_width_rule"),
)
def test_string_escape_rule_control_kills_named_case(monkeypatch, text_name, escaped, raw):
    text = next(row for row in TEXTS if row.name == text_name)
    channel = next(row for row in CHANNELS if row.name == "literal")
    _paired_control(
        monkeypatch, NamingHelper, "_cpp_string_escape",
        staticmethod(lambda value: STRING_ESCAPE(value).replace(escaped, raw)),
        lambda: _text_case(channel, text), f"{channel.name}-{text.name}",
    )


@dataclass(frozen=True)
class Name:
    name: str
    authored: str
    expected: str
    enum_type: str
    enum_member: str


NAMES = (
    # A process primitive gets the nonlegacy safe prefix in every binding.
    Name("process_name", "system", "pf_safe_system",
         "pf_safe_system_member", "Choice_pf_safe_system"),
    # A memory primitive gets the same prefix, even without call syntax.
    Name("memory_name", "free", "pf_safe_free",
         "pf_safe_free_member", "Choice_pf_safe_free"),
    # A C++ keyword outside the legacy set takes the safe prefix.
    Name("reserved_keyword", "namespace", "pf_safe_namespace",
         "pf_safe_namespace_member", "Choice_pf_safe_namespace"),
    # A legacy keyword keeps its historical surrounding underscores.
    Name("legacy_keyword", "class", "_class_",
         "_class__member", "Choice__class_"),
    # A trailing underscore still belongs to one escaped authored token.
    Name("token_suffix", "__pf_array_edge_", "pf_safe___pf_array_edge_",
         "pf_safe___pf_array_edge__member", "Choice_pf_safe___pf_array_edge_"),
    # A long temporary-shaped token retains every authored letter in its escape.
    Name("long_identifier",
         "__pf_array_abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz",
         "pf_safe___pf_array_abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz",
         "pf_safe___pf_array_abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz_member",
         "Choice_pf_safe___pf_array_abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz"),
)

NAME_CHANNELS = {
    "variable": "{name} = close\nvalue = {name} + open\n",
    "persistent": "var float {name} = na\n{name} := close\nvalue = {name}\n",
    "history": "{name} = close\nvalue = {name}[1]\n",
    "parameter": "calculate(float {name}) => {name} + 1.0\nvalue = calculate(close)\n",
    "method_parameter": "method calculate(float {name}) => {name} + 1.0\n"
                        "value = close.calculate()\n",
    "function": "{name}(float value) => value + 1.0\nvalue = {name}(close)\n",
    "record_type": "type {name}\n    float value\nrecord = {name}.new(close)\n"
                   "value = record.value\n",
    "record_field": "type Record\n    float {name}\nrecord = Record.new(close)\n"
                    "value = record.{name}\n",
    "enum_type": 'enum {name}\n    member\nchoice = input.enum({name}.member, "Choice")\n',
    "enum_member": 'enum Choice\n    {name}\nchoice = input.enum(Choice.{name}, "Choice")\n',
    "loop_binding": "value = 0.0\nfor {name} = 0 to 2\n    value += {name}\n",
    "array_binding": "value = 0.0\nfor {name} in array.from(close, open)\n"
                     "    value += {name}\n",
    "index_binding": "value = 0.0\nfor [{name}, item] in array.from(close, open)\n"
                     "    value += {name} + item\n",
    "tuple_binding": "[{name}, middle, last] = ta.macd(close, 2, 3, 2)\nvalue = {name}\n",
}


def _name_case(channel: str, row: Name) -> None:
    name = f"{channel}-{row.name}"
    cpp = transpile(PRELUDE + NAME_CHANNELS[channel].replace("{name}", row.authored) + TRADE)
    expected = row.enum_type if channel == "enum_type" else (
        row.enum_member if channel == "enum_member" else row.expected)
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code_only(cpp))
    assert expected in tokens, f"{name}: emitted authored binding is missing or merged"
    _assert_clean(cpp, name)


@pytest.mark.parametrize("channel", NAME_CHANNELS)
@pytest.mark.parametrize("row", NAMES, ids=lambda row: row.name)
def test_identifier_channel_inertness(monkeypatch, channel, row):
    _paired_control(
        monkeypatch, NamingHelper, "_safe_name", lambda self, value: value,
        lambda: _name_case(channel, row), f"{channel}-{row.name}",
    )


@pytest.mark.parametrize("row", NAMES, ids=lambda row: row.name)
def test_function_mangler_control_kills_identifier_channel(monkeypatch, row):
    _paired_control(
        monkeypatch, NamingHelper, "_func_safe_name", lambda self, value: value,
        lambda: _name_case("function", row), f"function-{row.name}",
    )


def _function_collision_case() -> None:
    name = "function_collision"
    cpp = transpile(
        PRELUDE + "var float calculate = 0.0\n"
        "calculate(float value) => value + 1.0\n"
        "value = calculate(close)\n" + TRADE
    )
    # One declaration and one call must use the function's separate spelling.
    assert len(re.findall(r"\b_fn_calculate\s*\(", code_only(cpp))) == 2, (
        f"{name}: emitted function and value bindings were not kept distinct"
    )
    _assert_clean(cpp, name)


def test_function_collision_control_kills_named_case(monkeypatch):
    _paired_control(
        monkeypatch, NamingHelper, "_func_safe_name",
        lambda self, value: self._safe_name(value),
        _function_collision_case, "function_collision",
    )


def _name_collision_case() -> None:
    name = "name_collision"
    cpp = transpile(
        PRELUDE + "namespace = close\npf_safe_namespace = open\n"
        "value = namespace + pf_safe_namespace\n" + TRADE
    )
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code_only(cpp))
    # The authored safe spelling occupies the first choice, so escape number 2 is used.
    assert "pf_safe_namespace_2" in tokens, f"{name}: emitted bindings share one spelling"
    assert "pf_safe_namespace" in tokens, f"{name}: emitted authored safe spelling is missing"
    _assert_clean(cpp, name)


def _allocate_without_collision_check(self, name):
    self._safe_name_map[name] = "pf_safe_" + name
    return self._safe_name_map[name]


INITIALISE_SAFE_NAMES = NamingHelper._initialise_safe_names


def _initialise_without_authored_reservations(self, ast):
    INITIALISE_SAFE_NAMES(self, ast)
    # Keep the binding inventory and required attributes, but forget occupied spellings.
    self._safe_name_map.clear()
    self._safe_name_occupied.clear()


@pytest.mark.parametrize(
    "method,weakened",
    (
        ("_initialise_safe_names", _initialise_without_authored_reservations),
        ("_allocate_safe_name", _allocate_without_collision_check),
    ),
    ids=("name_inventory", "name_allocation"),
)
def test_name_collision_control_kills_named_case(monkeypatch, method, weakened):
    _paired_control(
        monkeypatch, NamingHelper, method, weakened,
        _name_collision_case, "name_collision",
    )


@pytest.mark.parametrize(
    "case_name,method_name,member,expected",
    (
        # A method and a value of its generated spelling require separate names.
        ("process_name", "system", "_udt_float_system", "_fn__udt_float_system"),
        # The keyword remains inside the method token, with a separate value binding.
        ("reserved_keyword", "namespace", "_udt_float_namespace", "_fn__udt_float_namespace"),
        # An authored trailing underscore stays before the emitted call parenthesis.
        ("token_suffix", "Text_", "_udt_float_Text_", "_fn__udt_float_Text_"),
        # A long method component survives the receiver prefix without truncation.
        ("long_identifier",
         "Textabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz",
         "_udt_float_Textabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz",
         "_fn__udt_float_Textabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz"),
    ),
    ids=("process_name", "reserved_keyword", "token_suffix", "long_identifier"),
)
def test_method_name_channel_inertness(monkeypatch, case_name, method_name, member, expected):
    def case_body():
        name = f"method_name-{case_name}"
        cpp = transpile(
            PRELUDE + "var float " + member + " = 0.0\n"
            "method " + method_name + "(float value) => value + 1.0\n"
            "value = close." + method_name + "()\n" + TRADE
        )
        # The mangled method occurs once as a definition and once as a call.
        assert len(re.findall(r"\b" + re.escape(expected) + r"\s*\(", code_only(cpp))) == 2, (
            f"{name}: emitted method and value bindings were not kept distinct"
        )
        _assert_clean(cpp, name)

    _paired_control(
        monkeypatch, NamingHelper, "_func_safe_name",
        lambda self, value: self._safe_name(value),
        case_body, f"method_name-{case_name}",
    )


@pytest.mark.parametrize(
    "case_name,library_name,expected",
    (
        # A libc spelling is followed by a version and explicit separators.
        ("process_name", "system", "system_v1__message"),
        # A keyword is inert as a proper prefix of one complete identifier.
        ("reserved_keyword", "namespace", "namespace_v1__message"),
        # The authored final underscore precedes the version separator intact.
        ("token_suffix", "Text_", "Text__v1__message"),
        # A long validated library component keeps all its letters.
        ("long_identifier",
         "Textabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz",
         "Textabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz_v1__message"),
    ),
    ids=("process_name", "reserved_keyword", "token_suffix", "long_identifier"),
)
def test_library_name_channel_inertness(monkeypatch, case_name, library_name, expected):
    from pineforge_codegen.library_inline import _Linker

    def case_body():
        name = f"library_name-{case_name}"
        path = "texttest/" + library_name + "/1"
        cpp = transpile(
            PRELUDE + "import " + path + " as TextModule\n"
            "value = TextModule.message(close)\n" + TRADE,
            libraries={path: '//@version=6\nlibrary("Text module")\n'
                       'export message(float value) => value + 1.0\n'},
        )
        # The namespace prefix must occur on both the definition and its call.
        assert len(re.findall(r"\b" + re.escape(expected) + r"\s*\(", code_only(cpp))) == 2, (
            f"{name}: emitted library binding is missing or merged"
        )
        _assert_clean(cpp, name)

    _paired_control(
        monkeypatch, _Linker, "_prefix", lambda self, library: "",
        case_body, f"library_name-{case_name}",
    )


@pytest.mark.parametrize(
    "case_name,parameter,expected",
    (
        # The library's function scope precedes the complete authored parameter.
        ("process_name", "system", "TextModule_v1__message__system"),
        # A keyword stays inside the library-qualified parameter token.
        ("reserved_keyword", "namespace", "TextModule_v1__message__namespace"),
        # The final underscore remains part of the qualified parameter.
        ("token_suffix", "Text_", "TextModule_v1__message__Text_"),
        # A long parameter component is qualified without losing any letters.
        ("long_identifier",
         "Textabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz",
         "TextModule_v1__message__Textabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyz"),
    ),
    ids=("process_name", "reserved_keyword", "token_suffix", "long_identifier"),
)
def test_library_parameter_channel_inertness(monkeypatch, case_name, parameter, expected):
    from pineforge_codegen.library_inline import _Linker

    def case_body():
        name = f"library_parameter-{case_name}"
        cpp = transpile(
            PRELUDE + "import texttest/TextModule/1 as TextModule\n"
            "value = TextModule.message(close)\n" + TRADE,
            libraries={"texttest/TextModule/1": '//@version=6\nlibrary("Text module")\n'
                       'export message(float ' + parameter + ') => ' + parameter + ' + 1.0\n'},
        )
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code_only(cpp))
        # The declaration and expression must both retain their qualified token.
        assert tokens.count(expected) == 2, f"{name}: emitted library parameter is missing or merged"
        _assert_clean(cpp, name)

    _paired_control(
        monkeypatch, _Linker, "_local_name",
        lambda self, module, callable_name, name: name,
        case_body, f"library_parameter-{case_name}",
    )
