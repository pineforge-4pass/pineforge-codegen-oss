"""``request.security`` helper tuples mixing bool, numeric and string elements.

A helper returning ``[bool, float, int, bool]`` (or any tuple mixing the
families) was refused before codegen: "request.security tuple-return helpers
support two or more numeric int/float elements or homogeneous bool elements".
The result lived in one ``std::tuple`` of a single element type. It is now a
``std::tuple`` typed per element -- ``bool``, ``std::string``, and ``double``
for every numeric element and one whose type is not inferred (``src[k]`` of a
parameter) -- with TradingView's defaults: under ``gaps_on`` a chart bar that
completes no requested bar reads ``false``, ``na`` and an empty string
(tapes ``sec2_mixed_tuple`` and ``sec2_tuple_string``). A string element's
Program binding is a ``std::string`` member.

A helper parameter read at a helper-local index (``src_h[mHiAgo]`` with
``mHiAgo = -ta.highestbars(src_h, lb)``) composed the index into the
caller's scope, where the local does not exist; the index is now lowered in
the helper's scope and handed to the requested bar field's history.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests import _compile as compile_env
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits


@pytest.mark.parametrize("name", ["sec2_mixed_tuple", "sec2_tuple_string"])
def test_mixed_helper_tuples_match_the_tape(name, tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp(name)
    exits = replay(engine, base, {"probe": Build(source(name))})
    tape = tape_exits(name)
    assert len(tape) == 265
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    print(f"{name}: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


def test_mixed_helper_tuple_storage_is_typed_per_element():
    cpp = transpile(source("sec2_mixed_tuple"))
    decl = ("std::tuple<bool, double, double, bool> _req_sec_1 = "
            "std::tuple<bool, double, double, bool>{false, na<double>(), na<double>(), false};")
    assert decl in cpp
    cpp = transpile(source("sec2_tuple_string"))
    assert ("std::tuple<std::string, double, bool> _req_sec_1 = std::tuple<std::string, "
            "double, bool>{na<std::string>(), na<double>(), false};") in cpp
    assert re.search(r"std::string d2 = ", cpp)
    assert re.search(r"bool b2 = ", cpp)


def test_helper_parameter_history_at_a_helper_local_index():
    src = """//@version=6
strategy("helper local history index")
f(src, lb) =>
    ago = -ta.highestbars(src, lb)
    hi = src[ago]
    [hi, ago, hi > src]
[hi, ago, up] = request.security(syminfo.tickerid, "60", f(high, 10))
if up and ago > 2
    strategy.entry("L", strategy.long)
plot(hi)
"""
    cpp = transpile(src)
    body = cpp[cpp.index("void _eval_security_0("):cpp.index("void evaluate_security(")]
    assert re.search(r"int _hidx = [^;]*_sec0_f_\d+_ago", body), body
    assert "_sec0_hist_high[_hidx - 1]" in body
    compile_env.compile_cpp(cpp, label="security-helper-local-history-index")


@pytest.mark.parametrize("element, family", [
    ("color.red", "color"),
    ("array.from(src)", "void"),
])
def test_non_scalar_helper_tuple_elements_stay_refused(element, family):
    src = f"""//@version=6
strategy("non-scalar tuple")
f(float src) =>
    x = {element}
    [src, x]
[a, b] = request.security(syminfo.tickerid, "60", f(close))
plot(a)
"""
    with pytest.raises(CompileError, match=rf"int, float, bool or string elements; "
                                           rf"inferred 2 element\(s\) \[float, {family}\]"):
        transpile(src)


def test_untyped_helper_tuple_element_is_typed_at_the_call():
    src = """//@version=6
strategy("call-typed element")
f(s) => [close, s]
[c, t] = request.security(syminfo.tickerid, "60", f("abc"))
if t == "abc"
    strategy.entry("L", strategy.long)
plot(c)
"""
    cpp = transpile(src)
    assert "std::tuple<double, std::string> _req_sec_0" in cpp
    compile_env.compile_cpp(cpp, label="security-call-typed-tuple-element")


def test_untyped_element_the_call_cannot_type_stays_refused():
    src = """//@version=6
strategy("untyped element")
g(x) => x
f(s) => [close, g(s)]
[c, t] = request.security(syminfo.tickerid, "60", f(close))
plot(c)
"""
    with pytest.raises(CompileError, match=r"inferred 2 element\(s\) \[float, unknown\]"):
        transpile(src)


@pytest.mark.parametrize("src", [
    # An index argument bound to the caller's local.
    """//@version=6
strategy("local index argument")
f(float src, int k) => src[k]
g() =>
    k = 0
    f(close, k) + close
v = request.security(syminfo.tickerid, "60", g())
plot(v)
""",
    # A local the prepasses could fold (0) is still a run-time C++ local.
    """//@version=6
strategy("literal local index")
f(float src) =>
    k = 0
    src[k]
a = request.security(syminfo.tickerid, "60", f(close))
plot(a)
""",
    """//@version=6
strategy("local index through a caller parameter")
f(float src) =>
    k = bar_index % 3
    src[k]
g(int k) => f(close)
x = request.security(syminfo.tickerid, "60", g(0))
plot(x)
""",
    """//@version=6
strategy("local index beside a TA index")
f(float src) =>
    k = bar_index % 2
    src[k]
g(float x) => f(close[ta.barssince(x > open)])
v = request.security(syminfo.tickerid, "60", g(close) + g(high))
plot(v)
""",
])
def test_helper_local_index_declares_its_history_in_every_scope(src):
    compile_env.compile_cpp(transpile(src), label="security-helper-local-index-scopes")
