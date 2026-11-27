"""An untyped function parameter formats each call's own number.

``fmt(v) => str.tostring(v, "#.####")`` of
``fixtures/open_items_tv/temp_names_array.pine`` is one helper that one exit
comment calls ten times: with float values (``array.median(c)`` first,
``close % 7`` last) and with one int
(``array.get(array.sort_indices(idx), 0)``). TradingView formats the value
each call passes: its tape of that script
(``temp_names_array_tv_trades.csv``) spells the median ``2.5``, ``1.5`` and
``2.435`` and the index ``1`` and ``3``.

This runs that script, unchanged, over six synthetic 15m bars. It enters on
the 00:00 UTC bar and closes on the 00:15 bar, whose close is the row's
operand (``w = close % 7``, an element of every array). Three appended
lines read the trade's exit comment back through
``strategy.closedtrades.exit_comment``; they add no call of the helper. The
driver prints that string and nothing else, and a row compares one ``|``
field of it with its frozen text.

A run that leaves no 12-field exit comment is not a comparison: the fixture
stops with ``NOT COMPARED`` before any row is judged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


AUTHENTIC = Path(__file__).parent / "fixtures" / "open_items_tv" / "temp_names_array.pine"

# The helper and every use of it, in source order: its definition, then the
# ten calls of the exit comment. The two str.tostring fields between them make
# the comment's twelve fields.
HELPER = 'fmt(v) => str.tostring(v, "#.####")'
HELPER_USES = ["v", "c1", "c2", "c4", "c5", "c6", "c7", "c8", "c10", "c12", "w"]
FIELD_COUNT = 12
MEDIAN, SORT_INDEX, OPERAND = 0, 9, 11  # fmt(c1), fmt(c10), fmt(w)

READER = (
    'var string closedExitText = ""\n'
    "if strategy.closedtrades > 0\n"
    "    closedExitText := strategy.closedtrades.exit_comment(strategy.closedtrades - 1)\n"
)

# Six 15m bars from 2025-03-31 23:30 UTC, each open 10, high 14, low 7,
# close 10, volume 1. Bar 2 opens at 00:00 UTC (the entry), bar 3 at 00:15
# (the close order): its close is the row's operand.
FIRST_BAR_MS = 1743463800000
BAR_MS = 900_000
BAR_COUNT = 6
OPERAND_BAR = 3


@dataclass(frozen=True)
class Row:
    name: str
    close: str     # the 00:15 bar's close, as a C++ literal
    field: int     # index of the compared field in the exit comment
    expected: str  # that field's text, derived by hand below


ROWS = (
    # w = 13 % 7 = 6: c = [3, 1, 2, 6] sorts to [1, 2, 3, 6]; (2 + 3) / 2.
    Row("median-close-13", "13.0", MEDIAN, "2.5"),
    # w = 7.5 % 7 = 0.5: c sorts to [0.5, 1, 2, 3]; (1 + 2) / 2.
    Row("median-close-7.5", "7.5", MEDIAN, "1.5"),
    # w = 9 % 7 = 2: c sorts to [1, 2, 2, 3]; (2 + 2) / 2, a whole float.
    Row("median-close-9", "9.0", MEDIAN, "2"),
    # idx = [9, 1, 5, 6]: its smallest element, 1, is at index 1.
    Row("sort-index-close-13", "13.0", SORT_INDEX, "1"),
    # idx = [9, 1, 5, 0.5]: its smallest element, 0.5, is at index 3.
    Row("sort-index-close-7.5", "7.5", SORT_INDEX, "3"),
    # fmt(w), the float call after the int call: w = 7.5 % 7.
    Row("operand-close-7.5", "7.5", OPERAND, "0.5"),
)
assert len({row.name for row in ROWS}) == len(ROWS)


_RUN = r"""
    {
        pineforge::Bar bars[] = {@BARS@};
        GeneratedStrategy strategy;
        strategy.run(bars, sizeof(bars) / sizeof(bars[0]), "15", "15");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 2;
        }
        std::cout << "@CLOSE@" << '\t' << strategy.closedExitText << '\n';
    }
"""


def _bar(index: int, close: str) -> str:
    last = close if index == OPERAND_BAR else "10.0"
    return f"{{10.0, 14.0, 7.0, {last}, 1.0, {FIRST_BAR_MS + index * BAR_MS}LL}}"


def _driver(closes: list[str]) -> str:
    runs = "".join(
        _RUN.replace("@BARS@", ", ".join(_bar(index, close) for index in range(BAR_COUNT)))
        .replace("@CLOSE@", close)
        for close in closes
    )
    return "#include <iostream>\n\nint main() {" + runs + "    return 0;\n}\n"


@pytest.fixture(scope="module")
def exit_comments() -> dict[str, list[str]]:
    """The exit comment's fields per 00:15 close, from one build and run."""
    source = AUTHENTIC.read_text(encoding="utf-8")
    if HELPER not in source or re.findall(r"\bfmt\((\w+)\)", source) != HELPER_USES:
        pytest.fail(f"NOT COMPARED: {AUTHENTIC.name} no longer spells the helper "
                    "and its ten calls in this order", pytrace=False)
    closes = sorted({row.close for row in ROWS})
    output = run_emitted_tu(
        transpile(source + "\n" + READER), _driver(closes), opt="-O0",
        label="untyped formatter float argument",
    )
    comments = {}
    for line in output.splitlines():
        close, _, comment = line.partition("\t")
        comments[close] = comment.split("|")
    incomplete = [close for close in closes
                  if len(comments.get(close, ())) != FIELD_COUNT]
    if incomplete:
        pytest.fail(f"NOT COMPARED: no {FIELD_COUNT}-field exit comment for the "
                    f"close(s) {incomplete}; the run printed:\n{output}", pytrace=False)
    return comments


@pytest.mark.parametrize("row", ROWS, ids=lambda row: row.name)
def test_the_exit_comment_spells_the_value_each_call_passes(exit_comments, row):
    fields = exit_comments[row.close]
    assert fields[row.field] == row.expected, "|".join(fields)
