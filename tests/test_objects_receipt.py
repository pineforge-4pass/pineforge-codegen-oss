"""The generated library reports how many script-owned objects one execution can create.

The engine host reserves scratch for the objects a script owns (arrays, the string elements
they hold, maps, matrices, user-type instances and drawing records) from a count instead of a
fixed default. Every generated library therefore carries an additive export pair beside
``strategy_order_shapes_receipt`` and ``strategy_id_sites_receipt``:

    uint32_t strategy_objects_api_version(void)      -> 1u
    int      strategy_objects_receipt(void* s, char* json, size_t capacity, size_t* required,
                                      char* error, size_t error_capacity)

whose immutable canonical JSON (sort_keys, separators (",", ":")) is

    {"strategy":{"objects":{"perExecution":K}},"version":1}

for a bounded script, and

    {"strategy":{"objects":{"perExecutionStatus":"unbounded"}},"version":1}

when a loop or a recursion around a creating site has no static bound (no ``perExecution`` key).

K is an upper bound of the objects ONE execution of the script body creates, not a per-bar
figure: a run with ``calc_on_order_fills`` executes the body again. Each creating site counts 1:
an array creator (``array.new*``, ``array.from``, ``array.copy``, ``array.slice``, ``matrix.row``),
``map.new``, ``matrix.new`` and every matrix-returning operation, ``Type.new`` and a drawing made
with ``line.new``. An array creator whose elements are strings adds 1 per non-empty string element
it is given: ``array.new_string(3, "a")`` is 1 + 3 and ``array.from("x", "y")`` is 1 + 2. Plain
string temporaries, numeric element writes (``push``, ``set``) and an array passed as an argument
create nothing counted, and a script with no creating site reports 0 (the receipt is still
emitted).

The cost algebra is the one of the id-sites receipt: a site counts once per static call of the
user function that holds it (transitively) and once per iteration of a ``for`` loop whose start,
end and step are known when the script is translated, unless its body assigns the loop's counter
or declares a variable of the same name, and both arms of an ``if`` count. A ``while`` or
``for ... in`` loop, any other ``for`` loop and a recursion around a creating site have no static
bound, while a loop around a body that creates nothing adds nothing.
The copy of an array of strings is unbounded, because the number of strings it owns is not
tracked.

Every expectation below is built from those rules, never read off a transpile. A build without
the export fails every test with "generated C++ has no strategy_objects_receipt".
"""

import json
import re

import pytest

from pineforge_codegen import transpile


HEADER = '//@version=6\nstrategy("objects")\n'
UNBOUNDED = "unbounded"
ABSENT = "generated C++ has no {name} (the export is absent from the translation unit)"
RECEIPT_CALL = re.compile(r'checked_settings::receipt\(("(?:[^"\\]|\\.)*"), json, capacity, required\);')
RECEIPT_DEFINITION = re.compile(r"\bint\s+strategy_objects_receipt\s*\(")
RECEIPT_SIGNATURE = re.compile(
    r"\bint\s+strategy_objects_receipt\s*\(\s*void\s*\*\s*s\s*,\s*char\s*\*\s*json\s*,\s*"
    r"size_t\s+capacity\s*,\s*size_t\s*\*\s*required\s*,\s*char\s*\*\s*error\s*,\s*"
    r"size_t\s+error_capacity\s*\)")
API_VERSION_DEFINITION = re.compile(
    r"\buint32_t\s+strategy_objects_api_version\s*\(\s*void\s*\)\s*\{\s*return\s+1u\s*;\s*\}")
API_VERSION_NAME = re.compile(r"\bstrategy_objects_api_version\s*\(")
OBJECTS_NAMES = ("strategy_objects_api_version", "strategy_objects_receipt")


def bounded_receipt(count: int) -> str:
    """The document of a script whose one execution creates at most ``count`` objects."""
    return json.dumps({"strategy": {"objects": {"perExecution": count}}, "version": 1},
                      sort_keys=True, separators=(",", ":"))


def unbounded_receipt() -> str:
    """The document of a script with no static bound: a status, and no ``perExecution`` key."""
    return json.dumps({"strategy": {"objects": {"perExecutionStatus": UNBOUNDED}}, "version": 1},
                      sort_keys=True, separators=(",", ":"))


def transpiled(source: str) -> str:
    try:
        return transpile(source)
    except Exception as error:
        # A fixture the transpiler refuses says nothing about the export: report it as its own failure.
        pytest.fail("fixture does not transpile on this build, so it cannot show the objects receipt: "
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
    pytest.fail("strategy_objects_receipt has no closing brace in the generated C++", pytrace=False)


def receipt_literal(source: str) -> str:
    """The string literal ``strategy_objects_receipt`` hands to ``checked_settings::receipt``, as spelled."""
    cpp = transpiled(source)
    definition = require_export(cpp, "strategy_objects_receipt", RECEIPT_DEFINITION)
    call = RECEIPT_CALL.search(function_body(cpp, definition.start()))
    if call is None:
        pytest.fail("strategy_objects_receipt does not pass a document literal to "
                    "checked_settings::receipt(<literal>, json, capacity, required)", pytrace=False)
    return call.group(1)


def receipt_text(source: str) -> str:
    """The document text ``strategy_objects_receipt`` hands to ``checked_settings::receipt``."""
    text = json.loads(receipt_literal(source))
    if not text:
        pytest.fail("strategy_objects_receipt hands an empty document to checked_settings::receipt",
                    pytrace=False)
    return text


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


# Minimal strategy bodies, one creating shape each. The names are plain on purpose: nothing
# below reads a collection it makes, so a body holds exactly the sites its row counts.

FLOAT_ARRAY = 'levels = array.new_float(5)\n'

ARRAY_FROM_NUMBERS = 'levels = array.from(1.0, 2.0, 3.0)\n'

MAP_IN_STATIC_LOOP = '''for i = 0 to 2
    slot = map.new<string, float>()
'''

TYPE_BUILT_BY_A_FUNCTION_CALLED_TWICE = '''type P
    float x
make() =>
    P.new(1.0)
first_object = make()
second_object = make()
'''

NEW_STRING_ARRAY = 'names = array.new_string(3, "a")\n'

STRING_ARRAY_FROM_LITERALS = 'names = array.from("x", "y")\n'

NEW_COPY_AND_SLICE = '''original = array.new_float(3)
duplicate = array.copy(original)
window = array.slice(original, 0, 2)
'''

VAR_ARRAY_AND_LINE = '''var store = array.new_float(10)
segment = line.new(bar_index, close, bar_index + 1, close)
'''

MATRIX_TRANSPOSE_AND_ROW = '''grid = matrix.new<float>(2, 2, 0.0)
flipped = grid.transpose()
first_row = grid.row(0)
'''

SERIES_BOUND_LOOP_AROUND_AN_ARRAY = '''n = int(close)
for i = 0 to n
    levels = array.new_float(1)
'''

NO_CREATING_SITE = '''plot(close)
if close > open
    strategy.entry("L", strategy.long)
'''

STRING_ARRAY_COPY = '''names = array.new_string(2, "a")
duplicate = array.copy(names)
'''

NUMERIC_PUSHES_IN_A_LOOP = '''levels = array.new_float(0)
for i = 0 to 9
    array.push(levels, 1.0)
'''

WHILE_LOOP_AROUND_NOTHING = '''count = 0
while count < 3
    count += 1
levels = array.new_float(2)
'''

IF_ELSE_ARRAYS = '''if close > open
    bull_levels = array.new_float(1)
else
    bear_levels = array.new_float(1)
'''

NESTED_STATIC_LOOPS = '''for i = 0 to 1
    for j = 0 to 2
        cell = array.new_int(1)
'''

COPY_AS_A_METHOD = '''original = array.new_float(3)
duplicate = original.copy()
'''

STRING_TEMPORARIES = '''closing_text = str.tostring(close)
joined_text = closing_text + ":" + str.tostring(bar_index)
'''

# (body, objects one execution creates), worked out by hand from the rules in the module
# docstring (the arithmetic is on the row), never read off a transpile.
BOUNDED_CASES = [
    # 1 array creator over numbers, which own nothing = 1
    pytest.param(FLOAT_ARRAY, 1, id="array-new-float-counts-one"),
    # 1 array.from over three numbers = 1
    pytest.param(ARRAY_FROM_NUMBERS, 1, id="array-from-numbers-counts-one"),
    # i = 0, 1, 2: abs(2 - 0) // 1 + 1 = 3 iterations x 1 map.new = 3
    pytest.param(MAP_IN_STATIC_LOOP, 3, id="map-in-a-static-for-loop-counts-every-trip"),
    # make() holds 1 P.new and has 2 static calls: 2 x 1 = 2
    pytest.param(TYPE_BUILT_BY_A_FUNCTION_CALLED_TWICE, 2, id="function-building-a-type-called-twice"),
    # 1 array + 3 elements (the literal count 3, the non-empty literal "a") = 1 + 3 = 4
    pytest.param(NEW_STRING_ARRAY, 4, id="array-new-string-counts-its-elements"),
    # 1 array + 2 non-empty literals ("x" and "y") = 1 + 2 = 3
    pytest.param(STRING_ARRAY_FROM_LITERALS, 3, id="array-from-strings-counts-its-elements"),
    # array.new_float 1 + array.copy 1 + array.slice 1 = 3
    pytest.param(NEW_COPY_AND_SLICE, 3, id="new-copy-and-slice-count-one-each"),
    # var array.new_float 1 (its initializer runs in the first execution) + line.new 1 = 2
    pytest.param(VAR_ARRAY_AND_LINE, 2, id="var-array-and-line-count-one-each"),
    # matrix.new 1 + transpose (a matrix-returning operation) 1 + row (an array creator) 1 = 3
    pytest.param(MATRIX_TRANSPOSE_AND_ROW, 3, id="matrix-transpose-and-row-count-one-each"),
    # 0: no creating site (a plot, a comparison and an entry), and the receipt is still emitted
    pytest.param(NO_CREATING_SITE, 0, id="script-without-a-creating-site"),
    # array.new_float 1; the pushes of i = 0 .. 9 create nothing: 1 + 10 x 0 = 1
    pytest.param(NUMERIC_PUSHES_IN_A_LOOP, 1, id="numeric-pushes-create-nothing"),
    # the while loop holds no creating site, so it adds 0 and is not unbounded;
    # the array.new_float after it adds 1: 0 + 1 = 1
    pytest.param(WHILE_LOOP_AROUND_NOTHING, 1, id="while-loop-around-nothing-adds-nothing"),
    # the arms are not assumed exclusive, so each arm's array.new_float counts: 1 + 1 = 2
    pytest.param(IF_ELSE_ARRAYS, 2, id="both-arms-of-if-else-count"),
    # outer i = 0, 1 (2 iterations) x inner j = 0, 1, 2 (3 iterations) x 1 array.new_int = 6
    pytest.param(NESTED_STATIC_LOOPS, 6, id="nested-static-for-loops-multiply"),
    # array.new_float 1 + original.copy(), the method spelling of array.copy, 1 = 2
    pytest.param(COPY_AS_A_METHOD, 2, id="copy-written-as-a-method-counts-one"),
    # 0: str.tostring and + build plain string temporaries, which are not objects
    pytest.param(STRING_TEMPORARIES, 0, id="plain-string-temporaries-are-not-objects"),
]


@pytest.mark.parametrize(("body", "objects"), BOUNDED_CASES)
def test_receipt_counts_the_objects_one_execution_creates(body, objects):
    assert receipt_text(HEADER + body) == bounded_receipt(objects)


UNBOUNDED_CASES = [
    # unbounded: the end bound reads a series (n = int(close)), so no trip count is known when
    # the script is translated, and the loop holds a creating site
    pytest.param(SERIES_BOUND_LOOP_AROUND_AN_ARRAY, id="for-loop-with-a-series-bound-around-an-array"),
    # unbounded: the copy of an array of strings owns as many strings as its source holds, a
    # number the count does not track (the source's literal size 2 is not followed), so the
    # copy has no static bound; the source alone would be 1 + 2 = 3
    pytest.param(STRING_ARRAY_COPY, id="copy-of-a-string-array"),
]


@pytest.mark.parametrize("body", UNBOUNDED_CASES)
def test_receipt_is_unbounded_when_no_static_bound_holds(body):
    assert receipt_text(HEADER + body) == unbounded_receipt()


def test_documents_are_spelled_canonically():
    # Sorted keys and no whitespace, spelled out in full instead of built by the helpers above;
    # an unbounded document holds the status and no perExecution key.
    # array.new_float 1 + array.copy 1 + array.slice 1 = 3
    assert receipt_text(HEADER + NEW_COPY_AND_SLICE) == '{"strategy":{"objects":{"perExecution":3}},"version":1}'
    assert receipt_text(HEADER + SERIES_BOUND_LOOP_AROUND_AN_ARRAY) == (
        '{"strategy":{"objects":{"perExecutionStatus":"unbounded"}},"version":1}')


def test_receipt_is_identical_across_transpiles_of_one_source():
    source = HEADER + NEW_COPY_AND_SLICE
    first = receipt_literal(source)
    # array.new_float 1 + array.copy 1 + array.slice 1 = 3
    assert receipt_literal(source) == first
    assert json.loads(first) == bounded_receipt(3)


def test_receipt_ignores_comments_and_layout():
    commented = (
        '//@version=6\n'
        '// the script header\n'
        'strategy("objects") // declaration\n'
        '\n'
        '// one array\n'
        'levels = array.new_float(5) // five slots\n'
        '// done\n'
    )
    plain = receipt_literal(HEADER + FLOAT_ARRAY)
    # 1 array.new_float = 1
    assert receipt_literal(commented) == plain
    assert json.loads(plain) == bounded_receipt(1)


def test_exports_are_emitted_once_in_the_block_of_the_other_receipts():
    cpp = transpiled(HEADER + NEW_COPY_AND_SLICE)
    definition = require_export(cpp, "strategy_objects_receipt", RECEIPT_DEFINITION)
    require_export(cpp, "strategy_objects_api_version", API_VERSION_NAME)
    assert API_VERSION_DEFINITION.search(cpp), "strategy_objects_api_version must return 1u"
    assert RECEIPT_SIGNATURE.search(cpp), "strategy_objects_receipt must take the receipt protocol's six parameters"
    assert "::pineforge::checked_settings::receipt(" in function_body(cpp, definition.start()), (
        "strategy_objects_receipt must hand its document to ::pineforge::checked_settings::receipt")
    for name in OBJECTS_NAMES:
        assert len(re.findall(rf"\b{name}\b", cpp)) == 1, f"{name} must appear exactly once"
    block = settings_block(cpp, "strategy_order_shapes_receipt")
    assert block is not None, "strategy_order_shapes_receipt is expected inside #ifdef PF_SETTINGS_API_VERSION"
    for name in ("strategy_id_sites_api_version", "strategy_id_sites_receipt", *OBJECTS_NAMES):
        assert settings_block(cpp, name) == block, (
            f"{name} must sit in the #ifdef PF_SETTINGS_API_VERSION group of strategy_order_shapes_receipt")


# More creating shapes. A site counts the same objects on every route that lowers it: a TA call
# used as a value, a matrix method called on a field of a user type, a request payload, a history
# reference and a function's return, and a string stored in a field or copied into another array.
# The expectations come from the same rules, worked out by hand.

PIVOT_LEVELS_DECLARATION = 'levels = ta.pivot_point_levels("Traditional", timeframe.change("D"))\n'

PIVOT_LEVELS_WITH_A_CONSTANT_ANCHOR = 'levels = ta.pivot_point_levels("Traditional", true)\n'

PIVOT_LEVELS_AS_AN_ARGUMENT = 'first_level = array.get(ta.pivot_point_levels("Traditional", true), 0)\n'

PIVOT_LEVELS_IN_A_FUNCTION_CALLED_TWICE = '''level_of() =>
    array.get(ta.pivot_point_levels("Traditional", true), 0)
first_level = level_of()
second_level = level_of()
'''

MATRIX_RESULTS_OF_A_FIELD = '''type Holder
    matrix<float> grid
h = Holder.new(matrix.new<float>(2, 2, 1.0))
matrix<float> flipped = h.grid.transpose()
array<float> first_row = h.grid.row(0)
'''

STRING_MATRIX_ROW_OF_A_FIELD = '''type Names
    matrix<string> cells
n = Names.new(matrix.new<string>(2, 2, "a"))
array<string> first_row = n.cells.row(0)
'''

REQUEST_READING_A_GLOBAL_THAT_BUILDS_AN_ARRAY = '''total = array.sum(array.from(close, open))
requested = request.security(syminfo.tickerid, "1", total)
'''

REQUEST_READING_A_GLOBAL_FROM_A_FUNCTION_BUILDING_AN_ARRAY = '''pair_total() =>
    array.sum(array.from(close, open))
total = pair_total()
requested = request.security(syminfo.tickerid, "1", total)
'''

STRING_FIELD_STORED_IN_A_FOR_IN_LOOP = '''type Row
    string note
rows = array.new<Row>()
rows.push(Row.new())
for r in rows
    r.note := "seen"
'''

STRING_FIELD_STORED_IN_A_STATIC_LOOP = '''type Trade
    string note
var t = Trade.new()
for i = 0 to 4
    t.note := "entry " + str.tostring(i)
'''

STRING_ARRAY_REBOUND_TO_AN_ARRAY_FIELD = '''type Bag
    array<string> xs
names = array.from("a", "b")
bag = Bag.new()
bag.xs := names
'''

STRING_ARRAY_REASSIGNED = '''a = array.new_string(0)
b = array.from("x", "y")
a := b
'''

STRING_ARRAY_ALIASED = '''b = array.from("x", "y")
c = b
'''

NUMERIC_ARRAY_REASSIGNED = '''a = array.new_float(0)
b = array.new_float(2)
a := b
'''

ARRAY_HISTORY_REFERENCE = '''a = array.from(1.0, 2.0)
earlier = a[1]
n = array.size(earlier)
'''

FOR_IN_OVER_A_STRING_KEYED_MAP = '''m = map.new<string, float>()
m.put("a", 1.0)
total = 0.0
for [k, v] in m
    total += v
'''

FUNCTION_RETURNING_A_GLOBAL_FLOAT_ARRAY = '''levels = array.new_float(3)
levels_now() =>
    levels
snapshot = levels_now()
'''

FUNCTION_RETURNING_A_GLOBAL_STRING_ARRAY = '''names = array.new_string(2, "a")
names_now() =>
    names
snapshot = names_now()
'''

# (body, objects one execution creates), worked out by hand from the rules in the module
# docstring (the arithmetic is on the row), never read off a transpile.
LOWERING_ROUTE_CASES = [
    # ta.pivot_point_levels builds a new array of levels on every call = 1
    pytest.param(PIVOT_LEVELS_DECLARATION, 1, id="pivot-levels-declaration-counts-its-array"),
    # the same call with a constant anchor = 1
    pytest.param(PIVOT_LEVELS_WITH_A_CONSTANT_ANCHOR, 1,
                 id="pivot-levels-with-a-constant-anchor-counts-its-array"),
    # the call is the array that array.get reads, and array.get creates nothing = 1
    pytest.param(PIVOT_LEVELS_AS_AN_ARGUMENT, 1, id="pivot-levels-as-an-argument-count-their-array"),
    # level_of() holds 1 pivot array and has 2 static calls: 2 x 1 = 2
    pytest.param(PIVOT_LEVELS_IN_A_FUNCTION_CALLED_TWICE, 2, id="pivot-levels-in-a-function-called-twice"),
    # the receivers are a field of a user type:
    # Holder.new 1 + matrix.new 1 + transpose 1 + row 1 = 4
    pytest.param(MATRIX_RESULTS_OF_A_FIELD, 4, id="matrix-results-of-a-field-count-one-each"),
    # array.from 1 + the copy of a that the history keeps when the bar ends 1
    # + the copy earlier is bound to 1 = 3
    pytest.param(ARRAY_HISTORY_REFERENCE, 3, id="history-reference-of-an-array-counts-its-copy"),
    # Trade.new 1 + i = 0 .. 4 (5 trips) x 1 non-empty string stored in note = 1 + 5 = 6
    pytest.param(STRING_FIELD_STORED_IN_A_STATIC_LOOP, 6,
                 id="string-field-store-in-a-static-loop-counts-every-trip"),
    # array.new_float 1 + the copy levels_now() hands back (an array a variable holds, by value) 1 = 2
    pytest.param(FUNCTION_RETURNING_A_GLOBAL_FLOAT_ARRAY, 2,
                 id="function-returning-a-global-float-array-counts-the-copy"),
]


@pytest.mark.parametrize(("body", "objects"), LOWERING_ROUTE_CASES)
def test_receipt_counts_objects_made_through_every_lowering_route(body, objects):
    assert receipt_text(HEADER + body) == bounded_receipt(objects)


# Each of these makes strings or arrays at a rate the count does not track, so no number holds.
OWNED_ELEMENT_CASES = [
    # unbounded: row copies the strings of a string matrix held in a field
    # (Names.new 1 + matrix.new<string> 1 + 2 x 2 strings = 6 without the row)
    pytest.param(STRING_MATRIX_ROW_OF_A_FIELD, id="row-of-a-string-matrix-held-in-a-field"),
    # unbounded: the payload is a global whose declaration builds an array, and the evaluator
    # builds it again for every requested bar (counted on the chart alone it would be 1).
    # The payload builder accepts this shape: the row transpiles it and reads its receipt.
    pytest.param(REQUEST_READING_A_GLOBAL_THAT_BUILDS_AN_ARRAY,
                 id="request-payload-reading-a-global-that-builds-an-array"),
    # unbounded: the same, through a global bound to a user function that builds the array
    # (counted on the chart alone it would be 1); the row transpiles this shape too.
    pytest.param(REQUEST_READING_A_GLOBAL_FROM_A_FUNCTION_BUILDING_AN_ARRAY,
                 id="request-payload-reading-a-global-from-a-function-building-an-array"),
    # unbounded: the string stored in note sits in a for ... in loop, which has no static trip
    # count (array.new 1 + Row.new 1 = 2 without the store)
    pytest.param(STRING_FIELD_STORED_IN_A_FOR_IN_LOOP, id="string-field-store-in-a-for-in-loop"),
    # unbounded: the field takes a copy of names, an array of strings the count does not track
    # (array.from 1 + 2 + Bag.new 1 + its array field 1 = 5 without the copy)
    pytest.param(STRING_ARRAY_REBOUND_TO_AN_ARRAY_FIELD, id="string-array-rebound-to-an-array-field"),
    # unbounded: a := b copies an array of strings the count does not track
    # (array.new_string 1 + array.from 1 + 2 = 4 without the copy)
    pytest.param(STRING_ARRAY_REASSIGNED, id="string-array-reassigned-from-another-array"),
    # unbounded: c = b copies the same array of strings (array.from 1 + 2 = 3 without the copy)
    pytest.param(STRING_ARRAY_ALIASED, id="string-array-aliased-by-a-declaration"),
    # unbounded: for [k, v] in m builds the key array of a string-keyed map, which copies its
    # strings, as m.keys() does (map.new 1 + put 1 + the key "a" 1 = 3 without the loop)
    pytest.param(FOR_IN_OVER_A_STRING_KEYED_MAP, id="for-in-loop-over-a-string-keyed-map"),
    # unbounded: names_now() hands back a copy of an array of strings
    # (array.new_string 1 + 2 = 3 without the copy)
    pytest.param(FUNCTION_RETURNING_A_GLOBAL_STRING_ARRAY,
                 id="function-returning-a-global-string-array"),
]


@pytest.mark.parametrize("body", OWNED_ELEMENT_CASES)
def test_receipt_is_unbounded_for_owned_copies_stores_and_request_payloads(body):
    assert receipt_text(HEADER + body) == unbounded_receipt()


def test_receipt_leaves_a_reassigned_numeric_array_at_the_plain_count():
    # array.new_float 1 + array.new_float 1 = 2; a numeric array owns no strings, so a := b adds 0
    assert receipt_text(HEADER + NUMERIC_ARRAY_REASSIGNED) == bounded_receipt(2)


# A `for` loop whose body assigns its own counter has no static trip count: `i += 1` or `i := 0`
# in the body makes the loop run more or fewer iterations than its bounds spell, so a creating
# site in it has no static count. A loop that only reads its counter keeps the count of its
# bounds, and a body that creates nothing adds nothing either way. Each value is worked out by
# hand from those rules, never read off a transpile.

FOR_LOOP_ASSIGNING_ITS_COUNTER = '''for i = 0 to 2
    levels = array.new_float(1)
    i += 1
'''

FOR_LOOP_READING_ITS_COUNTER = '''count = 0
for i = 0 to 2
    levels = array.new_float(1)
    count += i
'''

FOR_LOOP_ASSIGNING_ITS_COUNTER_IN_AN_IF = '''for i = 0 to 2
    levels = array.new_float(1)
    if close > open
        i := i + 1
'''

FOR_LOOP_ASSIGNING_ITS_COUNTER_IN_AN_INNER_LOOP = '''for i = 0 to 2
    levels = array.new_float(1)
    for j = 0 to 1
        i += j
'''

FOR_LOOP_ASSIGNING_ITS_COUNTER_AROUND_NOTHING = '''levels = array.new_float(1)
for i = 0 to 2
    i += 1
'''

COUNTER_BOUNDED_CASES = [
    # 3: the body only reads i (count += i assigns count): abs(2 - 0) // 1 + 1 = 3 iterations x 1 array = 3
    pytest.param(FOR_LOOP_READING_ITS_COUNTER, 3, id="counter-only-read-keeps-its-trip-count"),
    # 1: the array before the loop adds 1; the loop's body creates nothing, so it adds 0: 1 + 0 = 1
    pytest.param(FOR_LOOP_ASSIGNING_ITS_COUNTER_AROUND_NOTHING, 1,
                 id="counter-assigned-around-nothing-adds-nothing"),
]


@pytest.mark.parametrize(("body", "objects"), COUNTER_BOUNDED_CASES)
def test_receipt_keeps_the_trip_count_of_a_loop_unless_the_body_assigns_its_counter(body, objects):
    assert receipt_text(HEADER + body) == bounded_receipt(objects)


COUNTER_UNBOUNDED_CASES = [
    # unbounded: i += 1 moves the counter, so the 3 trips the bounds spell (i = 0, 1, 2) are not the loop's
    pytest.param(FOR_LOOP_ASSIGNING_ITS_COUNTER, id="counter-assigned-in-the-body-is-unbounded"),
    # unbounded: i := i + 1 inside an if of the body moves the counter just the same
    pytest.param(FOR_LOOP_ASSIGNING_ITS_COUNTER_IN_AN_IF, id="counter-assigned-inside-an-if-is-unbounded"),
    # unbounded: i += j in the body of an inner loop moves the outer counter (the inner loop adds 0)
    pytest.param(FOR_LOOP_ASSIGNING_ITS_COUNTER_IN_AN_INNER_LOOP,
                 id="counter-assigned-in-an-inner-loop-is-unbounded"),
]


@pytest.mark.parametrize("body", COUNTER_UNBOUNDED_CASES)
def test_receipt_is_unbounded_for_a_loop_that_assigns_its_own_counter(body):
    assert receipt_text(HEADER + body) == unbounded_receipt()


# Copies and stores on the remaining routes: a `var` declaration's first-bar initializer that
# takes another variable's array, an element stored through `xs[k] := v` (priced as array.set
# prices it), and a function whose last statement is a tuple declaration, which returns copies
# of the arrays its names hold. Each value is worked out by hand from the rules above.

VAR_STRING_ARRAY_ALIASED_BY_A_VAR = '''var b = array.from("x", "y")
var c = b
'''

VAR_NUMERIC_ARRAY_ALIASED_BY_A_VAR = '''var b = array.from(1.0, 2.0)
var c = b
'''

STRING_ELEMENT_STORED_IN_A_STATIC_LOOP = '''names = array.new_string(2)
for i = 0 to 9
    names[0] := "x" + str.tostring(i)
'''

NUMERIC_ELEMENT_STORED_IN_A_STATIC_LOOP = '''levels = array.new_float(2)
for i = 0 to 9
    levels[0] := i * 2.0
'''

STRING_ARRAYS_RETURNED_BY_A_TUPLE_DECLARATION = '''pair() => [array.from("a", "b"), array.from("c")]
both() =>
    [p, q] = pair()
[x, y] = both()
'''

REMAINING_ROUTE_BOUNDED_CASES = [
    # array.from over two numbers 1 + var c = b, an array of numbers taken by a variable, 0 = 1
    pytest.param(VAR_NUMERIC_ARRAY_ALIASED_BY_A_VAR, 1, id="var-numeric-array-aliased-by-a-var"),
    # array.new_string 1 (no initial value) + i = 0 .. 9 (10 trips) x 1 non-empty string = 1 + 10 = 11
    pytest.param(STRING_ELEMENT_STORED_IN_A_STATIC_LOOP, 11,
                 id="string-element-store-in-a-static-loop-counts-every-trip"),
    # array.new_float 1 + i = 0 .. 9 (10 trips) x 0 (a number owns nothing) = 1
    pytest.param(NUMERIC_ELEMENT_STORED_IN_A_STATIC_LOOP, 1, id="numeric-element-store-adds-nothing"),
]


@pytest.mark.parametrize(("body", "objects"), REMAINING_ROUTE_BOUNDED_CASES)
def test_receipt_counts_element_stores_and_leaves_numeric_aliases_at_the_plain_count(body, objects):
    assert receipt_text(HEADER + body) == bounded_receipt(objects)


REMAINING_ROUTE_UNBOUNDED_CASES = [
    # unbounded: var c = b copies an array of strings the count does not track
    # (array.from 1 + 2 = 3 without the copy)
    pytest.param(VAR_STRING_ARRAY_ALIASED_BY_A_VAR, id="var-string-array-aliased-by-a-var"),
    # unbounded: both() returns copies of p and q, arrays of strings the count does not track
    # (array.from 1 + 2 + array.from 1 + 1 = 5 without the copies)
    pytest.param(STRING_ARRAYS_RETURNED_BY_A_TUPLE_DECLARATION,
                 id="string-arrays-returned-by-a-tuple-declaration"),
]


@pytest.mark.parametrize("body", REMAINING_ROUTE_UNBOUNDED_CASES)
def test_receipt_is_unbounded_for_owned_copies_on_the_remaining_routes(body):
    assert receipt_text(HEADER + body) == unbounded_receipt()


# Tuples held before their names take them. A tuple chosen by an `if` or a `switch` is built in a
# named temporary, which the names copy whichever way they bind: in one declaration, or one by one
# when a name is stored in its own member (a function declaring a `var` of the same name makes it
# one). A tuple a call returns is held the same way when a name is so stored, and each element is
# copied out of it. A function whose last statement is a tuple declaration returns copies of the
# arrays its names hold, arrays of numbers included. Each value is worked out by hand from the
# rules above.

STRING_ARRAYS_SELECTED_BY_AN_IF = '''[x, y] = if close > open
    [array.from("a", "b"), array.from("c")]
else
    [array.from("d"), array.from("e")]
'''

NUMERIC_ARRAYS_SELECTED_BY_AN_IF = '''[x, y] = if close > open
    [array.from(1.0), array.from(2.0)]
else
    [array.from(3.0), array.from(4.0)]
'''

STRING_ARRAY_SELECTED_INTO_A_MEMBER = '''left() =>
    var float state = 1.0
    state += 1.0
    state
a = left()
[state, names] = if close > open
    [100.0, array.from("a", "b")]
else
    [200.0, array.from("c")]
read() => state
b = read()
'''

STRING_ARRAY_DESTRUCTURED_INTO_A_MEMBER = '''left() =>
    var float state = 1.0
    state += 1.0
    state
pair() => [100.0, array.from("a", "b")]
a = left()
[state, names] = pair()
read() => state
b = read()
'''

NUMERIC_ARRAYS_RETURNED_BY_A_TUPLE_DECLARATION = '''pair() => [array.from(1.0, 2.0), array.from(3.0)]
both() =>
    [p, q] = pair()
[x, y] = both()
'''

HELD_TUPLE_BOUNDED_CASES = [
    # array.from over one number 1 in each of the four arm elements (both arms count) = 4; x and y
    # take a copy of the selected tuple, arrays of numbers taken by variables, which adds 0: 4 + 0 = 4
    pytest.param(NUMERIC_ARRAYS_SELECTED_BY_AN_IF, 4, id="numeric-arrays-selected-by-an-if"),
    # pair() makes array.from over two numbers 1 + array.from over one number 1 = 2; both() returns
    # a copy of each array p and q hold, one new array each, 1 + 1 = 2: 2 + 2 = 4
    pytest.param(NUMERIC_ARRAYS_RETURNED_BY_A_TUPLE_DECLARATION, 4,
                 id="numeric-arrays-returned-by-a-tuple-declaration"),
]


@pytest.mark.parametrize(("body", "objects"), HELD_TUPLE_BOUNDED_CASES)
def test_receipt_counts_numeric_arrays_copied_out_of_a_held_tuple(body, objects):
    assert receipt_text(HEADER + body) == bounded_receipt(objects)


HELD_TUPLE_UNBOUNDED_CASES = [
    # unbounded: x and y take a copy of the selected tuple, arrays of strings the count does not
    # track (array.from 1 + 2, 1 + 1, 1 + 1 and 1 + 1 in the two arms = 9 without the copy)
    pytest.param(STRING_ARRAYS_SELECTED_BY_AN_IF, id="string-arrays-selected-by-an-if"),
    # unbounded: left() declares a var named state, so the top-level state is stored in its own
    # member; the selected tuple is copied into a second temporary, and names takes a copy of its
    # array of strings (array.from 1 + 2 + array.from 1 + 1 = 5 without the copies)
    pytest.param(STRING_ARRAY_SELECTED_INTO_A_MEMBER, id="string-array-selected-into-a-member"),
    # unbounded: left() declares a var named state, so the top-level state is stored in its own
    # member; the tuple pair() returns is held, and names takes a copy of its array of strings
    # (array.from 1 + 2 = 3 without the copy)
    pytest.param(STRING_ARRAY_DESTRUCTURED_INTO_A_MEMBER,
                 id="string-array-destructured-into-a-member"),
]


@pytest.mark.parametrize("body", HELD_TUPLE_UNBOUNDED_CASES)
def test_receipt_is_unbounded_for_string_arrays_copied_out_of_a_held_tuple(body):
    assert receipt_text(HEADER + body) == unbounded_receipt()


# A declaration of the counter's name in the body of a `for` loop is a new variable in Pine, but
# where the name is a member (an `if` block of the script declares it) the declaration is written
# as an assignment of that name, which inside the emitted loop is the counter: the loop then runs
# a number of iterations its bounds do not give. A declaration of another name keeps the count of
# the bounds. Each value is worked out by hand from those rules, never read off a transpile.

FOR_LOOP_REDECLARING_ITS_COUNTER = '''if close > open
    i = 0.0
for i = 0 to 9 by 3
    i = i - 1
    levels = array.new_float(1)
'''

FOR_LOOP_DECLARING_ANOTHER_NAME = '''if close > open
    k = 0.0
for i = 0 to 9 by 3
    k = i - 1
    levels = array.new_float(1)
'''

COUNTER_DECLARATION_BOUNDED_CASES = [
    # 4: k = i - 1 declares another name, so i = 0, 3, 6, 9: abs(9 - 0) // 3 + 1 = 4 iterations
    # x 1 array = 4
    pytest.param(FOR_LOOP_DECLARING_ANOTHER_NAME, 4,
                 id="declaration-of-another-name-keeps-its-trip-count"),
]


@pytest.mark.parametrize(("body", "objects"), COUNTER_DECLARATION_BOUNDED_CASES)
def test_receipt_keeps_the_trip_count_of_a_loop_whose_body_declares_another_name(body, objects):
    assert receipt_text(HEADER + body) == bounded_receipt(objects)


COUNTER_DECLARATION_UNBOUNDED_CASES = [
    # unbounded: i = i - 1 writes the counter, so the body starts at i = 0, 2, 4, 6 and 8, 5 trips
    # (reading the bounds alone would give abs(9 - 0) // 3 + 1 = 4 iterations x 1 array = 4)
    pytest.param(FOR_LOOP_REDECLARING_ITS_COUNTER, id="counter-redeclared-in-the-body-is-unbounded"),
]


@pytest.mark.parametrize("body", COUNTER_DECLARATION_UNBOUNDED_CASES)
def test_receipt_is_unbounded_for_a_loop_whose_body_redeclares_its_counter(body):
    assert receipt_text(HEADER + body) == unbounded_receipt()
