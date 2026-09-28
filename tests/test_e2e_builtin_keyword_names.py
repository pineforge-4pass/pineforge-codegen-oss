"""nz, fixnan and str.repeat take TradingView's keyword names.

TradingView's signatures are ``nz(source, replacement)``, ``fixnan(source)``
and ``str.repeat(source, repeat, separator)``; ``nz(x = ...)`` and
``str.repeat(..., count = ...)`` are its compile errors ("The ... function
does not have an argument with the name ...", lab tv --no-note). The
signature registry named them ``x`` / ``y``, ``x`` and ``source`` /
``count`` without ``separator``, and the codegen read nz and fixnan by
position: ``nz(x, replacement = y)`` read 0, ``nz(source = x)`` and
``fixnan(source = x)`` raised IndexError in the transpiler, and
``str.repeat(source = "ab", repeat = 3, separator = ",")`` lost its count
and separator (an empty string).

The support checker now binds nz and fixnan to TradingView's signatures and
``builtin_keywords`` rewrites each bound call positionally; TradingView
evaluates the arguments in parameter order whatever order they are written
in, which is what the positional form does. Two tapes pin it:
``fixtures/open_items_tv/kw_names`` spells every keyword form on each exit
bar, and ``kw_order`` codes the order nz's keywords run in, written either
way (12: the source first).
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)
from tests._tv_tapes import FIXTURES, exit_misses, tape, window_feed


TAPES = ("kw_names", "kw_order")


@pytest.fixture(scope="module")
def tape_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("kw_names_tapes")
    feed = window_feed(engine, base)
    runs = execute_all(engine, feed, base, {
        name: Build((FIXTURES / f"{name}.pine").read_text()) for name in TAPES})
    return engine, base, feed, runs


@pytest.mark.parametrize("name", TAPES)
def test_the_keyword_tape_replays(tape_runs, name):
    engine, base, feed, runs = tape_runs
    ok(runs, name)
    trades = tape(name)
    assert len(trades) == 7
    misses = exit_misses(engine, base / name, feed, trades)
    assert not misses, misses[:3]


HEAD = ('//@version=6\nstrategy("keyword names", overlay = true)\n'
        "x = close > open ? na : close\n")
TAIL = "\n// @pf-trace v=v\nif v > close\n    strategy.entry(\"L\", strategy.long)\nelse\n    strategy.close(\"L\")\n"
# Each keyword spelling and its positional twin.
TWINS = {
    "nz_replacement": ("v = nz(x, replacement = open)", "v = nz(x, open)"),
    "nz_source": ("v = nz(source = x)", "v = nz(x)"),
    "nz_both_reordered": ("v = nz(replacement = open, source = x)", "v = nz(x, open)"),
    "fixnan_source": ("v = fixnan(source = x)", "v = fixnan(x)"),
    "nz_source_ta": ("v = nz(source = ta.sma(x, 3), replacement = open)",
                     "v = nz(ta.sma(x, 3), open)"),
    "repeat_keywords": ('v = str.length(str.repeat(source = "ab", repeat = 3, separator = ",")) + close',
                        'v = str.length(str.repeat("ab", 3, ",")) + close'),
}


@pytest.fixture(scope="module")
def twin_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("kw_names_twins")
    feed = chart_feed_head(engine, base, 400)
    builds = {}
    for shape, (keyword, positional) in TWINS.items():
        builds[f"{shape}-keyword"] = Build(HEAD + keyword + TAIL, trace=True)
        builds[f"{shape}-positional"] = Build(HEAD + positional + TAIL, trace=True)
    return execute_all(engine, feed, base, builds)


@pytest.mark.parametrize("shape", sorted(TWINS))
def test_a_keyword_call_runs_like_its_positional_twin(twin_runs, shape):
    print(assert_same_runs(ok(twin_runs, f"{shape}-keyword"),
                           ok(twin_runs, f"{shape}-positional")))


@pytest.mark.parametrize("call, message", [
    ("nz(x = x)", "nz has no parameter 'x'"),
    ("nz(x, y = 1.0)", "nz has no parameter 'y'"),
    ("fixnan(x = x)", "fixnan has no parameter 'x'"),
    ("nz(x, 1.0, 2.0)", "nz takes at most 2 arguments"),
    ("nz(replacement = 1.0)", "nz is missing its argument 'source'"),
    ("nz(x, source = x)", "nz got 'source' twice"),
])
def test_a_call_tradingview_rejects_is_refused(call, message):
    with pytest.raises(CompileError) as info:
        transpile(HEAD + f"v = {call}" + TAIL)
    assert any(message in d.message for d in info.value.diagnostics), \
        [d.message for d in info.value.diagnostics]
