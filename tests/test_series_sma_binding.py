"""Unit coverage for the caller's complete-evaluation SMA checkpoint."""

import re

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp, run_emitted_tu
from tests.test_array_retained_alias import _reference_cpp


@pytest.mark.parametrize("binding", [
    pytest.param("length = 5", id="plain-literal"),
    pytest.param("var int length = 5", id="admitted-persistent-literal"),
    pytest.param('length = input.int(5, "Window")', id="input"),
])
def test_fixed_sma_keeps_complete_base_cpp(binding):
    source = (
        '//@version=6\nstrategy("fixed SMA emission")\n'
        + binding + "\nvalue = ta.sma(close, length)\nplot(value)\n"
    )
    cpp = transpile(source)
    assert cpp == _reference_cpp(source)
    assert "_PFSeriesSma" not in cpp
    assert "pineforge/source/pine_ta_length.hpp" not in cpp
    compile_cpp(cpp, label="fixed SMA complete emission")


@pytest.mark.parametrize("body", [
    pytest.param("_pf_length = close\nplot(_pf_length)\n", id="factory-local-name"),
    pytest.param("""type _PFSeriesSma
    float value
sample = _PFSeriesSma.new(close)
plot(sample.value)
""", id="support-type-name"),
])
def test_unused_sma_helper_names_keep_complete_base_cpp(body):
    source = '//@version=6\nstrategy("unused SMA helper names")\n' + body
    cpp = transpile(source)
    assert cpp == _reference_cpp(source)
    compile_cpp(cpp, label="unused SMA helper name")


def test_series_sma_support_type_avoids_an_authored_type():
    cpp = transpile("""//@version=6
strategy("SMA support type collision")
type _PFSeriesSma
    float value
sample = _PFSeriesSma.new(close)
length = 1 + bar_index % 5
value = ta.sma(sample.value, length)
plot(value)
""")
    assert "struct _PFSeriesSma {\n    int32_t __pf_id" in cpp
    assert "class _PFSeriesSma__pf2 {" in cpp
    assert re.search(r"^    _PFSeriesSma__pf2 _ta_sma_\w+;$", cpp, re.M)
    compile_cpp(cpp, label="SMA allocated support type")


def test_series_sma_adapter_replaces_an_entire_evaluation_not_its_last_call():
    cpp = transpile("""//@version=6
strategy("SMA caller checkpoint")
length = 1 + bar_index % 5
value = ta.sma(close, length)
plot(value)
""")
    driver = r"""
#include <cassert>
int main() {
    _PFSeriesSma value;
    value.begin_evaluation(true);
    assert(is_na(value.compute(2, 2)));
    assert(value.compute(4, 2) == 3);

    // Replace both invocations, rather than just the last sample (4).
    value.begin_evaluation(false);
    assert(is_na(value.recompute(6, 2)));
    assert(value.recompute(8, 2) == 7);

    // The replacement's complete sequence is the next evaluation's history.
    value.begin_evaluation(true);
    assert(value.compute(10, 3) == 8);
    assert(value.compute(10, 4) == 8.5);  // equal sources still append

    // Missing sources read the same saved history without adding a sample.
    value.begin_evaluation(false);
    assert(value.recompute(na<double>(), 2) == 7);
    assert(value.recompute(12, 3) == 26.0 / 3.0);

    // An unexecuted replacement does not remove the prior reached result.
    value.begin_evaluation(false);
    value.begin_evaluation(true);
    assert(value.compute(14, 4) == 10);

    // A new slot that was skipped still supplies the right replacement base.
    value.begin_evaluation(true);
    value.begin_evaluation(false);
    assert(value.recompute(16, 2) == 15);

    // First use can be on a replacement callback.
    _PFSeriesSma first;
    first.begin_evaluation(false);
    assert(is_na(first.recompute(2, 2)));
    assert(first.recompute(2, 2) == 2);

    // Generated argument factories run once per reach, including replacement.
    int visits = 0;
    first.begin_evaluation(true);
    first.compute([&]() {
        ++visits;
        return std::pair<double, double>{4, 2};
    });
    assert(visits == 1);
    first.begin_evaluation(false);
    assert(first.recompute([&]() {
        ++visits;
        return std::pair<double, double>{6, 2};
    }) == 4);
    assert(visits == 2);
}
"""
    run_emitted_tu(cpp, driver, opt="-O2", label="series SMA evaluation checkpoint")


@pytest.mark.parametrize("series_first", [False, True])
def test_series_sma_requested_helper_copies_begin_on_their_own_context(series_first):
    calls = [
        'a = request.security(syminfo.tickerid, "60", mean(stable))',
        'b = request.security(syminfo.tickerid, "240", mean(bar_index % 5 + 1))',
    ]
    if series_first:
        calls.reverse()
    cpp = transpile(
        '//@version=6\nstrategy("SMA requested binding")\n'
        "mean(n) => ta.sma(close, n)\n"
        'stable = syminfo.type == "crypto" ? 9 : 14\n'
        + "\n".join(calls) + "\nplot(a + b)\n"
    )
    series_sec, stable_sec = (0, 1) if series_first else (1, 0)
    members = re.findall(rf"_PFSeriesSma (_sec{series_sec}__ta_sma_\w+);", cpp)
    assert members
    for member in members:
        assert f"{member}.begin_evaluation(security_series_slot_is_new({series_sec}));" in cpp
        assert f"{member}.begin_evaluation(history_advances_new_bar());" not in cpp
    assert re.search(
        rf"pineforge::source::FirstCallBound<ta::SMA> _sec{stable_sec}__ta_sma_\w+;",
        cpp,
    )
    compile_cpp(cpp, label="series SMA requested helper ownership")


def test_nested_helper_instances_each_begin_their_written_sma_state():
    cpp = transpile("""//@version=6
strategy("SMA nested call ownership")
mean(n) => ta.sma(close, n)
outer(n) => mean(n)
length = 1 + bar_index % 5
first = outer(length)
second = outer(length + 1)
third = mean(length + 2)
plot(first + second + third)
""")
    members = re.findall(r"^    _PFSeriesSma (\w+);$", cpp, re.M)
    assert len(members) >= 3 and len(set(members)) == len(members)
    for member in members:
        assert cpp.count(f"{member}.begin_evaluation(history_advances_new_bar());") == 1
    compile_cpp(cpp, label="nested SMA helper clocks")


@pytest.mark.parametrize("named", [False, True])
def test_series_sma_argument_factories_preserve_parameter_order(named):
    call = ("ta.sma(length = period(), source = sample())" if named
            else "ta.sma(sample(), period())")
    cpp = transpile("""//@version=6
strategy("SMA argument order")
sample() => close
period() => 1 + bar_index % 5
""" + f"value = {call}\nplot(value)\n")
    factories = re.findall(
        r"const double _pf_source = ([^;]+); "
        r"const double _pf_length = ([^;]+);",
        cpp,
    )
    assert factories
    for source, length in factories:
        assert "sample(" in source and "period(" not in source
        assert "period(" in length and "sample(" not in length
    compile_cpp(cpp, label="series SMA parameter evaluation order")


def test_series_sma_argument_temporaries_cannot_capture_authored_bindings():
    cpp = transpile("""//@version=6
strategy("SMA argument temporary hygiene")
_pf_source = close
_pf_length = 1 + bar_index % 5
value = ta.sma(_pf_source, _pf_length)
plot(value)
""")
    assert "const double _pf_source = (pf_safe__pf_source);" in cpp
    assert (
        "const double _pf_length__pf2 = pineforge::source::ta_number(_pf_length);"
    ) in cpp
    assert "return std::pair<double, double>{_pf_source, _pf_length__pf2};" in cpp
    compile_cpp(cpp, label="series SMA argument temporary hygiene")


@pytest.mark.parametrize("call_count", [1, 2])
def test_request_owning_helper_closes_each_changing_length_separately(call_count):
    source = """//@version=6
strategy("SMA helper owns requested context")
requested(n) => request.security(syminfo.tickerid, "60", ta.sma(close, n))
first = requested(1 + bar_index % 5)
plot(first)
"""
    if call_count == 2:
        source += "second = requested(2 + bar_index % 5)\nplot(second)\n"
    cpp = transpile(source)
    members = re.findall(r"^    _PFSeriesSma (_sec\d+__ta_sma_\w+);$", cpp, re.M)
    assert len(members) == call_count and len(set(members)) == call_count
    assert len({re.search(r"_sec(\d+)_", member).group(1) for member in members}) == call_count
    for member in members:
        sec_id = re.search(r"_sec(\d+)_", member).group(1)
        assert f"{member}.begin_evaluation(security_series_slot_is_new({sec_id}));" in cpp
    compile_cpp(cpp, label="changing lengths in request-owning helpers")
