"""E2E: an input title given as a constant expression is keyed by its string
value; a title that is not a compile-time constant is refused at transpile
time (lane C4, defect 5).

TradingView (Pine v6 reference, ``input.int``): ``title (const string)
Title of the input.`` A title spelled through a name (``T = "Len"`` then
``input.int(9, T)``) became the C++ text of the title expression -- the
manifest listed the input as ``std::string("Len")`` and every getter read it
under that key -- and a series title (``str.tostring(close)``) or another
input's value transpiled the same way.

Each accepted case runs next to the same strategy with the literal title
``"Len"``, at the defaults and under ``{"Len": 23}``: the manifest lists
``Len``, the trades are identical, and the override moves them. Each refused
case gets transpile_json's diagnostic at its title.

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


HEADER = '//@version=6\nstrategy("e2e-c4-title", overlay=true)\n'


def _trade(length: str = "len") -> str:
    return (f"x = ta.ema(close, {length})\n"
            "if ta.crossover(close, x)\n"
            '    strategy.entry("L", strategy.long)\n'
            "if ta.crossunder(close, x)\n"
            '    strategy.close("L")\n')


TWIN = Build(HEADER + 'len = input.int(9, "Len")\n' + _trade(), {"Len": 23})


@dataclass(frozen=True)
class Accepted:
    """``decls`` declares the input titled through a constant expression;
    ``length`` is the EMA length that reads it."""
    name: str
    decls: str
    length: str = "len"

    def subject(self) -> Build:
        return Build(HEADER + self.decls + _trade(self.length), {"Len": 23})


ACCEPTED: tuple[Accepted, ...] = (
    Accepted("bound_name", 'T = "Len"\nlen = input.int(9, T)\n'),
    Accepted("keyword_title", 'T = "Len"\nlen = input.int(9, title=T)\n'),
    Accepted("typed_const", 'const string T = "Len"\nlen = input.int(9, T)\n'),
    Accepted("alias_chain", 'A = "Len"\nT = A\nlen = input.int(9, T)\n'),
    Accepted("concatenation", 'T = "Le" + "n"\nlen = input.int(9, T)\n'),
    Accepted("inline_concatenation", 'len = input.int(9, "Le" + "n")\n'),
    Accepted("var_nested", 'T = "Len"\nvar len = input.int(9, T) * 1\n'),
    # An inline input spelled into the TA length keeps its title expression.
    Accepted("inline_ta_length", 'T = "Len"\n', length="input.int(9, T)"),
    Accepted("inline_ta_length_concatenation", "", length='input.int(9, "Le" + "n")'),
)
ACCEPTED_BY_NAME = {c.name: c for c in ACCEPTED}


REFUSAL = ("input title {spelled}is not a constant string: TradingView declares "
           "title (const string), and PineForge keys an input override by its title. "
           "— Use a string literal, or a name bound once at global scope to one "
           '(T = "Length").')


@dataclass(frozen=True)
class Refused:
    """``decls`` is refused at ``(line, col)`` of its title (line 3 is the
    first line after the header)."""
    name: str
    decls: str
    line: int
    col: int
    spelled: str = ""


REFUSED: tuple[Refused, ...] = (
    Refused("series_title", "len = input.int(9, str.tostring(close))\n", 3, 20),
    Refused("input_value_title", 't = input.string("Len")\nlen = input.int(9, t)\n', 4, 20,
            spelled="'t' "),
    Refused("reassigned_name", 'T = "Len"\nif close > open\n    T := "Other"\n'
            "len = input.int(9, T)\n", 6, 20, spelled="'T' "),
    Refused("var_name", 'var T = "Len"\nlen = input.int(9, T)\n', 4, 20, spelled="'T' "),
    Refused("number_title", "T = 5\nlen = input.int(9, T)\n", 4, 20, spelled="'T' "),
)
REFUSED_BY_NAME = {c.name: c for c in REFUSED}


@pytest.fixture(scope="session")
def outcomes(request, tmp_path_factory) -> dict[str, Outcome]:
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("e2e_input_title_constant")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    builds: dict[str, Build] = {"twin": TWIN}
    for item in request.session.items:
        if getattr(item, "originalname", None) == "test_constant_title_is_keyed_by_its_value":
            case = ACCEPTED_BY_NAME[item.callspec.params["case_name"]]
            builds[case.name] = case.subject()
    return execute_all(engine_root, feed, base, builds)


@pytest.mark.parametrize("case_name", list(ACCEPTED_BY_NAME))
def test_constant_title_is_keyed_by_its_value(case_name: str, outcomes) -> None:
    subject = ok(outcomes, case_name)
    twin = ok(outcomes, "twin")
    titles = [e["title"] for e in subject.transpiled["inputs"]]
    assert titles == ["Len"], f"[{case_name}] manifest titles {titles}"
    failures = []
    for tag in ("default", "override"):
        a, b = subject.trades[tag], twin.trades[tag]
        if digest(a) != digest(b):
            failures.append(f"{tag} trades {summary(a)} vs the literal-title twin's {summary(b)}")
    if subject.trades["default"] == subject.trades["override"]:
        failures.append(f"override {{'Len': 23}} left the trades unchanged "
                        f"({summary(subject.trades['default'])})")
    assert not failures, f"[{case_name}] " + "\n  ".join(failures)
    print(f"E2E title {case_name}: manifest {titles} == literal twin  default "
          f"{summary(subject.trades['default'])}  override {{'Len': 23}} "
          f"{summary(subject.trades['override'])}")


@pytest.mark.parametrize("case_name", list(REFUSED_BY_NAME))
def test_non_constant_title_is_refused(case_name: str, tmp_path: Path) -> None:
    case = REFUSED_BY_NAME[case_name]
    pine = tmp_path / "strategy.pine"
    pine.write_text(HEADER + case.decls + _trade())
    result = transpile_json(pine)
    assert not result["ok"], f"[{case_name}] transpiled; its title is not a constant"
    errors = [(d["line"], d["col"], d["message"]) for d in result["diagnostics"]
              if d["severity"] == "error"]
    assert errors == [(case.line, case.col, REFUSAL.format(spelled=case.spelled))], (
        f"[{case_name}] {result['diagnostics']}")
    print(f"E2E title {case_name}: refused at {case.line}:{case.col}")
