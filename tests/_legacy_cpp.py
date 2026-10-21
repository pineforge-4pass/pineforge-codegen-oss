"""Compare frozen lowering bytes without additive metadata/stop ABIs."""
import re

from pineforge_codegen.codegen.run_stops import RUN_STOP_SHIMS_CPP


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


def without_state_bounds(cpp: str) -> str:
    """Exclude additive, separately-pinned state-lowering scaffolding.

    Three additions are normalized out of *cross-build* scaffold comparisons
    (a comparison against another commit's codegen, which cannot carry a
    feature it predates):

    * the read-only bound query (``test_script_state_bounds`` pins its rows);
    * the O(1) store view and its stats export (``test_script_state_store``
      pins the emission, the spill counters and value-for-value parity with the
      resident fallback);
    * the precalculated TA cache's storage spelling, which is the same
      declaration with a store-backed element container.

    Nothing about the strategy body is removed or rewritten here.
    """
    cpp = cpp.replace('#include <cstddef>\n', '')
    cpp = re.sub(
        r'^struct _PFScriptStateBoundV1 \{\n.*?^    return bounds;\n}\n\n',
        '', cpp, flags=re.MULTILINE | re.DOTALL,
    )
    cpp = re.sub(r'^// >>> pf-o1-store\n.*?^// <<< pf-o1-store\n\n',
                 '', cpp, flags=re.MULTILINE | re.DOTALL)
    cpp = cpp.replace('    _PFO1StoreOwner _pf_o1_store_;\n', '')
    cpp = re.sub(
        r'^extern "C" int strategy_script_state_store_stats_v1\(.*?^}\n\n',
        '', cpp, flags=re.MULTILINE | re.DOTALL,
    )
    cpp = re.sub(
        r'_PFO1Buffer<([^>\n]+)> (_precalc_[A-Za-z0-9_]+)\{&_pf_o1_store_\};',
        r'std::vector<\1> \2;', cpp,
    )
    # The lifecycle reset repeats the declaration's initializer; with no store
    # owner to bind it is the same default construction as before.
    cpp = re.sub(
        r'decltype\(this->(_precalc_[A-Za-z0-9_]+)\)\{&_pf_o1_store_\}',
        r'decltype(this->\1){}', cpp,
    )
    return cpp


def legacy_cpp(cpp: str) -> str:
    # Coded-stop ABIs are additive; retain their actual call-site changes.
    cpp = _without_run_failure_scaffold(cpp)
    cpp = without_state_bounds(cpp)
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
