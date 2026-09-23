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

Interfaces, as a client drives them:

1. transpile: the ``transpile_json`` contract of pineforge-app's Pyodide glue
   (``gate/glue.py`` is its canonical copy), run as a subprocess. Its JSON
   carries the C++ and the input manifest the app builds its override form
   from.
2. compile: the emitted TU as a strategy shared library linked against the
   built engine runtime (the engine's ``corpus/CMakeLists.txt`` recipe).
3. run: the engine's ``scripts/run_strategy.py`` C-ABI harness over the
   corpus's real ETH-USDT 1m feed, resampled to the 15m chart feed by the
   engine's own ``derive_corpus_feeds`` resampler into a temp dir (never into
   the engine checkout). Overrides travel through ``inputs.json`` ->
   ``strategy_set_input``, keyed by the manifest title; per-bar values come
   back through ``--trace-json``.

The TA calls under an input override are direct assignments
(``x = ta.ema(close, n)``) on purpose: a TA call nested inside a larger
expression reads its precalculated series, which is sized from the input's
default, so no override reaches it in any spelling (a separate defect).

Needs PINEFORGE_ENGINE_INCLUDE (+ Eigen) and a built runtime
(PINEFORGE_ENGINE_LIB), with the engine checkout's ``scripts/`` and corpus
feed beside the include dir. Skips cleanly otherwise, like every compile test.
"""

from __future__ import annotations

import concurrent.futures
import csv
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests import _compile as compile_env


REPO_ROOT = Path(__file__).resolve().parent.parent
GLUE_DIR = REPO_ROOT / "gate"


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Build:
    """One strategy source and the input overrides it runs under."""
    source: str
    overrides: dict | None = None
    trace: bool = False


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
# Interfaces
# ---------------------------------------------------------------------------

def _engine_root() -> Path | None:
    inc = compile_env._ENGINE_INC
    return inc.parent if inc is not None else None


def _skip_unless_e2e_env() -> Path:
    compile_env.skip_if_no_engine_lib()
    root = _engine_root()
    assert root is not None
    if not (root / "scripts" / "run_strategy.py").is_file():
        pytest.skip(f"engine checkout {root} has no scripts/run_strategy.py")
    feed = root / "corpus" / "data" / "ohlcv_ETH-USDT-USDT_1m.csv"
    if not feed.is_file() or feed.stat().st_size < 1_000_000:
        pytest.skip(f"corpus 1m feed missing or an unsmudged LFS pointer: {feed}")
    return root


def _derive_chart_feed(engine_root: Path, out: Path) -> Path:
    """The corpus's 15m chart feed, via the engine's own resampler."""
    spec = importlib.util.spec_from_file_location(
        "_pf_derive_corpus_feeds", engine_root / "scripts" / "derive_corpus_feeds.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with mod.SOURCE_1M.open() as fh:
        next(fh)
        buckets = mod._resample_15m(fh)
    out.write_text("\n".join([mod.HEADER, *map(mod._format_bucket, buckets)]) + "\n")
    return out


_GLUE_MAIN = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import glue\n"
    "sys.stdout.write(glue.transpile_json(open(sys.argv[2], encoding='utf-8').read()))\n"
)


def transpile_json(pine: Path) -> dict:
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    proc = subprocess.run(
        [sys.executable, "-c", _GLUE_MAIN, str(GLUE_DIR), str(pine)],
        capture_output=True, text=True, timeout=300, env=env, cwd=REPO_ROOT)
    if proc.returncode != 0:
        raise RuntimeError(f"transpile_json crashed on {pine}:\n{proc.stderr}")
    return json.loads(proc.stdout)


def build_strategy_library(cpp: str, workdir: Path) -> None:
    (workdir / "generated.cpp").write_text(cpp)
    lib = str(compile_env._ENGINE_LIB)
    if sys.platform == "darwin":
        link = [f"-Wl,-force_load,{lib}"]
    else:
        link = ["-Wl,--whole-archive", lib, "-Wl,--no-whole-archive"]
    cmd = [compile_env._COMPILER, "-std=c++17", "-O2", "-fPIC", "-shared",
           *compile_env._include_flags(isolate_headers=True),
           str(workdir / "generated.cpp"), *link, "-o", str(workdir / "strategy.so")]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"compile failed in {workdir}:\n"
                           + "\n".join(proc.stderr.splitlines()[:20]))


def run_strategy(engine_root: Path, workdir: Path, feed: Path,
                 overrides: dict | None, tag: str, trace: bool
                 ) -> tuple[bytes, list[dict] | None]:
    out = workdir / f"engine_trades_{tag}.csv"
    cmd = [sys.executable, str(engine_root / "scripts" / "run_strategy.py"),
           str(workdir), "--ohlcv", str(feed), "--no-trim-output", "-o", str(out)]
    if overrides is not None:
        inputs = workdir / f"inputs_{tag}.json"
        inputs.write_text(json.dumps(overrides))
        cmd += ["--inputs-json", str(inputs)]
    trace_path = workdir / f"trace_{tag}.json"
    if trace:
        cmd += ["--trace-json", str(trace_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"run_strategy failed in {workdir}:\n{proc.stdout}\n{proc.stderr}")
    records = json.loads(trace_path.read_text())["trace"] if trace else None
    return out.read_bytes(), records


def trade_count(trades_csv: bytes) -> int:
    return len({row["Trade #"] for row in csv.DictReader(trades_csv.decode().splitlines())})


def digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _summary(blob: bytes) -> str:
    return f"{trade_count(blob)} trades sha256 {digest(blob)[:16]}"


# ---------------------------------------------------------------------------
# Execution: every build the selected tests need is transpiled, compiled and
# run in parallel once per session; each test then judges its own case.
# ---------------------------------------------------------------------------

@dataclass
class Outcome:
    build: Build
    transpiled: dict | None = field(default=None, repr=False)
    error: str | None = None
    trades: dict[str, bytes] = field(default_factory=dict, repr=False)
    traces: dict[str, list[dict]] = field(default_factory=dict, repr=False)


def _execute(engine_root: Path, feed: Path, workdir: Path, build: Build) -> Outcome:
    outcome = Outcome(build=build)
    workdir.mkdir(parents=True, exist_ok=True)
    pine = workdir / "strategy.pine"
    pine.write_text(build.source, encoding="utf-8")
    try:
        outcome.transpiled = transpile_json(pine)
        if not outcome.transpiled.get("ok"):
            outcome.error = "transpile_json refused it:\n" + json.dumps(
                outcome.transpiled.get("diagnostics"), indent=1, ensure_ascii=False)
            return outcome
        build_strategy_library(outcome.transpiled["cpp"], workdir)
        runs = [("default", None)]
        if build.overrides is not None:
            runs.append(("override", build.overrides))
        for tag, overrides in runs:
            trades, records = run_strategy(engine_root, workdir, feed, overrides,
                                           tag, build.trace)
            outcome.trades[tag] = trades
            if records is not None:
                outcome.traces[tag] = records
    except Exception as exc:  # recorded and asserted by the case's own test
        outcome.error = str(exc)
    return outcome


_TESTS = {
    "test_ta_change_equals_spelled_out_difference": "change",
    "test_input_title_needing_escapes_compiles_and_overrides": "title",
    "test_untitled_var_input_override_resizes_ta": "key",
    "test_string_constant_needing_escapes_compiles": "const",
}


@pytest.fixture(scope="session")
def outcomes(request, tmp_path_factory) -> dict[str, Outcome]:
    engine_root = _skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("e2e_ta_change_input_keys")
    feed = _derive_chart_feed(engine_root, base / "ohlcv_ETH-USDT-USDT_15m.csv")
    builds: dict[str, Build] = {}
    for item in request.session.items:
        kind = _TESTS.get(getattr(item, "originalname", None))
        if kind is not None:
            params = getattr(item, "callspec", None)
            builds.update(_builds_for(kind, params.params["case_name"] if params else ""))
    workers = max(2, min(8, (os.cpu_count() or 4) // 2))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {key: pool.submit(_execute, engine_root, feed, base / key, b)
                for key, b in builds.items()}
        return {key: job.result() for key, job in jobs.items()}


def _ok(outcomes: dict[str, Outcome], key: str) -> Outcome:
    outcome = outcomes[key]
    if outcome.error is not None:
        pytest.fail(f"[{key}] {outcome.error}\n--- source ---\n{outcome.build.source}",
                    pytrace=False)
    return outcome


def _same(a: float, b: float) -> bool:
    return (math.isnan(a) and math.isnan(b)) or a == b


def _per_bar_mismatches(subject: list[dict], reference: list[dict]
                        ) -> tuple[int, int, str | None]:
    """``(bars compared, mismatching bars, first mismatch)`` over the traced
    series both runs share, matched by trace name and bar."""
    by_name: dict[str, list[dict]] = {}
    for rec in reference:
        by_name.setdefault(rec["name"], []).append(rec)
    compared = mismatched = 0
    first = None
    positions: dict[str, int] = {}
    for rec in subject:
        name = rec["name"]
        i = positions.get(name, 0)
        positions[name] = i + 1
        refs = by_name.get(name, [])
        ref = refs[i] if i < len(refs) else None
        compared += 1
        if (ref is None or ref["timestamp"] != rec["timestamp"]
                or not _same(rec["value"], ref["value"])):
            mismatched += 1
            if first is None:
                first = (f"{name} at bar {rec['bar_index']} (ts {rec['timestamp']}): "
                         f"ta.change {rec['value']!r} vs spelled-out "
                         f"{None if ref is None else ref['value']!r}")
    if sum(len(v) for v in by_name.values()) != compared:
        mismatched += 1
        first = first or "the two runs traced different numbers of bars"
    return compared, mismatched, first


@pytest.mark.parametrize("case_name", list(CHANGE_BY_NAME))
def test_ta_change_equals_spelled_out_difference(case_name: str, outcomes) -> None:
    case = CHANGE_BY_NAME[case_name]
    subject = _ok(outcomes, f"change/{case_name}/subject")
    reference = _ok(outcomes, f"change/{case_name}/reference")
    failures = []
    lines = []
    for tag in subject.trades:
        compared, mismatched, first = _per_bar_mismatches(
            subject.traces[tag], reference.traces[tag])
        a, b = subject.trades[tag], reference.trades[tag]
        if mismatched:
            failures.append(
                f"{tag}: ta.change differs from the spelled-out difference on "
                f"{mismatched} of {compared} traced bars; first: {first}")
        if digest(a) != digest(b):
            failures.append(f"{tag}: trades differ: ta.change {_summary(a)} vs "
                            f"spelled-out {_summary(b)}")
        lines.append(f"{tag} {compared} bars equal, {_summary(a)}")
    if case.override is not None:
        default, override = subject.trades["default"], subject.trades["override"]
        if default == override:
            failures.append(f"override {case.override} left the trades unchanged "
                            f"({_summary(default)})")
        lit = _ok(outcomes, f"change/{case.override_equals}/subject")
        if digest(override) != digest(lit.trades["default"]):
            failures.append(
                f"override {case.override} gives {_summary(override)}, not the "
                f"{case.override_equals} build's {_summary(lit.trades['default'])}")
        else:
            lines.append(f"override == {case.override_equals}")
    assert not failures, f"[{case_name}] " + "\n  ".join(failures)
    print(f"E2E change {case_name}: ta.change == spelled-out  " + "  ".join(lines))


@pytest.mark.parametrize("case_name", list(TITLE_BY_NAME))
def test_input_title_needing_escapes_compiles_and_overrides(case_name: str, outcomes) -> None:
    case = TITLE_BY_NAME[case_name]
    subject = _ok(outcomes, f"title/{case_name}/subject")
    twin = _ok(outcomes, f"title/{case_name}/twin")
    for tag in ("default", "override"):
        a, b = subject.trades[tag], twin.trades[tag]
        assert digest(a) == digest(b), (
            f"[{case_name}] {tag} trades differ: {_summary(a)} vs the plain-title "
            f"twin's {_summary(b)}")
    assert subject.trades["default"] != subject.trades["override"], (
        f"[{case_name}] override {subject.build.overrides} left the trades unchanged")
    titles = [e["title"] for e in subject.transpiled["inputs"]]
    assert titles == [case.title], f"[{case_name}] manifest titles {titles}"
    print(f"E2E title {case_name}: title {case.title!r} == plain {case.plain!r}  "
          f"default {_summary(subject.trades['default'])}  override "
          f"{subject.build.overrides} {_summary(subject.trades['override'])}")


@pytest.mark.parametrize("case_name", list(KEY_BY_NAME))
def test_untitled_var_input_override_resizes_ta(case_name: str, outcomes) -> None:
    case = KEY_BY_NAME[case_name]
    subject = _ok(outcomes, f"key/{case_name}")
    twin = _ok(outcomes, "key/titled_twin")
    titles = [e["title"] for e in subject.transpiled["inputs"]]
    assert titles == [case.key], f"[{case_name}] manifest titles {titles}"
    failures = []
    for tag in ("default", "override"):
        a, b = subject.trades[tag], twin.trades[tag]
        if digest(a) != digest(b):
            failures.append(f"{tag} trades {_summary(a)} vs the titled twin's "
                            f"{_summary(b)}")
    if subject.trades["default"] == subject.trades["override"]:
        failures.append(f"override {subject.build.overrides} left the trades "
                        f"unchanged ({_summary(subject.trades['default'])})")
    assert not failures, f"[{case_name}] " + "\n  ".join(failures)
    print(f"E2E key {case_name}: {case.decl!r} == titled twin  default "
          f"{_summary(subject.trades['default'])}  override "
          f"{subject.build.overrides} {_summary(subject.trades['override'])}")


def test_string_constant_needing_escapes_compiles(outcomes) -> None:
    subject = _ok(outcomes, "const/subject")
    twin = _ok(outcomes, "const/twin")
    a, b = subject.trades["default"], twin.trades["default"]
    assert digest(a) == digest(b), (
        f"str.length of the constant: {_summary(a)} vs the literal 10's {_summary(b)}")
    print(f"E2E const string: str.length(Q) == 10  {_summary(a)}")


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
