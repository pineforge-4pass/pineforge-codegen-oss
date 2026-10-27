"""The generated library reports how many order sites create a fresh id per bar.

The engine host sizes its id/successor scratch table from a count instead of a fixed
default. Every generated library therefore carries an additive export pair beside
``strategy_order_shapes_receipt``:

    uint32_t strategy_id_sites_api_version(void)      -> 1u
    int      strategy_id_sites_receipt(void* s, char* json, size_t capacity, size_t* required,
                                       char* error, size_t error_capacity)

whose immutable canonical JSON (sort_keys, separators (",", ":")) is

    {"strategy":{"ids":{"freshPerBar":K}},"version":1}

for a bounded script, and

    {"strategy":{"ids":{"freshPerBarStatus":"unbounded"}},"version":1}

when a loop or a recursion around a computed id has no static bound (no ``freshPerBar`` key).

K counts the lowered ``strategy.entry`` / ``strategy.order`` / ``strategy.exit`` sites whose ``id``
argument is classed "dynamic" by ``order_shapes.parameter_class`` (a literal or empty id adds
nothing): once per static call site of the user function that holds the site (transitively), and
once per iteration of a loop with a static trip count. ``strategy.close`` / ``close_all`` /
``cancel`` / ``cancel_all`` create no id, and drawings are never counted.

Every expectation below is built from that contract, never read off a transpile. A build without
the export fails every test with "generated C++ has no strategy_id_sites_receipt".
"""

from collections import Counter
import json
import re

import pytest

from pineforge_codegen import transpile
from tests._legacy_cpp import without_id_sites_receipt


HEADER = '//@version=6\nstrategy("id sites")\n'
UNBOUNDED = "unbounded"
ABSENT = "generated C++ has no {name} (the export is absent from the translation unit)"
RECEIPT_CALL = re.compile(r'checked_settings::receipt\(("(?:[^"\\]|\\.)*"), json, capacity, required\);')
RECEIPT_DEFINITION = re.compile(r"\bint\s+strategy_id_sites_receipt\s*\(")
API_VERSION_DEFINITION = re.compile(
    r"\buint32_t\s+strategy_id_sites_api_version\s*\(\s*void\s*\)\s*\{\s*return\s+1u\s*;\s*\}")
API_VERSION_NAME = re.compile(r"\bstrategy_id_sites_api_version\s*\(")


def expected_receipt(fresh) -> str:
    """The contract's document: a count K, or the unbounded status."""
    ids = {"freshPerBarStatus": UNBOUNDED} if fresh == UNBOUNDED else {"freshPerBar": fresh}
    return json.dumps({"strategy": {"ids": ids}, "version": 1}, sort_keys=True, separators=(",", ":"))


def transpiled(source: str) -> str:
    try:
        return transpile(source)
    except Exception as error:
        # A fixture the transpiler refuses says nothing about the export: report it as its own failure.
        pytest.fail("fixture does not transpile on this build, so it cannot show the id-sites receipt: "
                    f"{type(error).__name__}: {error}", pytrace=False)


def require_export(cpp: str, name: str, definition: re.Pattern) -> re.Match:
    found = definition.search(cpp)
    if found is None:
        pytest.fail(ABSENT.format(name=name), pytrace=False)
    return found


def function_body(cpp: str, opening: int) -> str:
    """The brace-balanced body opened by the first ``{`` after ``opening``, string literals skipped."""
    start = cpp.index("{", opening)
    depth = 0
    in_string = False
    index = start
    while index < len(cpp):
        char = cpp[index]
        if in_string:
            if char == "\\":
                index += 1
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cpp[start:index + 1]
        index += 1
    pytest.fail("strategy_id_sites_receipt has no closing brace in the generated C++", pytrace=False)


def receipt_text(source: str) -> str:
    """The document text ``strategy_id_sites_receipt`` hands to ``checked_settings::receipt``."""
    cpp = transpiled(source)
    definition = require_export(cpp, "strategy_id_sites_receipt", RECEIPT_DEFINITION)
    call = RECEIPT_CALL.search(function_body(cpp, definition.start()))
    if call is None:
        pytest.fail("strategy_id_sites_receipt does not pass a document literal to "
                    "checked_settings::receipt(<literal>, json, capacity, required)", pytrace=False)
    return json.loads(call.group(1))


def settings_block(cpp: str, name: str):
    """Line opening the live ``#ifdef PF_SETTINGS_API_VERSION`` group around ``name``'s definition.

    None when the definition sits outside one, in an ``#else`` / ``#elif`` branch of it, or is absent.
    """
    groups = []
    for number, line in enumerate(cpp.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            words = stripped[1:].split()
            if not words:
                continue
            if words[0] in ("if", "ifdef", "ifndef"):
                groups.append([words[:2], True, number])
            elif words[0] in ("else", "elif") and groups:
                groups[-1][1] = False
            elif words[0] == "endif" and groups:
                groups.pop()
        elif re.search(rf"\b{name}\s*\(", line):
            for words, live, opened in groups:
                if words == ["ifdef", "PF_SETTINGS_API_VERSION"] and live:
                    return opened
            return None
    return None


COMPUTED_ENTRY = 'strategy.entry("L" + str.tostring(bar_index), strategy.long)\n'

LITERAL_IDS = '''strategy.entry("L", strategy.long)
strategy.exit("XL", "L", limit=high)
strategy.entry("S", strategy.short)
strategy.exit("XS", "S", stop=low)
strategy.order("O", strategy.long)
'''

THREE_COMPUTED_SITES = '''strategy.entry("E" + str.tostring(bar_index), strategy.long)
strategy.order("O" + str.tostring(bar_index), strategy.short)
strategy.exit("X" + str.tostring(bar_index), limit=high)
'''

FUNCTION_CALLED_TWICE = '''place(float price) =>
    strategy.entry("P" + str.tostring(bar_index), strategy.long, limit=price)
    price
value = place(close) + place(open)
'''

STATEFUL_FUNCTION_CALLED_TWICE = '''place(float price) =>
    strategy.entry("P" + str.tostring(bar_index), strategy.long, limit=price)
    ta.sma(price, 2)
value = place(close) + place(open)
'''

STATIC_FOR_LOOP = '''for i = 0 to 2
    strategy.entry("L" + str.tostring(i), strategy.long)
'''

CLOSE_AND_CANCEL = '''strategy.close("L" + str.tostring(bar_index))
strategy.cancel("L" + str.tostring(bar_index))
'''

DRAWINGS = '''var line segment = line.new(bar_index, close, bar_index + 1, close)
label.new(bar_index, high, "L" + str.tostring(bar_index))
'''

WHILE_SERIES_BOUND = '''count = 0
while count < bar_index % 3
    strategy.entry("W" + str.tostring(count), strategy.long)
    count += 1
'''

RECURSIVE_FUNCTION = '''countdown(int n) =>
    strategy.entry("R" + str.tostring(n), strategy.long)
    n <= 0 ? 0 : countdown(n - 1)
total = countdown(2)
'''

# The cases of the brief, (a) to (j): the lowered sites that create a computed id.
CASES = [
    pytest.param("plot(close)\n", 0, id="a-no-order-call"),
    pytest.param(LITERAL_IDS, 0, id="b-literal-ids-only"),
    pytest.param(COMPUTED_ENTRY, 1, id="c-one-computed-entry"),
    pytest.param(THREE_COMPUTED_SITES, 3, id="d-computed-entry-order-exit"),
    pytest.param(FUNCTION_CALLED_TWICE, 2, id="e-function-called-from-two-places"),
    pytest.param(STATEFUL_FUNCTION_CALLED_TWICE, 2, id="e-stateful-function-called-from-two-places"),
    pytest.param(STATIC_FOR_LOOP, 3, id="f-for-loop-with-static-trip-count"),
    pytest.param(CLOSE_AND_CANCEL, 0, id="g-close-and-cancel-with-computed-id"),
    pytest.param(DRAWINGS, 0, id="h-drawings-without-computed-order-id"),
    pytest.param(WHILE_SERIES_BOUND, UNBOUNDED, id="i-while-loop-bound-reads-a-series"),
    pytest.param(RECURSIVE_FUNCTION, UNBOUNDED, id="j-recursive-function"),
]


@pytest.mark.parametrize(("body", "fresh"), CASES)
def test_receipt_counts_the_sites_that_create_a_computed_id(body, fresh):
    assert receipt_text(HEADER + body) == expected_receipt(fresh)


NESTED_FUNCTIONS = '''inner(float price) =>
    strategy.entry("I" + str.tostring(bar_index), strategy.long, limit=price)
    price
outer(float price) =>
    inner(price) + inner(price + 1.0)
value = outer(close) + outer(open)
'''

UNBOUNDED_LOOP_WITH_LITERAL_ID = '''count = 0
while count < bar_index % 3
    strategy.entry("W", strategy.long)
    count += 1
strategy.entry("D" + str.tostring(bar_index), strategy.short)
'''

# Cases the contract's wording settles that the brief does not list: the "empty" id class, an
# id given by keyword, an id that is not a literal because it is an input, close_all and
# cancel_all, a call chain through two user functions (one site per call path), and an
# unbounded loop that holds only a literal id (it creates no computed id, so it adds nothing).
CONTRACT_EDGE_CASES = [
    pytest.param('strategy.entry("", strategy.short)\n', 0, id="empty-id-adds-nothing"),
    pytest.param('strategy.entry(id = "L" + str.tostring(bar_index), direction = strategy.long)\n', 1,
                 id="computed-id-given-by-keyword"),
    pytest.param('strategy.entry(input.string("L"), strategy.long)\n', 1, id="input-id-is-not-a-literal"),
    pytest.param('strategy.close_all()\nstrategy.cancel_all()\n', 0, id="close-all-and-cancel-all"),
    pytest.param(NESTED_FUNCTIONS, 4, id="nested-functions-count-every-call-path"),
    pytest.param(UNBOUNDED_LOOP_WITH_LITERAL_ID, 1, id="unbounded-loop-around-a-literal-id-only"),
]


@pytest.mark.parametrize(("body", "fresh"), CONTRACT_EDGE_CASES)
def test_receipt_follows_the_contract_wording_at_its_edges(body, fresh):
    assert receipt_text(HEADER + body) == expected_receipt(fresh)


def test_receipt_is_identical_across_transpiles_of_one_source():
    source = HEADER + COMPUTED_ENTRY
    first = receipt_text(source)
    assert receipt_text(source) == first == expected_receipt(1)


def test_receipt_ignores_comments_and_layout():
    commented = (
        '//@version=6\n'
        '// the script header\n'
        'strategy("id sites") // declaration\n'
        '\n'
        '// one computed id\n'
        'strategy.entry("L" + str.tostring(bar_index), strategy.long) // entry\n'
        '// done\n'
    )
    plain = receipt_text(HEADER + COMPUTED_ENTRY)
    assert receipt_text(commented) == plain == expected_receipt(1)


def test_exports_are_emitted_once_in_the_block_of_the_order_shapes_receipt():
    cpp = transpiled(HEADER + THREE_COMPUTED_SITES)
    require_export(cpp, "strategy_id_sites_receipt", RECEIPT_DEFINITION)
    require_export(cpp, "strategy_id_sites_api_version", API_VERSION_NAME)
    assert API_VERSION_DEFINITION.search(cpp), "strategy_id_sites_api_version must return 1u"
    for name in ("strategy_id_sites_api_version", "strategy_id_sites_receipt"):
        assert len(re.findall(rf"\b{name}\b", cpp)) == 1, f"{name} must appear exactly once"
    block = settings_block(cpp, "strategy_order_shapes_receipt")
    assert block is not None, "strategy_order_shapes_receipt is expected inside #ifdef PF_SETTINGS_API_VERSION"
    assert settings_block(cpp, "strategy_id_sites_api_version") == block
    assert settings_block(cpp, "strategy_id_sites_receipt") == block


# The cost algebra. A site counts once per static call of the function that holds it and
# once per iteration of a loop whose start, end and step are known when the script is
# translated; a `while` or `for ... in` loop, a `for` loop with a computed bound and a
# recursion around a computed id leave the count without a static bound. Each value below
# is worked out by hand from those rules (the arithmetic is on its row), never read off a
# transpile.

REVERSE_FOR_LOOP = '''for i = 3 to 1
    strategy.entry("L" + str.tostring(i), strategy.long)
'''

STEPPED_FOR_LOOP = '''for i = 0 to 10 by 5
    strategy.entry("L" + str.tostring(i), strategy.long)
'''

STEPPED_FOR_LOOP_WITH_REMAINDER = '''for i = 0 to 7 by 5
    strategy.entry("L" + str.tostring(i), strategy.long)
'''

NESTED_FOR_LOOPS = '''for i = 0 to 1
    for j = 0 to 2
        strategy.entry("L" + str.tostring(i * 3 + j), strategy.long)
'''

FUNCTION_CALLED_IN_FOR_LOOP = '''place(float price) =>
    strategy.entry("P" + str.tostring(bar_index), strategy.long, limit=price)
    price
for i = 0 to 2
    place(close)
'''

FUNCTION_WITH_LOOP_CALLED_IN_FOR_LOOP = '''place(float price) =>
    for j = 0 to 1
        strategy.entry("P" + str.tostring(j), strategy.long, limit=price)
    price
for i = 0 to 2
    place(close)
'''

RUNTIME_BOUND_FOR_LOOP = '''for i = 0 to bar_index % 3
    strategy.entry("L" + str.tostring(i), strategy.long)
'''

RUNTIME_BOUND_FOR_LOOP_WITH_LITERAL_ID = '''for i = 0 to bar_index % 3
    strategy.entry("L", strategy.long)
'''

FOR_IN_LOOP = '''for price in array.from(close, open)
    strategy.entry("F" + str.tostring(price), strategy.long)
'''

FOR_IN_LOOP_WITH_LITERAL_ID = '''for price in array.from(close, open)
    strategy.entry("F", strategy.long)
'''

# b is defined first, so the call of a inside it is the one reference to a later definition.
MUTUAL_RECURSION = '''b(int n) =>
    n <= 0 ? 0 : a(n - 1)
a(int n) =>
    strategy.entry("A" + str.tostring(n), strategy.long)
    n <= 0 ? 0 : b(n - 1)
total = a(2)
'''

IF_ELSE_ARMS = '''if close > open
    strategy.entry("U" + str.tostring(bar_index), strategy.long)
else
    strategy.entry("D" + str.tostring(bar_index), strategy.short)
'''

METHOD_CALLED_TWICE = '''method enter(float price) =>
    strategy.entry("M" + str.tostring(bar_index), strategy.long, limit=price)
    price
first_value = close.enter()
second_value = open.enter()
'''

REASSIGNED_VAR_BOUND = '''var int n = 1
n := 3
for i = 0 to n
    strategy.entry("L" + str.tostring(i), strategy.long)
'''

CONDITIONALLY_REASSIGNED_BOUND = '''n = 1
if close > open
    n := 3
for i = 0 to n
    strategy.entry("L" + str.tostring(i), strategy.long)
'''

LITERAL_BOUND_NEVER_REASSIGNED = '''n = 2
for i = 1 to n
    strategy.entry("L" + str.tostring(i), strategy.long)
'''

PARAMETER_BOUND_SHADOWS_LITERAL = '''n = 2
place_n(int n) =>
    for i = 1 to n
        strategy.entry("S" + str.tostring(i), strategy.long)
    n
value = place_n(3)
'''

INPUT_BOUND = '''n = input.int(3, "n")
for i = 1 to n
    strategy.entry("L" + str.tostring(i), strategy.long)
'''

COST_ALGEBRA_CASES = [
    # i = 3, 2, 1: abs(1 - 3) // 1 + 1 = 3 iterations x 1 site = 3
    pytest.param(REVERSE_FOR_LOOP, 3, id="reverse-for-loop-counts-every-iteration"),
    # i = 0, 5, 10: abs(10 - 0) // 5 + 1 = 3 iterations x 1 site = 3
    pytest.param(STEPPED_FOR_LOOP, 3, id="for-loop-with-step-counts-every-step"),
    # i = 0, 5 (10 is out of range): abs(7 - 0) // 5 + 1 = 2 iterations x 1 site = 2
    pytest.param(STEPPED_FOR_LOOP_WITH_REMAINDER, 2, id="for-loop-step-remainder-rounds-down"),
    # outer i = 0, 1 (2 iterations) x inner j = 0, 1, 2 (3 iterations) x 1 site = 6
    pytest.param(NESTED_FOR_LOOPS, 6, id="nested-for-loops-multiply"),
    # 1 site in place() x 1 static call inside a loop of abs(2 - 0) // 1 + 1 = 3 iterations = 3
    pytest.param(FUNCTION_CALLED_IN_FOR_LOOP, 3, id="function-called-in-for-loop"),
    # place() holds a loop of 2 iterations (j = 0, 1) x 1 site = 2 per call; 1 static call
    # inside a loop of 3 iterations (i = 0, 1, 2): 2 x 3 = 6
    pytest.param(FUNCTION_WITH_LOOP_CALLED_IN_FOR_LOOP, 6, id="function-with-loop-called-in-for-loop"),
    # unbounded: the end bound reads bar_index, so no trip count is known when the script is
    # translated, and the loop holds a computed id
    pytest.param(RUNTIME_BOUND_FOR_LOOP, UNBOUNDED, id="runtime-for-loop-bound-is-unbounded"),
    # 0: same loop, but its only id is a literal, which adds nothing, so nothing is unbounded
    pytest.param(RUNTIME_BOUND_FOR_LOOP_WITH_LITERAL_ID, 0, id="runtime-for-loop-bound-with-literal-id"),
    # unbounded: a `for ... in` loop has no static trip count and holds a computed id
    pytest.param(FOR_IN_LOOP, UNBOUNDED, id="for-in-loop-is-unbounded"),
    # 0: same loop with only a literal id
    pytest.param(FOR_IN_LOOP_WITH_LITERAL_ID, 0, id="for-in-loop-with-literal-id"),
    # unbounded: a() reaches itself through b() and a() holds a computed id (a recursion)
    pytest.param(MUTUAL_RECURSION, UNBOUNDED, id="mutual-recursion-is-unbounded"),
    # 2: the arms are not assumed exclusive, each arm's site counts: 1 + 1 = 2
    pytest.param(IF_ELSE_ARMS, 2, id="both-arms-of-if-else-count"),
    # 1 site in enter() x 2 static calls = 2
    pytest.param(METHOD_CALLED_TWICE, 2, id="method-called-twice"),
    # unbounded: n is declared `var` and reassigned, so its value at the loop is not the
    # literal 1 it was declared with (reading 1 would give abs(1 - 0) // 1 + 1 = 2 iterations)
    pytest.param(REASSIGNED_VAR_BOUND, UNBOUNDED, id="var-bound-reassigned-before-loop-is-unbounded"),
    # unbounded: n is 1 or 3 at the loop depending on the bar (2 or 4 iterations), so no trip
    # count is known when the script is translated (reading the declared 1 would give 2)
    pytest.param(CONDITIONALLY_REASSIGNED_BOUND, UNBOUNDED, id="conditionally-reassigned-bound-is-unbounded"),
    # 2: n is a literal 2 that is never reassigned; i = 1, 2: abs(2 - 1) // 1 + 1 = 2 iterations x 1 site = 2
    pytest.param(LITERAL_BOUND_NEVER_REASSIGNED, 2, id="literal-bound-never-reassigned-is-static"),
    # unbounded: inside place_n() the bound is the parameter n, not the top-level literal 2
    # (reading that one would give abs(2 - 1) // 1 + 1 = 2 iterations)
    pytest.param(PARAMETER_BOUND_SHADOWS_LITERAL, UNBOUNDED, id="parameter-bound-shadowing-a-literal-is-unbounded"),
    # unbounded: the bound is an input, which a host can override after translation, so its
    # default is no trip count (reading the default 3 would give abs(3 - 1) // 1 + 1 = 3 iterations)
    pytest.param(INPUT_BOUND, UNBOUNDED, id="input-bound-is-unbounded"),
]


@pytest.mark.parametrize(("body", "fresh"), COST_ALGEBRA_CASES)
def test_receipt_cost_algebra_follows_the_contract(body, fresh):
    assert receipt_text(HEADER + body) == expected_receipt(fresh)


# One script with no order call, one with literal ids only, two with computed ids (one of
# them unbounded), and one with an input and a plot.
HELPER_SCRIPTS = [
    pytest.param("plot(close)\n", id="no-order-call"),
    pytest.param(LITERAL_IDS, id="literal-ids"),
    pytest.param(COMPUTED_ENTRY, id="one-computed-entry"),
    pytest.param(THREE_COMPUTED_SITES, id="three-computed-sites"),
    pytest.param(WHILE_SERIES_BOUND, id="unbounded-count"),
    pytest.param('length = input.int(5, "length")\nplot(ta.sma(close, length))\n', id="input-and-plot"),
]

ID_SITES_NAMES = ("strategy_id_sites_api_version", "strategy_id_sites_receipt")
OTHER_EXPORT_NAMES = (
    "strategy_order_shapes_api_version", "strategy_order_shapes_receipt",
    "strategy_capabilities_api_version", "strategy_capabilities_receipt",
    "strategy_confirmed_bar_api_version", "strategy_confirmed_bar_receipt",
    "PF_CAPABILITIES_API_VERSION", "PF_SETTINGS_API_VERSION",
)
# Every export-like word of a translation unit: the counts of the ones the helper leaves
# alone must not move.
EXPORT_WORDS = re.compile(r"\b(?:strategy_\w+|PF_\w+|report_free|run_backtest\w*)\b")


def id_sites_block(cpp: str) -> tuple[int, int]:
    """Offsets of the whole lines the two exports occupy, found by matching braces.

    The block starts at the line of the api-version export and ends with the line that
    closes the receipt function. It does not use the pattern the helper under test is
    built on.
    """
    version = require_export(cpp, "strategy_id_sites_api_version", API_VERSION_NAME)
    definition = require_export(cpp, "strategy_id_sites_receipt", RECEIPT_DEFINITION)
    assert version.start() < definition.start(), "the api-version export must come before the receipt function"
    start = cpp.rfind("\n", 0, version.start()) + 1
    opening = cpp.index("{", definition.start())
    closing = opening + len(function_body(cpp, definition.start()))
    end = cpp.index("\n", closing) + 1
    last_line = cpp[cpp.rfind("\n", 0, closing - 1) + 1:end]
    assert last_line == "    }\n", f"the receipt function must end with its own closing line, not {last_line!r}"
    return start, end


@pytest.mark.parametrize("body", HELPER_SCRIPTS)
def test_helper_removes_exactly_the_two_id_sites_exports(body):
    original = transpiled(HEADER + body)
    for name in ID_SITES_NAMES:
        assert len(re.findall(rf"\b{name}\b", original)) == 1, f"{name} must appear once before the helper runs"
    start, end = id_sites_block(original)
    block = original[start:end]
    stripped = without_id_sites_receipt(original)

    # Neither name is left.
    for name in ID_SITES_NAMES:
        assert not re.search(rf"\b{name}\b", stripped), f"{name} is still in the text after the helper"

    # The lines gone are the lines the two exports occupy, and nothing else changed: the
    # text after the helper is the original without the block that braces delimit.
    occupied = block.count("\n")
    removed = original.count("\n") - stripped.count("\n")
    assert removed == occupied, f"the helper removed {removed} lines, the two exports occupy {occupied}"
    assert stripped == original[:start] + original[end:], "the helper removed text outside the two exports"

    # Every other export keeps its number of occurrences.
    before = Counter(EXPORT_WORDS.findall(original))
    for name in ID_SITES_NAMES:
        del before[name]
    for name in OTHER_EXPORT_NAMES:
        assert before[name] >= 1, f"{name} is expected in every translation unit"
    assert Counter(EXPORT_WORDS.findall(stripped)) == before

    # A text without the two exports comes back as it is.
    assert without_id_sites_receipt(stripped) == stripped
    assert without_id_sites_receipt("") == ""


# More of the cost algebra: a step that does not divide its range, a loop that counts down from
# a start above one, a recursion that reaches no computed id, and a loop bound that is a global
# constant. Each value is worked out by hand from the contract, never read off a transpile.

STEP_THREE_FOR_LOOP = '''for i = 0 to 10 by 3
    strategy.entry("L" + str.tostring(i), strategy.long)
'''

REVERSE_FIVE_FOR_LOOP = '''for i = 5 to 1
    strategy.entry("L" + str.tostring(i), strategy.long)
'''

# quiet() calls itself and holds no order call: a recursion that reaches no counted site.
QUIET_RECURSION = '''quiet(int n) =>
    n <= 0 ? 0 : quiet(n - 1)
settled = quiet(3)
'''

GLOBAL_CONSTANT_BOUND = '''n = 3
for i = 1 to n
    strategy.entry("L" + str.tostring(i), strategy.long)
'''

LOOP_AND_RECURSION_CASES = [
    # i = 0, 3, 6, 9 (12 is past 10): abs(10 - 0) // 3 + 1 = 4 iterations x 1 site = 4
    pytest.param(STEP_THREE_FOR_LOOP, 4, id="for-loop-step-three-counts-four-iterations"),
    # i = 5, 4, 3, 2, 1: abs(1 - 5) // 1 + 1 = 5 iterations x 1 site = 5
    pytest.param(REVERSE_FIVE_FOR_LOOP, 5, id="reverse-for-loop-from-five-counts-five-iterations"),
    # quiet() reaches no site, so the recursion adds 0; the top-level computed entry adds 1: 0 + 1 = 1
    pytest.param(QUIET_RECURSION + COMPUTED_ENTRY, 1, id="recursion-reaching-no-computed-id-adds-nothing"),
    # 0: the same script with a literal id, which adds nothing, so there is no site to count or to bound
    pytest.param(QUIET_RECURSION + 'strategy.entry("L", strategy.long)\n', 0,
                 id="recursion-beside-a-literal-id-adds-nothing"),
    # n = 3 is never reassigned, so i = 1, 2, 3: abs(3 - 1) // 1 + 1 = 3 iterations x 1 site = 3
    pytest.param(GLOBAL_CONSTANT_BOUND, 3, id="global-constant-bound-is-static"),
]


@pytest.mark.parametrize(("body", "fresh"), LOOP_AND_RECURSION_CASES)
def test_receipt_follows_the_contract_for_steps_directions_constant_bounds_and_quiet_recursion(body, fresh):
    assert receipt_text(HEADER + body) == expected_receipt(fresh)


# The count of a `for` loop against the loop the codegen emits. A `for` statement lowers to
# integer temporaries (start, end, step, direction) and a C++ `for` over them. The test below
# reads those integers and operators back out of the generated C++ and runs that loop on plain
# Python integers: the iteration count comes from the emitted loop alone, from no formula.

COMPARISON = r"(?:>=|<=|>|<)"
COMPARISONS = {
    ">=": lambda left, right: left >= right,
    "<=": lambda left, right: left <= right,
    ">": lambda left, right: left > right,
    "<": lambda left, right: left < right,
}
SIMULATION_LIMIT = 1000


def loop_excerpt(cpp: str) -> str:
    """The generated lines that open the first counted loop, for a failure message."""
    at = cpp.find("_for_start_")
    if at < 0:
        return "(the generated C++ holds no _for_start_ temporary)"
    first = cpp.rfind("\n", 0, at) + 1
    return "\n".join(cpp[first:].splitlines()[:8])


def declared_integer(cpp: str, name: str) -> int:
    """The integer of the one emitted statement ``int <name> = (<integer>);``."""
    found = re.findall(rf"\bint\s+{name}\s*=\s*\(*\s*([-+]?\d+)\s*\)*\s*;", cpp)
    if len(found) != 1:
        pytest.fail(f"expected one 'int {name} = <integer>;' statement in the generated C++, found {len(found)}:\n"
                    f"{loop_excerpt(cpp)}", pytrace=False)
    return int(found[0])


def emitted_for_loop(cpp: str) -> dict:
    """The integers and operators of the one counted loop in ``cpp``, as the generated C++ spells them.

    Every part must be found: a pattern that matches nothing fails the test instead of
    leaving it with nothing to check.
    """
    numbers = re.findall(r"\bint\s+_for_start_(\d+)\s*=", cpp)
    if len(numbers) != 1:
        pytest.fail("expected one counted loop (one 'int _for_start_<n> =' statement) in the generated C++, "
                    f"found {len(numbers)}:\n{loop_excerpt(cpp)}", pytrace=False)
    n = numbers[0]
    start = declared_integer(cpp, f"_for_start_{n}")
    end = declared_integer(cpp, f"_for_end_{n}")
    step = declared_integer(cpp, f"_for_step_{n}")
    if step <= 0:
        pytest.fail(f"the emitted step is {step}, not a positive literal; this test does not model a step "
                    f"that the emitted statements would still change:\n{loop_excerpt(cpp)}", pytrace=False)
    direction = re.search(
        rf"\bconst\s+bool\s+_for_down_{n}\s*=\s*\(\s*_for_start_{n}\s*({COMPARISON})\s*_for_end_{n}\s*\)\s*;", cpp)
    if direction is None:
        pytest.fail("no 'const bool _for_down_<n> = (_for_start_<n> <comparison> _for_end_<n>);' statement "
                    f"in the generated C++:\n{loop_excerpt(cpp)}", pytrace=False)
    header = re.search(
        rf"\bfor\s*\(\s*int\s+(?P<var>\w+)\s*=\s*_for_start_{n}\s*;\s*"
        rf"\(\s*_for_down_{n}\s*\?\s*\(\s*(?P=var)\s*(?P<down_test>{COMPARISON})\s*_for_end_{n}\s*\)\s*:"
        rf"\s*\(\s*(?P=var)\s*(?P<up_test>{COMPARISON})\s*_for_end_{n}\s*\)\s*\)\s*;\s*"
        rf"(?P=var)\s*\+=\s*\(\s*_for_down_{n}\s*\?\s*(?P<down_move>-?)\s*_for_step_{n}\s*:"
        rf"\s*(?P<up_move>-?)\s*_for_step_{n}\s*\)"
        rf"(?:\s*,\s*_for_end_{n}\s*=\s*\(*\s*(?P<refresh>[-+]?\d+)\s*\)*)?\s*\)\s*\{{", cpp)
    if header is None:
        pytest.fail("no 'for (int <v> = _for_start_<n>; (_for_down_<n> ? (<v> <cmp> _for_end_<n>) : "
                    "(<v> <cmp> _for_end_<n>)); <v> += (_for_down_<n> ? [-]_for_step_<n> : [-]_for_step_<n>)"
                    f"[, _for_end_<n> = (<integer>)]) {{' header in the generated C++:\n{loop_excerpt(cpp)}",
                    pytrace=False)
    refresh = header.group("refresh")
    return {
        "start": start, "end": end, "step": step, "direction": direction.group(1),
        "down_test": header.group("down_test"), "up_test": header.group("up_test"),
        "down_move": header.group("down_move"), "up_move": header.group("up_move"),
        "refresh": None if refresh is None else int(refresh),
    }


def run_emitted_loop(loop: dict) -> int:
    """Iterations the emitted loop makes, running its statements on plain Python integers."""
    down = COMPARISONS[loop["direction"]](loop["start"], loop["end"])
    still_inside = COMPARISONS[loop["down_test"] if down else loop["up_test"]]
    move = loop["down_move"] if down else loop["up_move"]
    index, bound, iterations = loop["start"], loop["end"], 0
    while still_inside(index, bound):
        iterations += 1
        if iterations > SIMULATION_LIMIT:
            pytest.fail(f"the emitted loop makes more than {SIMULATION_LIMIT} iterations: {loop}", pytrace=False)
        index += -loop["step"] if move == "-" else loop["step"]
        if loop["refresh"] is not None:
            bound = loop["refresh"]
    return iterations


# (source, the (start, end, step) its Pine text spells, the iterations counted by hand)
EMITTED_LOOP_CASES = [
    # i = 0, 3, 6, 9 and then 12, which is past 10: 4 iterations
    pytest.param(STEP_THREE_FOR_LOOP, (0, 10, 3), 4, id="step-three-upward"),
    # i = 5, 4, 3, 2, 1 and then 0, which is past 1: 5 iterations
    pytest.param(REVERSE_FIVE_FOR_LOOP, (5, 1, 1), 5, id="five-down-to-one"),
    # i = 3, 2, 1 and then 0, which is past 1: 3 iterations
    pytest.param(REVERSE_FOR_LOOP, (3, 1, 1), 3, id="three-down-to-one"),
]


@pytest.mark.parametrize(("body", "written", "iterations"), EMITTED_LOOP_CASES)
def test_receipt_count_is_the_number_of_iterations_the_emitted_loop_makes(body, written, iterations):
    source = HEADER + body
    cpp = transpiled(source)
    loop = emitted_for_loop(cpp)
    assert (loop["start"], loop["end"], loop["step"]) == written, (
        f"the emitted temporaries hold {(loop['start'], loop['end'], loop['step'])}, "
        f"the Pine text spells {written}:\n{loop_excerpt(cpp)}")
    assert run_emitted_loop(loop) == iterations, (
        f"the emitted loop does not make the {iterations} iterations counted by hand: {loop}\n{loop_excerpt(cpp)}")
    # The body holds one computed id, so the receipt's count is the loop's iterations.
    assert receipt_text(source) == expected_receipt(iterations)
