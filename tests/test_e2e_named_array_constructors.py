"""Named array constructors retain their values through execution contexts.

Every changed producer is checked against an analytic result and a positional
twin, at defaults and with overrides. The reversed-keyword effect test uses
explicit Pine statements as its control, so C++ operand order cannot make both
implementations agree on the wrong answer.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu
from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)


HEADER = """//@version=6
strategy("Array constructor contexts", process_orders_on_close = true)
int n = input.int(3, "Size", minval = 2)
float seed = input.float(1.5, "Fill")
float v = seed + bar_index
"""
TAIL = """
// @pf-trace valid=valid
if valid and bar_index % 7 == 3
    strategy.entry("L", strategy.long, qty = n)
if bar_index % 7 == 5
    strategy.close("L")
"""


# The placeholders stand for the same constructor in both sources. Only the
# binding spelling changes; observers, type declarations and control flow do not.
CONTEXTS = {
    "bindings": """
a = {nv}
b = {reversed}
c = {mixed}
d = {alias}
valid = a.size() == n and b.size() == n and c.size() == n and d.size() == n and a.sum() == n*v and b.sum() == n*v and c.sum() == n*v and d.sum() == n*v
""",
    "qualifiers": """
make(simple int count, simple float value) =>
    values = {formal}
    values.sum()
seriesValues = {series}
valid = make(n, seed) == n*seed and seriesValues.size() == n + bar_index % 2 and seriesValues.sum() == (n + bar_index % 2)*v
""",
    "lifetime": """
ordinary = {nv}
var persistent = {nv}
varip intrabar = {nv}
valid = ordinary.size() == n and ordinary.sum() == n*v and persistent.size() == n and persistent.sum() == n*seed and intrabar.size() == n and intrabar.sum() == n*seed
""",
    "first_reach": """
delayed(int count, float value) =>
    var local = {formal}
    varip localip = {formal}
    local.sum() + localip.sum()
bool valid = true
if bar_index >= 3
    var blockArray = {nv}
    varip blockArrayip = {nv}
    valid := blockArray.sum() == n*(seed + 3) and blockArrayip.sum() == n*(seed + 3) and delayed(n, v) == 2*n*(seed + 3)
""",
    "loop_freshness": """
float counted = 0
for i = 1 to n
    values = {loop}
    counted += values.sum()
float repeated = 0
int j = 1
while j <= n
    values = {while_loop}
    repeated += values.sum()
    j += 1
float iterated = 0
for count in array.from(1, 2, 3)
    values = {for_in}
    iterated += values.sum()
valid = counted == n*(n + 1)*v/2 and repeated == counted and iterated == 6*v
""",
    "history": """
values = {nv}
var persistent = {nv}
bool valid = values.size() == n and values.sum() == n*v
if bar_index > 0
    previous = values[1]
    previousPersistent = persistent[1]
    valid := valid and previous.size() == n and previous.sum() == n*(v - 1) and previousPersistent.sum() == n*seed and array.get(values[1], 0) == v - 1
""",
    "request_payload": """
payload(int count, float value) =>
    values = {formal}
    values.sum()
requested = request.security(syminfo.tickerid, timeframe.period, payload(n, close))
valid = requested == n*close
""",
    "request_local_arguments": """
payload(int count, float value, float delta) =>
    int localCount = count + 1
    float localValue = value + delta
    values = {local_formal}
    values.sum()
requested = request.security(syminfo.tickerid, timeframe.period, payload(n, close, seed))
valid = requested == (n + 1)*(close + seed)
""",
    "request_expression_arguments": """
payload(int count, float value, float delta) =>
    values = {expression_formal}
    values.sum()
requested = request.security(syminfo.tickerid, timeframe.period, payload(n, close, seed))
valid = requested == (n + 1)*(close + seed)
""",
    "reference_fill": """
type Point
    float x
point = Point.new(v)
values = {point}
point.x := v + seed
held = values.get(1)
valid = values.size() == n and held.x == v + seed
""",
    "context_routes": """
type Holder
    array<float> values
array<float> values = array.new<float>()
values := {nv}
holder = Holder.new({reversed})
holder.values := {mixed}
alias = values
valid = values.size() == n and alias.sum() == n*v and holder.values.size() == n and holder.values.sum() == n*v
""",
    "temporary_receiver": """
first(int count, float value) =>
    {formal}.get(0)
sumArray(array<float> values) =>
    values.sum()
method totalArray(array<float> values) =>
    values.sum()
direct = {nv}.get(0)
directSum = {nv}.sum()
passed = sumArray({nv})
methodPassed = {nv}.totalArray()
valid = direct == v and directSum == n*v and first(n, v) == v and passed == n*v and methodPassed == n*v
""",
    "scalar_families": """
floats = {nv}
floatAlias = {alias}
ints = {ints}
intAlias = {int_alias}
bools = {bools}
boolAlias = {bool_alias}
strings = {strings}
stringAlias = {string_alias}
valid = floats.size() == n and floatAlias.size() == n and ints.size() == n and intAlias.size() == n and bools.size() == n and boolAlias.size() == n and strings.size() == n and stringAlias.size() == n and floats.get(0) == v and floatAlias.get(0) == v and ints.get(0) == n and intAlias.get(0) == n and bools.get(0) == (close >= open) and boolAlias.get(0) == (close >= open) and strings.get(0) == str.tostring(v) and stringAlias.get(0) == str.tostring(v)
""",
    "explicit_na": """
floats = {float_na}
ints = {int_na}
strings = {string_na}
colors = {color_na}
lines = {line_na}
valid = floats.size() == n and ints.size() == n and strings.size() == n and colors.size() == n and lines.size() == n and na(floats.get(0)) and na(ints.get(0)) and na(strings.get(0)) and na(colors.get(0)) and na(lines.get(0))
""",
    "reference_defaults": """
type Point
    float x
lines = {line_default}
boxes = {box_default}
labels = {label_default}
linefills = {linefill_default}
points = {point_default}
valid = lines.size() == n and boxes.size() == n and labels.size() == n and linefills.size() == n and points.size() == n and na(lines.get(0)) and na(boxes.get(0)) and na(labels.get(0)) and na(linefills.get(0)) and na(points.get(0))
""",
    "enum_and_color": """
enum Direction
    up
    down
directions = {enum_fill}
missing = {enum_default}
colors = {colors}
valid = directions.size() == n and missing.size() == n and colors.size() == n and directions.get(0) == Direction.up and na(missing.get(0)) and colors.get(0) == color.red
""",
    "bool_string_defaults": """
bools = {bool_default}
strings = {string_default}
valid = bools.size() == n and strings.size() == n and not bools.get(0) and na(strings.get(0))
""",
    "epoch_fills": """
var int firstTime = time
values = {epoch}
var persistent = {epoch}
valid = values.size() == n and values.get(0) == time and persistent.size() == n and persistent.get(0) == firstTime
""",
    "zero_size_evaluation": """
events = array.new<int>()
nextValue() =>
    events.push(1)
    v
values = {zero_fill}
empty = array.new<float>()
valid = values.size() == 0 and empty.size() == 0 and events.size() == 1
""",
}


def source(context, named):
    def call(size, value=None, ctor="new<float>", form="normal"):
        if not named:
            arguments = size if value is None else f"{size}, {value}"
        elif value is None:
            arguments = f"size = {size}"
        elif form == "reversed":
            arguments = f"initial_value = {value}, size = {size}"
        elif form == "mixed":
            arguments = f"{size}, initial_value = {value}"
        else:
            arguments = f"size = {size}, initial_value = {value}"
        return f"array.{ctor}({arguments})"

    calls = {
        "nv": call("n", "v"),
        "reversed": call("n", "v", form="reversed"),
        "mixed": call("n", "v", form="mixed"),
        "alias": call("n", "v", "new_float"),
        "formal": call("count", "value", form="reversed"),
        "local_formal": call("localCount", "localValue", form="reversed"),
        "expression_formal": call("math.max(count, 1) + 1", "value + delta", form="reversed"),
        "series": call("n + bar_index % 2", "v"),
        "loop": call("i", "v"),
        "while_loop": call("j", "v"),
        "for_in": call("count", "v"),
        "point": call("n", "point", "new<Point>"),
        "ints": call("n", "n", "new<int>"),
        "int_alias": call("n", "n", "new_int"),
        "bools": call("n", "close >= open", "new<bool>"),
        "bool_alias": call("n", "close >= open", "new_bool"),
        "strings": call("n", "str.tostring(v)", "new<string>"),
        "string_alias": call("n", "str.tostring(v)", "new_string"),
        "colors": call("n", "color.red", "new<color>"),
        "enum_fill": call("n", "Direction.up", "new<Direction>"),
        "enum_default": call("n", ctor="new<Direction>"),
        "epoch": call("n", "time", "new<int>"),
        "zero_fill": (
            "array.new<float>(initial_value = nextValue())" if named
            else "array.new<float>(0, nextValue())"),
    }
    for family in ("float", "int", "string", "color", "line"):
        calls[f"{family}_na"] = call("n", "na", f"new<{family}>")
    for family in ("line", "box", "label", "linefill", "Point", "bool", "string"):
        calls[f"{family.lower()}_default"] = call("n", ctor=f"new<{family}>")
    return HEADER + CONTEXTS[context].format(**calls) + TAIL


@pytest.mark.parametrize("context", CONTEXTS)
def test_named_context_and_its_accepted_positional_twin_transpile(context):
    # These sources are self-contained public regression inputs. No private
    # probe directory is required to collect or execute this module.
    for named in (True, False):
        cpp = transpile(source(context, named))
        assert "class GeneratedStrategy" in cpp


@pytest.fixture(scope="module")
def context_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("named_array_contexts")
    feed = chart_feed_head(engine, base, 40)
    builds = {
        f"{context}-{form}": Build(
            source(context, form == "named"),
            overrides={"Size": 4, "Fill": 2.5}, trace=True)
        for context in CONTEXTS for form in ("named", "positional")
    }
    return execute_all(engine, feed, base, builds)


@pytest.mark.parametrize("context", CONTEXTS)
def test_named_constructor_context_has_analytic_values_and_matches_its_twin(context_runs, context):
    subject = ok(context_runs, f"{context}-named")
    reference = ok(context_runs, f"{context}-positional")
    for outcome in (subject, reference):
        for tag in ("default", "override"):
            records = [r for r in outcome.traces[tag] if r["name"] == "valid"]
            assert len(records) == 40
            assert all(r["value"] == 1 for r in records), (context, tag, records)
    assert_same_runs(subject, reference)


ORDER_BODY = """
events = array.new<int>()
nextSize() =>
    events.push(1)
    n
nextValue() =>
    events.push(2)
    events.size() == 1 ? seed : seed + 10
{construction}
float logCode = 0
for event in events
    logCode := logCode*10 + event
valid = values.size() == n and events.size() == 2 and logCode == 12 and values.get(0) == seed + 10
"""


@pytest.mark.parametrize("ctor", ["new<float>", "new_float"])
def test_reversed_keywords_run_in_formal_order_through_the_public_harness(tmp_path, ctor):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 30)
    subject = (
        f"values = array.{ctor}(initial_value = nextValue(), size = nextSize())")
    reference = (
        "evaluatedSize = nextSize()\nevaluatedValue = nextValue()\n"
        f"values = array.{ctor}(evaluatedSize, evaluatedValue)")
    runs = execute_all(engine, feed, tmp_path, {
        name: Build(HEADER + ORDER_BODY.format(construction=construction) + TAIL,
                    overrides={"Size": 4, "Fill": 2.5}, trace=True)
        for name, construction in (("named", subject), ("control", reference))
    })
    for name in runs:
        result = ok(runs, name)
        for tag in ("default", "override"):
            records = [r for r in result.traces[tag] if r["name"] == "valid"]
            assert len(records) == 30
            assert all(r["value"] == 1 for r in records), (name, tag, records)
    assert_same_runs(ok(runs, "named"), ok(runs, "control"))


def test_reported_program_runs_through_the_public_harness(tmp_path):
    engine = skip_unless_e2e_env()
    feed = chart_feed_head(engine, tmp_path, 12)
    reporter = (
        "//@version=6\nstrategy('repro')\n"
        "values = array.new<float>(size = 3, initial_value = 1.0)\n"
        "n = values.size()\ntotal = values.sum()\n"
        "// @pf-trace n=n\n// @pf-trace total=total\nplot(total)\n")
    runs = execute_all(engine, feed, tmp_path, {"reporter": Build(reporter, trace=True)})
    result = ok(runs, "reporter")
    for name in ("n", "total"):
        records = [r for r in result.traces["default"] if r["name"] == name]
        assert len(records) == 12
        assert all(r["value"] == 3 for r in records), (name, records)


@pytest.mark.parametrize("version", [5, 6])
def test_imported_constructor_arguments_keep_formal_order_and_values(version):
    library = f"""//@version={version}
library("Arrays")
markSize(array<int> events, int count) =>
    events.push(1)
    count
markFill(array<int> events, float value) =>
    events.push(2)
    events.size() == 1 ? value : value + 10
export build(array<int> events, int count, float value) =>
    values = array.new<float>(initial_value = markFill(events, value), size = markSize(events, count))
    values.sum()
"""
    source = (
        '//@version=6\nstrategy("Imported arrays")\n'
        'import pftest/Arrays/1 as A\n'
        'n = input.int(3, "Size")\nv = input.float(2.0, "Fill")\n'
        "events = array.new<int>()\ntotal = A.build(events, n, v)\n"
        "valid = total == n*(v + 10) and events.size() == 2 and events.get(0) == 1 and events.get(1) == 2\n"
    )
    cpp = transpile(source, libraries={"pftest/Arrays/1": library})
    output = run_emitted_tu(cpp, r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    strategy_set_input(&strategy, "Size", "4");
    strategy_set_input(&strategy, "Fill", "2.5");
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    std::cout << strategy.last_error() << "\n" << strategy.valid << "\n";
}
""", opt="-O0", label="imported_array_arguments")
    assert output.splitlines() == ["", "1"]
