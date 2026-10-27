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
end and step are known when the script is translated, and both arms of an ``if`` count. A
``while`` or ``for ... in`` loop, a ``for`` loop with a computed bound and a recursion around a
creating site have no static bound, while a loop around a body that creates nothing adds nothing.
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
