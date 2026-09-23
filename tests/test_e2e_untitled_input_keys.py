"""E2E: an untitled input is read under one key -- its declared name, the key
the manifest lists -- by the member, the TA reset, a request.security
timeframe and every other getter (lane C4, defect 4).

TradingView (Pine v6 reference, ``input.int``): ``title (const string) Title
of the input. If not specified, the variable name is used as the input's
title.`` PineForge's manifest follows it, but several getters re-derived the
key from a re-spelled call or an alias name:

* ``tf = input.timeframe("60")`` as a ``request.security`` timeframe was
  read under ``""``;
* ``len = input.int(9)`` with a stable ``len := ...`` reassignment: the TA
  reset read the folded ``(cond ? 30 : input.int(9))`` under ``""``;
* ``var n = input.int(9) * 2``: the reset read the nested call under ``""``;
* ``a = input.int(9)`` then ``b = a``: the reset read it under ``"b"``.

The override then set the member but never resized the TA (or retargeted
the security feed). Each case runs its untitled shape and the same strategy
with an explicit title at the defaults and under the same override value,
keyed by each one's manifest title: the trades must be identical, and the
override must move them.

Interfaces: ``tests/_e2e.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests._e2e import (
    Build, Outcome, derive_chart_feed, digest, execute_all, ok,
    skip_unless_e2e_env, summary,
)


HEADER = '//@version=6\nstrategy("e2e-c4-input-keys", overlay=true)\n'


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
    # A request.security timeframe, directly and through an alias.
    KeyCase("security_timeframe",
            'tf = input.timeframe("60"{title})\n'
            "x = request.security(syminfo.tickerid, tf, ta.ema(close, 9))\n"
            + _long_on_cross("close", "x"), key="tf", value="240", twin_title="TF"),
    KeyCase("security_timeframe_alias",
            'tf = input.timeframe("60"{title})\ntf2 = tf\n'
            "x = request.security(syminfo.tickerid, tf2, ta.ema(close, 9))\n"
            + _long_on_cross("close", "x"), key="tf", value="240", twin_title="TF"),
    # A length with a stable reassignment (folded into the TA reset).
    KeyCase("stable_reassignment",
            "len = input.int(9{title})\nif timeframe.isdaily\n    len := 30\n"
            "x = ta.ema(close, len)\n" + _long_on_cross("close", "x"),
            key="len", value=23),
    # An untitled call nested in a var initializer.
    KeyCase("var_nested",
            "var n = input.int(9{title}) * 2\nx = ta.ema(close, n)\n"
            + _long_on_cross("close", "x"), key="n", value=12),
    KeyCase("var_nested_call",
            "var n = math.max(input.int(9{title}), 2)\nx = ta.ema(close, n)\n"
            + _long_on_cross("close", "x"), key="n", value=23),
    KeyCase("var_nested_float",
            "var mult = input.float(2.0{title}) * 1.0\n"
            "[mid, up, lo] = ta.bb(close, 20, mult)\n"
            'if ta.crossover(close, up)\n    strategy.entry("L", strategy.long)\n'
            'if ta.crossunder(close, mid)\n    strategy.close("L")\n',
            key="mult", value=1.0, twin_title="Mult"),
    # An alias of an untitled input feeding a TA length and a compute() one.
    KeyCase("alias",
            "a = input.int(9{title})\nb = a\nx = ta.ema(close, b)\n"
            + _long_on_cross("close", "x"), key="a", value=23),
    KeyCase("alias_change_length",
            "a = input.int(9{title})\nb = a\nx = ta.change(close, b)\n"
            + _long_on_cross("x", "0"), key="a", value=23),
    # Controls: shapes already keyed by the declared name.
    KeyCase("direct", "len = input.int(9{title})\nx = ta.ema(close, len)\n"
            + _long_on_cross("close", "x"), key="len", value=23),
    KeyCase("source", "src = input.source(close{title})\nx = ta.ema(src, 9)\n"
            + _long_on_cross("close", "x"), key="src", value="open", twin_title="Src"),
    KeyCase("security_length",
            "len = input.int(9{title})\n"
            'x = request.security(syminfo.tickerid, "60", ta.ema(close, len))\n'
            + _long_on_cross("close", "x"), key="len", value=23),
)
CASES_BY_NAME = {c.name: c for c in CASES}
assert len(CASES_BY_NAME) == len(CASES), "duplicate case name"


@pytest.fixture(scope="session")
def outcomes(request, tmp_path_factory) -> dict[str, Outcome]:
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("e2e_untitled_input_keys")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    builds: dict[str, Build] = {}
    for item in request.session.items:
        if getattr(item, "originalname", None) == "test_untitled_input_reads_its_manifest_key":
            case = CASES_BY_NAME[item.callspec.params["case_name"]]
            builds[f"{case.name}/subject"] = case.subject()
            builds[f"{case.name}/twin"] = case.twin()
    return execute_all(engine_root, feed, base, builds)


@pytest.mark.parametrize("case_name", list(CASES_BY_NAME))
def test_untitled_input_reads_its_manifest_key(case_name: str, outcomes) -> None:
    case = CASES_BY_NAME[case_name]
    subject = ok(outcomes, f"{case_name}/subject")
    twin = ok(outcomes, f"{case_name}/twin")
    titles = [e["title"] for e in subject.transpiled["inputs"]]
    assert case.key in titles and "" not in titles, f"[{case_name}] manifest titles {titles}"
    failures = []
    for tag in ("default", "override"):
        a, b = subject.trades[tag], twin.trades[tag]
        if digest(a) != digest(b):
            failures.append(f"{tag} trades {summary(a)} vs the titled twin's {summary(b)}")
    if subject.trades["default"] == subject.trades["override"]:
        failures.append(f"override {subject.build.overrides} left the trades unchanged "
                        f"({summary(subject.trades['default'])})")
    assert not failures, f"[{case_name}] " + "\n  ".join(failures)
    print(f"E2E key {case_name}: untitled == titled twin  manifest {titles}  default "
          f"{summary(subject.trades['default'])}  override {subject.build.overrides} "
          f"{summary(subject.trades['override'])}")
