"""E2E: a multiline string literal (``\"\"\"...\"\"\"`` / ``'''...'''``) and a
line-wrapped single-line one denote the value the Pine manual documents
(lane C5, defect 2).

TradingView, Pine v6 User Manual, Strings, "Multiline strings":

    A multiline string is a literal character sequence enclosed by three
    pairs of ASCII quotation marks (e.g. \"\"\"...\"\"\") or apostrophes (e.g.,
    '''...'''). [...] All parts of a multiline string definition between the
    enclosing \"\"\" or ''' delimiters can occupy separate code lines and use
    any amount of indentation. The definition automatically adds the newline
    control character (U+000A) before each new line to insert a line
    terminator into the resulting string's sequence. [...] Although a
    multiline string can occupy multiple visible code lines in the Pine
    Editor, it is still considered part of a single-line expression [...]
    Multiline string definitions treat all code between the enclosing \"\"\"
    or ''' delimiters, including any space characters used for indentation,
    as literal text. [...] All lines in a multiline string definition after
    the first include every leading space, starting from column 0 in the
    Pine Editor. [...] a multiline string definition does not require a
    backslash to include the " or ' character if: The character does not
    occur three consecutive times in the string's sequence without other
    characters separating the occurrences. There is at least one other
    character or line break between the last " or ' character and the end of
    the string. [...] using `\\"` is *optional*

and, on the deprecated line wrapping of a single-line string:

    In Pine v6, programmers can use line wrapping to define single-line
    literal strings across multiple code lines, where each wrapped line has
    an indentation of one or more spaces. However, the resulting character
    sequence adds only one space to the start of the wrapped lines, and it
    does not automatically add line terminators.

The lexer read ``\"\"\"abc\"\"\"`` as three literals (``""``, ``"abc"``,
``""``) and the parser kept the first: every multiline string was ``""``
(an input it titled was keyed ``''``). A single-line string continued on the
next line kept the raw line break and indentation, and an unterminated one
ran on to the next quote or to the end of the script.

Each case reads one literal through two channels a client sees: the input
manifest (the literal titles an input, whose manifest title must be the
documented value, and an override keyed by exactly that value must reach
the input: trades equal the plain-titled twin's at the default and under the
same override), and per-bar traces (``str.length`` of the literal and its
equality with the single-line literal the manual gives as its equivalent,
on every bar). The manual's own examples are used verbatim where it gives
one.

Interfaces: ``tests/_e2e.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._e2e import (
    Build, Outcome, derive_chart_feed, digest, execute_all, ok,
    skip_unless_e2e_env, summary, transpile_json,
)
from pineforge_codegen.pine_spelling import pine_string_literal


HEADER = '//@version=6\nstrategy("e2e-c5-multiline-strings", overlay=true)\n'
TRADE = ("x = ta.ema(close, len)\n"
         "if ta.crossover(close, x)\n"
         '    strategy.entry("L", strategy.long)\n'
         "if ta.crossunder(close, x)\n"
         '    strategy.close("L")\n')


@dataclass(frozen=True)
class LiteralCase:
    """``declaration`` binds ``s`` to the literal under test (at global scope
    or in a local block); ``title`` spells the same literal as an input title
    (None: the literal is not a global expression). ``value`` is the string
    the manual says it denotes."""
    name: str
    declaration: str
    value: str
    title: str | None

    def _source(self, title: str) -> str:
        eq = pine_string_literal(self.value)
        return (HEADER + self.declaration + f"len = input.int(9, {title})\n"
                + "// @pf-trace length=str.length(s)\n"
                + f"// @pf-trace eq=s == {eq} ? 1 : 0\n" + TRADE)

    def subject(self) -> Build:
        if self.title is None:
            return Build(self._source('"fast"'), {"fast": 23}, trace=True)
        return Build(self._source(self.title), {self.value: 23}, trace=True)

    def twin(self) -> Build:
        return Build(self._source('"fast"'), {"fast": 23})


HELLO = '"""\nHello\nworld!\n"""'
INDENTED = '"""\n0 leading spaces\n 1 leading space\n    4 leading spaces\n        8 leading spaces\n"""'

CASES: tuple[LiteralCase, ...] = (
    # The manual's examples, verbatim.
    LiteralCase("manual_hello", f"string s = {HELLO}\n", "\nHello\nworld!\n", HELLO),
    LiteralCase("manual_indentation", f"string s = {INDENTED}\n",
                "\n0 leading spaces\n 1 leading space\n    4 leading spaces\n"
                "        8 leading spaces\n", INDENTED),
    LiteralCase("manual_local_block",
                'var string s = ""\n'
                "if close > 0\n"
                '    string localMultiStr = """This line is not indented.\n'
                "    This line has four leading spaces.\n"
                '        This line has four additional leading spaces."""\n'
                "    s := localMultiStr\n",
                "This line is not indented.\n    This line has four leading spaces.\n"
                "        This line has four additional leading spaces.", None),
    LiteralCase("manual_quotes", 'string s = """ "Some quoted text" """\n',
                ' "Some quoted text" ', '""" "Some quoted text" """'),
    # Apostrophe delimiters, a one-line multiline string, the empty one.
    LiteralCase("apostrophes", "s = '''It's a \"quote\"\n  and 'more'''\n",
                "It's a \"quote\"\n  and 'more", "'''It's a \"quote\"\n  and 'more'''"),
    LiteralCase("one_line", 's = """Len"""\n', "Len", '"""Len"""'),
    LiteralCase("empty", 's = """"""\n', "", None),
    # Escapes read as in any Pine string (the manual's `\"` is optional).
    LiteralCase("escapes", 's = """a\\tb\\nc \\"d\\" \\\\e\nf"""\n',
                'a\tb\nc "d" \\e\nf', '"""a\\tb\\nc \\"d\\" \\\\e\nf"""'),
    # A delimiter character twice in a row, and a trailing quote escaped.
    LiteralCase("inner_quotes", 's = """say ""hi"" \\""""\n', 'say ""hi"" "',
                '"""say ""hi"" \\""""'),
    # A multiline string is part of one expression: code follows it.
    LiteralCase("expression_continues", 's = """ab\ncd""" + "ef"\n', "ab\ncdef", None),
    # The deprecated line wrapping of a single-line string: one space, no
    # line terminator.
    LiteralCase("wrapped_single_line", 's = "Hello\n     world"\n', "Hello world",
                '"Hello\n     world"'),
    LiteralCase("wrapped_single_quoted", "s = 'a\n  b\n    c'\n", "a b c", None),
)
CASES_BY_NAME = {c.name: c for c in CASES}
assert len(CASES_BY_NAME) == len(CASES), "duplicate case name"


@dataclass(frozen=True)
class Refused:
    name: str
    body: str
    line: int
    col: int
    message: str


UNTERMINATED_MULTILINE = (
    'Unterminated multiline string literal: no closing """ before the end of the '
    'script. — A multiline string ends at the next """ (or \'\'\' for one opened '
    "with '''); escape a quote that would end it early."
)
UNTERMINATED = (
    "Unterminated string literal: the line ends before its closing quote and the "
    "next line is not indented as a wrapped line. — Close the string on its line, "
    'indent the line it wraps onto, or use a multiline string ("""...""").'
)

REFUSED: tuple[Refused, ...] = (
    Refused("unterminated_multiline", 's = """abc\nx = close\n', 3, 5,
            UNTERMINATED_MULTILINE),
    Refused("unterminated_single_line", 's = "abc\nx = close\n', 3, 5, UNTERMINATED),
    Refused("unterminated_at_end", 'x = close\ns = "abc', 4, 5, UNTERMINATED),
)
REFUSED_BY_NAME = {c.name: c for c in REFUSED}


@pytest.fixture(scope="session")
def outcomes(request, tmp_path_factory) -> dict[str, Outcome]:
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("e2e_multiline_strings")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    builds: dict[str, Build] = {}
    for item in request.session.items:
        if getattr(item, "originalname", None) == "test_string_literal_gives_the_documented_value":
            case = CASES_BY_NAME[item.callspec.params["case_name"]]
            builds[f"{case.name}/subject"] = case.subject()
            builds[f"{case.name}/twin"] = case.twin()
    return execute_all(engine_root, feed, base, builds)


@pytest.mark.parametrize("case_name", list(CASES_BY_NAME))
def test_string_literal_gives_the_documented_value(case_name: str, outcomes) -> None:
    case = CASES_BY_NAME[case_name]
    subject = ok(outcomes, f"{case_name}/subject")
    twin = ok(outcomes, f"{case_name}/twin")
    failures = []
    titles = [e["title"] for e in subject.transpiled["inputs"]]
    expected_title = case.value if case.title is not None else "fast"
    if titles != [expected_title]:
        failures.append(f"manifest titles {titles!r}, not {expected_title!r}")
    expected = {"length": float(len(case.value)), "eq": 1.0}
    seen: dict[str, set[float]] = {}
    for rec in subject.traces["default"]:
        seen.setdefault(rec["name"], set()).add(rec["value"])
    for name, value in expected.items():
        if seen.get(name) != {value}:
            failures.append(f"{name}: traced {sorted(seen.get(name, ()))} vs {value} "
                            f"for {case.value!r}")
    for tag in ("default", "override"):
        a, b = subject.trades[tag], twin.trades[tag]
        if digest(a) != digest(b):
            failures.append(f"{tag} trades {summary(a)} vs the plain-titled twin's {summary(b)}")
    if subject.trades["default"] == subject.trades["override"]:
        failures.append(f"override {subject.build.overrides!r} left the trades unchanged "
                        f"({summary(subject.trades['default'])})")
    assert not failures, f"[{case_name}] " + "\n  ".join(failures)
    bars = len(subject.traces["default"]) // len(expected)
    print(f"E2E multiline {case_name}: == {case.value!r}  manifest {titles!r}, "
          f"{len(expected)} traces x {bars} bars equal (length {len(case.value)})  "
          f"override {subject.build.overrides!r} {summary(subject.trades['override'])} == twin")


@pytest.mark.parametrize("case_name", list(REFUSED_BY_NAME))
def test_unterminated_string_literal_is_refused(case_name: str, tmp_path: Path) -> None:
    case = REFUSED_BY_NAME[case_name]
    pine = tmp_path / "strategy.pine"
    pine.write_text(HEADER + case.body)
    result = transpile_json(pine)
    assert not result["ok"], f"[{case_name}] transpiled; its string literal never ends"
    got = [(d["line"], d["col"], d["message"]) for d in result["diagnostics"]]
    assert got == [(case.line, case.col, case.message)], f"[{case_name}] {got}"
    print(f"E2E multiline {case_name}: refused at {case.line}:{case.col}")
