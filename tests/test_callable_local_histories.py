"""A callable's history belongs to its declaration and written call path."""

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.helpers import cpp_code_only


def _history_members(cpp):
    return set(re.findall(r"Series<double>\s+(\w+)(?:\{\d+\})?;", cpp))


def _written_history(cpp, value):
    return set(re.findall(
        rf"(\w+)\.(?:push|update)\(current_bar_\.{value}\);", cpp,
    ))


@pytest.mark.parametrize("local,other", [("x", "y"), ("sample", "previous")])
def test_same_named_callable_history_locals_match_renamed_control(local, other):
    def source(second):
        return f"""//@version=6
strategy("Local history")
closeChange() =>
    {local} = close
    {local} - {local}[1]
openChange() =>
    {second} = open
    {second} - {second}[1]
a = closeChange()
b = openChange()
plot(a)
plot(b)
"""

    cpp = cpp_code_only(transpile(source(local)))
    control = cpp_code_only(transpile(source(other)))
    close = _written_history(cpp, "close")
    opening = _written_history(cpp, "open")
    assert len(close) == len(opening) == 1
    assert close.isdisjoint(opening)
    assert close | opening <= _history_members(cpp)
    for member in close | opening:
        assert f"{member}[0]" in cpp
        assert f"{member}[1]" in cpp

    def normalize(text, histories):
        for value, placeholder in (("close", "CLOSE_HISTORY"), ("open", "OPEN_HISTORY")):
            for member in _written_history(text, value):
                histories[member] = placeholder
        # Compare the actual writes and reads, independent of private spelling.
        return [
            re.sub(r"\b\w+\b", lambda m: histories.get(m[0], m[0]), line.strip())
            for line in text.splitlines()
            if any(re.search(rf"\b{member}\b", line) for member in histories)
            and ("return " in line or re.search(r"\.(push|update)\(", line))
        ]

    assert normalize(cpp, {}) == normalize(control, {})


@pytest.mark.parametrize("name", ["x", "sample"])
def test_callable_history_shadow_keeps_global_and_initializer_binding(name):
    cpp = cpp_code_only(transpile(f"""//@version=6
strategy("Global and local history")
{name} = close
delta() =>
    {name} = {name} + open
    {name} - {name}[1]
a = delta()
plot({name}[1])
plot(a)
"""))
    members = _history_members(cpp)
    assert name in members
    local = next(member for member in members if "__pfhist" in member)
    assert local != name
    assert re.search(rf"{local}\.(?:push|update)\(\({name}\[0\] \+ current_bar_\.open\)\);", cpp)
    assert f"{local}[1]" in cpp
    assert f"{name}[1]" in cpp


@pytest.mark.parametrize("calls", [
    "a = first()\nb = first()\nc = second()\n",
    "wrapper() => first()\na = wrapper()\nb = wrapper()\nc = second()\n",
])
def test_declaration_histories_keep_distinct_call_site_clones(calls):
    cpp = cpp_code_only(transpile("""//@version=6
strategy("Call paths")
first() =>
    sample = close
    sample - sample[1]
second() =>
    sample = open
    sample - sample[1]
""" + calls))
    close = _written_history(cpp, "close")
    opening = _written_history(cpp, "open")
    assert len(close) == 2
    assert len(opening) == 1
    assert close.isdisjoint(opening)
    assert close | opening <= _history_members(cpp)
    for member in close | opening:
        assert f"{member}[1]" in cpp


def test_private_callable_history_storage_preserves_each_scalar_family():
    cpp = cpp_code_only(transpile("""//@version=6
strategy("Scalar history types")
number() =>
    sample = close
    nz(sample[1])
text() =>
    sample = str.tostring(open)
    sample[1]
a = number()
b = text()
"""))
    assert "Series<double> sample__pfhist1;" in cpp
    assert "Series<std::string> sample__pfhist2;" in cpp
    assert "sample__pfhist1[1]" in cpp
    assert "sample__pfhist2[1]" in cpp


def test_private_history_names_avoid_authored_bases_and_clone_spellings():
    cpp = cpp_code_only(transpile("""//@version=6
strategy("Private names")
sample__pfhist1 = input.float(2.0)
sample__pfhist2_cs1 = input.float(3.0)
first() =>
    sample = close
    sample - sample[1]
second() =>
    sample = open
    sample - sample[1]
a = first()
b = first()
c = second()
plot(sample__pfhist1 + sample__pfhist2_cs1 + a + b + c)
"""))
    histories = _written_history(cpp, "close") | _written_history(cpp, "open")
    assert len(histories) >= 3
    assert histories.isdisjoint({"sample__pfhist1", "sample__pfhist2_cs1"})
    assert histories <= _history_members(cpp)
