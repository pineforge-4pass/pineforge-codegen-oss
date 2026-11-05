"""Array constructor keywords preserve checked construction and evaluation."""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("Constructors")\n'


def assignment(cpp):
    return next(line.strip() for line in cpp.splitlines() if line.strip().startswith("values = "))


@pytest.mark.parametrize("ctor,value", [
    ("new<float>", "1.0"),
    ("new_float", "1.0"),
    ("new<int>", "7"),
    ("new_int", "7"),
    ("new<bool>", "true"),
    ("new_bool", "true"),
    ("new<string>", '"filled"'),
    ("new_string", '"filled"'),
])
@pytest.mark.parametrize("arguments", [
    "size = 3, initial_value = VALUE",
    "3, initial_value = VALUE",
    "initial_value = VALUE, size = 3",
])
def test_named_and_mixed_scalar_constructors_match_positional(ctor, value, arguments):
    # Before: keywords yielded an empty/default-filled vector; both slots now
    # reach the positional control's identical checked vector constructor.
    named = transpile(HEADER + f"values = array.{ctor}({arguments.replace('VALUE', value)})\n")
    positional = transpile(HEADER + f"values = array.{ctor}(3, {value})\n")
    assert assignment(named) == assignment(positional)
    assert '_PF_ARRAY_STOP("size_invalid", "new"' in assignment(named)


def test_reported_float_program_matches_its_positional_control():
    source = (
        "//@version=6\nstrategy('repro')\n"
        "values = array.new<float>(size = 3, initial_value = 1.0)\n"
        "n = values.size()\ntotal = values.sum()\n"
        "// @pf-trace n=n\n// @pf-trace total=total\nplot(total)\n"
    )
    # Before: values was empty; the control's checked vector(3, 1.0) has
    # size 3 and sum 3, and now the reporter's constructor is the same.
    assert assignment(transpile(source)) == assignment(transpile(
        source.replace("size = 3, initial_value = 1.0", "3, 1.0")))


FAMILIES = [
    ("new<float>", "1.0", "values.first() == 1.0"),
    ("new_float", "1.0", "values.first() == 1.0"),
    ("new<int>", "7", "values.first() == 7"),
    ("new_int", "7", "values.first() == 7"),
    ("new<bool>", "true", "values.first()"),
    ("new_bool", "true", "values.first()"),
    ("new<string>", '"filled"', 'values.first() == "filled"'),
    ("new_string", '"filled"', 'values.first() == "filled"'),
    ("new<color>", "color.red", "values.first() == color.red"),
    ("new<line>", "na", "na(values.first())"),
    ("new_line", "na", "na(values.first())"),
    ("new<box>", "na", "na(values.first())"),
    ("new_box", "na", "na(values.first())"),
    ("new<label>", "na", "na(values.first())"),
    ("new_label", "na", "na(values.first())"),
    ("new<linefill>", "na", "na(values.first())"),
    ("new_linefill", "na", "na(values.first())"),
]


@pytest.mark.parametrize("ctor,value,observer", FAMILIES)
def test_each_element_family_constructs_three_filled_elements(ctor, value, observer):
    source = HEADER + (
        f"values = array.{ctor}(size = 3, initial_value = {value})\n"
        "count = values.size()\n"
        f"filled = {observer}\n"
    )
    # Before: count was 0 and first() stopped; vector(3, value) now gives
    # count 3 and the supplied numeric/string/bool/color/na-handle value.
    output = run_emitted_tu(transpile(source), r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    std::cout << strategy.last_error() << "\n"
              << strategy.count << " " << strategy.filled << "\n";
}
""", opt="-O0", label="named_array_family")
    assert output.splitlines() == ["", "3 1"]


@pytest.mark.parametrize("arguments,expected", [
    ("", "std::vector<double>()"),
    ("initial_value = 8.0", "((0), (8.0))"),
])
def test_omitted_size_remains_zero(arguments, expected):
    # Empty call is an unchanged pin; initial_value alone now reaches the
    # checked constructor with default size 0 instead of dropping the value.
    line = assignment(transpile(HEADER + f"values = array.new<float>({arguments})\n"))
    assert expected in line


CONSTRUCTORS = [
    "new<float>", "new_float", "new<int>", "new_int",
    "new<bool>", "new_bool", "new<string>", "new_string",
    "new<color>", "new<line>", "new_line", "new<box>", "new_box",
    "new<label>", "new_label", "new<linefill>", "new_linefill",
]


@pytest.mark.parametrize("ctor", CONSTRUCTORS)
@pytest.mark.parametrize("arguments,code,reason,argument", [
    ("size = 3, bogus = 1", "PF-E2027", "unknown keyword argument 'bogus'", "1"),
    ("3, size = 4", "PF-E2024", "argument 'size' passed both positionally and by keyword", "4"),
    ("2, na, initial_value = na", "PF-E2024",
     "argument 'initial_value' passed both positionally and by keyword", "na"),
    ("2, na, 9", "PF-E2026", "too many positional arguments", "9"),
])
def test_constructor_refuses_invalid_bindings_at_the_argument(ctor, arguments, code, reason, argument):
    # Base silently ignored unknown/duplicate/extra slots.
    # These are binding errors, before fill typing.
    line = f"values = array.{ctor}({arguments})"
    with pytest.raises(CompileError) as raised:
        transpile(HEADER + line + "\n")
    diagnostic = raised.value.diagnostics[0]
    assert diagnostic.code == code
    assert reason in diagnostic.message
    assert (diagnostic.location.line, diagnostic.location.col) == (
        3, line.rindex(argument) + 1)


def test_repeated_constructor_keyword_keeps_its_parser_diagnostic():
    line = "values = array.new<float>(size = 2, size = 3)"
    with pytest.raises(CompileError) as raised:
        transpile(HEADER + line + "\n")
    diagnostic = raised.value.diagnostics[0]
    assert diagnostic.code == "PF-E0035"
    assert (diagnostic.location.line, diagnostic.location.col) == (3, line.rindex("size") + 1)


def test_generic_bool_na_keeps_its_existing_refusal():
    with pytest.raises(CompileError) as raised:
        transpile(HEADER + "values = array.new<bool>(size = 2, initial_value = na)\n")
    assert any(d.code == "PF-E2044" for d in raised.value.diagnostics)


@pytest.mark.parametrize("ctor", ["new<float>", "new_float", "new<int>", "new_int"])
@pytest.mark.parametrize("arguments", [
    "size = next_size(), initial_value = next_value()",
    "initial_value = next_value(), size = next_size()",
    "next_size(), initial_value = next_value()",
])
def test_emitted_constructor_evaluates_arguments_once_in_parameter_order(ctor, arguments):
    # Unlike a positional-only order assertion, the reversed form is red
    # on both the keyword-dropping base and the written-order implementation.
    cpp = transpile(HEADER + f"""
events = array.new<int>()
next_size() =>
    events.push(1)
    3
next_value() =>
    events.push(2)
    events.size()
values = array.{ctor}({arguments})
""")
    line = assignment(cpp)
    size_calls = list(re.finditer(r"\bnext_size(?:_cs\d+)?\(", line))
    value_calls = list(re.finditer(r"\bnext_value(?:_cs\d+)?\(", line))
    assert len(size_calls) == len(value_calls) == 1, line
    assert size_calls[0].start() < value_calls[0].start(), line
    # The two calls are separate statements, so the C++ operand rules
    # cannot reverse them.
    assert ";" in line[size_calls[0].end():value_calls[0].start()], line


@pytest.mark.parametrize("ctor", ["new<float>", "new_float"])
@pytest.mark.parametrize("arguments,order,first", [
    ("size = next_size(), initial_value = next_value()", "12", "12"),
    ("next_size(), initial_value = next_value()", "12", "12"),
    ("initial_value = next_value(), size = next_size()", "12", "12"),
])
def test_named_arguments_run_once_in_parameter_order(ctor, arguments, order, first):
    source = HEADER + f"""
events = array.new<int>()
next_size() =>
    events.push(1)
    3
next_value() =>
    events.push(2)
    events.size() == 1 ? 2.0 : 12.0
values = array.{ctor}({arguments})
count = values.size()
first = values.first()
"""
    # TradingView's reversed-keyword tape records size first, then fill:
    # two effects, log 12, first element 12.
    output = run_emitted_tu(transpile(source), r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    std::cout << strategy.last_error() << "\n";
    for (auto event : strategy.events) std::cout << event;
    std::cout << " " << strategy.count << " " << strategy.first << "\n";
}
""", opt="-O0", label="named_array_argument_order")
    assert output.splitlines() == ["", f"{order} 3 {first}"]


def test_initial_value_is_evaluated_once_even_with_default_zero_size():
    source = HEADER + """
events = array.new<int>()
next_value() =>
    events.push(2)
    9.0
values = array.new<float>(initial_value = next_value())
count = values.size()
"""
    # Before: next_value was dropped; zero default size now still evaluates
    # the supplied value once, appending 2 while leaving the result empty.
    output = run_emitted_tu(transpile(source), r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    std::cout << strategy.last_error() << "\n";
    for (auto event : strategy.events) std::cout << event;
    std::cout << " " << strategy.count << "\n";
}
""", opt="-O0", label="named_array_zero_size")
    assert output.splitlines() == ["", "2 0"]


def test_named_constructor_reads_input_overrides():
    source = HEADER + """
values = array.new<float>(size = input.int(2, "Size"), initial_value = input.float(1.0, "Value"))
count = values.size()
total = values.sum()
"""
    # Before: both input expressions were dropped; the checked constructor
    # now receives overridden size 4 and value 2.5, hence sum 10.
    output = run_emitted_tu(transpile(source), r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    strategy_set_input(&strategy, "Size", "4");
    strategy_set_input(&strategy, "Value", "2.5");
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    std::cout << strategy.last_error() << "\n"
              << strategy.count << " " << strategy.total << "\n";
}
""", opt="-O0", label="named_array_input_overrides")
    assert output.splitlines() == ["", "4 10"]


def test_named_user_type_constructor_keeps_its_element_type():
    source = HEADER + """
type Point
    float x
values = array.new<Point>(size = 3, initial_value = Point.new(4.0))
count = values.size()
element = values.first()
coordinate = element.x
"""
    # Before: the vector was empty and first() stopped; the supplied Point
    # now fills all three slots and the observed coordinate is 4.
    output = run_emitted_tu(transpile(source), r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    std::cout << strategy.last_error() << "\n"
              << strategy.count << " " << strategy.coordinate << "\n";
}
""", opt="-O0", label="named_array_user_type")
    assert output.splitlines() == ["", "3 4"]


@pytest.mark.parametrize("size", ["-1", "int(na)"])
@pytest.mark.parametrize("arguments", [
    "SIZE, 1.0", "size = SIZE, initial_value = 1.0",
])
def test_constructor_keeps_existing_negative_and_na_size_guards(size, arguments):
    # Negative sizes still stop. Int-na stopping is a known approximation,
    # deliberately preserved here; TradingView completes an empty array.
    output = run_emitted_tu(transpile(
        HEADER + f"values = array.new<float>({arguments.replace('SIZE', size)})\n"
    ), r"""
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    std::cout << strategy.last_error() << "\n";
}
""", opt="-O0", label="named_array_invalid_size")
    assert output.strip() == "cannot create std::vector larger than max_size()"


@pytest.mark.parametrize("scope", ["member", "local", "parameter"])
@pytest.mark.parametrize("spelling", [
    "_pf_array_new_size_arg", "_pf_array_new_initial_value_arg",
])
def test_constructor_argument_temporaries_do_not_capture_user_names(scope, spelling):
    # The earlier staging used these legal Pine spellings as C++ locals,
    # sometimes self-initializing the size or shadowing the fill.
    declarations = (
        f"int {spelling} = input.int(3, \"Size\")\n"
        "float fill = input.float(2.0, \"Fill\")\n"
    )
    body = (
        f"    values = array.new<float>(size = {spelling}, initial_value = fill + {spelling})\n"
        f"    values.size() == {spelling} and values.sum() == {spelling} * (fill + {spelling})\n"
    )
    if scope == "member":
        source = declarations + body.replace("    ", "").replace(
            f"values.size() == {spelling}", f"valid = values.size() == {spelling}")
    elif scope == "local":
        source = (
            "n = input.int(3, \"Size\")\nfill = input.float(2.0, \"Fill\")\n"
            f"make(int n, float fill) =>\n    int {spelling} = n\n{body}"
            "valid = make(n, fill)\n"
        )
    else:
        source = (
            "n = input.int(3, \"Size\")\nfill = input.float(2.0, \"Fill\")\n"
            f"make(int {spelling}, float fill) =>\n{body}"
            "valid = make(n, fill)\n"
        )
    cpp = transpile(HEADER + source)
    assert f"auto {spelling} = " not in cpp
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
""", opt="-O0", label="named_array_argument_scope")
    assert output.splitlines() == ["", "1"]


_REFERENCE_SPELLING = {
    "m = matrix.new<float>(rows = 2, columns = 3, initial_value = close)\n":
        "m = matrix.new<float>(2, 3, close)\n",
}


@pytest.mark.parametrize("body", [
    "values = array.new<float>()\n",
    "values = array.new<float>(3)\n",
    "values = array.new<float>(3, close)\n",
    "values = array.new_int(3, time)\n",
    "values = array.new_string(2, str.tostring(close))\n",
    "values = array.from(time, time + 1)\nb = array.copy(values)\nc = b.slice(0, 1)\n",
    "values = array.from(1.0, close)\nvalues.set(index = 0, value = 7.0)\nvalues.fill(3.0, index_to = 1)\n",
    "m = matrix.new<float>(2, 3, close)\nm.sort(order = order.descending)\n",
    "m = matrix.new<float>(rows = 2, columns = 3, initial_value = close)\n",
    "f() =>\n    matrix.new<float>(2, 3, close)\nm = f()\n",
    "values = array.from(year(time), hour(time = time, timezone = \"UTC\"))\n",
    "strategy.entry(\"L\", strategy.long, qty = 1)\nstrategy.order(\"O\", strategy.short, qty = 2)\n",
    "strategy.exit(\"X\", \"L\", stop = close)\nstrategy.close(\"L\", immediately = true)\nstrategy.close_all()\n",
    "strategy.cancel(\"L\")\nstrategy.cancel_all()\nx = strategy.convert_to_account(close)\n",
])
def test_unrelated_lowerings_keep_cpp_diagnostics_and_manifests(tmp_path, body):
    from tests._e2e import reference_codegen, transpile_json

    reference = reference_codegen("3ec62ae589f5b777122d03f1e7a706e7cda67ba8")
    assert reference is not None, "the source-preservation baseline must be available"
    pine = tmp_path / "strategy.pine"
    # The reference read the keywords of matrix.new by position, as a call with
    # no counts (a 0 by 0 matrix): that keyword spelling is held to what the
    # reference emitted for the positional one, which the keywords now reach.
    pine.write_text(HEADER + _REFERENCE_SPELLING.get(body, body))
    previous = transpile_json(pine, reference)
    pine.write_text(HEADER + body)
    current = transpile_json(pine)
    assert previous["ok"] and current["ok"], (previous, current)
    assert current == previous
