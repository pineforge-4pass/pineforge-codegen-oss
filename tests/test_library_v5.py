"""Pine v5 rules inside an inlined v5 library (``library_v5``).

A library keeps its own ``//@version``, and a v6 script can import a v5 one.
``V5_RULES`` holds every difference TradingView's migration guide to v6 lists
and what PineForge does with it inside a v5 library body: lower it with v5's
rule, refuse the construct by the library's name, or nothing because it
cannot reach a library body's lowering. The implemented rules are pinned end
to end by ``tests/test_e2e_library_v5.py``; these tests pin each lowering and
each refusal. The libraries are synthetic, written here.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level
from pineforge_codegen.library_v5 import V5_RULES
from tests._pine_libraries import library_sources

HEAD = '//@version=6\nstrategy("T")\nimport pftest/Five/1 as F\n'
TAIL = 'if na(x) or x > 0\n    strategy.entry("L", strategy.long)\n'


def _lib(body: str) -> dict[str, str]:
    return {"pftest/Five/1": '//@version=5\nlibrary("Five")\n' + body}


def _cpp(lib_body: str, call: str) -> str:
    return transpile(HEAD + call + TAIL, libraries=_lib(lib_body))


def _body(cpp: str, name: str) -> str:
    """The C++ body of inlined function ``name`` (its first call-site variant)."""
    match = re.search(rf"\b{re.escape(name)}(?:_cs\d+)?\([^)]*\) \{{", cpp)
    assert match, name
    return cpp[match.end():].split("\n    }")[0]


def _refusal(lib_body: str, call: str) -> str:
    with pytest.raises(CompileError) as err:
        transpile(HEAD + call + TAIL, libraries=_lib(lib_body))
    (diag,) = [d for d in err.value.diagnostics if d.level == Level.ERROR]
    assert diag.message.startswith("library 'pftest/Five/1' is //@version=5: "), diag.message
    assert diag.location.file == "pftest/Five/1"
    return diag.message


# The migration guide's list ("Here are the changes that affect v5 scripts"),
# read 2026-09-28 (pine-script-docs migration-guides/to-pine-version-6).
OFFICIAL_V6_CHANGES = frozenset({
    "implicit-bool-cast", "bool-na", "lazy-and-or", "dynamic-requests",
    "const-int-division", "when-parameter", "default-margin", "excess-orders",
    "exit-parameter-pairs", "literal-and-field-history", "repeated-parameters",
    "series-offset", "unique-type-na", "timeframe-period-multiplier",
    "negative-array-index", "mutable-const", "transp-parameter",
    "default-colors", "dynamic-for-boundary",
})


def test_every_v6_change_has_a_disposition():
    assert set(V5_RULES) == OFFICIAL_V6_CHANGES
    assert {r.disposition for r in V5_RULES.values()} <= {
        "implemented", "refused", "not applicable"}


# -- implemented ---------------------------------------------------------

@pytest.mark.parametrize("expr, lowered", [
    ("5 / 2", "int"), ("-7 / 2", "int"), ("C / 2", "int"), ("k / 2", "int"),
    ("math.max(5, 7) / 2", "int"), ("str.length(\"abc\") / 2", "int"),
    ("(true ? 5 : 7) / 2", "int"), ("int(7.9) / 2", "int"),
    ("m / 2", "fractional"), ("bar_index / 2", "fractional"),
    ("n / 2", "fractional"), ("nz(7) / 2", "fractional"), ("5.0 / 2", "fractional"),
])
def test_int_division_follows_v5_qualifiers(expr, lowered):
    body = ('int C = 5\n'
            f'export f(int n) =>\n    k = 9\n    var m = 7\n    m += 2\n    {expr}\n')
    cpp = _cpp(body, 'x = F.f(3)\n')
    ret = [line for line in _body(cpp, "Five_v1__f").splitlines() if "return" in line][-1]
    # int(a / b): the quotient narrowed back to an int (quirk 9's na-preserving cast).
    assert ("(int)_pf_v" in ret) == (lowered == "int"), ret


def test_and_or_evaluate_both_operands_in_a_v5_body_only():
    body = 'export f(bool a, bool b) => a and b or a\n'
    cpp = _cpp(body, 'x = F.f(close > open, high > low) ? 1 : 0\n')
    assert "const bool _pf_v5_l" in cpp
    v6 = transpile('//@version=6\nstrategy("T")\nf(bool a, bool b) => a and b or a\n'
                   'x = f(close > open, high > low) ? 1 : 0\n' + TAIL)
    assert "_pf_v5_l" not in v6


def test_a_for_loops_end_is_fixed_in_a_v5_body():
    body = 'export f(int n) =>\n    int it = 0\n    for i = 0 to n\n        it += 1\n    it\n'
    cpp = _cpp(body, 'x = F.f(3)\n')
    loop = [line for line in _body(cpp, "Five_v1__f").splitlines() if "for (int" in line]
    assert loop and all("_for_end_" not in line.split(";")[-1] for line in loop), loop


def test_v5_colors_and_timeframe_period():
    body = ('export r() => color.r(color.red) + color.g(color.teal) + color.b(color.yellow)\n'
            'export tf() => timeframe.period\n')
    cpp = _cpp(body, 'x = F.r()\ns = F.tf()\nplot(x)\nif s == "D"\n    strategy.entry("S", strategy.short)\n')
    assert "0xffFF5252LL" in cpp and "0xff00897BLL" in cpp and "0xffFFEB3BLL" in cpp
    assert 'std::string("D")' in cpp and 'std::string("1D")' in cpp


def test_a_v5_top_level_constant_and_field_default_keep_v5_colors():
    body = ('color TEAL = color.new(color.teal, 50)\n'
            'export type P\n    color c = color.red\n'
            'export f() => color.g(TEAL) + color.r(P.new().c)\n')
    cpp = _cpp(body, 'x = F.f()\n')
    assert "00897B" in cpp and "FF5252" in cpp


def test_a_negative_array_index_is_an_error_in_a_v5_body():
    cpp = _cpp('export at(array<int> a, int i) => array.get(a, i)\n',
               'x = F.at(array.from(1, 2, 3), -1)\n')
    body_cpp = _body(cpp, "Five_v1__at")
    assert "__pf_array_index" in body_cpp and "__pf_raw_index<0?" not in body_cpp


def test_a_bool_na_reads_as_false_where_v5_casts_it():
    body = ('export f(float v) =>\n    bool b = v > 1\n    bool n = na\n'
            '    (not b ? 1 : 0) + (n and true ? 1 : 0) + (n or false ? 1 : 0)\n')
    assert "Five_v1__f" in _cpp(body, 'x = F.f(na)\n')


# -- refused ------------------------------------------------------------

@pytest.mark.parametrize("body, call, fragment", [
    ('half(x) => x / 2\nexport f() => half(5)\n', 'x = F.f()\n',
     "depends on its call"),
    ('export f() => request.security(syminfo.tickerid, "60", close)\n', 'x = F.f()\n',
     "request.security() in a v5 library"),
    ('export f() => 6[1]\n', 'x = F.f()\n', "[] on a literal"),
    ('export f() => color.red[1]\n', 'x = F.f()\n', "[] on a built-in constant"),
    ('export type P\n    float v\nexport f(P p) => p.v[1]\n', 'x = F.f(F.P.new(close))\n',
     "a user-defined type's field"),
    ('export f() =>\n    var n = 5\n    n += 1\n    ta.ema(close, n)\n', 'x = F.f()\n',
     "a reassigned variable passed as ta.ema()'s length"),
    ('bool G = close > open and high > low\nexport f() => G ? 1 : 0\n', 'x = F.f()\n',
     "'and' in a top-level declaration"),
    ('int L = array.get(array.from(1, 2), -1)\nexport f() => L\n', 'x = F.f()\n',
     "array.get() in a top-level declaration"),
    ('export type P\n    bool b = 1 == 1\nexport f() => P.new().b ? 1 : 0\n', 'x = F.f()\n',
     "'==' in a field's default"),
])
def test_what_v5_reads_differently_is_refused_by_name(body, call, fragment):
    assert fragment in _refusal(body, call)


@pytest.mark.parametrize("expr, what", [
    ("na(b) ? 1 : 0", "na()"),
    ("nz(b, true) ? 1 : 0", "nz()"),
    ("b == false ? 1 : 0", "'=='"),
    ("b != true ? 1 : 0", "'!='"),
    ("str.length(str.tostring(b))", "str.tostring()"),
])
def test_an_observer_of_the_v5_bool_na_is_refused(expr, what):
    body = f'export f(float v) =>\n    bool b = v > 1\n    {expr}\n'
    with pytest.raises(CompileError) as err:
        transpile(HEAD + 'x = F.f(close)\n' + TAIL, libraries=_lib(body))
    messages = [d.message for d in err.value.diagnostics if d.level == Level.ERROR]
    assert any(f"{what} of a bool reads v5's third bool state" in m for m in messages), messages


def test_a_v5_function_in_a_request_payload_is_refused():
    body = 'export f(float v) => v * 2\n'
    with pytest.raises(CompileError) as err:
        transpile(HEAD + 'x = request.security(syminfo.tickerid, "60", F.f(close))\n' + TAIL,
                  libraries=_lib(body))
    (diag,) = [d for d in err.value.diagnostics if d.level == Level.ERROR]
    assert "reached from a request.security() payload" in diag.message


def test_a_repeated_keyword_argument_is_refused_in_the_library():
    body = 'export f() => math.round(number = 2.5, number = 3.5)\n'
    with pytest.raises(CompileError) as err:
        transpile(HEAD + 'x = F.f()\n' + TAIL, libraries=_lib(body))
    (diag,) = [d for d in err.value.diagnostics if d.level == Level.ERROR]
    assert diag.location.file == "pftest/Five/1"
    assert "duplicate keyword argument 'number'" in diag.message


def test_the_fixture_v5_library_inlines():
    cpp = transpile(HEAD.replace("pftest/Five/1 as F", "pftest/V5Rules/1 as R")
                    + 'x = R.divs(close)\nplot(close)\nif x != ""\n    strategy.entry("L", strategy.long)\n',
                    libraries=library_sources("pftest/V5Rules/1"))
    assert "V5Rules_v1__divs" in cpp
