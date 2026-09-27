"""A request timeframe the first bar computes with ``:=`` is registered with
the value the first bar computes (W9-CG-LTF-TF-REGISTRATION).

The engine registers every request in ``configure_security_evaluators()``,
before any bar. Registration expands the globals a timeframe reads into their
declaration expressions, down to inputs and ``timeframe.*``, but a global the
script reassigns (``lowerSeconds := math.max(60, lowerSeconds)``) rendered as
its member, which holds its initial value then: iamalala's five
``request.security_lower_tf`` sites registered "1" instead of "72" on a 1D
chart and booked no trade. TradingView computes the simple timeframe on the
first bar, reassignments included.

TradingView's tape of ``w9sec_ltf_computed_tf`` (``fixtures/security_ltf_tv``,
BINANCE:ETHUSDT.P 15) reads a "5" timeframe with three intrabars per chart
bar through a declaration expression and through ``:=`` reassignments on all
265 closes. The replay runs on the corpus 15m chart feed with the corpus 1m
feed as the auxiliary lower-timeframe feed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._e2e import (
    build_strategy_library, closed_trades, reference_codegen, skip_unless_e2e_env,
    transpile_json,
)
from tests._security_tapes import END_MS, START_MS, mismatches, source, tape_exits, tape_feed

FIXTURES = Path(__file__).parent / "fixtures" / "security_ltf_tv"
NAME = "w9sec_ltf_computed_tf"
PRE_LANE = "fdcdcbb"  # main at the CGINT3 integration base (cg/security2 4e5d482 in it)


def _aux_1m_feed(engine: Path, base: Path) -> Path:
    """The corpus 1m feed over the tapes' range: the lower-timeframe feed."""
    full = engine / "corpus" / "data" / "ohlcv_ETH-USDT-USDT_1m.csv"
    aux = base / "aux_1m.csv"
    with full.open() as inp, aux.open("w") as out:
        out.write(next(inp))
        for line in inp:
            ts = int(line.split(",", 1)[0])
            if ts >= END_MS:
                break
            if ts >= START_MS:
                out.write(line)
    return aux


def _exits(engine: Path, base: Path, key: str, pine: str, feed: Path, aux: Path,
           params: dict | None = None) -> dict[int, str]:
    workdir = base / key
    workdir.mkdir()
    (workdir / "strategy.pine").write_text(pine, encoding="utf-8")
    result = transpile_json(workdir / "strategy.pine")
    assert result.get("ok"), result.get("diagnostics")
    build_strategy_library(result["cpp"], workdir)
    trades = closed_trades(
        engine, workdir, feed, params, input_tf="15", script_tf="15",
        aux_security_ohlcv_csv=aux, aux_security_input_tf="1",
    )
    return {t["exit_time"]: t["exit_comment"] for t in trades}


def test_computed_lower_timeframe_matches_the_tape_and_its_override(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp(NAME)
    feed = tape_feed(engine, base)
    aux = _aux_1m_feed(engine, base)
    pine = source(NAME, FIXTURES)
    probe = _exits(engine, base, "probe", pine, feed, aux)
    tape = tape_exits(NAME, FIXTURES)
    assert len(tape) == 265
    missed = mismatches(tape, probe)
    assert not missed, f"{len(missed)} of {len(tape)} exits differ:\n" + "\n".join(missed[:10])
    # Profile=Fine reassigns the target inside an input-guarded block: the
    # computed timeframe is "1", as the literal "1" registers it.
    override = _exits(engine, base, "override", pine, feed, aux, {"Profile": "Fine"})
    literal = _exits(engine, base, "literal", pine.replace(
        "string ltf = str.tostring(int(math.max(1, lowerSeconds / 60)))",
        'string ltf = "1"'), feed, aux)
    assert override == literal
    assert override != probe
    # Fifteen 1-minute intrabars on every close (the run's last close, at the
    # end of the data, carries no comment).
    assert all(comment.split("|")[2:4] == ["1", "15"] for comment in literal.values() if comment)
    print(f"computed lower timeframe: {len(tape)} of {len(tape)} exit Signals equal "
          f"TradingView's; Profile=Fine equals the literal \"1\" on {len(literal)} exits")


def _configure(cpp: str) -> str:
    start = cpp.index("void configure_security_evaluators")
    return cpp[start:cpp.index("\n    }\n", start)]


def test_registration_replays_the_first_bar_in_source_order():
    from pineforge_codegen import transpile_full

    result = transpile_full(source(NAME, FIXTURES))
    configure = _configure(result["cpp"])
    replay = configure.index("decltype(this->target) target{};")
    assert replay < configure.index("decltype(this->lowerSeconds) lowerSeconds{};")
    # The input read through its getter, the guarded reassignment, then the
    # declaration and both reassignments of lowerSeconds, before registering.
    assert configure.index('get_input_string("Profile"') < configure.index("target = 15;")
    assert configure.count("lowerSeconds = ") == 3
    assert configure.rindex("lowerSeconds = ") < configure.index("register_security_lower_tf_eval(1,")
    assert not any("reassigns" in d.message for d in result["diagnostics"])


def test_a_timeframe_registration_cannot_replay_keeps_its_lowering_and_warns(tmp_path):
    from pineforge_codegen import transpile_full

    helper = '''//@version=6
strategy("replay fallback")
f(x) => x * 2
int sec = 150
sec := f(sec)
string ltf = str.tostring(sec / 60)
a = request.security_lower_tf(syminfo.tickerid, ltf, close)
plot(array.size(a))
'''
    after = '''//@version=6
strategy("replay after the request")
int sec = 300
a = request.security_lower_tf(syminfo.tickerid, str.tostring(sec / 60), close)
sec := 60
plot(array.size(a))
'''
    legacy = reference_codegen(PRE_LANE)
    for i, pine in enumerate((helper, after)):
        result = transpile_full(pine)
        configure = _configure(result["cpp"])
        assert "decltype(this->sec)" not in configure
        warnings = [d.message for d in result["diagnostics"] if "reassigns" in d.message]
        assert len(warnings) == 1 and "'sec'" in warnings[0], result["diagnostics"]
        assert ("is assigned a value it cannot compute there" in warnings[0]
                or "is assigned after the first request" in warnings[0]), warnings
        if legacy is None:
            pytest.skip(f"the lane base ({PRE_LANE}) is not in this checkout's history")
        path = tmp_path / f"s{i}.pine"
        path.write_text(pine, encoding="utf-8")
        # Registered as every earlier build registered it.
        assert configure == _configure(transpile_json(path, legacy)["cpp"])


def test_a_global_built_from_a_reassigned_one_is_computed_at_its_declaration():
    from pineforge_codegen import transpile_full

    # TradingView: snap = 60 * 2 before mins := mins * 4 runs.
    result = transpile_full('''//@version=6
strategy("declaration order")
int base = input.int(60, "Base")
int mins = base
int snap = mins * 2
mins := mins * 4
v = request.security(syminfo.tickerid, str.tostring(snap), close)
plot(v)
''')
    configure = _configure(result["cpp"])
    assert configure.index("snap = (mins * 2);") < configure.index("mins = (mins * 4);")
    assert "pine_str_tostring_tv(snap," in configure


def test_var_inline_input_and_method_requests_replay():
    from pineforge_codegen import transpile_full

    result = transpile_full('''//@version=6
strategy("replay shapes")
method htf(float self) => self + request.security(syminfo.tickerid, "D", close)
var string tf = "240"
mins = input.int(60, "Minutes")
mins := mins * 2
v = request.security(syminfo.tickerid, tf, close)
w = request.security(syminfo.tickerid, str.tostring(mins), close)
plot(v + w + close.htf())
''')
    configure = _configure(result["cpp"])
    assert 'tf = std::string("240");' in configure
    assert 'mins = get_input_int("Minutes", 60);' in configure
    assert "mins = (mins * 2);" in configure
    assert not any("reassigns" in d.message for d in result["diagnostics"])


def test_a_helper_parameter_named_like_a_reassigned_global_is_not_replayed(tmp_path):
    from pineforge_codegen import transpile_full

    pine = '''//@version=6
strategy("parameter shadow")
tf = "60"
if timeframe.isintraday
    tf := "240"
f(tf) => request.security(syminfo.tickerid, tf, close)
v = f("D")
plot(v)
'''
    configure = _configure(transpile_full(pine)["cpp"])
    assert "decltype(this->tf)" not in configure
    legacy = reference_codegen(PRE_LANE)
    if legacy is None:
        pytest.skip(f"the lane base ({PRE_LANE}) is not in this checkout's history")
    path = tmp_path / "shadow.pine"
    path.write_text(pine, encoding="utf-8")
    assert configure == _configure(transpile_json(path, legacy)["cpp"])
