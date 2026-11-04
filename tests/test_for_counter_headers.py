"""Native compile regressions and reference C++ for loop headers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import pytest

from pineforge_codegen import transpile
from tests import _compile as compile_env
from tests._e2e import build_strategy_library
from tests._for_counter_header_cases import (
    BASELINE_SOURCES, INT_START_SOURCES, MAIN_COMMIT,
)


FIXTURES = Path(__file__).parent / "fixtures" / "for_counter_headers"
BASELINE = FIXTURES.parent / "for_counter_header_baseline.json"
FIXTURE_SCRIPTS = (
    "open_counter_header_end",
    "open_counter_header_step",
    "end_reads_counter",
    "end_reads_counter_other",
    "step_reads_counter",
    "step_reads_counter_descending",
)
COUNTER_TYPE_SCRIPTS = (
    ("float_counter_float_step", "double"),
    ("float_counter_no_step", "double"),
    ("float_counter_unit_step", "double"),
    ("int_counter_float_end", "int"),
    ("fstep_a_param_start", "double"),
    ("fstep_b_body_local_start", "double"),
    ("fstep_c_array_bounds", "double"),
)


def _loop_source(body: str, context: str) -> str:
    source = '//@version=6\nstrategy("Loop counter type", currency=currency.NONE)\n'
    if context == "security":
        return (
            source + "total() =>\n"
            + "".join(f"    {line}\n" for line in body.splitlines())
            + "    acc\n"
            + "plot(request.security(syminfo.tickerid, timeframe.period, total()))\n"
        )
    return source + body + "\nplot(acc)\n"


def _counter_pattern(context: str, counter: str) -> str:
    return counter if context == "chart" else rf"\w+_\d+_{counter}"


@pytest.mark.parametrize("name,cpp_type", COUNTER_TYPE_SCRIPTS)
def test_tradingview_counter_types(name, cpp_type):
    # Positive float starts, or unknown starts with a float step, use double.
    pine = (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")
    counter = re.findall(r"(?m)^\s*for (\w+) =", pine)[-1]
    cpp = transpile(pine)
    loop = re.search(
        rf"(?ms)^\s*{cpp_type} _for_start_\d+ = .*?"
        rf"^\s*for \({cpp_type} {counter} = .*?^\s*\}}",
        cpp,
    )
    assert loop is not None, cpp
    if name == "float_counter_float_step":
        assert re.search(r"double _for_step_\d+ = \(0\.25\);", loop.group())


def _assert_counter_type_follows_start_type(
    context, counter_bound, prefix, start, cpp_type,
):
    end = "(walkIndex >= 0 ? 1.0 : 1.0)" if counter_bound else "1.0"
    step = "0.25" if cpp_type == "double" else "1"
    body = (
        "float acc = 0.0\n"
        f"for walkIndex = {start} to {end} by {step}\n"
        "    acc += walkIndex\n"
    )
    pine = _loop_source(body, context)
    declaration = pine.index("float acc" if context == "chart" else "total()")
    pine = pine[:declaration] + prefix + pine[declaration:]
    cpp = transpile(pine)
    counter = _counter_pattern(context, "walkIndex")
    assert re.search(rf"\b{cpp_type} {counter} = ", cpp), cpp
    if cpp_type == "double":
        loop = re.search(
            rf"(?ms)^\s*double \w+(?:_start_\d+|_start) = .*?"
            rf"\bdouble {counter} = .*?^\s*\}}",
            cpp,
        )
        assert loop is not None, cpp
        assert re.search(r"\bdouble \w+(?:_end_\d+|_end) = ", loop.group())
        assert re.search(r"\bdouble \w+(?:_step_\d+|_step) = \(0\.25\);", loop.group())
        assert "_pf_v" not in loop.group()
        end_var = re.search(r"\bdouble (\w+(?:_end_\d+|_end)) = ", loop.group())[1]
        assert re.search(rf", {end_var} = .*?\) \{{", loop.group())


@pytest.mark.parametrize("context", ["chart", "security"])
@pytest.mark.parametrize("counter_bound", [False, True])
@pytest.mark.parametrize("prefix,start,cpp_type", [
    ("", "0.0", "double"),
    ("first = input.float(0.0)\n", "first", "double"),
    ("float first = 0\n", "first", "double"),
    ("", "float(0)", "double"),
    ("", "(0.0 + 0)", "double"),
    ("", "(0 + 0)", "int"),
    ("", "-0", "int"),
    ("", "(true ? 0 : 0)", "int"),
    ("", "(true ? 0.0 : 0)", "double"),
    ("", "(true ? na : 0.0)", "double"),
    ("", "math.max(0, 0.0)", "double"),
    ("", "math.abs(-0.5)", "double"),
    ("", "math.round(0, 2)", "double"),
    ("", "math.sqrt(0)", "double"),
])
def test_counter_type_follows_start_type(
    context, counter_bound, prefix, start, cpp_type,
):
    _assert_counter_type_follows_start_type(
        context, counter_bound, prefix, start, cpp_type,
    )


@pytest.mark.parametrize("name,pine", sorted(INT_START_SOURCES.items()))
def test_int_or_unresolved_start_keeps_int_header_slots(name, pine):
    cpp = transpile(pine)
    counter = _counter_pattern(name.split("_", 1)[0], "walkIndex")
    assert re.search(rf"\bfor \(int {counter} = ", cpp), cpp
    assert not re.search(rf"\bdouble {counter} = ", cpp), cpp
    for slot in ("start", "end", "step"):
        assert re.search(rf"\bint \w+(?:_{slot}_\d+|_{slot}) = ", cpp), cpp


# The chart route retains v5's int counter even for a float start.
# A v5 library function in a request.security payload is already refused,
# so valid source cannot reach that route's v5 counter-type gate.
# This preserves emission without a new TradingView value claim.
@pytest.mark.parametrize("context", ["chart"])
@pytest.mark.parametrize("end", ["1.0", "last"])
def test_v5_library_float_start_keeps_int_counter(context, end):
    library = (
        '//@version=5\nlibrary("CounterTypes")\n'
        "export measure() =>\n"
        "    float last = 1.0\n"
        "    float acc = 0.0\n"
        f"    for walkIndex = 0.0 to {end} by 1\n"
        "        acc += 1.0\n"
        "    acc\n"
    )
    call = "C.measure()"
    if context == "security":
        call = f"request.security(syminfo.tickerid, timeframe.period, {call})"
    pine = (
        '//@version=6\nstrategy("Library counter type", currency=currency.NONE)\n'
        "import pftest/CounterTypes/1 as C\n"
        f"plot({call})\n"
    )
    cpp = transpile(pine, libraries={"pftest/CounterTypes/1": library})
    counter = r"\w*walkIndex"
    if context == "security":
        counter = rf"\w+_\d+_{counter}"
    assert re.search(rf"\bint {counter} = ", cpp), cpp
    assert not re.search(rf"\bdouble {counter} = ", cpp), cpp


@pytest.mark.parametrize("context", ["chart", "security"])
@pytest.mark.parametrize("end,reads_counter", [
    pytest.param("(walkIndex == 2 ? 3 : 1)", True, id="own_counter"),
    pytest.param("(math.max(walkIndex, 0) == 2 ? 3 : 1)", True,
                 id="nested_counter_argument"),
    pytest.param("3", False, id="counter_free_end"),
    pytest.param("limit", False, id="outer_binding_end"),
])
def test_first_end_reread_only_for_this_counter(context, end, reads_counter):
    # The step uses the counter in every row, so even the counter-free ends
    # exercise the shared counter-bound emitter. Only an end read resolved
    # to this loop's declaration may add an assignment before the first test.
    pine = _loop_source(
        "int limit = 3\nfloat acc = 1.1\n"
        f"for walkIndex = 2 to {end} by (walkIndex == 0 ? 1 : 2)\n"
        "    acc := acc + walkIndex + 1",
        context,
    )
    cpp = transpile(pine)
    direction = re.search(
        r"const bool \w+ = \((?P<start>\w+) > (?P<end>\w+)\);\n"
        r"\s*\w+ = (?P=start);\n",
        cpp,
    )
    assert direction is not None, cpp
    first_test = cpp.index("for (;", direction.end())
    before_test = cpp[direction.end():first_test]
    if reads_counter:
        assert re.fullmatch(rf"\s*{direction['end']} = [^\n]+;\n\s*", before_test)
    else:
        # Byte-for-byte adjacency: no new end read for a counter-free end.
        assert before_test.strip() == ""


def test_first_end_reread_includes_user_helper_argument():
    pine = (
        '//@version=6\nstrategy("Helper end read", currency=currency.NONE)\n'
        "bound(int counterValue) => counterValue == 2 ? 3 : 1\n"
        "float acc = 1.1\n"
        "for walkIndex = 2 to bound(walkIndex)\n"
        "    acc := acc + walkIndex + 1\nplot(acc)\n"
    )
    cpp = transpile(pine)
    assert re.search(
        r"walkIndex = _for_start_\d+;\n\s*_for_end_\d+ = [^\n]+;\n\s*for \(;",
        cpp,
    ), cpp


@pytest.mark.parametrize("context", ["chart", "security"])
@pytest.mark.parametrize("start", ["0.0", "float(0)", "math.sqrt(0)"])
def test_inferred_helper_local_float_start_uses_its_initializer(context, start):
    call = "measure()"
    if context == "security":
        call = f"request.security(syminfo.tickerid, timeframe.period, {call})"
    pine = (
        '//@version=6\nstrategy("Inferred loop start", currency=currency.NONE)\n'
        "measure() =>\n"
        f"    first = {start}\n"
        "    float acc = 0.0\n"
        "    for walkIndex = first to 1.0 by 0.25\n"
        "        acc += 1.0\n"
        "    acc\n"
        f"plot({call})\n"
    )
    cpp = transpile(pine)
    counter = _counter_pattern(context, "walkIndex")
    assert re.search(rf"\bdouble {counter} = ", cpp), cpp


# A request.security helper loop cannot hold a user function call (an
# existing limit of that route), so a start that calls a user function is
# checked on the chart route only.
@pytest.mark.parametrize("counter_bound", [False, True])
def test_user_function_float_start_gives_a_float_counter(counter_bound):
    _assert_counter_type_follows_start_type(
        "chart", counter_bound, "first() => 0.0\n", "first()", "double",
    )


@pytest.mark.parametrize("context", ["chart", "security"])
@pytest.mark.parametrize("counter_bound", [False, True])
@pytest.mark.parametrize("step", ["0.25", "0.0", "-0.25"])
def test_float_step_is_checked_once_without_narrowing(
    context, counter_bound, step,
):
    end = "(walkIndex >= 0 ? 1.0 : 1.0)" if counter_bound else "1.0"
    pine = _loop_source(
        "float acc = 0.0\n"
        f"for walkIndex = 0.0 to {end} by {step}\n"
        "    acc += 1.0\n",
        context,
    )
    cpp = transpile(pine)
    step_read = re.search(
        r"(?m)^\s*double (?P<step>\w+(?:_step_\d+|_step)) = (?P<value>.*);\n"
        r"\s*if \(!is_na\((?P=step)\) && (?P=step) <= 0\) "
        r"""pine_runtime_error\("'step' in loop must be greater than zero"\);""",
        cpp,
    )
    assert step_read is not None, cpp
    assert "_pf_v" not in step_read["value"]
    assert cpp.count(f"double {step_read['step']} = ") == 1
    if step == "0.25":
        assert step_read["value"] == "(0.25)"
    assert re.search(
        rf"\+= \(\w+ \? -{step_read['step']} : {step_read['step']}\)", cpp,
    )


@pytest.mark.parametrize("context", ["chart", "security"])
@pytest.mark.parametrize("counter_bound", [False, True])
@pytest.mark.parametrize("pine_type,cpp_type", [("float", "double"), ("int", "int")])
def test_counter_start_uses_declared_helper_parameter_type(
    context, counter_bound, pine_type, cpp_type,
):
    end = "(walkIndex >= 0 ? 1.0 : 1.0)" if counter_bound else "1.0"
    step = "0.25" if pine_type == "float" else "1"
    call = "total(0)"
    if context == "security":
        call = f"request.security(syminfo.tickerid, timeframe.period, {call})"
    pine = (
        '//@version=6\nstrategy("Loop start parameter", currency=currency.NONE)\n'
        f"total({pine_type} first) =>\n"
        "    float acc = 0.0\n"
        f"    for walkIndex = first to {end} by {step}\n"
        "        acc += walkIndex\n"
        "    acc\n"
        f"plot({call})\n"
    )
    cpp = transpile(pine)
    counter = _counter_pattern(context, "walkIndex")
    assert re.search(rf"\b{cpp_type} {counter} = ", cpp), cpp


@pytest.mark.parametrize("name", FIXTURE_SCRIPTS)
def test_fixture_scripts_compile_natively(name, tmp_path):
    compile_env.skip_if_no_engine_lib()
    pine = (FIXTURES / f"{name}.pine").read_text(encoding="utf-8")
    cpp = transpile(pine)
    build_strategy_library(cpp, tmp_path)


@pytest.fixture(scope="module")
def reference_cpp_hashes():
    assert BASELINE.is_file(), (
        f"Missing counted-loop C++ hashes: {BASELINE}. "
        "BASELINE_SOURCES must correspond to c1cc532b."
    )
    document = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert document["schema"] == 1
    assert document["revision"] == MAIN_COMMIT
    assert set(document["sources"]) == set(BASELINE_SOURCES), (
        "The baseline inventory must match c1cc532b."
    )
    return document["sources"]


@pytest.mark.parametrize("name", sorted(BASELINE_SOURCES))
def test_existing_loop_cpp_matches_reference_except_step_stop(
    name, reference_cpp_hashes
):
    pine = BASELINE_SOURCES[name]
    baseline = reference_cpp_hashes[name]
    assert hashlib.sha256(pine.encode("utf-8")).hexdigest() == baseline["source_sha256"], (
        f"{name}: the source inventory differs from its reference"
    )
    cpp = transpile(pine)
    # RE10021 requires a guard even for a dynamic step that happens to be
    # positive. Exclude only the added run-stop statement; all other bytes
    # still match the reference output.
    cpp = re.sub(
        r"(?m)^ *if \(!is_na\((_for_step_\d+)\) && \1 <= 0\) "
        r"""pine_runtime_error\("'step' in loop must be greater than zero"\);\n""",
        "",
        cpp,
    )
    assert hashlib.sha256(cpp.encode("utf-8")).hexdigest() == baseline["cpp_sha256"], (
        f"{name}: the complete C++ changed from reference {MAIN_COMMIT}"
    )


def _counted_loop_header(pine: str) -> str:
    cpp = transpile('//@version=6\nstrategy("Loop bound na", currency=currency.NONE)\n' + pine)
    return next(line for line in cpp.splitlines() if re.match(r"\s*for \(int i = ", line))


# An integer loop bound can hold the integer na sentinel (INT_MIN) even when the slot
# coercion leaves its text as written; the header then needs the same na guard as a
# double-typed bound, or the loop counts from its start down to INT_MIN.
@pytest.mark.parametrize("pine", [
    "float limit = 2.0 + bar_index % 2\nint trips = 0\nfor i = 1 to int((limit + 0.0)[1])\n    trips += 1\nplot(trips)",
    "int trips = 0\nfor i = 1 to int(na)\n    trips += 1\nplot(trips)",
    "var int bound = na\nint trips = 0\nfor i = 1 to bound\n    trips += 1\nplot(trips)",
    "var int first = na\nint trips = 0\nfor i = first to 3\n    trips += 1\nplot(trips)",
    "var int stride = na\nint trips = 0\nfor i = 1 to 5 by stride\n    trips += 1\nplot(trips)",
], ids=["history-cast-end", "na-literal-cast-end", "int-na-end", "int-na-start", "int-na-step"])
def test_integer_bound_that_may_be_na_keeps_the_loop_guard(pine):
    header = _counted_loop_header(pine)
    for slot in ("start", "end", "step"):
        assert re.search(rf"!is_na\(_for_{slot}_\d+\)", header), header


@pytest.mark.parametrize("pine", [
    "int trips = 0\nfor i = 1 to 3\n    trips += 1\nplot(trips)",
    "int trips = 0\nfor i = 1 to 10 by 2\n    trips += 1\nplot(trips)",
], ids=["literal-bounds", "literal-step"])
def test_integer_bounds_proven_present_stay_unguarded(pine):
    assert "is_na(" not in _counted_loop_header(pine)
