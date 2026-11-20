"""Pin the trim set; row IDs distinguish TradingView captures from the spec."""

import pytest

from pineforge_codegen import transpile
from tests._compile import run_emitted_tu


HEADER = '//@version=6\nstrategy("trim set")\n'

# ECMAScript WhiteSpace and LineTerminator. Only the marked edge cases were
# captured in TradingView; the interior expectations follow from edge trimming.
TRIMMED = [
    pytest.param("\t", id="U+0009-captured"),
    pytest.param("\n", id="U+000A-spec"),
    pytest.param("\v", id="U+000B-spec"),
    pytest.param("\f", id="U+000C-spec"),
    pytest.param("\r", id="U+000D-spec"),
    pytest.param(" ", id="U+0020-captured"),
    pytest.param("\u00a0", id="U+00A0-spec"),
    pytest.param("\u1680", id="U+1680-spec"),
    pytest.param("\u2000", id="U+2000-spec"),
    pytest.param("\u2001", id="U+2001-spec"),
    pytest.param("\u2002", id="U+2002-spec"),
    pytest.param("\u2003", id="U+2003-spec"),
    pytest.param("\u2004", id="U+2004-spec"),
    pytest.param("\u2005", id="U+2005-spec"),
    pytest.param("\u2006", id="U+2006-spec"),
    pytest.param("\u2007", id="U+2007-spec"),
    pytest.param("\u2008", id="U+2008-spec"),
    pytest.param("\u2009", id="U+2009-spec"),
    pytest.param("\u200a", id="U+200A-spec"),
    pytest.param("\u2028", id="U+2028-spec"),
    pytest.param("\u2029", id="U+2029-spec"),
    pytest.param("\u202f", id="U+202F-spec"),
    pytest.param("\u205f", id="U+205F-spec"),
    pytest.param("\u3000", id="U+3000-captured"),
    pytest.param("\ufeff", id="U+FEFF-captured"),
]

KEPT = [
    pytest.param("\0", 3, id="U+0000-spec"),
    pytest.param("\x01", 3, id="U+0001-spec"),
    pytest.param("\x1f", 3, id="U+001F-spec"),
    pytest.param("\x7f", 3, id="U+007F-spec"),
    pytest.param("\u0085", 3, id="U+0085-spec"),
    pytest.param("\u009f", 3, id="U+009F-spec"),
    pytest.param("\u00e9", 3, id="U+00E9-spec"),
    pytest.param("\u180e", 3, id="U+180E-captured"),
    pytest.param("\u200b", 3, id="U+200B-captured"),
    pytest.param("\u2060", 3, id="U+2060-captured"),
    pytest.param("\u4e2d", 3, id="U+4E2D-spec"),
    # Three code points, but Pine counts the emoji as two UTF-16 units.
    pytest.param("\U0001f600", 5, id="U+1F600-spec"),
]

PRINT_HEX = r'''
#include <cstdio>
#include <string>

static void print_hex(const std::string& value) {
    for (unsigned char byte : value)
        std::printf("%02x", static_cast<unsigned int>(byte));
    std::printf("\n");
}
'''

BAR_COUNT = 3
STRATEGY_DRIVER = PRINT_HEX + r'''
#include <iostream>
#include <vector>

int main() {
    std::vector<pineforge::Bar> bars(BAR_COUNT);
    for (std::size_t i = 0; i < bars.size(); ++i) {
        bars[i].open = bars[i].high = bars[i].low = bars[i].close = 100.0 + double(i);
        bars[i].volume = 1.0;
        bars[i].timestamp = 1704067200000LL + static_cast<long long>(i) * 60000LL;
    }
    GeneratedStrategy strategy;
    strategy.run(bars.data(), bars.size(), "1", "1");
    if (!strategy.last_error().empty()) {
        std::cerr << strategy.last_error() << "\n";
        return 1;
    }
    // OBSERVE
}
'''.replace("BAR_COUNT", str(BAR_COUNT))


def _run(script: str, observe: str) -> str:
    driver = STRATEGY_DRIVER.replace("// OBSERVE", observe)
    return run_emitted_tu(transpile(HEADER + script), driver, opt="-O0", label="str.trim")


def _pine_string(text: str) -> str:
    # Pine understands \n and \t escapes. Other test characters go in as-is
    # (including lone CR, which the lexer preserves inside a literal).
    escaped = text.replace("\n", r"\n").replace("\t", r"\t")
    return f'"{escaped}"'


def _assert_trim(source: str, expected: str, length: int) -> None:
    script = (
        f"out = str.trim({source})\n"
        "n = str.length(out)\n"
        f"matches = out == {_pine_string(expected)} ? 1 : 0\n"
    )
    actual = _run(
        script,
        r'print_hex(strategy.out); std::cout << strategy.n << " " << strategy.matches << "\n";',
    )
    assert actual == f'{expected.encode("utf-8").hex()}\n{length} 1\n'


@pytest.mark.parametrize("char", TRIMMED)
def test_trims_both_edges(char):
    c = _pine_string(char)
    _assert_trim(f'{c} + "a" + {c}', "a", 1)


@pytest.mark.parametrize("char,n", KEPT)
def test_keeps_non_space_at_both_edges(char, n):
    c = _pine_string(char)
    _assert_trim(f'{c} + "a" + {c}', f"{char}a{char}", n)


@pytest.mark.parametrize("char", TRIMMED)
def test_preserves_interior_space(char):
    _assert_trim(f'"a" + {_pine_string(char)} + "b"', f"a{char}b", 3)


@pytest.mark.parametrize("text,expected,n", [
    pytest.param("\t \u1680\u2003\ufeffx\u2028\u3000\u00a0", "x", 1, id="mixed-runs-spec"),
    pytest.param("\t \u1680\u2003\ufeff\u2028\u3000\u00a0", "", 0, id="all-space-spec"),
    pytest.param("", "", 0, id="empty-spec"),
    pytest.param("\u3000\u00e9\u3000", "\u00e9", 1, id="multibyte-boundary-spec"),
])
def test_trim_edge_runs(text, expected, n):
    _assert_trim(_pine_string(text), expected, n)


@pytest.mark.parametrize("script", [
    pytest.param('n = str.length("abc")\n', id="no-trim"),
    pytest.param('plot(str.length(str.trim(" abc ")))\n', id="elided-plot"),
    pytest.param('for i = 0 to i\n    discarded = str.trim(" abc ")\n',
                 id="discarded-loop"),
])
def test_trim_helper_is_absent_without_an_emitted_call(script):
    cpp = transpile(HEADER + script)
    assert "_pf_u8_trim" not in cpp
    assert "__PINEFORGE_TV_STRING_TRIM__" not in cpp


def test_trim_evaluates_its_argument_once_per_call():
    loop_count = 3
    script = (
        "paddedCount(int pad) =>\n"
        "    var int calls = 0\n"
        "    calls += 1\n"
        '    (pad % 2 == 0 ? " " : "\\t") + str.tostring(calls) + " "\n'
        "outside = str.trim(paddedCount(0))\n"
        'string inside = ""\n'
        f"for i = 1 to {loop_count}\n"
        "    inside := str.trim(paddedCount(i))\n"
    )
    actual = _run(script, "print_hex(strategy.outside); print_hex(strategy.inside);")
    outside = str(BAR_COUNT).encode("utf-8").hex()
    inside = str(BAR_COUNT * loop_count).encode("utf-8").hex()
    assert actual == f"{outside}\n{inside}\n"


def test_trim_stops_at_invalid_utf8_and_preserves_its_bytes():
    rows = [
        (b" \xff x ", b"\xff x"),
        (b" x \xff ", b"x \xff"),
        (b" x \xe3\x80", b"x \xe3\x80"),
        (b" \xc0\xa0 x \xc0\xa0 ", b"\xc0\xa0 x \xc0\xa0"),
        (b" \xe0\x82\xa0 x \xe0\x82\xa0 ", b"\xe0\x82\xa0 x \xe0\x82\xa0"),
        (b" \xf0\x83\x80\x80 x \xf0\x83\x80\x80 ", b"\xf0\x83\x80\x80 x \xf0\x83\x80\x80"),
        (b" x \xe3\x80\x80\x80 ", b"x \xe3\x80\x80\x80"),
    ]
    statements = []
    for text, _ in rows:
        literal = "".join(f"\\x{byte:02x}" for byte in text)
        statements.append(f'    print_hex(::_pf_u8_trim(std::string("{literal}", {len(text)})));')
    driver = PRINT_HEX + "\nint main() {\n" + "\n".join(statements) + "\n}\n"
    cpp = transpile(HEADER + 'out = str.trim(" x ")\n')
    assert run_emitted_tu(cpp, driver, opt="-O0", label="trim invalid UTF-8") == "".join(
        f"{expected.hex()}\n" for _, expected in rows
    )
