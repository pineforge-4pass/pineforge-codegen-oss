"""A helper called on its own result inside a ``request.security`` payload.

``u(_x) => ta.sma(_x, 3)`` read as ``u(u(close))`` in a payload holds two TA
states: the inner call's and the outer call's. The payload's TA variants are
collected by walking each helper call with a recursion guard keyed by the
helper's name, and the inner call is reached through the outer body's
parameter while that name is on the guard: it was skipped as recursion, its
TA site got no variant, and the evaluator read an undeclared base member
(``_sec0__ta_sma_1``), which did not compile. The guard is keyed by the
written call now: each nested call gets its own variant, ordered before the
outer one that reads it. A helper local's value is re-walked under the
helper-keyed guard still: the evaluator reads the local's C++ variable, and
entering the helper again there re-walked every earlier local once per read.

TradingView's tape of ``fixtures/silent2_tv/cgs2_nested_helper_payload``
spells ``u(u(close)) + u(open)`` and ``u(u(u(high)))`` requested at 60
minutes on every close from 06:00 UTC on.
"""

from __future__ import annotations

import time
from pathlib import Path

from pineforge_codegen import transpile
from tests._compile import compile_cpp
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits

FIXTURES = Path(__file__).parent / "fixtures" / "silent2_tv"
NAME = "cgs2_nested_helper_payload"


def test_the_nested_helper_payload_tape_replays(tmp_path):
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {NAME: Build(source(NAME, FIXTURES))})[NAME]
    tape = tape_exits(NAME, FIXTURES)
    assert len(tape) == 324
    missed = mismatches(tape, exits)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:5])
    print(f"nested helper payload: {len(tape)} of {len(tape)} exit Signals equal TradingView's")


def test_each_nested_call_gets_its_own_variant():
    cpp = transpile(
        "//@version=6\n"
        'strategy("nested helper")\n'
        "u(_x) => ta.sma(_x, 3)\n"
        'a = request.security(syminfo.tickerid, "60", u(u(close)) + u(open))\n'
        "if a > 0\n"
        '    strategy.entry("L", strategy.long)\n')
    evaluator = cpp[cpp.index("void _eval_security_0"):cpp.index("void evaluate_security")]
    assert "_sec0__ta_sma_1." not in evaluator
    assert "_sec0__ta_sma_1_v1.compute(bar.close)" in evaluator
    assert "_sec0__ta_sma_1_v2.compute(bar.open)" in evaluator
    # The outer call reads the inner call's result, computed before it.
    assert evaluator.index("_secval_0_v1 =") < evaluator.index("_secval_0_v0 =")
    assert "_sec0__ta_sma_1_v0.compute(_secval_0_v1)" in evaluator
    compile_cpp(cpp)


def test_a_chain_of_helper_locals_is_walked_once():
    # u reads its parameter twice and each local calls u on the previous one:
    # the collector entered u again through every earlier local once per
    # read, 2**n walks each adding a variant nothing computed (14 locals:
    # 140 SMA members, 15 took ten seconds and 20 did not finish).
    n = 14
    body = "".join(
        f"    a{k} = u({'close' if k == 0 else f'a{k - 1}'})\n" for k in range(n))
    start = time.monotonic()
    cpp = transpile(
        "//@version=6\n"
        'strategy("helper local chain")\n'
        "u(x) => ta.sma(x, 3) + x * 0.1\n"
        "comp() =>\n" + body + f"    a{n - 1}\n"
        'r = request.security(syminfo.tickerid, "60", comp())\n'
        "if r > close\n"
        '    strategy.entry("L", strategy.long)\n')
    assert time.monotonic() - start < 30
    assert cpp.count("ta::SMA _sec0__ta_sma_1_v") <= n + 1
    compile_cpp(cpp)
