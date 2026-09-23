"""E2E: an untitled input nested in a plain declaration is keyed by the
declared name, and inputs of one script that share an override key are
flagged with a warning (lane C5, defect 3).

TradingView (Pine v6 reference, ``input.int``): ``title (const string) Title
of the input. If not specified, the variable name is used as the input's
title.`` C4 keyed an untitled call bound straight to a name (``n =
input.int(9)``) or nested in a ``var`` initializer by that name; one nested in
a plain declaration (``n = input.int(9) * 2``, ``x = ta.ema(close,
input.int(9))``) was keyed ``""`` in the manifest and the C++ alike, so every
such input of a script answered to one override.

PineForge sets an input override by its key -- the title, else the name of
the declaration holding the call -- so inputs sharing a key cannot be
overridden apart: one override sets all of them. TradingView tells them apart
(a title repeated across input groups is common, e.g. an "M" minute field in
several time rows), so such a script keeps transpiling unchanged -- at its
defaults every input reads its own default -- and one warning per shared key
names every input the key reaches. Each such case must carry exactly its
warnings and book the trades the same script books when transpiled by
``PRE_C5`` (e6a64cd).

Each keyed case runs its untitled shape and the same strategy with an explicit
title at the defaults and under the same override value, keyed by each one's
manifest title: the trades must be identical, and the override must move them.

Interfaces: ``tests/_e2e.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests._e2e import (
    Build, Outcome, derive_chart_feed, digest, execute_all, ok,
    reference_codegen, skip_unless_e2e_env, summary,
)


HEADER = '//@version=6\nstrategy("e2e-c5-nested-input-keys", overlay=true)\n'


def _long_on_cross(a: str, b: str) -> str:
    return (f"if ta.crossover({a}, {b})\n"
            '    strategy.entry("L", strategy.long)\n'
            f"if ta.crossunder({a}, {b})\n"
            '    strategy.close("L")\n')


@dataclass(frozen=True)
class KeyCase:
    """``body`` holds ``{title}``: nothing in the subject (an untitled input,
    keyed ``key``), ``, "<twin_title>"`` in the twin. Both run under
    ``{<their key>: value}``."""
    name: str
    body: str
    key: str
    value: object
    twin_title: str = "fast"

    def subject(self) -> Build:
        return Build(HEADER + self.body.replace("{title}", ""), {self.key: self.value})

    def twin(self) -> Build:
        return Build(HEADER + self.body.replace("{title}", f', "{self.twin_title}"'),
                     {self.twin_title: self.value})


CASES: tuple[KeyCase, ...] = (
    KeyCase("product", "n = input.int(9{title}) * 2\nx = ta.ema(close, n)\n"
            + _long_on_cross("close", "x"), key="n", value=12),
    KeyCase("call_argument", "n = math.max(input.int(9{title}), 2)\nx = ta.ema(close, n)\n"
            + _long_on_cross("close", "x"), key="n", value=23),
    KeyCase("offset", "len = input.int(9{title}) + 1\nx = ta.ema(close, len)\n"
            + _long_on_cross("close", "x"), key="len", value=22),
    KeyCase("float_band_mult",
            "mult = input.float(2.0{title}) * 1.0\n[mid, up, lo] = ta.bb(close, 20, mult)\n"
            'if ta.crossover(close, up)\n    strategy.entry("L", strategy.long)\n'
            'if ta.crossunder(close, mid)\n    strategy.close("L")\n',
            key="mult", value=1.0, twin_title="Mult"),
    # The call is an argument of the TA call a declaration holds.
    KeyCase("inline_ta_length", "x = ta.ema(close, input.int(9{title}))\n"
            + _long_on_cross("close", "x"), key="x", value=23),
    # A value the strategy reads per bar, not a TA length.
    KeyCase("condition_threshold",
            "gap = input.float(0.2{title}) / 100\nx = ta.ema(close, 20)\n"
            + _long_on_cross("close", "x * (1 + gap)"), key="gap", value=1.0,
            twin_title="Gap"),
    # Controls: shapes already keyed by the declared name.
    KeyCase("direct", "len = input.int(9{title})\nx = ta.ema(close, len)\n"
            + _long_on_cross("close", "x"), key="len", value=23),
    KeyCase("var_nested", "var n = input.int(9{title}) * 2\nx = ta.ema(close, n)\n"
            + _long_on_cross("close", "x"), key="n", value=12),
)
CASES_BY_NAME = {c.name: c for c in CASES}
assert len(CASES_BY_NAME) == len(CASES), "duplicate case name"


def _shared(key: str, *spots: tuple[int, int]) -> str:
    listed = [f"{line}:{col}" for line, col in spots]
    return (f"input key '{key}' is shared by the inputs at {', '.join(listed[:-1])} and "
            f"{listed[-1]}: PineForge sets an input override by its title, else by the "
            "name of the declaration holding it, so one override sets all of them. — "
            "Give each input its own title to override them apart.")


# The codegen before lane C5 (origin/main when the lane was rebased).
PRE_C5 = "e6a64cd83fd61bf82723bc74c77a8c1555c65897"


@dataclass(frozen=True)
class Shared:
    """``body`` transpiles with one warning per shared key -- ``(key, spots)``,
    anchored at the key's second input (line 3 is the first body line) -- and
    books ``PRE_C5``'s trades for the same script."""
    name: str
    body: str
    keys: tuple[tuple[str, tuple[tuple[int, int], ...]], ...]

    def subject(self) -> Build:
        return Build(HEADER + self.body)

    def pre_c5(self, codegen) -> Build:
        return Build(HEADER + self.body, codegen=codegen)

    def warnings(self) -> list[tuple[int, int, str]]:
        return [(spots[1][0], spots[1][1], _shared(key, *spots)) for key, spots in self.keys]


SHARED: tuple[Shared, ...] = (
    Shared("nested_and_titled", 'a = input.int(9) * 2\nb = input.int(14, "a")\n'
           "x = ta.ema(close, a + b)\n" + _long_on_cross("close", "x"),
           (("a", ((3, 5), (4, 5))),)),
    Shared("two_in_one_declaration", "n = input.int(9) + input.int(3)\n"
           "x = ta.ema(close, n)\n" + _long_on_cross("close", "x"),
           (("n", ((3, 5), (3, 20))),)),
    Shared("same_title", 'a = input.int(9, "Len")\nb = input.int(14, "Len")\n'
           "x = ta.ema(close, a + b)\n" + _long_on_cross("close", "x"),
           (("Len", ((3, 5), (4, 5))),)),
    Shared("bound_name_and_title", 'len = input.int(9)\nfast = input.int(12, "len")\n'
           "x = ta.ema(close, len + fast)\n" + _long_on_cross("close", "x"),
           (("len", ((3, 7), (4, 8))),)),
    Shared("two_untitled_undeclared",
           "if ta.crossover(close, ta.sma(close, input.int(9)))\n"
           '    strategy.entry("L", strategy.long)\n'
           "if ta.crossunder(close, ta.sma(close, input.int(21)))\n"
           '    strategy.close("L")\n', (("", ((3, 38), (5, 39))),)),
    # Closed strategy 162-nicocashfx's shape: titles repeated across groups.
    Shared("titles_repeated_across_groups",
           'h1 = input.int(9, "H", group="Start", inline="s")\n'
           'm1 = input.int(30, "M", group="Start", inline="s")\n'
           'h2 = input.int(17, "H", group="End", inline="e")\n'
           'm2 = input.int(0, "M", group="End", inline="e")\n'
           'm3 = input.int(15, "M", group="Line")\n'
           "x = ta.ema(close, h1 + m1 + h2 + m2 + m3)\n" + _long_on_cross("close", "x"),
           (("H", ((3, 6), (5, 6))), ("M", ((4, 6), (6, 6), (7, 6))))),
)
SHARED_BY_NAME = {c.name: c for c in SHARED}
assert len(SHARED_BY_NAME) == len(SHARED), "duplicate case name"


@pytest.fixture(scope="session")
def outcomes(request, tmp_path_factory) -> dict[str, Outcome]:
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("e2e_nested_input_keys")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    builds: dict[str, Build] = {}
    pre_c5 = reference_codegen(PRE_C5)
    for item in request.session.items:
        name = getattr(item, "originalname", None)
        if name == "test_nested_untitled_input_reads_its_declared_name":
            case = CASES_BY_NAME[item.callspec.params["case_name"]]
            builds[f"{case.name}/subject"] = case.subject()
            builds[f"{case.name}/twin"] = case.twin()
        elif name == "test_inputs_sharing_a_key_are_flagged":
            shared = SHARED_BY_NAME[item.callspec.params["case_name"]]
            builds[f"{shared.name}/subject"] = shared.subject()
            if pre_c5 is not None:
                builds[f"{shared.name}/pre_c5"] = shared.pre_c5(pre_c5)
    return execute_all(engine_root, feed, base, builds)


@pytest.mark.parametrize("case_name", list(CASES_BY_NAME))
def test_nested_untitled_input_reads_its_declared_name(case_name: str, outcomes) -> None:
    case = CASES_BY_NAME[case_name]
    subject = ok(outcomes, f"{case_name}/subject")
    twin = ok(outcomes, f"{case_name}/twin")
    titles = [e["title"] for e in subject.transpiled["inputs"]]
    failures = []
    if titles != [case.key]:
        failures.append(f"manifest titles {titles}, not [{case.key!r}]")
    for tag in ("default", "override"):
        a, b = subject.trades[tag], twin.trades[tag]
        if digest(a) != digest(b):
            failures.append(f"{tag} trades {summary(a)} vs the titled twin's {summary(b)}")
    if subject.trades["default"] == subject.trades["override"]:
        failures.append(f"override {subject.build.overrides} left the trades unchanged "
                        f"({summary(subject.trades['default'])})")
    assert not failures, f"[{case_name}] " + "\n  ".join(failures)
    print(f"E2E nested-key {case_name}: untitled == titled twin  manifest {titles}  default "
          f"{summary(subject.trades['default'])}  override {subject.build.overrides} "
          f"{summary(subject.trades['override'])}")


@pytest.mark.parametrize("case_name", list(SHARED_BY_NAME))
def test_inputs_sharing_a_key_are_flagged(case_name: str, outcomes) -> None:
    case = SHARED_BY_NAME[case_name]
    subject = ok(outcomes, f"{case_name}/subject")
    if f"{case_name}/pre_c5" not in outcomes:
        pytest.skip(f"the pre-C5 codegen ({PRE_C5[:12]}) is not in this checkout's history")
    pre_c5 = ok(outcomes, f"{case_name}/pre_c5")
    warnings = [(d["line"], d["col"], d["message"])
                for d in subject.transpiled.get("diagnostics", [])
                if d["severity"] == "warning" and d["message"].startswith("input key")]
    assert warnings == case.warnings(), f"[{case_name}] warnings {warnings}"
    a, b = subject.trades["default"], pre_c5.trades["default"]
    assert digest(a) == digest(b), (
        f"[{case_name}] trades {summary(a)} vs the pre-C5 build's {summary(b)}")
    same_cpp = subject.transpiled["cpp"] == pre_c5.transpiled["cpp"]
    print(f"E2E nested-key {case_name}: warned at "
          f"{', '.join(f'{w[0]}:{w[1]}' for w in warnings)} "
          f"(keys {[k for k, _ in case.keys]!r}), trades == pre-C5 ({PRE_C5[:7]}) build  "
          f"{summary(a)}  C++ identical to pre-C5: {same_cpp}")
