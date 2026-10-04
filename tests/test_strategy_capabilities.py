"""Compiled execution requirements are receipts, not runtime guesses."""
import json
import re

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu
from pineforge_codegen.codegen.capabilities import STRATEGY_PARAMETERS


def emitted_receipt(source):
    cpp = transpile(source)
    match = re.search(r'checked_settings::receipt\(("(?:[^"\\]|\\.)*"), json, capacity, required\);', cpp)
    assert match, cpp[-6000:]
    document = json.loads(match.group(1))
    receipt = json.loads(document)
    assert json.dumps(receipt, sort_keys=True, separators=(',', ':')) == document
    assert 'strategy_capabilities_api_version(void)' in cpp
    assert '#ifdef PF_CAPABILITIES_API_VERSION' in cpp
    return receipt


@pytest.mark.parametrize(('name', 'value', 'expected'), [
    ('calc_on_every_tick', 'true', True),
    ('calc_on_order_fills', 'true', True),
    ('process_orders_on_close', 'true', True),
    ('use_bar_magnifier', 'true', True),
    ('fill_orders_on_standard_ohlc', 'true', True),
    ('backtest_fill_limits_assumption', '3', 3),
    ('currency', 'currency.EUR', 'currency.EUR'),
    ('timeframe', '"15"', '15'),
    ('timeframe_gaps', 'false', False),
    ('dynamic_requests', 'false', False),
    ('calc_on_every_history_tick', 'true', True),
])
def test_strategy_declarations(name, value, expected):
    receipt = emitted_receipt(f'//@version=6\nstrategy("capabilities", {name}={value})\nstrategy.entry("entry", strategy.long)')
    assert receipt['version'] == 1
    assert receipt['declarations'][name] == expected
    assert receipt['requirements']['fx_curve'] is (name == 'currency')
    assert not receipt['unresolved']


@pytest.mark.parametrize(('function', 'timeframe', 'lookahead'), [
    ('security', '15', 'off'), ('security', 'D', 'on'), ('security_lower_tf', '1', 'off'),
])
def test_security_contexts(function, timeframe, lookahead):
    options = f', lookahead=barmerge.lookahead_{lookahead}' if function == 'security' else ''
    expression = f'request.{function}(syminfo.tickerid, "{timeframe}", close{options})'
    payload = 'array.get(value, 0)' if function == 'security_lower_tf' else 'value'
    receipt = emitted_receipt(f'//@version=6\nstrategy("security receipt")\nvalue = {expression}\nif {payload} > close\n    strategy.entry("entry", strategy.long)')
    request = receipt['requests'][0]
    assert request['function'] == f'request.{function}'
    assert request['symbol'] == 'syminfo.tickerid'
    assert request['timeframe'] == timeframe
    assert request['lookahead'] == f'barmerge.lookahead_{lookahead}'
    assert request['feed'] == 'chart'
    assert not receipt['requirements']['auxiliary_security_feeds']


def test_auxiliary_symbol_and_recorded_requests():
    source = '''//@version=6
strategy("external receipt")
other = request.security("NASDAQ:MSFT", "D", close)
earnings = request.earnings("NASDAQ:MSFT")
if other > earnings
    strategy.entry("entry", strategy.long)
'''
    receipt = emitted_receipt(source)
    assert receipt['requirements']['auxiliary_security_feeds']
    assert receipt['requirements']['recorded_series']
    assert any(request['symbol'] == 'NASDAQ:MSFT' for request in receipt['requests'])
    assert any(request['feed'] == 'recorded' for request in receipt['requests'])


def test_dynamic_context_and_intrabar_persistence_are_explicit():
    receipt = emitted_receipt('''//@version=6
strategy("dynamic receipt")
tf = input.timeframe("15", "Requested timeframe")
varip int count = 0
count += 1
value = request.security(syminfo.tickerid, tf, close)
if value > close
    strategy.entry("entry", strategy.long)
''')
    assert receipt['requirements']['intrabar_persistence']
    assert receipt['unresolved'] == ['request.security[0].timeframe']


def test_nonliteral_declaration_is_not_silently_defaulted():
    receipt = emitted_receipt('''//@version=6
strategy("unresolved declaration", calc_on_every_tick=TICK)
const bool TICK = true
strategy.entry("entry", strategy.long)
''')
    assert 'calc_on_every_tick' in receipt['unresolved']
    assert receipt['declarations']['calc_on_every_tick'] != False


@pytest.mark.parametrize(('name', 'value', 'expected'), [
    ('calc_on_order_fills', 'true', True), ('calc_on_every_tick', 'true', True),
    ('backtest_fill_limits_assumption', '3', 3), ('currency', 'currency.EUR', 'currency.EUR'),
    ('process_orders_on_close', 'true', True), ('use_bar_magnifier', 'true', True),
    ('fill_orders_on_standard_ohlc', 'true', True),
])
def test_positional_declarations(name, value, expected):
    arguments = ['"t"', '"s"', 'true', 'format.price', '2', 'scale.right', '1',
                 'false', 'false', '500', '0', 'strategy.fixed', '1', '10000',
                 'currency.NONE', '0', 'strategy.commission.percent', '0', 'false',
                 '"FIFO"', '100', '100', 'false', '50', '50', '50', '0', '2', 'false', 'false']
    position = STRATEGY_PARAMETERS.index(name)
    arguments[position] = value
    receipt = emitted_receipt(f'//@version=6\nstrategy({",".join(arguments[:position + 1])})\nstrategy.entry("entry", strategy.long)')
    assert receipt['declarations'][name] == expected
    assert not receipt['unresolved']


def test_exact_review_positional_form():
    receipt = emitted_receipt('//@version=6\nstrategy("t","s",true,format.price,2,scale.right,1,true,true)\nstrategy.entry("entry", strategy.long)')
    assert receipt['declarations']['calc_on_every_tick'] is True
    assert receipt['declarations']['calc_on_order_fills'] is True


@pytest.mark.parametrize('declaration', ['strategy(TITLE)', 'strategy("t", pyramiding=COUNT)',
                                       'strategy("t", "s", true, format.price, 2, scale.right, COUNT)'])
def test_every_nonliteral_argument_is_unresolved(declaration):
    receipt = emitted_receipt(f'//@version=6\n{declaration}\nconst string TITLE = "t"\nconst int COUNT = 1\nstrategy.entry("entry", strategy.long)')
    assert receipt['unresolved'] == ['title' if declaration == 'strategy(TITLE)' else 'pyramiding']


@pytest.mark.parametrize('body', [
    'float value = na\nif true\n    symbol = "NASDAQ:MSFT"\n    value := request.security(symbol, "D", close)',
    'var string symbol = syminfo.tickerid\nif bar_index == 5\n    symbol := "NASDAQ:MSFT"\nvalue = request.security(symbol, "D", close)',
    'value = request.security(str.format("{0}:{1}", syminfo.prefix, syminfo.ticker), "D", close)',
    'inner() => request.security("NASDAQ:MSFT", "W", close)\nvalue = request.security(syminfo.tickerid, "D", inner())',
    'value = request.footprint(syminfo.tickerid, 100)',
])
def test_runtime_unpinned_requests_survive_lowering(body):
    receipt = emitted_receipt(f'//@version=6\nstrategy("unpinned")\n{body}\nif not na(value)\n    strategy.entry("entry", strategy.long)')
    requests = [request for request in receipt['requests'] if request['feed'] == 'unpinned']
    assert requests
    for request in requests:
        assert any(item.startswith(request['function'] + '[unpinned:') for item in receipt['unresolved'])


def test_lower_tf_records_foreign_symbol():
    receipt = emitted_receipt('//@version=6\nstrategy("lower")\nvalues = request.security_lower_tf("NASDAQ:MSFT", "1", close)\nif array.get(values, 0) > close\n    strategy.entry("entry", strategy.long)')
    assert all(request['symbol'] == 'NASDAQ:MSFT' for request in receipt['requests'])


@pytest.mark.parametrize('expression', ['barstate.isrealtime', 'timenow > time',
                                       'barstate.islast', 'barstate.islastconfirmedhistory',
                                       'last_bar_index == bar_index', 'last_bar_time == time'])
def test_realtime_approximations_are_named(expression):
    receipt = emitted_receipt(f'//@version=6\nstrategy("phase")\nif {expression}\n    strategy.entry("entry", strategy.long)')
    assert any(expression.split()[0] in name for name in receipt['unresolved'])


def test_capabilities_collect_facts_in_one_walk(monkeypatch):
    from pineforge_codegen.codegen import capabilities
    original = capabilities.iter_ast_nodes
    walks = []
    def counted(root):
        walks.append(root)
        yield from original(root)
    monkeypatch.setattr(capabilities, 'iter_ast_nodes', counted)
    emitted_receipt('//@version=6\nstrategy("walk")\nvarip int count = 0\nvalue = request.earnings("NASDAQ:MSFT")\nif value > count\n    strategy.entry("entry", strategy.long)')
    assert len(walks) == 1


@pytest.mark.parametrize('declaration', ['calc_on_every_tick=true', 'process_orders_on_close=true'])
def test_receipt_runtime_buffer_protocol_and_reused_handle(declaration):
    cpp = transpile(f'''//@version=6
strategy("runtime receipt", {declaration})
if bar_index % 3 == 0
    strategy.entry("entry", strategy.long)
''')
    driver = r'''
#include <cassert>
#include <cstring>
#include <iostream>
#include <vector>
std::string capability_receipt(void* strategy) {
    size_t required = 0;
    char error[32]{};
    assert(strategy_capabilities_receipt(strategy, nullptr, 0, &required, error, sizeof(error)) == PF_SETTINGS_BUFFER_TOO_SMALL);
    std::vector<char> output(required);
    assert(strategy_capabilities_receipt(strategy, output.data(), output.size(), &required, error, sizeof(error)) == PF_SETTINGS_OK);
    assert(error[0] == '\0' && output.back() == '\0' && strlen(output.data()) + 1 == required);
    return output.data();
}
int main() {
    assert(strategy_capabilities_api_version() == 1);
    auto strategy = strategy_create(nullptr);
    auto fresh = strategy_create(nullptr);
    const auto initial = capability_receipt(strategy);
    assert(capability_receipt(fresh) == initial);
    size_t required = 99;
    char tiny[2] = "x";
    char error[8]{};
    assert(strategy_capabilities_receipt(nullptr, tiny, sizeof(tiny), &required, error, sizeof(error)) == PF_SETTINGS_INVALID_ARGUMENT);
    assert(required == 0 && error[7] == '\0');
    assert(strategy_capabilities_receipt(strategy, tiny, sizeof(tiny), &required, error, sizeof(error)) == PF_SETTINGS_BUFFER_TOO_SMALL);
    assert(tiny[0] == '\0' && required == initial.size() + 1);
    std::vector<Bar> bars;
    for (int index = 0; index < 10; ++index)
        bars.push_back({100, 101, 99, 100, 5, 1577836800000LL + index * 60000LL});
    for (int run = 0; run < 2; ++run) {
        ReportC report{};
        run_backtest_full(strategy, bars.data(), static_cast<int>(bars.size()), "1", "1", 0, 4, 1, &report);
        report_free(&report);
        assert(capability_receipt(strategy) == initial);
    }
    strategy_set_override(strategy, "process_orders_on_close", "false");
    assert(capability_receipt(strategy) == initial);
    strategy_free(fresh);
    strategy_free(strategy);
    std::cout << initial;
}
'''
    receipt = json.loads(run_emitted_tu(cpp, driver, opt='-O0'))
    name = declaration.split('=')[0]
    assert receipt['declarations'][name] is True
