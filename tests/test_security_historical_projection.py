"""Historical projection is an explicit option of eligible chart registrations."""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.ast_nodes import (
    BoolLiteral, Identifier, MemberAccess, StringLiteral, Ternary,
)
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.codegen.security import SecurityEmitter
from pineforge_codegen.errors import Phase, SourceLocation
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser


PRELUDE = '//@version=6\nstrategy("historical security")\n'
PROJECTED = (
    'projected = request.security(syminfo.tickerid, "2D", close, '
    'lookahead=barmerge.lookahead_on)\n'
)
TRADE = 'if projected > control\n    strategy.entry("L", strategy.long)\n'


def _registrations(cpp: str) -> list[str]:
    return [
        line.strip() for line in cpp.splitlines()
        if line.strip().startswith((
            "register_security_eval(", "register_security_lower_tf_eval(",
        ))
    ]


@pytest.mark.parametrize("control, expected", [
    pytest.param(
        'control = request.security(syminfo.tickerid, "240", close)\n',
        # Omitted lookahead is off, so the five-argument call stays unchanged.
        ['register_security_eval(1, "240", input_tf_, false, false);'],
        id="default-lookahead"),
    pytest.param(
        'control = request.security(syminfo.tickerid, "3", close, '
        'lookahead=barmerge.lookahead_off)\n',
        # Explicit lookahead_off keeps the existing ordinary request contract.
        ['register_security_eval(1, "3", input_tf_, false, false);'],
        id="lookahead-off"),
    pytest.param(
        'control = request.security(syminfo.tickerid, "240", close, '
        'gaps=barmerge.gaps_on, lookahead=barmerge.lookahead_on)\n',
        # gaps_on keeps its publication mode without an added option.
        ['register_security_eval(1, "240", input_tf_, true, true);'],
        id="gaps-on"),
    pytest.param(
        'control = request.security(ticker.heikinashi(syminfo.tickerid), '
        '"240", close, lookahead=barmerge.lookahead_on)\n',
        # The sixth true still means Heikin-Ashi, with no projection argument.
        ['register_security_eval(1, "240", input_tf_, true, false, true);'],
        id="heikin-ashi"),
    pytest.param(
        'control = request.security(syminfo.tickerid, "", close, '
        'lookahead=barmerge.lookahead_on)\n',
        # An empty timeframe already resolves to the input timeframe.
        ['register_security_eval(1, input_tf_, input_tf_, true, false);'],
        id="empty-timeframe"),
    pytest.param(
        'control = request.security(syminfo.tickerid, timeframe.period, close, '
        'lookahead=barmerge.lookahead_on)\n',
        # The script timeframe is known to be the chart timeframe.
        ['register_security_eval(1, script_tf_, input_tf_, true, false);'],
        id="chart-timeframe"),
    pytest.param(
        'bars = request.security_lower_tf(syminfo.tickerid, "3", close)\n'
        'control = array.size(bars)\n',
        # Intrabar arrays retain their separate three-argument registration.
        ['register_security_lower_tf_eval(1, "3", input_tf_);'],
        id="lower-timeframe-array"),
    pytest.param(
        'enabled = input.bool(true, "Timing")\n'
        'mode = enabled ? barmerge.lookahead_off : barmerge.lookahead_off\n'
        'control = request.security(syminfo.tickerid, "240", close, lookahead=mode)\n',
        # Neither result arm enables lookahead, even though its input is unknown.
        ['register_security_eval(1, "240", input_tf_, '
         'static_cast<bool>(((get_input_bool("Timing", true)) ? (0) : (0))), '
         'false);'],
        id="computed-off-only"),
    pytest.param(
        'control = request.security(syminfo.tickerid, "240", close, '
        'lookahead=false ? barmerge.lookahead_on : barmerge.lookahead_off)\n',
        # A constant false condition makes the on arm unreachable.
        ['register_security_eval(1, "240", input_tf_, '
         'static_cast<bool>(((false) ? (1) : (0))), false);'],
        id="unreachable-lookahead-on"),
])
def test_projection_option_is_selected_per_request(control, expected):
    cpp = transpile(PRELUDE + PROJECTED + control + TRADE)
    # Ordinary chart OHLC with lookahead_on/gaps_off needs false HA, true projection.
    assert _registrations(cpp) == [
        'register_security_eval(0, "2D", input_tf_, true, false, false, true);',
        *expected,
    ]


@pytest.mark.parametrize("declarations, expected", [
    pytest.param(
        'mode = barmerge.lookahead_on\n',
        # A resolved enum alias is on; the timeframe still reads its input getter.
        'register_security_eval(0, get_input_string("Period", std::string("180")), '
        'input_tf_, true, false, false, true);',
        id="enum-alias"),
    pytest.param(
        'enabled = input.bool(false, "Future")\n'
        'mode = enabled ? barmerge.lookahead_on : barmerge.lookahead_off\n',
        # The runtime input chooses lookahead; projection is eligible only when on.
        'register_security_eval(0, get_input_string("Period", std::string("180")), '
        'input_tf_, static_cast<bool>(((get_input_bool("Future", false)) ? (1) : (0))), '
        'false, false, true);',
        id="input-on-off"),
    pytest.param(
        'enabled = input.bool(true, "Future")\n'
        'mode = enabled ? barmerge.lookahead_off : barmerge.lookahead_on\n',
        # Reversing the enum arms must preserve the authored lookahead expression.
        'register_security_eval(0, get_input_string("Period", std::string("180")), '
        'input_tf_, static_cast<bool>(((get_input_bool("Future", true)) ? (0) : (1))), '
        'false, false, true);',
        id="input-off-on"),
    pytest.param(
        'enabled = input.bool(false, "Future")\n'
        'selectedMode = if enabled\n'
        '    barmerge.lookahead_on\n'
        'else\n'
        '    barmerge.lookahead_off\n'
        'mode = selectedMode\n',
        # Block selection and its alias retain the input getter and enum arms.
        'register_security_eval(0, get_input_string("Period", std::string("180")), '
        'input_tf_, static_cast<bool>(((get_input_bool("Future", false)) ? (1) : (0))), '
        'false, false, true);',
        id="block-enum-alias"),
])
def test_projection_uses_resolved_timeframe_and_lookahead(declarations, expected):
    cpp = transpile(
        PRELUDE + 'requestedTf = input.timeframe("180", "Period")\n'
        + declarations
        + 'projected = request.security(syminfo.tickerid, requestedTf, close, '
        'barmerge.gaps_off, mode)\n'
        'control = close\n' + TRADE)
    assert _registrations(cpp) == [expected]


def test_symbol_routing_projects_only_the_chart_registration():
    cpp = transpile(
        PRELUDE
        + 'other = input.symbol("TEST:ALT", "Other")\n'
        'ignore = input.bool(true, "Ignore")\n'
        'projected = request.security(other, "2W", close, '
        'lookahead=barmerge.lookahead_on, ignore_invalid_symbol=ignore)\n'
        'control = close\n' + TRADE)
    # The chart arm has false HA/true projection; the feed arm keeps ignore-invalid.
    assert _registrations(cpp) == [
        'register_security_eval(0, "2W", input_tf_, true, false, false, true);',
        'register_security_eval(0, _pf_symbol, "2W", input_tf_, true, false, '
        'static_cast<bool>(get_input_bool("Ignore", true)));',
    ]
    assert (
        'if (_pf_symbol_is_chart(_pf_symbol)) {\n'
        '                register_security_eval(0, "2W", input_tf_, true, false, false, true);'
    ) in cpp
    assert 'const std::string _pf_symbol = get_input_string("Other", std::string("TEST:ALT"));' in cpp


@pytest.mark.parametrize("request_source", [
    'request.security(syminfo.tickerid, "D", close, '
    'lookahead=barmerge.lookahead_on)',
    'request.security(symbol=syminfo.tickerid, timeframe="D", expression=close, '
    'lookahead=barmerge.lookahead_on)',
], ids=["positional", "named"])
def test_projection_registration_does_not_rewrite_payload_offsets(request_source):
    cpp = transpile(
        PRELUDE
        + f'projected = {request_source}\n'
        'control = request.security(syminfo.tickerid, "D", close[1], '
        'lookahead=barmerge.lookahead_on)\n'
        '[weekHigh, weekLow] = request.security(symbol=syminfo.tickerid, '
        'timeframe="W", expression=[high, low], lookahead=barmerge.lookahead_on)\n'
        'if projected > control and weekHigh > weekLow\n'
        '    strategy.entry("L", strategy.long)\n')
    # Scalar, offset and tuple payloads share the same chart registration contract.
    assert _registrations(cpp) == [
        'register_security_eval(0, "D", input_tf_, true, false, false, true);',
        'register_security_eval(1, "D", input_tf_, true, false, false, true);',
        'register_security_eval(2, "W", input_tf_, true, false, false, true);',
    ]
    # Read the preceding completed bucket before committing this bucket's close.
    assert (
        '        _req_sec_1 = _sec1_hist_close[0];\n'
        '        if (is_complete) {\n'
        '            _sec1_hist_close.push(bar.close);\n'
        '        }'
    ) in cpp
    assert '_req_sec_0 = bar.close;' in cpp
    assert '_req_sec_2_0 = bar.high;' in cpp
    assert '_req_sec_2_1 = bar.low;' in cpp


def test_runtime_timeframe_keeps_its_cursor_beside_a_projected_site():
    cpp = transpile(
        PRELUDE + PROJECTED
        + 'chosenTf = str.tostring(60 + bar_index)\n'
        'control = request.security(syminfo.tickerid, chosenTf, close, '
        'lookahead=barmerge.lookahead_on)\n' + TRADE)
    # Runtime contexts do not register fixed sites; only the ordinary site opts in.
    assert _registrations(cpp) == [
        'register_security_eval(0, "2D", input_tf_, true, false, false, true);',
    ]
    assert 'make_runtime_security_cursor(tf, lookahead, gaps, lower, heikinashi)' in cpp
    assert '_runtime_request_1(chosenTf, 1, false)' in cpp


@pytest.mark.parametrize("with_budget", [False, True], ids=["no-budget", "budget"])
def test_projection_selection_accepts_optional_codegen_budget(with_budget):
    location = SourceLocation(file="<input>", line=3, col=1, end_col=8)
    choices = [
        MemberAccess(
            object=Identifier(name="barmerge", loc=location),
            member=name, loc=location,
        )
        for name in ("lookahead_on", "lookahead_off")
    ]
    value = Ternary(
        condition=BoolLiteral(value=True, loc=location),
        true_val=choices[0], false_val=choices[1], loc=location,
    )
    checks = []

    class Budget:
        def check(self, location=None, phase=Phase.PARSER):
            checks.append((location, phase))

    emitter = SecurityEmitter()
    emitter._budget = Budget() if with_budget else None
    assert emitter._security_historical_projection_arg(
        {"lookahead_value": value}, '"D"',
    ) == ", false, true"
    if with_budget:
        assert checks
        assert all(at == location and phase is Phase.CODEGEN for at, phase in checks)


@pytest.mark.parametrize("metadata, lookahead, projection", [
    pytest.param({}, "false", "", id="omitted-lookahead"),
    pytest.param(
        {"lookahead_value": None}, "false", "", id="null-lookahead-value"),
    pytest.param(
        {"lookahead_on": False}, "false", "", id="explicit-off"),
    pytest.param(
        {"lookahead_on": False, "lookahead_value": None},
        "false", "", id="explicit-off-null-value"),
    pytest.param(
        {"lookahead_on": True}, "true", ", false, true", id="explicit-on"),
    pytest.param(
        {"lookahead_on": True, "lookahead_value": None},
        "true", ", false, true", id="explicit-on-null-value"),
])
def test_sparse_lookahead_metadata_preserves_registration_contract(
    metadata, lookahead, projection,
):
    # Analyze a script without requests so the supplied metadata stays sparse.
    source = PRELUDE + "control = close\n"
    ast = Parser(Lexer(source).tokenize(), source=source).parse()
    gen = CodeGen(Analyzer(ast).analyze())
    location = SourceLocation(file="<input>", line=3, col=1, end_col=8)
    info = {
        "sec_id": 3,
        "symbol_node": StringLiteral(value="TEST:OTHER", loc=location),
        **metadata,
    }
    assert gen._security_historical_projection_arg(info, '"120"') == projection

    lines = []
    gen._emit_foreign_security_registration(
        info, '"120"', lookahead, "false", lines,
    )
    # Default/off keeps the five-argument chart call. Explicit on opts only
    # that call into projection; the feed call retains ignore-invalid=false.
    assert _registrations("\n".join(lines)) == [
        f'register_security_eval(3, "120", input_tf_, {lookahead}, false{projection});',
        f'register_security_eval(3, _pf_symbol, "120", input_tf_, {lookahead}, false, false);',
    ]
