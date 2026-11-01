"""A request payload recomputes callable arguments on the requested bars."""

from __future__ import annotations

import pytest

import pineforge_codegen
from pineforge_codegen import transpile
from tests._compile import compile_cpp, run_emitted_tu
from tests._e2e import transpile_json


_PRELUDE = '//@version=6\nstrategy("Requested parameter")\n'

_CASES = [
    pytest.param(
        'method sample(float self) => request.security(syminfo.tickerid, "60", self)\n',
        "(close - open).sample()", "close - open",
        ("6 6", "-6 -6"),  # Hourly close minus open: 16 - 10 = 6; 14 - 20 = -6.
        id="method-receiver",
    ),
    pytest.param(
        'method sample(float source) => request.security(syminfo.tickerid, "60", source)\n',
        "(close - open).sample()", "close - open",
        ("6 6", "-6 -6"),  # Renaming the receiver leaves 16 - 10 and 14 - 20.
        id="renamed-receiver",
    ),
    pytest.param(
        'method sample(float anchor, float source) => '
        'request.security(syminfo.tickerid, "60", source)\n',
        "close.sample(close - open)", "close - open",
        ("6 6", "-6 -6"),  # The second argument is the hourly body, independent of anchor.
        id="second-parameter",
    ),
    pytest.param(
        'method sample(float anchor, float bias, float source) => '
        'request.security(syminfo.tickerid, "60", source + bias)\n',
        "close.sample(source = close - open, bias = 2.0)", "close - open + 2.0",
        ("8 8", "-4 -4"),  # The third parameter contributes 6 + 2 and -6 + 2.
        id="third-parameter-keyword",
    ),
    pytest.param(
        'pull(float source) => request.security(syminfo.tickerid, "60", source)\n'
        "method sample(float value) => pull(value)\n",
        "(close - open).sample()", "close - open",
        ("6 6", "-6 -6"),  # The plain function receives the same hourly body through the method.
        id="function-parameter-through-method",
    ),
    pytest.param(
        'pull(float bias, float source) => '
        'request.security(syminfo.tickerid, "60", source + bias)\n'
        "method sample(float value) => pull(2.0, value)\n",
        "(close - open).sample()", "close - open + 2.0",
        ("8 8", "-4 -4"),  # The function's second parameter gives 6 + 2 and -6 + 2.
        id="function-second-parameter-through-method",
    ),
    pytest.param(
        'method sample(float source) => request.security(syminfo.tickerid, "60", source)\n'
        "pull(float value) => value.sample()\n",
        "pull(close - open)", "close - open",
        ("6 6", "-6 -6"),  # The method receiver resolves through the function to each hourly body.
        id="method-through-function",
    ),
    pytest.param(
        'method sample(float source) => request.security(syminfo.tickerid, "60", source)\n',
        "(close - open).sample() + (high - low).sample()",
        "(close - open) + (high - low)",
        ("14 14", "3 3"),  # Separate calls give 6 + (17 - 9) and -6 + (22 - 13).
        id="distinct-call-arguments",
    ),
    pytest.param(
        'method sample(float source, float bias = 2.0) => '
        'request.security(syminfo.tickerid, "60", source + bias)\n',
        "(close - open).sample()", "close - open + 2.0",
        ("8 8", "-4 -4"),  # The omitted argument binds its default: 6 + 2 and -6 + 2.
        id="method-default",
    ),
    pytest.param(
        'method sample(float source) => request.security(syminfo.tickerid, "60", source[1])\n',
        "close.sample()", "close[1]",
        ("na na", "16 16"),  # No preceding hour at first; the second hour reads the first close, 16.
        id="requested-parameter-history",
    ),
    pytest.param(
        "method sample(int index) => index + 100\n"
        'method sample(float source) => request.security(syminfo.tickerid, "60", source)\n',
        "(close - open).sample() + int(1).sample()", "close - open + 101",
        ("107 107", "95 95"),  # Only the float method requests data; the int method adds 101.
        id="typed-method-dispatch",
    ),
    pytest.param(
        'method sample(float value, float adjustment = 2.0) => '
        'request.security(syminfo.tickerid, "60", value + adjustment)\n',
        "float(close - open).sample()", "float(close - open) + 2.0",
        ("8 8", "-4 -4"),  # The cast keeps each hourly body; the default adds 2: 8, -4.
        id="cast-receiver-default",
    ),
    pytest.param(
        "method sample(float value) => value + 0.5\n"
        'method sample(int value) => request.security(syminfo.tickerid, "60", value)\n',
        "int(close - open).sample() + float(1).sample()", "int(close - open) + 1.5",
        ("7.5 7.5", "-4.5 -4.5"),  # Integer hourly bodies 6, -6 plus float(1) + 0.5.
        id="cast-integer-method-dispatch",
    ),
]


def _source(definitions: str, call: str, payload: str) -> str:
    return (
        _PRELUDE + definitions
        + f"actual = {call}\n"
        + f'reference = request.security(syminfo.tickerid, "60", {payload})\n'
        + "plot(actual + reference)\n"
    )


_DRIVER = r"""
#include <iostream>

struct ParameterObserver : GeneratedStrategy {
    static void print_value(double value) {
        if (is_na(value)) std::cout << "na";
        else std::cout << value;
    }

    void on_source_bar(const Bar& bar) override {
        GeneratedStrategy::on_source_bar(bar);
        if (bar.timestamp == 2700000 || bar.timestamp == 6300000) {
            print_value(actual);
            std::cout << ' ';
            print_value(reference);
            std::cout << '\n';
        }
    }
};

int main() {
    Bar bars[] = {
        {10, 12, 9, 11, 1, 0},
        {11, 14, 10, 13, 1, 900000},
        {13, 14, 11, 12, 1, 1800000},
        {12, 17, 11, 16, 1, 2700000},
        {20, 22, 19, 21, 1, 3600000},
        {21, 22, 17, 18, 1, 4500000},
        {18, 20, 16, 19, 1, 5400000},
        {19, 20, 13, 14, 1, 6300000},
    };
    ParameterObserver strategy;
    strategy.run(bars, 8, "15", "15");
}
"""


@pytest.mark.parametrize("definitions,call,payload,expected", _CASES)
def test_payload_parameters_translate_and_compile(definitions, call, payload, expected):
    cpp = transpile(_source(definitions, call, payload))
    compile_cpp(cpp, label="request payload parameter")


@pytest.mark.parametrize("definitions,call,payload,expected", _CASES)
def test_payload_parameters_match_inline_on_hourly_bars(
    tmp_path, definitions, call, payload, expected,
):
    pine = tmp_path / "strategy.pine"
    pine.write_text(_source(definitions, call, payload), encoding="utf-8")
    result = transpile_json(pine)
    assert result["ok"], result.get("diagnostics")
    output = run_emitted_tu(
        result["cpp"], _DRIVER, opt="-O0", label="request payload parameter",
    )
    assert tuple(output.splitlines()) == expected


@pytest.mark.parametrize("definitions,call,payload", [
    ("", 'request.security(syminfo.tickerid, "60", close - open)', "close - open"),
    ('pull(float source) => request.security(syminfo.tickerid, "60", source)\n',
     "pull(close - open)", "close - open"),
    ("method twice(float source) => source * 2\n",
     'request.security(syminfo.tickerid, "60", close.twice())', "close * 2"),
    ('method sample(float source) => request.security(syminfo.tickerid, "60", close)\n',
     "(close - open).sample()", "close"),
])
def test_payloads_without_method_parameter_reads_keep_cpp(
    monkeypatch, definitions, call, payload,
):
    source = _source(definitions, call, payload)
    cpp = transpile(source)
    monkeypatch.setattr(
        pineforge_codegen, "specialize_method_security_payloads",
        lambda *args, **kwargs: False, raising=False,
    )
    assert transpile(source) == cpp
