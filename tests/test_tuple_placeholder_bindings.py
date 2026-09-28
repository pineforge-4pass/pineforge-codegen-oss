"""A tuple declaration naming Pine's ``_`` placeholder.

``[a, _, _] = f()`` emitted the structured binding ``auto [a, _, _] = f();``,
which declares ``_`` twice. C++17 has no placeholder name: GCC rejects the
second declaration ("redeclaration of 'auto _'"), and so it does two tuples
of one block each naming ``_`` once. AppleClang and GCC 16 accept both as a
C++26 extension with a warning, so the Mac builds never saw it; Cloud Run's
GCC failed finnp17-cleantradequantum-v7-2-ob-fvg-integrated on every lane
(``f_cisdStateOnly() => [stateValue, _, _] = f_cisdLogic()``). Each ``_``
now gets a name of its own in the binding. The binding stays: a request's
TA result is a struct (``ta::SupertrendResult``), which ``std::get`` does not
take (``[_, dir] = request.security(..., ta.supertrend(3, 10))`` in a
helper, job-2678-smvdtravelsolutions-santoshpsiii-crypto-algo).
``-Werror=c++26-extensions`` holds a compiler that accepts the extension to
C++17.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from pineforge_codegen import transpile
from tests import _compile
from tests._compile import compile_cpp
from tests._e2e import (
    Build, assert_same_runs, chart_feed_head, execute_all, ok, skip_unless_e2e_env,
)


PRELUDE = '//@version=6\nstrategy("T", default_qty_type = strategy.fixed, default_qty_value = 1)\n'
TRIPLE = 'f() => [close, open, high - low]\n'
TRADE = 'if x > x[1]\n    strategy.entry("L", strategy.long)\nif x < x[1]\n    strategy.close("L")\n'
# Every structured binding the emitter writes, and the names it declares.
BINDING = re.compile(r"auto \[([^\]]*)\]")


def _strict_flags() -> tuple[str, ...]:
    """``-Werror=c++26-extensions`` where the compiler knows it (AppleClang,
    GCC 16), so its C++26 placeholder extension fails like GCC 13's error."""
    compiler = _compile._COMPILER
    if compiler is None:
        return ()
    probe = subprocess.run(
        [compiler, "-std=c++17", "-fsyntax-only", "-Werror=c++26-extensions",
         "-x", "c++", "-"], input="int main() { return 0; }\n",
        capture_output=True, text=True, timeout=60)
    return ("-Werror=c++26-extensions",) if probe.returncode == 0 else ()


@pytest.mark.parametrize("body", [
    # finnp17's shape: a helper keeping one element of another's tuple.
    'g() =>\n    [a, _, _] = f()\n    a\nx = g()\n',
    'g() =>\n    [_, b, _] = f()\n    b\nx = g()\n',
    '[a, _, _] = f()\nx = a\n',
    # One _ in each of two tuples of one block.
    '[a, _, c] = f()\n[_, b, _] = f()\nx = a + b + c\n',
    'g() =>\n    [a, _, c] = f()\n    [_, b, _] = f()\n    a + b + c\nx = g()\n',
    # The helper reached from a request.security payload.
    'g() =>\n    [a, _, _] = f()\n    a\nx = request.security(syminfo.tickerid, "60", g())\n',
    # A request's TA tuple: a struct, not a std::tuple.
    'st() =>\n    [_, dir] = request.security(syminfo.tickerid, "60", ta.supertrend(3, 10))\n'
    '    dir\nx = st()\n',
])
def test_placeholder_is_never_declared(body):
    cpp = transpile(PRELUDE + TRIPLE + body + TRADE)
    declared = [names.split(", ") for names in BINDING.findall(cpp)]
    assert not any("_" in names for names in declared), declared
    compile_cpp(cpp, label="tuple naming _", extra_flags=_strict_flags())


def test_named_tuple_keeps_its_structured_binding():
    cpp = transpile(PRELUDE + TRIPLE + '[a, b, c] = f()\nx = a + b + c\n' + TRADE)
    assert "auto [a, b, c] = f();" in cpp


def test_placeholder_tuple_trades_like_its_named_twin(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("tuple_placeholder")
    feed = chart_feed_head(engine, base, 3000)
    shape = PRELUDE + TRIPLE + (
        'g() =>\n    [a, _, _] = f()\n    [_, b, _] = f()\n    a - b\n'
        '[_, o, r] = f()\nx = g() + o * 0 + r\n') + TRADE
    twin = PRELUDE + TRIPLE + (
        'g() =>\n    [a, u1, u2] = f()\n    [u3, b, u4] = f()\n    a - b\n'
        '[u5, o, r] = f()\nx = g() + o * 0 + r\n') + TRADE
    runs = execute_all(engine, feed, base, {"shape": Build(shape), "twin": Build(twin)})
    print("tuple naming _:", assert_same_runs(ok(runs, "shape"), ok(runs, "twin")))
