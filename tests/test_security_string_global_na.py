"""A payload's copy of a global string stores a bare na as the string na.

``string tag = na`` written in a branch (``tag := "x"``) and read with history
inside a request.security payload (``tag[1]``) is replayed on the requested
bars: the evaluator keeps a series of strings for it, and pushed the numeric
``na<double>()`` as its seed, which g++ rejects ("cannot convert 'double' to
'std::string'"). The seed is now the string na; a string value, a float copy and
an integer copy keep their emitted text.
"""

import pytest

from pineforge_codegen import transpile

HEADER = '//@version=6\nstrategy("security string global", initial_capital = 100000)\n'
ENTRY = 'if v > 0\n    strategy.entry("L", strategy.long)\n'


def _evaluator(cpp: str, sec_id: int = 0) -> str:
    start = cpp.index(f"void _eval_security_{sec_id}")
    body = cpp[start:]
    return body[:body.index("\n    }\n")]


def test_a_string_global_na_seed_is_the_string_na():
    cpp = transpile(
        HEADER
        + "string tag = na\n"
        "if bar_index % 2 == 0\n"
        '    tag := "x"\n'
        "read() =>\n"
        "    (na(tag) ? 1.0 : 0.0) + (na(tag[1]) ? 2.0 : 0.0)\n"
        'float v = request.security(syminfo.tickerid, timeframe.period, read(), gaps=barmerge.gaps_off, lookahead=barmerge.lookahead_off)\n'
        + ENTRY
    )
    body = _evaluator(cpp)
    assert "_sec0_tag.push(na<std::string>())" in body, body
    assert "_sec0_tag.push(na<double>())" not in body, body
    assert "_sec0_tag.update(na<double>())" not in body, body


def test_a_string_global_written_with_a_string_keeps_its_value():
    cpp = transpile(
        HEADER
        + "string tag = na\n"
        "if bar_index % 2 == 0\n"
        '    tag := "x"\n'
        "read() =>\n"
        "    (na(tag) ? 1.0 : 0.0) + (na(tag[1]) ? 2.0 : 0.0)\n"
        'float v = request.security(syminfo.tickerid, timeframe.period, read(), gaps=barmerge.gaps_off, lookahead=barmerge.lookahead_off)\n'
        + ENTRY
    )
    assert '_sec0_tag.update(std::string("x"))' in _evaluator(cpp)


@pytest.mark.parametrize(
    "declaration",
    [
        pytest.param("float tag = na\n", id="float"),
        pytest.param("int tag = na\n", id="int"),
    ],
)
def test_a_numeric_global_na_seed_is_unchanged(declaration):
    cpp = transpile(
        HEADER
        + declaration
        + "if bar_index % 2 == 0\n"
        "    tag := 1\n"
        "read() =>\n"
        "    (na(tag) ? 1.0 : 0.0) + (na(tag[1]) ? 2.0 : 0.0)\n"
        'float v = request.security(syminfo.tickerid, timeframe.period, read(), gaps=barmerge.gaps_off, lookahead=barmerge.lookahead_off)\n'
        + ENTRY
    )
    body = _evaluator(cpp)
    assert "na<std::string>()" not in body, body
