"""String state in ``request.security`` helpers and string payloads.

``var string state = "Neutral"`` in a helper was refused ("request.security
helper-local var state currently supports only int, float, and bool
values"): helper state lives in ``_security_helper_series_``, a map of
``Series<double>``. A string ``var`` (or a string local read with history)
now lives in a second map, ``_security_helper_series_str_`` of
``Series<std::string>``, declared only when a payload's helper holds one, with
the same new-slot push and recompute rollback. A payload of string type was a
``double`` result the chart then compared with a string literal; the analyzer
now types it ``string`` and the result member is a ``std::string`` holding
``na`` (empty) until the first requested value.

TradingView's tape of ``sec2_var_string`` (``fixtures/security2_tv``): a
string state machine whose ``if`` conditions guard ``ta.crossover`` behind a
lazy ``and``, on 60 and 240 minutes and on the chart; the 240-minute state
reads empty until the first 4-hour bar completes. All 265 exit Signals
match.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile
from pineforge_codegen.errors import CompileError
from tests._e2e import Build, skip_unless_e2e_env
from tests._security_tapes import mismatches, replay, source, tape_exits


def test_helper_string_state_matches_the_tape(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("security2_var_string")
    exits = replay(engine, base, {"probe": Build(source("sec2_var_string"))})
    tape = tape_exits("sec2_var_string")
    assert len(tape) == 265
    assert tape[1743471000000] == "Neutral||Neutral"  # no 4-hour bar yet: empty
    missed = mismatches(tape, exits["probe"])
    assert not missed, "\n".join(missed[:10])
    print(f"helper string state: {len(tape)} of {len(tape)} exit Signals equal "
          "TradingView's")


def test_string_state_lives_in_the_string_series_map():
    cpp = transpile(source("sec2_var_string"))
    assert ("std::unordered_map<std::string, Series<std::string>> "
            "_security_helper_series_str_;") in cpp
    assert "std::string _req_sec_0 = na<std::string>();" in cpp
    clear = cpp[cpp.index("void clear_security(int sec_id)"):]
    assert "_req_sec_0 = na<std::string>();" in clear
    assert "std::string s60 = " in cpp
    # A script without string helper state keeps its members unchanged.
    plain = transpile(source("sec2_var_ctx"))
    assert "_security_helper_series_str_" not in plain


def test_non_scalar_helper_var_state_stays_refused():
    src = """//@version=6
strategy("array var reject")
f() =>
    var array<float> state = array.new<float>()
    state.size()
out = request.security(syminfo.tickerid, "2", f())
"""
    with pytest.raises(CompileError,
                       match="supports only int, float, bool and string values"):
        transpile(src)


def test_string_na_reaches_string_state():
    src = """//@version=6
strategy("string na")
f() =>
    var string st = ""
    if close > open
        st := "up"
    else
        st := na
    st
v = request.security(syminfo.tickerid, "60", f())
if v == "up"
    strategy.entry("L", strategy.long)
"""
    cpp = transpile(src)
    assert '_security_helper_series_str_["' in cpp and ".update(na<std::string>());" in cpp
    from tests import _compile as compile_env
    compile_env.compile_cpp(cpp, label="security-helper-string-na")


def test_string_literals_are_not_renamed_after_helper_locals():
    src = """//@version=6
strategy("literal beside local")
f() =>
    bull = close > open
    bull ? "bull" : "bear"
x = request.security(syminfo.tickerid, "60", f())
if x == "bull"
    strategy.entry("L", strategy.long)
"""
    body = transpile(src)
    assert '(std::string("bull")) : (std::string("bear"))' in body
