"""A binary operator's left operand is evaluated before its right operand.

Pine evaluates the operands of a binary operator left to right: TradingView's
tape of ``fixtures/krunerr_tv/array_negative_index.pine`` spells
``str.tostring(a.get(-1)) + "," + str.tostring(m) + "," +
str.tostring(a.remove(-1)) + "," + array.join(a, ",")`` as
``99,50,99,5,10,20,30,45``: the ``get`` reads the last element before the
``remove`` takes it, and the ``join`` runs after it. C++ leaves the order of
the operands of ``+ - * / %`` and of an overloaded operator (``std::string``'s
``+`` and ``==``, the ``std::fmod`` of ``%``) unspecified. AppleClang evaluates
them left to right; GCC on x86-64 evaluated std::string ``operator+``'s right
operand first, so the Linux build spelled ``45,50,99,5,10,20,30,45,99``
(lab remote job rj-20260927t190702-ab025b,
test_e2e_krunerr_array_negative_index).

When one operand has an effect the other one can observe (an array, map or
matrix mutation, a drawing, an order, a log line, or a user function or method
that does one of these or assigns a UDT field, against an operand that reads
more than literals, variables and pure calls), the codegen binds the left
operand's value first and evaluates the right operand after it, on every
compiler, on the chart and in a ``request.security`` payload. Other operators
keep their C++.
"""

from __future__ import annotations

import re
from pathlib import Path

from pineforge_codegen import transpile
from tests.test_pinemap_semantics import _compile_and_run


FIXTURES = Path(__file__).parent / "fixtures" / "krunerr_tv"
_BINDING = re.compile(r"\[&\]\{ (?:auto|bool) (__pf_binop_lhs_\d+) = \(")


def _assignment(cpp: str, target: str) -> str:
    return next(
        line for line in cpp.splitlines()
        if line.strip().startswith(f"{target} =")
    )


def test_mutating_operand_is_evaluated_after_the_left_operand_is_bound():
    cpp = transpile((FIXTURES / "array_negative_index.pine").read_text())
    s6 = _assignment(cpp, "s6")
    bound = _BINDING.findall(s6)
    # The + that adds str.tostring(a.remove(-1)) and the + that adds
    # array.join(a, ","); the + of a literal "," and the effect-free + of the
    # two reads before them keep their spelling.
    assert len(bound) == 2, s6
    # The inner binding's right operand comes first in the text.
    before_remove, before_join = sorted(
        s6.index(f"return ({token} + ") for token in bound
    )
    # a.get(-1) is read into the inner binding, then a.remove(-1) erases.
    assert s6.index("return (__pf_array[(size_t)__pf_array_index]);") < before_remove
    assert s6.index(".erase(") > before_remove
    # array.join(a, ",") reads the array after the outer binding.
    assert s6.index("std::to_string(a[") > before_join
    # Operands no other operand changes keep the plain C++ operator: s4's
    # two reads, and every entry id's concatenation.
    assert "__pf_binop_lhs_" not in _assignment(cpp, "s4")
    assert cpp.count("__pf_binop_lhs_") == s6.count("__pf_binop_lhs_")


_ORDER_SOURCE = '''//@version=6
strategy("binop operand order")
type Counter
    int n = 0
method bump(Counter self) =>
    self.n += 1
    self.n
pushed(array<int> id) =>
    array.push(id, 7)
    array.size(id)
ints = array.from(5, 10, 20, 30, 45, 99)
concat = str.tostring(ints.get(-1)) + "," + str.tostring(ints.remove(-1)) + "," + array.join(ints, ",")
floats = array.from(3.0, 2.0, 10.0)
difference = floats.pop() - floats.pop()
mods = array.from(3.0, 10.0)
modulo = mods.pop() % mods.pop()
quots = array.from(4.0, 2.0)
quotient = quots.pop() / quots.pop()
names = array.from("x", "y")
same_name = names.shift() == names.get(0)
flags = array.from(false, true)
same_flag = flags.get(0) == flags.remove(0)
counts = array.new<int>()
through_call = pushed(counts) * 10 + counts.size()
calls = array.new<int>()
call_text = str.tostring(calls.size()) + str.tostring(pushed(calls))
counter = Counter.new()
method_text = str.tostring(counter.n) + str.tostring(counter.bump())
'''


def test_user_calls_methods_and_payloads_bind_their_left_operand():
    cpp = transpile(_ORDER_SOURCE + '''x = close
plain_text = str.tostring(floats.pop()) + "," + str.tostring(x) + "," + str.tostring(math.round(x * 2))
payload = request.security(syminfo.tickerid, "60", str.tostring(ints.get(-1)) + str.tostring(ints.remove(-1)))
''')
    # A user function that pushes, and a UDT method that assigns a field,
    # are effects: the left operand is bound, the right one read after it.
    for target, bound, after in (
        ("through_call", "pushed(counts)", "counts.size()"),
        ("call_text", "calls.size()", "pushed(calls)"),
        ("method_text", "_pf_udt_Counter.read(counter).n",
         "_udt_Counter_bump(counter)"),
    ):
        line = _assignment(cpp, target)
        (token,) = _BINDING.findall(line)
        assert (line.index(bound) < line.index(f"return ({token} ")
                < line.index(after)), line
    # A bool operand is bound as a bool: a std::vector<bool> element is a
    # proxy of the bit, which the remove moves (a bound proxy stays right only
    # while the compiler converts it before evaluating the remove).
    assert "[&]{ bool __pf_binop_lhs_" in _assignment(cpp, "same_flag")
    # Beside an effect, literals, variables and pure calls over them are
    # order-free: the pop's neighbours keep the plain operator.
    assert "__pf_binop_lhs_" not in _assignment(cpp, "plain_text")
    # A request.security payload binds its left operand the same way.
    payload = _assignment(cpp, "_req_sec_0")
    (token,) = _BINDING.findall(payload)
    assert payload.index(".erase(") > payload.index(f"return ({token} + ")


def test_operands_are_evaluated_left_to_right_at_run_time():
    cpp = transpile(_ORDER_SOURCE)
    driver = r'''
#include <iostream>
int main() {
    GeneratedStrategy strategy;
    Bar bar{1.0, 1.0, 1.0, 1.0, 1.0, 0};
    strategy.run(&bar, 1);
    if (!strategy.last_error().empty()) {
        std::cout << strategy.last_error() << "\n";
        return 2;
    }
    std::cout << strategy.concat << "|" << strategy.difference << "|"
              << strategy.modulo << "|" << strategy.quotient << "|"
              << strategy.same_name << "|" << strategy.same_flag << "|"
              << strategy.through_call << "|" << strategy.call_text << "|"
              << strategy.method_text << "\n";
}
'''
    # Left to right: get(-1) reads 99 before remove(-1) takes it and join
    # runs last; 10 - 2; 10 % 3; 2 / 4; "x" == "y"; false == false (the
    # element read before the remove, not the bit that moves into its place);
    # the call pushes before counts.size() reads; calls.size() and counter.n
    # read before the call pushes and the method bumps.
    assert _compile_and_run(cpp + driver, label="binop-operand-order") == (
        "99,99,5,10,20,30,45|8|1|0.5|0|1|11|01|01\n"
    )
