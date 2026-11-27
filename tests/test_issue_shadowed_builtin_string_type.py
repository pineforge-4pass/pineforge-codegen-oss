from pineforge_codegen import transpile
from tests._compile import compile_cpp


def test_shadowed_calendar_strings_concatenate():
    cpp = transpile("""//@version=6
strategy("Calendar name bindings")
second = str.tostring(close)
minute = str.tostring(open)
strategy.entry("s:" + second, strategy.long, comment = minute + "m")
""")
    compile_cpp(cpp, label="shadowed calendar string concatenation")
    assert 'std::string("s:") + second' in cpp
    assert 'minute + std::string("m")' in cpp


def test_unshadowed_second_reads_numeric_calendar_value():
    cpp = transpile("""//@version=6
strategy("Bar clock")
strategy.entry("clock", strategy.long, qty = second + 1)
""")
    assert "pine_second(current_bar_.timestamp, syminfo_.timezone)" in cpp
    compile_cpp(cpp, label="unshadowed calendar second in arithmetic")
