"""``request.security`` helper locals keep ``na`` and integer narrowing as the
chart's locals do.

The linear helper emitter declared a local of its declaration's C++ type
from the builder's text as is: ``int k = na`` became ``int ... =
na<double>()`` (a NaN narrowed implicitly: undefined, 0 on arm64), ``bool b =
na`` a NaN that reads true, and ``int k = math.round(x)`` narrowed a double
without the na-preserving cast (CLAUDE.md quirk 9). A local now takes
``na<T>()`` of its own type and narrows through ``_coerce_int_slot``, on its
declaration and on each ``:=``. Every earlier build had the defect in a bare
payload's helper.
"""

from __future__ import annotations

import re

from pineforge_codegen import transpile
from tests import _compile as compile_env


def _eval_bodies(cpp: str) -> str:
    return cpp[cpp.index("void _eval_security_0("):cpp.index("void evaluate_security(")]


def test_na_locals_take_their_own_type():
    src = """//@version=6
strategy("helper na locals")
f() =>
    int k = na
    if close > open
        k := 1
    else
        k := na
    nz(k, -1)
g() =>
    string s = na
    if close > open
        s := "up"
    s == "up" ? 1.0 : 0.0
mb(float x) =>
    bool up = na
    if x > open
        up := true
    up ? 1.0 : -1.0
a = request.security(syminfo.tickerid, "60", f())
b = request.security(syminfo.tickerid, "60", g())
c = request.security(syminfo.tickerid, "60", mb(close))
plot(a + b + c)
"""
    cpp = transpile(src)
    body = _eval_bodies(cpp)
    assert re.search(r"int _sec0_f_\d+_k = na<int>\(\);", body), body
    assert re.search(r"_sec0_f_\d+_k = na<int>\(\);", body.split("= na<int>();", 1)[1]), body
    assert re.search(r"std::string _sec1_g_\d+_s = na<std::string>\(\);", body), body
    assert re.search(r"bool _sec2_mb_\d+_up = na<bool>\(\);", body), body
    assert "na<double>()" not in body, body
    compile_env.compile_cpp(cpp, label="security-helper-na-locals")


def test_int_locals_narrow_without_losing_na():
    src = """//@version=6
strategy("helper int narrowing")
f() =>
    int k = math.round(close[5] / close[1])
    nz(k, -1)
v = request.security(syminfo.tickerid, "60", f())
plot(v)
"""
    cpp = transpile(src)
    body = _eval_bodies(cpp)
    assert re.search(
        r"int _sec0_f_\d+_k = \[&\]\(\)\{ auto _pf_v = \(std::round\(.*\)\); "
        r"return is_na\(_pf_v\) \? na<int>\(\) : \(int\)_pf_v; \}\(\);",
        body,
    ), body
    compile_env.compile_cpp(cpp, label="security-helper-int-narrowing")


def test_int_locals_read_from_helper_state_narrow_without_losing_na():
    # ``var`` state and a series local live in the double helper map, whatever
    # the chart's type: narrowing one to an int needs the na-preserving cast.
    src = """//@version=6
strategy("helper state narrowing")
mc(float x) =>
    var int cnt = na
    if x > open and x > close[1]
        cnt := nz(cnt) + 1
    int c2 = cnt
    na(c2) ? -1 : c2
v = request.security(syminfo.tickerid, "60", mc(close))
plot(v)
"""
    cpp = transpile(src)
    body = _eval_bodies(cpp)
    assert re.search(
        r"int _sec0_mc_\d+_c2 = \[&\]\(\)\{ auto _pf_v = "
        r"\(_security_helper_series_\[\"[^\"]+\"\]\[0\]\); "
        r"return is_na\(_pf_v\) \? na<int>\(\) : \(int\)_pf_v; \}\(\);",
        body,
    ), body
    compile_env.compile_cpp(cpp, label="security-helper-state-narrowing")
