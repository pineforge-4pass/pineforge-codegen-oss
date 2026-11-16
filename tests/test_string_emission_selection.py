"""Unit boundaries for selecting byte or UTF-16 string emission.

The existing complete-reference and numeric-NaN assertions pin the supplied
regressions. These cases cover propagation and coexistence, without replacing
those references or asserting new TradingView measurements.
"""

import pytest

from pineforge_codegen import transpile


HEADER = '//@version=6\nstrategy("string emission selection")\n'
UNIT_HELPER = "inline int _pf_u16_length("


@pytest.mark.parametrize("operation", ("contains", "startswith", "endswith"))
@pytest.mark.parametrize("requested", (False, True), ids=("chart", "requested"))
def test_complete_string_history_and_forwarding_do_not_select_unit_helpers(
    operation, requested,
):
    expression = "predicate()"
    if requested:
        expression = f'request.security(syminfo.tickerid, "60", {expression})'
    cpp = transpile(
        HEADER
        + "forward(string text) => text\n"
        + "predicate() =>\n"
        + '    string current = bar_index % 2 == 0 ? "ab" : "ac"\n'
        + "    string previous = current[1]\n"
        + f'    str.{operation}(forward(previous), "a")\n'
        + f"observed = {expression}\nplot(close)\n"
    )
    # An absent history slot is string na; neither it nor the complete ASCII
    # alternatives can introduce an isolated surrogate.
    assert UNIT_HELPER not in cpp
    assert f"_pf_u16_{operation}(" not in cpp


@pytest.mark.parametrize("operation", ("contains", "startswith", "endswith"))
@pytest.mark.parametrize("requested", (False, True), ids=("chart", "requested"))
def test_a_forwarded_surrogate_half_keeps_unit_search(operation, requested):
    expression = 'predicate("😀", str.substring("😀", 0, 1))'
    if requested:
        expression = f'request.security(syminfo.tickerid, "60", {expression})'
    cpp = transpile(
        HEADER
        + "forward(string text) => text\n"
        + "predicate(string text, string needle) =>\n"
        + f"    str.{operation}(forward(text), forward(needle))\n"
        + f"observed = {expression}\nplot(close)\n"
    )
    assert UNIT_HELPER in cpp
    assert f"return _pf_u16_{operation}(_pf_search_source, _pf_search_needle);" in cpp


@pytest.mark.parametrize("requested", (False, True), ids=("chart", "requested"))
def test_complete_string_writes_and_history_keep_native_concatenation(requested):
    expression = 'combine() + "c"'
    if requested:
        expression = f'request.security(syminfo.tickerid, "60", {expression})'
    cpp = transpile(
        HEADER
        + "combine() =>\n"
        + '    string current = "a"\n'
        + '    current += "b"\n'
        + "    string previous = current[1]\n"
        + "    current + previous\n"
        + f"observed = {expression}\nplot(close)\n"
    )
    assert UNIT_HELPER not in cpp
    assert "_pf_u16_concat(" not in cpp
    assert "_pf_u16_normalize(" not in cpp


@pytest.mark.parametrize("requested", (False, True), ids=("chart", "requested"))
def test_a_write_of_a_half_taints_later_history_and_concatenation(requested):
    expression = "combine()"
    if requested:
        expression = f'request.security(syminfo.tickerid, "60", {expression})'
    cpp = transpile(
        HEADER
        + "combine() =>\n"
        + '    string current = "a"\n'
        + "    if bar_index % 2 == 0\n"
        + '        current := str.substring("😀", 0, 1)\n'
        + "    string previous = current[1]\n"
        + "    current + previous\n"
        + f"observed = {expression}\nplot(close)\n"
    )
    # The substring itself needs the prelude, so require a live join call too.
    assert UNIT_HELPER in cpp
    assert cpp.count("_pf_u16_concat(") > 1


def test_unit_calls_do_not_change_unrelated_native_string_expressions():
    cpp = transpile(
        HEADER
        + 'half = str.substring("😀", 0, 1)\n'
        + 'rejoined = half + str.substring("😀", 1, 2)\n'
        + 'ordinary = "a" + na\n'
        + 'flag = str.contains("ab", "a")\nplot(close)\n'
    )
    assert UNIT_HELPER in cpp
    assert 'ordinary = (std::string("a") + std::to_string(na<double>()));' in cpp
    assert 'flag = (std::string("ab").find(std::string("a")) != std::string::npos);' in cpp
    joined = next(line for line in cpp.splitlines()
                  if line.strip().startswith("rejoined ="))
    assert "_pf_u16_concat(" in joined


def test_native_predicate_keeps_a_counted_nul_literal():
    cpp = transpile(
        HEADER + 'flag = str.contains("a\0b", "b")\nplot(close)\n'
    )
    assert 'std::string("a\\000b", 3).find(std::string("b"))' in cpp
    assert UNIT_HELPER not in cpp


def test_requested_numeric_na_keeps_the_native_string_conversion():
    cpp = transpile(
        HEADER
        + 'observed = request.security(syminfo.tickerid, "60", "a" + na)\n'
        + "plot(close)\n"
    )
    assert '(std::string("a") + std::to_string(na<double>()))' in cpp
    assert UNIT_HELPER not in cpp


def test_a_default_parameter_is_independent_of_a_same_named_half():
    cpp = transpile(
        HEADER
        + 'text = str.substring("😀", 0, 1)\n'
        + 'predicate(string text = "ab") => str.contains(text, "a")\n'
        + "observed = predicate()\nplot(close)\n"
    )
    assert UNIT_HELPER in cpp
    assert '.find(std::string("a"))' in cpp
    assert "const std::string _pf_search_source =" not in cpp


def test_one_half_argument_keeps_a_shared_helper_on_unit_search():
    cpp = transpile(
        HEADER
        + 'predicate(string text) => str.contains("😀", text)\n'
        + 'ordinary = predicate("a")\n'
        + 'half = predicate(str.substring("😀", 0, 1))\nplot(close)\n'
    )
    assert "return _pf_u16_contains(_pf_search_source, _pf_search_needle);" in cpp
