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

A request reads a reassigned global where the request is written: a write
after it does not reach it, and reaches the next request. TradingView compiled
the two-request script below without errors or warnings on 2026-11-18. On
BINANCE:BTCUSDT 60, all 188 completed aligned pairs showed 12 intrabars at the
first request and 60 at the second: "5" before the write, "1" after it.
This is one observation of that script and window.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._e2e import (
    build_strategy_library, closed_trades, reference_codegen, skip_unless_e2e_env,
    transpile_json,
)
from tests._security_tapes import END_MS, START_MS, mismatches, source, tape_exits, tape_feed
from tests._numeric_expected import (
    integer_arithmetic, integer_store, integer_to_double, require_numeric_hunk,
)

FIXTURES = Path(__file__).parent / "fixtures" / "security_ltf_tv"
NAME = "w9sec_ltf_computed_tf"
BASELINE_CODEGEN_REVISION = "fdcdcbb"  # main before this fix; it already contains 4e5d482

# The two-request script observed on 2026-11-18.
READ_ORDER_SOURCE = '''//@version=6
strategy("lower timeframe request read order")
int sec = 300
beforeWrite = request.security_lower_tf(syminfo.tickerid, str.tostring(sec / 60), close)
sec := 60
afterWrite = request.security_lower_tf(syminfo.tickerid, str.tostring(sec / 60), close)
plot(array.size(beforeWrite))
plot(array.size(afterWrite))
'''
READ_ORDER_SOURCE_BYTES = 320
READ_ORDER_PLOTS = "plot(array.size(beforeWrite))\nplot(array.size(afterWrite))\n"
# A run's plots are not in its report: the two sizes travel in the comment of
# a close, as the fixtures' values do.
READ_ORDER_CLOSES = '''m = minute(time, "UTC")
if m == 0 or m == 30
    strategy.entry("L", strategy.long)
if m == 15 or m == 45
    strategy.close("L", comment = str.tostring(array.size(beforeWrite)) + "|" + str.tostring(array.size(afterWrite)))
'''
READ_ORDER_TIMEFRAME = "str.tostring(sec / 60)"
CHART_MINUTES = 15  # the corpus chart feed of ``tape_feed``


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


def _registration(configure: str, sec_id: int) -> str:
    """The lower-timeframe registration call of ``sec_id``: from its name to
    the chart timeframe argument that follows its timeframe."""
    start = configure.index(f"register_security_lower_tf_eval({sec_id}, ")
    return configure[start:configure.index(", input_tf_);", start)]


def _stores_above_each_request(pine: str, name: str) -> list[list[str]]:
    """For each lower-timeframe request line of ``pine``, what the lines above
    it declare and assign ``name`` with, in source order."""
    stores: list[str] = []
    above: list[list[str]] = []
    for line in pine.splitlines():
        store = re.fullmatch(rf"(?:int )?{name} :?= (.+)", line)
        if store:
            stores.append(store.group(1))
        elif "request.security_lower_tf(" in line:
            above.append(list(stores))
    return above


def _returned(call: str) -> str:
    """The timeframe a registration call's own replay returns."""
    returned = re.search(r"^ {12}return ", call, re.M)
    assert returned, call
    return call[returned.start():]


def _replayed(call: str, name: str) -> list[str]:
    """What a registration call stores into its own ``name``, in order,
    between declaring it and returning its timeframe."""
    declaration = f"decltype(this->{name}) {name}{{}};"
    assert call.count(declaration) == 1, call
    declared = call.index(declaration)
    returned = len(call) - len(_returned(call))
    stores = [(store.start(), store.group(1))
              for store in re.finditer(rf"^ {{12}}{name} = (.+);$", call, re.M)]
    assert all(declared < at < returned for at, _ in stores), call
    return [value for _, value in stores]


def _closes(pine: str) -> str:
    """``pine`` with its two plots replaced by the closes that report them."""
    assert pine.endswith(READ_ORDER_PLOTS)
    return pine[:-len(READ_ORDER_PLOTS)] + READ_ORDER_CLOSES


def _with_literal_timeframes(pine: str, *timeframes: str) -> str:
    """``pine`` with each request's computed timeframe replaced, in source
    order, by the literal given for it."""
    parts = pine.split(READ_ORDER_TIMEFRAME)
    assert len(parts) == len(timeframes) + 1
    out = parts[0]
    for timeframe, rest in zip(timeframes, parts[1:]):
        out += f'"{timeframe}"' + rest
    return out


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
    # The write after the only request does not reach it: on every bar the
    # request reads what the lines above it give ``sec``, and its registration
    # replays exactly those. This case was pinned to the lane base's whole
    # registration, which read the value the script writes last; ``READ_ORDER_SOURCE``'s
    # request before its write reads the declared one (12 intrabars, not 60).
    result = transpile_full(after)
    configure = _configure(result["cpp"])
    assert not [d.message for d in result["diagnostics"] if "reassigns" in d.message]
    (above,) = _stores_above_each_request(after, "sec")
    later = re.search(r"^sec := (.+)$", after, re.M).group(1)
    assert len(above) == 1 and later not in above
    call = _registration(configure, 0)
    assert _replayed(call, "sec") == above
    assert re.search(r"\bsec\b", _returned(call)), call
    # Nothing but the request's own call declares or stores it.
    assert configure.count("decltype(this->sec) sec{};") == 1
    assert f"sec = {later};" not in configure

    # A value registration cannot compute keeps its lowering and warns.
    result = transpile_full(helper)
    configure = _configure(result["cpp"])
    assert "decltype(this->sec)" not in configure
    warnings = [d.message for d in result["diagnostics"] if "reassigns" in d.message]
    assert len(warnings) == 1 and "'sec'" in warnings[0], result["diagnostics"]
    assert "is assigned a value it cannot compute there" in warnings[0], warnings
    legacy = reference_codegen(BASELINE_CODEGEN_REVISION)
    if legacy is None:
        pytest.skip(f"the lane base ({BASELINE_CODEGEN_REVISION}) is not in this checkout's history")
    path = tmp_path / "s0.pine"
    path.write_text(helper, encoding="utf-8")
    # Registered as every earlier build registered it: an integer member read.
    pinned = require_numeric_hunk(
        configure, "((double)(sec) / (double)(60))", integer_arithmetic("sec", "/", "60"))
    assert pinned == _configure(transpile_json(path, legacy)["cpp"])


def test_two_requests_around_a_write_each_register_what_they_read():
    from pineforge_codegen import transpile_full

    assert len(READ_ORDER_SOURCE.encode("utf-8")) == READ_ORDER_SOURCE_BYTES
    result = transpile_full(READ_ORDER_SOURCE)
    configure = _configure(result["cpp"])
    assert not [d.message for d in result["diagnostics"] if "reassigns" in d.message]
    before, after = _stores_above_each_request(READ_ORDER_SOURCE, "sec")
    # The second request sees one more store than the first: the write between.
    assert before and after[:-1] == before and after[-1] not in before
    first, second = _registration(configure, 0), _registration(configure, 1)
    assert _replayed(first, "sec") == before
    assert _replayed(second, "sec") == after
    # Each call returns the timeframe computed from its own values; nothing is
    # replayed ahead of the two calls for both to share.
    assert re.search(r"\bsec\b", _returned(first)), first
    assert re.search(r"\bsec\b", _returned(second)), second
    assert configure.count("decltype(this->sec) sec{};") == 2
    assert configure.index("decltype(this->sec) sec{};") > configure.index(first[:40])


def test_a_request_replay_reads_inputs_and_declared_timeframes_where_it_is():
    from pineforge_codegen import transpile_full

    # The first request of the script is unrelated, so nothing below it
    # precedes "the first request"; each timeframe is a global declared above
    # its request, and the last write follows both.
    result = transpile_full('''//@version=6
strategy("declared timeframes")
daily = request.security(syminfo.tickerid, "D", close)
int sec = input.int(300, "Seconds")
string early = str.tostring(sec / 60)
a = request.security_lower_tf(syminfo.tickerid, early, close)
sec := 60
string late = str.tostring(sec / 60)
b = request.security_lower_tf(syminfo.tickerid, late, close)
sec := 120
plot(daily)
plot(array.size(a))
plot(array.size(b))
''')
    configure = _configure(result["cpp"])
    assert not [d.message for d in result["diagnostics"] if "reassigns" in d.message]
    seconds = 'get_input_int("Seconds", 300)'
    assert _replayed(_registration(configure, 1), "sec") == [seconds]
    assert _replayed(_registration(configure, 2), "sec") == [seconds, "60"]
    assert "sec = 120;" not in configure


def test_a_request_replay_that_cannot_hold_keeps_its_lowering_and_warns():
    from pineforge_codegen import transpile_full

    # A ``var`` carries the later value into the next bar's request: "5" on
    # the first bar and "1" from the second, which no one registration is.
    kept = '''//@version=6
strategy("kept across bars")
var int sec = 300
a = request.security_lower_tf(syminfo.tickerid, str.tostring(sec / 60), close)
sec := 60
plot(array.size(a))
'''
    # Where a function is called is not followed: a write after its
    # definition may come before or after the request the function makes.
    called = '''//@version=6
strategy("requested in a function")
int mins = 60
f() => request.security(syminfo.tickerid, str.tostring(mins), close)
v = f()
mins := 120
plot(v)
'''
    for pine, name in ((kept, "sec"), (called, "mins")):
        result = transpile_full(pine)
        configure = _configure(result["cpp"])
        assert f"decltype(this->{name})" not in configure, pine
        warnings = [d.message for d in result["diagnostics"] if "reassigns" in d.message]
        assert len(warnings) == 1 and f"'{name}'" in warnings[0], result["diagnostics"]
        assert "is assigned after the first request" in warnings[0], warnings


def test_two_requests_around_a_write_read_the_intrabars_their_literals_do(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("request_read_order")
    feed = tape_feed(engine, base)
    aux = _aux_1m_feed(engine, base)
    # What each request reads, from the script's own lines: the last value
    # stored above it, in minutes.
    timeframes = [str(int(stores[-1]) // 60)
                  for stores in _stores_above_each_request(READ_ORDER_SOURCE, "sec")]
    assert len(timeframes) == 2 and timeframes[0] != timeframes[1]
    pine = _closes(READ_ORDER_SOURCE)
    computed = _exits(engine, base, "computed", pine, feed, aux)
    literal = _exits(
        engine, base, "literal", _with_literal_timeframes(pine, *timeframes), feed, aux)
    assert computed == literal
    # Each request has its own count of intrabars on every close (the run's
    # last close, at the end of the data, carries no comment).
    sizes = "|".join(str(CHART_MINUTES // int(timeframe)) for timeframe in timeframes)
    assert {comment for comment in literal.values() if comment} == {sizes}
    print(f"two requests around a write: {len(computed)} exits equal the literal "
          f"timeframes {timeframes}, {sizes} intrabars")


def test_a_request_replay_reads_the_input_as_overridden(tmp_path_factory):
    engine = skip_unless_e2e_env()
    base = tmp_path_factory.mktemp("request_read_order_input")
    feed = tape_feed(engine, base)
    aux = _aux_1m_feed(engine, base)
    declared = "int sec = 300"
    assert READ_ORDER_SOURCE.count(declared) == 1
    pine = _closes(READ_ORDER_SOURCE.replace(declared, 'int sec = input.int(300, "Seconds")'))
    default = _exits(engine, base, "input", pine, feed, aux)
    # The same build under Seconds=60: the first request reads the input, so
    # both read "1", as the two literals "1" register it.
    overridden = {
        trade["exit_time"]: trade["exit_comment"]
        for trade in closed_trades(
            engine, base / "input", feed, {"Seconds": 60}, input_tf="15", script_tf="15",
            aux_security_ohlcv_csv=aux, aux_security_input_tf="1")
    }
    literal = _exits(
        engine, base, "literal", _with_literal_timeframes(_closes(READ_ORDER_SOURCE), "1", "1"), feed, aux)
    assert overridden == literal
    assert overridden != default
    one = str(CHART_MINUTES // 1)
    assert {comment for comment in literal.values() if comment} == {f"{one}|{one}"}
    assert {comment for comment in default.values() if comment} == {
        f"{CHART_MINUTES // (300 // 60)}|{one}"}
    print(f"input override: {len(literal)} exits under Seconds=60 equal the literal \"1\"s")


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
    # The inferred snapshot member is double; the reassigned member is int.
    snap = integer_to_double(integer_arithmetic("mins", "*", "2"))
    later = integer_store(integer_arithmetic("mins", "*", "4"))
    assert configure.index(f"snap = {snap};") < configure.index(f"mins = {later};")
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
    assert f"mins = {integer_store(integer_arithmetic('mins', '*', '2'))};" in configure
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
    legacy = reference_codegen(BASELINE_CODEGEN_REVISION)
    if legacy is None:
        pytest.skip(f"the lane base ({BASELINE_CODEGEN_REVISION}) is not in this checkout's history")
    path = tmp_path / "shadow.pine"
    path.write_text(pine, encoding="utf-8")
    assert configure == _configure(transpile_json(path, legacy)["cpp"])
