"""A method declaration must not fence a fixed top-level ``var`` TA length.

The stable literal proof is intentionally narrow: a direct, never-reassigned
``var int`` may size ``ta.sma`` once per run, while a reassigned ``var`` or a
``varip`` remains unsupported.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("TA SMA var length with methods")\n'


# Twelve bars: a length of five has four warm-up bars (na) and eight defined
# values, so a wrong length changes the printed text (three bars would print na
# for every length of four or more and could not tell a length from another).
CLOSES = (121.3, 124.7, 121.9, 125.1, 123.4, 126.8,
          122.2, 127.5, 124.9, 128.3, 123.7, 129.6)
LENGTH = 5

_DRIVER_TEMPLATE = r"""
#include <iostream>
#include <vector>
int main() {
    std::vector<pineforge::Bar> bars(%COUNT%);
    const double closes[] = {%CLOSES%};
    for (std::size_t i = 0; i < bars.size(); ++i) {
        bars[i].open = closes[i];
        bars[i].high = closes[i] + 1.0;
        bars[i].low = closes[i] - 1.0;
        bars[i].close = closes[i];
        bars[i].volume = 1.0;
        bars[i].timestamp = 1704067200000LL
            + static_cast<long long>(i) * 3600000LL;
    }
    for (std::size_t count = 1; count <= bars.size(); ++count) {
        GeneratedStrategy strategy;
        strategy.run(bars.data(), count, "60", "60");
        if (!strategy.last_error().empty()) {
            std::cerr << strategy.last_error() << "\n";
            return 1;
        }
        std::cout << strategy.z << "\n";
    }
}
"""

DRIVER = (_DRIVER_TEMPLATE
          .replace("%COUNT%", str(len(CLOSES)))
          .replace("%CLOSES%", ", ".join(repr(c) for c in CLOSES)))


METHOD_SHAPES = [
    pytest.param(
        """method id(int self) => self
x = 1
y = x.id()
""",
        id="primitive-receiver",
    ),
    pytest.param(
        """method push(array<int> self, int x) => array.unshift(self, x)
var array<int> a = array.new<int>()
a.push(1)
""",
        id="collection-receiver",
    ),
]


def _sma_source(prefix: str, *, length: str = "var int p = 5") -> str:
    return HEADER + prefix + length + "\nz = ta.sma(close, p)\n"


def _run(source: str, label: str) -> str:
    return run_emitted_tu(transpile(source), DRIVER, opt="-O0", label=label)


def _assert_sma_lines(output: str, length: int = LENGTH) -> None:
    """The driver prints ``z`` once per prefix of CLOSES: the simple moving
    average of the last ``length`` closes, written here by arithmetic."""

    lines = output.split()
    assert len(lines) == len(CLOSES), output
    for count, line in enumerate(lines, start=1):
        if count < length:
            assert line.lstrip("-").lower() == "nan", (count, line)
        else:
            window = CLOSES[count - length:count]
            assert float(line) == pytest.approx(sum(window) / length, rel=1e-5), (count, line)


def test_control_prints_the_simple_moving_average_of_five_bars() -> None:
    # The control itself is checked against arithmetic, so the comparison below
    # is not between two outputs that could both be wrong.
    _assert_sma_lines(_run(_sma_source(""), "ta-sma-var-length-control-arithmetic"))


@pytest.mark.parametrize("method_prefix", METHOD_SHAPES)
def test_method_shapes_match_their_method_free_controls(method_prefix: str) -> None:
    method_source = _sma_source(method_prefix)
    control_source = _sma_source("")

    # The oracle is independently generated from the method-free control.
    expected = _run(control_source, "ta-sma-var-length-control")
    got = _run(method_source, "ta-sma-var-length-method")
    assert got == expected
    _assert_sma_lines(got)


@pytest.mark.parametrize(
    "source_tail",
    [
        pytest.param(
            "method id(int self) => self\n"
            "var int p = 5\n"
            "q = p.id()\n"
            "z = ta.sma(close, p)\n",
            id="length-is-a-method-receiver",
        ),
        pytest.param(
            "method add(int self, int p) => self + p\n"
            "x = 1\n"
            "y = x.add(2)\n"
            "var int p = 5\n"
            "z = ta.sma(close, p)\n",
            id="method-parameter-named-like-the-length",
        ),
        pytest.param(
            "var int p = 5\n"
            "method bump(int self) =>\n"
            "    p := p + 1\n"
            "    self\n"
            "x = 1\n"
            "y = x.bump()\n"
            "z = ta.sma(close, p)\n",
            id="method-assigns-the-length",
        ),
    ],
)
def test_length_a_method_can_touch_stays_refused(source_tail: str) -> None:
    # The method declaration no longer fences the whole script, so these three
    # shapes are held by the receiver, binding-count and reassignment checks:
    # each must refuse the length itself, at the ta.sma call.
    with pytest.raises(CompileError) as caught:
        transpile(HEADER + source_tail)

    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E5092"
    assert "Unsupported TA constructor length 'p' for ta.sma" in str(caught.value)


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param(
            "method id(int self) => self\nx = 1\ny = x.id()\n",
            id="reassigned-with-method",
        ),
        pytest.param(
            "",
            id="reassigned-without-method",
        ),
    ],
)
def test_reassigned_var_length_stays_refused(prefix: str) -> None:
    source = (
        HEADER
        + prefix
        + "var int p = 5\n"
        + "p := p + 1\n"
        + "z = ta.sma(close, p)\n"
    )

    with pytest.raises(CompileError) as caught:
        transpile(source)

    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E5092"


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("method id(int self) => self\n", id="varip-with-method"),
        pytest.param("", id="varip-without-method"),
    ],
)
def test_varip_length_stays_refused(prefix: str) -> None:
    source = _sma_source(prefix, length="varip int p = 5")

    with pytest.raises(CompileError) as caught:
        transpile(source)

    diagnostic, = caught.value.diagnostics
    assert diagnostic.code == "PF-E5092"


def test_literal_sma_length_is_unchanged_by_a_method() -> None:
    method_source = HEADER + "method id(int self) => self\nz = ta.sma(close, 5)\n"
    control_source = HEADER + "z = ta.sma(close, 5)\n"

    assert _run(method_source, "ta-sma-literal-method") == _run(
        control_source,
        "ta-sma-literal-control",
    )
