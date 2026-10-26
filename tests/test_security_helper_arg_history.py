"""Requested-bar history of an unconditionally called helper's argument.

The hourly inputs and arithmetic below are independent of generated output.
Every helper call in these Pine scripts runs on every requested bar; sparse
calls and branches around calls are deliberately outside this contract.

Runtime probes reuse the native synthetic-bar harness, including its engine
skip guard, from test_security_helper_local_var. A skip is not a runtime
verdict: the supervisor must run these cases with the paired built engine.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import NamedTuple

import pytest

from pineforge_codegen import transpile
from tests.test_security_helper_local_var import _compile_and_run, _eval_body


_REPORTER = """//@version=6
strategy('Helper history')
f(x) => x[2] - x
a = ta.sma(close, 3)
b = f(a)
c = request.security(syminfo.tickerid, '60', b)
plot(c)
"""

_HOURLY_OPENS = (1.0, 3.0, 5.0, 7.0, 9.0)
_CHART_CLOSES = (
    (1.0, 2.0, 3.0, 3.0),
    (4.0, 5.0, 6.0, 6.0),
    (7.0, 8.0, 9.0, 9.0),
    (10.0, 11.0, 12.0, 12.0),
    (13.0, 14.0, 15.0, 15.0),
)
_HOUR_MS = 60 * 60 * 1000


class _Evaluation(NamedTuple):
    timestamp: int
    complete: bool
    open: float
    close: float
    value: float


def _hourly_closes() -> list[float]:
    return [children[-1] for children in _CHART_CLOSES]


def _body_samples() -> list[float]:
    return [close - open_ for open_, close in zip(_HOURLY_OPENS, _hourly_closes())]


def _sma(samples: list[float], length: int) -> list[float]:
    """Cold Pine SMA: retain the warmup bars as na-valued samples."""
    return [
        math.nan if i + 1 < length else sum(samples[i + 1 - length : i + 1]) / length
        for i in range(len(samples))
    ]


def _history(samples: list[float], offset: int) -> list[float]:
    """All calls here execute once per requested slot, including na samples."""
    return [
        samples[i - offset] if i >= offset else math.nan
        for i in range(len(samples))
    ]


def _difference(samples: list[float], offset: int = 2) -> list[float]:
    return [past - current for past, current in zip(_history(samples, offset), samples)]


def _assert_values(actual: list[float], expected: list[float]) -> None:
    assert len(actual) == len(expected), (actual, expected)
    for i, (observed, wanted) in enumerate(zip(actual, expected)):
        if math.isnan(wanted):
            assert math.isnan(observed), f"slot {i}: expected na, got {observed}"
        else:
            assert not math.isnan(observed), f"slot {i}: expected {wanted}, got na"
            assert observed == pytest.approx(wanted), f"slot {i}"


def _source(definitions: str, payload: str, *, lookahead: str = "off") -> str:
    return (
        '//@version=6\nstrategy("requested helper argument history")\n'
        + definitions
        + "\nc = request.security(syminfo.tickerid, '60', "
        + payload
        + f", lookahead=barmerge.lookahead_{lookahead})\nplot(c)\n"
    )


_OBSERVER = r"""
#include <iomanip>
#include <iostream>

struct RequestedHistoryObserver : GeneratedStrategy {
    const char* tag = "";

    void evaluate_security(int sec_id, const Bar& bar, bool is_complete) override {
        GeneratedStrategy::evaluate_security(sec_id, bar, is_complete);
        if (sec_id == 0) {
            std::cout << tag << ' ' << bar.timestamp << ' ' << is_complete
                      << ' ' << bar.open << ' ' << bar.close
                      << ' ' << _req_sec_0 << '\n';
        }
    }
};

int main() {
    std::cout << std::setprecision(17);
"""


def _run_requested(
    cpp: str,
    feeds: dict[str, tuple[int, tuple[tuple[float, ...], ...]]] | None = None,
) -> dict[str, list[_Evaluation]]:
    """Observe the real requested evaluator during the normal native run.

    Each feed is (chart minutes, five rows of chart closes). The first child
    opens at its specified hourly open; later children open at the previous
    child close. These valid OHLC bars aggregate to the inputs of the oracle.
    No engine callbacks, slot flags or generated histories are mocked.
    """
    if feeds is None:
        feeds = {"chart15": (15, _CHART_CLOSES)}
    runs = []
    for tag, (minutes, hourly_children) in feeds.items():
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", tag)
        assert len(hourly_children) == len(_HOURLY_OPENS)
        bars = []
        for hour, (open_, children) in enumerate(zip(_HOURLY_OPENS, hourly_children)):
            assert len(children) * minutes == 60
            for child, close in enumerate(children):
                timestamp = hour * _HOUR_MS + child * minutes * 60 * 1000
                fields = (open_, max(open_, close), min(open_, close), close, 1.0)
                bars.append(
                    "        Bar{"
                    + ", ".join(repr(value) for value in fields)
                    + f", {timestamp}}}"
                )
                open_ = close
        runs.append(
            "    {\n"
            "        RequestedHistoryObserver strategy;\n"
            f'        strategy.tag = "{tag}";\n'
            "        Bar bars[] = {\n"
            + ",\n".join(bars)
            + "\n        };\n"
            + f'        strategy.run(bars, {len(bars)}, "{minutes}", "{minutes}");\n'
            + "    }\n"
        )
    output = _compile_and_run(cpp + _OBSERVER + "".join(runs) + "    return 0;\n}\n")
    evaluations: dict[str, list[_Evaluation]] = {tag: [] for tag in feeds}
    for line in output.splitlines():
        fields = line.split()
        assert len(fields) == 6, f"unexpected native probe output: {line!r}"
        tag, timestamp, complete, open_, close, value = fields
        assert tag in evaluations, line
        assert complete in {"0", "1"}, line
        evaluations[tag].append(
            _Evaluation(
                int(timestamp), complete == "1", float(open_), float(close), float(value)
            )
        )
    return evaluations


def _assert_completed(rows: list[_Evaluation], expected: list[float]) -> None:
    completed = [row for row in rows if row.complete]
    assert [row.timestamp for row in completed] == [
        hour * _HOUR_MS for hour in range(len(_HOURLY_OPENS))
    ], rows
    _assert_values([row.open for row in completed], list(_HOURLY_OPENS))
    _assert_values([row.close for row in completed], _hourly_closes())
    _assert_values([row.value for row in completed], expected)


def _assert_sampled_sites(cpp: str, count: int) -> None:
    """Count sampling cells by operations, without prescribing their names.

    These helpers have no locals or persistent state. TA uses compute /
    recompute; the old completed-field histories only push. A target with
    both push and update in this evaluator is therefore an argument sample.
    Re-reading a bound result must not emit another sampling operation.
    """
    body = _eval_body(cpp, 0, None)
    operations = re.findall(r'(\b\w+(?:\["[^"]+"\])?)\.(push|update)\s*\(', body)
    pushes = Counter(target for target, operation in operations if operation == "push")
    updates = Counter(target for target, operation in operations if operation == "update")
    sampled = pushes.keys() & updates.keys()
    assert len(sampled) == count, body
    for target in sampled:
        assert pushes[target] == updates[target] == 1, body


def test_exact_reporter_transpiles():
    # Base 063c4e30 raises at security.py:1722, before any native harness.
    cpp = transpile(_REPORTER)
    assert "class GeneratedStrategy" in cpp


def test_reporter_uses_requested_sma_and_preserves_na_argument_samples():
    cpp = transpile(_REPORTER)
    _assert_sampled_sites(cpp, 1)
    # Hourly SMA samples are na,na,6,9,12. Their two-call differences are
    # na,na,na,na,6-12. In contrast the last chart SMA is (14+15+15)/3.
    samples = _sma(_hourly_closes(), 3)
    _assert_completed(_run_requested(cpp)["chart15"], _difference(samples))


@pytest.mark.parametrize(
    "definitions,payload",
    [
        pytest.param("f(x) => x[2] - x\n", "f(close - open)", id="inline-arithmetic"),
        pytest.param("f(x) => x[2] - x\nd = close - open\n", "f(d)", id="bound-arithmetic"),
    ],
)
def test_arithmetic_argument_history_uses_requested_values(definitions, payload):
    cpp = transpile(_source(definitions, payload))
    _assert_sampled_sites(cpp, 1)
    # Samples 2,3,4,5,6 yield na,na,2-4,3-5,4-6.
    _assert_completed(_run_requested(cpp)["chart15"], _difference(_body_samples()))


def test_nested_helper_result_is_sampled_on_requested_bars():
    cpp = transpile(
        _source("f(x) => x[2] - x\ng(y) => y * 2\n", "f(g(close))")
    )
    _assert_sampled_sites(cpp, 1)
    # g supplies 6,12,18,24,30; two-call differences are na,na,-12,-12,-12.
    samples = [2 * close for close in _hourly_closes()]
    _assert_completed(_run_requested(cpp)["chart15"], _difference(samples))


@pytest.mark.parametrize(
    "payload,scale",
    [
        pytest.param("f(d) + f(2*d)", 2, id="different-arguments"),
        pytest.param("f(d) + f(d)", 1, id="equal-arguments-separate-sites"),
    ],
)
def test_two_written_calls_keep_independent_argument_histories(payload, scale):
    cpp = transpile(_source("f(x) => x[2] - x\nd = close - open\n", payload))
    # Equal arguments must still allocate two cells. Their numeric sum
    # alone cannot distinguish two sites from an incorrectly shared cache.
    _assert_sampled_sites(cpp, 2)
    first = _difference(_body_samples())
    second = _difference([scale * sample for sample in _body_samples()])
    expected = [left + right for left, right in zip(first, second)]
    # After two na slots: (-2)+(-4)=-6, or (-2)+(-2)=-4.
    _assert_completed(_run_requested(cpp)["chart15"], expected)


def test_two_reads_of_one_bound_call_sample_its_argument_once():
    cpp = transpile(
        _source("f(x) => x[2] - x\nd = close - open\nb = f(d)\n", "b + b")
    )
    _assert_sampled_sites(cpp, 1)
    # b has samples na,na,-2,-2,-2; reading b twice adds no executions.
    expected = [2 * value for value in _difference(_body_samples())]
    _assert_completed(_run_requested(cpp)["chart15"], expected)


@pytest.mark.parametrize("offset", [7, 0], ids=["unavailable-seven", "current-zero"])
def test_constant_offsets_keep_missing_history_and_current_sample_distinct(offset):
    cpp = transpile(_source(f"f(x) => x[{offset}] - x\n", "f(close - open)"))
    # Five calls cannot supply [7]; [0] is each call's current argument.
    # Thus the expectations are five na values or five (d-d)=0 values.
    expected = _difference(_body_samples(), offset)
    _assert_completed(_run_requested(cpp)["chart15"], expected)


def test_two_offsets_of_one_parameter_share_one_sample_per_call():
    cpp = transpile(
        _source("f(x) => x[1] + x[2] - 2*x\nd = close - open\n", "f(d)")
    )
    _assert_sampled_sites(cpp, 1)
    samples = _body_samples()
    expected = [
        previous + older - 2 * current
        for previous, older, current in zip(
            _history(samples, 1), _history(samples, 2), samples
        )
    ]
    # First two slots lack history; then (d-1)+(d-2)-2*d=-3.
    _assert_completed(_run_requested(cpp)["chart15"], expected)


@pytest.mark.parametrize("producer", ["sma", "arithmetic"])
def test_existing_global_history_is_an_independent_oracle(producer):
    # These contain no helper and must already pass on base 063c4e30.
    if producer == "sma":
        pine = _source("a = ta.sma(close, 3)\n", "a[2] - a")
        samples = _sma(_hourly_closes(), 3)
    else:
        pine = _source("d = close - open\n", "d[2] - d")
        samples = _body_samples()
    cpp = transpile(pine)
    _assert_completed(_run_requested(cpp)["chart15"], _difference(samples))


def test_same_requested_slot_replaces_provisional_argument_without_extra_history():
    # All feeds have the same five final hourly O/C pairs. H4 first reaches
    # close 18 on the finer feeds, then finishes at 15. Varying chart size
    # changes how many evaluations the same requested slot receives.
    revised = _CHART_CLOSES[:-1] + ((13.0, 18.0, 15.0, 15.0),)
    feeds = {
        "chart15": (15, revised),
        "chart30": (30, tuple((row[1], row[3]) for row in revised)),
        "chart60": (60, tuple((row[-1],) for row in revised)),
    }
    pine = _REPORTER.replace(
        "'60', b)", "'60', b, lookahead=barmerge.lookahead_on)"
    )
    cpp = transpile(pine)
    _assert_sampled_sites(cpp, 1)
    evaluations = _run_requested(cpp, feeds)
    expected = _difference(_sma(_hourly_closes(), 3))
    h4_counts = []
    for tag, rows in evaluations.items():
        _assert_completed(rows, expected)
        h4 = [row for row in rows if row.timestamp == 4 * _HOUR_MS]
        h4_counts.append(len(h4))
        if tag != "chart60":
            provisional = [row for row in h4 if not row.complete and row.close == 18.0]
            assert provisional, f"{tag} did not exercise a provisional close of 18: {h4}"
            # Only the current SMA changes: (9+12+18)/3=13. The value two
            # calls back stays (3+6+9)/3=6, so provisional output is -7.
            provisional_closes = _hourly_closes()
            provisional_closes[-1] = 18.0
            provisional_expected = _difference(_sma(provisional_closes, 3))[-1]
            _assert_values(
                [row.value for row in provisional],
                [provisional_expected] * len(provisional),
            )
    assert h4_counts[0] > h4_counts[1] > h4_counts[2] == 1, h4_counts


@pytest.mark.parametrize(
    "definitions,payload",
    [
        pytest.param("id(x) => x\n", "id(close - open)", id="value-only-derived"),
        pytest.param("f(src) => src[1]\n", "f(close)", id="direct-bar-history"),
    ],
)
def test_existing_value_and_direct_history_forms_transpile(definitions, payload):
    assert "class GeneratedStrategy" in transpile(_source(definitions, payload))


@pytest.mark.parametrize(
    "definitions,payload,offset",
    [
        pytest.param("id(x) => x\n", "id(close - open)", None, id="value-only-derived"),
        pytest.param("f(src) => src[1]\n", "f(close)", 1, id="direct-bar-history"),
    ],
)
def test_existing_value_and_direct_history_forms_compile_and_run(
    definitions, payload, offset
):
    # Compatibility fallback authorized by the brief: compare behavior
    # through the compiler/runtime, rather than inventing names for new
    # members or claiming byte identity from normalized generated text.
    cpp = transpile(_source(definitions, payload))
    expected = _body_samples() if offset is None else _history(_hourly_closes(), offset)
    # Value-only: 2,3,4,5,6. Direct [1]: na,3,6,9,12.
    _assert_completed(_run_requested(cpp)["chart15"], expected)
