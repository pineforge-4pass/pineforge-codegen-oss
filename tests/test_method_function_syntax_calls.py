"""A typed user method called in function syntax is the same call as in member syntax.

``pair(holder, x)`` and ``pair(self=holder, value=x)`` call the typed method ``pair`` of the
receiver ``holder``, exactly as ``holder.pair(x)`` does: the receiver is the first positional
argument, or the argument named like the method's first parameter. A tuple declaration in
function syntax used to be emitted as the comment ``/* unsupported tuple assignment */`` (the
destructured names kept their zero), and the same call in an expression was refused as an
unknown function.

The emission rows pin that the function-syntax call is the member-syntax call (equal C++); the
native rows pin the values the built strategy computes.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("method function syntax")\n'

TUPLE_SOURCE = HEADER + '''
type Holder
    float offset
method pair(Holder {receiver}, float value) => [value, value + {receiver}.offset]
Holder holder = Holder.new(7.0)
float x = 100.0 + 13.0 * bar_index
[_, g] = {call}
method readGlobal(Holder self) => g + self.offset
float observed = holder.readGlobal()
'''

SCALAR_SOURCE = HEADER + '''
type Holder
    float offset
method shifted(Holder {receiver}, float value) => value + {receiver}.offset
Holder holder = Holder.new(7.0)
float x = 100.0 + 13.0 * bar_index
float y = {call}
'''

# (call in function syntax, the same call in member syntax)
TUPLE_CALLS = [
    ("pair(self=holder, value=x)", "holder.pair(value=x)"),
    ("pair(holder, x)", "holder.pair(x)"),
    ("pair(holder, value=x)", "holder.pair(value=x)"),
    ("pair(value=x, self=holder)", "holder.pair(value=x)"),
]
# (call in function syntax, the same call in member syntax, scale, add): the value is
# scale * (x + offset) + add
SCALAR_CALLS = [
    ("shifted(self=holder, value=x)", "holder.shifted(value=x)", 1.0, 0.0),
    ("shifted(holder, x)", "holder.shifted(x)", 1.0, 0.0),
    ("2.0 * shifted(holder, value=x) + 1.0", "2.0 * holder.shifted(value=x) + 1.0", 2.0, 1.0),
]

DRIVER = r'''
#include <iomanip>
#include <iostream>
#include <vector>
int main() {
    std::vector<Bar> bars;
    for (int i = 0; i < 5; ++i) {
        bars.push_back(Bar{100.0 + i, 110.0 + i, 90.0 + i, 105.0 + i, 1000.0,
                           1706745600000LL + 3600000LL * i});
    }
    GeneratedStrategy strategy;
    strategy.run(bars.data(), bars.size(), "60", "60");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error();
        return 1;
    }
    std::cout << std::setprecision(17) << @MEMBERS@ << "\n";
}
'''


def run_members(source: str, members: list[str], *, opt: str = "-O0") -> dict[str, float]:
    """Build ``source`` with the real engine, run five bars, read the members back."""
    printed = ' << " " << '.join(f"strategy.{name}" for name in members)
    output = run_emitted_tu(
        transpile(source),
        DRIVER.replace("@MEMBERS@", printed),
        opt=opt,
        label="method function syntax",
    )
    values = output.split()
    assert len(values) == len(members), output
    return {name: float(value) for name, value in zip(members, values)}


@pytest.mark.parametrize("function_call, member_call", TUPLE_CALLS)
def test_tuple_declaration_in_function_syntax_is_the_member_call(function_call, member_call):
    function_cpp = transpile(TUPLE_SOURCE.format(receiver="self", call=function_call))
    member_cpp = transpile(TUPLE_SOURCE.format(receiver="self", call=member_call))
    assert "unsupported tuple assignment" not in function_cpp
    assert function_cpp == member_cpp


@pytest.mark.parametrize("function_call, member_call, scale, add", SCALAR_CALLS)
def test_expression_in_function_syntax_is_the_member_call(
    function_call, member_call, scale, add,
):
    function_cpp = transpile(SCALAR_SOURCE.format(receiver="self", call=function_call))
    member_cpp = transpile(SCALAR_SOURCE.format(receiver="self", call=member_call))
    assert function_cpp == member_cpp


@pytest.mark.parametrize("function_call, member_call", TUPLE_CALLS)
def test_tuple_declaration_in_function_syntax_keeps_the_destructured_value(
    function_call, member_call,
):
    for call in (member_call, function_call):
        values = run_members(
            TUPLE_SOURCE.format(receiver="self", call=call), ["x", "g", "observed"],
        )
        assert values["x"] > 100.0, values
        # [value, value + offset]: the second element is bound, and the method that
        # reads the global adds its own receiver's offset again.
        assert values["g"] == pytest.approx(values["x"] + 7.0), (call, values)
        assert values["observed"] == pytest.approx(values["x"] + 14.0), (call, values)


def test_the_receiver_keyword_is_the_name_the_method_declares():
    # The keyword is the declared name of the first parameter, not the word ``self``.
    values = run_members(
        TUPLE_SOURCE.format(receiver="holderArg", call="pair(holderArg=holder, value=x)"),
        ["x", "g", "observed"],
    )
    assert values["g"] == pytest.approx(values["x"] + 7.0), values
    assert values["observed"] == pytest.approx(values["x"] + 14.0), values


@pytest.mark.parametrize("function_call, member_call, scale, add", SCALAR_CALLS)
def test_expression_in_function_syntax_computes_the_method_value(
    function_call, member_call, scale, add,
):
    for call in (member_call, function_call):
        values = run_members(
            SCALAR_SOURCE.format(receiver="self", call=call), ["x", "y"],
        )
        expected = scale * (values["x"] + 7.0) + add
        assert values["y"] == pytest.approx(expected), (call, values)


def test_the_receiver_type_selects_the_method_of_that_type():
    source = HEADER + '''
type A
    float v
type B
    float v
method weigh(A self, float k) => self.v * k
method weigh(B self, float k) => self.v * k + 1000.0
A a = A.new(3.0)
B b = B.new(3.0)
float fromA = weigh(a, 2.0)
float fromB = weigh(self=b, k=2.0)
float memberA = a.weigh(2.0)
float memberB = b.weigh(2.0)
'''
    values = run_members(source, ["fromA", "fromB", "memberA", "memberB"])
    assert values["memberA"] == pytest.approx(6.0), values
    assert values["memberB"] == pytest.approx(1006.0), values
    assert values["fromA"] == values["memberA"], values
    assert values["fromB"] == values["memberB"], values


def test_a_scalar_receiver_selects_the_method_of_its_primitive_type():
    source = HEADER + '''
method doubled(float self) => self * 2.0
float x = 100.0 + 13.0 * bar_index
float d = doubled(x)
float memberD = x.doubled()
'''
    values = run_members(source, ["x", "d", "memberD"])
    assert values["memberD"] == pytest.approx(2.0 * values["x"]), values
    assert values["d"] == values["memberD"], values


def test_a_function_body_calls_the_method_in_function_syntax():
    source = HEADER + '''
type Holder
    float offset
method shifted(Holder self, float value) => value + self.offset
Holder holder = Holder.new(7.0)
total(float v) => shifted(holder, v) + 1.0
float x = 100.0 + 13.0 * bar_index
float t = total(x)
'''
    values = run_members(source, ["x", "t"])
    assert values["t"] == pytest.approx(values["x"] + 8.0), values


def test_the_optimised_build_computes_the_same_values():
    values = run_members(
        TUPLE_SOURCE.format(receiver="self", call="pair(self=holder, value=x)"),
        ["x", "g", "observed"],
        opt="-O2",
    )
    assert values["g"] == pytest.approx(values["x"] + 7.0), values
    assert values["observed"] == pytest.approx(values["x"] + 14.0), values


def test_a_first_argument_without_that_method_stays_an_unknown_function():
    source = HEADER + '''
type Holder
    float offset
method shifted(Holder self, float value) => value + self.offset
float x = 100.0 + 13.0 * bar_index
float y = shifted(x, 2.0)
'''
    with pytest.raises(CompileError) as caught:
        transpile(source)
    messages = [diagnostic.message for diagnostic in caught.value.diagnostics]
    assert any("Unknown function 'shifted(...)'" in message for message in messages), messages
