"""``import <user>/<name>/<version> [as <alias>]``: the parsed parts, and the
one import PineForge accepts -- a library whose alias is a built-in
namespace while the script references only that namespace's built-ins.

The parser used to join the rest of the line, so ``import
jdehorty/MLExtensions/2 as ml`` became the path ``jdehorty/MLExtensions/2asml``
and every import was refused. TradingView's pine-facade compile of
``import TradingView/ta/7`` links no library (``metaInfo.usedLibs`` stays
empty) when the script calls only built-in ``ta.*`` names, and links the
library for a library-only name such as ``ta.dema`` (XSYM-DESIGN report
section 1.3): such an import is a no-op, and the script is the same script
without it. Every other import keeps the refusal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.ast_nodes import ImportStmt
from pineforge_codegen.errors import CompileError
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits


XSYM_TV = Path(__file__).parent / "fixtures" / "xsym_tv"


def _imports(src: str) -> list[ImportStmt]:
    program = Parser(Lexer(src).tokenize(), source=src).parse()
    return [stmt for stmt in program.body if isinstance(stmt, ImportStmt)]


@pytest.mark.parametrize("line, parts", [
    ("import TradingView/ta/7", ("TradingView", "ta", 7, None, "TradingView/ta/7")),
    ("import jdehorty/MLExtensions/2 as ml",
     ("jdehorty", "MLExtensions", 2, "ml", "jdehorty/MLExtensions/2")),
    ("import richardgong1988/HanJinSignals26/15 as S",
     ("richardgong1988", "HanJinSignals26", 15, "S", "richardgong1988/HanJinSignals26/15")),
])
def test_import_parts_are_parsed(line, parts):
    (node,) = _imports(f"//@version=6\n{line}\nstrategy(\"t\")\n")
    assert (node.user, node.name, node.version, node.alias, node.path) == parts


HEAD = '//@version=6\nstrategy("import probe", overlay=true)\n'
BODY = """
fast = ta.ema(close, 9)
slow = ta.sma(close, 21)
r = ta.rsi(close, 14)
if ta.crossover(fast, slow) and r > 50 and ta.tr > 0
    strategy.entry("L", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("L")
"""


@pytest.mark.parametrize("line", [
    "import TradingView/ta/7",
    "import TradingView/ta/7 as ta",
    # The rule is the alias's: any library imported as ``ta`` whose members
    # the script never names is not linked either.
    "import someone/Helpers/3 as ta",
])
def test_builtin_namespace_import_is_a_no_op(line):
    with_import = transpile(HEAD + line + "\n" + BODY)
    # A blank line keeps every later source line where it was.
    without = transpile(HEAD + "\n" + BODY)
    assert with_import == without


@pytest.mark.parametrize("line, body, spelled", [
    # A library-only member links the library.
    ("import TradingView/ta/7", "x = ta.dema(close, 9)\nplot(x)\n",
     "TradingView/ta/7"),
    # A library type links it too.
    ("import TradingView/ta/7", "ta.Pivot p = na\n", "TradingView/ta/7"),
    # An alias that is no built-in namespace.
    ("import TradingView/ta/7 as t", "x = t.ema(close, 9)\nplot(x)\n",
     "TradingView/ta/7 as t"),
    ("import jdehorty/MLExtensions/2 as ml", "x = ml.n_rsi(close, 14, 1)\nplot(x)\n",
     "jdehorty/MLExtensions/2 as ml"),
    # ``log`` is a built-in namespace whose members PineForge does not list
    # for this rule: the import keeps its refusal.
    ("import someone/Logger/1 as log", 'log.info("x")\n', "someone/Logger/1 as log"),
])
def test_other_imports_stay_refused(line, body, spelled):
    with pytest.raises(CompileError) as err:
        transpile(HEAD + line + "\n" + body)
    messages = [d.message for d in err.value.diagnostics]
    assert f"Import is not supported: '{spelled}'" in messages, messages


def test_imported_ta_replays_tradingview_tape(tmp_path_factory):
    """TradingView's tape of ``xa_import_ta`` is byte for byte the tape of the
    same script with its import deleted (``fixtures/xsym_tv``); PineForge
    builds the import as the script without it and reproduces every exit
    Signal (the values of ``ta.sma``, ``ta.highest``, ``ta.rsi``, ``ta.tr``)."""
    probe = source("xa_import_ta", XSYM_TV)
    # Blanked, the import line keeps every order's source position.
    assert transpile(probe) == transpile(probe.replace("import TradingView/ta/7", ""))
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("xsym_import_ta")
    exits = replay(engine, base, {"probe": Build(source("xa_import_ta", XSYM_TV))})
    tape = tape_exits("xa_import_ta", XSYM_TV)
    assert len(tape) == 265
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
