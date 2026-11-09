"""Order keywords must bind to the called function's declared parameters."""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("order keywords")\n'

# Each refusal starts with a complete, otherwise valid call. In particular,
# exit has a price parameter, so its separate price requirement cannot pass
# an unknown-keyword test for the wrong reason.
ORDER_CALLS = (
    ("entry", ('"L"', "strategy.long")),
    ("order", ('"O"', "strategy.short", "qty = 2.5")),
    ("exit", ('"X"', '"L"', "stop = low")),
    ("close", ('"L"',)),
    ("close_all", ()),
    ("cancel", ('"L"',)),
    ("cancel_all", ()),
)


def _call(name, arguments):
    return f"strategy.{name}({', '.join(arguments)})"


def _assert_unknown_keyword(
    source, name, keyword, *, check_support=True, removed_when=False,
):
    with pytest.raises(CompileError) as caught:
        transpile(
            source, filename="order_keywords.pine", check_support=check_support,
        )
    errors = [
        diagnostic for diagnostic in caught.value.diagnostics
        if diagnostic.level == Level.ERROR
    ]
    assert len(errors) == 1, caught.value.diagnostics
    diagnostic, = errors
    # Expected text follows the Pine v6 migration rule; generic names retain
    # the existing unknown-parameter contract, independently of the catalog.
    if removed_when:
        assert diagnostic.code == "PF-E2081"
        assert diagnostic.message == (
            f"strategy.{name}: the 'when' argument was removed in Pine v6; "
            "place the order conditionally with an if statement around the call"
        )
        assert diagnostic.hint == (
            'if close > open\n    strategy.entry("L", strategy.long)'
        )
    else:
        assert diagnostic.code == "PF-E2027"
        assert diagnostic.message == (
            f"strategy.{name}: unknown keyword argument '{keyword}'"
        )
        assert diagnostic.hint is None
    assert diagnostic.args == {"key": f"strategy.{name}", "name": keyword}
    start = source.index(f"{keyword} =")
    line = source.count("\n", 0, start) + 1
    col = start - source.rfind("\n", 0, start)
    assert diagnostic.location.file == "order_keywords.pine"
    assert (
        diagnostic.location.line, diagnostic.location.col,
        diagnostic.location.end_col,
    ) == (line, col, col + len(keyword))


@pytest.mark.parametrize("name, arguments", ORDER_CALLS)
@pytest.mark.parametrize("keyword", ("when", "bogus", "misspelled_parameter"))
@pytest.mark.parametrize("value", (
    "true", "false", "bar_index % 20 == 0", "input.bool(false)",
))
@pytest.mark.parametrize("check_support", (True, False))
def test_unknown_order_keywords_are_located_errors(
    name, arguments, keyword, value, check_support,
):
    call = _call(name, (*arguments, f"{keyword} = {value}"))
    _assert_unknown_keyword(
        HEADER + call + "\n", name, keyword, check_support=check_support,
        removed_when=keyword == "when",
    )


@pytest.mark.parametrize("name, arguments", ORDER_CALLS)
@pytest.mark.parametrize("body, check_support", (
    ("{call}\n", True),
    ("{call}\n", False),
    ("if false\n    {call}\n", True),
    ("if false\n    {call}\n", False),
    # With the support checker bypassed, a `when` argument inside the body of
    # a function that nothing calls raises nothing, on main as well, so this
    # body is asked only with the support checker, which checks every body.
    ("unused() =>\n    {call}\n", True),
))
def test_unreachable_and_multiline_order_calls_still_validate_names(
    name, arguments, body, check_support,
):
    call = _call(name, (*arguments, "\n        when = close > open"))
    _assert_unknown_keyword(
        HEADER + body.format(call=call), name, "when",
        check_support=check_support, removed_when=True,
    )


@pytest.mark.parametrize("name, arguments", ORDER_CALLS)
@pytest.mark.parametrize("check_support", (True, False))
def test_misspelled_order_parameter_with_integer_keeps_generic_error(
    name, arguments, check_support,
):
    # An integer expression does not change the unknown-name diagnostic.
    call = _call(name, (*arguments, "misspelled_parameter = 1"))
    _assert_unknown_keyword(
        HEADER + call + "\n", name, "misspelled_parameter",
        check_support=check_support,
    )


@pytest.mark.parametrize("name, arguments", ORDER_CALLS)
@pytest.mark.parametrize("check_support", (True, False))
def test_v5_order_when_keeps_existing_refusal(name, arguments, check_support):
    # The support gate checks keywords before analysis; bypassing it reaches
    # the existing v6-only version gate before any order lowering.
    source = (
        '//@version=5\nstrategy("order keywords")\n'
        + _call(name, (*arguments, "when = true")) + "\n"
    )
    if check_support:
        _assert_unknown_keyword(source, name, "when")
        return
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="order_keywords.pine", check_support=False)
    diagnostic, = caught.value.diagnostics
    assert diagnostic.level == Level.ERROR
    assert diagnostic.code == "PF-E2002"
    assert diagnostic.args == {"version": 5}
    assert diagnostic.message == (
        "PineForge supports PineScript v6 only (found //@version=5)."
    )
    assert diagnostic.hint == (
        "Migrate the script to //@version=6 using TradingView's editor."
    )
    assert diagnostic.location.file == "order_keywords.pine"
    assert (
        diagnostic.location.line, diagnostic.location.col,
        diagnostic.location.end_col,
    ) == (1, 1, 1)


@pytest.mark.parametrize("check_support", (True, False))
def test_when_on_another_builtin_keeps_generic_error(check_support):
    # timeframe.change has no when parameter and no order migration rule.
    source = HEADER + 'changed = timeframe.change("D", when = true)\n'
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="other_keywords.pine", check_support=check_support)
    diagnostic, = caught.value.diagnostics
    assert diagnostic.level == Level.ERROR
    assert diagnostic.code == "PF-E2027"
    assert diagnostic.args == {"key": "timeframe.change", "name": "when"}
    assert diagnostic.message == "timeframe.change: unknown keyword argument 'when'"
    assert diagnostic.hint is None
    assert diagnostic.location.file == "other_keywords.pine"
    assert (
        diagnostic.location.line, diagnostic.location.col,
        diagnostic.location.end_col,
    ) == (3, 33, 37)


@pytest.mark.parametrize("name, arguments, keyword, value", (
    ("entry", ('"L"', "strategy.long"), "qty_percent", "50"),
    ("order", ('"O"', "strategy.long", "qty = 1"), "immediately", "true"),
    ("exit", ('"X"', '"L"', "limit = high"), "direction", "strategy.long"),
    ("close", ('"L"',), "limit", "high"),
    ("close_all", (), "id", '"L"'),
    ("cancel", ('"L"',), "comment", '"not a cancel parameter"'),
    ("cancel_all", (), "id", '"L"'),
))
@pytest.mark.parametrize("check_support", (True, False))
def test_keywords_from_another_order_function_are_not_accepted(
    name, arguments, keyword, value, check_support,
):
    call = _call(name, (*arguments, f"{keyword} = {value}"))
    _assert_unknown_keyword(
        HEADER + call + "\n", name, keyword, check_support=check_support,
    )


@pytest.mark.parametrize("keyword", ("when", "bogus", "misspelled_parameter"))
def test_existing_ta_unknown_keyword_control(keyword):
    source = HEADER + f"value = ta.sma(close, 10, {keyword} = false)\n"
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="ta_keywords.pine")
    errors = [
        diagnostic for diagnostic in caught.value.diagnostics
        if diagnostic.level == Level.ERROR
    ]
    assert len(errors) == 1
    diagnostic, = errors
    assert f"ta.sma has no parameter '{keyword}'" in diagnostic.message
    line = source.splitlines()[2]
    assert diagnostic.location.file == "ta_keywords.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == (
        3, line.index("false") + 1,
    )


# Written independently of the registry: valid optional names must not become
# casualties of enabling validation. Retain the existing qty_type extension
# and alert metadata policy as well as the parameters used by the runtime.
VALID_ARGUMENTS = (
    ("entry", (
        ("id", '"L"'), ("direction", "strategy.long"), ("qty", "2.5"),
        ("limit", "low"), ("stop", "high"), ("oca_name", '"entries"'),
        ("oca_type", "strategy.oca.cancel"), ("comment", '"entry comment"'),
        ("alert_message", '"entry alert"'), ("disable_alert", "false"),
        ("qty_type", "strategy.fixed"),
    )),
    ("order", (
        ("id", '"O"'), ("direction", "strategy.short"), ("qty", "3.5"),
        ("limit", "high"), ("stop", "low"), ("oca_name", '"orders"'),
        ("oca_type", "strategy.oca.reduce"), ("comment", '"order comment"'),
        ("alert_message", '"order alert"'), ("disable_alert", "true"),
        ("qty_type", "strategy.fixed"),
    )),
    ("exit", (
        ("id", '"X"'), ("from_entry", '"L"'), ("qty", "1.5"),
        ("qty_percent", "50"), ("profit", "10"), ("loss", "5"),
        ("limit", "high"), ("stop", "low"), ("trail_price", "high"),
        ("trail_points", "10"), ("trail_offset", "2"), ("oca_name", '"exits"'),
        ("comment", '"exit comment"'), ("comment_profit", '"profit"'),
        ("comment_loss", '"loss"'), ("comment_trailing", '"trailing"'),
        ("alert_message", '"exit alert"'), ("alert_profit", '"profit alert"'),
        ("alert_loss", '"loss alert"'), ("alert_trailing", '"trailing alert"'),
        ("disable_alert", "false"),
    )),
    ("close", (
        ("id", '"L"'), ("comment", '"close comment"'), ("qty", "1.5"),
        ("qty_percent", "50"), ("alert_message", '"close alert"'),
        ("immediately", "true"), ("disable_alert", "false"),
    )),
    ("close_all", (
        ("comment", '"close all comment"'), ("alert_message", '"close all alert"'),
        ("immediately", "true"), ("disable_alert", "false"),
    )),
    ("cancel", (("id", '"O"'),)),
    ("cancel_all", ()),
)


def _arguments_for_form(parameters, form):
    if form == "positional":
        return [value for _, value in parameters]
    if form == "named":
        return [f"{key} = {value}" for key, value in reversed(parameters)]
    assert form == "mixed"
    return [
        value if index == 0 else f"{key} = {value}"
        for index, (key, value) in enumerate(parameters)
    ]


# cancel_all has no argument spelling to vary; cancel's sole mixed argument
# stays positional. Retire only those six identical-input comparisons.
KEYWORD_EQUIVALENCE_CASES = tuple(
    (name, parameters, form)
    for name, parameters in VALID_ARGUMENTS
    for form in ("named", "mixed")
    if parameters and (form == "named" or len(parameters) > 1)
)


@pytest.mark.parametrize("name, parameters, form", KEYWORD_EQUIVALENCE_CASES)
@pytest.mark.parametrize("check_support", (True, False))
def test_valid_order_keywords_keep_the_positional_emission(
    name, parameters, form, check_support,
):
    positional = _call(name, _arguments_for_form(parameters, "positional"))
    named = _call(name, _arguments_for_form(parameters, form))
    assert named != positional, (name, form)
    assert transpile(HEADER + named + "\n", check_support=check_support) == \
        transpile(HEADER + positional + "\n", check_support=check_support)


# Independent Pine statement expectations, not transpiled controls. Each
# cancel_all line is an actual command; these witness emission, not runtime
# cancellation parity. The two IDs must survive as distinct argument values.
CANCELLATION_EMISSION_CASES = (
    pytest.param(
        "strategy.cancel_all()\n", True,
        (("cancel_all", ()),),
        "strategy_cancel_all();", ("strategy_cancel();",),
        id="cancel-all-one-wrong-opcode",
    ),
    pytest.param(
        "strategy.cancel_all()\n"
        "strategy.cancel_all()\n", False,
        (("cancel_all", ()), ("cancel_all", ())),
        "strategy_cancel_all();", (),
        id="cancel-all-two-missing-command",
    ),
    pytest.param(
        "strategy.cancel_all()\n"
        "strategy.cancel_all()\n"
        "strategy.cancel_all()\n", True,
        (("cancel_all", ()), ("cancel_all", ()), ("cancel_all", ())),
        "strategy_cancel_all();",
        ("strategy_cancel_all();", "strategy_cancel_all();"),
        id="cancel-all-three-extra-command",
    ),
    pytest.param(
        "strategy.cancel_all()\n"
        "strategy.cancel_all()\n"
        "strategy.cancel_all()\n"
        "strategy.cancel_all()\n", False,
        (
            ("cancel_all", ()), ("cancel_all", ()),
            ("cancel_all", ()), ("cancel_all", ()),
        ),
        "strategy_cancel_all();",
        ('strategy_cancel_all(std::string("unexpected"));',),
        id="cancel-all-four-wrong-arity",
    ),
    pytest.param(
        'strategy.cancel(id = "pending-long")\n', True,
        (("cancel", (("string", "pending-long"),)),),
        'strategy_cancel(std::string("pending-long"));',
        ('strategy_cancel(std::string("pending-short"));',),
        id="cancel-pending-long-wrong-id",
    ),
    pytest.param(
        'strategy.cancel("pending-short")\n', False,
        (("cancel", (("string", "pending-short"),)),),
        'strategy_cancel(std::string("pending-short"));',
        ('strategy_cancel(std::string("pending-long"));',),
        id="cancel-pending-short-wrong-id",
    ),
)


def _cpp_code_tokens(cpp):
    # Quoted literals stay atomic; braces/call spellings in them are not code.
    return [
        match for match in re.finditer(
            r"""//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[A-Za-z_]\w*|[^\s]""",
            cpp, re.DOTALL,
        )
        if not match.group().startswith(("//", "/*"))
    ]


def _source_bar_bounds(cpp):
    matches = _cpp_code_tokens(cpp)
    tokens = tuple(match.group() for match in matches)
    signature = (
        "void", "on_source_bar", "(", "const", "Bar", "&", "bar", ")",
        "override", "{",
    )
    openings = [
        index + len(signature) - 1
        for index in range(len(tokens) - len(signature) + 1)
        if tokens[index:index + len(signature)] == signature
    ]
    assert len(openings) == 1, "expected one emitted on_source_bar definition"
    opening, = openings
    depth = 1
    for index in range(opening + 1, len(tokens)):
        depth += (tokens[index] == "{") - (tokens[index] == "}")
        if depth == 0:
            return matches[opening].end(), matches[index].start()
    raise AssertionError("unclosed emitted on_source_bar body")


def _semantic_argument(tokens):
    text = "".join(tokens)
    literal = re.fullmatch(r'"([A-Za-z0-9_-]*)"', text)
    wrapped = re.fullmatch(r'std::string\("([A-Za-z0-9_-]*)"\)', text)
    string = literal or wrapped
    if string is not None:
        return "string", string[1]
    # Unknown expressions must never compare equal to a decoded string ID.
    return "cpp", text


def _strategy_call_records(cpp):
    start, end = _source_bar_bounds(cpp)
    tokens = tuple(match.group() for match in _cpp_code_tokens(cpp[start:end]))
    records = []
    for index, name in enumerate(tokens[:-1]):
        if not name.startswith("strategy_") or tokens[index + 1] != "(":
            continue
        # Keep unexpected strategy opcodes too: filtering them could hide a
        # wrong-command mutation. Balance nested argument expressions.
        arguments = []
        argument = []
        depth = 0
        for token in tokens[index + 2:]:
            if token == ")" and depth == 0:
                if argument or arguments:
                    assert argument, "empty emitted argument"
                    arguments.append(_semantic_argument(argument))
                records.append((name[len("strategy_"):], tuple(arguments)))
                break
            if token == "," and depth == 0:
                assert argument, "empty emitted argument"
                arguments.append(_semantic_argument(argument))
                argument = []
            else:
                depth += token in ("(", "[", "{")
                depth -= token in (")", "]", "}")
                assert depth >= 0, "unbalanced emitted argument"
                argument.append(token)
        else:
            raise AssertionError("unclosed emitted strategy call")
    return tuple(records)


def _replace_first_emitted_command(cpp, command, replacements):
    start, end = _source_bar_bounds(cpp)
    matches = _cpp_code_tokens(cpp[start:end])
    tokens = tuple(match.group() for match in matches)
    wanted = tuple(match.group() for match in _cpp_code_tokens(command))
    positions = [
        index for index in range(len(tokens) - len(wanted) + 1)
        if tokens[index:index + len(wanted)] == wanted
    ]
    assert positions, f"actual emitted command not found: {command}"
    index = positions[0]
    first = start + matches[index].start()
    last = start + matches[index + len(wanted) - 1].end()
    return cpp[:first] + "\n        ".join(replacements) + cpp[last:]


@pytest.mark.parametrize(
    "body, check_support, expected, command, replacements",
    CANCELLATION_EMISSION_CASES,
)
def test_cancellation_semantic_emission_and_wrong_value_control(
    body, check_support, expected, command, replacements,
):
    cpp = transpile(HEADER + body, check_support=check_support)
    assert _strategy_call_records(cpp) == expected

    # A fault edits the actual command in the returned TU, including real
    # command removal/addition for the two call-count controls.
    wrong_cpp = _replace_first_emitted_command(cpp, command, replacements)
    assert wrong_cpp != cpp
    wrong_records = _strategy_call_records(wrong_cpp)
    with pytest.raises(AssertionError):
        assert wrong_records == expected

    # Observer controls only: decoys must not become command evidence.
    start, end = _source_bar_bounds(cpp)
    decoys = (
        '\n        // strategy_cancel_all(); }\n'
        '        /* { strategy_cancel_all(); } */\n'
        '        const char* decoy = "strategy_cancel_all(); }";\n'
        "        const char brace = '}';\n"
    )
    unrelated = '\nvoid unrelated() { strategy_cancel_all(); }\n'
    decorated = cpp[:start] + decoys + cpp[start:end] + cpp[end:] + unrelated
    assert _strategy_call_records(decorated) == expected


@pytest.mark.parametrize("name, arguments", ORDER_CALLS)
def test_valid_minimal_order_calls_still_transpile(name, arguments):
    cpp = transpile(HEADER + _call(name, arguments) + "\n")
    assert cpp


@pytest.mark.parametrize("form", ("positional", "named", "mixed"))
def test_valid_order_arguments_compile_and_evaluate_once(form):
    source = '''//@version=6
strategy("order evaluation", initial_capital=25000, pyramiding=2,
    default_qty_type=strategy.fixed, default_qty_value=1,
    process_orders_on_close=true)
quantity = input.float(2.0, "Quantity")
var array<int> counts = array.new<int>(6, 0)
var array<float> sums = array.new<float>(6, 0.0)
record(array<int> events, array<float> totals, int slot, float value) =>
    array.set(events, slot, array.get(events, slot) + 1)
    array.set(totals, slot, array.get(totals, slot) + value)
    value
'''
    calls = (
        ("entry", (
            ("id", '"L"'), ("direction", "strategy.long"),
            ("qty", "record(counts, sums, 0, quantity)"),
        )),
        ("order", (
            ("id", '"O"'), ("direction", "strategy.long"),
            ("qty", "record(counts, sums, 1, quantity + 1)"),
        )),
        ("exit", (
            ("id", '"X"'), ("from_entry", '"L"'), ("qty", "na"),
            ("qty_percent", "100"),
            ("profit", "record(counts, sums, 2, quantity + 3)"),
        )),
        ("close", (
            ("id", '"L"'), ("comment", '""'),
            ("qty", "record(counts, sums, 3, quantity)"),
        )),
        ("close_all", (
            ("comment", '""'), ("alert_message", '""'),
            ("immediately", "record(counts, sums, 4, close) > 0"),
        )),
        ("cancel", (
            ("id", "str.tostring(record(counts, sums, 5, close))"),
        )),
        ("cancel_all", ()),
    )
    source += "\n".join(
        _call(name, _arguments_for_form(parameters, form))
        for name, parameters in calls
    ) + "\n"
    prices = (11.0, 17.0, 13.0, 19.0, 23.0)
    quantities = (1.25, 3.5)
    driver = r'''
#include <iostream>
#include <vector>
int main() {
    for (const char* quantity : {QUANTITIES}) {
        GeneratedStrategy strategy;
        strategy_set_input(&strategy, "Quantity", quantity);
        std::vector<Bar> bars;
        for (double price : {PRICES}) {
            const auto time = 1577836800000LL
                + static_cast<long long>(bars.size()) * 60000LL;
            bars.push_back({price, price + 1, price - 1, price, 1, time});
        }
        strategy.run(bars.data(), static_cast<int>(bars.size()));
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 2;
        }
        for (int count : strategy.counts) std::cout << count << " ";
        std::cout << "|";
        for (double sum : strategy.sums) std::cout << sum << " ";
        std::cout << "\n";
    }
}
'''
    driver = driver.replace(
        "QUANTITIES", ", ".join(f'"{value}"' for value in quantities),
    ).replace("PRICES", ", ".join(str(price) for price in prices))
    rows = run_emitted_tu(
        transpile(source), driver, opt="-O0", label=f"order-keywords-{form}",
    ).splitlines()
    assert len(rows) == len(quantities)
    for row, quantity in zip(rows, quantities):
        counts, sums = row.split("|")
        assert [int(value) for value in counts.split()] == [len(prices)] * 6
        assert [float(value) for value in sums.split()] == [
            quantity * len(prices), (quantity + 1) * len(prices),
            (quantity + 3) * len(prices), quantity * len(prices),
            sum(prices), sum(prices),
        ]
