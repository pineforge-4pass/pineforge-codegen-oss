"""Compare frozen lowering bytes without additive settings/capabilities/stop ABIs."""
import re

from pineforge_codegen.codegen.run_stops import RUN_STOP_SHIMS_CPP
from tests._cpp_tokens import code_only, regions


# Exact source-bearing spellings emitted by base.py (host base),
# emit_top.py (two includes, host/config/override uses), checked_settings.py
# (config), security.py (host time_close), and ta.py (length adapters).
# Do not allow arbitrary namespaces, paths, or source-qualified members:
# they could hide an unrenamed script identifier.
_EMITTER_SOURCE_SPELLINGS = (
    "#include <pineforge/source/pine_strategy_host.hpp>",
    "#include <pineforge/source/pine_ta_length.hpp>",
    "pineforge::source::PineStrategyHost",
    "pineforge::source::PineStrategyConfig",
    "pineforge::source::StrategyOverrides",
    "pineforge::source::SeriesHighest",
    "pineforge::source::SeriesLowest",
    "pineforge::source::SeriesHighestBars",
    "pineforge::source::SeriesLowestBars",
    "pineforge::source::PineSupertrend",
    "pineforge::source::FirstCallBound",
    "pineforge::source::ta_number",
    "pineforge::source::simple_ta_length",
)


def assert_source_rename_complete(raw_cpp: str) -> None:
    """Refuse a mixed rename before projecting it onto historical bytes."""
    code = code_only(raw_cpp)
    if not re.search(r"\bpf_safe_source\b", code):
        return
    for spelling in _EMITTER_SOURCE_SPELLINGS:
        code = re.sub(rf"(?<![\w:]){re.escape(spelling)}(?!\w)", " ", code)
    assert not re.search(r"\bsource\b", code), (
        "partial source rename: unescaped 'source' identifier in generated C++"
    )


def without_source_rename(cpp: str) -> str:
    """Reverse Opus P2-3's ``source`` reservation for historical comparisons.

    The reservation avoids collisions with engine namespace ``pineforge::source``.
    Keep this narrow: only the complete C++ identifier ``pf_safe_source``
    becomes ``source``. Preserve longer identifiers, comments and literals
    byte for byte so historical lowering checks still catch other drift.
    Refuse a partial rename before reversing any identifiers.
    The raw-output source reservation regression covers the rename itself.
    """
    assert_source_rename_complete(cpp)
    identifier = re.compile(r"\bpf_safe_source\b")
    parts = []
    last = 0
    for _kind, start, end, _value in regions(cpp):
        parts.append(identifier.sub("source", cpp[last:start]))
        parts.append(cpp[start:end])
        last = end
    parts.append(identifier.sub("source", cpp[last:]))
    return "".join(parts)


def _without_run_failure_scaffold(cpp: str) -> str:
    cpp = cpp.replace(RUN_STOP_SHIMS_CPP + '\n', '')
    output = []
    stack = []
    active = True
    for line in cpp.splitlines(keepends=True):
        directive = line.strip()
        if re.match(r'#if(?:def|ndef)?\b', directive):
            feature = 'PINEFORGE_HAS_RUN_FAILURE_CODES_V1' in directive
            stack.append({'feature': feature, 'parent': active, 'converted': False,
                          'absent_branch': directive.startswith('#ifndef')})
            if feature:
                active = active and stack[-1]['absent_branch']
                continue
        elif directive.startswith('#elif') and stack[-1]['feature']:
            active = stack[-1]['parent']
            stack[-1]['converted'] = True
            line = line.replace('#elif defined(PF_SETTINGS_API_VERSION)',
                                '#ifdef PF_SETTINGS_API_VERSION')
        elif directive == '#else' and stack[-1]['feature']:
            active = stack[-1]['parent'] and (stack[-1]['converted'] or
                                              not stack[-1]['absent_branch'])
            if not stack[-1]['converted']:
                continue
        elif directive == '#endif':
            frame = stack.pop()
            active = frame['parent']
            if frame['feature'] and not frame['converted']:
                continue
        if active:
            output.append(line)
    return ''.join(output)


def without_trade_history_policy(cpp: str) -> str:
    """Remove CR-150's additive policy block/export and normalize ctor lookup.

    Whole-C++ identity fixtures pinned before L5 compare semantic lowering
    against that accepted scaffold. The L5 raw-output tests cover these two
    emitted ABI pieces directly. CR150-CX also qualifies the constructor's
    existing configure call with ``this->``; normalize only that statement
    when comparing historical lowering. Raw constructor compile/spelling
    coverage lives in test_cr150_constructor_names.py.
    """
    cpp = cpp.replace(
        '        this->configure_pine_strategy(cfg);\n',
        '        configure_pine_strategy(cfg);\n')
    # The order-shapes receipt's host_reads records emitted host-member uses.
    # Non-readers gain this setter element when it becomes reserved; readers
    # emit no policy call and have no such element.
    cpp = cpp.replace('\\"set_trade_history_streamable\\",', '')
    cpp = re.sub(
        r'^#if defined\(PINEFORGE_HAS_TRADE_HISTORY_POLICY_V1\)\n'
        r'        (?:this->)?set_trade_history_streamable\(true\);\n'
        r'^#endif\n',
        '', cpp, flags=re.MULTILINE)
    return re.sub(
        r'^    int strategy_reads_trade_history\(void\) \{\n'
        r'        return [01];\n'
        r'    \}\n',
        '', cpp, flags=re.MULTILINE)


def without_restore_script_state(cpp: str) -> str:
    """Remove the body of the generated rollback hook.

    A fill recalculation restores chart state only; the request.security
    context's own progression is left alone, so that one function's text
    differs from every older reference. Pins that guard everything else in
    the emission compare with it removed on both sides.
    """
    marker = "    void restore_script_state() override {\n"
    if marker not in cpp:
        return cpp
    start = cpp.index(marker)
    return cpp[:start] + cpp[cpp.index("\n    }\n", start):]


def legacy_cpp(cpp: str) -> str:
    # Coded-stop ABIs are additive; retain their actual call-site changes.
    cpp = _without_run_failure_scaffold(cpp)
    # CR-150's trade-history policy and per-strategy reader export are also
    # additive. Historical lowering oracles intentionally ignore both so the
    # feature's ABI shape is covered by its own raw-output tests.
    cpp = without_trade_history_policy(cpp)
    cpp = cpp.replace(
        '#if __has_include(<pineforge/checked_settings.hpp>)\n'
        '#include <pineforge/checked_settings.hpp>\n#endif\n', '')
    cpp = re.sub(r'^#ifdef PF_CAPABILITIES_API_VERSION\n.*?^#endif\n', '',
                 cpp, flags=re.MULTILINE | re.DOTALL)
    cpp = re.sub(r'^#ifdef PF_SETTINGS_API_VERSION\n.*?^#endif\n', '',
                 cpp, flags=re.MULTILINE | re.DOTALL)
    cpp = re.sub(r'^#ifndef PF_SETTINGS_API_VERSION\n.*?^#endif\n', '',
                 cpp, flags=re.MULTILINE | re.DOTALL)
    cpp = re.sub(r'^    void _pf_record_failure\(.*?\n    }\n'
                 r'    bool _pf_refuse_failed_setting\(.*?\n    }\n', '',
                 cpp, flags=re.MULTILINE | re.DOTALL)
    cpp = cpp.replace('    bool _pf_setting_failed_ = false;\n', '')
    cpp = cpp.replace('    std::string _pf_setting_failure_;\n', '')
    cpp = cpp.replace('        _pf_require_settings_ok();\n', '')
    cpp = cpp.replace('        if (strat->_pf_refuse_failed_setting(out)) return;\n', '')
    cpp = cpp.replace(
        '        if (!strat->_pf_refuse_failed_setting(out)) strat->fill_report(out);\n',
        '        strat->fill_report(out);\n')
    cpp = re.sub(r'        } catch \(const std::exception& _pf_error\) \{\n.*?'
                 r'        } catch \(\.\.\.\) \{\n.*?        }\n', '',
                 cpp, flags=re.DOTALL)
    cpp = re.sub(r'^        catch \(const std::exception& _pf_error\).*?\n'
                 r'        catch \(\.\.\.\).*?\n', '', cpp, flags=re.MULTILINE)
    cpp = cpp.replace(
        '        try { return new GeneratedStrategy(); } catch (...) { return nullptr; }\n',
        '        return new GeneratedStrategy();\n')
    cpp = cpp.replace(
        '    void run_backtest(void* s, Bar* bars, int n, ReportC* out) {\n'
        '        try {\n',
        '    void run_backtest(void* s, Bar* bars, int n, ReportC* out) {\n')
    cpp = cpp.replace(
        '        strat->fill_report(out);\n        } catch (...) {}\n',
        '        strat->fill_report(out);\n')
    cpp = cpp.replace(
        '    void run_backtest_full(void* s, Bar* bars, int n, const char* input_tf, const char* script_tf,\n'
        '                           int bar_magnifier, int magnifier_samples, int magnifier_dist, ReportC* out) {\n'
        '        try { _pf_run_backtest_full_impl(s, bars, n, input_tf, script_tf, bar_magnifier, magnifier_samples, magnifier_dist, out); }\n'
        '    }\n', '')
    cpp = cpp.replace('    static void _pf_run_backtest_full_impl(',
                      '    void run_backtest_full(')
    for statement in (
        'delete static_cast<GeneratedStrategy*>(s);',
        'BacktestEngine::free_report(report);',
        'static_cast<GeneratedStrategy*>(s)->set_input(key, value);',
        'static_cast<GeneratedStrategy*>(s)->set_strategy_override(key, value);',
        'static_cast<GeneratedStrategy*>(s)->set_magnifier_volume_weighted(on != 0);',
    ):
        cpp = cpp.replace(f'        try {{ {statement} }} catch (...) {{}}\n',
                          f'        {statement}\n')
        cpp = cpp.replace(f'        try {{ {statement} }}\n', f'        {statement}\n')
    return cpp
