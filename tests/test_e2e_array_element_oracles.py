"""Independent oracles for the element kind of an array: compile it, run it, read the value.

The rows of ``test_enum_generic_argument.py`` and
``test_selection_array_registration.py`` inspect the emitted C++ text. Text can
be matched by a lowering that does not compile or that computes another value.
These rows run the whole generated program through the built engine and read
the values the script computes itself: an enum element compares equal to the
member it was filled with and to nothing else, an omitted fill is missing, an
array of a user type holds that type's objects (a field read and a shared
write reach them), and an array chosen by a ``switch``, ``if`` or ternary is
the array of the arm that was taken, whichever input picks the arm.
"""

from __future__ import annotations

import pytest

from tests._e2e import Build, chart_feed_head, execute_all, ok, skip_unless_e2e_env

HEADER = '//@version=6\nstrategy("Element oracles")\n'
TAIL = """
// @pf-trace valid=valid
if valid and bar_index % 7 == 3
    strategy.entry("L", strategy.long)
if bar_index % 7 == 5
    strategy.close("L")
"""
BARS = 24

ENUM = HEADER + """
enum Choice
    first
    second
filled = array.new<Choice>(size=2, initial_value=Choice.second)
firsts = array.new<Choice>(size=2, initial_value=Choice.first)
empty = array.new<Choice>(size=2)
wrong = filled.get(0) == Choice.first
valid = filled.get(0) == Choice.second and filled.get(1) != Choice.first and firsts.get(1) == Choice.first and na(empty.get(0)) and not wrong
// @pf-trace wrong=wrong
""" + TAIL

USER_TYPE = HEADER + """
type Cell
    float value = 0.0
filled = array.new<Cell>(size=2, initial_value=Cell.new(2.5))
other = array.new<Cell>(size=1, initial_value=Cell.new(7.0))
cell = filled.get(0)
before = cell.value
cell.value := 3.5
got = filled.get(1).value
valid = filled.size() == 2 and other.get(0).value == 7.0 and before == 2.5 and got == 3.5
// @pf-trace got=got
""" + TAIL

SWITCH = """a = switch mode
    0 => array.new<float>(size=3, initial_value=1.0)
    1 => array.new<float>(size=2, initial_value=2.0)
    => array.new<float>(size=1, initial_value=3.0)
"""
TERNARY = "a = mode == 0 ? array.new<float>(3, 1.0) : array.new<float>(2, 2.0)\n"
IF = """a = if mode == 0
    array.new<float>(3, 1.0)
else
    array.new<float>(2, 2.0)
"""
SWITCH_EXPECTED = ("mode == 0 ? 1.0 : mode == 1 ? 2.0 : 3.0", "mode == 0 ? 3 : mode == 1 ? 2 : 1")
TWO_ARMS_EXPECTED = ("mode == 0 ? 1.0 : 2.0", "mode == 0 ? 3 : 2")

# (the declaration of a, the Pine expressions of its first element and size, the
# default mode, the overriding mode, the first element each run must read)
SELECTIONS = {
    "switch_second_then_first_arm": (
        SWITCH, SWITCH_EXPECTED, 1, 0, {"default": 2.0, "override": 1.0}),
    "switch_default_arm_twice": (
        SWITCH, SWITCH_EXPECTED, 2, 3, {"default": 3.0, "override": 3.0}),
    "ternary": (TERNARY, TWO_ARMS_EXPECTED, 1, 0, {"default": 2.0, "override": 1.0}),
    "if": (IF, TWO_ARMS_EXPECTED, 1, 0, {"default": 2.0, "override": 1.0}),
}


def _selection_source(declaration, expected, default_mode):
    first, size = expected
    return (
        HEADER
        + f'int mode = input.int({default_mode}, "Mode", minval = 0, maxval = 3)\n'
        + declaration
        + f"float expectedFirst = {first}\n"
        + f"int expectedSize = {size}\n"
        + "float first = a.get(0)\n"
        + "valid = first == expectedFirst and a.size() == expectedSize\n"
        + "// @pf-trace first=first\n"
        + TAIL
    )


def _values(outcome, tag, name):
    records = [r for r in outcome.traces[tag] if r["name"] == name]
    assert len(records) == BARS, (name, tag, len(records))
    return [r["value"] for r in records]


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("array_element_oracles")
    feed = chart_feed_head(engine, base, BARS)
    builds = {"enum": Build(ENUM, trace=True), "user_type": Build(USER_TYPE, trace=True)}
    for name, (declaration, expected, default_mode, override_mode, _) in SELECTIONS.items():
        builds[name] = Build(
            _selection_source(declaration, expected, default_mode),
            overrides={"Mode": override_mode}, trace=True)
    return execute_all(engine, feed, base, builds)


def test_an_enum_element_equals_its_member_and_nothing_else(runs):
    outcome = ok(runs, "enum")
    assert _values(outcome, "default", "valid") == [1] * BARS
    assert _values(outcome, "default", "wrong") == [0] * BARS


def test_an_array_of_a_user_type_holds_that_types_objects(runs):
    outcome = ok(runs, "user_type")
    assert _values(outcome, "default", "valid") == [1] * BARS
    assert _values(outcome, "default", "got") == [3.5] * BARS


@pytest.mark.parametrize("name", SELECTIONS)
def test_a_selected_array_is_the_array_of_the_arm_taken(runs, name):
    outcome = ok(runs, name)
    for tag, value in SELECTIONS[name][4].items():
        assert _values(outcome, tag, "valid") == [1] * BARS, (name, tag)
        assert _values(outcome, tag, "first") == [value] * BARS, (name, tag)
