"""E2E: a Pine string literal's escape sequences produce the value the Pine
manual documents (lane C4, defect 6).

TradingView, Pine v6 User Manual, Strings, "Escape sequences":

    The backslash character (\\), also known as the Reverse Solidus in Unicode
    (U+005C), is an escape character in Pine strings. [...] Characters with a
    special meaning in "string" value definitions, such as quotation marks and
    backslashes, become literal characters when prefixed by a backslash (e.g.,
    \\\\ includes a single \\ in the character sequence). [...] The \\n
    sequence represents the newline character (U+000A), a line terminator for
    multiline text. The \\t sequence represents the horizontal tab character
    (U+0009), which is helpful for indentation. [...] If a backslash applied
    to a character does not form a supported escape sequence, the
    character's meaning does not change.

The lexer turned every ``\\X`` into ``X``, so ``"a\\nb"`` was ``anb``. Each
case reads one literal through two channels a client sees:

* the input manifest: the literal titles an input, whose manifest title
  must be the documented value, and an override keyed by exactly that value
  must reach the input (the trades equal the plain-titled twin's at the
  default and under the same override);
* per-bar traces: ``str.length`` of the literal and ``str.contains`` of the
  characters an escape could produce, each equal to the value computed from
  the documented text on every bar.

Interfaces: ``tests/_e2e.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests._e2e import (
    Build, Outcome, derive_chart_feed, digest, execute_all, ok,
    skip_unless_e2e_env, summary,
)


HEADER = '//@version=6\nstrategy("e2e-c4-escapes", overlay=true)\n'
# (trace name, Pine needle literal, the needle's character)
PROBES = (("has_n", '"n"', "n"), ("has_t", '"t"', "t"), ("has_T", '"T"', "T"),
          ("has_backslash", '"\\\\"', "\\"), ("has_dquote", "'\"'", '"'),
          ("has_squote", '"\'"', "'"), ("has_newline", '"\\n"', "\n"),
          ("has_tab", '"\\t"', "\t"))


@dataclass(frozen=True)
class EscapeCase:
    """``literal`` as written in Pine, ``value`` the string it denotes."""
    name: str
    literal: str
    value: str
    inline: bool = False

    def _source(self, title: str) -> str:
        traces = ["// @pf-trace length=str.length(s)\n"]
        traces += [f"// @pf-trace {n}=str.contains(s, {needle}) ? 1 : 0\n"
                   for n, needle, _ in PROBES]
        length = f"input.int(9, {title})" if self.inline else "len"
        decl = "" if self.inline else f"len = input.int(9, {title})\n"
        return (HEADER + f"s = {self.literal}\n" + decl
                + f"x = ta.ema(close, {length})\n" + "".join(traces)
                + "if ta.crossover(close, x)\n"
                '    strategy.entry("L", strategy.long)\n'
                "if ta.crossunder(close, x)\n"
                '    strategy.close("L")\n')

    def subject(self) -> Build:
        return Build(self._source(self.literal), {self.value: 23}, trace=True)

    def twin(self) -> Build:
        return Build(self._source('"fast"'), {"fast": 23})

    def expected(self) -> dict[str, float]:
        out = {"length": float(len(self.value))}
        out.update({n: 1.0 if ch in self.value else 0.0 for n, _, ch in PROBES})
        return out


CASES: tuple[EscapeCase, ...] = (
    EscapeCase("newline", '"a\\nb"', "a\nb"),
    EscapeCase("tab", '"a\\tb"', "a\tb"),
    EscapeCase("backslash", '"a\\\\b"', "a\\b"),
    EscapeCase("double_quote", '"a\\"b"', 'a"b'),
    EscapeCase("single_quote", "'a\\'b'", "a'b"),
    EscapeCase("newline_single_quoted", "'a\\nb'", "a\nb"),
    EscapeCase("mixed", '"x\\t\\"y\\"\\\\n\\n"', 'x\t"y"\\n\n'),
    EscapeCase("inline_ta_length_title", '"a\\nb\\tc"', "a\nb\tc", inline=True),
    # Not a supported sequence: the character keeps its meaning (the Pine
    # manual's own "\This" example); the lexer already read it that way.
    EscapeCase("undocumented", '"a\\Tb"', "aTb"),
)
CASES_BY_NAME = {c.name: c for c in CASES}
assert len(CASES_BY_NAME) == len(CASES), "duplicate case name"


@pytest.fixture(scope="session")
def outcomes(request, tmp_path_factory) -> dict[str, Outcome]:
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("e2e_string_escapes")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    builds: dict[str, Build] = {}
    for item in request.session.items:
        if getattr(item, "originalname", None) == "test_escape_sequence_gives_the_documented_value":
            case = CASES_BY_NAME[item.callspec.params["case_name"]]
            builds[f"{case.name}/subject"] = case.subject()
            builds[f"{case.name}/twin"] = case.twin()
    return execute_all(engine_root, feed, base, builds)


@pytest.mark.parametrize("case_name", list(CASES_BY_NAME))
def test_escape_sequence_gives_the_documented_value(case_name: str, outcomes) -> None:
    case = CASES_BY_NAME[case_name]
    subject = ok(outcomes, f"{case_name}/subject")
    twin = ok(outcomes, f"{case_name}/twin")
    failures = []
    titles = [e["title"] for e in subject.transpiled["inputs"]]
    if titles != [case.value]:
        failures.append(f"manifest titles {titles!r}, not the value {case.value!r}")
    expected = case.expected()
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
            failures.append(f"{tag} trades {summary(a)} vs the plain-titled twin's "
                            f"{summary(b)}")
    if subject.trades["default"] == subject.trades["override"]:
        failures.append(f"override {subject.build.overrides!r} left the trades unchanged "
                        f"({summary(subject.trades['default'])})")
    assert not failures, f"[{case_name}] " + "\n  ".join(failures)
    bars = len(subject.traces["default"]) // len(expected)
    print(f"E2E escape {case_name}: {case.literal} == {case.value!r}  manifest title equal, "
          f"{len(expected)} traces x {bars} bars equal (length {expected['length']:.0f})  "
          f"override {subject.build.overrides!r} {summary(subject.trades['override'])} "
          f"== twin")
