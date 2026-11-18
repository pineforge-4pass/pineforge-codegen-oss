"""String identifier reads retain the type of their lexical storage."""

import json

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


_LOG_DRIVER = r"""
#include <cstdio>
#include <iostream>

int main() {
    if (std::freopen(LOG_PATH, "w+", stderr) == nullptr) {
        return 3;
    }
    const pineforge::Bar bars[] = {
        {10.0, 10.0, 10.0, 10.0, 1.0, 1743466500000LL},
    };
    GeneratedStrategy strategy;
    strategy.run(bars, 1);
    if (!strategy.last_error().empty()) {
        std::cout << strategy.last_error();
        return 2;
    }
    std::fflush(stderr);
    std::rewind(stderr);
    for (int value; (value = std::fgetc(stderr)) != EOF;) {
        std::cout.put(static_cast<char>(value));
    }
}
"""


@pytest.mark.parametrize(
    "declarations, operand",
    [
        pytest.param("", "txt", id="getter-result"),
        pytest.param("alias = txt\n", "alias", id="getter-alias"),
    ],
)
def test_drawing_getter_string_identifier_compiles_and_logs(
    tmp_path, declarations, operand,
):
    source = (
        '//@version=6\nstrategy("drawing text identifier")\n'
        'mark = label.new(bar_index, close, "x")\n'
        "txt = label.get_text(mark)\n"
        f"{declarations}"
        f'message = str.format("{{0}}", "<" + {operand} + ">")\n'
        "log.info(message)\n"
    )
    driver = _LOG_DRIVER.replace("LOG_PATH", json.dumps(str(tmp_path / "log.txt")))
    output = run_emitted_tu(
        transpile(source), driver, opt="-O0", label="drawing text identifier",
    )
    assert output.splitlines() == ["[INFO] <x>"]


_COMPARISON_DRIVER = r"""
#include <iostream>

int main() {
    const pineforge::Bar bars[] = {
        {10.0, 10.0, 10.0, 10.0, 1.0, 1743466500000LL},
        {10.0, 20.0, 10.0, 20.0, 1.0, 1743466560000LL},
        {20.0, 20.0, 20.0, 20.0, 1.0, 1743466620000LL},
        {20.0, 30.0, 20.0, 30.0, 1.0, 1743466680000LL},
    };
    for (int count = 2; count <= 4; ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars, count);
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error();
            return 2;
        }
        std::cout << strategy.matched << '\n';
    }
}
"""


@pytest.mark.parametrize(
    "left, right, history, expected",
    [
        pytest.param("a", "b", False, ["0", "1", "0"], id="plain-locals-control"),
        pytest.param("a", "b", True, ["0", "1", "0"], id="series-locals"),
        pytest.param("a", "other", True, ["0", "1", "0"], id="parameter-control"),
        pytest.param("a", "a[1]", True, ["0", "1", "0"], id="local-history-control"),
        pytest.param("a[1]", "b[1]", True, ["1", "0", "1"], id="history-control"),
    ],
)
def test_callable_string_equality_compiles_and_compares_strings(
    left, right, history, expected,
):
    # History reads promote the locals to Series<std::string>. Bar zero
    # seeds those buffers; observations start with an existing previous bar.
    histories = "    previous_a = a[1]\n    previous_b = b[1]\n" if history else ""
    source = (
        '//@version=6\nstrategy("string identifier equality")\n'
        "matches(string other) =>\n"
        "    a = str.tostring(close)\n"
        "    b = str.tostring(open)\n"
        f"{histories}"
        f"    {left} == {right}\n"
        "matched = matches(str.tostring(open))\n"
        "plot(matched ? 1 : 0)\n"
    )
    output = run_emitted_tu(
        transpile(source), _COMPARISON_DRIVER, opt="-O0",
        label="string identifier equality",
    )
    assert output.splitlines() == expected
