"""A trace-only history read must use the same chart-bar buffer as a body read."""

import pytest

from pineforge_codegen import CompileError, transpile
from tests._compile import compile_cpp


_STRATEGY = '''//@version=6
strategy("Trace scalar history")
plot(close)
'''


def _assert_buffer(cpp, member, cpp_type, value):
    assert f"Series<{cpp_type}> {member};" in cpp
    assert cpp.count(f"if (history_advances_new_bar()) {member}.push({value});") == 1
    assert f"else {member}.update({value});" in cpp


def test_trace_only_scalar_history():
    cpp = transpile(_STRATEGY + '''// @pf-trace prev_time=time[1]
// @pf-trace prev_bar=bar_index[1]
// @pf-trace prev_close_time=time_close[1]
// @pf-trace prev_hour=hour[1]
// @pf-trace prev_size=strategy.position_size[1]
''')
    for label, member, cpp_type, value in (
        ("prev_time", "time", "int64_t", "current_bar_.timestamp"),
        ("prev_bar", "bar_index", "int", "pine_bar_index()"),
        ("prev_close_time", "_time_close_", "int64_t", "time_close()"),
        ("prev_hour", "hour", "int", "pine_hour(current_bar_.timestamp, syminfo_.timezone)"),
        ("prev_size", "_strat_position_size", "double", "signed_position_size()"),
    ):
        assert f'trace(std::string("{label}"), (double)({member}[1]));' in cpp
        _assert_buffer(cpp, member, cpp_type, value)
    compile_cpp(cpp, label="trace-only scalar history")


def test_trace_of_body_time_history():
    cpp = transpile(_STRATEGY + '''p = time[1]
// @pf-trace prev_time=p
''')
    _assert_buffer(cpp, "time", "int64_t", "current_bar_.timestamp")
    assert any("p = " in line and "time[1]" in line for line in cpp.splitlines())
    assert 'trace(std::string("prev_time"), (double)(p));' in cpp
    compile_cpp(cpp, label="body time history traced through p")


def test_trace_of_shadowed_builtin_uses_only_the_declarations_push():
    cpp = transpile(_STRATEGY + '''hour = bar_index
// @pf-trace previous=hour[1]
''')
    writes = [line for line in cpp.splitlines() if "hour.push(" in line]
    assert len(writes) == 1 and "pine_bar_index()" in writes[0], writes
    assert 'trace(std::string("previous"), (double)(hour[1]));' in cpp
    compile_cpp(cpp, label="history of a variable named hour")


@pytest.mark.parametrize("receiver", ["na", "barstate.isfirst"])
def test_trace_builtin_without_history_is_diagnosed(receiver):
    pragma = f"// @pf-trace bad=1 + {receiver}[1]"
    with pytest.raises(CompileError) as caught:
        transpile(_STRATEGY + pragma + "\n", filename="probe.pine")
    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E5099"
    assert receiver in diagnostic.message and "history" in diagnostic.message
    assert (diagnostic.location.file, diagnostic.location.line, diagnostic.location.col) == (
        "probe.pine", 4, pragma.index("[") + 1,
    )
