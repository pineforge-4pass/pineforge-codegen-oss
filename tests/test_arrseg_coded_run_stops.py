"""Array-history stops retain their English and the runtime's failure identity."""

import json
import subprocess
import sys

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.run_stops import RUN_STOP_SHIMS_CPP
from pineforge_codegen.collection_history import (
    HISTORICAL_CHANGE_MESSAGE, NA_ARRAY_MESSAGE, NA_MATRIX_MESSAGE,
)
from tests import _compile as compile_env
from tests._compile import compile_cpp, run_emitted_tu
from tests._e2e import build_strategy_library, chart_feed_head, skip_unless_e2e_env


HEADER = '//@version=6\nstrategy("collection history stops")\n'
STOPS = {
    "bound_array": (
        HEADER + "a = array.from(close)\npb = a[20]\nmissing = na(pb)\nresult = pb.size()\n",
        "Cannot call array methods when id of array is na.",
        "pine_na_reference", {"object": "array"},
        '_PF_COLLECTION_STOP("na_reference", "array",'),
    "bound_matrix": (
        HEADER + "m = matrix.new<float>(1, 1, 0.0)\npb = m[20]\nresult = pb.get(0, 0)\n",
        "Cannot call matrix methods when id of matrix is na.",
        "pine_na_reference", {"object": "matrix"},
        '_PF_COLLECTION_STOP("na_reference", "matrix",'),
    "history_element_index": (
        HEADER + "a = array.new<float>()\na[20] := 3.0\n",
        "Array history element index is out of bounds.",
        "pine_array_error", {"reason": "index_out_of_bounds"},
        '_PF_ARRAY_STOP("index_out_of_bounds", nullptr,'),
}


def generated(source, legacy):
    cpp = transpile(source)
    assert cpp.count(RUN_STOP_SHIMS_CPP) == 1
    if legacy:
        cpp = cpp.replace(RUN_STOP_SHIMS_CPP,
                          "#undef PINEFORGE_HAS_RUN_FAILURE_CODES_V1\n" + RUN_STOP_SHIMS_CPP)
        return cpp + "\n#ifdef PINEFORGE_HAS_RUN_FAILURE_CODES_V1\n#error legacy stops required\n#endif\n"
    header = compile_env._ENGINE_INC / "pineforge" / "run_failure.hpp" if compile_env._ENGINE_INC else None
    if header is None or not header.is_file():
        pytest.skip("engine has no coded run-stop interface")
    return cpp + "\n#ifndef PINEFORGE_HAS_RUN_FAILURE_CODES_V1\n#error coded stops required\n#endif\n"


def test_history_stop_english_constants_keep_their_legacy_bytes():
    assert NA_ARRAY_MESSAGE == STOPS["bound_array"][1]
    assert NA_MATRIX_MESSAGE == STOPS["bound_matrix"][1]


@pytest.mark.parametrize("legacy", [False, True], ids=["coded", "legacy"])
def test_bound_array_history_read_compiles_in_both_branches(legacy):
    source = HEADER + """a = array.from(close)
b = a[1]
float result = 0.0
if bar_index > 1
    result := na(b[1]) ? 0.0 : (b[1]).size()
"""
    compile_cpp(generated(source, legacy), label="bound array history read")


@pytest.mark.parametrize("legacy", [False, True], ids=["coded", "legacy"])
@pytest.mark.parametrize("name", STOPS)
def test_history_stop_compiles_in_both_branches(name, legacy):
    source, english, _, _, shim = STOPS[name]
    cpp = generated(source, legacy)
    assert shim in cpp
    assert english in cpp
    compile_cpp(cpp, label=f"{name} {'legacy' if legacy else 'coded'} stop")


@pytest.mark.parametrize("legacy", [False, True], ids=["coded", "legacy"])
@pytest.mark.parametrize("name", STOPS)
def test_history_stop_keeps_english_and_code(name, legacy):
    source, english, code, arguments, _ = STOPS[name]
    cpp = generated(source, legacy)
    driver = r'''
#include <cassert>
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    try { strategy.on_source_bar(bar); assert(false); }
    catch (const std::exception& error) {
#ifdef PINEFORGE_HAS_RUN_FAILURE_CODES_V1
        auto failure = classify_run_failure(error);
        assert(std::string(run_failure_code_name(failure.code)) == EXPECTED_CODE);
        assert(failure.args && *failure.args == EXPECTED_ARGS);
#endif
        std::cout << error.what();
    }
}
'''.replace("EXPECTED_CODE", json.dumps(code)).replace(
        "EXPECTED_ARGS", json.dumps(json.dumps(arguments, separators=(",", ":"), sort_keys=True)))
    assert run_emitted_tu(cpp, driver, opt="-O2", label=name) == english


@pytest.mark.parametrize("legacy", [False, True], ids=["coded", "legacy"])
def test_nullable_array_traits_cover_every_history_operation(legacy):
    cpp = generated(STOPS["bound_array"][0], legacy)
    driver = r'''
#include <cassert>
#include <iostream>
template <typename Action>
void stopped(Action action, const char* english, const char* code, const char* args) {
    try { action(); assert(false); }
    catch (const std::exception& error) {
        assert(std::string(error.what()) == english);
#ifdef PINEFORGE_HAS_RUN_FAILURE_CODES_V1
        auto failure = classify_run_failure(error);
        assert(std::string(run_failure_code_name(failure.code)) == code);
        assert(failure.args && *failure.args == args);
#endif
    }
}
int main() {
    using Value = _PFArrayHistoryValue<std::vector<double>>;
    using Traits = _PFCollectionTraits<Value>;
    Value missing;
    assert(is_na(missing));
    assert(Traits::is_na(missing));
    assert(!Traits::freeze(missing));
    assert(Traits::copy(missing).is_na());
    assert(Traits::reference(nullptr).is_na());
    Value current(std::vector<double>{2.0});
    _PFCollectionHistory<Value> history;
    history.open(current, true);
    history.close(current);
    history.open(current, true);
    assert(!history.is_na(1));
    assert(history.is_na(20));
    assert(!history.is_na(0, current));
    assert(history.at(1).get()[0] == 2.0);
    assert(history.at(0, current).get()[0] == 2.0);
    assert(history.value(1).get()[0] == 2.0);
    assert(history.value(0, current).get()[0] == 2.0);
    assert(history.reference(1).get()[0] == 2.0);
    assert(history.reference(0, current).get()[0] == 2.0);
    assert(history.value(20).is_na());
    assert(history.reference(20).is_na());
    assert(history.is_na(0, missing));
    assert(history.value(0, missing).is_na());
    assert(history.reference(0, missing).is_na());
    assert(&history.changed(0, current) == &current);
    assert(&history.changed(0, missing) == &missing);
    assert(std::string(_PFCollectionTraits<Value>::na_message()) == NA_ENGLISH);
    history.close(Value{});
    stopped([&] { (void)missing.get(); }, NA_ENGLISH, "pine_na_reference", R"({"object":"array"})");
    stopped([] { Traits::na_stop(); }, NA_ENGLISH, "pine_na_reference", R"({"object":"array"})");
    stopped([] { Traits::history_stop(); }, HISTORY_ENGLISH, "pine_array_error", R"({"collection":"array","reason":"historical_modified"})");
    stopped([] { _PFCollectionTraits<std::vector<double>>::na_stop(); }, NA_ENGLISH, "pine_na_reference", R"({"object":"array"})");
    stopped([] { _PFCollectionTraits<std::vector<double>>::history_stop(); }, HISTORY_ENGLISH, "pine_array_error", R"({"collection":"array","reason":"historical_modified"})");
    stopped([&] { (void)history.at(20); }, NA_ENGLISH, "pine_na_reference", R"({"object":"array"})");
    stopped([&] { (void)history.changed(20, current); }, NA_ENGLISH, "pine_na_reference", R"({"object":"array"})");
    stopped([&] { (void)history.changed(1, current); }, HISTORY_ENGLISH, "pine_array_error", R"({"collection":"array","reason":"historical_modified"})");
    stopped([] { _pf_collection_history_changed<Value>(); }, HISTORY_ENGLISH, "pine_array_error", R"({"collection":"array","reason":"historical_modified"})");
    std::cout << "every nullable-array trait instantiated";
}
'''.replace("NA_ENGLISH", json.dumps(NA_ARRAY_MESSAGE)).replace(
        "HISTORY_ENGLISH", json.dumps(HISTORICAL_CHANGE_MESSAGE))
    assert run_emitted_tu(cpp, driver, opt="-O2", label="nullable array traits") == "every nullable-array trait instantiated"


@pytest.mark.parametrize("name", STOPS)
def test_history_stop_run_json_reports_code_and_args(name, tmp_path):
    engine = skip_unless_e2e_env()
    source, english, code, arguments, _ = STOPS[name]
    cpp = generated(source, False)
    build_strategy_library(cpp, tmp_path)
    feed = chart_feed_head(engine, tmp_path, 4)
    result = subprocess.run(
        [sys.executable, str(engine / "docker" / "run_json.py"),
         "--so", str(tmp_path / "strategy.so"), "--ohlcv", str(feed),
         "--generated-cpp", str(tmp_path / "generated.cpp")],
        capture_output=True, text=True, timeout=180)
    assert result.returncode == 1, f"{result.stdout}\n{result.stderr}"
    payload = json.loads(result.stdout)
    assert set(payload) == {"engine", "error", "code", "args"}
    assert payload["engine"] == "pineforge"
    assert payload["error"].endswith(english)
    assert payload["code"] == code
    assert payload["args"] == arguments
