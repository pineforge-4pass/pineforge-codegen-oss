"""E2E: a generic ``input()`` in arithmetic is a TA length the way
``input.int()`` is (lane C5, defect 4).

``len = input.int(9) + 1`` feeding ``ta.ema(close, len)`` sizes the EMA from
the input at run time (the ``_ta_initialized_`` reset re-reads the override),
and so does the inline ``ta.ema(close, input(9) + 1)``: an inline input call
is one leaf of a TA length when its bound spelling would be input-backed --
not a source input, a constant defval (``_is_stable_inline_input``, lanes
C2 / C4). The declared ``len = input(9) + 1`` was refused ("Unsupported TA
constructor length 'len'"): the stability classifier behind a derived length
(``_expr_is_stable``) admitted ``input.<type>()`` calls but not the generic
``input()``, which it read as an unknown user function.

Each case runs its generic spelling and the same strategy spelled with
``input.int`` / ``input.float`` (identical manifest key) at the defaults and
under the same override: the trades must be identical, and the override must
move them.

Interfaces: ``tests/_e2e.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests._e2e import (
    Build, Outcome, derive_chart_feed, digest, execute_all, ok,
    skip_unless_e2e_env, summary,
)


HEADER = '//@version=6\nstrategy("e2e-c5-generic-input-length", overlay=true)\n'


def _long_on_cross(a: str, b: str) -> str:
    return (f"if ta.crossover({a}, {b})\n"
            '    strategy.entry("L", strategy.long)\n'
            f"if ta.crossunder({a}, {b})\n"
            '    strategy.close("L")\n')


BB_TRADE = ('if ta.crossover(close, up)\n    strategy.entry("L", strategy.long)\n'
            'if ta.crossunder(close, mid)\n    strategy.close("L")\n')


@dataclass(frozen=True)
class GenericCase:
    """``body`` spells its input ``{input}``: ``input`` in the subject,
    ``input.<twin_type>`` in the twin. Both run under ``{key: value}``."""
    name: str
    body: str
    key: str
    value: object
    twin_type: str = "int"

    def subject(self) -> Build:
        return Build(HEADER + self.body.replace("{input}", "input"), {self.key: self.value})

    def twin(self) -> Build:
        return Build(HEADER + self.body.replace("{input}", f"input.{self.twin_type}"),
                     {self.key: self.value})


CASES: tuple[GenericCase, ...] = (
    GenericCase("offset", "len = {input}(9) + 1\nx = ta.ema(close, len)\n"
                + _long_on_cross("close", "x"), key="len", value=22),
    GenericCase("product", "len = {input}(9) * 2\nx = ta.ema(close, len)\n"
                + _long_on_cross("close", "x"), key="len", value=12),
    GenericCase("call_argument", "len = math.max({input}(9), 2)\nx = ta.ema(close, len)\n"
                + _long_on_cross("close", "x"), key="len", value=23),
    GenericCase("titled", 'len = {input}(9, "Length") + 1\nx = ta.ema(close, len)\n'
                + _long_on_cross("close", "x"), key="Length", value=22),
    GenericCase("var_declared", "var len = {input}(9) + 1\nx = ta.ema(close, len)\n"
                + _long_on_cross("close", "x"), key="len", value=22),
    GenericCase("float_band_mult",
                "mult = {input}(2.0) * 1.0\n[mid, up, lo] = ta.bb(close, 20, mult)\n" + BB_TRADE,
                key="mult", value=1.0, twin_type="float"),
    GenericCase("request_security",
                "len = {input}(9) + 1\n"
                'x = request.security(syminfo.tickerid, "60", ta.ema(close, len))\n'
                + _long_on_cross("close", "x"), key="len", value=22),
    GenericCase("nested_user_function",
                "f(n) => ta.ema(close, n)\nlen = {input}(9) + 1\nx = f(len)\n"
                + _long_on_cross("close", "x"), key="len", value=22),
    # Controls: spellings that already sized the TA from the input.
    GenericCase("inline", "x = ta.ema(close, {input}(9) + 1)\n"
                + _long_on_cross("close", "x"), key="x", value=22),
    GenericCase("bound_then_derived", "len = {input}(9)\nlen2 = len + 1\nx = ta.ema(close, len2)\n"
                + _long_on_cross("close", "x"), key="len", value=21),
)
CASES_BY_NAME = {c.name: c for c in CASES}
assert len(CASES_BY_NAME) == len(CASES), "duplicate case name"


@pytest.fixture(scope="session")
def outcomes(request, tmp_path_factory) -> dict[str, Outcome]:
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("e2e_generic_input_length")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    builds: dict[str, Build] = {}
    for item in request.session.items:
        if getattr(item, "originalname", None) == "test_generic_input_length_equals_typed_twin":
            case = CASES_BY_NAME[item.callspec.params["case_name"]]
            builds[f"{case.name}/subject"] = case.subject()
            builds[f"{case.name}/twin"] = case.twin()
    return execute_all(engine_root, feed, base, builds)


@pytest.mark.parametrize("case_name", list(CASES_BY_NAME))
def test_generic_input_length_equals_typed_twin(case_name: str, outcomes) -> None:
    case = CASES_BY_NAME[case_name]
    subject = ok(outcomes, f"{case_name}/subject")
    twin = ok(outcomes, f"{case_name}/twin")
    failures = []
    titles = [e["title"] for e in subject.transpiled["inputs"]]
    if titles != [case.key]:
        failures.append(f"manifest titles {titles}, not [{case.key!r}]")
    for tag in ("default", "override"):
        a, b = subject.trades[tag], twin.trades[tag]
        if digest(a) != digest(b):
            failures.append(f"{tag} trades {summary(a)} vs the input.{case.twin_type} twin's "
                            f"{summary(b)}")
    if subject.trades["default"] == subject.trades["override"]:
        failures.append(f"override {subject.build.overrides} left the trades unchanged "
                        f"({summary(subject.trades['default'])})")
    assert not failures, f"[{case_name}] " + "\n  ".join(failures)
    print(f"E2E generic-input {case_name}: input() == input.{case.twin_type}()  manifest "
          f"{titles}  default {summary(subject.trades['default'])}  override "
          f"{subject.build.overrides} {summary(subject.trades['override'])}")
