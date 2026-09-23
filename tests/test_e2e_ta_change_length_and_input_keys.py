"""E2E: ``ta.change(source, length)`` honours its length, an input title with
``"`` or ``\\`` compiles and overrides, and an untitled ``var`` input resizes
the TA it feeds (lane C3).

Three silent codegen defects, each pinned from outside:

1. ``ta.change(src, n)`` computed a one-bar change for every ``n``. The length
   reached only the ``ta::Change`` constructor, which merely bounds the kept
   history, while ``Change::compute(src, length = 1)`` takes the lookback from
   its own argument, and the emitted call was ``compute(src)``. TradingView's
   ``ta.change(source, length)`` is ``source - source[length]``: every case
   compares it bar by bar (``@pf-trace`` values) with the spelled-out
   difference, and requires a strategy trading on it to book exactly the
   spelled-out form's trades.
2. An input title holding ``"`` or ``\\`` was pasted into the emitted C++
   string literal unescaped, so the TU did not compile.
3. ``var n = input.int(9)`` with no title: the member read the input under the
   key ``"n"`` but the TA reset re-read it under ``""``, so overriding ``n``
   never resized ``ta.ema(close, n)``.

Interfaces, as a client drives them: ``tests/_e2e.py`` (transpile through
the app's glue, compile against the built engine runtime, run on the real
15m feed with ``inputs.json`` overrides and ``--trace-json`` values).

The TA calls under an input override are direct assignments
(``x = ta.ema(close, n)``) on purpose: a TA call nested inside a larger
expression reads its precalculated series, which is sized from the input's
default, so no override reaches it in any spelling (a separate defect).

Skips cleanly without the engine environment ``tests/_e2e.py`` needs.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests._e2e import (
    Build, Outcome, derive_chart_feed, digest, execute_all, ok,
    per_bar_mismatches, skip_unless_e2e_env, summary, transpile_json,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# One header for every build: byte-compared runs differ only where meant to.
HEADER = '//@version=6\nstrategy("e2e-c3", overlay=true)\n'


def _long_on_cross(a: str, b: str) -> str:
    return (f"if ta.crossover({a}, {b})\n"
            '    strategy.entry("L", strategy.long)\n'
            f"if ta.crossunder({a}, {b})\n"
            '    strategy.close("L")\n')


def _change_source(decls: str, expr: str, extra: str = "") -> str:
    """``x`` oscillates around zero: trade its zero crossings, trace it."""
    return (f"{HEADER}{decls}x = {expr}\n{extra}// @pf-trace x=x\n"
            + _long_on_cross("x", "0"))


def _ema_source(decl: str, length: str) -> str:
    """Trade close crossing an EMA whose length is an input."""
    return (f"{HEADER}{decl}\nx = ta.ema(close, {length})\n"
            + _long_on_cross("close", "x"))


@dataclass(frozen=True)
class ChangeCase:
    """``ta.change`` spelling vs the spelled-out ``source - source[length]``.

    ``extra`` / ``ref_extra`` add further traced lines; ``override`` also runs
    both under that input override, which must move the trades, and whose
    trades must equal the literal-length case ``override_equals``.
    """
    name: str
    decls: str
    expr: str
    ref_expr: str
    extra: str = ""
    ref_extra: str = ""
    override: dict | None = None
    override_equals: str | None = None

    def subject(self) -> Build:
        return Build(_change_source(self.decls, self.expr, self.extra),
                     self.override, trace=True)

    def reference(self) -> Build:
        return Build(_change_source(self.decls, self.ref_expr, self.ref_extra),
                     self.override, trace=True)


def _literal(n: int) -> ChangeCase:
    return ChangeCase(f"lit_{n}", "", f"ta.change(close, {n})", f"close - close[{n}]")


CHANGE_CASES: tuple[ChangeCase, ...] = (
    *(_literal(n) for n in (1, 2, 14, 31)),
    ChangeCase("input_len", 'len = input.int(14, "Length")\n',
               "ta.change(close, len)", "close - close[len]",
               override={"Length": 31}, override_equals="lit_31"),
    # An untitled ``var`` length: the compute argument reads the member
    # under the key the manifest lists ("len").
    ChangeCase("var_input_len", "var len = input.int(14)\n",
               "ta.change(close, len)", "close - close[len]",
               override={"len": 31}, override_equals="lit_31"),
    ChangeCase("kwarg_len", "", "ta.change(close, length=14)", "close - close[14]"),
    ChangeCase("hl2_source", "", "ta.change(hl2, 14)", "hl2 - hl2[14]"),
    # Two call sites of one helper: each per-call-site clone gets its own
    # length.
    ChangeCase("user_function", "chg(src, n) => ta.change(src, n)\n",
               "chg(close, 14)", "close - close[14]",
               extra="y = chg(close, 2)\n// @pf-trace y=y\n",
               ref_extra="y = close - close[2]\n// @pf-trace y=y\n"),
    ChangeCase("history_ref", "", "ta.change(close, 14)[1]", "close[1] - close[15]"),
    ChangeCase("request_security", "",
               'request.security(syminfo.tickerid, "60", ta.change(close, 14))',
               'request.security(syminfo.tickerid, "60", close - close[14])'),
)


@dataclass(frozen=True)
class TitleCase:
    """An input whose title needs escaping in C++, vs the same strategy with
    the plain title ``plain``: trades equal at the defaults and under the same
    override value, keyed by each one's manifest title."""
    name: str
    template: str  # ``{title}`` is the Pine title literal
    pine_title: str
    title: str  # the title as Pine reads it: the manifest and override key
    plain: str
    value: object

    def build(self, pine_title: str, title: str) -> tuple[str, dict]:
        src = HEADER + self.template.format(title=pine_title)
        return src, {title: self.value}

    def subject(self) -> Build:
        src, ov = self.build(self.pine_title, self.title)
        return Build(src, ov)

    def twin(self) -> Build:
        src, ov = self.build(f'"{self.plain}"', self.plain)
        return Build(src, ov)


EMA_BY_INPUT = "{decl}x = ta.ema(close, len)\n" + _long_on_cross("close", "x")

TITLE_CASES: tuple[TitleCase, ...] = (
    TitleCase("double_quotes_and_backslashes",
              EMA_BY_INPUT.replace("{decl}", "len = input.int(9, {title})\n"),
              r'"He said \"fast\" \\ C:\\bars"', 'He said "fast" \\ C:\\bars',
              "fast", 23),
    TitleCase("single_quoted",
              EMA_BY_INPUT.replace("{decl}", "len = input.int(9, {title})\n"),
              r"""'say "hi" \\ bye'""", 'say "hi" \\ bye', "fast", 23),
    TitleCase("keyword_title",
              EMA_BY_INPUT.replace("{decl}", "len = input.int(defval=9, title={title})\n"),
              r'"a\\b \"c\""', 'a\\b "c"', "fast", 23),
    TitleCase("var_binding",
              EMA_BY_INPUT.replace("{decl}", "var len = input.int(9, {title})\n"),
              r'"Len \"v\" \\ x"', 'Len "v" \\ x', "fast", 23),
    # An inline input call outside any TA constructor.
    TitleCase("inline",
              'x = ta.ema(close, 9) + input.float(0.0, {title})\n'
              + _long_on_cross("close", "x"),
              r'"Offset \"pts\" \\ abs"', 'Offset "pts" \\ abs', "offset", 25.0),
    # input.source reads through get_input_source, also in precalculate().
    TitleCase("source",
              'src = input.source(close, {title})\nx = ta.ema(src, 9)\n'
              + _long_on_cross("close", "x"),
              r'"Source \"px\" \\"', 'Source "px" \\', "src", "open"),
)


# A string constant is inlined where it is read: it needs the same escaping.
# ``say "hi" \`` is 10 characters, so the twin adds the literal 10.
CONST_STRING = Build(
    HEADER + 'Q = "say \\"hi\\" \\\\"\n'
    'x = ta.ema(close, 9) + str.length(Q)\n' + _long_on_cross("close", "x"))
CONST_STRING_TWIN = Build(
    HEADER + 'x = ta.ema(close, 9) + 10\n' + _long_on_cross("close", "x"))


@dataclass(frozen=True)
class KeyCase:
    """An untitled ``var`` input feeding a TA length: overriding it by its
    manifest title ``key`` must resize the TA exactly like the titled
    spelling ``n = input.int(9, "fast")`` under the same value."""
    name: str
    decl: str
    key: str = "n"


KEY_CASES: tuple[KeyCase, ...] = (
    KeyCase("var_untitled", "var n = input.int(9)"),
    KeyCase("var_int_untitled", "var int n = input.int(9)"),
    KeyCase("var_untitled_defval_kwarg", "var n = input.int(defval=9)"),
    KeyCase("var_untitled_minval", "var n = input.int(9, minval=1, maxval=500)"),
    KeyCase("var_titled", 'var n = input.int(9, "fast")', key="fast"),
    # Control: the non-var untitled binding was already keyed "n".
    KeyCase("plain_untitled", "n = input.int(9)"),
)
KEY_OVERRIDE = 23
TITLED_TWIN = Build(_ema_source('n = input.int(9, "fast")', "n"),
                    {"fast": KEY_OVERRIDE})


def _key_build(case: KeyCase) -> Build:
    return Build(_ema_source(case.decl, "n"),
                 {case.key: KEY_OVERRIDE})


def _builds_for(kind: str, name: str) -> dict[str, Build]:
    if kind == "change":
        case = CHANGE_BY_NAME[name]
        out = {f"change/{name}/subject": case.subject(),
               f"change/{name}/reference": case.reference()}
        if case.override_equals:
            lit = CHANGE_BY_NAME[case.override_equals]
            out[f"change/{lit.name}/subject"] = lit.subject()
        return out
    if kind == "title":
        case = TITLE_BY_NAME[name]
        return {f"title/{name}/subject": case.subject(),
                f"title/{name}/twin": case.twin()}
    if kind == "const":
        return {"const/subject": CONST_STRING, "const/twin": CONST_STRING_TWIN}
    case = KEY_BY_NAME[name]
    return {f"key/{name}": _key_build(case), "key/titled_twin": TITLED_TWIN}


CHANGE_BY_NAME = {c.name: c for c in CHANGE_CASES}
TITLE_BY_NAME = {c.name: c for c in TITLE_CASES}
KEY_BY_NAME = {c.name: c for c in KEY_CASES}


# ---------------------------------------------------------------------------
# Execution: every build the selected tests need is transpiled, compiled and
# run in parallel once per session; each test then judges its own case.
# ---------------------------------------------------------------------------

_TESTS = {
    "test_ta_change_equals_spelled_out_difference": "change",
    "test_input_title_needing_escapes_compiles_and_overrides": "title",
    "test_untitled_var_input_override_resizes_ta": "key",
    "test_string_constant_needing_escapes_compiles": "const",
}


@pytest.fixture(scope="session")
def outcomes(request, tmp_path_factory) -> dict[str, Outcome]:
    engine_root = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("e2e_ta_change_input_keys")
    feed = derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    builds: dict[str, Build] = {}
    for item in request.session.items:
        kind = _TESTS.get(getattr(item, "originalname", None))
        if kind is not None:
            params = getattr(item, "callspec", None)
            builds.update(_builds_for(kind, params.params["case_name"] if params else ""))
    return execute_all(engine_root, feed, base, builds)


@pytest.mark.parametrize("case_name", list(CHANGE_BY_NAME))
def test_ta_change_equals_spelled_out_difference(case_name: str, outcomes) -> None:
    case = CHANGE_BY_NAME[case_name]
    subject = ok(outcomes, f"change/{case_name}/subject")
    reference = ok(outcomes, f"change/{case_name}/reference")
    failures = []
    lines = []
    for tag in subject.trades:
        compared, mismatched, first = per_bar_mismatches(
            subject.traces[tag], reference.traces[tag], ("ta.change", "spelled-out"))
        a, b = subject.trades[tag], reference.trades[tag]
        if mismatched:
            failures.append(
                f"{tag}: ta.change differs from the spelled-out difference on "
                f"{mismatched} of {compared} traced bars; first: {first}")
        if digest(a) != digest(b):
            failures.append(f"{tag}: trades differ: ta.change {summary(a)} vs "
                            f"spelled-out {summary(b)}")
        lines.append(f"{tag} {compared} bars equal, {summary(a)}")
    if case.override is not None:
        default, override = subject.trades["default"], subject.trades["override"]
        if default == override:
            failures.append(f"override {case.override} left the trades unchanged "
                            f"({summary(default)})")
        lit = ok(outcomes, f"change/{case.override_equals}/subject")
        if digest(override) != digest(lit.trades["default"]):
            failures.append(
                f"override {case.override} gives {summary(override)}, not the "
                f"{case.override_equals} build's {summary(lit.trades['default'])}")
        else:
            lines.append(f"override == {case.override_equals}")
    assert not failures, f"[{case_name}] " + "\n  ".join(failures)
    print(f"E2E change {case_name}: ta.change == spelled-out  " + "  ".join(lines))


@pytest.mark.parametrize("case_name", list(TITLE_BY_NAME))
def test_input_title_needing_escapes_compiles_and_overrides(case_name: str, outcomes) -> None:
    case = TITLE_BY_NAME[case_name]
    subject = ok(outcomes, f"title/{case_name}/subject")
    twin = ok(outcomes, f"title/{case_name}/twin")
    for tag in ("default", "override"):
        a, b = subject.trades[tag], twin.trades[tag]
        assert digest(a) == digest(b), (
            f"[{case_name}] {tag} trades differ: {summary(a)} vs the plain-title "
            f"twin's {summary(b)}")
    assert subject.trades["default"] != subject.trades["override"], (
        f"[{case_name}] override {subject.build.overrides} left the trades unchanged")
    titles = [e["title"] for e in subject.transpiled["inputs"]]
    assert titles == [case.title], f"[{case_name}] manifest titles {titles}"
    print(f"E2E title {case_name}: title {case.title!r} == plain {case.plain!r}  "
          f"default {summary(subject.trades['default'])}  override "
          f"{subject.build.overrides} {summary(subject.trades['override'])}")


@pytest.mark.parametrize("case_name", list(KEY_BY_NAME))
def test_untitled_var_input_override_resizes_ta(case_name: str, outcomes) -> None:
    case = KEY_BY_NAME[case_name]
    subject = ok(outcomes, f"key/{case_name}")
    twin = ok(outcomes, "key/titled_twin")
    titles = [e["title"] for e in subject.transpiled["inputs"]]
    assert titles == [case.key], f"[{case_name}] manifest titles {titles}"
    failures = []
    for tag in ("default", "override"):
        a, b = subject.trades[tag], twin.trades[tag]
        if digest(a) != digest(b):
            failures.append(f"{tag} trades {summary(a)} vs the titled twin's "
                            f"{summary(b)}")
    if subject.trades["default"] == subject.trades["override"]:
        failures.append(f"override {subject.build.overrides} left the trades "
                        f"unchanged ({summary(subject.trades['default'])})")
    assert not failures, f"[{case_name}] " + "\n  ".join(failures)
    print(f"E2E key {case_name}: {case.decl!r} == titled twin  default "
          f"{summary(subject.trades['default'])}  override "
          f"{subject.build.overrides} {summary(subject.trades['override'])}")


def test_string_constant_needing_escapes_compiles(outcomes) -> None:
    subject = ok(outcomes, "const/subject")
    twin = ok(outcomes, "const/twin")
    a, b = subject.trades["default"], twin.trades["default"]
    assert digest(a) == digest(b), (
        f"str.length of the constant: {summary(a)} vs the literal 10's {summary(b)}")
    print(f"E2E const string: str.length(Q) == 10  {summary(a)}")


def test_ta_change_length_from_a_request_security_helper_param_is_refused(tmp_path) -> None:
    """A request.security evaluator is a class method, so a TA compute()
    argument naming a parameter of the helper holding the request is out of
    scope there -- refused like ``ta.sma(src, 14)`` or ``ta.linreg(close,
    14, off)`` with ``src`` / ``off`` parameters. Before the length reached
    compute() this shape transpiled and silently computed a one-bar change."""
    pine = tmp_path / "strategy.pine"
    pine.write_text(HEADER + 'htf(n) => request.security(syminfo.tickerid, "60", '
                    "ta.change(close, n))\nx = htf(14)\n" + _long_on_cross("x", "0"))
    result = transpile_json(pine)
    assert not result["ok"], "a helper-parameter length inside request.security transpiled"
    assert [(d["line"], d["col"], d["message"]) for d in result["diagnostics"]] == [
        (3, 69, "Unknown variable 'n' — not a PineForge builtin or a declared variable.")]
