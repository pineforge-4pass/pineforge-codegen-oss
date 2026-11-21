"""Keep the string helpers used by a pre-rendered request timeframe."""

import math

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import Build, chart_feed_head, execute_all, same, skip_unless_e2e_env


HEADER = '//@version=6\nstrategy("Request timeframe string helpers")\n'
INPUT = 'input.timeframe("D")'
SUBSTRING = "str.substring({source}, 0, 1)"
CASES = [
    pytest.param("str.tostring(str.length({source}))",
                 "inline int _pf_u16_length(const std::string& text) {", id="length"),
    pytest.param('str.tostring(str.pos({source}, "D"))',
                 "inline int _pf_u16_pos(const std::string& text, const std::string& needle) {",
                 id="pos"),
    pytest.param('str.match({source}, "D")',
                 "inline std::string _pf_u8_match(const std::string& source, const std::string& pattern) {",
                 id="match"),
    pytest.param(SUBSTRING,
                 "inline std::string _pf_u16_substr_checked(const std::string& text, BeginT begin,",
                 id="substring"),
]


def _request(timeframe):
    return f"request.security(syminfo.tickerid, {timeframe}, close)"


@pytest.mark.parametrize("expression,definition", CASES)
@pytest.mark.parametrize("outside", [False, True], ids=["timeframe-only", "also-outside"])
def test_request_timeframe_emits_one_helper_block(expression, definition, outside):
    source = HEADER + "out = " + _request(expression.format(source=INPUT)) + "\n"
    if outside:
        source += "extra = " + expression.format(source="syminfo.tickerid") + "\n"
    cpp = transpile(source)
    compile_cpp(cpp, label=expression)
    assert cpp.splitlines().count(definition) == 1


def test_request_timeframe_without_a_unit_call_omits_the_block():
    cpp = transpile(HEADER + "out = " + _request(INPUT) + "\n")
    assert "_pf_u16_" not in cpp


def test_substring_timeframe_reads_the_literal_daily_close_on_every_bar(tmp_path):
    engine = skip_unless_e2e_env()
    bars = 500  # Several completed daily candles on the 15-minute chart.
    feed = chart_feed_head(engine, tmp_path, bars)
    source = (HEADER
              + "computed = " + _request(SUBSTRING.format(source=INPUT)) + "\n"
              + "literal = " + _request('"D"') + "\n"
              + "// @pf-trace computed=computed\n// @pf-trace literal=literal\n")
    run = execute_all(engine, feed, tmp_path / "runs", {"daily": Build(source, trace=True)})["daily"]
    assert run.error is None, run.error
    records = run.traces["default"]
    computed, literal = (
        {r["bar_index"]: r["value"] for r in records if r["name"] == name}
        for name in ("computed", "literal")
    )
    assert len(literal) == bars and literal.keys() == computed.keys()
    assert any(math.isfinite(value) for value in literal.values())
    for bar, value in literal.items():
        assert same(computed[bar], value), (bar, computed[bar], value)
