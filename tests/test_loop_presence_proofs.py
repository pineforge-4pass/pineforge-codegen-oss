"""Which integer reads in loops are shown present, and which keep their guard.

An int read that may be na gets the integer-na-aware arithmetic (`_pna_l`).
The position binder of `for [index, value] in <array>` is a present int, and a
plain (not var/varip) block local holding only int literals is present from its
own declaration even when a sibling block counts a loop over the same name.
An array element, a map value, a single binder, an updated local and a local
written from anything else are not shown present and keep the guard.
"""

import re

from pineforge_codegen import transpile

HEADER = '//@version=6\nstrategy("loop presence", initial_capital = 100000)\n'
GUARD = "_pna_l"


def _reads(cpp: str, accumulator: str, name: str) -> list[str]:
    """The emitted statements that add ``name`` into ``accumulator``."""
    return [
        line.strip() for line in cpp.splitlines()
        if re.match(rf"\s*{accumulator} \+?= ", line)
        and re.search(rf"\b{name}\b", line)
    ]


def _read(cpp: str, accumulator: str, name: str) -> str:
    lines = _reads(cpp, accumulator, name)
    assert len(lines) == 1, (accumulator, name, lines)
    return lines[0]


def test_array_position_binder_reads_plain():
    cpp = transpile(
        HEADER
        + "values = array.from(1.0, 2.0)\n"
        "float total = 0.0\n"
        "for [ix, item] in values\n"
        "    total += ix * item\n"
        "plot(total)\n"
    )
    line = _read(cpp, "total", "ix")
    assert GUARD not in line
    assert line == "total += (ix * item);"


def test_array_value_binder_of_an_int_array_keeps_its_guard():
    cpp = transpile(
        HEADER
        + "values = array.new_int(2, 7)\n"
        "float byIndex = 0.0\n"
        "float byValue = 0.0\n"
        "for [ix, item] in values\n"
        "    byIndex += ix\n"
        "    byValue += item\n"
        "plot(byIndex + byValue)\n"
    )
    assert GUARD not in _read(cpp, "byIndex", "ix")
    assert GUARD in _read(cpp, "byValue", "item")


def test_single_binder_and_map_value_binder_keep_their_guard():
    cpp = transpile(
        HEADER
        + "values = array.new_int(2, 7)\n"
        "lookup = map.new<string, int>()\n"
        'lookup.put("a", 2)\n'
        "float single = 0.0\n"
        "float counted = 0.0\n"
        "for item in values\n"
        "    single += item\n"
        "for [key, count] in lookup\n"
        "    counted += count\n"
        "plot(single + counted)\n"
    )
    assert GUARD in _read(cpp, "single", "item")
    assert GUARD in _read(cpp, "counted", "count")


def test_an_inner_binder_of_the_same_spelling_does_not_inherit_the_position_proof():
    cpp = transpile(
        HEADER
        + "values = array.from(1.0, 2.0)\n"
        "others = array.new_int(2, 7)\n"
        "float outer = 0.0\n"
        "float inner = 0.0\n"
        "for [ix, item] in values\n"
        "    outer += ix\n"
        "    for ix in others\n"
        "        inner += ix\n"
        "plot(outer + inner)\n"
    )
    assert GUARD not in _read(cpp, "outer", "ix")
    assert GUARD in _read(cpp, "inner", "ix")


SIBLING_COUNTER = (
    "if bar_index > 0\n"
    "    {decl}\n"
    "{middle}"
    "    total += walkIndex\n"
    "if bar_index >= 0\n"
    "    for walkIndex = 0 to 3\n"
    "        total += walkIndex\n"
)


def _sibling(decl: str, middle: str = "") -> str:
    return (
        HEADER + "float total = 0.0\n"
        + SIBLING_COUNTER.format(decl=decl, middle=middle)
        + "plot(total)\n"
    )


def _first_block_read(cpp: str) -> str:
    """The read of the block local (the first of the two ``walkIndex`` adds)."""
    lines = _reads(cpp, "total", "walkIndex")
    assert len(lines) == 2, lines
    return lines[0]


def test_block_local_literal_with_a_sibling_counter_reads_plain():
    line = _first_block_read(transpile(_sibling("int walkIndex = 3")))
    assert GUARD not in line
    assert line == "total += walkIndex;"


def test_updated_or_computed_block_local_keeps_its_guard():
    for decl, middle in (
        ("int walkIndex = 3", "    walkIndex += 1\n"),
        ("int walkIndex = 3", "    walkIndex := int(close)\n"),
        ("int walkIndex = int(close)", ""),
    ):
        line = _first_block_read(transpile(_sibling(decl, middle)))
        assert GUARD in line, (decl, middle, line)
