"""Pins for the static TA precalc vectors and the host-switchable read.

A static chart TA site (bar-data ``compute()`` arguments, in a top-level statement) owns one
class member ``std::vector<T> _precalc_<member>;`` that ``precalculate()`` fills. A call nested in
an expression reads it through a fallback the host can switch off,

    (_use_precalc ? _precalc_X[bar_index_] : (history_advances_new_bar() ? X.compute(...) : X.recompute(...)))

(``CallVisitor._visit_func_call``). These tests pass on main and keep the inventory and the
fallback in place while the cache is reworked.

The sites are nested in an expression of a top-level declaration. A TA call that is itself the
value of a declaration (``x = ta.sma(close, 14)``) and a tuple declaration read the indicator live
(``visit_stmt``), so the tuple test pins the declaration only; a call in an ``if`` condition is
not static (``Analyzer._visit_IfStmt`` leaves the global scope) and gets no vector.
"""

import re

import pytest

from pineforge_codegen import transpile


HEADER = '//@version=6\nstrategy("precalc inventory")\n'
DECLARATION = re.compile(
    r"^[ \t]*std::vector<(?P<type>[A-Za-z_][A-Za-z_0-9:]*)>[ \t]+(?P<name>_precalc_\w+);[ \t]*$",
    re.MULTILINE,
)

NO_SITE = '''flag = close > open
if flag
    strategy.entry("L", strategy.long)
'''

ONE_SITE = '''flag = close > ta.sma(close, 14)
if flag
    strategy.entry("L", strategy.long)
'''

THREE_SITES = '''fast = close > ta.sma(close, 14)
mid = close > ta.sma(close, 50)
slow = close > ta.sma(close, 200)
if fast and mid and slow
    strategy.entry("L", strategy.long)
'''

MACD_SITE = '''[macdLine, signalLine, histLine] = ta.macd(close, 12, 26, 9)
if macdLine > signalLine
    strategy.entry("L", strategy.long)
'''

CROSSOVER_SITE = '''blocked = not ta.crossover(close, open)
if blocked
    strategy.entry("L", strategy.long)
'''


def inventory(body):
    """The generated C++ and the ``(element type, member)`` of every precalc vector declaration."""
    cpp = transpile(HEADER + body)
    return cpp, [(found["type"], found["name"]) for found in DECLARATION.finditer(cpp)]


def fallback_read(name):
    """The read of ``name`` with the same arguments to ``compute`` and to ``recompute``."""
    member = name[len("_precalc_"):]
    return re.compile(
        r"\(_use_precalc \? " + re.escape(name) + r"\[bar_index_\] : "
        + r"\(history_advances_new_bar\(\) \? " + re.escape(member) + r"\.compute\((?P<arguments>[^\n]*?)\) : "
        + re.escape(member) + r"\.recompute\((?P=arguments)\)\)\)"
    )


@pytest.mark.parametrize(("body", "count"), [(NO_SITE, 0), (ONE_SITE, 1), (THREE_SITES, 3)])
def test_one_double_vector_member_per_static_single_value_site(body, count):
    cpp, declared = inventory(body)
    assert [kind for kind, _name in declared] == ["double"] * count
    assert len({name for _kind, name in declared}) == count
    assert "    bool _use_precalc = false;" in cpp


def test_a_tuple_site_declares_one_result_vector():
    _cpp, declared = inventory(MACD_SITE)
    assert [kind for kind, _name in declared] == ["ta::MACDResult"]


def test_a_crossover_site_declares_a_bool_vector():
    _cpp, declared = inventory(CROSSOVER_SITE)
    assert [kind for kind, _name in declared] == ["bool"]


@pytest.mark.parametrize("body", [ONE_SITE, THREE_SITES, CROSSOVER_SITE])
def test_every_static_site_read_keeps_the_host_switchable_fallback(body):
    cpp, declared = inventory(body)
    assert declared
    for _kind, name in declared:
        assert fallback_read(name).search(cpp), f"{name} is read without the _use_precalc fallback"
