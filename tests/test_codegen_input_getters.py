"""Codegen contract: the full ``input.<type>`` -> C++ runtime getter mapping.

``pineforge_codegen/codegen/input.py::_input_type_to_getter`` maps every
Pine v6 ``input.<type>`` short name to one of four C++ runtime getters
(plus bare ``input(...)``, which hits the fallback). The pre-existing
suite only spot-checks two rows of this table:

- ``test_support_checker_input_color.py`` — input.color defval *rejection*
- ``test_input_time_int64.py`` — input.time int64 routing + color-int spot

Nothing verified the FULL table emits the right ``get_input_*`` call for
*each* input type. This module closes that gap (finding F5.4) with one
transpile-and-assert per branch of ``_input_type_to_getter``:

    int                                              -> get_input_int
    float / price                                    -> get_input_double
    source                                           -> get_input_source (Series&)
    bool                                             -> get_input_bool
    string / timeframe / session / symbol / text_area-> get_input_string
    color                                            -> get_input_int64 (packed ARGB)
    time                                             -> get_input_int64
    enum                                             -> get_input_int
    bare input(...) / unrecognised (fallback)        -> get_input_double

These are additive, test-only assertions — no production behavior is
exercised that the corpus does not already exercise.
"""

from __future__ import annotations

from pineforge_codegen import transpile


def _emit(decl: str) -> str:
    """Transpile a minimal strategy whose only input is ``decl``."""
    src = f'''//@version=6
strategy("t")
x = {decl}
plot(close)
'''
    return transpile(src)


# --- get_input_int -------------------------------------------------------

def test_input_int_getter():
    assert "get_input_int(" in _emit('input.int(5, "n")')


def test_input_color_getter_routes_to_int64():
    # Color packs into 0xAARRGGBB, which overflows signed int32 (e.g.
    # color.red == 0xFFFF0000), so it routes through get_input_int64.
    # defval must be a color constant/builder (see
    # test_support_checker_input_color).
    assert "get_input_int64(" in _emit('input.color(color.red, "c")')


def test_input_enum_getter_routes_to_int():
    # input.enum returns an int index; defval is an enum member and the
    # enum must be declared *above* the call.
    src = '''//@version=6
strategy("t")
enum Sig
    Buy
x = input.enum(Sig.Buy, "e")
plot(close)
'''
    assert "get_input_int(" in transpile(src)


# --- get_input_double ----------------------------------------------------

def test_input_float_getter():
    assert "get_input_double(" in _emit('input.float(1.5, "n")')


def test_input_source_getter_routes_to_source():
    # input.source returns a Series<double>& (runtime-overridable); the
    # defval must be a native OHLCV series, bound to the engine's base
    # source series. The value read is get_input_source(...)[0].
    emitted = _emit('input.source(close, "s")')
    assert 'get_input_source("s", _src_close_)' in emitted


def test_input_price_getter_routes_to_double():
    assert "get_input_double(" in _emit('input.price(1.0, "p")')


def test_input_source_hl2_binds_base_series():
    # Derived native source maps to its base series member.
    assert 'get_input_source("s", _src_hl2_)' in _emit('input.source(hl2, "s")')


def test_input_source_subscript_is_series():
    # A subscripted source var becomes a Series member (analyzer marks it
    # series via the [k] access), pushed from the resolved source's current
    # value, so src[1] lowers to a real Series history read.
    src = '''//@version=6
strategy("t")
s = input.source(close, "s")
x = s[1]
plot(close)
'''
    cpp = transpile(src)
    assert 'get_input_source("s", _src_close_)[0]' in cpp
    assert "Series<double> s" in cpp  # declared as a series member


def test_input_source_sets_active_flag():
    # The ctor turns on the engine's native source-series push only when
    # the script uses input.source.
    cpp = _emit('input.source(close, "s")')
    assert "_src_series_active_ = true;" in cpp
    # A script without input.source must NOT pay the cost.
    no_src = transpile('//@version=6\nstrategy("t")\nplot(close)\n')
    assert "_src_series_active_ = true;" not in no_src


# --- get_input_int64 (color) --------------------------------------------

def test_input_color_hex_literal_packs_argb():
    # #RRGGBB lowers to opaque 0xAARRGGBB (alpha=ff) as an int64 literal.
    assert 'get_input_int64("c", 0xff00e676LL)' in _emit('input.color(#00e676, "c")')


def test_input_color_hex_literal_with_alpha():
    # #RRGGBBAA carries an explicit alpha; reorder to alpha-first.
    assert 'get_input_int64("c", 0x8000ff00LL)' in _emit('input.color(#00ff0080, "c")')


def test_input_color_all_defval_shapes_pack_to_int64():
    """Compile golden for input.color (formerly a corpus probe — color is
    cosmetic, no trade parity). Every defval shape lowers to a packed-ARGB
    int64 via get_input_int64: const, #RRGGBB (opaque), #RRGGBBAA (explicit
    alpha), and the color.new(...) builder."""
    src = '''//@version=6
strategy("t")
cConst = input.color(color.red, "Const")
cHex6  = input.color(#00e676, "H6")
cHex8  = input.color(#00ff0080, "H8")
cNew   = input.color(color.new(color.blue, 50), "Builder")
plot(close)
'''
    cpp = transpile(src)
    assert 'get_input_int64("Const", pine_color::red)' in cpp
    assert 'get_input_int64("H6", 0xff00e676LL)' in cpp
    assert 'get_input_int64("H8", 0x8000ff00LL)' in cpp
    assert 'get_input_int64("Builder", pine_color::new_color(pine_color::blue' in cpp
    # No color defval may lower to the old "0" fallback or the int32 getter.
    assert 'get_input_int("Const"' not in cpp
    assert 'get_input_int64("Const", 0)' not in cpp


# --- get_input_bool ------------------------------------------------------

def test_input_bool_getter():
    assert "get_input_bool(" in _emit('input.bool(true, "b")')


# --- get_input_string ----------------------------------------------------

def test_input_string_getter():
    assert "get_input_string(" in _emit('input.string("a", "st")')


def test_input_timeframe_getter_routes_to_string():
    assert "get_input_string(" in _emit('input.timeframe("D", "tf")')


def test_input_session_getter_routes_to_string():
    assert "get_input_string(" in _emit('input.session("0930-1600", "se")')


def test_input_symbol_getter_routes_to_string():
    assert "get_input_string(" in _emit('input.symbol("AAPL", "sy")')


def test_input_text_area_getter_routes_to_string():
    assert "get_input_string(" in _emit('input.text_area("x", "ta")')


# --- get_input_int64 -----------------------------------------------------

def test_input_time_getter_routes_to_int64():
    # int64 because Pine v6 input.time is epoch ms — overflows int32.
    cpp = _emit('input.time(timestamp("2025-01-01T00:00:00"), "ti")')
    assert "get_input_int64(" in cpp
    # ...and never the int32 helper for this key.
    assert 'get_input_int("ti"' not in cpp


# --- fallback branch: bare input(...) -> get_input_double ----------------

def test_bare_input_falls_back_to_double_numeric():
    # Bare input(...) has func_name="input" (not a short type name), so it
    # hits the `return "get_input_double"` fallback regardless of defval.
    assert "get_input_double(" in _emit('input(5, "n")')


def test_bare_input_falls_back_to_double_bool_defval():
    # Even a bool defval routes to double through the bare-input fallback
    # (the getter table keys on the type *name*, not the value).
    assert "get_input_double(" in _emit('input(true, "b")')


def test_bare_input_falls_back_to_double_string_defval():
    assert "get_input_double(" in _emit('input("a", "s")')
