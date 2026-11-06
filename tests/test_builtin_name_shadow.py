"""A user variable named like a builtin variable is read as the user variable.

``float second = na`` declares a script variable; its reads were emitted as the
builtin ``pine_second(...)`` because the builtin lookup came before the user's
declaration binding. A read bound to a user declaration is the user's; a read
of the builtin itself keeps its emitted text.
"""

from pineforge_codegen import transpile

HEADER = '//@version=6\nstrategy("builtin name shadow", initial_capital = 100000)\n'


def test_user_variable_named_like_a_builtin_is_read_as_the_user_variable():
    cpp = transpile(
        HEADER
        + "float second = na\n"
        "float minute = na\n"
        "for i = 1 to 2\n"
        "    float observed = close + i\n"
        "    if i == 1\n"
        "        second := observed\n"
        "    else\n"
        "        minute := observed\n"
        "float code = math.abs(second - minute) < 0.5 ? 1.0 : 2.0\n"
        "plot(code)\n"
    )
    assert "pine_second(" not in cpp
    assert "pine_minute(" not in cpp
    assignments = [line for line in cpp.splitlines() if line.startswith("        code = ")]
    assert len(assignments) == 1
    assert "std::abs((second - minute))" in assignments[0]


def test_read_of_the_builtin_variable_keeps_its_emitted_text():
    cpp = transpile(HEADER + "float s = second + minute\nplot(s)\n")
    assert "pine_second(current_bar_.timestamp, syminfo_.timezone)" in cpp
    assert "pine_minute(current_bar_.timestamp, syminfo_.timezone)" in cpp
