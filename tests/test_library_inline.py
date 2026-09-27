"""Inlining the Pine libraries a script imports (``library_inline``).

TradingView links a published library's exports into an importing script;
PineForge inlines the library's source at transpile time. Only what the
script reaches is inlined, every inlined top-level name, local and parameter
gets a module-qualified name, ``alias.f(...)`` / ``alias.T`` / method syntax
resolve into the library, and an inlined function is an ordinary user
function of the program (state per call site). The sources are the
clean-room fixtures of ``fixtures/pine_libraries``; the open libraries the
lane was measured against are evidence outside this repository.
"""

from __future__ import annotations

import pytest

from pineforge_codegen import transpile, transpile_full
from pineforge_codegen.errors import CompileError, Level
from tests._pine_libraries import library_sources

SOURCES = library_sources("pftest/Signals/1", "pftest/Base/1")
HEAD = '//@version=6\nstrategy("T")\n'


def _cpp(body: str, head: str = HEAD, libraries=SOURCES) -> str:
    return transpile(head + body, libraries=libraries)


def _errors(body: str, head: str = HEAD, libraries=SOURCES) -> list:
    with pytest.raises(CompileError) as err:
        transpile(head + body, libraries=libraries)
    return [d for d in err.value.diagnostics if d.level == Level.ERROR]


def test_an_export_and_what_it_reaches_are_inlined():
    cpp = _cpp('import pftest/Signals/1 as S\nx = S.scaled(close)\nplot(x)\n'
               'if x > 0\n    strategy.entry("L", strategy.long)\n')
    # scaled -> base.twice (the transitive import) and the constant RATIO.
    for name in ("Signals_v1__scaled", "Base_v1__twice", "Signals_v1__RATIO"):
        assert name in cpp, name


def test_only_what_the_script_reaches_is_inlined():
    """An unreached export, private helper or example code is not part of the
    program: ``seeded`` calls ``request.seed``, which PineForge refuses."""
    cpp = _cpp('import pftest/Signals/1 as S\nif S.pin() == "BUY"\n'
               '    strategy.entry("L", strategy.long)\n')
    assert "Signals_v1__pin" in cpp and "Signals_v1__wicks" in cpp
    for unreached in ("Signals_v1__seeded", "Signals_v1__counter", "Base_v1__twice",
                      "Signals_v1__tag", "Signals_v1__secret"):
        assert unreached not in cpp, unreached
    (err,) = _errors('import pftest/Signals/1 as S\nx = S.seeded()\nplot(x)\n'
                     'if x > 0\n    strategy.entry("L", strategy.long)\n')
    assert "request.seed" in err.message
    assert err.location.file == "pftest/Signals/1"


def test_library_names_never_meet_the_scripts():
    """The script declares ``secret``, ``filter``, ``trSmooth``, ``wicks`` and a
    function ``pin``; the library has a private ``secret``, locals named
    ``filter`` and ``trSmooth`` with history, a helper ``wicks`` and parameters
    named ``high`` and ``low``."""
    body = ('import pftest/Signals/1 as S\n'
            'type Filter\n    bool on\n'
            'Filter filter = Filter.new(true)\n'
            'trSmooth = ta.sma(close, 3)\n'
            'secret = "main"\n'
            'wicks = 2\n'
            'pin(x) => x + 1\n'
            'sm = S.smooth(close, 4)\n'
            'sp = S.span(high, low)\n'
            'tg = S.tag(secret)\n'
            'p = pin(wicks)\n'
            'if S.pin() == "BUY" and sm > sp and filter.on and tg != "" and trSmooth > p\n'
            '    strategy.entry("L", strategy.long)\n')
    cpp = _cpp(body)
    for name in ("Signals_v1__smooth__filter", "Signals_v1__smooth__trSmooth",
                 "Signals_v1__span__high", "Signals_v1__span__low",
                 "Signals_v1__secret", "Signals_v1__wicks"):
        assert name in cpp, name


def test_keyword_arguments_follow_the_parameters_new_names():
    cpp = _cpp('import pftest/Signals/1 as S\n'
               'if S.pin(strict = false, frac = 0.5) == "BUY"\n'
               '    strategy.entry("L", strategy.long)\n')
    assert "Signals_v1__pin(0.5, false)" in cpp


def test_types_enums_constants_and_methods_resolve_into_the_library():
    body = ('import pftest/Signals/1 as S\n'
            'S.Bar2 b = S.Bar2.new(low, high)\n'
            'S.Side side = close > open ? S.Side.long : S.Side.short\n'
            'wide(S.Bar2 r) => r.width() > 1\n'
            'w = b.width() + S.width(b) + S.RATIO\n'
            'if side == S.Side.long and wide(b) and w > 0\n'
            '    strategy.entry("L", strategy.long)\n')
    cpp = _cpp(body)
    assert "Signals_v1__Bar2" in cpp and "Signals_v1__Side" in cpp
    assert "Signals_v1__RATIO" in cpp


def test_an_alias_equal_to_a_builtin_namespace_reads_its_builtins():
    """``import TradingView/ta/7`` has no alias, so it is ``ta``: TradingView's
    ``usedLibs`` stays empty for built-in names and lists the library for a
    library-only one."""
    body = ('import pftest/Signals/1 as ta\n'
            'x = ta.sma(close, 3)\n'
            'if ta.pin() == "BUY" and ta.rsi(close, 14) > 50 and x > 0\n'
            '    strategy.entry("L", strategy.long)\n')
    cpp = _cpp(body)
    assert "Signals_v1__pin" in cpp
    assert "ta::SMA" in cpp and "ta::RSI" in cpp
    cpp = _cpp('import pftest/Base/1 as math\nx = math.max(math.twice(close), 1)\n'
               'plot(x)\nif x > 0\n    strategy.entry("L", strategy.long)\n')
    assert "Base_v1__twice" in cpp and "std::max" in cpp


def test_an_alias_equal_to_another_builtin_namespace_is_refused():
    (err,) = _errors('import pftest/Base/1 as array\nx = array.twice(close)\nplot(x)\n')
    assert err.message == ("Import is not supported: 'pftest/Base/1 as array': its "
                           "alias 'array' is a built-in namespace other than ta, "
                           "math or str")


@pytest.mark.parametrize("body, message", [
    ('import pftest/Signals/1 as S\nx = S.wicks()\nplot(x)\n',
     "library 'pftest/Signals/1' does not export 'wicks'"),
    ('import pftest/Signals/1 as S\nx = S.nope()\nplot(x)\n',
     "library 'pftest/Signals/1' does not export 'nope': it has no 'nope'"),
    ('import pftest/Signals/1 as S\nimport pftest/Base/1 as S\nx = S.pin()\nplot(x)\n',
     "import alias 'S' is used by two imports"),
    ('import pftest/Signals/1 as S\nS = 1\nplot(S)\n',
     "import alias 'S' is also declared as a variable, parameter or function"),
])
def test_what_a_library_does_not_offer_is_refused_by_name(body, message):
    (err,) = _errors(body)
    assert err.message == message


def test_an_unused_import_links_nothing():
    """TradingView prunes an import the script never uses (``usedLibs`` is
    null), and a requests manifest never pins it: with sources configured it
    is dropped; a used one that is not at hand is refused by name."""
    cpp = _cpp('import pftest/Signals/1 as S\nimport someone/Unused/3 as U\n'
               'if S.pin() == "BUY"\n    strategy.entry("L", strategy.long)\n')
    assert "Signals_v1__pin" in cpp
    (err,) = _errors('import someone/Missing/2 as M\nx = M.f()\nplot(x)\n')
    assert err.message == ("Import is not supported: 'someone/Missing/2': library "
                           "'someone/Missing/2' is not among the libraries passed "
                           "to transpile()")


def test_a_library_imported_twice_is_inlined_once():
    cpp = _cpp('import pftest/Signals/1 as S\nimport pftest/Base/1 as B\n'
               'x = S.scaled(close) + B.twice(open)\nplot(x)\n'
               'if x > 0\n    strategy.entry("L", strategy.long)\n')
    assert cpp.count("double Base_v1__twice(") == 1


def test_each_call_site_keeps_its_own_state():
    """Two calls of an export holding ``var`` state and a TA site are two call
    sites, exactly as two calls of a user function."""
    body = ('import pftest/Signals/1 as S\n'
            '[n1, s1] = S.counter(5)\n[n2, s2] = S.counter(9)\n'
            'if n1 == n2 and s1 > s2\n    strategy.entry("L", strategy.long)\n')
    cpp = _cpp(body)
    twin = HEAD + ('counter(simple int len) =>\n    var int n = 0\n    n += 1\n'
                   '    [n, ta.sma(close, len)]\n'
                   '[n1, s1] = counter(5)\n[n2, s2] = counter(9)\n'
                   'if n1 == n2 and s1 > s2\n    strategy.entry("L", strategy.long)\n')
    plain = transpile(twin)
    assert cpp.count("ta::SMA") == plain.count("ta::SMA") >= 2
    assert cpp.count("Signals_v1__counter_cs") == plain.count("counter_cs")


def test_a_library_is_inlined_the_same_way_every_time():
    body = ('import pftest/Signals/1 as S\nx = S.scaled(close)\nplot(x)\n'
            'if S.pin() == "BUY"\n    strategy.entry("L", strategy.long)\n')
    assert _cpp(body) == _cpp(body)


def test_library_diagnostics_are_located_in_the_library():
    full = transpile_full(HEAD + 'import pftest/Signals/1 as S\nd = S.dir(1)\n'
                          'if d > 0\n    strategy.entry("L", strategy.long)\n',
                          libraries=SOURCES)
    assert "Signals_v1__dirAt" in full["cpp"]
    files = {d.location.file for d in full["diagnostics"] if d.location}
    assert files <= {"<input>", "pftest/Signals/1", "pftest/Base/1"}


def test_an_overloaded_library_function_is_refused():
    overloaded = dict(SOURCES)
    overloaded["pftest/Over/1"] = ('//@version=6\nlibrary("Over")\n'
                                   'export f(float x) => x\nexport f(string s) => s\n')
    (err,) = _errors('import pftest/Over/1 as O\nx = O.f(close)\nplot(x)\n',
                     libraries=overloaded)
    assert "defines 'f' 2 times (overloads)" in err.message


def test_a_method_on_the_scripts_receiver_type_is_refused():
    lib = dict(SOURCES)
    lib["pftest/Meth/1"] = ('//@version=6\nlibrary("Meth")\n'
                            'export method twice(array<float> self) => self.size() * 2\n')
    body = ('import pftest/Meth/1 as M\n'
            'method twice(array<float> self) => self.size() * 3\n'
            'a = array.new<float>()\nx = a.twice()\nplot(x)\n')
    (err,) = _errors(body, libraries=lib)
    assert "has the same receiver type as a method of the script" in err.message


def test_a_primitive_local_named_like_a_namespace_leaves_it_built_in():
    """``TradingView/RelativeValue/2`` names a string parameter ``timeframe``
    and calls ``timeframe.change(timeframe)``: the call is the built-in's, its
    argument the renamed parameter. A local of a type with members (an array,
    a user type) is an object: ``name.member`` reads the local."""
    lib = {"pftest/Tf/1": ('//@version=6\nlibrary("Tf")\n'
                           'export changed(string timeframe) => timeframe.change(timeframe)\n'
                           'export first(array<float> array) => array.first()\n')}
    cpp = _cpp('import pftest/Tf/1 as T\nx = T.changed("D")\ny = T.first(array.from(1.0))\n'
               'plot(y)\nif x\n    strategy.entry("L", strategy.long)\n', libraries=lib)
    assert "tf_change(prev_bar_timestamp_, current_bar_.timestamp, Tf_v1__changed__timeframe," in cpp
    assert "Tf_v1__first__array" in cpp


def test_a_v5_library_inlines():
    """Its body keeps v5's rules (``tests/test_library_v5.py``)."""
    lib = {"pftest/Five/1": '//@version=5\nlibrary("Five")\nexport f(float x) => x * 2\n'}
    cpp = _cpp('import pftest/Five/1 as F\nx = F.f(close)\nplot(x)\n', libraries=lib)
    assert "Five_v1__f(" in cpp
