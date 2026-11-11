"""A NUL inside a Pine string literal does not end the string.

TradingView keeps such a string whole: "a", NUL, "b" has length 3 (measured). The
generated code used to build a literal with std::string("...") from a C string,
which ends at the first NUL, so the string was cut there. Each row transpiles a
small script, builds the emitted translation unit with the engine headers and
library, runs the strategy on three bars and reads the script's own variables.
"""

import re

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu
from tests._cpp_tokens import string_values

HEADER = '//@version=6\nstrategy("string nul values")\n'

DRIVER = r'''
#include <iostream>
#include <vector>

int main() {
    std::vector<pineforge::Bar> bars(3);
    for (std::size_t i = 0; i < bars.size(); ++i) {
        bars[i].open = bars[i].high = bars[i].low = bars[i].close = 100.0 + double(i);
        bars[i].volume = 1.0;
        bars[i].timestamp = 1704067200000LL + static_cast<long long>(i) * 60000LL;
    }
    GeneratedStrategy strategy;
    strategy.run(bars.data(), bars.size(), "1", "1");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << "\n";
        return 14;
    }
    // OBSERVE
    return 0;
}
'''

NUL = "\x00"


def _run(script: str, observe: str) -> str:
    cpp = transpile(HEADER + script)
    return run_emitted_tu(
        cpp, DRIVER.replace("// OBSERVE", observe), opt="-O0", label="string nul values",
    )


ROWS = [
    pytest.param(
        f's = "a{NUL}b"\nn = str.length(s)\nplot(n)\n',
        'std::cout << strategy.n << "\\n";', "3\n", id="length-of-a-literal",
    ),
    pytest.param(
        f's = "a{NUL}b" + "c"\nn = str.length(s)\nplot(n)\n',
        'std::cout << strategy.n << "\\n";', "4\n", id="length-after-concatenation",
    ),
    pytest.param(
        f'a = "a{NUL}b"\nb = "a{NUL}c"\neq = a == b ? 1 : 0\nplot(eq)\n',
        'std::cout << strategy.eq << "\\n";', "0\n", id="differ-after-the-nul",
    ),
    pytest.param(
        f's = "a{NUL}b"\nu = s == "a{NUL}b" ? 1 : 0\nv = s == "a" ? 1 : 0\nplot(u + v)\n',
        'std::cout << strategy.u << " " << strategy.v << "\\n";', "1 0\n",
        id="equals-itself-and-not-its-prefix",
    ),
    pytest.param(
        f's = "a{NUL}b"\nt = s\nn = str.length(t)\nplot(n)\n',
        'std::cout << strategy.n << "\\n";', "3\n",
        id="length-of-a-copy-of-a-known-variable",
    ),
    pytest.param(
        f's = "a{NUL}b"\nvar string t = s\nn = str.length(t)\nplot(n)\n',
        'std::cout << strategy.n << "\\n";', "3\n",
        id="length-of-a-var-set-from-a-known-variable",
    ),
    pytest.param(
        f'enum Mode\n    fast = "f{NUL}t"\n    slow\n'
        'e = Mode.fast\ntext = str.tostring(e)\nn = str.length(text)\nplot(n)\n',
        'std::cout << strategy.n << "\\n";', "3\n", id="length-of-an-enum-title",
    ),
    pytest.param(
        'n = str.length("abc")\nplot(n)\n',
        'std::cout << strategy.n << "\\n";', "3\n", id="control-ascii-length",
    ),
]


@pytest.mark.parametrize("script, observe, expected", ROWS)
def test_an_embedded_nul_does_not_end_a_string(script, observe, expected):
    assert _run(script, observe) == expected


def test_no_string_literal_with_a_nul_is_built_from_a_c_string():
    """Every emitted std::string literal that holds a NUL carries its length."""
    script = (
        f'kept = "k{NUL}z"\n'
        f'var string held = "h{NUL}z"\n'
        f'direct = str.length("d{NUL}z")\n'
        f'viakept = str.length(kept)\n'
        f'viaheld = str.length(held)\n'
        f'joined = str.length("j{NUL}z" + "w")\n'
        f'chosen = str.length(close > 0 ? "t{NUL}z" : "u")\n'
        'plot(direct + viakept + viaheld + joined + chosen)\n'
    )
    cpp = transpile(HEADER + script)
    assert "\\000" in cpp
    # A literal with a NUL and no explicit length is the cut string of old.
    assert re.findall(r'std::string\("[^"]*\\000[^"]*"\)', cpp) == []


AUTHORED = [
    # (the Pine text between the quotes, the string it stands for)
    (f"a{NUL}b", "a\0b"),
    (f"{NUL}", "\0"),
    (f"x{NUL}7", "x\x007"),
    (f'q{NUL}\\"z', 'q\0"z'),
    (f"p{NUL}\\\\q", "p\0\\q"),
    (f"t{NUL}\\tu", "t\0\tu"),
    (f"\U000000e9{NUL}\U000000e9", "\U000000e9\0\U000000e9"),
    (f"{NUL}\U0001F600", "\0\U0001F600"),
]


def test_the_emitted_length_is_the_byte_count_of_the_emitted_literal():
    """A literal that holds a NUL carries the number of bytes it stands for.

    The count is read from the emitted text next to the literal, whatever its
    escapes (quote, backslash, tab, an octal NUL before a digit) and whatever
    its UTF-8 bytes; a count that is too small cuts the string, one that is too
    large reads past the end of the literal."""
    lines = [f'v{i} = str.length("{pine}")\n' for i, (pine, _) in enumerate(AUTHORED)]
    script = "".join(lines) + "plot(" + " + ".join(f"v{i}" for i in range(len(AUTHORED))) + ")\n"
    cpp = transpile(HEADER + script)
    sized = re.findall(r'std::string\(("(?:\\.|[^"\\])*"), (\d+)\)', cpp)
    assert len(sized) == len(AUTHORED), sized
    for (literal, count), (_, expected) in zip(sized, AUTHORED):
        (value,) = string_values(literal)
        assert value == expected, (literal, value, expected)
        assert int(count) == len(expected.encode("utf-8")), (literal, count)


def test_the_forms_above_keep_their_lengths_at_run_time():
    script = (
        f'kept = "k{NUL}z"\n'
        f'var string held = "h{NUL}z"\n'
        f'direct = str.length("d{NUL}z")\n'
        f'viakept = str.length(kept)\n'
        f'viaheld = str.length(held)\n'
        f'joined = str.length("j{NUL}z" + "w")\n'
        f'chosen = str.length(close > 0 ? "t{NUL}z" : "u")\n'
        'plot(direct + viakept + viaheld + joined + chosen)\n'
    )
    observe = (
        'std::cout << strategy.direct << " " << strategy.viakept << " " '
        '<< strategy.viaheld << " " << strategy.joined << " " << strategy.chosen << "\\n";'
    )
    assert _run(script, observe) == "3 3 3 4 3\n"
