"""``strategy.default_entry_qty(fill_price)`` reads the host's default-quantity query.

TradingView accepts the call with one numeric price and returns the order size
that the strategy's default quantity settings give at that price. It used to
lower to ``calc_qty(...)``, a function no host declares, so every script that
read the query failed to build; and every spelling TradingView refuses (the
wrong number of arguments, an unknown argument name, an argument that is not a
number) translated to the same undeclared call.

The expectations are TradingView's own answers. For the accepted spellings they
are the entry quantities of the trades TradingView exported for probe scripts
that encode the query's answer in the quantity of an entry (``q == 7`` as
quantity 2, and the like), on BINANCE:ETHUSDT.P at 15 minutes. For the refused
spellings they are the failure records TradingView returned (``CE10115`` too
many arguments, ``CE10120`` unknown argument name, ``CE10123`` argument of the
wrong type, ``CE10165`` no value for a parameter).

The native rows link the emitted code against the engine's host, which has to
provide the query (``default_entry_qty``), and skip without an engine build.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError, Level
from tests import _compile as compile_env


def _preface(default_qty_value: str = "7") -> str:
    return (
        "//@version=6\n"
        "strategy('Default quantity', currency=currency.NONE, initial_capital=100000000, "
        f"default_qty_type=strategy.fixed, default_qty_value={default_qty_value})\n"
    )


def _query_call_sites(cpp: str) -> list[str]:
    """The calls of the host query in the emitted code (not a mention in a comment)."""
    return re.findall(r"(?<![\w.])default_entry_qty\(", cpp)


# --------------------------------------------------------------------------
# Spellings TradingView accepts: each translates to one call of the host query.
# --------------------------------------------------------------------------

ACCEPTED_PRICES = {
    "series float": "close",
    "named": "fill_price=close",
    "int literal": "100",
    "float literal": "100.0",
    "series int": "bar_index + 1",
    "expression": "open + 1",
    "history": "close[1]",
    "bare na": "na",
    "typed na": "float(na)",
    "zero": "0.0",
    "negative": "-100.0",
}


@pytest.mark.parametrize("price", ACCEPTED_PRICES.values(), ids=ACCEPTED_PRICES.keys())
def test_an_accepted_price_lowers_to_one_call_of_the_host_query(price):
    cpp = transpile(_preface() + f"float seen = strategy.default_entry_qty({price})\n")
    assert "calc_qty" not in cpp
    assert len(_query_call_sites(cpp)) == 1, cpp


def test_the_price_expression_is_the_argument_of_the_query():
    cpp = transpile(_preface() + "float seen = strategy.default_entry_qty(close)\n")
    assert "default_entry_qty(current_bar_.close)" in cpp


# --------------------------------------------------------------------------
# Spellings TradingView refuses are refused here too, where the offending
# argument or call is, and also inside the arm of a switch.
# --------------------------------------------------------------------------

REFUSED = {
    # CE10165: no value assigned to the "fill_price" parameter.
    "no price": ("q = strategy.default_entry_qty()", 3, "fill_price"),
    # CE10115: too many arguments passed into the function call.
    "two prices": ("q = strategy.default_entry_qty(close, close)", 3, "at most 1"),
    "price twice": ("q = strategy.default_entry_qty(close, fill_price=open)", 3, "twice"),
    # CE10120: the function does not have an argument with that name.
    "unknown name": ("q = strategy.default_entry_qty(price=close)", 3, "no parameter 'price'"),
    # CE10123: an argument of another type was used where a series float is expected.
    "string price": ('q = strategy.default_entry_qty("100")', 3, "string argument"),
    "bool price": ("q = strategy.default_entry_qty(true)", 3, "bool argument"),
    "color price": ("q = strategy.default_entry_qty(color.red)", 3, "color argument"),
    "array price": ("q = strategy.default_entry_qty(array.from(close))", 3, "collection argument"),
    # CE10271: could not find the function.
    "unknown function": ("q = strategy.default_entry_quantity(close)", 3, "default_entry_quantity"),
    # The same failures inside the arm of a switch (CE10165 and CE10123 at the arm).
    "no price in a switch arm": (
        "q = switch bar_index % 7\n    3 => strategy.default_entry_qty()\n    => 1.0",
        4, "fill_price"),
    "bool price in a switch arm": (
        "q = switch bar_index % 7\n    3 => strategy.default_entry_qty(true)\n    => 1.0",
        4, "bool argument"),
}


@pytest.mark.parametrize("statement,line,needle", REFUSED.values(), ids=REFUSED.keys())
def test_a_spelling_tradingview_refuses_is_refused(statement, line, needle):
    with pytest.raises(CompileError) as caught:
        transpile(_preface() + statement + "\nplot(1.0)\n")
    # The first refusal; a warning about the script's own text may come before it.
    diagnostic = next(d for d in caught.value.diagnostics if d.level == Level.ERROR)
    assert diagnostic.location.line == line, diagnostic
    assert needle in diagnostic.message, diagnostic.message


# --------------------------------------------------------------------------
# Native rows: the emitted translation unit links against the engine's host.
# --------------------------------------------------------------------------

_DRIVER = r"""
#include <iomanip>
#include <iostream>
#include <vector>

int main() {
    std::vector<Bar> bars;
    for (int i = 0; i < 12; ++i)
        bars.push_back(Bar{100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0,
                           1743465600000LL + i * 3600000LL});
    GeneratedStrategy strategy;
    strategy.run(bars.data(), static_cast<int>(bars.size()));
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << "\n";
        return 2;
    }
    std::cout << std::setprecision(17) << MEMBER << "\n";
}
"""


def _read(source: str, member: str = "seen") -> float:
    """Run ``source`` for twelve bars and return the final value of ``member``."""
    cpp = transpile(source)
    shown = compile_env.run_emitted_tu(
        cpp, _DRIVER.replace("MEMBER", f"strategy.{member}"), opt="-O0",
        label=f"default entry quantity {member}")
    return float(shown)


VALID_PRICE_SETUPS = {
    "series float": ("", "close"),
    "named": ("", "fill_price=close"),
    "int literal": ("", "100"),
    "float literal": ("", "100.0"),
    "series int": ("", "bar_index + 1"),
    "float input": ("p = input.float(100, 'Price', minval=1)\n", "p"),
    "int input": ("p = input.int(100, 'Price', minval=1)\n", "p"),
}


@pytest.mark.parametrize("setup,price", VALID_PRICE_SETUPS.values(), ids=VALID_PRICE_SETUPS.keys())
def test_fixed_sizing_returns_the_configured_units_at_a_valid_price(setup, price):
    # TradingView: q == 7 for every one of these spellings (entry quantity 2).
    source = _preface() + setup + f"float seen = strategy.default_entry_qty({price})\n"
    assert _read(source) == 7.0


INVALID_PRICES = {"zero": "0.0", "negative": "-100.0", "bare na": "na", "typed na": "float(na)"}


@pytest.mark.parametrize("price", INVALID_PRICES.values(), ids=INVALID_PRICES.keys())
def test_fixed_sizing_ignores_an_invalid_price(price):
    # TradingView does not look at the price when the size is fixed: q == 7.
    source = _preface() + f"float seen = strategy.default_entry_qty({price})\n"
    assert _read(source) == 7.0


def test_fixed_sizing_returns_a_fractional_size_unrounded():
    # TradingView: q == 2.75, not 2 or 3.
    source = _preface("2.75") + "float seen = strategy.default_entry_qty(close)\n"
    assert _read(source) == 2.75


def test_a_zero_default_size_is_returned_as_zero():
    # TradingView: q == 0 (not na).
    source = _preface("0") + "float seen = strategy.default_entry_qty(close)\n"
    assert _read(source) == 0.0


def test_the_result_is_a_series():
    source = (_preface() + "float seen = strategy.default_entry_qty(close)\n"
              "float before = seen[1]\n")
    assert _read(source, "before") == 7.0


def test_the_price_argument_is_evaluated_once_per_call():
    # TradingView evaluates the price expression exactly once.
    source = (
        _preface()
        + "var calls = array.new_int(1, 0)\n"
        "price() =>\n"
        "    array.set(calls, 0, array.get(calls, 0) + 1)\n"
        "    close\n"
        "array.set(calls, 0, 0)\n"
        "float seen = strategy.default_entry_qty(price())\n"
        "int count = array.get(calls, 0)\n"
    )
    assert _read(source, "count") == 1.0


def test_an_unselected_price_argument_is_not_evaluated():
    # TradingView does not evaluate the arm a conditional price does not select.
    source = (
        _preface()
        + "bad() =>\n"
        "    runtime.error('Unselected price argument evaluated')\n"
        "    100.0\n"
        "float seen = strategy.default_entry_qty(bar_index > -1 ? close : bad())\n"
    )
    assert _read(source) == 7.0


def test_the_reported_script_builds_and_sizes_its_entries_with_the_query():
    # The script as it was reported: an EMA crossover that sizes its entry with the
    # query at the signal bar's close. It used to stop at the undeclared function.
    source = (
        "//@version=6\n"
        "strategy('repro', default_qty_type=strategy.percent_of_equity, default_qty_value=10)\n"
        "fast = ta.ema(close, 9)\n"
        "slow = ta.ema(close, 21)\n"
        "size = strategy.default_entry_qty(close)\n"
        "if ta.crossover(fast, slow)\n"
        "    strategy.entry('Long', strategy.long, qty = size)\n"
        "if ta.crossunder(fast, slow)\n"
        "    strategy.close('Long')\n"
    )
    size = _read(source, "size")
    assert size > 0.0 and size < float("inf"), size


def test_a_script_variable_named_like_the_query_does_not_hide_it():
    source = (
        _preface()
        + "float default_entry_qty = 3.5\n"
        "float seen = strategy.default_entry_qty(close)\n"
    )
    cpp = transpile(source)
    assert not re.search(r"\bdouble\s+default_entry_qty\b", cpp), cpp
    assert _read(source) == 7.0
