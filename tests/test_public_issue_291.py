"""Native value regressions for the five examples in public issue #291.

Authored before product changes against c12c1877da48629b9c711f745a752ffc20ceef17.
The issue contains TradingView prose, not an archived value tape. These tests
use source-derived arithmetic oracles and native positive controls; they do
not claim another TradingView measurement or an EMA seeding convention.

The fixture follows tests/_e2e.py and the callable tuple regression pattern.
Every trace checks the complete synthetic feed, including missing history.
Set PF_PUBLIC_291_CODEGEN_ROOT to an extracted baseline or mutation tree to
run these same tests against that tree's gate and codegen.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path

import pytest


BARS = 96
START_MS = 1743465600000
BAR_MS = 900000
HEADER = '//@version=6\nstrategy("Public value regressions", initial_capital=100000)\n'


class EvidenceError(ValueError):
    """Missing prerequisites or malformed observations are not semantic RED."""


class SemanticMismatch(AssertionError):
    """A complete native value stream disagrees with its source oracle."""


def _require(condition, detail):
    if not condition:
        raise EvidenceError(detail)


def _close(bar):
    return 50.0 + bar / 32.0 + ((17 * bar) % 23) / 8.0


def _open(bar):
    return _close(bar) - (1 + bar % 5) / 8.0


def _mid(bar, gain=1.0):
    return (_close(bar) + _open(bar)) / 2.0 * gain


def _traced(source, *names):
    return source + "".join(f"// @pf-trace {name}={name}\n" for name in names)


def _values(outcome, name, tag="default"):
    records = [r for r in outcome.traces[tag] if r["name"] == name]
    _require(len(records) == BARS, f"{tag}/{name}: trace length {len(records)}")
    _require([r["bar_index"] for r in records] == list(range(BARS)),
             f"{tag}/{name}: missing, repeated or reordered bars")
    _require([r["timestamp"] for r in records]
             == [START_MS + b * BAR_MS for b in range(BARS)],
             f"{tag}/{name}: timestamps differ from the native feed")
    _require(all(type(r["value"]) in (int, float) for r in records),
             f"{tag}/{name}: nonnumeric trace")
    return [r["value"] for r in records]


def _assert_values(actual, expected, label):
    _require(len(actual) == len(expected) == BARS, f"{label}: oracle length")
    # The independent arithmetic below uses binary fractions and small ints.
    # No output rounding or tolerance is needed to hide a semantic mismatch.
    wrong = [
        (b, got, want)
        for b, (got, want) in enumerate(zip(actual, expected))
        if not (math.isnan(got) if math.isnan(want) else got == want)
    ]
    if wrong:
        raise SemanticMismatch(f"{label}: {len(wrong)}/{BARS} mismatches; "
                               f"first {wrong[:3]!r}; last {wrong[-3:]!r}")


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    required = (
        "PINEFORGE_ENGINE_INCLUDE", "PINEFORGE_EIGEN_INCLUDE",
        "PINEFORGE_GENERATED_INCLUDE", "PINEFORGE_ENGINE_LIB",
    )
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        message = f"Explicit paired native inputs required: {missing!r}"
        if os.environ.get("PINEFORGE_REQUIRE_NATIVE") == "1":
            raise EvidenceError(message)
        pytest.skip(f"paired native inputs unavailable: {', '.join(missing)}")
    generated = Path(os.environ["PINEFORGE_GENERATED_INCLUDE"])
    _require((generated / "pineforge" / "version.h").is_file(),
             "A real generated version header is required")
    from tests import _compile, _e2e

    _require(_compile.have_compile_env(), "Paired engine headers, Eigen and CXX required")
    _require(_compile.have_engine_lib(), "Paired built libpineforge.a required")
    engine = _e2e.engine_root()
    _require(engine is not None and (engine / "scripts" / "run_strategy.py").is_file(),
             "The paired engine's native runner is required")
    codegen = Path(os.environ.get("PF_PUBLIC_291_CODEGEN_ROOT", _e2e.REPO_ROOT))
    _require((codegen / "gate" / "glue.py").is_file(), "Selected codegen has no gate/glue.py")
    _require((codegen / "pineforge_codegen" / "__init__.py").is_file(),
             "Selected codegen has no package")
    base = tmp_path_factory.mktemp("public_issue_291")
    feed = base / "chart.csv"
    rows = ["timestamp,open,high,low,close,volume"]
    for b in range(BARS):
        rows.append(f"{START_MS + b * BAR_MS},{_open(b)},{_close(b) + 1},"
                    f"{_close(b) - 1},{_close(b)},{1000 + b}")
    feed.write_text("\n".join(rows) + "\n", encoding="utf-8")
    outcomes = {}

    def run(key, source, overrides=None):
        # The native setter consumes bool text and packed decimal colors.
        encoded = None if overrides is None else {
            name: ("true" if value else "false") if type(value) is bool else value
            for name, value in overrides.items()
        }
        build = _e2e.Build(source, overrides=encoded, trace=True, codegen=codegen)
        # Engine and feed are fixed for this fixture. Include every per-call
        # input, including raw Pine spelling, rather than just a lossy label.
        request = {
            "label": key,
            "build": {**asdict(build), "codegen": str(build.codegen)},
            "raw_overrides": overrides,
        }
        key = hashlib.sha256(json.dumps(
            request, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        if key not in outcomes:
            outcomes[key] = _e2e.execute(engine, feed, base / key, build)
        _require(outcomes[key].build == build, f"Reused native key {key!r}")
        outcome = _e2e.ok(outcomes, key)
        _require(set(outcome.traces) == ({"default"} if overrides is None
                                       else {"default", "override"}),
                 f"{key}: incomplete native runs")
        return outcome

    return run


HISTORY_HEAD = HEADER + """depth = input.int(3, "Depth")
gain = input.float(1.0, "Gain")
midpoint(float c, float o, float g) => (c + o) / 2 * g
mid = (close + open) / 2 * gain
"""


@pytest.mark.parametrize("shape", ("expression", "call", "nested", "fixed_offset"))
def test_expression_history_advances_once_per_bar_in_loops(native, shape):
    term = ("midpoint(close, open, gain)" if shape == "call"
            else "((close + open) / 2 * gain)")
    offset = "2" if shape == "fixed_offset" else "i"
    body = f"""float observed = 0.0
float control = 0.0
for i = 1 to depth
    observed += {term}[{offset}]
    control += mid[{offset}]
outside = ((close + open) / 2 * gain)[1]
"""
    copies = 1
    if shape == "nested":
        copies = 3
        body = """float observed = 0.0
float control = 0.0
for outer = 1 to 3
    for i = 1 to depth
        observed += ((close + open) / 2 * gain)[i]
        control += mid[i]
outside = ((close + open) / 2 * gain)[1]
"""
    out = native(f"history_{shape}", _traced(HISTORY_HEAD + body,
                 "observed", "control", "outside"), {"Depth": 5, "Gain": -0.5})
    for tag, depth, gain in (("default", 3, 1.0), ("override", 5, -0.5)):
        offsets = [2] * depth if shape == "fixed_offset" else range(1, depth + 1)
        expected = [
            math.nan if b < max(offsets)
            else copies * sum(_mid(b - i, gain) for i in offsets)
            for b in range(BARS)
        ]
        outside = [math.nan, *[_mid(b, gain) for b in range(BARS - 1)]]
        _assert_values(_values(out, "control", tag), expected, f"{shape}/{tag}/named")
        _assert_values(_values(out, "observed", tag), expected, f"{shape}/{tag}/expression")
        _assert_values(_values(out, "outside", tag), outside, f"{shape}/{tag}/outside")


def test_expression_history_updates_the_current_slot_on_repeated_evaluation(native):
    source = HISTORY_HEAD + """float running = 0.0
float observed = na
for i = 1 to depth
    running += i
    observed := (running + close)[1]
"""
    out = native("history_current_updates", _traced(source, "observed"),
                 {"Depth": 5, "Gain": -0.5})
    for tag, depth in (("default", 3), ("override", 5)):
        expected = [math.nan, *[
            _close(b) + depth * (depth + 1) / 2 for b in range(BARS - 1)
        ]]
        _assert_values(_values(out, "observed", tag), expected, f"{tag}/last evaluation")


def test_callable_expression_history_keeps_each_written_call_site(native):
    source = HISTORY_HEAD + """sumHistory(float multiplier, int count) =>
    float result = 0.0
    for i = 1 to count
        result += ((close + open) / 2 * multiplier)[i]
    result
observed = sumHistory(gain, depth)
other = sumHistory(gain * 3, depth)
"""
    out = native("history_callable", _traced(source, "observed", "other"),
                 {"Depth": 5, "Gain": -0.5})
    for tag, depth, gain in (("default", 3, 1.0), ("override", 5, -0.5)):
        for name, multiplier in (("observed", gain), ("other", gain * 3)):
            expected = [
                math.nan if b < depth else sum(
                    _mid(b - i, multiplier) for i in range(1, depth + 1)
                ) for b in range(BARS)
            ]
            _assert_values(_values(out, name, tag), expected, f"{tag}/{name}")


def test_requested_expression_history_keeps_the_requested_clock(native):
    source = HISTORY_HEAD + """observed = request.security(syminfo.tickerid, "60", ((close + open) / 2 * gain)[1])
control = request.security(syminfo.tickerid, "60", (close[1] + open[1]) / 2 * gain)
chart = ((close + open) / 2 * gain)[1]
"""
    out = native("history_requested", _traced(source, "observed", "control", "chart"),
                 {"Depth": 5, "Gain": -0.5})
    for tag, gain in (("default", 1.0), ("override", -0.5)):
        control = _values(out, "control", tag)
        defined = [v for v in control if math.isfinite(v)]
        _require(len(defined) > 8 and len(set(defined)) > 4, "No varying requested control")
        _assert_values(_values(out, "observed", tag), control, f"{tag}/requested")
        _assert_values(_values(out, "chart", tag),
                       [math.nan, *[_mid(b, gain) for b in range(BARS - 1)]],
                       f"{tag}/chart")


TUPLE_HEAD = HEADER + """origin = input.float(3.5, "Origin")
slope = input.float(0.75, "Slope")
offset = input.float(2.25, "Offset")
pair(float value, float delta) => [value, value + delta]
triple(float value, float delta) => [value - delta, value, value + delta]
float x = origin + slope * bar_index + close * 0.5
"""
TUPLE_BODIES = {
    "tuple_global": """[sibling, g] = pair(x, offset)
readGlobal() => g
float observed = readGlobal()
""",
    "plain_global": """float g = x + offset
readGlobal() => g
float observed = readGlobal()
""",
    "program_only": """[sibling, g] = pair(x, offset)
float observed = g
""",
    "parameter_shadow": """[sibling, g] = pair(x, offset)
readParam(float g) => g
float observed = readParam(2.0 * x - offset)
""",
    "callable_local": """[sibling, g] = pair(x, offset)
readLocal(float value, float delta) =>
    float g = value - delta
    g
float observed = readLocal(-x, offset)
""",
    "callable_tuple_local": """[sibling, g] = pair(x, offset)
readLocal(float value, float delta) =>
    [sibling, g] = pair(value, delta)
    g
float observed = readLocal(2.0 * x, -offset)
""",
    "tuple_sibling": """[sibling, g] = pair(x, offset)
readSibling() => sibling
float observed = readSibling()
""",
    "repeated_discard": """[_, _, g] = triple(x, offset)
[_, sibling] = pair(2.0 * x, offset)
readGlobal() => g
float observed = readGlobal() + sibling
""",
    "block_shadow": """[sibling, g] = pair(x, offset)
readGlobal() => g
float blockValue = 0.0
if bar_index >= 0
    [sibling, g] = pair(2.0 * x, -offset)
    blockValue := g
float observed = readGlobal() + blockValue
""",
}


@pytest.mark.parametrize("binding", tuple(TUPLE_BODIES))
def test_tuple_global_reads_preserve_current_bindings_and_lexical_shadows(native, binding):
    out = native(f"tuple_{binding}", _traced(TUPLE_HEAD + TUPLE_BODIES[binding],
                 "x", "observed"), {"Origin": -8.25, "Slope": -0.5, "Offset": 5.75})
    for tag, origin, slope, delta in (
        ("default", 3.5, 0.75, 2.25), ("override", -8.25, -0.5, 5.75),
    ):
        x = [origin + slope * b + _close(b) * 0.5 for b in range(BARS)]
        if binding in {"parameter_shadow", "callable_tuple_local"}:
            expected = [2.0 * v - delta for v in x]
        elif binding == "callable_local":
            expected = [-v - delta for v in x]
        elif binding == "tuple_sibling":
            expected = x
        elif binding == "repeated_discard":
            expected = [3.0 * v + 2.0 * delta for v in x]
        elif binding == "block_shadow":
            expected = [3.0 * v for v in x]
        else:
            expected = [v + delta for v in x]
        _assert_values(_values(out, "x", tag), x, f"{binding}/{tag}/producer")
        _assert_values(_values(out, "observed", tag), expected, f"{binding}/{tag}/reader")
    _require(_values(out, "x") != _values(out, "x", "override"), "Ignored tuple inputs")


def test_macd_tuple_function_reads_the_same_current_value_as_the_plain_copy(native):
    source = HEADER + """[macdLine, signalLine, hist] = ta.macd(close, 12, 26, 9)
float histCopy = hist
readHist() => hist
readCopy() => histCopy
v = readHist()
control = readCopy()
"""
    out = native("tuple_macd", _traced(source, "hist", "v", "control"))
    expected = _values(out, "hist")
    _require(len({v for v in expected if math.isfinite(v) and v != 0}) > 8,
             "MACD producer never supplied varying nonzero values")
    _assert_values(_values(out, "control"), expected, "MACD plain-copy control")
    _assert_values(_values(out, "v"), expected, "MACD tuple reader")


@pytest.mark.parametrize("scope", ("global", "block", "callable"))
@pytest.mark.parametrize("initializer", ("na", "string(na)"))
def test_typed_ordinary_string_na_resets_and_matches_its_cast_control(native, scope, initializer):
    head = HEADER + """period = input.int(3, "Period")
payload = input.string("x", "Payload")
"""
    if scope == "callable":
        body = f"""readTag(int cycle, string text) =>
    string tag = {initializer}
    if bar_index % cycle == 0
        tag := text
    [na(tag) ? 1 : 0, str.length(tag)]
[missing, length] = readTag(period, payload)
"""
    else:
        inner = f"""string tag = {initializer}
if bar_index % period == 0
    tag := payload
missing := na(tag) ? 1 : 0
length := str.length(tag)
"""
        body = "int missing = 0\nint length = 0\n"
        body += ("if bar_index >= 0\n" + "".join("    " + line + "\n"
                 for line in inner.splitlines())) if scope == "block" else inner
    source = head + body + """var int seen = 0
if missing == 0
    seen += 1
"""
    out = native(f"string_{scope}_{'bare' if initializer == 'na' else 'cast'}",
                 _traced(source, "missing", "length", "seen"),
                 {"Period": 5, "Payload": "longer"})
    for tag, period, text in (("default", 3, "x"), ("override", 5, "longer")):
        _assert_values(_values(out, "missing", tag),
                       [float(b % period != 0) for b in range(BARS)], f"{scope}/{tag}/na")
        _assert_values(_values(out, "length", tag),
                       [len(text) if b % period == 0 else 0 for b in range(BARS)],
                       f"{scope}/{tag}/length")
        _assert_values(_values(out, "seen", tag),
                       [b // period + 1 for b in range(BARS)], f"{scope}/{tag}/seen")


def test_string_na_history_empty_and_persistent_controls(native):
    source = HEADER + """period = input.int(3, "Period")
payload = input.string("x", "Payload")
string tag = na
if bar_index % period == 0
    tag := payload
priorMissing = na(tag[1]) ? 1 : 0
priorLength = str.length(tag[1])
string empty = ""
emptyMissing = na(empty) ? 1 : 0
var string kept = na
varip string keptip = na
if bar_index == period
    kept := payload
    keptip := payload
keptMissing = na(kept) ? 1 : 0
keptipMissing = na(keptip) ? 1 : 0
"""
    out = native("string_history_persistence", _traced(source, "priorMissing",
                 "priorLength", "emptyMissing", "keptMissing", "keptipMissing"),
                 {"Period": 5, "Payload": "longer"})
    for tag, period, text in (("default", 3, "x"), ("override", 5, "longer")):
        _assert_values(_values(out, "priorMissing", tag),
                       [1, *[float(b % period != 0) for b in range(BARS - 1)]],
                       f"{tag}/string history na")
        _assert_values(_values(out, "priorLength", tag),
                       [0, *[len(text) if b % period == 0 else 0 for b in range(BARS - 1)]],
                       f"{tag}/string history length")
        _assert_values(_values(out, "emptyMissing", tag), [1] * BARS, f"{tag}/empty")
        for name in ("keptMissing", "keptipMissing"):
            _assert_values(_values(out, name, tag),
                           [float(b < period) for b in range(BARS)], f"{tag}/{name}")


@pytest.mark.parametrize("constructor", ("array.new<float>", "array.new_float"))
@pytest.mark.parametrize("binding", ("positional", "ordered", "reversed", "mixed"))
def test_array_constructor_arguments_bind_to_size_and_initial_value(native, constructor, binding):
    arguments = {
        "positional": "count, fill",
        "ordered": "size = count, initial_value = fill",
        "reversed": "initial_value = fill, size = count",
        "mixed": "count, initial_value = fill",
    }[binding]
    source = HEADER + f"""sizeInput = input.int(3, "Size")
seed = input.float(1.0, "Seed")
count = sizeInput + bar_index % 3
fill = seed + bar_index / 8.0
values = {constructor}({arguments})
n = values.size()
total = values.sum()
first = n > 0 ? values.get(0) : na
"""
    out = native(f"array_{constructor.replace('.', '_').replace('<', '_').replace('>', '')}_{binding}",
                 _traced(source, "n", "total", "first"), {"Size": 5, "Seed": -2.5})
    for tag, size, seed in (("default", 3, 1.0), ("override", 5, -2.5)):
        counts = [size + b % 3 for b in range(BARS)]
        fills = [seed + b / 8.0 for b in range(BARS)]
        for name, expected in (("n", counts), ("first", fills),
                               ("total", [n * v for n, v in zip(counts, fills)])):
            _assert_values(_values(out, name, tag), expected, f"{binding}/{tag}/{name}")


@pytest.mark.parametrize("binding", ("ordered", "reversed", "positional", "value_only"))
def test_array_constructor_evaluates_each_supplied_operand_once(native, binding):
    arguments = {
        "ordered": "size = sizeArg(count), initial_value = valueArg(fill)",
        "reversed": "initial_value = valueArg(fill), size = sizeArg(count)",
        "positional": "sizeArg(count), valueArg(fill)",
        "value_only": "initial_value = valueArg(fill)",
    }[binding]
    source = HEADER + f"""var events = array.new_int(2, 0)
sizeArg(int value) =>
    events.set(0, events.get(0) + 1)
    value
valueArg(float value) =>
    events.set(1, events.get(1) + 1)
    value
count = 1 + bar_index % 4
fill = close / 2
values = array.new<float>({arguments})
n = values.size()
total = values.sum()
sizes = events.get(0)
fills = events.get(1)
"""
    out = native(f"array_once_{binding}", _traced(source, "n", "total", "sizes", "fills"))
    counts = [0 if binding == "value_only" else 1 + b % 4 for b in range(BARS)]
    totals = ([math.nan] * BARS if binding == "value_only"
              else [n * _close(b) / 2 for b, n in enumerate(counts)])
    for name, expected in (
        ("n", counts), ("total", totals),
        ("sizes", [0 if binding == "value_only" else b + 1 for b in range(BARS)]),
        ("fills", [b + 1 for b in range(BARS)]),
    ):
        _assert_values(_values(out, name), expected, f"{binding}/{name}")


@pytest.mark.parametrize("constructor", ("array.new<float>", "array.new_float"))
def test_array_constructor_omitted_fill_keeps_element_na(native, constructor):
    source = HEADER + f"""count = 1 + bar_index % 4
values = {constructor}(size = count)
n = values.size()
firstMissing = n > 0 ? (na(values.get(0)) ? 1 : 0) : -1
"""
    out = native(f"array_default_{constructor}", _traced(source, "n", "firstMissing"))
    _assert_values(_values(out, "n"), [1 + b % 4 for b in range(BARS)], "size-only array")
    _assert_values(_values(out, "firstMissing"), [1] * BARS, "default float element na")


@pytest.mark.parametrize("family", ("int", "bool", "string", "color"))
def test_array_named_initializers_keep_their_element_family(native, family):
    fill, observe, oracle = {
        "int": ("bar_index + 7", "values.get(0)", [b + 7 for b in range(BARS)]),
        "bool": ("bar_index % 3 == 0", "values.get(0) ? 1 : 0",
                 [float(b % 3 == 0) for b in range(BARS)]),
        "string": ('bar_index % 2 == 0 ? "x" : "longer"', "str.length(values.get(0))",
                   [1 if b % 2 == 0 else 6 for b in range(BARS)]),
        "color": ("color.rgb(bar_index % 128, 33, 65)", "color.r(values.get(0))",
                  [b % 128 for b in range(BARS)]),
    }[family]
    source = HEADER + f"""values = array.new<{family}>(initial_value = {fill}, size = 1 + bar_index % 3)
n = values.size()
observed = n > 0 ? ({observe}) : na
"""
    out = native(f"array_family_{family}", _traced(source, "n", "observed"))
    _assert_values(_values(out, "n"), [1 + b % 3 for b in range(BARS)], f"{family}/size")
    _assert_values(_values(out, "observed"), oracle, f"{family}/fill")


INPUT_CALLS = {
    "float": 'input.float(2.0, "Operand")',
    "float_named": 'input.float(title = "Operand", defval = 2.0)',
    "generic": 'input(2.0, "Operand")',
    "int": 'input.int(2, "Operand")',
}


@pytest.mark.parametrize("spelling", tuple(INPUT_CALLS))
@pytest.mark.parametrize("mutation", ("double", "conditional"))
def test_reassigned_input_declarations_restart_from_the_selected_value(native, spelling, mutation):
    update = ("mult := mult * 2\n" if mutation == "double" else
              "if bar_index % 3 == 0\n    mult += bar_index + 1\n")
    source = HEADER + f"""mult = {INPUT_CALLS[spelling]}
before = mult
""" + update + """after = mult
"""
    selected = 5 if spelling == "int" else -3.5
    out = native(f"input_{spelling}_{mutation}", _traced(source, "before", "after"),
                 {"Operand": selected})
    for tag, value in (("default", 2.0), ("override", selected)):
        expected = ([2 * value] * BARS if mutation == "double" else
                    [value + b + 1 if b % 3 == 0 else value for b in range(BARS)])
        _assert_values(_values(out, "before", tag), [value] * BARS, f"{tag}/input at declaration")
        _assert_values(_values(out, "after", tag), expected, f"{tag}/input after assignment")
    _require(_values(out, "before") != _values(out, "before", "override"), "Ignored input override")


@pytest.mark.parametrize("family", ("bool", "string", "color"))
def test_non_numeric_input_declarations_reset_without_changing_their_transport(native, family):
    declarations = {
        "bool": ('input.bool(true, "Operand")',
                 "before = operand ? 1 : 0\noperand := not operand\nafter = operand ? 1 : 0\n"),
        "string": ('input.string("x", "Operand")',
                   'before = str.length(operand)\noperand := operand + (bar_index % 2 == 0 ? "a" : "bc")\nafter = str.length(operand)\n'),
        "color": ('input.color(#123456, "Operand")',
                  "before = color.r(operand)\ngreen = color.g(operand)\nblue = color.b(operand)\noperand := color.rgb((color.r(operand) + 17 + bar_index % 3) % 256, color.g(operand), color.b(operand))\nafter = color.r(operand)\ngreenAfter = color.g(operand)\nblueAfter = color.b(operand)\n"),
    }
    call, body = declarations[family]
    override = {"bool": False, "string": "longer", "color": str(0xFF426180)}[family]
    names = ["before", "after"]
    if family == "color":
        names += ["green", "blue", "greenAfter", "blueAfter"]
    out = native(f"input_family_{family}", _traced(HEADER + f"operand = {call}\n" + body,
                 *names), {"Operand": override})
    for tag in ("default", "override"):
        if family == "bool":
            value = tag == "default"
            expected = {"before": [float(value)] * BARS, "after": [float(not value)] * BARS}
        elif family == "string":
            size = 1 if tag == "default" else 6
            expected = {"before": [size] * BARS,
                        "after": [size + 1 + b % 2 for b in range(BARS)]}
        else:
            red, green, blue = (0x12, 0x34, 0x56) if tag == "default" else (0x42, 0x61, 0x80)
            expected = {
                "before": [red] * BARS,
                "after": [(red + 17 + b % 3) % 256 for b in range(BARS)],
                "green": [green] * BARS, "greenAfter": [green] * BARS,
                "blue": [blue] * BARS, "blueAfter": [blue] * BARS,
            }
        for name, values in expected.items():
            _assert_values(_values(out, name, tag), values, f"{family}/{tag}/{name}")


def test_input_history_retains_final_prior_values_and_immutable_input_control(native):
    source = HEADER + """operand = input.float(2.0, "Operand")
before = operand
operand += bar_index + 1
after = operand
prior = operand[1]
unchanged = input.float(7.0, "Unchanged")
immutablePrior = unchanged[1]
"""
    out = native("input_history", _traced(source, "before", "after", "prior", "immutablePrior"),
                 {"Operand": -3.5, "Unchanged": 11.25})
    for tag, value, unchanged in (("default", 2.0, 7.0), ("override", -3.5, 11.25)):
        after = [value + b + 1 for b in range(BARS)]
        for name, expected in (
            ("before", [value] * BARS), ("after", after),
            ("prior", [math.nan, *after[:-1]]),
            ("immutablePrior", [math.nan, *([unchanged] * (BARS - 1))]),
        ):
            _assert_values(_values(out, name, tag), expected, f"{tag}/{name}")


@pytest.mark.parametrize("mode", ("var", "varip"))
def test_persistent_input_declarations_keep_their_accumulated_state(native, mode):
    source = HEADER + f"""{mode} operand = input.float(2.0, "Operand")
before = operand
operand += 1 + bar_index % 3
after = operand
"""
    out = native(f"input_{mode}", _traced(source, "before", "after"), {"Operand": 5.5})
    for tag, value in (("default", 2.0), ("override", 5.5)):
        before, after = [], []
        current = value
        for b in range(BARS):
            before.append(current)
            current += 1 + b % 3
            after.append(current)
        _assert_values(_values(out, "before", tag), before, f"{mode}/{tag}/before")
        _assert_values(_values(out, "after", tag), after, f"{mode}/{tag}/after")


def test_plain_input_copy_and_lexical_shadow_remain_independent(native):
    source = HEADER + """seed = input.float(2.0, "Operand")
plain = seed
plain := plain * 2
simple float copied = plain
float localValue = 0.0
if bar_index % 2 == 0
    float seed = close
    seed += 1
    localValue := seed
"""
    out = native("input_copy_shadow", _traced(source, "seed", "plain", "copied", "localValue"),
                 {"Operand": 5.5})
    for tag, value in (("default", 2.0), ("override", 5.5)):
        for name, expected in (
            ("seed", [value] * BARS), ("plain", [2 * value] * BARS),
            ("copied", [2 * value] * BARS),
            ("localValue", [_close(b) + 1 if b % 2 == 0 else 0 for b in range(BARS)]),
        ):
            _assert_values(_values(out, name, tag), expected, f"{tag}/{name}")
