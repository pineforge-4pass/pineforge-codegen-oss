"""A script's names cannot capture, or be captured by, the emitter's temporaries.

The codegen wraps a user's expression in C++ that declares temporaries --
nz's ``[&]{ auto _nz_v = (x); ... }()``, fixnan's ``_fixnan_v``, a
comparison's ``_pna_l`` / ``_pna_r``, a history read's ``_hv``, math.max's
``_v0`` ..., timestamp()'s calendar fields -- and its str.* / array.*
templates declared short locals (``std::string s``, ``std::string r``,
``int i``, ``auto c``, ``auto it``, ...). A script variable spelled like
one was captured where the user's expression is read inside that C++:
``nz(x, _nz_v)`` read the lambda's own ``_nz_v``, ``str.repeat(r, 2)``
appended the empty local ``r`` to itself, ``str.repeat("x", i)`` counted to
the loop's own ``i``, ``str.upper(s)`` initialized ``s`` from itself (UB),
and ``_pna_l > close``, ``fixnan(.. _fixnan_v)``, ``str.pos(s, p)``,
``array.median(c)`` and more did not compile (lane W2's open findings;
the templates' short locals found by this lane).

``helpers.is_emitter_temporary`` now names every temporary the emitter
declares -- ``CPP_TEMPORARY_NAMES`` and the numbered forms, the templates'
short locals renamed ``__pf_*`` (C++ reserves double-underscore names) --
and ``_safe_name`` escapes an authored spelling of one. TradingView's
tapes of ``fixtures/open_items_tv/temp_names`` and ``temp_names_array``
(lab tv --no-note) spell every such read on each exit bar; the replay
compares every exit comment. A guard derives the temporaries from emitted
C++ and requires each to be reserved.
"""

from __future__ import annotations

import re

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.codegen.helpers import CPP_TEMPORARY_NAMES, is_emitter_temporary
from pineforge_codegen.lexer import Lexer
from tests._e2e import Build, execute_all, ok, skip_unless_e2e_env
from tests._tv_tapes import FIXTURES, exit_misses, tape, window_feed


TAPES = ("temp_names", "temp_names_array")


@pytest.fixture(scope="module")
def tape_runs(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("temp_names_tapes")
    feed = window_feed(engine, base)
    runs = execute_all(engine, feed, base, {
        name: Build((FIXTURES / f"{name}.pine").read_text()) for name in TAPES})
    return engine, base, feed, runs


@pytest.mark.parametrize("name", TAPES)
def test_the_temporary_name_tape_replays(tape_runs, name):
    engine, base, feed, runs = tape_runs
    ok(runs, name)
    trades = tape(name)
    assert len(trades) == 7
    misses = exit_misses(engine, base / name, feed, trades)
    assert not misses, misses[:3]


HEAD = '//@version=6\nstrategy("temporary names", overlay = true)\n'


@pytest.mark.parametrize("name", sorted(CPP_TEMPORARY_NAMES) + [
    "_v0", "_v12", "__switch_val_0", "_tuple_result_1", "_for_end_2", "_pf_str_a0",
    "_pf_every_bar_ta_1", "__pf_array_arg_0", "__pf_raw_index_value"])
def test_an_authored_temporary_spelling_is_escaped(name):
    cpp = transpile(HEAD + f"{name} = close * 2\nif {name} > close\n"
                           "    strategy.entry(\"L\", strategy.long)\n")
    assert re.search(rf"\b{re.escape('pf_safe_' + name)}\b", cpp)
    # The script's own assignment is escaped; the emitter's temporaries are
    # declared inside the lambdas it generates, never at a statement's start.
    assert not re.search(rf"^\s+{re.escape(name)} = ", cpp, re.M)


def test_names_outside_the_temporaries_keep_their_spelling():
    # A name the emitter allocates against the authored spellings itself
    # (``_pf_udt_Item__pf2``, ``__pf_map_iter_1``) is not reserved, so an
    # authored spelling of it stays as written.
    for name in ("_t", "_v", "_i", "s", "r", "i", "pf_x", "_pf_x", "_pf_udt_Item",
                 "__switch_val", "__pf_map_iter_0", "__pf_map_key_0", "__pfx"):
        assert not is_emitter_temporary(name), name


DECL = re.compile(
    r"(?<![\w:])(?:(?:auto|double|int|int64_t|bool|std::string|std::size_t|size_t"
    r"|long double)\s+|auto&&\s*|auto&\s*)(_\w+)\s*(?:=|;|\()")


def test_every_temporary_the_emitter_declares_is_reserved():
    # Temporaries derived from emitted C++: every underscore-prefixed local
    # declared inside a generated lambda that is not a name of the script.
    names = set()
    for name in TAPES:
        source = (FIXTURES / f"{name}.pine").read_text()
        authored = {t.value for t in Lexer(source).tokenize() if t.type.name == "IDENT"}
        for line in transpile(source).splitlines():
            if "[&]" in line:
                names |= {n for n in DECL.findall(line)
                          if n not in authored and not n.startswith("pf_safe_")}
    assert names, "the tapes' C++ declares no temporary"
    assert sorted(n for n in names if not is_emitter_temporary(n)) == []
