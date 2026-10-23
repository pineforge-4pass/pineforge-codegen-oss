"""Tests for the generated-C++ allowlist checker (CR-164 S1).

(1) Every generated ``.cpp`` committed in this repository must pass with no
findings -- the checker may never refuse real codegen output.
(2) Each rule has one small hand-written translation unit that produces
exactly that rule's finding.
(3) The two sealed timezone templates keep their environment calls and the
same templates with any other spelling do not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.cpp_allowlist import (
    Finding,
    check_generated_cpp,
)

ROOT = Path(__file__).resolve().parents[1]
COMMITTED_GENERATED_CPP = sorted((ROOT / "tests").rglob("*.cpp"))

# The committed golden is normalised by ``tests/_legacy_cpp.legacy_cpp`` and
# predates the unconditional settings and run-stop blocks, so it cannot show
# those paths.  These fixtures are transpiled here, against the current
# emitter, for the positive corpus: settings/inputs (which emit ``<::``),
# timezone use (the sealed timestamp calendar run), drawing and request.
CURRENT_CODEGEN_FIXTURES = (
    "tests/fixtures/matrix_eigen_pca.pine",
    "tests/fixtures/input_metadata_numeric.pine",
    "tests/fixtures/drawing_lifetime/w9dg-label-collect/strategy.pine",
    "tests/fixtures/request_discovery/case_strategy.pine",
)


def test_committed_generated_cpp_passes_the_allowlist():
    assert COMMITTED_GENERATED_CPP, "no committed generated .cpp found"
    for path in COMMITTED_GENERATED_CPP:
        findings = check_generated_cpp(path.read_text(encoding="utf-8"))
        assert findings == [], f"{path}: {findings}"


@pytest.mark.parametrize("relative", CURRENT_CODEGEN_FIXTURES)
def test_current_codegen_output_passes_the_allowlist(relative: str):
    source = (ROOT / relative).read_text(encoding="utf-8")
    findings = check_generated_cpp(transpile(source))
    assert findings == [], f"{relative}: {findings}"


def test_libc_named_pine_variables_are_renamed():
    # ``free`` and ``system`` are legal Pine identifiers; the emitter must
    # escape them so their generated members never read as libc calls.
    source = (
        '//@version=6\nstrategy("t")\n'
        "free = 1.0\nsystem = 2.0\nplot(close + free + system)\n"
    )
    cpp = transpile(source)
    assert "pf_safe_free" in cpp
    assert "pf_safe_system" in cpp
    assert check_generated_cpp(cpp) == []


def test_current_matrix_output_exercises_settings_and_run_stop_macros():
    source = (ROOT / "tests/fixtures/matrix_eigen_pca.pine").read_text(
        encoding="utf-8"
    )
    cpp = transpile(source)
    assert "std::vector<::" in cpp
    assert "#define _PF_ARRAY_STOP" in cpp
    assert check_generated_cpp(cpp) == []


NEGATIVES = {
    "asm": '__asm__("nop");',
    "include": "#include <fcntl.h>\n",
    "pragma": "#pragma once\n",
    "define": "#define EVIL 1\n",
    "undef": "#undef EVIL\n",
    "directive": "#line 41 \"strategy.pine\"\n",
    "extern-c": 'extern "C" void f();',
    "process": 'system("id");',
    "dlopen": 'dlopen("libc.so.6", 1);',
    "file-io": 'fopen("/etc/passwd", "r");',
    "filesystem-stream": "std::ifstream stream;",
    "socket": "socket(2, 1, 0);",
    "env": 'getenv("HOME");',
    "function-pointer": "void* (*p)(size_t) = &malloc;",
    "reinterpret-cast": "auto p = reinterpret_cast<char*>(q);",
    "trigraph": "int x = 1 ??= 2;",
    "digraph": "int a<:3;",
    "line-splice": "int a = 1;\\\nint b = 2;",
    "nul": "int a = 1;\x00",
    "non-ascii-identifier": "int а = 1;",
    "numeric-udl": "auto x = 10_km;",
    "numeric-separator": "int x = 1'0;",
    "raw-string": 'auto s = R"(payload)";',
    "malformed-literal": 'const char* s = "unterminated\n',
}


@pytest.mark.parametrize(
    "rule,source", sorted(NEGATIVES.items()), ids=sorted(NEGATIVES)
)
def test_each_rule_has_a_load_bearing_negative(rule: str, source: str):
    findings = check_generated_cpp(source)
    assert {finding.rule for finding in findings} == {rule}, findings
    assert all(isinstance(finding, Finding) for finding in findings)


def test_shift_scope_sequence_is_not_a_digraph():
    # C++ max munch: ``<::`` is ``<`` then ``::`` unless followed by
    # ``:`` or ``>``.  Settings metadata emits this shape.
    cpp = "std::vector<::pineforge::checked_settings::Setting> settings;"
    assert check_generated_cpp(cpp) == []


def test_pragma_spelling_with_underscore_is_refused():
    findings = check_generated_cpp('_Pragma("once")')
    assert {finding.rule for finding in findings} == {"pragma"}, findings


def test_universal_character_name_identifier_is_refused():
    # ``а`` spells the same non-ASCII identifier without a non-ASCII byte.
    findings = check_generated_cpp("int \\u0430 = 1;")
    assert {finding.rule for finding in findings} == {"escape"}, findings


def test_qualified_libc_function_pointer_is_refused():
    findings = check_generated_cpp("auto p = &std::malloc;")
    assert {finding.rule for finding in findings} == {"function-pointer"}, findings


@pytest.mark.parametrize(
    "source",
    [
        "auto p = malloc;",
        "auto p = std::malloc;",
        "use(malloc);",
        "return malloc;",
        "+malloc;",
    ],
)
def test_libc_function_decay_is_refused(source: str):
    findings = check_generated_cpp(source)
    assert {finding.rule for finding in findings} == {"function-pointer"}, findings


def test_digit_separator_does_not_hide_code():
    # ``1'0`` is one pp-number in C++14, not the start of a character
    # literal; the call after it must still be scanned.
    findings = check_generated_cpp("int x = 1'0; system(\"id\");")
    assert {finding.rule for finding in findings} == {
        "numeric-separator",
        "process",
    }, findings


def test_secure_getenv_is_refused():
    findings = check_generated_cpp('secure_getenv("HOME");')
    assert {finding.rule for finding in findings} == {"env"}, findings


def test_include_allowlist_comes_from_emit_top():
    # A header the emitter never spells is refused; a header it does spell is
    # accepted only through the derived set.
    assert {f.rule for f in check_generated_cpp("#include <unistd.h>\n")} == {
        "include"
    }
    assert check_generated_cpp(
        "#include <pineforge/source/pine_strategy_host.hpp>\n"
    ) == []


def test_known_macro_with_a_different_body_is_refused():
    findings = check_generated_cpp(
        '#define _PF_ARRAY_STOP(reason, method, english) system("id")\n'
    )
    assert {finding.rule for finding in findings} == {"define"}, findings


TZ_FIELD_SEALED = """\
int f() {
    if (x) {
    } else { static std::mutex _pf_tz_mu; std::lock_guard<std::mutex> _pf_tz_lock(_pf_tz_mu); const char* _old = std::getenv("TZ"); std::string _old_tz = _old ? _old : ""; bool _had_old = (_old != nullptr); ::setenv("TZ", _tz.c_str(), 1); ::tzset(); localtime_r(&_secs, &tm_buf); if (_had_old) { ::setenv("TZ", _old_tz.c_str(), 1); } else { ::unsetenv("TZ"); } ::tzset(); }
}
"""

TZ_CALENDAR_SEALED = """\
int f() {
    if (x) {
    } else { static std::mutex _pf_ts_mu; std::lock_guard<std::mutex> _pf_ts_mu_lock(_pf_ts_mu); const char* _old = std::getenv("TZ"); std::string _old_tz = _old ? _old : ""; bool _had_old = (_old != nullptr); ::setenv("TZ", _tz.c_str(), 1); ::tzset(); _res = (int64_t)mktime(&t) * 1000; if (_had_old) { ::setenv("TZ", _old_tz.c_str(), 1); } else { ::unsetenv("TZ"); } ::tzset(); }
}
"""


@pytest.mark.parametrize(
    "source", [TZ_FIELD_SEALED, TZ_CALENDAR_SEALED], ids=["field", "calendar"]
)
def test_sealed_timezone_templates_keep_their_environment_calls(source: str):
    assert check_generated_cpp(source) == []


@pytest.mark.parametrize(
    "source", [TZ_FIELD_SEALED, TZ_CALENDAR_SEALED], ids=["field", "calendar"]
)
def test_a_timezone_template_with_any_other_spelling_is_refused(source: str):
    mutated = source.replace("_pf_tz_mu", "_pf_tz_mu_x").replace(
        "_pf_ts_mu", "_pf_ts_mu_x"
    )
    findings = check_generated_cpp(mutated)
    assert {finding.rule for finding in findings} == {"env"}, findings
