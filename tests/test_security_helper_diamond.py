"""A diamond of pure helpers in a request.security payload is spelled once.

``f1(x) => f0(x) + f0(x)``, ``f2(x) => f1(x) + f1(x)``, ...: the evaluator
inlines each helper call on the requested bar, and every prepass walked, and
the builder spelled, the leaf once per call path -- 2**22 copies for 22
levels, so lane CG-SECURITY-2's ``diamond.pine`` ran for minutes (the
analyzer's map-history validation and the known-value spelling of a global
walked the same paths). A call of a pure helper (one expression over its
parameters, the requested bar's fields and literals, through operators and
such calls) on arguments of that kind holds nothing a prepass collects, so
the walks stop at it, and one the evaluator reaches again with the same
arguments reads the value its first reach computed, once its inlined text is
long, where the evaluator opens (``_security_share_pure_call``).

The values do not change: each level doubles the one below it, exactly, so
the 22-level payload is 2**22 times its leaf, bar for bar, which the replay
compares with the spelled-out product on the requested bar.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pineforge_codegen import transpile
from pineforge_codegen.codegen.helpers import is_emitter_temporary
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import replay

REPO_ROOT = Path(__file__).resolve().parent.parent


def _diamond(prefix: str, depth: int, leaf: str) -> list[str]:
    lines = [f"{prefix}0(x) => {leaf}"]
    lines += [f"{prefix}{i}(x) => {prefix}{i - 1}(x) + {prefix}{i - 1}(x)"
              for i in range(1, depth + 1)]
    return lines


def _script(payload: str, depth: int = 22, leaf: str = "x + 1") -> str:
    return "\n".join([
        "//@version=6",
        'strategy("diamond")',
        *_diamond("f", depth, leaf),
        f"method m(float x) => f{depth}(x)",
        f'v = request.security(syminfo.tickerid, "60", {payload})',
        "if v > close",
        '    strategy.entry("L", strategy.long)',
        "",
    ])


def _evaluator(cpp: str) -> str:
    body = cpp[cpp.index("void _eval_security_0"):]
    return body[:body.index("\n    }\n")]


def test_a_22_level_diamond_transpiles_in_seconds():
    # Every earlier build spent minutes here: bound the run, in a process of
    # its own, instead of waiting for it.
    probe = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        f"sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
        "from test_security_helper_diamond import _script\n"
        "from pineforge_codegen import transpile\n"
        "for payload in ('f22(close)', 'nz(close.m())'):\n"
        "    print(len(transpile(_script(payload))))\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    sizes = [int(line) for line in proc.stdout.split()]
    assert len(sizes) == 2 and max(sizes) < 60_000, sizes


def test_each_level_adds_a_share_of_a_value_not_a_copy():
    small = transpile(_script("f10(close)", depth=10))
    large = transpile(_script("f20(close)", depth=20))
    # The leaf's copies doubled per level; a value of a few hundred
    # characters every few levels instead.
    assert len(large) - len(small) < 4_000
    body = _evaluator(large)
    assert body.count("const auto _pf_shared_0_") >= 4
    assert body.count("(bar.close + 1)") == 16


def test_a_pure_call_reached_once_keeps_its_inline_text():
    # Other arguments, a short text, or an argument the prepasses must see
    # (a TA call): the payload inlines each call as every earlier build did.
    for payload in ("g(close) + g(open)", "g(close) + g(close)",
                    "g(ta.sma(close, 3)) + g(ta.sma(close, 3))"):
        cpp = transpile("\n".join([
            "//@version=6", 'strategy("once")', "g(x) => x * 2 - open",
            f'v = request.security(syminfo.tickerid, "60", {payload})',
            "if v > close", '    strategy.entry("L", strategy.long)', "",
        ]))
        assert "_pf_shared_" not in cpp, payload


def test_a_call_reading_more_than_its_arguments_is_not_shared():
    # A global (on the chart's terms or the requested bar's), history or a
    # TA call inside the helper: its prepasses and inlining are unchanged.
    for leaf in ("x + g", "x + close[1]", "x + ta.sma(close, 3)"):
        cpp = transpile("\n".join([
            "//@version=6", 'strategy("impure")', "g = close * 2",
            *_diamond("f", 8, leaf),
            'v = request.security(syminfo.tickerid, "60", f8(close))',
            "if v > close", '    strategy.entry("L", strategy.long)', "",
        ]))
        assert "_pf_shared_" not in cpp, leaf


def test_a_script_name_spelled_like_a_lambda_is_escaped():
    assert is_emitter_temporary("_pf_shared_0_0")
    cpp = transpile(_script("f12(close) + _pf_shared_0_0", depth=12).replace(
        "method m", "_pf_shared_0_0 = high\nmethod m"))
    assert "_pf_shared_0_0 = high" not in cpp
    assert "const auto _pf_shared_0_0 = " in _evaluator(cpp)


PROBE = "\n".join([
    "//@version=6",
    'strategy("PF diamond payload", overlay = true)',
    *_diamond("f", 22, "x * 2 - open"),
    *_diamond("g", 16, "x > open ? x - open : open - x"),
    "method m(float x) => f22(x)",
    'a = request.security(syminfo.tickerid, "60", f22(close))',
    'b = request.security(syminfo.tickerid, "60", nz(close.m(), -1))',
    'r = request.security(syminfo.tickerid, "60", 4194304 * (close * 2 - open))',
    'c = request.security(syminfo.tickerid, "240", g16(close) + 1)',
    'cr = request.security(syminfo.tickerid, "240", 65536 * math.abs(close - open) + 1)',
    "s(x) => na(x) ? \"na\" : str.tostring(x)",
    'm = minute(time, "UTC")',
    "if m == 0 or m == 30",
    '    strategy.entry("L", strategy.long)',
    "if m == 15 or m == 45",
    '    strategy.close("L", comment = s(a) + "|" + s(b) + "|" + s(r) + "|" + s(c) + "|" + s(cr))',
    "",
])


def test_the_diamond_payloads_are_their_spelled_out_products(tmp_path):
    engine = skip_unless_e2e_env()
    exits = replay(engine, tmp_path, {"diamond": Build(PROBE)})["diamond"]
    # Every exit the probe spells (the run's last close, at the end of the
    # feed, has no comment).
    comments = [comment for comment in exits.values() if comment]
    assert len(comments) > 300
    finite = 0
    for comment in comments:
        a, b, r, c, cr = comment.split("|")
        assert a == r and b == r and c == cr, comment
        finite += r != "na" and cr != "na"
    assert finite > 300
