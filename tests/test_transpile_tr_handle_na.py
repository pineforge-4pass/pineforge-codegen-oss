"""TV-parity tests for ``ta.tr(handle_na)`` transpilation and runtime semantics.

Pine v6 changed the documented default for ``ta.tr(handle_na)`` from the
legacy v4 ``true`` (first-bar fallback ``high - low``) to ``false`` (first-bar
returns ``na``). PineForge previously inlined the expression and silently
defaulted ``handle_na`` to ``true``; these tests pin the new behaviour:

* ``ta.tr()`` (no args) goes through the ``ta::TR`` runtime class with
  ``handle_na=false`` baked into the constructor initialiser list.
* ``ta.tr(true)`` opts into the legacy ``high - low`` first-bar branch.
* ``ta.tr(handle_na=true)`` is keyword-equivalent to ``ta.tr(true)``.
* The bare property form ``ta.tr`` (no parens) keeps the inline legacy
  semantics — covered in ``test_codegen_new.py`` to avoid duplicating the
  inline-expression assertion here.

The C++ runtime side of the contract lives in ``pineforge-engine/tests/test_ta.cpp``
(``test_tr_handle_na_default_returns_na_on_first_bar`` /
``test_tr_subsequent_bars_match_regardless_of_handle_na``).
"""

from __future__ import annotations

import re

from pineforge_codegen.analyzer import Analyzer
from pineforge_codegen.codegen import CodeGen
from pineforge_codegen.lexer import Lexer
from pineforge_codegen.parser import Parser


def _generate(src: str) -> str:
    """Lex/parse/analyse/codegen ``src`` and return the emitted C++ source."""
    tokens = Lexer(src).tokenize()
    ast = Parser(tokens, source=src).parse()
    ctx = Analyzer(ast).analyze()
    return CodeGen(ctx).generate()


def _wrap(body: str) -> str:
    """Wrap a Pine snippet in the minimum boilerplate the transpiler needs."""
    return f'//@version=6\nstrategy("T")\n{body}\n'


def _tr_ctor_handle_na(cpp: str) -> str | None:
    """Extract the ``handle_na`` literal passed to the ``_ta_tr_<n>`` ctor.

    Returns ``"true"`` / ``"false"`` if the initialiser is present, ``None``
    otherwise. Lets each test pin the exact bool literal without depending
    on the auto-numbered member index.
    """
    m = re.search(r"_ta_tr_\d+\((true|false)\)", cpp)
    return m.group(1) if m else None


def test_ta_tr_no_args_defaults_to_handle_na_false():
    # TV v6: ``ta.tr()`` returns na on the first bar; PineForge encodes this
    # by passing ``false`` to ``ta::TR``'s constructor.
    cpp = _generate(_wrap("x = ta.tr()"))
    assert "ta::TR _ta_tr_" in cpp
    assert _tr_ctor_handle_na(cpp) == "false", cpp


def test_ta_tr_explicit_true_passes_true_to_ctor():
    # ``ta.tr(true)`` opts into the legacy v4 first-bar fallback (high - low).
    cpp = _generate(_wrap("x = ta.tr(true)"))
    assert _tr_ctor_handle_na(cpp) == "true", cpp


def test_ta_tr_explicit_false_passes_false_to_ctor():
    # Explicit ``false`` is identical to the default but worth pinning so a
    # future codegen refactor cannot accidentally drop the literal.
    cpp = _generate(_wrap("x = ta.tr(false)"))
    assert _tr_ctor_handle_na(cpp) == "false", cpp


def test_ta_tr_keyword_handle_na_true_matches_positional_true():
    # ``ta.tr(handle_na=true)`` and ``ta.tr(true)`` must transpile to the
    # same ctor literal — they are interchangeable in Pine.
    pos_cpp = _generate(_wrap("x = ta.tr(true)"))
    kw_cpp = _generate(_wrap("x = ta.tr(handle_na=true)"))
    assert _tr_ctor_handle_na(pos_cpp) == _tr_ctor_handle_na(kw_cpp) == "true"


def test_ta_tr_keyword_handle_na_false_matches_positional_false():
    pos_cpp = _generate(_wrap("x = ta.tr(false)"))
    kw_cpp = _generate(_wrap("x = ta.tr(handle_na=false)"))
    assert _tr_ctor_handle_na(pos_cpp) == _tr_ctor_handle_na(kw_cpp) == "false"


def test_ta_tr_compute_uses_implicit_bar_ohlc():
    # ``handle_na`` is a ctor arg; ``compute`` should receive only the
    # implicit bar OHLC (high, low, close) — it never sees ``handle_na``.
    cpp = _generate(_wrap("x = ta.tr()"))
    assert (
        "_ta_tr_1.compute(current_bar_.high, current_bar_.low, current_bar_.close, prev_chart_close())"
        in cpp
    ), cpp
    # Defensive: the bool literal must NOT appear inside the compute call.
    assert "_ta_tr_1.compute(true" not in cpp
    assert "_ta_tr_1.compute(false" not in cpp


def test_ta_tr_property_form_stays_inline_with_legacy_semantics():
    # ``ta.tr`` (no parens) is the legacy property form — TV keeps the
    # ``handle_na=true`` first-bar fallback for it. PineForge mirrors this
    # by emitting the inline expression instead of allocating a ``ta::TR``.
    cpp = _generate(_wrap("x = ta.tr"))
    assert "ta::TR _ta_tr_" not in cpp
    assert (
        "std::isnan(_s_close[1]) ? (current_bar_.high - current_bar_.low)"
        in cpp
    )


def test_ta_tr_multiple_call_sites_get_independent_state():
    # Every ``ta.tr(...)`` call site mints its own ``_ta_tr_<n>`` member so
    # ``handle_na`` choices and prev-close state stay isolated. This guards
    # against a regression where a single shared TR was reused.
    cpp = _generate(_wrap("a = ta.tr()\nb = ta.tr(true)"))
    members = sorted(set(re.findall(r"_ta_tr_\d+", cpp)))
    assert len(members) >= 2, members
    # Each member must have its own ctor literal in the initialiser list.
    ctor_calls = re.findall(r"_ta_tr_\d+\((true|false)\)", cpp)
    assert "false" in ctor_calls and "true" in ctor_calls, ctor_calls
