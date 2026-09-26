"""The ``//@version=N`` directive is found where TradingView finds it.

TradingView's verdict on 46 spellings (``fixtures/popfix_tv/
version_directive_spellings.json``: each probe is the same strategy using
v6-only syntax, exported with ``lab tv --no-note``; a v6 compile books 23
trades, anything else fails to compile). The directive is the FIRST line that
holds nothing but ``//``, ``@version``, ``=`` and ASCII digits, with optional
spaces, tabs or form feeds before ``//``, between ``//`` and ``@version``,
around ``=`` and at the end; TradingView ends a line at ``\\r\\n``, ``\\r`` or
``\\n``:

* accepted: ``// @version=6``, ``//    @version=6``, ``//<tab>@version=6``,
  ``// <tab>@version=6``, ``//@version = 6`` / ``= 6`` / `` =6``, a space,
  tab or form feed before ``//``, ``06``, trailing spaces, a form feed after
  ``//``, around ``=`` or at the end, a ``\\r\\n`` line end, ``\\r`` and a
  space after it, ``// header\\r`` before it, and the line anywhere in the
  script: after other comments, after the ``strategy()`` call, mid-script,
  last, and inside a multiline string (TradingView reads the lines, not the
  tokens);
* not a directive (TradingView: "Script could not be translated"):
  ``@Version`` / ``@VERSION``, ``//@ version``, ``///@version``, trailing text
  or a trailing ``// note``, the directive after code or after other comment
  text on the same line (so inside a one-line string literal), and
  ``//\\r@version`` ("no viable alternative" at the ``@`` on line 2);
* invalid version (TradingView: "given value is not a version" or "Version
  number must be an integer value from range [1, 2]"): a no-break space after
  ``//`` or at the end, an ideographic space after ``//``, a trailing vertical
  tab, the Arabic-Indic digit six (U+0666), ``6.0``, ``6;`` and
  ``//@version\\r=\\r6``;
* ``//@version=5`` followed by ``//@version=6`` compiles as v5: the first wins.

Two divergences are PineForge's lexer, not its directive rule, and predate it.
The lexer refuses a form feed outside a comment or string, so the probe with
one before ``//`` reads as v6 and is refused as "Unexpected character". The
lexer ends a line only at a line feed and runs a comment to it, so text after
a bare carriage return on a comment line -- the directive's too -- is part of
the comment, where TradingView reads it as the next line.

PineForge used to search the raw source for ``//@version=N`` anywhere: it
refused the space and ``=``-spacing spellings (job-2614-andrewwieiw-frosty-alerts
starts ``// @version=6``) and every form feed but a trailing one, and took
``///@version``, trailing text or blanks TradingView rejects, an inline
comment, other comment text, a one-line string, ``6.0``, ``6;`` or a non-ASCII
digit as the directive. Each probe's verdict must now equal TradingView's, and
every accepted spelling must transpile to exactly the canonical probe's C++.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from pineforge_codegen.parser import Parser


FIXTURE = Path(__file__).parent / "fixtures" / "popfix_tv" / "version_directive_spellings.json"
PROBES = json.loads(FIXTURE.read_text())
# The t* and u* probes read timeframe.main_period (v6 only) to tell v5 from v6.
CANONICAL = {"s": "s01-canonical", "t": "t07-main-period-canonical",
             "u": "t07-main-period-canonical"}
# v6 on TradingView; PineForge reads the directive and its lexer refuses the
# script, before and after the directive rule changed.
LEXER_REFUSES = {"u17-formfeed-before-slashes": r"Unexpected character: '\\x0c'"}


def _source(probe: dict) -> str:
    # Every probe titles itself after its name; one title makes the C++ comparable.
    return probe["source"].replace(f'PF vdir {probe["name"]}', "PF vdir")


def _program(probe: dict) -> str:
    # Order call sites carry their source line and column (``(line << 32) |
    # col``); a directive line above them moves those, not the program.
    return re.sub(r"\b\d+ULL\b", "<site>", transpile(_source(probe)))


def _body(probe: dict) -> list[str]:
    """The probe's lines other than directive-looking and comment-only ones."""
    return [line for line in _source(probe).split("\n")
            if "@version" not in line and not line.strip().startswith("//")]


def _by_name(name: str) -> dict:
    return next(probe for probe in PROBES if probe["name"] == name)


def test_fixture_covers_every_tradingview_verdict():
    verdicts = [probe["tradingview"]["verdict"] for probe in PROBES]
    assert len(PROBES) == 46
    assert verdicts.count("v6") == 26
    assert verdicts.count("not a directive") == 11
    assert verdicts.count("invalid version") == 8
    assert verdicts.count("v5") == 1
    assert all(probe["tradingview"]["closed_trades"] == 23
               for probe in PROBES if probe["tradingview"]["verdict"] == "v6")


@pytest.mark.parametrize("probe", PROBES, ids=[probe["name"] for probe in PROBES])
def test_pineforge_reads_the_directive_as_tradingview_does(probe):
    verdict = probe["tradingview"]["verdict"]
    if probe["name"] in LEXER_REFUSES:
        assert verdict == "v6"
        assert Parser([], source=_source(probe))._extract_version() == 6
        with pytest.raises(CompileError, match=LEXER_REFUSES[probe["name"]]):
            transpile(_source(probe))
    elif verdict == "v6":
        canonical = _by_name(CANONICAL[probe["name"][0]])
        if _body(probe) == _body(canonical):
            assert _program(probe) == _program(canonical)
        else:  # t08 adds the multiline string holding its directive line
            assert "strategy_entry(" in transpile(_source(probe))
    elif verdict == "v5":
        with pytest.raises(CompileError, match=r"found //@version=5"):
            transpile(_source(probe))
    else:
        with pytest.raises(CompileError, match="Missing PineScript version directive"):
            transpile(_source(probe))


CANONICAL_BODY = _source(_by_name("t07-main-period-canonical")).split("\n", 1)[1]


@pytest.mark.parametrize("before, after", [
    ("", "\r"),                      # a \r\n line end
    ("", "\r \t"),                   # blanks on TradingView's next line
    ("", "\r// note"),               # a comment there
    ("", '\rstrategy.close("L")'),   # code there: the lexer's comment runs over it
    ("// header\r", ""),             # the directive on TradingView's second line
    ('s = """\r', '\r"""'),          # inside a multiline string
])
def test_a_carriage_return_ends_the_directive_line(before, after):
    source = before + "//@version=6" + after + "\n" + CANONICAL_BODY
    assert Parser([], source=source)._extract_version() == 6
    assert "strategy_entry(" in transpile(source)
