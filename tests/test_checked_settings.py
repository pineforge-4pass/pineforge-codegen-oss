"""The opt-in settings ABI validates before mutating real generated strategies."""
import json
import os
import re
import shutil
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp, run_emitted_tu


SOURCE = '''//@version=6
strategy("checked settings", initial_capital=10000, default_qty_value=1)
enum Side
    neutral
    long
    short
const int FLOOR = 1
length = input.int(3, "Length", minval=FLOOR, maxval=50, step=2)
threshold = input.float(2.5, "Threshold", minval=0, maxval=10, step=0.25)
enabled = input.bool(true, "Enabled")
mode = input.string("fast", "Mode", options=["fast", "slow"])
side = input.enum(Side.long, "Side", options=[Side.long, Side.short])
choice = input.int(3, "Choice", options=[3, 5, 7])
stamp = input.time(1577836800000, "Stamp")
shade = input.color(#ff0000, "Shade")
if enabled and bar_index % length == 0
    strategy.entry("entry", strategy.long, qty=threshold)
'''


def test_metadata_and_exception_wrappers_are_emitted():
    cpp = transpile(SOURCE)
    for symbol in ("strategy_settings_api_version", "strategy_create_checked",
                   "strategy_set_input_checked", "strategy_set_override_checked",
                   "strategy_get_effective_settings", "run_backtest_full_checked"):
        assert symbol in cpp
    assert '#if __has_include(<pineforge/checked_settings.hpp>)' in cpp
    assert '{"Length", "int"' in cpp
    assert '{"Mode", "string"' in cpp and '{std::string("fast"), std::string("slow")}' in cpp
    assert '{"Side.long", "Side.short"}' in cpp
    assert cpp.count('catch (...)') >= 7
    for entrypoint in ("run_backtest", "run_backtest_full", "strategy_set_input",
                       "strategy_set_override", "strategy_set_magnifier_volume_weighted"):
        recorder = "_pf_record_setting_failure" if entrypoint.startswith("strategy_set_") else "_pf_record_failure"
        assert f'{recorder}("{entrypoint}", _pf_error.what())' in cpp
        assert f'{recorder}("{entrypoint}", "unknown C++ exception")' in cpp
    assert 'bool _pf_setting_failed_ = false;' in cpp
    assert 'std::string _pf_setting_failure_;' in cpp
    assert 'throw ::pineforge::checked_settings::LatchedSettingsFailure(' in cpp
    assert '        _pf_require_settings_ok();\n        _pf_script_state_checkpoint_.reset();' in cpp
    assert 'unknown input key' in cpp and 'unknown override key' in cpp
    assert 'return new GeneratedStrategy();' in cpp


@pytest.mark.parametrize("optimization", ["-O0", "-O2"])
def test_checked_values_and_receipt_at_runtime(optimization):
    driver = r'''
#include <cassert>
#include <iostream>
#include <vector>
int main() {
    void* strategy = nullptr;
    char error[256]{};
    assert(strategy_create_checked(nullptr, &strategy, error, sizeof(error)) == PF_SETTINGS_OK);
    for (const auto& invalid : std::vector<std::pair<const char*, const char*>>{
            {"Lenght", "3"}, {"Length", "3x"}, {"Length", "0"},
            {"Enabled", "yes"}, {"Threshold", "NaN"}, {"Threshold", "Inf"},
            {"Mode", "typo"}, {"Side", "Side.neutral"}, {"Side", "3"},
            {"Choice", "4"}, {"Length", "5.5"}, {"Length", "5.0000000000000001"},
            {"Length", "2147483648.0"}, {"Length", "NaN"}, {"Length", "Inf"}}) {
        assert(strategy_set_input_checked(strategy, invalid.first, invalid.second,
                                          error, sizeof(error)) == PF_SETTINGS_INVALID_ARGUMENT);
        assert(error[0]);
    }
    assert(strategy_set_input_checked(strategy, "Length", "5.0", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_input_checked(strategy, "Length", "5e0", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_input_checked(strategy, "Side", "short", error, sizeof(error)) == PF_SETTINGS_INVALID_ARGUMENT);
    assert(std::string(error) == "invalid enum option");
    assert(strategy_set_input_checked(strategy, "Enabled", "0", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_input_checked(strategy, "Side", "Side.short", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_input_checked(strategy, "Stamp", "1700000000000", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_override_checked(strategy, "default_qty_type", "typo", error, sizeof(error)) == PF_SETTINGS_INVALID_ARGUMENT);
    assert(strategy_set_override_checked(strategy, "slippage", "2x", error, sizeof(error)) == PF_SETTINGS_INVALID_ARGUMENT);
    assert(strategy_set_override_checked(strategy, "process_orders_on_close", "yes", error, sizeof(error)) == PF_SETTINGS_INVALID_ARGUMENT);
    assert(strategy_set_override_checked(strategy, "default_qty_type", "strategy.cash", error, sizeof(error)) == PF_SETTINGS_OK);
    assert(strategy_set_override_checked(strategy, "default_qty_value", "200", error, sizeof(error)) == PF_SETTINGS_OK);
    size_t required = 0;
    assert(strategy_get_effective_settings(strategy, nullptr, 0, &required, error, sizeof(error)) == PF_SETTINGS_BUFFER_TOO_SMALL);
    std::vector<char> receipt(required);
    assert(strategy_get_effective_settings(strategy, receipt.data(), receipt.size(), &required, error, sizeof(error)) == PF_SETTINGS_OK);
    std::cout << receipt.data();
    strategy_free(strategy);
}
'''
    receipt = json.loads(run_emitted_tu(transpile(SOURCE), driver, opt=optimization,
                                       label="checked settings"))
    inputs = {entry["name"]: entry for entry in receipt["inputs"]}
    assert inputs["Length"]["min"] == 1
    assert inputs["Length"]["max"] == 50
    assert inputs["Length"]["step"] == 2
    assert inputs["Length"]["effective_value"] == "5"
    assert inputs["Length"]["kind"] == "int"
    assert inputs["Enabled"]["effective_value"] == "false"
    assert inputs["Side"]["options"] == ["Side.long", "Side.short"]
    assert inputs["Side"]["effective_value"] == "2"
    assert inputs["Choice"]["options"] == ["3", "5", "7"]
    assert inputs["Stamp"]["effective_value"] == "1700000000000"
    assert inputs["Shade"]["default"] == "4294901760"
    assert inputs["Shade"]["effective_value"] == "4294901760"
    assert inputs["Shade"]["type"] == "int" and inputs["Shade"]["kind"] == "string"
    overridden = {entry["name"]: entry for entry in receipt["overrides"]}["default_qty_value"]
    assert overridden["default"] == "1" and overridden["effective_value"] == "200"
    assert {entry["name"]: entry for entry in receipt["overrides"]}["default_qty_type"]["effective_value"] == "cash"


def test_duplicate_input_key_is_only_refused_by_checked_setter():
    source = '''//@version=6
strategy("duplicate settings")
first = input.int(3, "Shared")
second = input.int(7, "Shared")
'''
    driver = r'''
#include <cassert>
int main() {
    void* strategy = strategy_create(nullptr);
    char error[128]{};
    assert(strategy_set_input_checked(strategy, "Shared", "9", error, sizeof(error)) == PF_SETTINGS_UNSUPPORTED);
    strategy_set_input(strategy, "Shared", "9");
    strategy_free(strategy);
}
'''
    run_emitted_tu(transpile(source), driver, opt="-O0", label="ambiguous checked input")


def test_enum_options_supply_type_for_nonliteral_default():
    source = SOURCE.replace('side = input.enum(Side.long,',
                            'defaultSide = Side.long\nside = input.enum(defaultSide,')
    driver = r'''
#include <cassert>
#include <iostream>
#include <vector>
int main() {
    void* strategy = strategy_create(nullptr);
    char error[256]{};
    size_t required = 0;
    assert(strategy_get_effective_settings(strategy, nullptr, 0, &required, error, sizeof(error)) == PF_SETTINGS_BUFFER_TOO_SMALL);
    std::vector<char> receipt(required);
    assert(strategy_get_effective_settings(strategy, receipt.data(), receipt.size(), &required, error, sizeof(error)) == PF_SETTINGS_OK);
    const std::string before = receipt.data();
    Bar bars[]{{100, 101, 99, 100, 1000, 1577836800000LL}};
    ReportC report{};
    run_backtest(strategy, bars, 1, &report);
    report_free(&report);
    assert(static_cast<GeneratedStrategy*>(strategy)->side == Side__long_);
    assert(strategy_get_effective_settings(strategy, receipt.data(), receipt.size(), &required, error, sizeof(error)) == PF_SETTINGS_OK);
    assert(before == receipt.data());
    std::cout << receipt.data();
    strategy_free(strategy);
}
'''
    receipt = json.loads(run_emitted_tu(transpile(source), driver, opt="-O0", label="enum option inference"))
    setting = {entry["name"]: entry for entry in receipt["inputs"]}["Side"]
    assert setting["supported"] is True
    assert setting["options"] == ["Side.long", "Side.short"]
    assert setting["option_values"] == ["1", "2"]
    assert setting["default"] == setting["effective_value"] == "1"


SETTINGS_MEMBER_NAMES = (
    "StreamPhase", "config_", "inputs_", "last_error", "last_error_", "override_",
    "script_bars_processed", "stream_phase_", "_pf_record_failure",
    "_pf_record_setting_failure", "_pf_require_settings_ok",
    "_pf_setting_failed_", "_pf_setting_failure_",
    "_pf_refuse_failed_setting", "_pf_settings_declared_config", "_pf_settings_inputs",
    "_pf_settings_overrides", "_pf_set_input_checked", "_pf_set_override_checked",
    "_pf_settings_receipt",
    "_pf_close_entries_rule_word", "_pf_default_qty_type_word", "_pf_commission_type_word",
    "LatchedSettingsFailure", "Error", "Setting", "require", "copy_error", "boundary",
    "number", "integer", "real", "quote", "validate", "describe", "receipt", "checked_settings",
)


@pytest.mark.parametrize("optimization", ["-O0", "-O2"])
def test_setting_names_do_not_shadow_settings_scaffold(optimization):
    source = '\n'.join([
        '//@version=6', 'strategy("setting member names")',
        *(f'{name} = input.int(3, "{name}")' for name in SETTINGS_MEMBER_NAMES),
    ])
    names = ', '.join(json.dumps(name) for name in SETTINGS_MEMBER_NAMES)
    driver = r'''
#include <cassert>
#include <iostream>
#include <vector>
int main() {
    void* strategy = nullptr;
    char error[256]{};
    assert(strategy_create_checked(nullptr, &strategy, error, sizeof(error)) == PF_SETTINGS_OK);
    for (const char* name : std::vector<const char*>{NAMES}) {
        assert(strategy_set_input_checked(strategy, name, "5", error, sizeof(error)) == PF_SETTINGS_OK);
    }
    size_t required = 0;
    assert(strategy_get_effective_settings(strategy, nullptr, 0, &required, error, sizeof(error)) == PF_SETTINGS_BUFFER_TOO_SMALL);
    std::vector<char> receipt(required);
    assert(strategy_get_effective_settings(strategy, receipt.data(), receipt.size(), &required, error, sizeof(error)) == PF_SETTINGS_OK);
    std::cout << receipt.data();
    strategy_free(strategy);
}
'''.replace('NAMES', names)
    receipt = json.loads(run_emitted_tu(transpile(source), driver, opt=optimization,
                                       label="setting member names"))
    inputs = {entry["name"]: entry for entry in receipt["inputs"]}
    assert set(inputs) == set(SETTINGS_MEMBER_NAMES)
    assert all(entry["effective_value"] == "5" for entry in inputs.values())


def test_settings_name_reservations_keep_default_corpus_emission_identical(monkeypatch):
    from pineforge_codegen.codegen import helpers

    corpus = os.environ.get("PINEFORGE_ENGINE_CORPUS")
    if not corpus:
        pytest.skip("PINEFORGE_ENGINE_CORPUS is needed for emitted-byte comparison")
    sources = sorted(Path(corpus).glob("*/*/strategy.pine"))
    assert len(sources) >= 300
    current_reserved = helpers.CPP_RESERVED
    previous_reserved = current_reserved - set(SETTINGS_MEMBER_NAMES)
    for path in sources:
        source = path.read_text(encoding="utf-8")
        current = transpile(source, filename=str(path))
        with monkeypatch.context() as context:
            context.setattr(helpers, "CPP_RESERVED", previous_reserved)
            previous = transpile(source, filename=str(path))
        assert current == previous, str(path)


SETTINGS_HELPER_NAMES = (
    "LatchedSettingsFailure", "Error", "Setting", "require", "copy_error", "boundary",
    "number", "integer", "real", "quote", "validate", "describe", "receipt", "checked_settings",
)


@pytest.mark.parametrize("shape", ["variable", "function", "udt", "enum"])
def test_settings_helpers_are_not_shadowed_by_script_declarations(shape):
    declarations = []
    for name in SETTINGS_HELPER_NAMES:
        if shape == "variable":
            declarations.append(f"{name} = 1")
        elif shape == "function":
            declarations.append(f"{name}(value) => value + 1")
        elif shape == "udt":
            declarations.append(f"type {name}\n    int value")
        else:
            declarations.append(f"enum {name}\n    first\n    second")
    source = '\n'.join(['//@version=6', 'strategy("helper declarations")',
                        *declarations, 'length = input.int(3, "Length")'])
    compile_cpp(transpile(source), label=f"settings helpers as {shape}")


def test_settings_namespace_names_are_root_qualified_and_version_guarded():
    cpp = transpile(SOURCE)
    assert "using namespace pineforge::checked_settings" not in cpp
    guarded = False
    for line in cpp.splitlines():
        if line == "#ifdef PF_SETTINGS_API_VERSION":
            guarded = True
        elif line in ("#else", "#endif"):
            guarded = False
        if "pineforge::checked_settings::" in line:
            assert guarded, line
            assert re.search(r"(?<!:)\bpineforge::checked_settings::", line) is None
        if guarded:
            for name in SETTINGS_HELPER_NAMES:
                assert re.search(rf"(?<![\w:]){name}\s*(?:\(|\{{|\*)", line) is None, line


def test_generated_tu_compiles_without_settings_header(tmp_path, monkeypatch):
    from tests import _compile

    _compile.skip_if_no_compile_env()
    header_tree = tmp_path / "include"
    shutil.copytree(_compile._ENGINE_INC, header_tree)
    (header_tree / "pineforge" / "checked_settings.hpp").unlink()
    public_header = header_tree / "pineforge" / "pineforge.h"
    public_header.write_text(re.sub(r"^#define PF_SETTINGS_API_VERSION.*\n", "",
                                   public_header.read_text(), flags=re.MULTILINE))
    monkeypatch.setattr(_compile, "_ENGINE_INC", header_tree)
    compile_cpp(transpile(SOURCE), label="legacy engine without checked settings header")


@pytest.mark.parametrize("options", ["Side.long, alternate", "Side.long, Other.short"])
def test_enum_options_require_literal_members_of_one_enum(options):
    source = SOURCE.replace('const int FLOOR = 1',
                            'enum Other\n    short\nalternate = Side.short\nconst int FLOOR = 1')
    source = source.replace('options=[Side.long, Side.short]', f'options=[{options}]')
    cpp = transpile(source)
    row = next(line for line in cpp.splitlines() if '{"Side", "enum"' in line)
    assert ', false,' in row


def test_unresolved_enum_default_does_not_read_script_member_in_receipt():
    source = SOURCE.replace('side = input.enum(Side.long,',
                            'var defaultSide = Side.long\nside = input.enum(defaultSide,')
    cpp = transpile(source)
    metadata = cpp.split('std::vector<::pineforge::checked_settings::Setting> _pf_settings_inputs()', 1)[1]
    metadata = metadata.split('std::vector<::pineforge::checked_settings::Setting> _pf_settings_overrides()', 1)[0]
    assert 'defaultSide' not in metadata
    assert '"Side", "enum", std::string("na")' in metadata
    assert ', false,' in metadata
    receipt = cpp.split('std::string _pf_settings_receipt()', 1)[1].split('#endif', 1)[0]
    assert 'defaultSide' not in receipt
    assert 'get_input_int("Side", defaultSide)' in cpp


def test_legacy_setter_failure_is_readable_and_prevents_execution():
    driver = r'''
#include <cassert>
int main() {
    auto* strategy = static_cast<GeneratedStrategy*>(strategy_create(nullptr));
    char error[256]{};
    strategy_set_override(strategy, "pyramiding", "abc");
    const auto failure = strategy->last_error();
    assert(failure.find("strategy_set_override:") == 0 && failure.size() > 23);
    ReportC report{};
    report.total_trades = 99;
    run_backtest_full(strategy, nullptr, 0, "", "", 0, 4, 0, &report);
    assert(report.total_trades == 0 && strategy->last_error() == failure);
    assert(run_backtest_full_checked(strategy, nullptr, 0, "", "", 0, 4, 0,
                                     &report, error, sizeof(error)) == PF_SETTINGS_RUN_FAILED);
    assert(std::string(error) == failure);
    strategy_free(strategy);
}
'''
    run_emitted_tu(transpile(SOURCE), driver, opt="-O0", label="legacy exception readback")


@pytest.mark.parametrize("optimization", ["-O0", "-O2"])
def test_legacy_setter_failure_survives_error_clears_and_refuses_stream(optimization):
    driver = r'''
#include <cassert>
int main() {
    auto* strategy = static_cast<GeneratedStrategy*>(strategy_create(nullptr));
    strategy_set_override(strategy, "pyramiding", "abc");
    const std::string failure = strategy_get_last_error(strategy);
    assert(failure.find("strategy_set_override:") == 0 && failure.size() > 23);
    pf_bar_t warmup{100, 101, 99, 100, 1000, 1577836800000LL};
    assert(strategy_stream_begin(strategy, &warmup, 1, "1", "1") == -1);
    assert(std::string(strategy_get_last_error(strategy)).find(failure) != std::string::npos);
    assert(strategy_last_run_status(strategy) == 1);
    strategy_free(strategy);

    strategy = static_cast<GeneratedStrategy*>(strategy_create(nullptr));
    strategy_set_override(strategy, "pyramiding", "abc");
    assert(std::string(strategy_get_last_error(strategy)) == failure);
    assert(strategy_set_aux_security_feed(strategy, nullptr, 0, "1") == 0);
    assert(strategy_set_native_security_feed(strategy, "D", nullptr, 0) == 0);
    assert(std::string(strategy_get_last_error(strategy)).empty());
    strategy_set_override(strategy, "pyramiding", "3");
    strategy_set_override(strategy, "default_qty_value", "abc");
    assert(std::string(strategy_get_last_error(strategy)) == failure);
    ReportC report{};
    report.total_trades = 99;
    run_backtest(strategy, nullptr, 0, &report);
    assert(report.total_trades == 0 && strategy->last_error() == failure);
    assert(strategy_last_run_status(strategy) == 1);
    assert(strategy_set_native_security_feed(strategy, "D", nullptr, 0) == 0);
    assert(std::string(strategy_get_last_error(strategy)).empty());
    report.total_trades = 99;
    run_backtest_full(strategy, nullptr, 0, "", "", 0, 4, 0, &report);
    assert(report.total_trades == 0 && strategy->last_error() == failure);
    assert(strategy_last_run_status(strategy) == 1);
    assert(strategy_set_aux_security_feed(strategy, nullptr, 0, "1") == 0);
    char error[256]{};
    assert(run_backtest_full_checked(strategy, nullptr, 0, "", "", 0, 4, 0,
                                    &report, error, sizeof(error)) == PF_SETTINGS_RUN_FAILED);
    assert(std::string(error) == failure);
    assert(strategy_last_run_status(strategy) == 1);
    assert(strategy_set_input_checked(strategy, "Length", "5", error, sizeof(error)) == PF_SETTINGS_RUN_FAILED);
    assert(std::string(error) == failure);
    assert(strategy_set_override_checked(strategy, "pyramiding", "5", error, sizeof(error)) == PF_SETTINGS_RUN_FAILED);
    assert(std::string(error) == failure);
    size_t required = 99;
    assert(strategy_get_effective_settings(strategy, nullptr, 0, &required, error, sizeof(error)) == PF_SETTINGS_RUN_FAILED);
    assert(required == 0 && std::string(error) == failure);
    assert(strategy_set_aux_security_feed(strategy, nullptr, 0, "1") == 0);
    assert(strategy_stream_begin(strategy, &warmup, 1, "1", "1") == -1);
    assert(std::string(strategy_get_last_error(strategy)) == failure);
    strategy_free(strategy);

    strategy = static_cast<GeneratedStrategy*>(strategy_create(nullptr));
    strategy_set_override(strategy, "pyramiding", "abc");
    assert(strategy_set_aux_security_feed(strategy, nullptr, 0, "1") == 0);
    Bar bars[]{{100, 101, 99, 100, 1000, 1577836800000LL}};
    strategy->run(bars, 1);
    assert(std::string(strategy_get_last_error(strategy)).find(failure) != std::string::npos);
    assert(strategy->script_bars_processed() == 0);
    ReportC retry_report{};
    retry_report.total_trades = 99;
    run_backtest(strategy, bars, 1, &retry_report);
    assert(retry_report.total_trades == 0 && strategy->last_error() == failure);
    strategy_free(strategy);

    strategy = static_cast<GeneratedStrategy*>(strategy_create(nullptr));
    assert(strategy_stream_begin(strategy, &warmup, 1, "1", "1") == 0);
    strategy_free(strategy);
}
'''
    run_emitted_tu(transpile(SOURCE), driver, opt=optimization,
                   label="sticky legacy setter failure in batch and stream")
