"""Closed-trade literal comparisons avoid owning string temporaries."""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from tests.test_runtime_var_initialization import _compile_and_run


_HEADER = '//@version=6\nstrategy("closed trade text")\n'
_FAST_SOURCE = 'optimized = strategy.closedtrades.entry_id(0) == "L"\n'
_FAST_CPP = 'closed_trade_entry_id_equals(0, std::string_view("L", sizeof("L") - 1))'

_COMPARISONS = [
    # Entry ID equality reads index 0 and compares one literal byte.
    ('strategy.closedtrades.entry_id(0) == "L"', _FAST_CPP),
    # Reversing equality operands keeps the same entry ID predicate.
    ('"L" == strategy.closedtrades.entry_id(0)', _FAST_CPP),
    # Entry ID inequality negates the equality predicate.
    ('strategy.closedtrades.entry_id(0) != "L"',
     '!(closed_trade_entry_id_equals(0, std::string_view("L", sizeof("L") - 1)))'),
    # Reversed entry ID inequality negates the same predicate.
    ('"L" != strategy.closedtrades.entry_id(0)',
     '!(closed_trade_entry_id_equals(0, std::string_view("L", sizeof("L") - 1)))'),
    # Exit ID equality reads the exit ID rather than the entry ID.
    ('strategy.closedtrades.exit_id(0) == "L"',
     'closed_trade_exit_id_equals(0, std::string_view("L", sizeof("L") - 1))'),
    # Reversed exit ID equality still reads index 0's exit ID.
    ('"L" == strategy.closedtrades.exit_id(0)',
     'closed_trade_exit_id_equals(0, std::string_view("L", sizeof("L") - 1))'),
    # Exit ID inequality is the negated exit ID equality.
    ('strategy.closedtrades.exit_id(0) != "L"',
     '!(closed_trade_exit_id_equals(0, std::string_view("L", sizeof("L") - 1)))'),
    # Reversed exit ID inequality uses that same negated predicate.
    ('"L" != strategy.closedtrades.exit_id(0)',
     '!(closed_trade_exit_id_equals(0, std::string_view("L", sizeof("L") - 1)))'),
    # Entry comment equality selects the entry comment helper.
    ('strategy.closedtrades.entry_comment(0) == "L"',
     'closed_trade_entry_comment_equals(0, std::string_view("L", sizeof("L") - 1))'),
    # Reversed entry comment equality has the same meaning.
    ('"L" == strategy.closedtrades.entry_comment(0)',
     'closed_trade_entry_comment_equals(0, std::string_view("L", sizeof("L") - 1))'),
    # Entry comment inequality negates that helper's result.
    ('strategy.closedtrades.entry_comment(0) != "L"',
     '!(closed_trade_entry_comment_equals(0, std::string_view("L", sizeof("L") - 1)))'),
    # Reversed entry comment inequality uses the same negation.
    ('"L" != strategy.closedtrades.entry_comment(0)',
     '!(closed_trade_entry_comment_equals(0, std::string_view("L", sizeof("L") - 1)))'),
    # Exit comment equality selects the exit comment helper.
    ('strategy.closedtrades.exit_comment(0) == "L"',
     'closed_trade_exit_comment_equals(0, std::string_view("L", sizeof("L") - 1))'),
    # Reversed exit comment equality still compares the exit comment.
    ('"L" == strategy.closedtrades.exit_comment(0)',
     'closed_trade_exit_comment_equals(0, std::string_view("L", sizeof("L") - 1))'),
    # Exit comment inequality negates the exit comment predicate.
    ('strategy.closedtrades.exit_comment(0) != "L"',
     '!(closed_trade_exit_comment_equals(0, std::string_view("L", sizeof("L") - 1)))'),
    # Reversed exit comment inequality negates the same predicate.
    ('"L" != strategy.closedtrades.exit_comment(0)',
     '!(closed_trade_exit_comment_equals(0, std::string_view("L", sizeof("L") - 1)))'),
    # The empty literal has zero bytes after excluding the terminator.
    ('strategy.closedtrades.entry_id(0) == ""',
     'closed_trade_entry_id_equals(0, std::string_view("", sizeof("") - 1))'),
    # UTF-8 encodes é as two bytes (C3 A9) and 猫 as three (E7 8C AB); emitted C++
    # is ASCII, so each byte is a three-digit octal escape (303 251 347 214 253);
    # sizeof excludes only the NUL, so the length is still five.
    ('strategy.closedtrades.entry_id(0) == "é猫"',
     r'closed_trade_entry_id_equals(0, std::string_view("\303\251\347\214\253", sizeof("\303\251\347\214\253") - 1))'),
    # a, quote, backslash, newline and tab are five bytes after C++ unescaping.
    (r'strategy.closedtrades.entry_id(0) == "a\"\\\n\t"',
     r'closed_trade_entry_id_equals(0, std::string_view("a\"\\\n\t", sizeof("a\"\\\n\t") - 1))'),
]


def _assert_retains_rows(cpp: str) -> None:
    # A helper reads a closed row, so the export is 1 and retirement stays off.
    assert "int strategy_reads_trade_history(void) {\n        return 1;\n" in cpp
    assert "set_trade_history_streamable(true);" not in cpp


@pytest.mark.parametrize("requested", [False, True], ids=["chart", "request"])
@pytest.mark.parametrize("expression, expected", _COMPARISONS)
def test_literal_comparison_emission(expression, expected, requested):
    value = (f'request.security(syminfo.tickerid, "60", {expression})'
             if requested else expression)
    cpp = transpile(_HEADER + f"result = {value}\n")
    if requested:
        evaluator = re.search(
            r"void _eval_security_0\(const Bar& bar, bool is_complete\) \{\n(.*?)\n    \}",
            cpp, re.S,
        )
        assert evaluator is not None
        assert f"_req_sec_0 = {expected};" in evaluator.group(1)
    else:
        assert f"result = {expected};" in cpp
    assert cpp.count("_equals(") == 1
    _assert_retains_rows(cpp)


_LOOP_COMPARISONS = [
    # The int loop binder stays i; the single stored L matches once.
    ('strategy.closedtrades.entry_id(i) == "L"',
     'closed_trade_entry_id_equals(i, std::string_view("L", sizeof("L") - 1))',
     "1\n"),
    # Reversing equality keeps index i and the one matching stored L.
    ('"L" == strategy.closedtrades.entry_id(i)',
     'closed_trade_entry_id_equals(i, std::string_view("L", sizeof("L") - 1))',
     "1\n"),
    # Inequality negates the same index-i predicate; stored L gives zero hits.
    ('strategy.closedtrades.entry_id(i) != "L"',
     '!(closed_trade_entry_id_equals(i, std::string_view("L", sizeof("L") - 1)))',
     "0\n"),
    # Reversed inequality keeps the negation and also gives zero hits.
    ('"L" != strategy.closedtrades.entry_id(i)',
     '!(closed_trade_entry_id_equals(i, std::string_view("L", sizeof("L") - 1)))',
     "0\n"),
]

_LOOP_SOURCE = (
    "matches = 0\n"
    "for i = 0 to strategy.closedtrades - 1\n"
    "    if {expression}\n"
    "        matches += 1\n"
)

# Bounds 0..closedtrades-1 keep the counted loop's direction, step and refresh.
_LOOP_CPP = (
    "        int _for_start_0 = (0);\n"
    "        int _for_end_0 = ((((int)trades_.size()) - 1));\n"
    "        int _for_step_0 = (1);\n"
    "        if (_for_step_0 < 0) _for_step_0 = -_for_step_0;\n"
    "        if (_for_step_0 == 0) _for_step_0 = 1;\n"
    "        const bool _for_down_0 = (_for_start_0 > _for_end_0);\n"
    "        for (int i = _for_start_0; "
    "(_for_down_0 ? (i >= _for_end_0) : (i <= _for_end_0)); "
    "i += (_for_down_0 ? -_for_step_0 : _for_step_0), "
    "_for_end_0 = ((((int)trades_.size()) - 1))) {\n"
)


@pytest.mark.parametrize(
    "expression, expected_cpp", [row[:2] for row in _LOOP_COMPARISONS],
)
def test_loop_variable_comparison_emission(expression, expected_cpp):
    cpp = transpile(_HEADER + _LOOP_SOURCE.format(expression=expression))
    assert _LOOP_CPP + f"            if ({expected_cpp}) {{\n" in cpp
    assert cpp.count(expected_cpp) == 1
    assert cpp.count("_equals(") == 1
    assert "closed_trade_entry_id(" not in cpp
    _assert_retains_rows(cpp)


@pytest.mark.parametrize("source, expected_cpp", [
    # Boolean and keeps the input gate and one equality helper under &&.
    ('gate = input.bool(true, "Gate")\n'
     'result = gate and strategy.closedtrades.entry_id(0) == "L"\n',
     'result = (gate && closed_trade_entry_id_equals(0, std::string_view("L", sizeof("L") - 1)));'),
    # Boolean or keeps the input gate and one equality helper under ||.
    ('gate = input.bool(false, "Gate")\n'
     'result = gate or strategy.closedtrades.entry_id(0) == "L"\n',
     'result = (gate || closed_trade_entry_id_equals(0, std::string_view("L", sizeof("L") - 1)));'),
    # A ternary uses the equality helper once as its condition before 1 and 0.
    ('result = strategy.closedtrades.entry_id(0) == "L" ? 1 : 0\n',
     'result = ((closed_trade_entry_id_equals(0, std::string_view("L", sizeof("L") - 1))) ? (1) : (0));'),
    # An if statement tests the helper directly before assigning result.
    ('result = 0\nif strategy.closedtrades.entry_id(0) == "L"\n    result := 1\n',
     'if (closed_trade_entry_id_equals(0, std::string_view("L", sizeof("L") - 1))) {'),
], ids=["and", "or", "ternary", "if"])
def test_nested_comparison_emission(source, expected_cpp):
    cpp = transpile(_HEADER + source)
    assert expected_cpp in cpp
    assert cpp.count(_FAST_CPP) == 1
    assert cpp.count("_equals(") == 1
    assert "closed_trade_entry_id(" not in cpp
    _assert_retains_rows(cpp)


@pytest.mark.parametrize("requested", [False, True], ids=["chart", "request"])
def test_na_trade_index_reuses_typed_na_coercion(requested):
    expression = 'strategy.closedtrades.entry_id(na) == "L"'
    value = (f'request.security(syminfo.tickerid, "60", {expression})'
             if requested else expression)
    cpp = transpile(_HEADER + f"result = {value}\n")
    # Bare na emits na<double>(), then the int slot directly selects na<int>().
    expected = 'closed_trade_entry_id_equals(na<int>(), std::string_view("L", sizeof("L") - 1))'
    target = "_req_sec_0" if requested else "result"
    assert f"{target} = {expected};" in cpp
    assert cpp.count(expected) == 1
    assert cpp.count("_equals(") == 1
    assert "closed_trade_entry_id(" not in cpp
    _assert_retains_rows(cpp)


@pytest.mark.parametrize("name, escaped, comparison, expected_cpp", [
    # A host entry ID member reserves its name; the authored binding gets pf_safe_.
    ("closed_trade_entry_id_equals", "pf_safe_closed_trade_entry_id_equals",
     'strategy.closedtrades.entry_id(0) == "L"',
     'closed_trade_entry_id_equals(0, std::string_view("L", sizeof("L") - 1))'),
    # The exit ID member uses the same escape rule and still resolves its helper.
    ("closed_trade_exit_id_equals", "pf_safe_closed_trade_exit_id_equals",
     'strategy.closedtrades.exit_id(0) == "L"',
     'closed_trade_exit_id_equals(0, std::string_view("L", sizeof("L") - 1))'),
    # The entry comment member reserves its spelling independently of entry ID.
    ("closed_trade_entry_comment_equals", "pf_safe_closed_trade_entry_comment_equals",
     'strategy.closedtrades.entry_comment(0) == "L"',
     'closed_trade_entry_comment_equals(0, std::string_view("L", sizeof("L") - 1))'),
    # The exit comment member also escapes the authored binding and keeps its call.
    ("closed_trade_exit_comment_equals", "pf_safe_closed_trade_exit_comment_equals",
     'strategy.closedtrades.exit_comment(0) == "L"',
     'closed_trade_exit_comment_equals(0, std::string_view("L", sizeof("L") - 1))'),
])
def test_authored_comparison_member_names_are_escaped(
    name, escaped, comparison, expected_cpp,
):
    cpp = transpile(_HEADER + f"{name} = close\n"
                    + f"copied = {name}\nresult = {comparison}\n")
    assert re.search(rf"\b{escaped}\b", cpp)
    assert f"{escaped} = current_bar_.close;" in cpp
    assert f"copied = {escaped};" in cpp
    assert not re.search(rf"^\s+{name} = ", cpp, re.M)
    assert f"result = {expected_cpp};" in cpp
    assert cpp.count(expected_cpp) == 1
    assert cpp.count("_equals(") == 1
    assert not re.search(r"\bclosed_trade_(?:entry|exit)_(?:id|comment)\(", cpp)
    _assert_retains_rows(cpp)


_FALLBACKS = [
    # The owning constructor still truncates this literal at its first NUL.
    # Preserves existing behaviour; this makes no claim about Pine semantics.
    ('strategy.closedtrades.entry_id(0) == "L\0tail"',
     r'(closed_trade_entry_id(0) == std::string("L\000tail"))'),
    # The same NUL truncation remains when the literal is on the left.
    # Preserves existing behaviour; this makes no claim about Pine semantics.
    ('"L\0tail" != strategy.closedtrades.entry_id(0)',
     r'(std::string("L\000tail") != closed_trade_entry_id(0))'),
    # Exit IDs with NUL literals still use their owning getter.
    # Preserves existing behaviour; this makes no claim about Pine semantics.
    ('strategy.closedtrades.exit_id(0) == "\0"',
     r'(closed_trade_exit_id(0) == std::string("\000"))'),
    # Entry comments with NUL literals still use their owning getter.
    # Preserves existing behaviour; this makes no claim about Pine semantics.
    ('strategy.closedtrades.entry_comment(0) != "\0"',
     r'(closed_trade_entry_comment(0) != std::string("\000"))'),
    # Exit comments with NUL literals still use their owning getter.
    # Preserves existing behaviour; this makes no claim about Pine semantics.
    ('"\0" == strategy.closedtrades.exit_comment(0)',
     r'(std::string("\000") == closed_trade_exit_comment(0))'),
    # Two getters must both retain their independently owned strings.
    ('strategy.closedtrades.entry_id(0) == strategy.closedtrades.exit_id(1)',
     '(closed_trade_entry_id(0) == closed_trade_exit_id(1))'),
    # Inequality between two getters also remains an ordinary comparison.
    ('strategy.closedtrades.exit_comment(1) != strategy.closedtrades.entry_comment(0)',
     '(closed_trade_exit_comment(1) != closed_trade_entry_comment(0))'),
    # An input-backed variable remains a variable, even with a literal default.
    ('strategy.closedtrades.entry_id(0) == wanted',
     '(closed_trade_entry_id(0) == wanted)'),
    # Reversed variable inequality keeps both its order and its operator.
    ('wanted != strategy.closedtrades.exit_id(0)',
     '(wanted != closed_trade_exit_id(0))'),
    # A call that returns text is not a literal operand.
    ('strategy.closedtrades.entry_comment(0) == textValue()',
     '(closed_trade_entry_comment(0) == textValue())'),
    # A helper wrapping a getter is not a direct accessor call.
    ('tradeText() == "L"', '(tradeText() == std::string("L"))'),
    # A stored entry ID remains the owning getter call.
    ('strategy.closedtrades.entry_id(0)', 'closed_trade_entry_id(0)'),
    # A stored exit ID remains the owning getter call.
    ('strategy.closedtrades.exit_id(0)', 'closed_trade_exit_id(0)'),
    # A stored entry comment remains the owning getter call.
    ('strategy.closedtrades.entry_comment(0)', 'closed_trade_entry_comment(0)'),
    # A stored exit comment remains the owning getter call.
    ('strategy.closedtrades.exit_comment(0)', 'closed_trade_exit_comment(0)'),
    # Concatenation still constructs an owning string for its literal operand.
    ('strategy.closedtrades.entry_id(0) + "!"',
     '(closed_trade_entry_id(0) + std::string("!"))'),
    # Open-trade text is outside the closed-trade comparison contract.
    ('strategy.opentrades.entry_id(0) == "L"',
     '(open_trade_entry_id(0) == std::string("L"))'),
    # An ordinary string comparison keeps the same owning literal constructor.
    ('wanted == "L"', '(wanted == std::string("L"))'),
]


@pytest.mark.parametrize("expression, expected", _FALLBACKS)
def test_other_shapes_keep_exact_owning_emission(expression, expected):
    source = (_HEADER
              + 'wanted = input.string("L", "Wanted")\n'
              + 'textValue() => "L"\n'
              + 'tradeText() => strategy.closedtrades.entry_id(0)\n'
              + _FAST_SOURCE + f"result = {expression}\n")
    cpp = transpile(source)
    # The companion fast path makes each row fail before the new lowering exists.
    assert f"optimized = {_FAST_CPP};" in cpp
    assert f"result = {expected};" in cpp
    assert cpp.count("_equals(") == 1


@pytest.mark.parametrize("expression, expected", _FALLBACKS[:7])
def test_request_fallbacks_keep_exact_owning_emission(expression, expected):
    cpp = transpile(_HEADER + _FAST_SOURCE
                    + f'result = request.security(syminfo.tickerid, "60", {expression})\n')
    assert f"optimized = {_FAST_CPP};" in cpp
    assert f"_req_sec_0 = {expected};" in cpp
    assert cpp.count("_equals(") == 1


@pytest.mark.parametrize("requested", [False, True], ids=["chart", "request"])
def test_fractional_trade_index_reuses_na_preserving_coercion(requested):
    expression = 'strategy.closedtrades.entry_id(0.75) == "L"'
    value = (f'request.security(syminfo.tickerid, "60", {expression})'
             if requested else expression)
    cpp = transpile(_HEADER + f"result = {value}\n")
    # 0.75 narrows to index 0; the existing cast binds its source exactly once.
    expected = (
        'closed_trade_entry_id_equals([&](){ auto _pf_v = (0.75); '
        'if constexpr (std::is_floating_point_v<decltype(_pf_v)>) '
        'return (_pf_v >= -2147483648.0 && _pf_v < 2147483648.0) '
        '? (int)_pf_v : na<int>(); else return is_na(_pf_v) ? na<int>() : (int)_pf_v; }(), '
        'std::string_view("L", sizeof("L") - 1))'
    )
    target = "_req_sec_0" if requested else "result"
    assert f"{target} = {expected};" in cpp
    assert cpp.count("auto _pf_v = (0.75);") == 1


_RUNTIME_ROWS = [
    # Identical one-byte fields give equality, inequality, reversed equality, inequality.
    ('std::string("L")', '"L"', "0", "1010 1010 1010 1010\n"),
    # Different fields make both equalities false and both inequalities true.
    ('std::string("other")', '"L"', "0", "0101 0101 0101 0101\n"),
    # Empty stored fields and empty literals compare equal.
    ('std::string()', '""', "0", "1010 1010 1010 1010\n"),
    # Empty stored fields differ from a nonempty literal.
    ('std::string()', '"L"', "0", "0101 0101 0101 0101\n"),
    # The six stored bytes L, NUL, t, a, i, l differ from the one-byte literal.
    (r'std::string("L\0tail", 6)', '"L"', "0", "0101 0101 0101 0101\n"),
    # Two plus three UTF-8 bytes compare equal using their explicit extent.
    ('std::string("é猫")', '"é猫"', "0", "1010 1010 1010 1010\n"),
    # Both spellings decode to a, quote, backslash, newline and tab.
    (r'std::string("a\"\\\n\t")', r'"a\"\\\n\t"', "0", "1010 1010 1010 1010\n"),
    # A negative index reads the same empty text as the existing getter.
    ('std::string("L")', '""', "-1", "1010 1010 1010 1010\n"),
    # One row has no index 1; its empty result differs from L.
    ('std::string("L")', '"L"', "1", "0101 0101 0101 0101\n"),
    # Truncation toward zero makes 0.75 read the one stored row at index 0.
    ('std::string("L")', '"L"', "0.75", "1010 1010 1010 1010\n"),
    # The unchanged owning NUL literal truncates to L, which equals the stored L.
    # Preserves existing behaviour; this makes no claim about Pine semantics.
    ('std::string("L")', '"L\0tail"', "0", "1010 1010 1010 1010\n"),
]

_FIELDS = ("entry_id", "exit_id", "entry_comment", "exit_comment")
_FORMS = (("eq", "{read} == {literal}"), ("ne", "{read} != {literal}"),
          ("req", "{literal} == {read}"), ("rne", "{literal} != {read}"))

_SEEDED_HOST = r'''
class TextStrategy : public GeneratedStrategy {
    std::string text_;
public:
    explicit TextStrategy(std::string text) : text_(std::move(text)) {}
    void on_source_bar(const Bar& bar) override {
        Trade row{};
        row.entry_price = row.exit_price = 100.0;
        row.qty = 1.0;
        row.is_long = true;
        row.entry_time = bar.timestamp - 60000;
        row.exit_time = bar.timestamp;
        row.entry_bar_index = row.exit_bar_index = 0;
        row.entry_id = row.exit_id = row.entry_comment = row.exit_comment = text_;
        record_close_trade(std::move(row));
        GeneratedStrategy::on_source_bar(bar);
    }
};
'''


@pytest.mark.parametrize("expression, expected_cpp, expected_stdout", _LOOP_COMPARISONS)
def test_loop_variable_comparisons_execute_in_source_host(
    expression, expected_cpp, expected_stdout,
):
    cpp = transpile(_HEADER + _LOOP_SOURCE.format(expression=expression))
    assert _LOOP_CPP + f"            if ({expected_cpp}) {{\n" in cpp
    assert cpp.count("_equals(") == 1
    assert "closed_trade_entry_id(" not in cpp
    _assert_retains_rows(cpp)
    driver = (
        '#include <iostream>\n#include <utility>\n' + _SEEDED_HOST
        + r'''
int main() {
    Bar bars[] = {Bar{100.0, 100.0, 100.0, 100.0, 1.0, 60000}};
    TextStrategy strategy{std::string("L")};
    strategy.run(bars, 1);
    if (!strategy.last_error().empty()) return 2;
    if (strategy.trade_count() != 1) return 3;
    std::cout << strategy.matches << "\n";
}
'''
    )
    assert _compile_and_run(cpp + driver) == expected_stdout


@pytest.mark.parametrize("stored, literal, index, expected", _RUNTIME_ROWS)
def test_comparisons_execute_in_source_host(stored, literal, index, expected):
    body = []
    output = []
    for field in _FIELDS:
        read = f"strategy.closedtrades.{field}({index})"
        for suffix, form in _FORMS:
            name = f"{field}_{suffix}"
            body.append(f"{name} = {form.format(read=read, literal=literal)}\n")
            output.append(f" << strategy.{name}")
        output.append(' << " "')
    cpp = transpile(_HEADER + _FAST_SOURCE + "".join(body))
    assert f"optimized = {_FAST_CPP};" in cpp
    _assert_retains_rows(cpp)
    driver = (
        '#include <iostream>\n#include <utility>\n' + _SEEDED_HOST
        + '\nint main() {\n'
        + '    Bar bars[] = {Bar{100.0, 100.0, 100.0, 100.0, 1.0, 60000}};\n'
        + f'    TextStrategy strategy{{{stored}}};\n'
        + '    strategy.run(bars, 1);\n'
        + '    if (!strategy.last_error().empty()) return 2;\n'
        + '    if (strategy.trade_count() != 1) return 3;\n'
        + '    std::cout' + "".join(output[:-1]) + ' << "\\n";\n'
        + '}\n'
    )
    assert _compile_and_run(cpp + driver) == expected


@pytest.mark.parametrize("expression, expected_cpp, expected_stdout", [
    # Empty equals L is false; one index evaluation on each of three bars.
    ('strategy.closedtrades.entry_id(nextIndex()) == "L"',
     'closed_trade_entry_id_equals(nextIndex(), std::string_view("L", sizeof("L") - 1))',
     "0 3\n"),
    # Reversing equality operands still calls the index once per bar.
    ('"L" == strategy.closedtrades.entry_id(nextIndex())',
     'closed_trade_entry_id_equals(nextIndex(), std::string_view("L", sizeof("L") - 1))',
     "0 3\n"),
    # Empty differs from L; negation must not duplicate the index evaluation.
    ('strategy.closedtrades.entry_id(nextIndex()) != "L"',
     '!(closed_trade_entry_id_equals(nextIndex(), std::string_view("L", sizeof("L") - 1)))',
     "1 3\n"),
    # Reversed inequality has the same true result and three evaluations.
    ('"L" != strategy.closedtrades.entry_id(nextIndex())',
     '!(closed_trade_entry_id_equals(nextIndex(), std::string_view("L", sizeof("L") - 1)))',
     "1 3\n"),
])
def test_side_effecting_index_is_evaluated_once_per_bar(
    expression, expected_cpp, expected_stdout,
):
    cpp = transpile(_HEADER + '''var calls = array.new<int>(1, 0)
nextIndex() =>
    calls.set(0, calls.get(0) + 1)
    0
''' + f"result = {expression}\nevaluationCount = calls.get(0)\n")
    # One function invocation supplies the helper's single index argument.
    assert f"result = {expected_cpp};" in cpp
    on_bar = cpp[cpp.index("    void on_source_bar("):]
    assert on_bar.count("nextIndex()") == 1
    _assert_retains_rows(cpp)
    driver = r'''
#include <iostream>
int main() {
    Bar bars[] = {
        Bar{100.0, 100.0, 100.0, 100.0, 1.0, 60000},
        Bar{100.0, 100.0, 100.0, 100.0, 1.0, 120000},
        Bar{100.0, 100.0, 100.0, 100.0, 1.0, 180000},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 3);
    if (!strategy.last_error().empty()) return 2;
    std::cout << strategy.result << " " << strategy.evaluationCount << "\n";
}
'''
    assert _compile_and_run(cpp + driver) == expected_stdout
