"""Stateful array payloads keep typed storage on each requested clock."""

import pytest

from pineforge_codegen import transpile
from tests._compile import compile_cpp, run_emitted_tu


# Interleave two requested clocks with different first bars and bar counts.
REQUESTED_BARS = (
    (0, 10.0, 13.0),
    (1, 100.0, 107.0),
    (0, 20.0, 29.0),
    (0, 30.0, 47.0),
    (1, 200.0, 211.0),
)
CHART_SAMPLES = (901.0, 902.0)


def _source(declarations, payload):
    return (
        '//@version=6\nstrategy("Stateful array payload")\n'
        + declarations
        + f'[first, count, last] = request.security(syminfo.tickerid, "60", {payload})\n'
        + f'[other_first, other_count, other_last] = request.security('
          f'syminfo.tickerid, "240", {payload})\n'
        + "plot(first + count + last + other_first + other_count + other_last)\n"
    )


def _requested_results(source, chart_setup, chart_reads):
    calls = []
    for index, (sec_id, opened, closed) in enumerate(REQUESTED_BARS):
        high = max(opened, closed) + 2.0
        low = min(opened, closed) - 2.0
        fields = [
            *(f"strategy._req_sec_{sec_id}_{element}" for element in range(3)),
            *chart_reads,
        ]
        calls.append(
            f"    strategy.evaluate_security({sec_id}, "
            f"Bar{{{opened}, {high}, {low}, {closed}, 1.0, "
            f"{(index + 1) * 3600000}LL}}, true);\n"
            + '    std::cout << ' + ' << " " << '.join(fields) + ' << "\\n";\n'
        )
    driver = (
        "#include <iomanip>\n#include <iostream>\n"
        "int main() {\n"
        "    GeneratedStrategy strategy;\n"
        f"    {chart_setup}\n"
        "    std::cout << std::setprecision(17);\n"
        + "".join(calls)
        + "}\n"
    )
    return [
        tuple(map(float, line.split()))
        for line in run_emitted_tu(
            transpile(source), driver, opt="-O2", label="stateful array payload"
        ).splitlines()
    ]


def _array_results(declarations, payload):
    return _requested_results(
        _source(declarations, payload),
        "strategy.samples = {" + ", ".join(map(str, CHART_SAMPLES)) + "};",
        ("strategy.samples[0]", "strategy.samples.size()"),
    )


def test_var_array_payload_compiles():
    compile_cpp(transpile(
        '//@version=6\nstrategy("array payload")\n'
        "var array<float> samples = array.from(close, open)\n"
        'v = request.security(syminfo.tickerid, "60", samples.get(0))\n'
        "plot(v)\n"
    ), label="persistent array getter")


@pytest.mark.parametrize("persistent", ("var", "varip"))
def test_persistent_array_writes_use_each_requested_bar(persistent):
    actual = _array_results(
        f"{persistent} array<float> samples = array.from(open)\n"
        "samples.push(close)\n",
        "[samples.get(0), samples.size(), samples.get(samples.size() - 1)]",
    )
    first = {}
    sizes = {}
    expected = []
    for sec_id, opened, closed in REQUESTED_BARS:
        first.setdefault(sec_id, opened)
        sizes[sec_id] = sizes.get(sec_id, 1) + 1
        expected.append((
            first[sec_id], sizes[sec_id], closed,
            CHART_SAMPLES[0], len(CHART_SAMPLES),
        ))
    assert actual == expected


def test_reassigned_array_replays_stores_before_namespace_writes():
    actual = _array_results(
        "array<float> samples = array.from(open)\n"
        "samples := array.from(close, high)\n"
        "array.push(id=samples, value=low)\n",
        "[array.get(samples, 0), array.size(samples), "
        "array.get(samples, array.size(samples) - 1)]",
    )
    expected = [
        (closed, 3, min(opened, closed) - 2.0, CHART_SAMPLES[0], len(CHART_SAMPLES))
        for _sec_id, opened, closed in REQUESTED_BARS
    ]
    assert actual == expected


def test_scalar_var_keeps_its_requested_accumulator():
    actual = _requested_results(
        _source("var float saved = 0.0\nsaved += close\n",
                "[saved, saved * 2.0, saved - close]"),
        "strategy.saved = 901.0;",
        ("strategy.saved",),
    )
    totals = {}
    expected = []
    for sec_id, _opened, closed in REQUESTED_BARS:
        previous = totals.get(sec_id, 0.0)
        totals[sec_id] = previous + closed
        expected.append((totals[sec_id], totals[sec_id] * 2.0, previous, 901.0))
    assert actual == expected
