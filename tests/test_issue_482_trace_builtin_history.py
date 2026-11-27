"""A trace-only bar history read needs the same Series as a strategy-body read."""

from pineforge_codegen import transpile
from tests._compile import compile_cpp


def _assert_traced_history(cpp: str, label: str, field: str, offset: int) -> None:
    member = f"_s_{field}"
    assert f"Series<double> {member};" in cpp
    assert cpp.count(f"{member}.push(current_bar_.{field});") == 1
    (trace_line,) = (
        line for line in cpp.splitlines() if f'trace(std::string("{label}"),' in line
    )
    assert f"{member}[{offset}]" in trace_line


def test_trace_only_builtin_history_compiles():
    cpp = transpile("""//@version=6
strategy("Trace history")
strategy.entry("L", strategy.long)
// @pf-trace prev_close=close[1]
// @pf-trace high_change=high - high[2]
""")
    compile_cpp(cpp, label="trace_only_builtin_history")
    _assert_traced_history(cpp, "prev_close", "close", 1)
    _assert_traced_history(cpp, "high_change", "high", 2)


def test_trace_shares_body_builtin_history():
    cpp = transpile("""//@version=6
strategy("Trace history")
if close > close[1]
    strategy.entry("L", strategy.long)
// @pf-trace prev_close=close[1]
""")
    compile_cpp(cpp, label="trace_shares_body_builtin_history")
    _assert_traced_history(cpp, "prev_close", "close", 1)
