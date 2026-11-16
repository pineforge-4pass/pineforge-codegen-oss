"""Translating a request reads its value type without a parameter overlay."""

import pineforge_codegen
from pineforge_codegen.symbols import TypeSpec


def _security_calls(source: str):
    _gen, ctx, cpp, _support, _sites = pineforge_codegen._generate(
        source, True, "request-result-type.pine", {}
    )
    assert "GeneratedStrategy" in cpp
    return ctx.security_calls


def test_requested_scalar_has_no_structured_result_type():
    calls = _security_calls('''//@version=6
strategy("requested scalar")
level = request.security(syminfo.tickerid, "60", close)
plot(level)
''')
    # A scalar's PineType already describes it; only records and
    # collections carry a structured result type.
    assert [call.result_type_spec for call in calls] == [None]


def test_requested_array_keeps_its_element_type():
    calls = _security_calls('''//@version=6
strategy("requested array")
values = request.security(syminfo.tickerid, "60", array.from(close, open))
plot(values.size())
''')
    assert [call.result_type_spec for call in calls] == [
        TypeSpec.array(TypeSpec.primitive("float")),
    ]


def test_requested_tuple_keeps_its_array_element_type():
    calls = _security_calls('''//@version=6
strategy("requested tuple")
[values, level] = request.security(syminfo.tickerid, "60", [array.from(close), close])
plot(level + values.size())
''')
    assert len(calls) == 1
    assert len(calls[0].tuple_element_specs) == 2
    assert calls[0].tuple_element_specs[0] == TypeSpec.array(
        TypeSpec.primitive("float"),
    )
