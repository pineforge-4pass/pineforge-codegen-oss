"""Issue 476: an untyped parameter reached with a fractional float, measured
on executed arithmetic and, separately, on the generated signature.

``fixtures/open_items_tv/temp_names_array.pine`` binds
``c1 = array.median(array.from(3.0, 1.0, 2.0, close % 7))`` -- four float
elements, an even count, so the median is the mean of the middle two -- and
passes it to the untyped ``fmt(v)``. TradingView's tape of that fixture
prints that median on seven bars: 2.5 on five of them, 1.5 and 2.435 on the
other two. A parameter emitted as an integer would narrow the argument at
the call. The fixture only shows the value through its formatter, in an
order comment; these tests ask whether arithmetic on the parameter changes.

Numeric arm (replays). ``f(v) => v * 2`` is called with the fixture's own
median on the bars the tape prints, and the result is an order's ``qty``.
The sink is the executed entry quantity in the engine's trade report: not a
plot, not a comment, not profit. The expected quantity is twice the median
TradingView printed for that bar (5, 3 and 4.87), which no PineForge code
computes. ``single_call`` calls the function once; ``fixture_call_shape`` is
the fixture itself with the formatter's body made numeric and its ten call
sites kept in their statement. Controls: the same median as a quantity with
no call (the pre-call observation), a changing integer argument, and an
integer-element array, whose pre-call median is the one observed in its own
run and is never assumed (the tape does not print it).

Signature arm (compile only, no replay): the generated function that the
emitted fixture calls with the median takes a ``double``. The callee is
read from that call site and its parameter type is decided by the C++
compiler, so another function that happens to take a double cannot answer
for it. A probe that cannot be compiled is reported as no verdict, never as
a wrong signature.

The formatted text of the median is the sister test's target and is not
repeated here.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import statistics

import pytest

from pineforge_codegen import transpile
from tests import _compile as native
from tests._e2e import Build, closed_trades, execute_all, ok, skip_unless_e2e_env
from tests._tv_tapes import FIXTURES, tape, window_feed


TAPE = "temp_names_array"
FIXTURE = FIXTURES / f"{TAPE}.pine"

# Lines of the fixture, each spelled there exactly once (``fixture_lines``).
REMAINDER = "w = close % 7"
FLOAT_ARRAY = "c = array.from(3.0, 1.0, 2.0, w)"
FLOAT_MEDIAN = "c1 = array.median(c)"
FORMATTER = 'fmt(v) => str.tostring(v, "#.####")'
ENTRY_BAR = 'if hour(time, "UTC") % 6 == 0 and minute(time, "UTC") == 0'
ENTRY_ORDER = '    strategy.entry("L", strategy.long, comment = "E")'
EXIT_BAR = 'if hour(time, "UTC") % 6 == 0 and minute(time, "UTC") == 15'
EXIT_ORDER = '    strategy.close("L", comment = '
FORMATTER_CALL = re.compile(r"\bfmt\((\w+)\)")
FORMATTER_ARGUMENTS = ["c1", "c2", "c4", "c5", "c6", "c7", "c8", "c10", "c12", "w"]

# The bar the fixture evaluates its exit comment on, so the bar whose median
# the tape prints; these scripts place their sized order there.
ORDER_BAR = 'hour(time, "UTC") % 6 == 0 and minute(time, "UTC") == 15'
FLAT_BAR = 'hour(time, "UTC") % 6 == 0 and minute(time, "UTC") == 30'
NUMERIC = "f(v) => v * 2"

# (order bar, UTC; close % 7; array.median(3.0, 1.0, 2.0, close % 7)), as
# TradingView's tape spells them in the last and the first field of each
# exit comment. ``test_the_frozen_points_are_the_tape_medians`` reads them
# back from the export.
POINTS = (
    ("2025-04-01 06:15", 6.08, 2.5),
    ("2025-04-01 12:15", 6.41, 2.5),
    ("2025-04-01 18:15", 4.61, 2.5),
    ("2025-04-02 00:15", 6.99, 2.5),
    ("2025-04-02 06:15", 0.8, 1.5),
    ("2025-04-02 12:15", 2.87, 2.435),
    ("2025-04-02 18:15", 5.42, 2.5),
)
BAR_MS = 15 * 60 * 1000
# The engine may round an order's quantity to the instrument's step, and the
# tape prints four decimals. The smallest change a lost fraction makes to a
# quantity on these bars is 0.435.
QUANTITY_TOLERANCE = 0.005


def bar_ms(bar: str) -> int:
    when = dt.datetime.strptime(bar, "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
    return int(when.timestamp() * 1000)


def fill_ms(bar: str) -> int:
    """An order placed on ``bar`` fills at the next bar's open."""
    return bar_ms(bar) + BAR_MS


def near(got: float | None, expected: float, tolerance: float) -> bool:
    return got is not None and abs(got - expected) <= tolerance


def show(title: str, columns: tuple[str, ...], rows: list[tuple]) -> str:
    text = "\n".join([title, " | ".join(columns),
                      *(" | ".join(map(repr, row)) for row in rows)])
    print(text)
    return text


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def fixture_lines() -> list[str]:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    for line in (REMAINDER, FLOAT_ARRAY, FLOAT_MEDIAN, FORMATTER,
                 ENTRY_BAR, ENTRY_ORDER, EXIT_BAR):
        assert lines.count(line) == 1, f"the fixture does not spell {line!r} exactly once"
    assert sum(line.startswith(EXIT_ORDER) for line in lines) == 1, EXIT_ORDER
    return lines


def order_script(key: str, declarations: list[str], quantity: str) -> str:
    """A strategy whose entry on each ``ORDER_BAR`` is sized ``quantity``."""
    return "\n".join([
        "//@version=6",
        f'strategy("pf-476-{key}", overlay = true)',
        *declarations,
        f"if {ORDER_BAR}",
        f'    strategy.entry("L", strategy.long, qty = {quantity})',
        f"if {FLAT_BAR}",
        '    strategy.close("L")',
    ]) + "\n"


def fixture_call_shape() -> str:
    """The fixture with its formatter's body made numeric.

    Every declaration and each of the ten call sites stays as the fixture
    spells it, in the same statement and order; a call's result is now a
    number, so the comment formats it with the built-in. The entry moves to
    the bar the tape prints and is sized by the function called with the
    median; the comment's bar moves with it and its text is not read.
    """
    out = []
    for line in fixture_lines():
        if line == FORMATTER:
            line = "fmt(v) => v * 2"
        elif line == ENTRY_BAR:
            line = f"if {ORDER_BAR}"
        elif line == ENTRY_ORDER:
            line = '    strategy.entry("L", strategy.long, qty = fmt(c1))'
        elif line == EXIT_BAR:
            line = f"if {FLAT_BAR}"
        elif line.startswith(EXIT_ORDER):
            assert FORMATTER_CALL.findall(line) == FORMATTER_ARGUMENTS, line
            line = FORMATTER_CALL.sub(r'str.tostring(fmt(\1), "#.####")', line)
        out.append(line)
    return "\n".join(out) + "\n"


def numeric_sources() -> dict[str, str]:
    fixture_lines()  # the witness below is spelled by the fixture
    float_median = [REMAINDER, FLOAT_ARRAY, FLOAT_MEDIAN]
    changing_int = [REMAINDER, "k = int(w) + 1"]
    int_median = [*changing_int, "ci = array.from(3, 1, 2, k)", "m1 = array.median(ci)"]
    return {
        "float_raw": order_script("float_raw", float_median, "c1"),
        "single_call": order_script("single_call", [*float_median, NUMERIC], "f(c1)"),
        "fixture_call_shape": fixture_call_shape(),
        "int_argument": order_script("int_argument", [*changing_int, NUMERIC], "f(k)"),
        "int_element_raw": order_script("int_element_raw", int_median, "m1"),
        "int_element_call": order_script(
            "int_element_call", [*int_median, NUMERIC], "f(m1)"),
    }


# ---------------------------------------------------------------------------
# The oracle is TradingView's own print
# ---------------------------------------------------------------------------

def test_the_frozen_points_are_the_tape_medians():
    spelled = []
    for trade in tape(TAPE):
        fields = trade["exit"][1].split("|")
        spelled.append((trade["exit"][0], float(fields[-1]), float(fields[0])))
    assert spelled == [(fill_ms(bar), remainder, median)
                       for bar, remainder, median in POINTS]
    # The printed median is the arithmetic one of the four operands.
    for _, remainder, median in POINTS:
        assert round(statistics.median([3.0, 1.0, 2.0, remainder]), 4) == median
    # More than one input, and a fraction that is not 2.5.
    assert {median for _, _, median in POINTS} == {2.5, 1.5, 2.435}


# ---------------------------------------------------------------------------
# Numeric arm: executed entry quantities
# ---------------------------------------------------------------------------

class Replays:
    """The builds of ``numeric_sources``, each replayed once over the tape's window."""

    def __init__(self, engine, base, feed, outcomes):
        self.engine, self.base, self.feed, self.outcomes = engine, base, feed, outcomes
        self._quantities: dict[str, dict[int, float]] = {}

    def ran(self, key: str) -> bool:
        return self.outcomes[key].error is None

    def quantities(self, key: str) -> dict[int, float]:
        """Executed entry quantity by fill time (ms) of ``key``'s closed
        trades; fails the calling test when the build did not run."""
        ok(self.outcomes, key)
        if key not in self._quantities:
            self._quantities[key] = {
                trade["entry_time"]: trade["qty"]
                for trade in closed_trades(self.engine, self.base / key, self.feed)}
        return self._quantities[key]


@pytest.fixture(scope="module")
def replays(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("issue_476_numeric")
    feed = window_feed(engine, base)
    outcomes = execute_all(engine, feed, base, {
        key: Build(source) for key, source in numeric_sources().items()})
    return Replays(engine, base, feed, outcomes)


def reading(got: float | None, median: float) -> str:
    """What a quantity says about the argument; a label for the report only."""
    if got is None:
        return "no trade"
    if near(got, 2 * median, 2 * QUANTITY_TOLERANCE):
        return "2 * median"
    if near(got, 2 * math.trunc(median), 2 * QUANTITY_TOLERANCE):
        return "2 * int(median): the argument lost its fraction"
    return "neither"


def test_the_float_median_is_the_order_quantity_before_any_call(replays):
    # Control and pre-call observation: qty = c1, no user function.
    got = replays.quantities("float_raw")
    rows = [(bar, remainder, median, got.get(fill_ms(bar)))
            for bar, remainder, median in POINTS]
    report = show(
        "float_raw: executed entry quantity of qty = c1",
        ("order bar (UTC)", "close % 7 (tape)", "median (tape)", "engine quantity"), rows)
    wrong = [row for row in rows if not near(row[3], row[2], QUANTITY_TOLERANCE)]
    assert not wrong, f"the median itself is not the tape's on {len(wrong)} bars\n{report}"


@pytest.mark.parametrize("key", ("single_call", "fixture_call_shape"))
def test_the_numeric_function_doubles_the_float_median(replays, key):
    got = replays.quantities(key)
    raw = replays.quantities("float_raw") if replays.ran("float_raw") else {}
    rows = []
    for bar, remainder, median in POINTS:
        quantity = got.get(fill_ms(bar))
        rows.append((bar, remainder, median, raw.get(fill_ms(bar)), 2 * median,
                     quantity, reading(quantity, median)))
    report = show(
        f"{key}: executed entry quantity of qty = <function>(c1), body v * 2",
        ("order bar (UTC)", "close % 7 (tape)", "median (tape)",
         "pre-call median (float_raw quantity)", "expected quantity",
         "engine quantity", "reading"), rows)
    wrong = [row for row in rows if not near(row[5], row[4], 2 * QUANTITY_TOLERANCE)]
    assert not wrong, (
        f"[{key}] v * 2 is not twice the tape's median on {len(wrong)} of "
        f"{len(rows)} bars\n{report}")


def test_the_numeric_function_doubles_a_changing_integer(replays):
    # Control: an integer argument that changes with the bar.
    got = replays.quantities("int_argument")
    rows = []
    for bar, remainder, _ in POINTS:
        argument = int(remainder) + 1
        rows.append((bar, remainder, argument, 2 * argument, got.get(fill_ms(bar))))
    report = show(
        "int_argument: executed entry quantity of qty = f(int(close % 7) + 1)",
        ("order bar (UTC)", "close % 7 (tape)", "argument", "expected quantity",
         "engine quantity"), rows)
    assert {row[2] for row in rows} == {1, 3, 5, 6, 7}
    wrong = [row for row in rows if not near(row[4], row[3], QUANTITY_TOLERANCE)]
    assert not wrong, f"v * 2 is not twice the integer on {len(wrong)} bars\n{report}"


def test_the_numeric_function_doubles_the_integer_element_median_it_is_given(replays):
    # array.from(3, 1, 2, k) has integer elements and the tape prints no
    # median of it, so its pre-call median is the one int_element_raw
    # executes; the call must double exactly that value. The arithmetic
    # median is shown for the reader and is not asserted.
    raw = replays.quantities("int_element_raw")
    got = replays.quantities("int_element_call")
    rows = []
    for bar, remainder, _ in POINTS:
        last = int(remainder) + 1
        rows.append((bar, last, statistics.median([3, 1, 2, last]),
                     raw.get(fill_ms(bar)), got.get(fill_ms(bar))))
    report = show(
        "int_element: executed entry quantities of qty = m1 and qty = f(m1)",
        ("order bar (UTC)", "k", "arithmetic median (not asserted)",
         "pre-call median (int_element_raw quantity)", "f(m1) quantity"), rows)
    wrong = [row for row in rows
             if row[3] is None or not near(row[4], 2 * row[3], 2 * QUANTITY_TOLERANCE)]
    assert not wrong, (
        f"v * 2 is not twice the observed pre-call median on {len(wrong)} bars\n{report}")


# ---------------------------------------------------------------------------
# Signature arm: the parameter of the function the median reaches
# ---------------------------------------------------------------------------

OWNER = re.compile(r"^class (\w+)\s*:\s*public\s+pineforge::source::PineStrategyHost\b", re.M)
# A user function is a member of the generated class: one definition line.
DEFINITION = re.compile(
    r"^ {4}(?P<result>\S(?:.*\S)?) (?P<name>\w+)\((?P<parameter>[^(),]+)\) \{$", re.M)
MARKER = "PF476_REACHED_FORMATTER_PARAMETER_IS_NOT_DOUBLE"
STATIC_ASSERT_FAILED = re.compile(r"static[_ ]assert(?:ion)? failed")
PROBE = """
#include <type_traits>
namespace pf476_probe {
template <class F> struct first_parameter;
template <class C, class R, class A>
struct first_parameter<R (C::*)(A)> { using type = A; };
template <class C, class R, class A>
struct first_parameter<R (C::*)(A) const> { using type = A; };
template <class R, class A>
struct first_parameter<R (*)(A)> { using type = A; };
using reached = first_parameter<decltype(&%(owner)s::%(name)s)>::type;
}
"""
VERDICT = ('static_assert(std::is_same<pf476_probe::reached, double>::value, "'
           + MARKER + '");\n')
NO_VERDICT = "NO SIGNATURE VERDICT (this is not a wrong signature): "


def emitted_name(cpp: str, pine_name: str) -> str | None:
    """The one C++ name the script's ``pine_name`` is assigned under."""
    names = [name for name in (pine_name, f"pf_safe_{pine_name}")
             if re.search(rf"^\s*(?:[\w:<>]+ )?{re.escape(name)} = ", cpp, re.M)]
    return names[0] if len(names) == 1 else None


def argument_text(cpp: str, opening: int) -> str:
    depth = 0
    for index in range(opening, len(cpp)):
        if cpp[index] == "(":
            depth += 1
        elif cpp[index] == ")":
            depth -= 1
            if depth == 0:
                return cpp[opening + 1:index]
    return cpp[opening + 1:]


def calls_reading(cpp: str, token: str, names) -> list[tuple[str, str]]:
    """``(callee, argument text)`` of each call of one of the unit's own
    functions ``names`` whose argument reads ``token``: the innermost such
    call around every read of it."""
    found = []
    for use in re.finditer(rf"(?<![\w.:]){re.escape(token)}(?![\w(])", cpp):
        depth = 0
        index = use.start() - 1
        while index >= 0 and cpp[index] != "\n":
            if cpp[index] == ")":
                depth += 1
            elif cpp[index] == "(":
                if depth:
                    depth -= 1
                else:
                    callee = re.search(r"(\w+)$", cpp[max(0, index - 160):index])
                    if callee is not None and callee.group(1) in names:
                        found.append((callee.group(1), argument_text(cpp, index)))
                        break
            index -= 1
    return found


def diagnostics(source: str, label: str, *flags: str) -> str | None:
    """None when ``source`` compiles; the harness's report otherwise."""
    try:
        native.compile_cpp(source, label=label, extra_flags=flags)
    except AssertionError as error:
        return str(error)
    return None


def test_the_formatter_reached_with_the_float_median_takes_a_double():
    native.skip_if_no_compile_env()
    cpp = transpile(FIXTURE.read_text(encoding="utf-8"))
    broken = diagnostics(cpp, "temp_names_array as emitted")
    if broken is not None:
        pytest.fail(NO_VERDICT + "the emitted fixture does not compile.\n" + broken,
                    pytrace=False)

    owners = OWNER.findall(cpp)
    median = emitted_name(cpp, "c1")
    definitions: dict[str, list[str]] = {}
    for match in DEFINITION.finditer(cpp):
        definitions.setdefault(match["name"], []).append(match["parameter"])
    if len(owners) != 1 or median is None:
        pytest.fail(NO_VERDICT + f"generated classes {owners}, the median's variable "
                    f"{median!r}; the emitted unit is not shaped as this probe reads it.",
                    pytrace=False)
    reached = calls_reading(cpp, median, definitions)
    callees = sorted({name for name, _ in reached})
    if len(callees) != 1 or len(definitions[callees[0]]) != 1:
        declared = [(callee, definitions[callee]) for callee in callees]
        pytest.fail(
            NO_VERDICT + "absent or ambiguous helper. Generated one-parameter functions "
            f"called with {median}: {callees}; their definitions: {declared}.",
            pytrace=False)
    name, parameter = callees[0], definitions[callees[0]][0]
    arguments = sorted({argument for _, argument in reached})
    print(f"reached: {owners[0]}::{name}({parameter}); called with {arguments}")

    probe = PROBE % {"owner": owners[0], "name": name}
    unnamed = diagnostics(cpp + probe, "the reached formatter, named",
                          "-fno-access-control")
    if unnamed is not None:
        pytest.fail(NO_VERDICT + f"the probe cannot name {owners[0]}::{name} "
                    "(absent, overloaded, or not a one-parameter function).\n" + unnamed,
                    pytrace=False)
    verdict = diagnostics(cpp + probe + VERDICT, "the reached formatter's parameter",
                          "-fno-access-control")
    if verdict is None:
        return
    if MARKER in verdict and STATIC_ASSERT_FAILED.search(verdict):
        pytest.fail(
            f"WRONG SIGNATURE: {owners[0]}::{name}, the function the emitted fixture "
            f"calls with the float median {median}, is declared ({parameter}); the "
            f"compiler says its parameter is not double. Argument text at the call: "
            f"{arguments}", pytrace=False)
    pytest.fail(NO_VERDICT + "the type check itself did not compile.\n" + verdict,
                pytrace=False)
