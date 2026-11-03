"""Located qualifier/variadic errors and complete-C++ controls."""

from __future__ import annotations

from textwrap import dedent

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.diagnostic_codes import render_diagnostic
from pineforge_codegen.errors import CompileError, Level, Phase
from tests._e2e import reference_codegen, transpile_json


CPP_REFERENCE = "c12c1877da48629b9c711f745a752ffc20ceef17"
HEADER = '//@version=6\nstrategy("Parameter qualifiers")\n'


def _pair(name, body, refused_text, accepted_text, token, *, at=None, code="PF-E2080"):
    source = HEADER + dedent(body).lstrip()
    assert refused_text in source
    control = source.replace(refused_text, accepted_text)
    statement = at or refused_text
    lines = source.splitlines()
    line = next(index for index, text in enumerate(lines, 1) if statement in text)
    column = lines[line - 1].rindex(token) + 1
    return pytest.param(source, control, code, (line, column), id=name)


_CASES = [
    _pair(
        "series_builtin_to_simple_function",
        """
        keep(simple int value) => value
        result = keep(time)
        """,
        "keep(time)", "keep(input.int(7))", "time",
    ),
    _pair(
        "series_to_simple_int_method",
        """
        type Counter
            int total = 0
        method record(Counter receiver, simple int amount) =>
            receiver.total := amount
        counter = Counter.new()
        counter.record(time)
        """,
        "counter.record(time)", "counter.record(input.int(7))", "time",
    ),
    _pair(
        "array_field_element_to_simple_function",
        """
        type Ledger
            array<int> values
        keep(simple int amount) => amount
        ledger = Ledger.new(array.new<int>(1, time))
        result = keep(ledger.values.get(0))
        """,
        "keep(ledger.values.get(0))", "keep(input.int(7))", "ledger",
    ),
    _pair(
        "array_field_element_to_simple_method",
        """
        type Ledger
            array<int> values
        method measure(Ledger receiver, simple int amount) =>
            amount + receiver.values.size()
        ledger = Ledger.new(array.new<int>(1, time))
        result = ledger.measure(ledger.values.get(0))
        """,
        "ledger.measure(ledger.values.get(0))",
        "ledger.measure(input.int(7))", "ledger",
    ),
    _pair(
        "derived_series_to_simple_array_helper",
        """
        first(simple int amount) =>
            values = array.from(amount)
            values.get(0)
        base = input.int(7)
        int shifted = base + bar_index
        result = first(shifted)
        """,
        "base + bar_index", "base + 1", "shifted", at="result = first(shifted)",
    ),
    _pair(
        "named_series_argument",
        """
        keep(simple int amount, int offset = 0) => amount + offset
        result = keep(offset=1, amount=time)
        """,
        "keep(offset=1, amount=time)", "keep(offset=1, amount=input.int(7))", "time",
    ),
    _pair(
        "named_method_series_argument",
        """
        type Counter
            int total = 0
        method record(Counter receiver, simple int amount, int offset = 0) =>
            receiver.total := amount + offset
        counter = Counter.new()
        counter.record(offset=1, amount=time)
        """,
        "counter.record(offset=1, amount=time)",
        "counter.record(offset=1, amount=input.int(7))", "time",
    ),
    _pair(
        "explicit_series_literal",
        """
        keep(simple int amount) => amount
        series int amount = 7
        result = keep(amount)
        """,
        "series int amount = 7", "simple int amount = 7", "amount",
        at="result = keep(amount)",
    ),
    _pair(
        "series_alias_reassignment",
        """
        keep(simple int amount) => amount
        int amount = 7
        amount := bar_index
        result = keep(amount)
        """,
        "amount := bar_index", "amount := 8", "amount", at="result = keep(amount)",
    ),
    _pair(
        "series_float_argument",
        """
        keep(simple float amount) => amount
        result = keep(close)
        """,
        "keep(close)", "keep(input.float(7.0))", "close",
    ),
    _pair(
        "series_bool_argument",
        """
        keep(simple bool enabled) => enabled
        result = keep(close > open)
        """,
        "keep(close > open)", "keep(input.bool(true))", "close",
    ),
    _pair(
        "series_string_argument",
        """
        keep(simple string label) => label
        result = keep(str.tostring(time))
        """,
        "keep(str.tostring(time))", 'keep(input.string("value"))', "str.tostring",
    ),
    _pair(
        "series_to_const_parameter",
        """
        keep(const int amount) => amount
        result = keep(time)
        """,
        "keep(time)", "keep(7)", "time",
    ),
    _pair(
        "series_helper_result",
        """
        echo(int amount) => amount
        keep(simple int amount) => amount
        result = keep(echo(time))
        """,
        "keep(echo(time))", "keep(echo(input.int(7)))", "echo",
    ),
    _pair(
        "forwarded_series_to_simple",
        """
        keep(simple int amount) => amount
        relay(int value) => keep(value)
        result = relay(time)
        """,
        "relay(time)", "relay(input.int(7))", "value",
        at="relay(int value) => keep(value)",
    ),
    _pair(
        "primitive_receiver_simple_parameter",
        """
        method offset(int receiver, simple int amount) => receiver + amount
        int anchor = 1
        result = anchor.offset(time)
        """,
        "anchor.offset(time)", "anchor.offset(input.int(7))", "time",
    ),
    _pair(
        "tuple_return_qualifier",
        """
        pair(int amount) => [amount, amount + 2]
        keep(simple int amount) => amount
        [first, second] = pair(time)
        result = keep(first)
        """,
        "pair(time)", "pair(input.int(7))", "first", at="result = keep(first)",
    ),
    _pair(
        "switch_result_qualifier",
        """
        choose(int amount) =>
            switch amount % 2
                0 => amount
                => amount + 1
        keep(simple int amount) => amount
        result = keep(choose(time))
        """,
        "keep(choose(time))", "keep(choose(input.int(7)))", "choose",
    ),
    _pair(
        "loop_tail_qualifier",
        """
        last(int amount) =>
            for offset = 1 to 3
                amount + offset
        keep(simple int amount) => amount
        result = keep(last(time))
        """,
        "keep(last(time))", "keep(last(input.int(7)))", "last",
    ),
    _pair(
        "declaration_tail_qualifier",
        """
        echo(int amount) =>
            int returned = amount
        keep(simple int amount) => amount
        result = keep(echo(time))
        """,
        "keep(echo(time))", "keep(echo(input.int(7)))", "echo",
    ),
    _pair(
        "assignment_tail_qualifier",
        """
        echo(int amount) =>
            int returned = 0
            if amount > 0
                returned := amount
            else
                returned := amount + 1
        keep(simple int amount) => amount
        result = keep(echo(time))
        """,
        "keep(echo(time))", "keep(echo(input.int(7)))", "echo",
    ),
    _pair(
        "request_payload_to_simple",
        """
        keep(simple float amount) => amount
        requested = request.security(syminfo.tickerid, "60", close)
        result = keep(requested)
        """,
        "keep(requested)", "keep(input.float(7.0))", "requested",
    ),
    _pair(
        "simple_call_inside_request",
        """
        keep(simple int amount) => amount
        requested = request.security(syminfo.tickerid, "60", keep(time))
        """,
        "keep(time)", "keep(input.int(7))", "time",
    ),
    _pair(
        "variadic_array_from_keyword",
        """
        values = array.from(arg0=7)
        result = values.get(0)
        """,
        "array.from(arg0=7)", "array.from(7)", "7", code="PF-E2082",
    ),
]


@pytest.mark.parametrize("source,control,code,position", _CASES)
def test_refusals_are_coded_and_located(source, control, code, position):
    with pytest.raises(CompileError) as caught:
        transpile(source, filename="strategy.pine")
    assert len(caught.value.diagnostics) == 1
    diagnostic = caught.value.diagnostics[0]
    assert diagnostic.code == code
    assert diagnostic.level is Level.ERROR
    assert diagnostic.phase is Phase.ANALYZER
    assert diagnostic.location.file == "strategy.pine"
    assert (diagnostic.location.line, diagnostic.location.col) == position
    assert render_diagnostic(code, diagnostic.args) == (
        diagnostic.message, diagnostic.hint,
    )


@pytest.fixture(scope="module")
def reference():
    tree = reference_codegen(CPP_REFERENCE)
    if tree is None:
        pytest.skip("The C++ reference commit is unavailable")
    return tree


def _assert_unchanged(source, reference, tmp_path):
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before
    assert transpile(source) == before["cpp"]


@pytest.mark.parametrize("source,control,code,position", _CASES)
def test_refused_sources_were_accepted_by_reference(
    source, control, code, position, reference, tmp_path,
):
    pine = tmp_path / "strategy.pine"
    pine.write_text(source, encoding="utf-8")
    before = transpile_json(pine, root=reference)
    assert before["ok"], before


@pytest.mark.parametrize("source,control,code,position", _CASES)
def test_paired_controls_keep_complete_cpp(source, control, code, position, reference, tmp_path):
    _assert_unchanged(control, reference, tmp_path)


@pytest.mark.parametrize("body", [
    "keep(simple int value) => value\nresult = keep(input.int(7))\n",
    "keep(simple int value) => value\nconst int fixed = 7\nresult = keep(fixed)\n",
    "keep(simple string value) => value\nresult = keep(syminfo.tickerid)\n",
    "keep(simple int value) => value\nsimple int fixed = 7\nresult = keep(fixed)\n",
    "keep(simple int value) => value\nresult = keep(dayofweek.monday)\n",
    "keep(simple float value) => value\nresult = keep(math.pi)\n",
    "keep(simple bool value) => value\nresult = keep(chart.is_standard)\n",
    "keep(series int value) => value\nresult = keep(time)\n",
    "keep(int value) => value\nresult = keep(time)\n",
    "keep(value) => value\nresult = keep(time)\n",
    "keep(series int value) => value\nsimple int fixed = 7\nresult = keep(fixed)\n",
    """
    type Ledger
        array<int> values
    ledger = Ledger.new(values=na)
    result = bar_index > 3 ? ledger.values.get(0) : 0
    """,
    """
    keep(simple int value) => value
    int amount = time
    echo(simple int amount) => keep(amount)
    result = echo(input.int(7))
    """,
    """
    keep(simple int value) => value
    int amount = 7
    if close > open
        int amount = time
    result = keep(amount)
    """,
    """
    method measure(array<int> receiver, simple int amount) => receiver.size() + amount
    method measure(array<float> receiver, int amount) => receiver.size() + amount
    values = array.from<float>(1)
    read() => values.measure(time)
    result = read()
    """,
    """
    method measure(array<int> receiver, simple int amount) => receiver.size() + amount
    method measure(array<float> receiver, int amount) => receiver.size() + amount
    values = array.from(1, 2.0)
    read() => values.measure(time)
    result = read()
    """,
])
def test_qualifier_controls_keep_complete_cpp(body, reference, tmp_path):
    _assert_unchanged(HEADER + dedent(body).lstrip(), reference, tmp_path)
