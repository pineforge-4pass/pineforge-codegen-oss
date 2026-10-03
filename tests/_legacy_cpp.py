"""Compare frozen lowering bytes without the additive checked-settings ABI."""
import re


def legacy_cpp(cpp: str) -> str:
    cpp = cpp.replace(
        '#if __has_include(<pineforge/checked_settings.hpp>)\n'
        '#include <pineforge/checked_settings.hpp>\n#endif\n', '')
    cpp = re.sub(r'^#ifdef PF_SETTINGS_API_VERSION\n.*?^#endif\n', '',
                 cpp, flags=re.MULTILINE | re.DOTALL)
    cpp = re.sub(r'^    void _pf_record_failure\(.*?\n    }\n'
                 r'    bool _pf_refuse_failed_setting\(.*?\n    }\n', '',
                 cpp, flags=re.MULTILINE | re.DOTALL)
    cpp = cpp.replace('    bool _pf_setting_failed_ = false;\n', '')
    cpp = cpp.replace('    std::string _pf_setting_failure_;\n', '')
    cpp = cpp.replace('        _pf_require_settings_ok();\n', '')
    cpp = cpp.replace('        if (strat->_pf_refuse_failed_setting(out)) return;\n', '')
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
