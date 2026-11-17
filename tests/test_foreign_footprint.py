"""A footprint of another symbol reads its pinned feed's delta column.

``request.security(sym, tf, request.footprint(ticks, va))`` followed by
``.delta()`` (or ``footprint.delta(fp)``) is the footprint delta of the
requested bar. The requests manifest pins it as a named column of the
symbol's feed, ``fp_delta_<ticks>_<va>``, and the engine hands
a foreign site its bar's column (``security_column_value``). It
was a deferred refusal, and ``fp.delta()`` did not compile (a double has no
member). A footprint value is now its delta; every other ``footprint.*``
member stays refused by name, and a feed without the column is missing data:
the request's reads stop the run.

TradingView's footprint delta of a range depends on the range (month slices
of one year disagree), so a feed is pinned for the window it was captured
over: a synthetic BITSTAMP:BTCUSD delta filter on BINANCE:BTCUSDT 15 over
the capture's year books its 1674 trades exactly on it (kept outside this
repository with the capture).
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.errors import CompileError
from tests._compile import compile_cpp, run_emitted_tu
from tests._cpp_tokens import string_values
from tests._e2e import chart_feed_head, skip_unless_e2e_env
from tests._requests import build, hourly, run, same, write_root


HEAD = '//@version=6\nstrategy("xe footprint", overlay = true)\n'
REQUEST = 'footprint fp = request.security("PF:A", "15", request.footprint(100, 70))\n'
TRADE = ('if bar_index % 2 == 0 and d > 0\n    strategy.entry("L", strategy.long)\n'
         'if bar_index % 2 == 1\n    strategy.close("L")\n')
PINNED = "no data is pinned for this request, and its value was read"
M15 = 15 * 60_000


def test_footprint_delta_reads_the_feed_column():
    result = transpile_full(HEAD + REQUEST + 'float d = not na(fp) ? fp.delta() : na\n'
                            'float d2 = footprint.delta(fp)\n' + TRADE
                            + 'if d2 < 0\n    strategy.close("L")\n')
    cpp = result["cpp"]
    assert '_req_sec_0 = _pf_symbol_column(0, "fp_delta_100_70");' in cpp
    assert '_pf_symbol_data_installed(_pf_symbol, "15", "fp_delta_100_70")' in cpp
    assert "double fp = " in cpp and ".delta()" not in cpp
    compile_cpp(cpp, label="foreign-footprint-delta")


@pytest.mark.parametrize("member", ["poc()", "buy_volume()", "rows()"])
def test_other_footprint_members_stay_refused_by_name(member):
    with pytest.raises(CompileError, match=re.escape(f"footprint.{member.split('(')[0]}(...) "
                                                     "is not supported.")):
        transpile(HEAD + REQUEST + f'd = fp.{member}\n' + TRADE)
    with pytest.raises(CompileError, match="footprint"):
        transpile(HEAD + REQUEST + f'd = footprint.{member.split("(")[0]}(fp)\n' + TRADE)


def test_footprint_of_non_literal_rows_keeps_the_deferred_refusal():
    """The column's name spells the ticks per row: a series or input keys
    none, and the request stays a deferred refusal (it transpiles)."""
    result = transpile_full(HEAD + 'rows = input.int(100, "Rows")\n'
                            'footprint fp = request.security("PF:A", "15", '
                            'request.footprint(rows, 70))\nd = fp.delta()\n' + TRADE)
    assert any("no data is pinned" in d.message for d in result["diagnostics"])
    assert "register_security_eval" not in result["cpp"]


def test_footprint_delta_on_the_requested_bars(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_footprint")
    feed_path = chart_feed_head(engine, base, 160)
    opens = [int(line.split(",", 1)[0]) for line in feed_path.read_text().splitlines()[1:]]
    other = hourly("PF:A", opens[0] - 2 * M15, 170, 15)
    deltas = [round(((i * 53) % 29) - 14.5 + i * 0.001, 6) for i in range(len(other.bars))]
    other.columns["fp_delta_100_70"] = deltas
    source = ("// @pf-trace pf_d=d\n" + HEAD + REQUEST
              + 'float d = not na(fp) ? fp.delta() : na\n' + TRADE)
    work = base / "xe-footprint"
    build(source, work)
    result = run(engine, work, feed_path, write_root(base, "xe-footprint", "BINANCE:ETHUSDT",
                                                    "15", [other]))
    assert result.ok, result.error
    for chart_open in opens:
        visible = [i for i, bar in enumerate(other.bars) if bar[1] <= chart_open + M15]
        want = deltas[visible[-1]] if visible else float("nan")
        assert same(result.trace[chart_open]["pf_d"], want), chart_open
    # A feed of the symbol without the column is missing data.
    del other.columns["fp_delta_100_70"]
    result = run(engine, work, feed_path, write_root(base / "bare", "xe-footprint",
                                                    "BINANCE:ETHUSDT", "15", [other]))
    assert not result.ok
    # Line 4: the trace pragma is line 1.
    assert (f'request.security("PF:A", "15", ...) at line 4: {PINNED}') in result.error


@pytest.mark.parametrize("body", [
    'getfp() => request.security("PF:A", "15", request.footprint(100, 70))\n'
    'fp = getfp()\nd = fp.delta()\n',
    REQUEST + 'f(footprint x) => x.delta()\nd = f(fp)\n',
    REQUEST + 'g(x) => footprint.delta(x)\nd = g(fp)\n',
])
def test_footprint_through_a_helper_or_a_parameter(body):
    """A helper returning a footprint, and a parameter a footprint is passed
    to (typed ``footprint`` or not), are footprint values: ``.delta()`` on
    them emitted a member call on a double, which did not compile."""
    cpp = transpile(HEAD + body + TRADE)
    assert ".delta()" not in cpp and '_pf_symbol_column(0, "fp_delta_100_70")' in cpp
    compile_cpp(cpp, label="footprint-helper")


def test_footprint_of_the_charts_own_symbol_reads_its_feed(tmp_path_factory):
    """The chart's bars carry no footprint: a footprint request of the
    chart's own symbol reads that symbol's feed column too. It registered on
    the chart, where the column reads na, and silently took no trades."""
    source = (HEAD + 'footprint fp = request.security("BINANCE:ETHUSDT", "15", '
              'request.footprint(100, 70))\nfloat d = not na(fp) ? fp.delta() : na\n' + TRADE)
    assert "_pf_symbol_is_chart(_pf_symbol)" not in transpile(source)
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xe_footprint_chart")
    feed_path = chart_feed_head(engine, base, 160)
    opens = [int(line.split(",", 1)[0]) for line in feed_path.read_text().splitlines()[1:]]
    own = hourly("BINANCE:ETHUSDT", opens[0] - 2 * M15, 170, 15)
    deltas = [round(((i * 41) % 31) - 15.5 + i * 0.001, 6) for i in range(len(own.bars))]
    own.columns["fp_delta_100_70"] = deltas
    work = base / "xe-footprint-chart"
    build("// @pf-trace pf_d=d\n" + source, work)
    result = run(engine, work, feed_path, write_root(base, "xe-footprint-chart",
                                                    "BINANCE:ETHUSDT", "15", [own]))
    assert result.ok, result.error
    for chart_open in opens:
        visible = [i for i, bar in enumerate(own.bars) if bar[1] <= chart_open + M15]
        assert same(result.trace[chart_open]["pf_d"], deltas[visible[-1]]), chart_open
    result = run(engine, work, feed_path, None, tag="none")
    assert not result.ok and PINNED in result.error


def _engine_refuses_column(name: str) -> bool:
    """The engine's rule for the name of an instrument-feed column: required,
    so not empty, and without a NUL (every name below is valid UTF-8)."""
    return name == "" or "\0" in name


@pytest.mark.parametrize("names", [
    ("fp\0left", "fp\0right", "fp"),
    ("fp\x01left", "fp\x01right", "fp"),
    ("fpleft", "fpright", "fp"),
    ("é\0中😀left", "é\0中😀right", "é"),
    ("", "suffix", "missing"),
], ids=["nul", "other-control", "ascii", "unicode-nul", "empty-name"])
def test_generated_column_helpers_compare_whole_names(names):
    """Internal serializer/lookup contract; public Pine produces ASCII names.

    Two contracts, kept apart. The emitted literal carries the whole name and
    the emitted lookup compares whole names: that holds for every row. A feed
    the engine runs is the other one: it refuses a column named by the empty
    string or holding a NUL, so a run over such a column is that refusal, by
    its field, and only the other rows read their columns through a run."""
    from pineforge_codegen import CodeGen, _generate
    from pineforge_codegen.external_requests import FOOTPRINT_COLUMN_ANNOTATION

    # Supply an internal annotation as serializer input, after normal checking.
    # No Pine source or public footprint-name producer is changed or bypassed.
    _, context, _, _, _ = _generate(HEAD + REQUEST + "d = fp.delta()\n", True, "<input>")
    (site,) = context.security_calls
    assert site.expression.annotations[FOOTPRINT_COLUMN_ANNOTATION] == "fp_delta_100_70"
    site.expression.annotations[FOOTPRINT_COLUMN_ANNOTATION] = names[0]
    cpp = CodeGen(context).generate()
    registration = next(line for line in cpp.splitlines()
                        if "if (_pf_symbol_data_installed(" in line)
    payload = next(line for line in cpp.splitlines() if "_req_sec_0 = _pf_symbol_column(" in line)
    for line in (registration, payload):
        assert names[0] in string_values(line)
        if "\0" in names[0]:
            counted = re.findall(r'std::string\(("(?:[^"\\]|\\.)*"), (\d+)\)', line)
            assert any(
                string_values(literal) == [names[0]]
                and int(count) == len(names[0].encode("utf-8"))
                for literal, count in counted
            ), line

    def native_bytes(value):
        return "std::string{" + ", ".join(
            f"static_cast<char>({byte})" for byte in value.encode("utf-8")
        ) + "}"

    body = "\n".join(
        f'check("name{index}", probe._pf_symbol_data_installed("PF:A", "15", '
        f'{native_bytes(name)}));'
        for index, name in enumerate(names)
    )
    driver = r'''
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <type_traits>
#include <vector>

class ColumnProbe : public GeneratedStrategy {
public:
    using GeneratedStrategy::_pf_symbol_data_installed;
    using GeneratedStrategy::_pf_symbol_column;

    // The two columns go into the host's feed store, which the emitted lookup
    // reads, under any name. Whether the engine runs a feed holding them is
    // its own judgement of the names, made when the run begins.
    void install(const std::string& left, const std::string& right,
                 const std::vector<pineforge::Bar>& bars,
                 const std::vector<int64_t>& closes) {
        if (!set_symbol_feed("PF:A", "15", bars.data(), closes.data(), bars.size()))
            throw std::runtime_error("could not install column test feed");
        auto& feed = symbol_feeds_.back();
        feed.columns.emplace_back();
        feed.columns.back().name = left;
        feed.columns.back().values = {1.25, -4.5, 19.75};
        feed.columns.emplace_back();
        feed.columns.back().name = right;
        feed.columns.back().values = {7.0, 11.0, -23.5};
    }
};

static_assert(std::is_invocable_r_v<double, decltype(&ColumnProbe::_pf_symbol_column),
                                   const ColumnProbe&, int, const std::string&>);

void check(const char* name, bool found) {
    std::cout << name << "=" << found << "\n";
}

int main() {
    std::vector<pineforge::Bar> bars(3);
    std::vector<int64_t> closes;
    for (std::size_t i = 0; i < bars.size(); ++i) {
        bars[i].open = bars[i].low = 90.0 + double(i);
        bars[i].high = 110.0 + double(i);
        bars[i].close = 100.0 + double(i);
        bars[i].volume = 1.0;
        bars[i].timestamp = 1704067200000LL + static_cast<int64_t>(i) * 900000LL;
        closes.push_back(bars[i].timestamp + 900000LL);
    }
    ColumnProbe probe;
    probe.install(LEFT, RIGHT, bars, closes);
    // BODY
    check("no-column-filter", probe._pf_symbol_data_installed("PF:A", "15"));
    check("empty-name", probe._pf_symbol_data_installed("PF:A", "15", std::string{}));
    check("other-symbol", probe._pf_symbol_data_installed("PF:B", "15", LEFT));
    check("other-timeframe", probe._pf_symbol_data_installed("PF:A", "60", LEFT));
    check("unknown-context-na", std::isnan(probe._pf_symbol_column(-1, LEFT)));
    probe.run(bars.data(), bars.size(), "15", "15");
    // The run's own record, whole: nothing is caught and nothing is rethrown.
    std::cout << "run-error=" << probe.last_error() << "\n";
    std::cout << "run-code=" << pineforge::run_failure_code_of(probe) << "\n";
    std::cout << "run-args=" << pineforge::run_failure_args_of(probe) << "\n";
    if (probe.last_error().empty()) {
        std::cout << "payload=" << probe.d << "\n";
        std::cout << "left=" << probe._pf_symbol_column(0, LEFT) << "\n";
        std::cout << "right=" << probe._pf_symbol_column(0, RIGHT) << "\n";
    } else {
        check("left-na", std::isnan(probe._pf_symbol_column(0, LEFT)));
        check("right-na", std::isnan(probe._pf_symbol_column(0, RIGHT)));
    }
    check("unknown-column-na", std::isnan(probe._pf_symbol_column(0, MISSING)));
}
'''.replace("LEFT", native_bytes(names[0])).replace("RIGHT", native_bytes(names[1]))
    driver = driver.replace("MISSING", native_bytes(names[2])).replace("// BODY", body)
    output = run_emitted_tu(cpp, driver, opt="-O0", label="complete column names")
    # The emitted lookup, for every row: each installed name is found whole,
    # and neither its prefix nor another feed's key is.
    lookup = [
        "name0=1", "name1=1", "name2=0", "no-column-filter=1",
        f"empty-name={int(names[0] == '')}", "other-symbol=0", "other-timeframe=0",
        "unknown-context-na=1",
    ]
    # The run, by the engine's rule for the two installed names.
    if any(_engine_refuses_column(name) for name in names[:2]):
        # The engine judges the feed before the first bar and names the field:
        # 70 is the run spec's instrument-feed columns. No site is registered,
        # so no column value is delivered.
        outcome = [
            "run-error=Pine adapter produced invalid native run spec field 70",
            "run-code=symbol_feeds_refused",
            'run-args={"field":"instrument_feed_columns","reason":"kernel_refused_series"}',
            "left-na=1", "right-na=1",
        ]
    else:
        # An accepted feed: the payload reads the last bar's value of the
        # first column, and each lookup its own column's; the two differ.
        outcome = ["run-error=", "run-code=", "run-args=",
                   "payload=19.75", "left=19.75", "right=-23.5"]
    assert output.splitlines() == lookup + outcome + ["unknown-column-na=1"]
