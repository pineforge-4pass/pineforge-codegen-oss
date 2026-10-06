"""Coded stops preserve legacy English and cannot be selected by Pine text."""

import json
import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.helpers import CPP_EMITTER_NAMES, CPP_STANDARD_MACROS
from pineforge_codegen.codegen.run_stops import RUN_STOP_SHIMS_CPP, request_stop
from tests._compile import compile_cpp, run_emitted_tu
from tests._legacy_cpp import _without_run_failure_scaffold


PRELUDE = '//@version=6\nstrategy("coded stops")\n'
HELPERS = (
    "pine_no_data_stop", "pine_other_symbol_stop", "pine_array_stop",
    "pine_collection_stop", "pine_na_stop", "pine_limit_stop",
    "pine_unsupported_stop", "pine_string_stop", "pine_engine_invariant",
    "note_run_failure", "note_run_failure_unknown",
)


def _legacy_branch(cpp):
    return cpp.replace(RUN_STOP_SHIMS_CPP,
                       "#undef PINEFORGE_HAS_RUN_FAILURE_CODES_V1\n" + RUN_STOP_SHIMS_CPP)


@pytest.mark.parametrize("statement, expected", [
    ('runtime.error("positional")', 'pine_runtime_error(std::string("positional"))'),
    ('runtime.error(message = "named")', 'pine_runtime_error(std::string("named"))'),
    ('runtime.error()', 'pine_runtime_error("")'),
    ('string text = switch\n    => runtime.error(message = "arm"), ""',
     'pine_runtime_error(std::string("arm"))'),
])
def test_runtime_error_messages(statement, expected):
    cpp = transpile(PRELUDE + statement + "\n")
    assert expected in cpp.split("class GeneratedStrategy", 1)[1]


@pytest.mark.parametrize("statement, expected", [
    ('runtime.error(message = "named")', "named"),
    ('runtime.error()', ""),
    ('string text = switch\n    => runtime.error(message = "arm"), ""', "arm"),
])
def test_runtime_error_really_throws(statement, expected):
    cpp = transpile(PRELUDE + statement + "\n")
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    try { strategy.on_source_bar(bar); assert(false); }
    catch (const std::exception& error) { std::cout << error.what(); }
}
'''
    assert run_emitted_tu(cpp, driver, opt="-O2", label="runtime.error messages") == expected


def test_tonumber_evaluates_before_its_narrow_parse_catches():
    cpp = transpile(PRELUDE + '''
fail() =>
    runtime.error("argument stopped")
    "12"
amount = str.tonumber(fail())
strategy.entry("L", strategy.long, qty=amount)
''')
    statement = next(line for line in cpp.splitlines() if "_pf_number_text=" in line)
    assert statement.index("_pf_number_text=") < statement.index("try {")
    assert "std::stod(_pf_number_text)" in statement
    assert "catch (const std::invalid_argument&)" in statement
    assert "catch (const std::out_of_range&)" in statement
    assert "catch (...)" not in statement
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    try { strategy.on_source_bar(bar); assert(false); }
    catch (const std::exception& error) { std::cout << error.what(); }
}
'''
    assert run_emitted_tu(cpp, driver, opt="-O2", label="tonumber stop propagation") == "argument stopped"


@pytest.mark.parametrize("legacy", [False, True])
def test_stop_shims_compile_and_keep_english(legacy):
    cpp = transpile(PRELUDE)
    if legacy:
        cpp = _legacy_branch(cpp)
    driver = r'''
#include <cassert>
#include <iostream>
template <typename Action>
void stop(Action action, const char* expected_code) {
    try { action(); assert(false); }
    catch (const std::exception& error) {
        assert(std::string(error.what()) == "unchanged");
#ifdef PINEFORGE_HAS_RUN_FAILURE_CODES_V1
        assert(std::string(run_failure_code_name(classify_run_failure(error).code)) == expected_code);
#endif
    }
}
int main() {
    stop([] { _PF_NO_DATA_STOP("request.financial", "request.financial(...) at line 3", 3, "unchanged"); }, "no_data_request");
    stop([] { _PF_OTHER_SYMBOL_STOP("request.security", "A:X", "request.security(...) at line 3", 3, "unchanged"); }, "other_symbol_request");
    stop([] { _PF_OTHER_SYMBOL_STOP("request.security_lower_tf", nullptr, "request.security_lower_tf(...) at line 3", 3, "unchanged"); }, "other_symbol_request");
    stop([] { _PF_ARRAY_STOP("index_out_of_bounds", "get", "unchanged"); }, "pine_array_error");
    stop([] { _PF_ARRAY_STOP("slice_range_inverted", "slice", "unchanged"); }, "pine_array_error");
    stop([] { _PF_ARRAY_STOP("empty_array_access", "first", "unchanged"); }, "pine_array_error");
    stop([] { _PF_ARRAY_STOP("size_invalid", "new", "unchanged"); }, "pine_array_error");
    stop([] { _PF_COLLECTION_STOP("historical_modified", "array", "unchanged"); }, "pine_array_error");
    stop([] { _PF_COLLECTION_STOP("historical_modified", "matrix", "unchanged"); }, "pine_array_error");
    stop([] { _PF_COLLECTION_STOP("na_reference", "array", "unchanged"); }, "pine_na_reference");
    stop([] { _PF_COLLECTION_STOP("na_reference", "matrix", "unchanged"); }, "pine_na_reference");
    stop([] { _PF_NA_STOP("udt_object", "unchanged"); }, "pine_na_reference");
    stop([] { _PF_LIMIT_STOP("udt_objects", 2147483647LL, "unchanged"); }, "pine_runtime_limit");
    stop([] { _PF_UNSUPPORTED_STOP("nested_heikinashi_request", 3, "unchanged"); }, "request_unsupported");
    stop([] { _PF_STRING_STOP("substring_out_of_range", "unchanged"); }, "pine_string_error");
    stop([] { _PF_STRING_STOP("format_index_overflow", "unchanged"); }, "pine_string_error");
    stop([] { _PF_ENGINE_INVARIANT("unchanged", std::runtime_error); }, "engine_invariant");
    std::cout << "all stops propagated";
}
'''
    assert run_emitted_tu(cpp, driver, opt="-O2", label="both run-stop branches") == "all stops propagated"


@pytest.mark.parametrize("helper", HELPERS)
def test_helper_names_are_reserved(helper):
    assert helper in CPP_EMITTER_NAMES
    cpp = transpile(PRELUDE + f"{helper} = close\nplot({helper})\n")
    assert not re.search(rf"\bdouble {helper}\b", cpp)
    compile_cpp(cpp, label=f"reserved {helper}")


def test_feature_macro_is_reserved_and_shim_is_emitted_once():
    assert "PINEFORGE_HAS_RUN_FAILURE_CODES_V1" in CPP_STANDARD_MACROS
    cpp = transpile(PRELUDE + "PINEFORGE_HAS_RUN_FAILURE_CODES_V1 = close\n")
    assert cpp.count(RUN_STOP_SHIMS_CPP) == 1
    assert not re.search(r"\bdouble PINEFORGE_HAS_RUN_FAILURE_CODES_V1\b", cpp)


def test_legacy_normalization_keeps_actual_stop_calls_and_nested_settings_guards():
    cpp = transpile(PRELUDE + 'runtime.error(message="named")\n')
    normalized = _without_run_failure_scaffold(cpp)
    assert "PINEFORGE_HAS_RUN_FAILURE_CODES_V1" not in normalized
    assert 'pine_runtime_error(std::string("named"))' in normalized
    assert "#ifdef PF_SETTINGS_API_VERSION" in normalized
    assert "checked_settings::LatchedSettingsFailure" in normalized


def test_setter_stop_metadata_has_only_literal_entrypoints_and_reasons():
    cpp = transpile(PRELUDE)
    for entrypoint in ("strategy_set_input", "strategy_set_override",
                       "strategy_set_magnifier_volume_weighted"):
        assert f'_PF_SETTING_FAILURE(static_cast<GeneratedStrategy*>(s), "{entrypoint}",' in cpp
    assert '{"reason", "unparseable_value"}' in RUN_STOP_SHIMS_CPP


@pytest.mark.parametrize("method, arguments", [
    ("get", "3"), ("set", "3, 5.0"), ("insert", "3, 5.0"),
    ("remove", "3"), ("percentrank", "3"), ("fill", "5.0, 0, 3"),
    ("slice", "0, 3"),
])
def test_array_bounds_have_literal_reason_and_method(method, arguments):
    cpp = transpile(PRELUDE + f"items = array.new_float(2)\narray.{method}(items, {arguments})\n")
    assert f'_PF_ARRAY_STOP("index_out_of_bounds", "{method}",' in cpp


@pytest.mark.parametrize("method", ["first", "last", "pop", "shift"])
def test_empty_array_stops_have_literal_method(method):
    cpp = transpile(PRELUDE + f"items = array.new_float()\narray.{method}(items)\n")
    assert f'_PF_ARRAY_STOP("empty_array_access", "{method}", "Cannot use {method}() if array is empty.")' in cpp


@pytest.mark.parametrize("legacy", [False, True])
def test_collection_and_udt_stop_sites_are_coded(legacy):
    cpp = transpile(PRELUDE + '''
type Record
    float value
var record = Record.new(close)
items = array.from(record.value)
count = array.size(items[1])
''')
    for fragment in (
        '_PF_COLLECTION_STOP("historical_modified", "array",',
        '_PF_COLLECTION_STOP("na_reference", "array",',
        '_PF_NA_STOP("udt_object", "UDT access on na or invalid object ID")',
        '_PF_LIMIT_STOP("udt_objects", 2147483647LL, "UDT object-ID capacity exceeded")',
        '_PF_ENGINE_INVARIANT("UDT checkpoint generation exhausted", std::overflow_error)',
        '_PF_ENGINE_INVARIANT("invalid UDT undo generation", std::runtime_error)',
        '_PF_ENGINE_INVARIANT("invalid UDT coordinator checkpoint token", std::runtime_error)',
        '_PF_ENGINE_INVARIANT("UDT arena requires undo coordinator", std::invalid_argument)',
        '_PF_ENGINE_INVARIANT("UDT coordinator checkpoint is not active", std::runtime_error)',
        '_PF_ENGINE_INVARIANT("invalid UDT checkpoint token", std::runtime_error)',
        '_PF_INVARIANT_AT(_pf_records_, index)',
    ):
        assert fragment in cpp
    if legacy:
        cpp = _legacy_branch(cpp)
    compile_cpp(cpp, label="collection and UDT coded stops")


@pytest.mark.parametrize("symbol, literal", [('"A:X"', '"A:X"'), ('symbol', "nullptr")])
def test_other_symbol_metadata_never_reads_the_computed_symbol(symbol, literal):
    cpp = transpile(PRELUDE + 'symbol = input.string("A:X")\n'
                    + f'price = request.security({symbol}, "60", close)\n'
                    + 'strategy.entry("L", strategy.long, qty=price)\n')
    body = cpp.split("class GeneratedStrategy", 1)[1]
    assert f'_PF_OTHER_SYMBOL_STOP("request.security", {literal},' in body
    assert '_PF_OTHER_SYMBOL_STOP("request.security", _pf_symbol,' not in body


def test_forged_english_does_not_select_a_request_stop():
    english = 'request.security("A:X", "60", ...) at line 3: no data is pinned for this request, and its value was read'
    cpp = transpile(PRELUDE + f"runtime.error({json.dumps(english)})\n")
    body = cpp.split("class GeneratedStrategy", 1)[1]
    assert "pine_runtime_error(std::string(" in body
    assert "_PF_NO_DATA_STOP(" not in body and "_PF_OTHER_SYMBOL_STOP(" not in body


def test_request_stop_escapes_only_compile_time_metadata():
    marker = {"function": "request.security", "kind": "other_symbol", "symbol_literal": 'A:"X',
              "call": 'request.security("A:\"X", "60", ...) at line 7', "line": 7,
              "message": 'quote " and newline\n'}
    stop = request_stop(marker)
    assert '\\"' in stop and '\\n' in stop
    assert '\n' not in stop


@pytest.mark.parametrize("legacy", [False, True])
def test_checked_string_and_array_operations(legacy):
    cpp = transpile(PRELUDE + '''
text = str.substring("abcd", 1, 3)
value = str.tonumber("12.5")
missing = str.tonumber("not a number")
overflow = str.tonumber("1e9999")
formatted = str.format("{0} {1}", "text", 7)
items = array.new_int(2, 3)
''')
    if legacy:
        cpp = _legacy_branch(cpp)
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.on_source_bar(bar);
    assert(strategy.text == "bc");
    assert(strategy.value == 12.5);
    assert(is_na(strategy.missing));
    assert(is_na(strategy.overflow));
    assert(strategy.formatted == "text 7");
    assert(strategy.items.size() == 2 && strategy.items[0] == 3);
    try { pine_str_format_tv("{999999999999999999999999999999}", {_PFTvFormatValue(1)}); assert(false); }
    catch (const std::exception& error) { assert(std::string(error.what()) == "stoul"); }
    std::cout << "valid operations unchanged; overflow stopped";
}
'''
    assert run_emitted_tu(cpp, driver, opt="-O2", label="checked string operations") == "valid operations unchanged; overflow stopped"


@pytest.mark.parametrize("body, english", [
    ('items = array.new_float(-1)', "cannot create std::vector larger than max_size()"),
    ('items = array.new_float(int(na))', "cannot create std::vector larger than max_size()"),
    ('text = str.substring("abc", 5)', "basic_string::substr"),
])
def test_invalid_sizes_and_substring_stop(body, english):
    cpp = transpile(PRELUDE + body + "\n")
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    try { strategy.on_source_bar(bar); assert(false); }
    catch (const std::exception& error) { std::cout << error.what(); }
}
'''
    assert english in run_emitted_tu(cpp, driver, opt="-O2", label="checked invalid size or substring")


@pytest.mark.parametrize("legacy", [False, True])
def test_wrapper_keeps_the_inner_code_not_its_english(legacy):
    cpp = transpile(PRELUDE)
    before = "try { _pf_run_backtest_full_impl(s, bars, n, input_tf, script_tf, bar_magnifier, magnifier_samples, magnifier_dist, out); }"
    after = 'try { _PF_NO_DATA_STOP("request.financial", "request.financial(...) at line 3", 3, "copied text"); }'
    assert cpp.count(before) == 1
    cpp = cpp.replace(before, after)
    if legacy:
        cpp = _legacy_branch(cpp)
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    ReportC report{};
    run_backtest_full(&strategy, nullptr, 0, "", "", 0, 4, 0, &report);
    assert(strategy.last_error() == "run_backtest_full: copied text");
#ifdef PINEFORGE_HAS_RUN_FAILURE_CODES_V1
    assert(std::string(run_failure_code_of(strategy)) == "no_data_request");
    assert(std::string(run_failure_args_of(strategy)) == R"({"call":"request.financial(...) at line 3","function":"request.financial","line":3})");
#endif
    std::cout << "wrapper kept failure";
}
'''
    assert run_emitted_tu(cpp, driver, opt="-O2", label="wrapper inner code") == "wrapper kept failure"


@pytest.mark.parametrize("legacy", [False, True])
def test_setter_latch_retains_its_first_code_and_native_exception_type(legacy):
    cpp = transpile(PRELUDE + 'quantity = input.float(1.0, "Quantity")\n')
    if legacy:
        cpp = _legacy_branch(cpp)
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    strategy_set_override(&strategy, "initial_capital", "invalid");
    assert(strategy.last_error() == "strategy_set_override: stod");
    strategy_set_override(&strategy, "slippage", "invalid");
    assert(strategy.last_error() == "strategy_set_override: stod");
    try { strategy._pf_require_settings_ok(); assert(false); }
#ifdef PF_SETTINGS_API_VERSION
    catch (const checked_settings::LatchedSettingsFailure& error) {
#else
    catch (const std::runtime_error& error) {
#endif
        assert(std::string(error.what()) == "strategy_set_override: stod");
#ifdef PINEFORGE_HAS_RUN_FAILURE_CODES_V1
        assert(classify_run_failure(error).code == RunFailureCode::setting_rejected);
#endif
    }
#ifdef PINEFORGE_HAS_RUN_FAILURE_CODES_V1
    assert(std::string(run_failure_code_of(strategy)) == "setting_rejected");
    assert(std::string(run_failure_args_of(strategy)) == R"({"entrypoint":"strategy_set_override","reason":"unparseable_value"})");
#endif
    std::cout << "first setting refusal retained";
}
'''
    assert run_emitted_tu(cpp, driver, opt="-O2", label="setter failure code") == "first setting refusal retained"


@pytest.mark.parametrize("legacy", [False, True])
def test_checked_construction_exposes_the_original_error(legacy):
    cpp = transpile(PRELUDE)
    assert cpp.count("        configure_pine_strategy(cfg);") == 1
    cpp = cpp.replace("        configure_pine_strategy(cfg);", '        throw std::runtime_error("constructor stopped");')
    if legacy:
        cpp = _legacy_branch(cpp)
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    assert(strategy_create(nullptr) == nullptr);
#ifdef PF_SETTINGS_API_VERSION
    void* strategy = nullptr;
    char error[64];
    assert(strategy_create_checked(nullptr, &strategy, error, sizeof(error)) == PF_SETTINGS_EXCEPTION);
    assert(strategy == nullptr && std::string(error) == "constructor stopped");
#endif
    std::cout << "checked creation kept text";
}
'''
    assert run_emitted_tu(cpp, driver, opt="-O2", label="checked creation failure") == "checked creation kept text"


@pytest.mark.parametrize("legacy", [False, True])
def test_matrix_collection_history_uses_its_literal_collection(legacy):
    cpp = transpile(PRELUDE + 'var values = matrix.new<float>(1, 1, 2.0)\ncount = matrix.rows(values[1])\n')
    assert '_PF_COLLECTION_STOP("historical_modified", "matrix",' in cpp
    assert '_PF_COLLECTION_STOP("na_reference", "matrix",' in cpp
    if legacy:
        cpp = _legacy_branch(cpp)
    compile_cpp(cpp, label="matrix collection code branches")
