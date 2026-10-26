"""Issue #227: the account currency is a string, distinct from the quote currency."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu
from tests.test_strategy_capabilities import emitted_receipt


_USDT_DRIVER = r'''
#include <exception>
#include <iostream>

struct USDTStrategy : GeneratedStrategy {
    USDTStrategy() {
        syminfo_.tickerid = "BINANCE:BTCUSDT";
        syminfo_.ticker = "BTCUSDT";
        syminfo_.currency = "USDT";
    }
};

int main() {
    Bar bars[] = {
        Bar{100.0, 102.0, 99.0, 101.0, 10.0, 1743465600000LL},
        Bar{101.0, 103.0, 100.0, 102.0, 11.0, 1743466500000LL},
        Bar{102.0, 104.0, 101.0, 103.0, 12.0, 1743467400000LL},
    };
    USDTStrategy strategy;
    try {
        strategy.run(bars, 3);
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << '\n';
        return 2;
    }
    if (strategy.completed_bars != 3) {
        std::cerr << "strategy did not reach the final bar\n";
        return 3;
    }
    std::cout << "ok\n";
}
'''


def _run_on_usdt(source: str) -> None:
    # A marker after the script's checks also catches a swallowed runtime.error.
    cpp = transpile(source + "\ncompleted_bars = bar_index + 1\n")
    assert run_emitted_tu(
        cpp, _USDT_DRIVER, opt="-O0", label="strategy-account-currency"
    ) == "ok\n"


def test_explicit_usd_account_currency_on_a_usdt_symbol() -> None:
    _run_on_usdt('''//@version=6
strategy('Account currency', currency=currency.USD)
if barstate.islast and strategy.account_currency != 'USD'
    runtime.error('account currency is not USD')
plot(close)
''')


def test_account_currency_compares_with_currency_constants() -> None:
    _run_on_usdt('''//@version=6
strategy('Account currency', currency=currency.USD)
if strategy.account_currency != currency.USD
    runtime.error('USD account required')
bool equal_branch_taken = false
if strategy.account_currency == currency.USD
    equal_branch_taken := true
if not equal_branch_taken
    runtime.error('USD equality branch was not taken')
''')


@pytest.mark.parametrize('operator, expected', [('==', 'true'), ('!=', 'false')])
def test_two_account_currency_reads_use_string_comparison(operator, expected) -> None:
    # Both operands used to fall back to double in codegen's type inference,
    # sending std::string values through static_cast<double> and is_na.
    _run_on_usdt(f'''//@version=6
strategy('Account string comparison', currency=currency.USD)
same = strategy.account_currency {operator} strategy.account_currency
if same != {expected}
    runtime.error('account string comparison failed')
''')


def test_account_currency_concatenates_as_a_string() -> None:
    _run_on_usdt('''//@version=6
strategy('Account currency', currency=currency.USD)
label_text = 'account ' + strategy.account_currency
if label_text != 'account USD'
    runtime.error('incorrect account label')
plot(close)
''')


@pytest.mark.parametrize('argument, expected', [
    ('currency.EUR', 'EUR'),
    ('currency.BTC', 'BTC'),
    ('currency.USDT', 'USDT'),
    ('currency.NONE', 'USDT'),
    ("'JPY'", 'JPY'),
])
def test_other_account_currency_declarations(argument, expected) -> None:
    _run_on_usdt(f'''//@version=6
strategy('Other account currency', currency={argument})
if strategy.account_currency != '{expected}'
    runtime.error('incorrect declared account currency')
label_text = 'account ' + strategy.account_currency
if label_text != 'account {expected}'
    runtime.error('incorrect declared account label')
''')


def test_omitted_currency_uses_the_symbol_currency_as_a_string() -> None:
    _run_on_usdt('''//@version=6
strategy('Default account currency')
bool equal_branch_taken = false
if strategy.account_currency == currency.USDT
    equal_branch_taken := true
if not equal_branch_taken or strategy.account_currency != 'USDT'
    runtime.error('default account currency is not USDT')
label_text = 'account ' + strategy.account_currency
if label_text != 'account USDT'
    runtime.error('incorrect default account label')
''')


def test_positional_currency_declaration() -> None:
    _run_on_usdt('''//@version=6
strategy('Positional account currency', 'account', true, format.price, 2,
    scale.right, 1, false, false, 500, 0, strategy.fixed, 1, 10000, currency.EUR)
label_text = 'account ' + strategy.account_currency
if label_text != 'account EUR'
    runtime.error('positional account currency is not EUR')
''')


def test_syminfo_currency_remains_the_quote_currency_for_a_eur_account() -> None:
    _run_on_usdt('''//@version=6
strategy('Account versus quote currency', currency=currency.EUR)
if syminfo.currency != 'USDT'
    runtime.error('symbol quote currency changed')
if strategy.account_currency != 'EUR'
    runtime.error('account currency is not EUR')
''')


def test_bare_account_currency_binding_keeps_the_symbol_default() -> None:
    # Compatibility control for the previously working bare string binding.
    _run_on_usdt('''//@version=6
strategy('Bound account currency')
account = strategy.account_currency
if account != 'USDT'
    runtime.error('bound account currency changed')
''')


def test_nonliteral_currency_keeps_its_unresolved_declaration() -> None:
    # Strategy declaration evaluation does not resolve variables. Preserve the
    # capability receipt's unresolved marker and the existing symbol fallback.
    source = '''//@version=6
strategy('Unresolved account currency', currency=ACCOUNT)
string ACCOUNT = 'EUR'
label_text = 'account ' + strategy.account_currency
if label_text != 'account USDT'
    runtime.error('unresolved currency fallback changed')
'''
    receipt = emitted_receipt(source)
    assert receipt['declarations']['currency'] == 'ACCOUNT'
    assert 'currency' in receipt['unresolved']
    _run_on_usdt(source)
